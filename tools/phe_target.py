"""Target 适配：选择 Claude Code / Codex，构造默认 argv，解析 Codex JSONL（标准库）。

Codex 事件形状取自上游 `rust-v0.163.0` 的 `exec/src/exec_events.rs`（版本号一致不等于二进制
commit 一致）。解析只认事件 `type`：`turn.completed.usage` 是 thread 累计值，只取最后一次，
不把 cached / reasoning / cache_write 再加总；失败、致命 error、非零退出和坏行都保留 error
语义。缺失 usage、字段类型非法或负数不补零；全 0 可能是上游缺 telemetry 的默认值，与真实
零用量不可区分，因此也不当作有效测量。费用在价目表冻结前恒为 None，由现有 telemetry 缺口
路径停止。

本模块不启动 Target，也不读取 Codex 凭据：指纹只覆盖 `~/.codex/AGENTS.md` 和
`~/.codex/config.toml`。
"""

import hashlib
import json
from pathlib import Path

AGENTS = ("claude-code", "codex")
DEFAULT_AGENT = "claude-code"
CLAUDE_FILES = (".claude/CLAUDE.md", ".claude/RTK.md", ".claude/settings.json")
CODEX_FILES = (".codex/AGENTS.md", ".codex/config.toml")
USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)


def sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def argv_digest(argv) -> str:
    return hashlib.sha256(json.dumps(argv).encode()).hexdigest()


def select(proposal: dict, cfg: dict) -> dict:
    """从 proposal.target.agent 选 Target；config.target_agent 只用于声明同一选择。

    Codex 只用规范 argv：config.target_argv 不能覆盖它，冻结的 model_id 也不能被换掉。
    """
    target = proposal.get("target") or {}
    if not isinstance(target, dict):
        raise TypeError("proposal.target 必须是对象")
    agent = target.get("agent")
    if agent is None:
        agent = DEFAULT_AGENT
    if agent not in AGENTS:
        raise ValueError(f"不支持的 Target agent：{agent!r}；只支持 {'、'.join(AGENTS)}")
    declared = cfg.get("target_agent")
    if declared is not None and declared != agent:
        raise ValueError(f"config.target_agent 与 proposal.target.agent 冲突：{declared!r} != {agent!r}")
    model_id = target.get("model_id")
    if agent == "codex" and (not isinstance(model_id, str) or not model_id.strip()):
        raise ValueError("Codex Target 需要 proposal.target.model_id 为已冻结的非空字符串")
    if agent == "codex" and cfg.get("target_argv"):
        raise ValueError("Codex Target 不接受 config.target_argv 覆盖；本地验证在测试层替换 subprocess")
    return {"agent": agent, "model_id": model_id}


def default_argv(target: dict, cfg: dict, work: Path, output: Path) -> list[str]:
    if target["agent"] == "codex":
        return [
            "codex",
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "-s",
            "workspace-write",
            "-C",
            str(work),
            "-m",
            target["model_id"],
            "-o",
            str(output / "last-message.txt"),
            cfg["prompt"],
        ]
    return [
        "claude",
        "-p",
        cfg["prompt"],
        *cfg["claude_args"],
        "--settings",
        json.dumps(cfg["settings"]),
        "--debug-file",
        str(output / "debug.log"),
    ]


def argv_sha256(argv: list[str], agent: str, custom: bool) -> str:
    """Claude 默认命令沿用旧口径；自定义命令和 Codex 记录完整 argv。"""
    return argv_digest(argv if custom or agent == "codex" else argv[3:])


def user_files(agent: str, home: Path | None = None) -> list[Path]:
    root = Path.home() if home is None else home
    return [root / name for name in (CODEX_FILES if agent == "codex" else CLAUDE_FILES)]


def user_fingerprint(agent: str, home: Path | None = None) -> dict:
    return {str(path.resolve()): sha256_file(path.resolve()) for path in user_files(agent, home)}


