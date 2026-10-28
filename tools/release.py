#!/usr/bin/env python3
"""PHE 资产导入、发布与 CLI（标准库）。写入层的守卫和 deploy/rollback 在 release_deploy.py。

只在沙箱里运行：`--home` 和 `--phe-home` 必填，且不得指向真实环境。
deploy 不改登记表：部署后要重新盘点并 adopt 才会得到新基线。
"""

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_deploy import (
    GUARD_EXIT_CODE,
    GuardError,
    Sandbox,
    deploy,
    inside,
    read_registry,
    rollback,
    sandbox_roots,
    utc_now,
)

# import：把登记表选中的资产复制进资产源。


def source_roots(home: Path, phe_home: Path) -> list[Path]:
    """import 的只读来源：沙箱 home、PHE_HOME，或两者的共同祖先（沙箱根）。"""
    roots = [Path(os.path.realpath(home)), Path(os.path.realpath(phe_home))]
    return [*roots, Path(os.path.commonpath(roots))]


def walk_error(error: OSError) -> None:
    """os.walk 默认吞掉遍历错误；发布不能把读不到的子目录当成空目录。"""
    raise error


def copy_tree(sandbox: Sandbox, source: Path, destination: Path) -> None:
    """逐项复制并逐项过守卫；资产内容里出现软链或特殊文件就拒绝，避免把链接目标混进发布。"""
    if not source.is_dir():
        raise ValueError(f"资产来源不是可用的目录：{source}")
    sandbox.makedirs(destination)
    for directory, dirnames, filenames in os.walk(source, followlinks=False, onerror=walk_error):
        dirnames.sort()
        target = destination / Path(directory).relative_to(source)
        sandbox.makedirs(target)
        for name in dirnames:
            if os.path.islink(Path(directory) / name):
                raise ValueError(f"资产内容含软链，拒绝导入：{Path(directory) / name}")
        for name in sorted(filenames):
            item = Path(directory) / name
            if os.path.islink(item) or not item.is_file():
                raise ValueError(f"资产内容含软链或特殊文件，拒绝导入：{item}")
            sandbox.write_bytes(target / name, item.read_bytes())


def selected_assets(data: dict, asset_ids: list[str]) -> list[dict]:
    assets = {asset["asset_id"]: asset for asset in data["assets"]}
    if not asset_ids:
        if not assets:
            raise ValueError("登记表里没有资产")
        return [assets[key] for key in sorted(assets)]
    missing = [asset_id for asset_id in asset_ids if asset_id not in assets]
    if missing:
        raise ValueError(f"登记表里没有：{'、'.join(missing)}")
    return [assets[asset_id] for asset_id in asset_ids]


def check_source(source: Path, roots: list[Path]) -> None:
    if not source.exists():
        raise ValueError(f"来源不存在：{source}")
    if not inside(source, roots):
        raise ValueError(f"来源不在沙箱之内，拒绝导入：{source}")


def check_inventory(inventory_path: Path, home: Path, asset: dict) -> None:
    """--inventory 的作用：只导入本次盘点在 --home 里看到的登记资产。"""
    data = json.loads(inventory_path.read_text(encoding="utf-8"))
    if os.path.realpath(data.get("home", "")) != os.path.realpath(home):
        raise ValueError(f"盘点 {inventory_path} 的 home 不是 {home}")
    paths = {record["path"] for record in data["records"]}
    if not any(observed["path"] in paths for observed in asset["observed"]):
        raise ValueError(f"{asset['asset_id']}: 盘点里没有它的任何引用")


def cmd_import(args: argparse.Namespace, roots: list[Path]) -> int:
    sandbox = Sandbox(roots)
    phe = roots[1]
    data = read_registry(args.registry)
    roots_for_source = source_roots(args.home, args.phe_home)
    for asset in selected_assets(data, args.asset_id):
        source = Path(asset["resolved_path"])
        check_source(source, roots_for_source)
        check_inventory(args.inventory, args.home, asset)
        asset_dir = phe / "assets" / asset["asset_id"]
        if os.path.lexists(asset_dir):
            raise ValueError(f"{asset['asset_id']} 已经导入过，拒绝覆盖：{asset_dir}")
        content = asset_dir / "content"
        kind = "skill" if source.is_dir() else "instructions"
        if kind == "skill":
            copy_tree(sandbox, source, content)
        else:
            sandbox.write_bytes(content / source.name, source.read_bytes())
        sandbox.write_json(
            asset_dir / "asset.json",
            {
                "asset_id": asset["asset_id"],
                "kind": kind,
                "name": source.name,
                "source": str(source),
                "imported_at": utc_now(),
            },
        )
        print(f"imported {asset['asset_id']} {kind} {source}")
    return 0


# release：由资产源构建不可变发布。


def asset_entries(phe: Path) -> list[dict]:
    assets_dir = phe / "assets"
    entries = []
    if assets_dir.is_dir():
        for directory in sorted(assets_dir.iterdir()):
            metadata = directory / "asset.json"
            if metadata.is_file():
                entries.append(json.loads(metadata.read_text(encoding="utf-8")))
    if not entries:
        raise ValueError(f"资产源为空，先运行 import：{assets_dir}")
    return entries


