"""资产内容层：单文件与目录资产的读取、暂存、整棵重建与逐文件 diff（标准库）。

`change.py`（旧沙箱闭环）和 `personalize*.py`（产品入口）共用这一层，避免两份 patch/重建实现。
内容口径：单文件资产按文件字节取 sha256；目录资产按「相对路径 + 每个文件的 sha256」整棵取摘要，
不用 SKILL.md 单文件的 hash 代替目录 hash。写入一律经调用方的 Writer（沙箱或计划放行的产品范围）。
"""

import difflib
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import release as release_tool


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def content_files(root: Path) -> dict[str, str]:
    """内容根 → {相对路径: 文件 sha256}：单文件一条，目录逐文件；软链与特殊文件拒绝。"""
    if root.is_file():
        return {root.name: sha256_file(root)}
    files = {}
    for item in sorted(root.rglob("*")):
        if item.is_symlink():
            raise ValueError(f"资产内容含软链，不支持更新：{item}")
        if item.is_file():
            files[str(item.relative_to(root))] = sha256_file(item)
    return files


def content_digest(files: dict[str, str]) -> str:
    """整棵内容的摘要：任一路径增删或任一文件字节变化都会变。"""
    return sha256_bytes("\n".join(f"{name} {digest}" for name, digest in sorted(files.items())).encode("utf-8"))


def content_sha(root: Path) -> str | None:
    """内容 sha256：不存在或断链记 None；单文件取文件字节，目录取整棵摘要。"""
    if not root.exists():
        return None
    if root.is_file():
        return sha256_file(root)
    return content_digest(content_files(root))


def effective_file(asset: dict) -> Path:
    """当前实际生效的内容：入口是软链时按真实路径取，不用登记表里的旧路径；可能是文件或目录。"""
    return Path(os.path.realpath(asset["observed"][0]["path"]))


def asset_kind(phe: Path, asset: dict) -> str:
    """资产逻辑类型取 asset.json 的 kind；没有元数据时按来源物理形状判断（与 import 同一条规则）。"""
    metadata = phe / "assets" / asset["asset_id"] / "asset.json"
    if metadata.is_file():
        return json.loads(metadata.read_text(encoding="utf-8"))["kind"]
    return "skill" if effective_file(asset).is_dir() else "instructions"


def patch_targets(patch: str) -> list[str]:
    """patch 涉及的目标路径，按成对 diff 头取：新增与修改取 `+++`，只删除的文件取对应的 `---`。"""
    targets = []
    old = None
    for line in patch.splitlines():
        if line.startswith("--- "):
            old = line[4:].split("\t")[0].strip().removeprefix("a/")
        elif line.startswith("+++ "):
            new = line[4:].split("\t")[0].strip().removeprefix("b/")
            path = new if new and new != "/dev/null" else old
            if path and path != "/dev/null":
                targets.append(path)
            old = None
    if not targets:
        raise ValueError("patch 里没有目标路径")
    return targets


def patch_target(patch: str) -> str:
    """单文件资产的 patch 目标：只认唯一一个非 /dev/null 的路径。"""
    targets = patch_targets(patch)
    if len(targets) != 1:
        raise ValueError(f"单文件资产的 patch 只能改一个文件，实际是：{targets}")
    return targets[0]


def git_apply(directory: Path, patch: str) -> None:
    """在 directory 里把 patch 打到已有文件上；打不上就抛 ValueError，不继续写资产或发布。"""
    result = subprocess.run(
        ["git", "apply", "-p1", "-"], cwd=directory, input=patch, text=True, capture_output=True, check=False
    )
    if result.returncode != 0:
        raise ValueError(f"patch 打不上当前生效内容：{result.stderr.strip()}")


