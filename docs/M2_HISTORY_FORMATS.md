# M2 历史会话格式

**本机观察（2026-10-28），未按官方文档核实。** 本文只写字段名和结构，示例值一律虚构（`/home/alice/proj`、`11111111-…`）。标 `unknown` 的是这次没探测清楚的地方，不猜。`tools/history.py` 按本文的字段名解析；格式变化时先改这里，再改代码。同类知识另见 `~/AI/temper/analysis/adapters.py`（只读参考）。

## 采集范围

| Host | `--root` | 采集的 glob（相对 root） | 默认排除 |
|---|---|---|---|
| claude | `<home>/.claude` | `projects/*/*.jsonl` | 无 |
| codex | `<home>/.codex` | `sessions/**/*.jsonl` | 无 |
| pi | `<home>/.pi/agent` | `sessions/**/*.jsonl` | `sessions/subagent/*` |

一条 JSONL 记录对应一行。Episode 的 `line` 是物理行号（从 1 开始，空行也占一行），配 `source_path` 和源文件 `sha256` 才能定位回原文。

## Claude Code

本机观察：12 个项目目录、285 个会话文件；另有 2 个子代理记录在 `projects/<项目目录>/<会话 id>/subagents/` 下（不在采集范围）。

| 项目 | 位置 | 说明 |
|---|---|---|
| 目录布局 | `<root>/projects/<项目目录>/<会话 id>.jsonl` | 项目目录名是 cwd 里的 `/` 换成 `-`；子代理记录在 `projects/<项目目录>/<会话 id>/subagents/agent-*.jsonl`（**不采集**） |
| 会话 id | 每条记录的 `sessionId`（assistant 记录另有同值的 `session_id`） | 等于文件名去掉 `.jsonl`；缺失时退回文件名 |
| 项目 | 记录的 `cwd` | 记录级字段，取最近一条非空值 |
| 时间戳 | 记录的 `timestamp` | ISO 8601，UTC，带 `Z`；`mode`、`ai-title`、`file-history-*`、`queue-operation` 等记录没有时间戳 |
| 用户消息 | `type: "user"`，`message.content` 是字符串或 `[{type:"text"}]` 块 | `isMeta`、`isCompactSummary` 为 true 的是注入内容；`<command-`、`<local-command`、`<system-reminder>`、`<task-notification>`、`Caveat:` 前缀同样是注入 |
| 助手消息 | `type: "assistant"`，`message.content` 是块列表 | 块类型：`text`、`thinking`、`tool_use` |
| 工具调用 | assistant 的 `{type:"tool_use", id, name, input}` | `input` 是对象，形状随工具 |
| 工具结果 | user 的 `{type:"tool_result", tool_use_id, is_error, content}` | 同一行可能带顶层 `toolUseResult`（结构化结果，含 `stdout`/`stderr`） |
| 工具失败 | `tool_result.is_error == true` | 只认这个标记，不从结果文本猜 |
| 其他记录 | `system`（`subtype: compact_boundary`）、`attachment`、`queue-operation`、`cost-state`、`api-request`、`api-request-blob`、`file-history-snapshot`、`file-history-delta`、`mode`、`permission-mode`、`atis-latch`、`last-prompt`、`ai-title` | 采集器忽略。`api-request-blob` 里重复一份请求内容，能否用来补全缺失的调用：`unknown` |

工具结果只带 `tool_use_id`。被引用的调用可能不在同一文件（会话恢复或压缩后），这时工具名记 `unknown`。

## Codex

本机观察：33 个会话文件，目录按年月日三层分开；另有 `archived_sessions/` 目录，内容未探测（`unknown`：是否同格式）。

| 项目 | 位置 | 说明 |
|---|---|---|
| 目录布局 | `<root>/sessions/<YYYY>/<MM>/<DD>/rollout-<时间戳>-<会话 id>.jsonl` | 每行是 `{type, payload, timestamp}`，内容几乎都在 `payload` 里 |
| 会话 id | `session_meta` 记录的 `payload.id`（另有同值 `payload.session_id`） | 等于文件名尾部 UUID；缺失时退回文件名 |
| 项目 | `session_meta.payload.cwd` | `turn_context.payload.cwd` 同值 |
| 时间戳 | 记录顶层 `timestamp` | ISO 8601，UTC；`world_state`、`token_usage_record`、`turn_context` 没有顶层时间戳 |
| 用户消息 | `response_item` + `payload.type: "message"` + `payload.role: "user"`，`content` 是块列表 | 块类型 `input_text`、`input_image`；真人输入与注入上下文的区分见下一行 |
| 注入上下文 | `payload.internal_chat_message_metadata_passthrough.content_item_kinds` | 本机取值：`user.text`、`user.image`（真人）；`environments.environment_context`、`permissions.instructions`、`additional_content.*`、`multi_agent.subagent_notification`、`unknown`（注入）。字段缺失的旧格式按文本前缀 `<`、`# AGENTS.md` 判断 |
| 助手消息 | 同上，`payload.role: "assistant"` | 块类型 `output_text`；另有 `role: "developer"`（开发者消息） |
| 工具调用 | `response_item` + `payload.type` 为 `custom_tool_call`（`name`、`input`、`call_id`）或 `function_call`（`name`、`arguments`、`call_id`） | `input`/`arguments` 是字符串，不是对象 |
| 工具结果 | `response_item` + `payload.type` 为 `custom_tool_call_output` 或 `function_call_output`（`call_id`、`output`） | `output` 是块列表或字符串 |
| 工具失败 | `event_msg` + `payload.type: "item_completed"` 的 `payload.item.exit_code` 非 0；或工具结果文本以 `Script failed` 开头 | `item_completed` 的 `item.id` 与工具调用的 `call_id` 不是同一套 id，两者无法按 id 对上，因此一次失败可能产生两条 Episode |
| 其他记录 | `event_msg`（`token_count`、`task_started`、`task_complete`、`turn_aborted`、`thread_settings_applied`）、`token_usage_record`、`turn_context`、`world_state`、`compacted`、`response_item.reasoning`、`agent_message`、`additional_tools` | 采集器忽略 |

