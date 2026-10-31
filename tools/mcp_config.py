"""MCP server 条目的逻辑定义与两种 JSON 适配器：有界定位、渲染与条目级比对（标准库）。

范围（R3b 第一片）：Pi 的 `mcpServers`（用户级 `.pi/agent/mcp.json`、项目级 `.pi/mcp.json`）与 Claude Code
项目级 `.mcp.json` 里**已经存在**的 server 条目。写入只替换 `mcpServers` 下指定成员的值跨度：未托管的
server 与文件外围字节原样保留，不用 `json.dumps` 重写整个文件。新增与删除条目本片未支持。

凭证：不读、不解析 `env` / `headers` 的值，也不执行 `!command`。逻辑定义里 `env` / `headers` 的值必须含
`${NAME}` 引用，`url` 不得带 userinfo；其他字面值、`oauth`、`timeout`、`exposure`、`description`、
`headersHelper` 等本片不支持的字段会让该条目不可托管，并明确报出字段名（不做通用 secret 扫描，也不声称
能识别所有内联凭证）。

官方依据（2026-11-01 核对）：

- Pi 1.4.0 `docs/mcp.md`：`mcpServers`；stdio 用 `command` / `args` / `env` / `cwd`；HTTP 用 `url` / `headers`；
  `type` 可选（`stdio` / `http` / `streamable-http`），`sse` 被拒；`enabled: false` 保留条目不连接；
  `env` / `headers` 可用 `${NAME}`。
- Claude Code <https://code.claude.com/docs/en/mcp>：项目级 `.mcp.json` 的 `mcpServers`；stdio 用
  `command` / `args` / `env`；HTTP 必须 `"type": "http"`（缺 `type` 会被读成 stdio，`streamable-http` 是别名）；
  `${VAR}` 与 `${VAR:-default}` 在 `command` / `args` / `env` / `url` / `headers` 里展开；条目没有 `enabled`
  字段，启停按项目记在 `~/.claude.json` 的 `disabledMcpServers`，本片不写那个文件。
"""

import difflib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

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
}
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


# 有界定位：只扫最外层对象与 mcpServers 的成员，值的边界交给 JSONDecoder.raw_decode。


def _skip_ws(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\n\r":
        index += 1
    return index


def _expect(text: str, index: int, char: str) -> int:
    index = _skip_ws(text, index)
    if index >= len(text) or text[index] != char:
        raise ValueError(f"{index} 处不是 {char!r}：配置文件不是可定位的 JSON 对象")
    return index + 1


def object_members(text: str, start: int) -> tuple[list[tuple[str, int, int, int]], int]:
    """`start` 处对象的成员 [(键, 键起点, 值起点, 值终点)] 与对象结束位置；值用 raw_decode 跳过。"""
    decoder = json.JSONDecoder()
    members: list[tuple[str, int, int, int]] = []
    index = _skip_ws(text, _expect(text, start, "{"))
    if index < len(text) and text[index] == "}":
        return members, index + 1
    while True:
        key_start = _skip_ws(text, index)
        key, index = decoder.raw_decode(text, key_start)
        if not isinstance(key, str):
            raise TypeError(f"{index} 处的成员名不是字符串：无法定位 mcpServers")
        index = _expect(text, index, ":")
        value_start = _skip_ws(text, index)
        _value, value_end = decoder.raw_decode(text, value_start)
        members.append((key, key_start, value_start, value_end))
        index = _skip_ws(text, value_end)
        if index < len(text) and text[index] == ",":
            index += 1
            continue
        if index < len(text) and text[index] == "}":
            return members, index + 1
        raise ValueError(f"{index} 处不是 ',' 或 '}}'：配置文件不是可定位的 JSON 对象")


def server_location(text: str, server: str) -> dict | None:
    """指定 server 条目的跨度与排版位置：值跨度、(键)缩进列、文件自己的缩进步长。

    没有 `mcpServers` 或没有这个成员时返回 None。步长取自 `mcpServers` 与它第一个成员的缩进差，
    这样渲染出来的托管跨度贴近原文件排版，而不是写死 2 或 4。
    """
    if not text.strip():
        return None
    top, _end = object_members(text, _skip_ws(text, 0))
    for key, key_start, start, _value_end in top:
        if key != "mcpServers":
            continue
        inner, _inner_end = object_members(text, start)
        step = 2
        if inner:
            guessed = column_of(text, inner[0][1]) - column_of(text, key_start)
            if guessed > 0:
                step = guessed
        for name, inner_key_start, inner_start, inner_end in inner:
            if name == server:
                return {"span": (inner_start, inner_end), "column": column_of(text, inner_key_start), "step": step}
    return None


def server_span(text: str, server: str) -> tuple[int, int] | None:
    """`mcpServers` 下指定成员的值跨度；没有 `mcpServers` 或没有这个成员时返回 None。"""
    location = server_location(text, server)
    return location["span"] if location is not None else None


def column_of(text: str, index: int) -> int:
    """`index` 所在行的缩进列：渲染片段时按它对齐，让托管跨度里的排版贴近原文件。"""
    return index - (text.rfind("\n", 0, index) + 1)


def splice(text: str, span: tuple[int, int], replacement: str) -> str:
    """只替换跨度内的字节；跨度前后原样保留。"""
    start, end = span
    return text[:start] + replacement + text[end:]


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


def _check_type(adapter: str, transport: str, value) -> str:
    if not isinstance(value, str) or value not in TRANSPORT_TYPES[transport]:
        raise ValueError(f"type 与传输不一致：{transport} 只接受 {' / '.join(TRANSPORT_TYPES[transport])}")
    if adapter == "claude" and transport == "stdio":
        raise ValueError("Claude 的 stdio 条目没有 type 字段（官方只要求 HTTP 写 type）：未支持，不猜")
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
    fields = list(TRANSPORT_FIELDS[transport])
    if transport == "http" or adapter == "pi":
        fields.append("type")
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
        _check_type(adapter, transport, entry["type"])
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


def render_entry_text(adapter: str, definition: dict, column: int = 0, step: int = 2) -> str:
    """渲染片段文本；多行时按 `column` 与文件自己的缩进步长对齐到条目原来的位置。"""
    text = json.dumps(render_entry(adapter, definition), ensure_ascii=False, indent=step)
    return text.replace("\n", "\n" + " " * column)


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
        current = render_entry(adapter, definition_from_entry(adapter, entry, server))
        expected = render_entry(adapter, definition)
    except ValueError as exc:
        return {"state": "unsupported", "fields": [], "reason": str(exc), "span": span, "fragment": fragment}
    fields = sorted(set(current) | set(expected))
    changed = [name for name in fields if current.get(name) != expected.get(name)]
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
