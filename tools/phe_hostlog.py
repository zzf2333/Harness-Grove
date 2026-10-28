"""实验续作记录与阶段推导（标准库）。

`log` 把一条"哪个 Host 做了哪一步"的声明追加到 `experiments/<id>/host_log.jsonl`；`status`
只读推导阶段，不写任何文件。host_log 放在 proposal 冻结哈希之外：proposal.json 冻结后绑定
proposal.sha256 和 approval.json，run 期间逐次校验，所以续作记录不能写进 proposal。

阶段映射（以 tools/phe.py 的实际读写和 docs/M-1_PROTOCOL.md「实验目录与报告」为准）：

    阶段        证据文件          由谁写入
    empty       无
    drafted     proposal.json     Host 起草（从模板复制）
    frozen      proposal.sha256   Host 冻结时对 proposal.json 的原始字节算 SHA-256
    approved    approval.json     Host 记录用户批准（proposal_sha256 绑定 proposal.sha256）
    running     runs.jsonl        phe.py run_slot 每完成一个 slot 追加一行
    judged      result.json       phe.py judge；--out 写到别处时本目录不出现该文件
    reported    report.md         Host 写报告

不参与阶段推导的实验文件：config.json（phe.py load 读取执行参数）、stop.json（phe.py run
结束时写入 stop_reason，值为 null 表示没有停止事件）、artifacts/（每个 slot 的原始产物）。

推导规则：阶段取"自身证据文件存在"的最靠后一步，链上有缺口时把缺的文件写进 notes，不中断。
host_log 是声明，文件存在性是证据：最后一条合法声明的 step 比文件证据推出的阶段更靠后时
inconsistent 为 true，阶段仍以文件为准，退出码仍是 0。
"""

import json
import os
import stat
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HOSTS = ("claude", "codex", "pi")
STEPS = ("draft", "freeze", "approve", "run", "judge", "report")
STAGES = ("empty", "drafted", "frozen", "approved", "running", "judged", "reported")
STEP_STAGE = dict(zip(STEPS, STAGES[1:], strict=True))
NOTE_MAX_CHARS = 200
LOG_NAME = "host_log.jsonl"

# (文件名, 对应阶段)，顺序即阶段顺序。
GATED = (
    ("proposal.json", "drafted"),
    ("proposal.sha256", "frozen"),
    ("approval.json", "approved"),
    ("runs.jsonl", "running"),
    ("result.json", "judged"),
    ("report.md", "reported"),
)
UNSTAGED = ("config.json", "stop.json")


def experiments_root(root: Path | None = None) -> Path:
    return ((REPO_ROOT if root is None else Path(root)) / "experiments").resolve()


def experiment_dir(experiment: Path, root: Path | None = None) -> Path:
    """返回已存在、且真实路径位于 `<root>/experiments/` 之下的实验目录。"""
    base = experiments_root(root)
    exp = Path(experiment).resolve()
    if exp == base or base not in exp.parents:
        raise ValueError(f"实验目录必须位于 {base} 之下：{experiment}")
    if not exp.is_dir():
        raise ValueError(f"实验目录不存在：{experiment}")
    return exp


def validate_entry(host: str, step: str, note: str) -> None:
    if host not in HOSTS:
        raise ValueError(f"--host 只能是 {'、'.join(HOSTS)}：{host}")
    if step not in STEPS:
        raise ValueError(f"--step 只能是 {'、'.join(STEPS)}：{step}")
    if len(note) > NOTE_MAX_CHARS:
        raise ValueError(f"--note 最多 {NOTE_MAX_CHARS} 字，当前 {len(note)} 字")


def log_path(exp: Path) -> Path:
    return exp / LOG_NAME


