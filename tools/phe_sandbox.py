"""执行层写入隔离：用 macOS 内置 sandbox-exec 包裹 Target 及其子进程（标准库）。

保证（config 声明了 `sandbox` 时）：Target 和它启动的所有子进程只能写本 slot 的工作目录、
本 slot 的 artifacts 目录，以及 config 显式列出的目录；每次 run 启动 Target 之前用哨兵实测
这组规则真的生效，不符合预期就拒绝启动。config 没声明 `sandbox` 时不包裹（旧 proposal 路径）。

不保证：读范围（登录凭据仍可读，网络按 config 的 allow/deny 透传给 sandbox-exec）；外置验收器
在沙箱外运行；只在 macOS 上实现，其他平台声明了就拒绝启动，绝不静默按无隔离方式执行。
"""

import os
import pwd
import shutil
import subprocess
import tempfile
from pathlib import Path

MECHANISM = "macos-sandbox-exec"
EXECUTABLE = Path("/usr/bin/sandbox-exec")
NETWORKS = ("allow", "deny")
PROFILE_NAME = "sandbox.sb"
# 写 /dev/null 和 /dev/fd/* 是普通命令行行为（重定向），不是用户状态写入。
DEVICE_WRITE_RULES = ('(literal "/dev/null")', '(subpath "/dev/fd")')
SENTINEL_TIMEOUT_S = 20


def real_home() -> Path:
    """真实 home 从 passwd 取，不读 $HOME：环境变量可以被改写。"""
    return Path(os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir))


def load_settings(cfg: dict) -> dict | None:
    """校验 config.sandbox，返回规范化设置；没声明返回 None。

    声明了就要求机制真的可用：平台不支持、程序不存在或写根不合法时抛 ValueError，由调用方
    拒绝启动。这样"隔离配置被忽略"不会变成静默的无隔离执行。
    """
    raw = cfg.get("sandbox")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("config.sandbox 必须是对象")  # noqa: TRY004 —— JSON 配置错误按 ValueError 处理
    if raw.get("mechanism") != MECHANISM:
        raise ValueError(f"config.sandbox.mechanism 只支持 {MECHANISM}：{raw.get('mechanism')!r}")
    network = raw.get("network", "allow")
    if network not in NETWORKS:
        raise ValueError(f"config.sandbox.network 只能是 {'、'.join(NETWORKS)}：{network!r}")
    exe = Path(raw.get("executable") or EXECUTABLE)
    if not (exe.is_file() and os.access(exe, os.X_OK)):
        raise ValueError(f"sandbox 程序不可用：{exe}；平台不支持时拒绝启动，不做无隔离执行")
    return {"executable": exe, "network": network, "write_roots": write_roots(raw.get("write_roots", []))}


def write_roots(values) -> list[Path]:
    """显式允许写入的目录：绝对路径、已存在的目录、不在真实 home 内或包含它。"""
    if not isinstance(values, list):
        raise ValueError("config.sandbox.write_roots 必须是列表")  # noqa: TRY004 —— 同上
    home = real_home()
    roots = []
    for value in values:
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError(f"config.sandbox.write_roots 必须是绝对路径：{value!r}")
        root = Path(os.path.realpath(value))
        if not root.is_dir():
            raise ValueError(f"config.sandbox.write_roots 必须是已存在的目录：{value!r}")
        if root == home or home.is_relative_to(root) or root.is_relative_to(home):
            raise ValueError(f"config.sandbox.write_roots 不能是真实 home 或它的祖先/子目录：{value!r}")
        roots.append(root)
    return roots


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


def wrap(settings: dict | None, work: Path, art: Path) -> list[str]:
    """返回 Target 命令的前缀，并把本次生效的 profile 写进 art/sandbox.sb。

    settings 为 None（config 没声明 sandbox）时返回空列表，命令保持原样。
    """
    if settings is None:
        return []
    roots = [Path(os.path.realpath(work)), Path(os.path.realpath(art)), *settings["write_roots"]]
    policy = art / PROFILE_NAME
    policy.write_text(profile_text(settings, roots))
    return [str(settings["executable"]), "-f", str(policy)]


def verify(settings: dict, timeout_s: float = SENTINEL_TIMEOUT_S) -> None:
    """用哨兵实测隔离真的生效；不符合预期就抛 ValueError，调用方必须停止启动 Target。

    只验写入边界和子进程继承，不验证网络规则。哨兵在 /tmp 的独立目录里跑，不碰实验目录、
    仓库和用户配置。
    """
    root = Path(tempfile.mkdtemp(prefix="phe-sandbox-sentinel-", dir="/tmp"))
    try:
        allowed, denied = root / "allowed", root / "denied"
        allowed.mkdir()
        denied.mkdir()
        policy = root / "policy.sb"
        policy.write_text(profile_text(settings, [Path(os.path.realpath(allowed))]))
        cases = [
            ("写入允许目录", allowed / "allowed.txt", False, True),
            ("写入未允许目录", denied / "denied.txt", False, False),
            ("子进程写入允许目录", allowed / "child.txt", True, True),
            ("子进程写入未允许目录", denied / "child.txt", True, False),
        ]
        for name, path, nested, writable in cases:
            try:
                actual = wrote(settings["executable"], policy, path, nested, timeout_s)
            except subprocess.TimeoutExpired:
                raise ValueError(f"沙箱哨兵超时：{name}（{path}）") from None
            if actual != writable:
                raise ValueError(f"沙箱哨兵失败：{name}（{path}）")
    finally:
        shutil.rmtree(root, ignore_errors=True)


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
