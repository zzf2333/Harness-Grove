#!/usr/bin/env python3
"""沙箱写入层与 deploy / rollback（标准库）。

四层守卫都在代码里：`--home`、`--phe-home` 必填（在 release.py 的参数定义里）、真实 home 拒绝、
每次写入前的 guarded_write_path、单测覆盖。deploy 和 rollback 都只用这里的 Sandbox 写盘。
"""

import importlib.util
import json
import os
import pwd
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def load_sibling(name: str):
    """tools/ 不是包：按文件路径加载同目录模块，避免被仓库根目录的同名目录挡住。"""
    path = Path(__file__).resolve().parent / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"phe_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


registry_tool = load_sibling("registry")
inventory_tool = load_sibling("inventory")

# 真实 home 下这些路径属于用户正在使用的 Agent 配置和 temper，任何写入都不允许。
PROTECTED_SUBPATHS = (".claude", ".codex", ".pi", ".phe", ".agents", "AI/temper")
GUARD_EXIT_CODE = 2


class GuardError(Exception):
    """目标越出沙箱或指向真实环境；调用方以退出码 2 结束。"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_registry(path: Path) -> dict:
    return registry_tool.read_registry(path)


# 第 2 层守卫：拒绝真实环境。


def real_home() -> Path:
    """真实 home 从 passwd 取，不读 $HOME：环境变量可以被改写，passwd 才是账户本身。"""
    return Path(os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir))


def refusal_reason(path: Path) -> str | None:
    home = real_home()
    real = Path(os.path.realpath(path))
    if real == home:
        return f"{path} 是真实 home"
    if real in home.parents:
        return f"{path} 是真实 home 的祖先目录"
    for relative in PROTECTED_SUBPATHS:
        protected = home / relative
        if real == protected or protected in real.parents:
            return f"{path} 位于真实环境 {protected} 之内"
    return None


def sandbox_roots(home: Path, phe_home: Path) -> list[Path]:
    reasons = [reason for reason in (refusal_reason(home), refusal_reason(phe_home)) if reason is not None]
    if reasons:
        raise GuardError("；".join(reasons))
    return [Path(os.path.realpath(home)), Path(os.path.realpath(phe_home))]


# 第 3 层守卫：所有写入的唯一入口。


def write_target(path: Path) -> Path:
    """写对象自身的路径：所在目录取 realpath，叶子保持原样。写软链时不能解析叶子。"""
    return Path(os.path.realpath(Path(path).parent)) / Path(path).name


def under(path: Path, roots: list[Path]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def guarded_write_path(path: Path, roots: list[Path]) -> None:
    """被写对象所在目录的 realpath 必须在某个沙箱根之下。

    只看目录、不看叶子：写软链时检查的是链接自身所在的目录，不是它指向的目标。
    沙箱根本身允许创建，否则 phe-home 还不存在时建不出来；根已经在第 2 层校验过。
    """
    target = write_target(path)
    if under(target, roots) or under(target.parent, roots):
        return
    raise GuardError(f"拒绝写入沙箱之外的路径：{path}")


class Sandbox:
    """所有写入都从这里过一遍 guarded_write_path，再调用 os 的写函数。"""

    def __init__(self, roots: list[Path]) -> None:
        self.roots = roots

    def check(self, path: Path) -> Path:
        guarded_write_path(path, self.roots)
        return path

    def mkdir(self, path: Path) -> None:
        self.check(path)
        if not os.path.lexists(path):
            os.mkdir(path)

    def makedirs(self, path: Path) -> None:
        missing = []
        current = Path(path)
        while not os.path.lexists(current) and current != current.parent:
            missing.append(current)
            current = current.parent
        for directory in reversed(missing):
            self.mkdir(directory)

    def write_bytes(self, path: Path, data: bytes) -> None:
        """同目录临时文件 + os.replace：写到一半失败时旧文件保持不变。"""
        self.check(path)
        self.makedirs(Path(path).parent)
        fd, temporary = tempfile.mkstemp(dir=Path(path).parent, prefix=f".{Path(path).name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def write_json(self, path: Path, data: dict) -> None:
        self.write_bytes(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    def symlink(self, target: str, path: Path) -> None:
        self.check(path)
        self.makedirs(Path(path).parent)
        os.symlink(target, path)

    def replace(self, source: Path, path: Path) -> None:
        self.check(path)
        os.replace(source, path)

    def unlink(self, path: Path) -> None:
        self.check(path)
        os.unlink(path)

    def chmod(self, path: Path, mode: int) -> None:
        self.check(path)
        os.chmod(path, mode)


def inside(path: Path, roots: list[Path]) -> bool:
    """解析整条路径后判断，用于只读来源：软链指到沙箱外时也算在外面。"""
    return under(Path(os.path.realpath(path)), roots)


def link_inside(path: Path, roots: list[Path]) -> bool:
    """只看链接自身所在的目录，用于要写入的链接路径。"""
    return under(write_target(path), roots)


# deploy / rollback：记录式事务的软链替换。


def current_state(path: Path) -> tuple[str, str | None]:
    if not os.path.lexists(path):
        return "absent", None
    if os.path.islink(path):
        return "symlink", os.readlink(path)
    return "other", None


def read_manifest(release_dir: Path) -> dict:
    path = release_dir / "manifest.json"
    if not path.is_file():
        raise ValueError(f"没有发布清单：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def drift_changes(data: dict, observed: dict) -> list[dict]:
    """复用 registry 的比对口径：只针对已登记条目，同一 path 取第一条记录。"""
    latest: dict[str, dict] = {}
    for record in observed["records"]:
        latest.setdefault(record["path"], record)
    at = registry_tool.utc_now()
    changes = []
    for asset in data["assets"]:
        changes.extend(registry_tool.asset_changes(asset, latest, at))
    return changes


def link_plan(data: dict, manifest: dict, agent: str, home: Path, phe: Path) -> list[dict]:
    """按登记表里该 agent 的 observed 路径算目标链接；只处理 --home 之下的用户级路径。"""
    home_real = Path(os.path.realpath(home))
    release_dir = phe / "releases" / manifest["release"]
    plans = []
    for asset_id, meta in sorted(manifest["assets"].items()):
        target = release_dir / meta["entry"]
        if meta["kind"] == "instructions":
            target = target / meta["name"]
        for asset in data["assets"]:
            if asset["asset_id"] != asset_id:
                continue
            for observed in asset["observed"]:
                if observed["agent"] != agent:
                    continue
                path = Path(observed["path"])
                if not link_inside(path, [home_real]):
                    print(f"skip {path}: 不在 --home 之下", file=sys.stderr)
                    continue
                plans.append({"asset_id": asset_id, "path": path, "target": target})
    return plans


def inspect_links(plans: list[dict], sandbox: Sandbox) -> list[dict]:
    """写之前的全部检查：目标在发布里、父目录存在、守卫通过、不覆盖普通文件。"""
    entries = []
    for plan in plans:
        path = plan["path"]
        if not os.path.lexists(plan["target"]):
            raise ValueError(f"发布里没有目标：{plan['target']}")
        sandbox.check(path)
        if not path.parent.is_dir():
            raise ValueError(f"链接的父目录不存在：{path.parent}")
        state, old_target = current_state(path)
        if state == "other":
            raise ValueError(f"目标是普通文件或目录，拒绝覆盖：{path}")
        entries.append(plan | {"state": state, "old_target": old_target})
    return entries


def write_journal(sandbox: Sandbox, phe: Path, agent: str, release_id: str, entries: list[dict]) -> Path:
    directory = phe / "journal"
    sandbox.makedirs(directory)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{stamp}.json"
    suffix = 1
    while os.path.lexists(path):
        path = directory / f"{stamp}-{suffix}.json"
        suffix += 1
    sandbox.write_json(
        path,
        {
            "created_at": utc_now(),
            "agent": agent,
            "release": release_id,
            "phe_home": str(phe),
            "entries": [
                {
                    "path": str(entry["path"]),
                    "state": entry["state"],
                    "old_target": entry["old_target"],
                    "new_target": str(entry["target"]),
                }
                for entry in entries
            ],
        },
    )
    return path


def temp_link_path(path: Path) -> Path:
    return path.parent / f".{path.name}.phe-tmp-{os.getpid()}"


def atomic_symlink(sandbox: Sandbox, target: str, path: Path) -> None:
    """先建同目录临时链接再 os.replace：替换是原子的，不会留下半条链接。"""
    temporary = temp_link_path(path)
    if os.path.lexists(temporary):
        sandbox.unlink(temporary)
    sandbox.symlink(target, temporary)
    try:
        sandbox.replace(temporary, path)
    except BaseException:
        if os.path.lexists(temporary):
            sandbox.unlink(temporary)
        raise


def restore_entry(sandbox: Sandbox, entry: dict) -> None:
    path = Path(entry["path"])
    sandbox.check(path)
    if entry["state"] == "absent":
        if os.path.lexists(path):
            sandbox.unlink(path)
        return
    atomic_symlink(sandbox, entry["old_target"], path)


def restore_entries(sandbox: Sandbox, entries: list[dict]) -> list[str]:
    """按给定顺序恢复；单条失败不中断其余恢复，返回失败说明。"""
    failures = []
    for entry in entries:
        try:
            restore_entry(sandbox, entry)
        except OSError as exc:
            failures.append(f"{entry['path']}: {exc}")
    return failures


def read_bindings(phe: Path) -> dict:
    path = phe / "bindings.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def bind(sandbox: Sandbox, phe: Path, agent: str, paths: list[str], release_id: str) -> None:
    data = read_bindings(phe)
    entries = data.setdefault(agent, {})
    for path in paths:
        entries[path] = release_id
    sandbox.write_json(phe / "bindings.json", data)


def unbind(sandbox: Sandbox, phe: Path, agent: str, paths: list[str]) -> None:
    data = read_bindings(phe)
    entries = data.get(agent, {})
    for path in paths:
        entries.pop(path, None)
    if not entries:
        data.pop(agent, None)
    sandbox.write_json(phe / "bindings.json", data)


def deploy(args, roots: list[Path]) -> int:
    sandbox = Sandbox(roots)
    home, phe = roots
    data = registry_tool.read_registry(args.registry)
    manifest = read_manifest(phe / "releases" / args.release)
    changes = drift_changes(data, inventory_tool.inventory(home, []))
    if changes:
        for change in changes:
            print(f"external_change {change['type']} {change['path']} ({change['asset_id']})", file=sys.stderr)
        raise ValueError(f"盘点发现 {len(changes)} 条 ExternalChange，未改动任何链接")
    plans = link_plan(data, manifest, args.agent, home, phe)
    if not plans:
        raise ValueError(f"{args.agent} 在 {args.release} 里没有可部署的链接")
    entries = inspect_links(plans, sandbox)
    journal = write_journal(sandbox, phe, args.agent, args.release, entries)
    applied: list[dict] = []
    try:
        for entry in entries:
            atomic_symlink(sandbox, str(entry["target"]), entry["path"])
            applied.append(entry)
        bind(sandbox, phe, args.agent, [str(entry["path"]) for entry in entries], args.release)
    except BaseException:
        for failure in restore_entries(sandbox, list(reversed(applied))):
            print(f"rollback_failed {failure}", file=sys.stderr)
        raise
    print(f"deployed {args.release} -> {args.agent}: {len(entries)} 条链接，journal {journal}")
    return 0


def rollback(args, roots: list[Path]) -> int:
    sandbox = Sandbox(roots)
    home, phe = roots
    journal = json.loads(args.journal.read_text(encoding="utf-8"))
    home_real = Path(os.path.realpath(home))
    plans = []
    for entry in reversed(journal["entries"]):
        path = Path(entry["path"])
        if not link_inside(path, [home_real]):
            raise ValueError(f"journal 里的路径不在 --home 之下：{path}")
        sandbox.check(path)
        state, _ = current_state(path)
        if state == "other":
            raise ValueError(f"目标是普通文件或目录，拒绝覆盖：{path}")
        plans.append(entry | {"path": path})
    failures = restore_entries(sandbox, plans)
    if failures:
        raise ValueError("回滚未完成：" + "；".join(failures))
    unbind(sandbox, phe, journal["agent"], [str(plan["path"]) for plan in plans])
    print(f"rolled back {journal['release']} for {journal['agent']}: {len(plans)} 条链接")
    return 0
