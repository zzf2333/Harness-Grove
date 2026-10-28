"""deploy 预检里的绑定发布校验（标准库）。

放行一条 `symlink_target_changed` 之前，先证明 `bindings.json` 记的那次发布本身没被改写：
编号合法、目录链上没有软链、该资产的文件集合与每个文件的 sha256 都与 manifest 一致。
只读，不写盘；任何不符或读不出来的情况都算不通过，调用方按漂移处理并中止。
"""

import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

# 发布编号只由 release 命令生成：r 加至少 4 位数字，不含路径分隔符，`..` 和绝对路径都进不来。
RELEASE_ID_PATTERN = re.compile(r"r\d{4,}")


def read_manifest(release_dir: Path) -> dict:
    path = release_dir / "manifest.json"
    if not path.is_file():
        raise ValueError(f"没有发布清单：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def link_target(phe: Path, release_id: str, meta: dict) -> Path:
    """发布里一个资产的链接目标：instructions 指到文件，skill 指到整个目录。"""
    target = phe / "releases" / release_id / meta["entry"]
    if meta["kind"] == "instructions":
        target = target / meta["name"]
    return target


def expected_deployment(change: dict, bindings: dict, phe: Path, verified: dict) -> bool:
    """这条 symlink_target_changed 是不是 PHE 自己的部署：路径已绑定，链接精确指向校验通过的发布文件。

    只看路径在 bindings 里不够：链接被改指别处、发布或其中资产不存在、发布内容被改写时仍然算漂移。
    """
    path = change["path"]
    if not os.path.islink(path):
        return False
    current = os.readlink(path)
    for entries in bindings.values():
        release_id = entries.get(path)
        if release_id is None:
            continue
        target = bound_target(phe, release_id, change["asset_id"], verified)
        if target is not None and current == str(target):
            return True
    return False


def bound_target(phe: Path, release_id: str, asset_id: str, verified: dict) -> Path | None:
    """bindings 记的发布里该资产的目标；校验不通过时返回 None，原因打到 stderr。

    同一 release+asset 在一次预检里只校验一次，结果（含不通过）缓存在 verified 里。
    """
    key = (release_id, asset_id)
    if key not in verified:
        verified[key] = check_release_asset(phe, release_id, asset_id)
    return verified[key]


def check_release_asset(phe: Path, release_id: str, asset_id: str) -> Path | None:
    """校验失败和读取错误都算不放行：发布读不出来时不能当成没被改过。"""
    try:
        return verify_release_asset(phe, release_id, asset_id)
    except (OSError, ValueError, KeyError) as exc:
        print(f"verify_failed {exc}", file=sys.stderr)
        return None


def verify_release_asset(phe: Path, release_id: str, asset_id: str) -> Path:
    """按 manifest 校验绑定发布里的一个资产，返回它的链接目标；任何不符都抛 ValueError。"""
    if not RELEASE_ID_PATTERN.fullmatch(release_id):
        raise ValueError(f"发布编号不合法：{release_id}")
    releases = phe / "releases"
    release_dir = releases / release_id
    plain_mode(releases)
    plain_mode(release_dir)
    plain_mode(release_dir / "manifest.json")
    manifest = read_manifest(release_dir)
    meta = manifest.get("assets", {}).get(asset_id)
    if meta is None:
        raise ValueError(f"发布 {release_id} 里没有资产 {asset_id}")
    parts = Path(meta["entry"]).parts
    if not parts or Path(meta["entry"]).is_absolute() or ".." in parts:
        raise ValueError(f"资产路径越出发布：{meta['entry']}")
    # 从发布目录到资产目录逐级 lstat，中间的 files/ 被换成软链也要拒绝。
    asset_dir = release_dir
    for part in parts:
        asset_dir = asset_dir / part
        plain_mode(asset_dir)
    target = link_target(phe, release_id, meta)
    prefix = meta["entry"] + "/"
    expected = {
        relative[len(prefix) :]: entry
        for relative, entry in manifest.get("files", {}).items()
        if relative.startswith(prefix)
    }
    actual = walk_asset(asset_dir)
    if meta["kind"] == "instructions" and meta["name"] not in actual:
        # name 必须是本资产里校验过的一个文件，不能借 `../` 指到发布之外。
        raise ValueError(f"发布 {release_id} 资产 {asset_id} 的链接目标不在资产文件里：{meta['name']}")
    if set(actual) != set(expected):
        raise ValueError(
            f"发布 {release_id} 资产 {asset_id} 的文件集合与 manifest 不符："
            f"多出 {sorted(set(actual) - set(expected))}，缺少 {sorted(set(expected) - set(actual))}"
        )
    for relative, item in sorted(actual.items()):
        if hashlib.sha256(item.read_bytes()).hexdigest() != expected[relative]["sha256"]:
            raise ValueError(f"发布 {release_id} 资产 {asset_id} 的 {relative} 与 manifest 的 sha256 不符")
    return target


def walk_asset(asset_dir: Path) -> dict[str, Path]:
    """资产目录下的全部普通文件，键相对资产目录；软链和非普通文件一律拒绝。"""
    files = {}
    for directory, dirnames, filenames in os.walk(asset_dir, followlinks=False, onerror=walk_error):
        base = Path(directory)
        for name in dirnames:
            plain_mode(base / name)
        for name in filenames:
            item = base / name
            if not stat.S_ISREG(plain_mode(item)):
                raise ValueError(f"资产里有非普通文件：{item}")
            files[item.relative_to(asset_dir).as_posix()] = item
    return files


def plain_mode(path: Path) -> int:
    """lstat 取模式；路径本身是软链时拒绝，校验不能跟着链接跑到发布之外。"""
    mode = os.lstat(path).st_mode
    if stat.S_ISLNK(mode):
        raise ValueError(f"发布里有软链：{path}")
    return mode


def walk_error(error: OSError) -> None:
    """os.walk 默认吞掉遍历错误；校验不能把读不到的目录当成空目录。"""
    raise error
