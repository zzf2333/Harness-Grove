"""MCP server 条目的逻辑定义与两种 JSON 适配器：有界定位、渲染与条目级比对（标准库）。

范围（R3b）：Pi 的 `mcpServers`（用户级 `.pi/agent/mcp.json`、项目级 `.pi/mcp.json`）与 Claude Code
项目级 `.mcp.json` 里**已经存在**的 server 条目。写入只替换 `mcpServers` 下指定成员的值跨度：未托管的
server 与文件外围字节原样保留，不用 `json.dumps` 重写整个文件。新增与删除条目本片未支持。

启停（`enabled`）：Pi 的条目自己就有 `enabled: false`；Claude Code 的 `.mcp.json` 条目没有这个字段，启停按项目
记在 `~/.claude.json` 的 `projects[<项目键>].disabledMcpServers`，由 `mcp_toggle.py` 与 `claude-toggle` 目标写。
启停只表示去掉这一层的禁用，不保证 Host 会加载或能连上；项目 `.mcp.json` 的审批（`disabledMcpjsonServers` /
`enabledMcpjsonServers`）、信任（`hasTrustDialogAccepted`）与默认关闭的内置 server 不是同一套开关，本片不碰。

凭证：不读、不解析 `env` / `headers` 的值，也不执行 `!command`。逻辑定义里 `env` / `headers` 的值必须含
`${NAME}` 引用，`url` 不得带 userinfo；其他字面值、`oauth`、`timeout`、`exposure`、`description`、
`headersHelper` 等本片不支持的字段会让该条目不可托管，并明确报出字段名（不做通用 secret 扫描，也不声称
能识别所有内联凭证）。

官方依据（2026-10-31 核对）：

- Pi 1.4.0 `docs/mcp.md`：`mcpServers`；stdio 用 `command` / `args` / `env` / `cwd`；HTTP 用 `url` / `headers`；
  `type` 可选（`stdio` / `http` / `streamable-http`），`sse` 被拒；`enabled: false` 保留条目不连接；
  `env` / `headers` 可用 `${NAME}`。
- Claude Code <https://code.claude.com/docs/en/mcp>：项目级 `.mcp.json` 的 `mcpServers`；stdio 用
  `command` / `args` / `env`，也接受官方示例里的显式 `"type": "stdio"`；HTTP 必须 `"type": "http"`（缺 `type` 会被读成 stdio，`streamable-http` 是别名）；
  `${VAR}` 与 `${VAR:-default}` 在 `command` / `args` / `env` / `url` / `headers` 里展开；条目没有 `enabled`
  字段，启停按项目记在 `~/.claude.json` 的 `projects[<项目键>].disabledMcpServers`。
- Claude Code 项目启停：`/mcp disable` 把 name 追加进项目条目的 `disabledMcpServers`，`/mcp enable` 删除该名；
  缺这个列表等价于空列表；项目键是启动时工作目录的路径字符串（NFC 规范化，不 realpath），不在 `projects`
  里的键回落成默认项目条目、改动静默无效——所以本片只按显式给出的项目键读写，不创建项目条目。
  依据是 2026-10-31 从本机安装的 2.1.312 二进制只读定位的产品自身说明与读写函数（静态证据，不是 Host 实跑）。
"""

import difflib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_span import server_span

SCHEMA_VERSION = 1
DEFINITION_KIND = "phe_mcp_server"
DEFINITION_NAME = "server.json"

STDIO_FIELDS = ("command", "args", "env", "cwd")
HTTP_FIELDS = ("url", "headers")
TRANSPORT_FIELDS = {"stdio": STDIO_FIELDS, "http": HTTP_FIELDS}
TRANSPORT_TYPES = {"stdio": ("stdio",), "http": ("http", "streamable-http")}

# 适配器差异只有三处：HTTP 渲染时的 type、是否有 cwd、是否有条目的 enabled。
ADAPTERS = {
    "pi": {"http_type": None, "cwd": True, "enabled": True, "scope": ("user", "project")},
    "claude": {"http_type": "http", "cwd": False, "enabled": False, "scope": ("project",)},
}
# 本片承认的 Host 入口路径形状：显式给出，不按任意 mcp.json 猜 scope。
ADAPTER_PATHS = {
    "pi": (("user", (".pi", "agent", "mcp.json")), ("project", (".pi", "mcp.json"))),
    "claude": (("project", (".mcp.json",)),),
    # 不是条目适配器，而是 Claude Code 的项目启停目标：写入在 `~/.claude.json` 的 `projects[<项目键>]` 下。
    "claude-toggle": (("user", (".claude.json",)),),
}
# 启停目标的数组名：数组里有这个名字 = 禁用，没有 = 启用，缺列表等价于空列表。
TOGGLE_ARRAYS = {"claude-toggle": "disabledMcpServers"}
# 逻辑定义里允许出现的字段；`type` 由适配器渲染，不进入定义。
DEFINITION_FIELDS = (
    "schema_version",
    "kind",
    "server",
    "transport",
    "command",
    "args",
    "env",
    "cwd",
    "url",
    "headers",
    "enabled",
)
REFERENCE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*(:-[^{}]*)?\}")
NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


