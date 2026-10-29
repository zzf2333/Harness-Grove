"""执行层写入隔离：用 macOS 内置 /usr/bin/sandbox-exec 包裹 Target、外置验收和 Git 采集（标准库）。

保证（config 声明了 `sandbox` 时）：Target、它启动的子进程、外置验收器（可能执行 Target 改过的
项目代码/测试）以及 runner 的后续 Git 采集都受同一套规则约束；只能写本 slot 的工作目录、runner
为本 slot 创建的专用临时目录，以及 Target 的输出子目录。每次启动 Target 之前，用本 slot 实际写
下的 policy 文件和完整允许清单实测允许/禁止写入与子进程继承，不符合预期就拒绝启动。

不保证：读范围（登录凭据仍可读，网络按 config 的 allow/deny 透传）；`/dev/null` 和 `/dev/fd/*`
的写入放行；只在 macOS 上实现，其他平台声明了就拒绝启动，绝不静默按无隔离方式执行。沙箱程序
固定为 `/usr/bin/sandbox-exec`，config 不能替换可执行文件或追加写目录。
"""

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

MECHANISM = "macos-sandbox-exec"
EXECUTABLE = Path("/usr/bin/sandbox-exec")
NETWORKS = ("allow", "deny")
ALLOWED_KEYS = ("mechanism", "network")
TARGET_POLICY = "policy-target.sb"
ORACLE_POLICY = "policy-oracle.sb"
GIT_POLICY = "policy-git.sb"
# 写 /dev/null 和 /dev/fd/* 是普通命令行行为（重定向），不是用户状态写入。
DEVICE_WRITE_RULES = ('(literal "/dev/null")', '(subpath "/dev/fd")')
SENTINEL_TIMEOUT_S = 20


class SandboxError(ValueError):
    """声明的隔离不可用或不生效；调用方必须拒绝启动，不得降级成无隔离执行。"""


def load_settings(cfg: dict) -> dict | None:
    """校验 config.sandbox，返回规范化设置；没声明返回 None。

    只接受机制和网络两项：沙箱程序固定为 macOS 内置程序，写目录由 runner 按 slot 决定。
    声明了就要求机制真的可用；出现可执行文件、写目录或其他覆盖键时明确拒绝，由调用方拒绝
    启动——"隔离配置被忽略"不会变成静默的无隔离执行。
    """
    raw = cfg.get("sandbox")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("config.sandbox 必须是对象")  # noqa: TRY004 —— JSON 配置错误按 ValueError 处理
    unsupported = sorted(set(raw) - set(ALLOWED_KEYS))
    if unsupported:
        raise ValueError(
            f"config.sandbox 不接受 {unsupported}：只支持 mechanism 和 network；"
            f"沙箱程序固定为 {EXECUTABLE}，写目录由 runner 按 slot 决定"
        )
    if raw.get("mechanism") != MECHANISM:
        raise ValueError(f"config.sandbox.mechanism 只支持 {MECHANISM}：{raw.get('mechanism')!r}")
    network = raw.get("network", "allow")
    if network not in NETWORKS:
        raise ValueError(f"config.sandbox.network 只能是 {'、'.join(NETWORKS)}：{network!r}")
    if not (EXECUTABLE.is_file() and os.access(EXECUTABLE, os.X_OK)):
        raise SandboxError(f"sandbox 程序不可用：{EXECUTABLE}；平台不支持时拒绝启动，不做无隔离执行")
    return {"executable": EXECUTABLE, "network": network}


def quoted(path: Path) -> str:
    """SBPL 双引号字符串：只转义反斜杠和双引号。"""
    return str(path).replace("\\", "\\\\").replace('"', '\\"')


def profile_text(settings: dict, roots: list[Path]) -> str:
    """SBPL profile：允许读和按配置决定的网络，禁止一切文件写入，再逐个放行写根。"""
    lines = ["(version 1)", "(allow default)"]
    if settings["network"] == "deny":
        lines.append("(deny network*)")
    lines.append("(deny file-write*)")
    allowed = [f'(subpath "{quoted(root)}")' for root in roots] + list(DEVICE_WRITE_RULES)
    lines.append("(allow file-write* " + " ".join(allowed) + ")")
    return "\n".join(lines) + "\n"