def append_line(path: Path, line: str) -> None:
    """用 O_APPEND 追加一整行并 fsync：已有行不会被截断或改写。

    O_NOFOLLOW 拒绝软链；打开后对同一个描述符 fstat，拒绝非普通文件和硬链接（st_nlink != 1），
    否则会向 proposal.json 或实验目录外的同一个 inode 追加内容。检查和写入用同一个描述符。
    """
    try:
        fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    except OSError as exc:
        raise ValueError(f"{LOG_NAME} 无法安全打开（软链或不可写）：{path}") from exc
    with os.fdopen(fd, "r+b") as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError(f"{LOG_NAME} 不是独立的普通文件（硬链接或特殊文件），拒绝写入：{path}")
        # 已有文件最后一行没有换行时先补一个，否则新记录会接在那一行后面。
        prefix = b""
        if info.st_size:
            f.seek(-1, os.SEEK_END)
            prefix = b"" if f.read(1) == b"\n" else b"\n"
        f.write(prefix + line.encode("utf-8") + b"\n")
        f.flush()
        os.fsync(f.fileno())


def log(experiment: Path, host: str, step: str, note: str = "", root: Path | None = None) -> int:
    """追加一条续作记录。返回退出码：0 成功，2 校验不通过（此时不写文件）。"""
    try:
        validate_entry(host, step, note)
        exp = experiment_dir(experiment, root)
        path = log_path(exp)
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "host": host, "step": step, "note": note}
        append_line(path, json.dumps(entry, ensure_ascii=False))
    except ValueError as exc:
        print(f"phe log: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(entry, ensure_ascii=False))
    return 0


def read_log(exp: Path) -> tuple[list[dict], list[str]]:
    """返回 (合法记录, notes)。坏行只记行号，不中断。"""
    path = exp / LOG_NAME
    if path.is_symlink():
        return [], [f"{LOG_NAME} 是软链，未读取"]
    if not path.exists():
        return [], []
    entries, notes = [], []
    # 只按 JSONL 的 "\n" 分行；splitlines 会把 note 里的 U+2028 等当成行边界。
    for number, line in enumerate(path.read_text(errors="replace").split("\n"), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            entry = None
        if not isinstance(entry, dict) or entry.get("host") not in HOSTS or entry.get("step") not in STEPS:
            notes.append(f"{LOG_NAME} 第 {number} 行无法解析或字段不合法")
            continue
        entries.append(entry)
    return entries, notes


def derive(exp: Path) -> dict:
    """只读推导阶段。返回 stage、evidence、last_log、inconsistent、notes。"""
    evidence = {name: (exp / name).exists() for name in [n for n, _ in GATED] + list(UNSTAGED)}
    stage = "empty"
    for name, name_stage in GATED:
        if evidence[name]:
            stage = name_stage
    entries, notes = read_log(exp)
    gaps = [name for name, s in GATED if STAGES.index(s) <= STAGES.index(stage) and not evidence[name]]
    if gaps:
        notes.append(f"阶段 {stage} 缺少证据文件：{'、'.join(gaps)}")
    last_log = entries[-1] if entries else None
    inconsistent = last_log is not None and STAGES.index(STEP_STAGE[last_log["step"]]) > STAGES.index(stage)
    return {
        "stage": stage,
        "evidence": evidence,
        "last_log": last_log,
        "inconsistent": inconsistent,
        "notes": notes,
    }


def summary(exp: Path, report: dict) -> str:
    present = [name for name, exists in report["evidence"].items() if exists]
    last = report["last_log"]
    last_text = f"{last['host']}/{last['step']}" if last else "无"
    conflict = "是" if report["inconsistent"] else "否"
    return (
        f"{exp.name}: stage={report['stage']} 证据={'、'.join(present) or '无'} "
        f"last_log={last_text} inconsistent={conflict}"
    )


def status(experiment: Path, root: Path | None = None) -> int:
    """只读推导阶段。返回退出码：0 成功，2 实验目录不合法。"""
    try:
        exp = experiment_dir(experiment, root)
    except ValueError as exc:
        print(f"phe status: {exc}", file=sys.stderr)
        return 2
    report = derive(exp)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(summary(exp, report), file=sys.stderr)
    return 0