def adapter_scope(adapter: str, path) -> str | None:
    """显式入口路径对应的 scope：只认本片支持的形状，其余返回 None。"""
    parts = Path(path).parts
    for scope, suffix in ADAPTER_PATHS[adapter]:
        if len(parts) >= len(suffix) and parts[-len(suffix) :] == suffix:
            return scope
    return None


def target_path_reason(adapter: str, path) -> str | None:
    """入口路径不在本片范围时的说明；支持时返回 None。"""
    if adapter_scope(adapter, path) is not None:
        return None
    shapes = "、".join("`" + "/".join(suffix) + "`" for _scope, suffix in ADAPTER_PATHS[adapter])
    return f"{path} 不是本片支持的 {adapter} 入口形状（只支持 {shapes}）"


# 跨度定位在 `mcp_span.py`：这里只用它找 `mcpServers` 下托管成员的值跨度，不重写整份文件。


# 字段规则：凭证只允许引用，非敏感常量本片不支持。


def _check_reference(value, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where} 必须是字符串")
    if value.startswith("!"):
        raise ValueError(f"{where} 是 !command 形式：本片不执行也不持久化命令，未支持")
    if REFERENCE.search(value) is None:
        raise ValueError(f"{where} 只支持含 ${{NAME}} 引用的值；字面值可能含凭证，本片不支持也不猜")
    return value


def _check_url(url, where: str) -> str:
    if not isinstance(url, str) or not url:
        raise ValueError(f"{where} 必须是字符串")
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"{where} 只支持 http / https：{parsed.scheme or '缺少协议'}")
    if parsed.username or parsed.password:
        raise ValueError(f"{where} 带内联认证（userinfo）：本片不支持，请改成 ${{NAME}} 引用")
    return url


def _check_mapping(value, where: str) -> dict:
    if not isinstance(value, dict):
        raise TypeError(f"{where} 必须是对象")
    checked = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise ValueError(f"{where} 的键必须是非空字符串")
        checked[key] = _check_reference(item, f"{where}.{key}")
    return checked


def _check_args(value, where: str) -> list:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{where} 必须是字符串数组")
    return list(value)


def _check_command(value, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where} 必须是非空字符串")
    return value