def next_release_number(phe: Path) -> int:
    """编号用高水位：high_water.json 记下一个可用编号，目录名只在文件丢失时兜底。"""
    releases = phe / "releases"
    numbers = [int(path.name[1:]) for path in releases.glob("r[0-9][0-9][0-9][0-9]") if path.is_dir()]
    high_water = releases / "high_water.json"
    saved = 1
    if high_water.is_file():
        saved = int(json.loads(high_water.read_text(encoding="utf-8"))["next_release_number"])
    return max(saved, max(numbers, default=0) + 1)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(sandbox: Sandbox, release_dir: Path) -> None:
    """发布不可变：文件和目录都去掉写权限。

    用 lstat 判类型，不跟随软链：发布里出现软链说明内容被污染，chmod 会改到链接目标上。
    """
    for item in sorted(release_dir.rglob("*"), reverse=True):
        mode = os.lstat(item).st_mode
        if stat.S_ISLNK(mode):
            raise ValueError(f"发布内容含软链，拒绝冻结：{item}")
        sandbox.chmod(item, 0o555 if stat.S_ISDIR(mode) else 0o444)
    sandbox.chmod(release_dir, 0o555)


def discard_release(release_dir: Path) -> None:
    """失败清理：已冻结的文件是只读的，先放开所有者写权限再删；编号不复用，由高水位照常推进。"""
    if not os.path.lexists(release_dir):
        return
    for item in sorted(release_dir.rglob("*"), reverse=True):
        mode = os.lstat(item).st_mode
        if not stat.S_ISLNK(mode):
            os.chmod(item, stat.S_IMODE(mode) | stat.S_IWUSR)
    os.chmod(release_dir, stat.S_IMODE(os.lstat(release_dir).st_mode) | stat.S_IWUSR)
    shutil.rmtree(release_dir)


def collect_release(sandbox: Sandbox, phe: Path, release_id: str, release_dir: Path, assets: list[dict]) -> dict:
    manifest = {"release": release_id, "created_at": utc_now(), "assets": {}, "files": {}}
    for asset in assets:
        content = phe / "assets" / asset["asset_id"] / "content"
        destination = release_dir / "files" / asset["asset_id"]
        copy_tree(sandbox, content, destination)
        manifest["assets"][asset["asset_id"]] = {
            "kind": asset["kind"],
            "name": asset["name"],
            "entry": f"files/{asset['asset_id']}",
        }
        for item in sorted(destination.rglob("*")):
            if item.is_file():
                manifest["files"][str(item.relative_to(release_dir))] = {
                    "sha256": file_sha256(item),
                    "size": item.stat().st_size,
                    "asset_id": asset["asset_id"],
                }
    return manifest


def build_release(sandbox: Sandbox, phe: Path, assets: list[dict]) -> str:
    """构建不可变发布；任何一步失败都删掉本次 rNNNN，不留半成品和有效 manifest。"""
    number = next_release_number(phe)
    release_id = f"r{number:04d}"
    release_dir = phe / "releases" / release_id
    if os.path.lexists(release_dir):
        raise ValueError(f"发布编号已存在，拒绝覆盖：{release_dir}")
    sandbox.makedirs(phe / "releases")
    sandbox.write_json(phe / "releases" / "high_water.json", {"next_release_number": number + 1})
    try:
        sandbox.makedirs(release_dir)
        manifest = collect_release(sandbox, phe, release_id, release_dir, assets)
        sandbox.write_json(release_dir / "manifest.json", manifest)
        freeze(sandbox, release_dir)
    except BaseException:
        discard_release(release_dir)
        raise
    return release_id


def cmd_release(args: argparse.Namespace, roots: list[Path]) -> int:
    phe = roots[1]
    assets = asset_entries(phe)
    release_id = build_release(Sandbox(roots), phe, assets)
    print(f"released {release_id} ({len(assets)} 个资产)")
    return 0


COMMANDS = {"import": cmd_import, "release": cmd_release, "deploy": deploy, "rollback": rollback}


def add_sandbox_arguments(parser: argparse.ArgumentParser) -> None:
    """两个根都必填，没有默认值：默认值等于替用户选中真实环境。"""
    parser.add_argument("--phe-home", type=Path, required=True, help="PHE 资产源根目录")
    parser.add_argument("--home", type=Path, required=True, help="沙箱里的 Agent home")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PHE 发布与部署（只在沙箱运行）")
    commands = parser.add_subparsers(dest="cmd", required=True)

    importer = commands.add_parser("import", help="把登记表里选中的资产复制进 PHE_HOME/assets")
    importer.add_argument("--registry", type=Path, required=True)
    importer.add_argument("--inventory", type=Path, required=True)
    importer.add_argument("--asset-id", action="append", default=[], help="只导入指定资产，缺省全部")
    add_sandbox_arguments(importer)

    builder = commands.add_parser("release", help="由 assets 构建不可变发布")
    add_sandbox_arguments(builder)

    deployer = commands.add_parser("deploy", help="盘点 + 漂移检查后把发布部署为软链")
    deployer.add_argument("--agent", required=True)
    deployer.add_argument("--release", required=True, help="发布编号，如 r0001")
    deployer.add_argument("--registry", type=Path, required=True)
    add_sandbox_arguments(deployer)

    restorer = commands.add_parser("rollback", help="按 journal 恢复部署前的链接")
    restorer.add_argument("--journal", type=Path, required=True)
    add_sandbox_arguments(restorer)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        roots = sandbox_roots(args.home, args.phe_home)
    except GuardError as exc:
        print(f"release: {exc}", file=sys.stderr)
        return GUARD_EXIT_CODE
    try:
        return COMMANDS[args.cmd](args, roots)
    except GuardError as exc:
        print(f"release: {exc}", file=sys.stderr)
        return GUARD_EXIT_CODE
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
