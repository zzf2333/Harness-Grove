"""JSON 配置文本的跨度定位与拼接：不重写整份文件（标准库）。

只做一件事：在一份普通 JSON 文本里找到某个对象成员的值跨度、按文件自己的缩进排版渲染托管片段，并用一次
`splice` 替换那一段。值的边界交给 `JSONDecoder.raw_decode`，不做通用 JSON 编辑器，也不把整份文本
`json.dumps` 重写。

`mcp_config.py`（MCP 条目的 `mcpServers` 成员）与 `mcp_toggle.py`（Claude Code 项目启停数组）共用这一层。
"""

import json


def skip_ws(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\n\r":
        index += 1
    return index


def expect(text: str, index: int, char: str) -> int:
    index = skip_ws(text, index)
    if index >= len(text) or text[index] != char:
        raise ValueError(f"{index} 处不是 {char!r}：配置文件不是可定位的 JSON 对象")
    return index + 1


def object_members(text: str, start: int) -> tuple[list[tuple[str, int, int, int]], int]:
    """`start` 处对象的成员 [(键, 键起点, 值起点, 值终点)] 与对象结束位置；值用 raw_decode 跳过。"""
    decoder = json.JSONDecoder()
    members: list[tuple[str, int, int, int]] = []
    index = skip_ws(text, expect(text, start, "{"))
    if index < len(text) and text[index] == "}":
        return members, index + 1
    while True:
        key_start = skip_ws(text, index)
        key, index = decoder.raw_decode(text, key_start)
        if not isinstance(key, str):
            raise TypeError(f"{index} 处的成员名不是字符串：无法定位对象成员")
        index = expect(text, index, ":")
        value_start = skip_ws(text, index)
        _value, value_end = decoder.raw_decode(text, value_start)
        members.append((key, key_start, value_start, value_end))
        index = skip_ws(text, value_end)
        if index < len(text) and text[index] == ",":
            index += 1
            continue
        if index < len(text) and text[index] == "}":
            return members, index + 1
        raise ValueError(f"{index} 处不是 ',' 或 '}}'：配置文件不是可定位的 JSON 对象")


def array_elements(text: str, start: int) -> tuple[list[tuple[int, int]], int]:
    """`start` 处数组的元素跨度与右括号位置；元素边界用 raw_decode 跳过。"""
    decoder = json.JSONDecoder()
    elements: list[tuple[int, int]] = []
    index = skip_ws(text, expect(text, start, "["))
    if index < len(text) and text[index] == "]":
        return elements, index
    while True:
        value_start = skip_ws(text, index)
        _value, value_end = decoder.raw_decode(text, value_start)
        elements.append((value_start, value_end))
        index = skip_ws(text, value_end)
        if index < len(text) and text[index] == ",":
            index += 1
            continue
        if index < len(text) and text[index] == "]":
            return elements, index
        raise ValueError(f"{index} 处不是 ',' 或 ']'：配置文件不是可定位的 JSON 数组")


def column_of(text: str, index: int) -> int:
    """`index` 所在行的缩进列：渲染片段时按它对齐，让托管跨度里的排版贴近原文件。"""
    return index - (text.rfind("\n", 0, index) + 1)


def server_location(text: str, server: str) -> dict | None:
    """指定 server 条目的跨度与排版位置：值跨度、(键)缩进列、文件自己的缩进步长。

    没有 `mcpServers` 或没有这个成员时返回 None。步长取自 `mcpServers` 与它第一个成员的缩进差，
    这样渲染出来的托管跨度贴近原文件排版，而不是写死 2 或 4。
    """
    if not text.strip():
        return None
    top, _end = object_members(text, skip_ws(text, 0))
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


def splice(text: str, span: tuple[int, int], replacement: str) -> str:
    """只替换跨度内的字节；跨度前后原样保留。"""
    start, end = span
    return text[:start] + replacement + text[end:]