def stage_patch(writer, staging: Path, patch: str, base: Path) -> Path:
    """在暂存区把 patch 打到当前生效内容的副本上；返回暂存的内容根（文件或目录）。

    每次先重建本次执行自己拥有的暂存根（`staging/<执行 ID>/tree|file`）：上一次失败留下的文件不能混进
    这一次的发布。只清这一个目录，不动整个 staging、发布或用户数据。
    目录资产整棵复制再打 patch，所以新增、删除、修改引用文件都落在同一份暂存内容上；打不上就失败，
    不写资产、不建发布。单文件的 patch 路径只用来定位暂存，内容根按资产原文件名保存。
    """
    root = staging / ("tree" if base.is_dir() else "file")
    writer.check(root)
    if os.path.lexists(root):
        discard_content(root)
    if base.is_dir():
        release_tool.copy_tree(writer, base, root)
        git_apply(root, patch)
        return root
    writer.makedirs(root)
    path = root / patch_target(patch)
    writer.write_bytes(path, base.read_bytes())
    git_apply(root, patch)
    if path.name == base.name:
        return path
    staged = root / base.name
    writer.write_bytes(staged, path.read_bytes())
    writer.check(path)
    path.unlink()
    return staged


def discard_content(content: Path) -> None:
    """删掉旧内容根：资产内容不冻结，整棵替换前先清掉，避免残留文件进入下一次发布。"""
    if content.is_dir() and not content.is_symlink():
        for item in sorted(content.rglob("*"), reverse=True):
            if not item.is_symlink():
                os.chmod(item, stat.S_IMODE(os.lstat(item).st_mode) | stat.S_IWUSR)
        shutil.rmtree(content)
    else:
        content.unlink()


def write_asset(writer, phe: Path, asset: dict, staged: Path, source: Path, at: str) -> None:
    """按 release import 的同一格式重建资产内容；不复用 import（它拒绝覆盖且固定从 resolved_path 复制）。

    内容整棵替换：patch 删掉的引用文件也要真的从资产内容里消失，否则下一次发布会把它带回来。
    单文件资产按来源文件的原名写回：patch 里的路径只用于暂存定位，不改变资产名。
    """
    directory = phe / "assets" / asset["asset_id"]
    content = directory / "content"
    if os.path.lexists(content):
        discard_content(content)
    name = Path(asset["resolved_path"]).name
    if staged.is_dir():
        release_tool.copy_tree(writer, staged, content)
    else:
        writer.write_bytes(content / name, staged.read_bytes())
    writer.write_json(
        directory / "asset.json",
        {
            "asset_id": asset["asset_id"],
            "kind": "skill" if staged.is_dir() else "instructions",
            "name": name,
            "source": str(source),
            "imported_at": at,
        },
    )


def read_text(root: Path, name: str, files: dict[str, str]) -> list[str]:
    """diff 显示用：文件不存在时当空内容；非 UTF-8 字节按替换符显示，内容绑定仍以 sha256 为准。"""
    if name not in files:
        return []
    data = (root / name).read_bytes() if root.is_dir() else root.read_bytes()
    return data.decode("utf-8", "replace").splitlines(keepends=True)


def file_diffs(before: Path | None, after: Path) -> list[dict]:
    """逐文件 diff（含新增与删除）：预览要能看出引用文件的增删，而不只是正文改动。"""
    before_files = content_files(before) if before is not None and before.exists() else {}
    after_files = content_files(after)
    diffs = []
    for name in sorted(set(before_files) | set(after_files)):
        if before_files.get(name) == after_files.get(name):
            continue
        old = read_text(before, name, before_files) if before is not None and before.exists() else []
        new = read_text(after, name, after_files)
        diffs.append(
            {
                "path": name,
                "state": "added" if name not in before_files else "deleted" if name not in after_files else "modified",
                "before_sha256": before_files.get(name),
                "after_sha256": after_files.get(name),
                "diff": "".join(difflib.unified_diff(old, new, fromfile=f"a/{name}", tofile=f"b/{name}")),
            }
        )
    return diffs