def _check_enabled(value, where: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{where} 必须是 true / false")
    return value


def _check_type(transport: str, value) -> str:
    if not isinstance(value, str) or value not in TRANSPORT_TYPES[transport]:
        raise ValueError(f"type 与传输不一致：{transport} 只接受 {' / '.join(TRANSPORT_TYPES[transport])}")
    return value


def _transport_of(entry: dict, server: str, adapter: str) -> str:
    if "command" in entry and "url" in entry:
        raise ValueError(f"{adapter} 条目 {server} 同时有 command 与 url：传输不明确，未支持")
    if "command" in entry:
        return "stdio"
    if "url" in entry:
        return "http"
    raise ValueError(f"{adapter} 条目 {server} 没有 command 或 url：只有 enabled 的项目级覆盖，本片未支持")


def _supported_entry_fields(adapter: str, transport: str) -> list[str]:
    limits = ADAPTERS[adapter]
    # `type` 两个适配器都可能显式写出来（Claude 官方 stdio 示例就是 `"type": "stdio"`）。
    fields = [*TRANSPORT_FIELDS[transport], "type"]
    if limits["enabled"]:
        fields.append("enabled")
    return fields


def _check_definition_fields(definition: dict, where: str) -> None:
    extra = sorted(set(definition) - set(DEFINITION_FIELDS))
    if extra:
        raise ValueError(f"{where} 有本片不支持的字段：{'、'.join(extra)}")
    transport = definition.get("transport")
    if transport not in TRANSPORT_FIELDS:
        raise ValueError(f"{where} 的 transport 只能是 {' / '.join(TRANSPORT_FIELDS)}：{transport}")
    fields = [name for name in TRANSPORT_FIELDS[transport] if name in definition]
    other = [name for name in sum(TRANSPORT_FIELDS.values(), ()) if name in definition and name not in fields]
    if other:
        raise ValueError(f"{where} 的 {transport} 定义里不能有：{'、'.join(other)}")
    if transport == "stdio" and "command" not in definition:
        raise ValueError(f"{where} 的 stdio 定义缺少 command")
    if transport == "http" and "url" not in definition:
        raise ValueError(f"{where} 的 http 定义缺少 url")


def check_definition(definition: dict, where: str = "逻辑定义") -> dict:
    """校验并规范化逻辑定义（`server.json` 的内容）。"""
    if not isinstance(definition, dict):
        raise TypeError(f"{where} 必须是对象")
    if definition.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{where} 的 schema_version 只支持 {SCHEMA_VERSION}")
    if definition.get("kind") != DEFINITION_KIND:
        raise ValueError(f"{where} 的 kind 只能是 {DEFINITION_KIND}")
    server = definition.get("server")
    if not isinstance(server, str) or NAME_PATTERN.fullmatch(server) is None:
        raise ValueError(f"{where} 的 server 只能含字母、数字、_ 与 -")
    _check_definition_fields(definition, where)
    checked = {
        "schema_version": SCHEMA_VERSION,
        "kind": DEFINITION_KIND,
        "server": server,
        "transport": definition["transport"],
    }
    if "command" in definition:
        checked["command"] = _check_command(definition["command"], f"{where}.command")
    if "args" in definition:
        checked["args"] = _check_args(definition["args"], f"{where}.args")
    if "env" in definition:
        checked["env"] = _check_mapping(definition["env"], f"{where}.env")
    if "cwd" in definition:
        checked["cwd"] = _check_command(definition["cwd"], f"{where}.cwd")
    if "url" in definition:
        checked["url"] = _check_url(definition["url"], f"{where}.url")
    if "headers" in definition:
        checked["headers"] = _check_mapping(definition["headers"], f"{where}.headers")
    if "enabled" in definition:
        checked["enabled"] = _check_enabled(definition["enabled"], f"{where}.enabled")
    return checked


def definition_from_entry(adapter: str, entry, server: str) -> dict:
    """把一个 Host 里的 server 条目投影成逻辑定义；不支持的字段与值在这里拒绝。"""
    if not isinstance(entry, dict):
        raise TypeError(f"{adapter} 条目 {server} 不是对象")
    transport = _transport_of(entry, server, adapter)
    allowed = _supported_entry_fields(adapter, transport)
    extra = sorted(set(entry) - set(allowed))
    if extra:
        raise ValueError(
            f"{adapter} 条目 {server} 有本片不支持的字段：{'、'.join(extra)}（不支持就不托管，不猜也不删）"
        )
    definition = {"schema_version": SCHEMA_VERSION, "kind": DEFINITION_KIND, "server": server, "transport": transport}
    if "type" in entry:
        _check_type(transport, entry["type"])
    where = f"{adapter} 条目 {server}"
    if transport == "stdio":
        definition["command"] = _check_command(entry["command"], f"{where}.command")
        if "args" in entry:
            definition["args"] = _check_args(entry["args"], f"{where}.args")
        if "env" in entry:
            definition["env"] = _check_mapping(entry["env"], f"{where}.env")
        if "cwd" in entry:
            if not ADAPTERS[adapter]["cwd"]:
                raise ValueError(f"{where} 的 cwd 在 {adapter} 的配置里没有对应字段：未支持，不猜")
            definition["cwd"] = _check_command(entry["cwd"], f"{where}.cwd")
    else:
        definition["url"] = _check_url(entry["url"], f"{where}.url")
        if "headers" in entry:
            definition["headers"] = _check_mapping(entry["headers"], f"{where}.headers")
    if "enabled" in entry:
        if not ADAPTERS[adapter]["enabled"]:
            raise ValueError(
                f"{where} 的 enabled 在 Claude 的 .mcp.json 里没有对应字段"
                "（启停按项目记在 ~/.claude.json 的 disabledMcpServers）：未支持，不猜"
            )
        definition["enabled"] = _check_enabled(entry["enabled"], f"{where}.enabled")
    return check_definition(definition, where)


def render_entry(adapter: str, definition: dict) -> dict:
    """按适配器渲染托管条目：字段顺序固定，差异只在 type / cwd / enabled。"""
    limits = ADAPTERS[adapter]
    if limits["http_type"] is None and adapter != "pi":
        raise ValueError(f"未支持的适配器：{adapter}")
    entry: dict = {}
    if definition["transport"] == "http":
        if limits["http_type"] is not None:
            entry["type"] = limits["http_type"]
        entry["url"] = definition["url"]
        if "headers" in definition:
            entry["headers"] = dict(definition["headers"])
    else:
        entry["command"] = definition["command"]
        if "args" in definition:
            entry["args"] = list(definition["args"])
        if "env" in definition:
            entry["env"] = dict(definition["env"])
        if "cwd" in definition:
            if not limits["cwd"]:
                raise ValueError(f"{adapter} 的配置里没有 cwd 字段：这个目标未支持，不猜")
            entry["cwd"] = definition["cwd"]
    if "enabled" in definition:
        if not limits["enabled"]:
            if definition["enabled"]:
                return entry
            raise ValueError(
                f"{adapter} 的 .mcp.json 条目没有 enabled 字段；启停按项目记在 ~/.claude.json 的 disabledMcpServers，"
                "本片不写那个文件"
            )
        if not definition["enabled"]:
            entry["enabled"] = False
    return entry


def render_target_entry(adapter: str, definition: dict, entry: dict, manage_enabled: bool = False) -> dict:
    """按适配器渲染托管条目，并保留目标本地状态（显式 stdio `type` 始终保留）。

    `manage_enabled` 为 False 时启停是目标本地状态：条目里写了 `enabled` 就沿用原值、缺字段就不补（普通参数更新
    不动目标开关）。为 True 时（这次 patch 改的就是启停）才按定义渲染。Claude 的 `.mcp.json` 条目没有启停字段：
    它的启停由 `claude-toggle` 目标写到 `~/.claude.json`，这里不从定义往条目里塞 `enabled`。
    """
    limits = ADAPTERS[adapter]
    rendered = render_entry(adapter, definition if limits["enabled"] else _without_enabled(definition))
    if limits["enabled"] and not manage_enabled:
        if "enabled" in entry:
            rendered["enabled"] = entry["enabled"]
        else:
            rendered.pop("enabled", None)
    if definition["transport"] == "stdio" and entry.get("type") == "stdio":
        rendered = {"type": "stdio", **rendered}
    return rendered


def _without_enabled(definition: dict) -> dict:
    return {key: value for key, value in definition.items() if key != "enabled"}


def render_target_entry_text(
    adapter: str, definition: dict, entry: dict, column: int = 0, step: int = 2, manage_enabled: bool = False
) -> str:
    text = json.dumps(render_target_entry(adapter, definition, entry, manage_enabled), ensure_ascii=False, indent=step)
    return text.replace("\n", "\n" + " " * column)


def render_target_fragment(
    adapter: str,
    definition: dict,
    entry: dict,
    text: str,
    span: tuple[int, int],
    column: int = 0,
    step: int = 2,
    manage_enabled: bool = False,
) -> str:
    """目标要替换进文件的片段：渲染结果与当前条目一致时原样返回现片段，不重排已一致的条目。"""
    if current_entry(adapter, entry, definition["server"]) == render_target_entry(
        adapter, definition, entry, manage_enabled
    ):
        return text[span[0] : span[1]]
    return render_target_entry_text(adapter, definition, entry, column, step, manage_enabled)


def current_entry(adapter: str, entry: dict, server: str) -> dict:
    """目标当前条目的规范化渲染，用来和期待条目比对；目标本地状态保持原值。"""
    return render_target_entry(adapter, definition_from_entry(adapter, entry, server), entry)


def entry_at(text: str, server: str) -> tuple[dict, tuple[int, int], str]:
    """当前文件里这个 server 的条目对象、跨度与原文片段；不存在时抛 ValueError。"""
    span = server_span(text, server)
    if span is None:
        raise ValueError(f"配置文件里没有 mcpServers 条目：{server}")
    entry, _end = json.JSONDecoder().raw_decode(text, span[0])
    return entry, span, text[span[0] : span[1]]


def drift(adapter: str, definition: dict, text: str) -> dict:
    """条目级比对：把托管逻辑定义按适配器渲染后与当前条目比较，只报字段名。"""
    server = definition["server"]
    try:
        entry, span, fragment = entry_at(text, server)
    except ValueError as exc:
        return {"state": "missing", "fields": [], "reason": str(exc), "span": None, "fragment": None}
    try:
        current = current_entry(adapter, entry, server)
        expected = render_target_entry(adapter, definition, entry)
    except ValueError as exc:
        return {"state": "unsupported", "fields": [], "reason": str(exc), "span": span, "fragment": fragment}
    fields = sorted(set(current) | set(expected))
    changed = [name for name in fields if current.get(name) != expected.get(name)]
    if "enabled" in definition and ADAPTERS[adapter]["enabled"] and entry.get("enabled", True) != definition["enabled"]:
        # 启停是逻辑定义里管着的字段：外部把 Pi 条目的 `enabled` 改回去也算漂移。
        changed.append("enabled")
    return {
        "state": "drifted" if changed else "in_sync",
        "fields": changed,
        "reason": None,
        "span": span,
        "fragment": fragment,
        "current": current,
        "expected": expected,
    }


def definition_text(definition: dict) -> str:
    return json.dumps(definition, ensure_ascii=False, indent=2) + "\n"


def definition_diff(before: dict, after: dict) -> str:
    """逻辑定义的字段级 diff：只比较规范化后的定义，不含 Host 侧渲染差异。"""
    old = definition_text(before).splitlines(keepends=True)
    new = definition_text(after).splitlines(keepends=True)
    return "".join(difflib.unified_diff(old, new, fromfile="a/server.json", tofile="b/server.json"))