def write_policy(settings: dict | None, policy: Path, roots: list[Path]) -> str | None:
    """把本次实际生效的 profile 写到 policy，返回它的 SHA-256；没声明 sandbox 时返回 None。

    policy 必须落在 Target 不可写的路径（runner 的 artifacts），它同时是哨兵和 Target 使用的
    那一份，避免"测一个 profile、跑另一个"。
    """
    if settings is None:
        return None
    policy.write_text(profile_text(settings, [Path(os.path.realpath(root)) for root in roots]))
    return hashlib.sha256(policy.read_bytes()).hexdigest()


def wrap(settings: dict | None, policy: Path | None) -> list[str]:
    """返回包裹命令的前缀；没声明 sandbox 时返回空列表，命令保持原样。"""
    if settings is None:
        return []
    return [str(settings["executable"]), "-f", str(policy)]


GIT_SAFE_OPTIONS = ("--no-optional-locks", "-c", "core.fsmonitor=false")


def git_env(tmp: Path | None = None) -> dict:
    """Git 采集用的干净环境：不继承 GIT_*，不读用户/系统配置，避免仓库配置注入 helper。"""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }
    if tmp is not None:
        env["TMPDIR"] = str(tmp)
    return env


def git_collect(work: Path, args: list[str], settings=None, policy: Path | None = None, tmp: Path | None = None) -> str:
    """跑一条 Git 采集；声明 sandbox 时同策略包裹，并禁用可能执行程序的仓库配置。"""
    argv = ["git", *GIT_SAFE_OPTIONS, *args]
    done = subprocess.run(
        wrap(settings, policy) + argv, cwd=work, env=git_env(tmp), capture_output=True, text=True, check=True
    )
    return done.stdout


def verify(settings: dict, policy: Path, allowed: list[Path], timeout_s: float = SENTINEL_TIMEOUT_S) -> None:
    """用本 slot 实际生效的 policy 和完整允许清单实测隔离；不符合预期就抛 SandboxError。

    哨兵只写 allowed 里的 runner 自有 /tmp 目录和自建的禁止目录，不碰实验证据、仓库和用户
    配置；allowed 必须全部真的可写，禁止目录必须不可写，子进程同规则。
    """
    denied = Path(tempfile.mkdtemp(prefix="phe-sandbox-denied-", dir="/tmp"))
    probes = []
    sentinel_files = []
    for index, root in enumerate(allowed):
        allowed_file = root / f"sentinel-{index}.txt"
        child_file = root / f"sentinel-child-{index}.txt"
        sentinel_files += [allowed_file, child_file]
        probes += [
            (f"写入允许目录 {root}", allowed_file, False, True),
            (f"子进程写入允许目录 {root}", child_file, True, True),
        ]
    probes += [
        ("写入未允许目录", denied / "denied.txt", False, False),
        ("子进程写入未允许目录", denied / "child.txt", True, False),
    ]
    try:
        for name, path, nested, writable in probes:
            try:
                actual = wrote(settings["executable"], policy, path, nested, timeout_s)
            except subprocess.TimeoutExpired:
                raise SandboxError(f"沙箱哨兵超时：{name}（{path}）") from None
            if actual != writable:
                raise SandboxError(f"沙箱哨兵失败：{name}（{path}）")
    finally:
        for path in sentinel_files:
            path.unlink(missing_ok=True)
        shutil.rmtree(denied, ignore_errors=True)


def wrote(exe: Path, policy: Path, path: Path, nested: bool, timeout_s: float) -> bool:
    """在 profile 下尝试写 path，返回是否真的写进去了（子进程模式多包一层 sh）。"""
    script = f"printf x > '{path}'"
    if nested:
        script = f'sh -c "{script}"'
    done = subprocess.run(
        [str(exe), "-f", str(policy), "/bin/sh", "-c", script],
        capture_output=True,
        timeout=timeout_s,
        check=False,
    )
    return done.returncode == 0 and path.exists()