def parse_codex(stdout: bytes, exit_code: int, timed_out: bool = False) -> dict:
    """解析 `codex exec --json` 的逐行 JSONL；坏行不补零，失败语义不被后续 completed 覆盖。

    最后一个 `turn.started` 没有被 completed/failed 闭合时，流在 turn 内结束，视为不完整。
    """
    state = {
        "thread_id": None,
        "turn_status": "missing",
        "usage": None,
        "tokens": None,
        "num_turns": 0,
        "num_turns_basis": "codex_turn_started_events",
        "error_message": None,
        "errors": [],
        "final_message": None,
        "command_exits": [],
        "parse_error": None,
        "usage_all_zero": False,
        "is_error": False,
    }
    completed_seen = False
    failure_seen = False
    open_turn = None

    def fail(message: str) -> None:
        if state["parse_error"] is None:
            state["parse_error"] = message

    for lineno, raw in enumerate(stdout.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            fail(f"line {lineno}: 非 JSON（{exc}）")
            continue
        if not isinstance(event, dict):
            fail(f"line {lineno}: 事件不是对象")
            continue
        kind = event.get("type")
        if not isinstance(kind, str):
            fail(f"line {lineno}: 事件缺少字符串 type")
            continue
        if kind == "thread.started":
            if isinstance(event.get("thread_id"), str):
                state["thread_id"] = event["thread_id"]
        elif kind == "turn.started":
            state["num_turns"] += 1
            open_turn = lineno
        elif kind == "turn.completed":
            open_turn = None
            usage = usage_from_event(event.get("usage"))
            if usage is None:
                fail(f"line {lineno}: turn.completed.usage 缺失或非法")
                state["usage"] = state["tokens"] = None
                continue
            completed_seen = True
            if all(value == 0 for value in usage.values()):
                state["usage_all_zero"] = True
                state["usage"] = state["tokens"] = None
            else:
                state["usage_all_zero"] = False
                state["usage"] = usage
                state["tokens"] = usage["input_tokens"] + usage["output_tokens"]
        elif kind == "turn.failed":
            open_turn = None
            failure_seen = True
            message = error_message(event.get("error"))
            if message is None:
                fail(f"line {lineno}: turn.failed.error.message 缺失或非法")
            else:
                state["errors"].append({"source": "turn.failed", "message": message})
        elif kind == "error":
            failure_seen = True
            message = event.get("message")
            if not isinstance(message, str):
                fail(f"line {lineno}: error.message 缺失或非法")
            else:
                state["errors"].append({"source": "error", "message": message})
        elif kind.startswith("item."):
            parse_item(event.get("item"), state, lineno, fail)
        # 未知事件类型按契约容忍，不改变终态。
    if not completed_seen and not failure_seen:
        fail("missing turn.completed/turn.failed terminal event")
    if open_turn is not None:
        fail(f"line {open_turn}: turn.started 之后没有 turn.completed/turn.failed，流在 turn 内结束")
    if state["parse_error"] is not None:
        state["usage"] = state["tokens"] = None
    if state["errors"]:
        state["error_message"] = state["errors"][-1]["message"]
    if failure_seen:
        state["turn_status"] = "failed"
    elif completed_seen:
        state["turn_status"] = "completed"
    state["is_error"] = bool(
        timed_out
        or exit_code != 0
        or failure_seen
        or state["parse_error"] is not None
        or state["turn_status"] != "completed"
    )
    return state


def usage_from_event(raw) -> dict | None:
    """五个字段全部为 int 且非负才有效；缺字段、bool、字符串、负数一律不合格。"""
    if not isinstance(raw, dict):
        return None
    usage = {}
    for field in USAGE_FIELDS:
        value = raw.get(field)
        if type(value) is not int or value < 0:
            return None
        usage[field] = value
    return usage


def error_message(raw) -> str | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("message"), str):
        return None
    return raw["message"]


def parse_item(item, state: dict, lineno: int, fail) -> None:
    if not isinstance(item, dict) or not isinstance(item.get("type"), str):
        fail(f"line {lineno}: item 或 item.type 缺失/非法")
        return
    kind = item["type"]
    if kind == "agent_message":
        text = item.get("text")
        if not isinstance(text, str):
            fail(f"line {lineno}: agent_message.text 缺失或非法")
            return
        state["final_message"] = text
    elif kind == "command_execution":
        command, exit_code, status = item.get("command"), item.get("exit_code"), item.get("status")
        if command is not None and not isinstance(command, str):
            fail(f"line {lineno}: command_execution.command 类型非法")
            return
        if exit_code is not None and type(exit_code) is not int:
            fail(f"line {lineno}: command_execution.exit_code 类型非法")
            return
        if status is not None and not isinstance(status, str):
            fail(f"line {lineno}: command_execution.status 类型非法")
            return
        if type(exit_code) is int:
            state["command_exits"].append({"command": command, "exit_code": exit_code, "status": status})


def claude_usage_record(result: dict, tok_key: str) -> dict:
    """Claude `-p --output-format json` 的旧口径：四个分量相加，费用取 total_cost_usd。"""
    model_usage = result.get("modelUsage") or {}
    total = 0
    for usage in model_usage.values():
        total += sum(
            usage.get(key, 0)
            for key in ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
        )
    return {
        "model_usage": model_usage,
        tok_key: total if model_usage else None,
        "estimated_cost_usd_v1": result.get("total_cost_usd"),
        "num_turns": result.get("num_turns"),
        "subtype": result.get("subtype"),
        "is_error": result.get("is_error"),
        "duration_ms": result.get("duration_ms"),
        "duration_api_ms": result.get("duration_api_ms"),
    }


def codex_record(parsed: dict) -> dict:
    """映射到 runs.jsonl 现有字段；费用没有来源，保持 None 让 telemetry 缺口路径停止。"""
    usage = parsed["usage"]
    return {
        "model_usage": {"codex": usage} if usage else {},
        "estimated_cost_usd_v1": None,
        "cost_basis": "not_available_codex_exec_json_has_no_cost",
        "telemetry_source": "codex_exec_jsonl",
        "num_turns": parsed["num_turns"],
        "num_turns_basis": parsed["num_turns_basis"],
        "subtype": None,
        "is_error": parsed["is_error"],
        "duration_ms": None,
        "duration_api_ms": None,
        "thread_id": parsed["thread_id"],
        "turn_status": parsed["turn_status"],
        "usage": usage,
        "usage_all_zero": parsed["usage_all_zero"],
        "error_message": parsed["error_message"],
        "errors": parsed["errors"],
        "final_message": parsed["final_message"],
        "command_exits": parsed["command_exits"],
        "parse_error": parsed["parse_error"],
    }
