"""Claude Code 的项目启停：`~/.claude.json` 的 `projects[<项目键>].disabledMcpServers`（标准库）。

一个 server 在一个项目里是否被禁用，落点是这个数组里的名字：有 = 禁用，没有 = 启用，缺这个列表等价于空列表。
写入只增删绑定的那个名字：数组里其他名字与文件外围字节保留，项目条目本身不由 PHE 创建。

依据（2026-10-31 从本机安装的 Claude Code 2.1.312 二进制只读定位，静态证据，不是 Host 实跑）：产品自身说明写
`/mcp disable` 把名字写进 `~/.claude.json` 项目条目的 `disabledMcpServers`，`/mcp enable` 删除该名；项目键是
启动时工作目录的路径字符串（NFC 规范化，不做 realpath）。所以本片只按调用方显式给出的项目键读写，值不命中
项目键时直接报错，不回落、不创建项目条目。`disabledMcpjsonServers` / `enabledMcpjsonServers`（项目 `.mcp.json`
的审批）、`hasTrustDialogAccepted`（信任）与默认关闭的内置 server 是另外几套开关，本模块不碰。
"""

import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_config import TOGGLE_ARRAYS
from mcp_span import array_elements, object_members, skip_ws


def project_object(text: str, project_key: str) -> tuple[list[tuple[str, int, int, int]], int]:
    """`projects[<项目键>]` 的成员与右括号位置；缺 `projects` 或缺这个键时报具体原因。"""
    if not text.strip():
        raise ValueError("配置文件是空的")
    top, _end = object_members(text, skip_ws(text, 0))
    projects = next((member for member in top if member[0] == "projects"), None)
    if projects is None:
        raise ValueError("配置文件没有 projects：本片不创建项目条目")
    buckets, _buckets_end = object_members(text, projects[2])
    for key, _key_start, start, _end in buckets:
        if key != project_key:
            continue
        if text[start] != "{":
            raise ValueError(f"项目键 {project_key} 的值不是对象：未支持")
        members, close = object_members(text, start)
        return members, close - 1
    raise ValueError(f"projects 里没有项目键 {project_key}：本片不创建项目条目，请先在这个工作目录启动一次 Claude Code")


def _removal_span(elements: list[tuple[int, int]], index: int) -> tuple[int, int]:
    """删掉第 `index` 个元素（连同一个逗号）的跨度；其他元素与行外空白不动。"""
    if index > 0:
        return (elements[index - 1][1], elements[index][1])
    if len(elements) > 1:
        return (elements[0][0], elements[1][0])
    return elements[0]


def toggle_edit(adapter: str, text: str, project_key: str, server: str, disabled: bool) -> dict:
    """把项目启停翻到 `disabled` 要替换的跨度与替换文本；已经是目标状态时 `change` 为 False。

    只定位启停数组与绑定的 server 名：其他名字、其他字段与外围字节都不进返回值，也不进预览、计划或 journal。
    """
    array_name = TOGGLE_ARRAYS[adapter]
    members, close = project_object(text, project_key)
    member = next((item for item in members if item[0] == array_name), None)
    if member is None:
        if not disabled:
            return _result(False, False, 0, disabled, (close, close), "", text)
        separator = ", " if members else ""
        return _result(False, False, 0, disabled, (close, close), f'{separator}"{array_name}": ["{server}"]', text)
    elements, bracket = array_elements(text, member[2])
    index = next(
        (position for position, (start, end) in enumerate(elements) if json.loads(text[start:end]) == server), None
    )
    if index is not None:
        if not disabled:
            return _result(True, True, len(elements) - 1, disabled, _removal_span(elements, index), "", text)
        return _result(True, True, len(elements) - 1, disabled, (elements[index][1], elements[index][1]), "", text)
    if not disabled:
        return _result(False, True, len(elements), disabled, (bracket, bracket), "", text)
    if elements:
        return _result(False, True, len(elements), disabled, (elements[-1][1], elements[-1][1]), f', "{server}"', text)
    return _result(False, True, 0, disabled, (bracket, bracket), f'"{server}"', text)


def _result(
    present: bool, array_present: bool, others: int, disabled: bool, span: tuple[int, int], replacement: str, text: str
) -> dict:
    return {
        "present": present,
        "array_present": array_present,
        "others": others,
        "disabled": disabled,
        "span": span,
        "replacement": replacement,
        "before_fragment": text[span[0] : span[1]],
        "change": replacement != text[span[0] : span[1]],
    }


def toggle_drift(adapter: str, definition: dict, text: str, project_key: str, server: str) -> dict:
    """项目启停是否与逻辑定义一致：只比对绑定的 server 名，报逻辑字段 `enabled`。"""
    if "enabled" not in definition:
        return {"state": "not_managed", "fields": [], "reason": None}
    try:
        edit = toggle_edit(adapter, text, project_key, server, disabled=not definition["enabled"])
    except ValueError as exc:
        return {"state": "unsupported", "fields": [], "reason": str(exc)}
    return {
        "state": "drifted" if edit["change"] else "in_sync",
        "fields": ["enabled"] if edit["change"] else [],
        "reason": None,
    }


def toggle_diff(adapter: str, edit: dict, project_key: str) -> str:
    """启停改动的文字 diff：只报绑定的 server 与「数组另有几项」，不复制其他名字。"""
    array_name = TOGGLE_ARRAYS[adapter]
    others = f"数组另有 {edit['others']} 项原样保留" if edit["array_present"] else "原本没有这个列表"
    current = "有这个名字（禁用）" if edit["present"] else "没有这个名字（启用）"
    desired = "有这个名字（禁用）" if edit["disabled"] else "没有这个名字（启用）"
    lines = [
        f"--- 项目启停：项目键 {project_key} 的 {array_name}",
        "+++ 应用后",
        f"-{current}；{others}",
        f"+{desired}；{others}",
    ]
    if not edit["change"]:
        lines.append(" 当前已经是目标状态：这个目标不写字节")
    elif edit["replacement"]:
        lines.append(f" 插入片段：`{edit['replacement']}`")
    else:
        lines.append(f" 删除片段：`{edit['before_fragment']}`")
    return "\n".join(lines) + "\n"


def toggle_restore_span(entry: dict) -> tuple[int, int]:
    """按 journal 的编辑方向反推恢复跨度：应用是插入就删掉插入区间，应用是删除就在删除起点插回。"""
    if entry["before_fragment"]:
        return (entry["span"][0], entry["span"][0])
    return (entry["span"][0], entry["span"][0] + len(entry["after_fragment"]))