## Pi

本机观察：`sessions/` 下 385 个主会话文件、369 个子代理 transcript、1292 个 `sessions/subagent/` 会话文件、15 个分叉会话。

| 项目 | 位置 | 说明 |
|---|---|---|
| 目录布局 | `<root>/sessions/<项目目录>/<ISO 时间戳>_<会话 id>.jsonl` | 项目目录名是 cwd 的 `-` 包裹形式；分叉会话在 `<项目目录>/<会话 id>/forks/<…>.jsonl`；子代理会话在 `sessions/subagent/<父会话 id>/run-N/session.jsonl`（默认排除） |
| 会话 id | 首条 `session` 记录的 `id` | 等于文件名 `<时间戳>_<会话 id>.jsonl` 的后半段；缺失时退回文件名 |
| 项目 | `session` 记录的 `cwd` | |
| 时间戳 | 记录的 `timestamp` | ISO 8601，UTC，毫秒精度 |
| 用户消息 | `type: "message"` + `message.role: "user"`，`content` 是块列表 | 块类型 `text`、`image`；`<skill name="…">` 展开的 skill 正文不是用户写的 |
| 助手消息 | 同上，`message.role: "assistant"` | 块类型 `text`、`thinking`、`toolCall` |
| 工具调用 | assistant 的 `{type:"toolCall", id, name, arguments}` | `arguments` 是对象 |
| 工具结果 | `type: "message"` + `message.role: "toolResult"`（`toolName`、`toolCallId`、`isError`、`content`、`details`） | 本机没在 `details` 里观察到 `exitCode`；`details` 的形状随工具（`diff`/`patch`、`calls`、`results` 等） |
| 工具失败 | `toolResult.isError == true` | 只认这个标记 |
| 其他记录 | `model_change`、`thinking_level_change`、`session_info`、`compaction`、`context_edit`、`custom` 与 `custom_message`（`customType` 如 `subagent-notify`、`goal-state`、`web-search-results`） | 采集器忽略 |
| 另一种 schema | `<项目目录>/*_transcript.jsonl`：顶层没有 `type`，用 `recordType`、`agent`、`runId`、`role`、`text` | 采集器只认 `type`，这类文件不产生 Episode；是否要单独解析：`unknown` |

## kind 的判定（三个 Host 共用）

`kind` 是启发式，召回率和准确率都没评估。数据常量在 `tools/history.py` 顶部。

| kind | 判定 | 定位到哪一行 |
|---|---|---|
| `user_correction` | 用户消息命中纠正词表（`不对`、`不是这样`、`错了`、`别`、`重新`、`wrong`、`don't`、行首 `no,` 等） | 该条用户消息 |
| `tool_failure` | Claude：`tool_result.is_error`；Pi：`toolResult.isError`；Codex：`item_completed.item.exit_code` 非 0 或工具结果以 `Script failed` 开头 | 工具结果（或 Codex 的 `item_completed`） |
| `retry` | 同一会话里相邻两次工具调用完全同（工具名和参数都相同） | 第二次调用。一串连续相同的调用只记第一条 |

`excerpt` 取触发那条记录里的文本：工具失败是「工具名: 结果」，重试是「工具名 + 参数」，纠正取消息开头最多 300 字，因此纠正词可能在截断之外。摘录先脱敏再截断，覆盖 `sk-…`、`ghp_…`、`AKIA…`、`Bearer …`、`-----BEGIN … PRIVATE KEY-----`、`password=`/`api_key=` 后面的值。脱敏只到正则级别，摘录仍按私人数据对待。

## 未知与待核实

- 三种 Host 的子代理记录都存在于同一批目录下，只有 Pi 的 `sessions/subagent/` 默认排除；Claude 的 `subagents/` 和 Codex 的 `multi_agent.subagent_notification` 是否要同样处理：`unknown`。
- Codex `archived_sessions/`、Claude `api-request-blob` 的复用价值：`unknown`。
- 各 Host 的版本号字段（Claude `version`、Codex `cli_version`、Pi `version`）与格式变化的关系：`unknown`。
