"""R3a Host 验收探针（驱动侧）：在一次性隔离环境里跑真实 Pi SDK，核对发现、广告与显式展开。

隔离方式：给子进程一份显式 env（`HOME` / `PI_CODING_AGENT_DIR` / `PI_OFFLINE` 等指到临时目录），不改父进程
环境，也不读真实用户配置。检查项：真实 loader 只发现这个 Skill 且 name/description/path 正确；system prompt
的技能清单列出它；`/skill:<name>` 的展开正文与 `baseDir` 指向本 PHE_HOME 发布出来的内容（逐文件比对）；
没有工具执行。任一不成立退出 1。

模型层是本地 faux provider（零网络、零费用）：这不证明模型会自主选用这个 Skill，也不证明任务执行效果。
用法见 `docs/acceptance/r3a.md`。
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from asset_content import content_files

PROBE = Path(__file__).resolve().parent / "skill_host_probe.mjs"
ISOLATED_KEYS = ("HOME", "PI_CODING_AGENT_DIR", "PI_SESSION_DIR", "PI_CODING_AGENT_SESSION_DIR")
# 这些变量让 Pi 不做网络活动、不查版本、不发遥测。
PI_ENV = {"PI_OFFLINE": "1", "PI_SKIP_VERSION_CHECK": "1", "PI_TELEMETRY": "0"}


def sdk_version(sdk: Path) -> str:
    """探针用的 Pi 版本：提交的工件不写安装路径（可能含本机用户名），只记版本号。"""
    return json.loads((sdk / "package.json").read_text(encoding="utf-8"))["version"]


def pi_ai_root(sdk: Path) -> Path:
    """`@earendil-works/pi-ai` 的实际目录：装在 pi-coding-agent 下（嵌套）或与它并列（hoisted）。"""
    for candidate in (sdk / "node_modules/@earendil-works/pi-ai", sdk.parent / "@earendil-works/pi-ai"):
        if (candidate / "dist/index.js").is_file():
            return candidate
    raise ValueError(f"找不到 {sdk} 依赖的 @earendil-works/pi-ai（试过嵌套与并列两种布局）")


def isolated_env(args: argparse.Namespace) -> dict:
    """子进程环境：继承 PATH，其余隔离项全部显式覆盖；父进程的 PI_* 会话变量不带进去。"""
    env = {key: value for key, value in os.environ.items() if not key.startswith("PI_")}
    for key in ISOLATED_KEYS:
        env.pop(key, None)
    env.update(PI_ENV)
    env.update(
        {
            "HOME": str(args.home),
            "PI_CODING_AGENT_DIR": str(args.agent_dir),
            "PROBE_SDK_ROOT": str(args.sdk),
            "PROBE_AI_ROOT": str(pi_ai_root(args.sdk)),
            "PROBE_AGENT_DIR": str(args.agent_dir),
            "PROBE_CWD": str(args.cwd),
            "PROBE_SKILL_NAME": args.skill_name,
            "PROBE_SKILL_ARGS": args.args,
        }
    )
    return env


def skill_body(text: str) -> str:
    """去掉 YAML frontmatter：展开正文是 SKILL.md 去掉 frontmatter 后的内容。"""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            return text[end + 5 :].strip()
    return text.strip()


def check(record: dict, args: argparse.Namespace) -> dict:
    entry = Path(args.entry)
    skills = record["skills"]
    skill = skills[0] if len(skills) == 1 else {}
    prompt = record["skills_section"] or ""
    expanded = record["expanded"] or ""
    body = skill_body((Path(args.release) / "SKILL.md").read_text(encoding="utf-8"))
    return {
        "one_skill_discovered": len(skills) == 1 and skill.get("name") == args.skill_name,
        "description_advertised": bool(skill.get("description")) and skill["description"] in prompt,
        "file_path_is_entry": Path(skill.get("filePath", "")) == entry / "SKILL.md",
        "base_dir_is_entry": Path(skill.get("baseDir", "")) == entry,
        "skills_section_lists_skill": f"<name>{args.skill_name}</name>" in prompt,
        "expanded_body_matches_release": body in expanded,
        "expanded_base_dir_matches_entry": f"References are relative to {entry}" in expanded,
        "expanded_keeps_user_args": args.args in expanded,
        "files_match_release": content_files(entry) == content_files(Path(args.release)),
        "no_tool_execution": record["tool_results"] == 0 and record["model_calls"] >= 1,
    }


def run(args: argparse.Namespace) -> int:
    result = subprocess.run(
        ["node", str(PROBE)],
        env=isolated_env(args),
        cwd=str(args.cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        print(f"探针没有跑完：node 退出 {result.returncode}", file=sys.stderr)
        return 1
    record = json.loads(result.stdout)
    checks = check(record, args)
    summary = {
        "sdk_package": "pi-coding-agent",
        "sdk_version": sdk_version(args.sdk),
        "home": str(args.home),
        "agent_dir": str(args.agent_dir),
        "cwd": str(args.cwd),
        "entry": str(args.entry),
        "release": str(args.release),
        "skill_name": args.skill_name,
        "args": args.args,
        "checks": checks,
        "record": record,
    }
    text = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    failed = sorted(name for name, ok in checks.items() if not ok)
    if failed:
        print(f"探针检查未通过：{'、'.join(failed)}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="R3a Host 验收探针：真实 Pi SDK 发现与显式展开（隔离环境）")
    parser.add_argument("--sdk", type=Path, required=True, help="已安装的 pi-coding-agent 包目录")
    parser.add_argument("--home", type=Path, required=True, help="一次性隔离的 HOME")
    parser.add_argument("--agent-dir", type=Path, required=True, help="一次性隔离的 PI_CODING_AGENT_DIR")
    parser.add_argument("--cwd", type=Path, required=True, help="一次性隔离的工作目录")
    parser.add_argument("--entry", type=Path, required=True, help="Host 里指向发布的 Skill 入口")
    parser.add_argument("--release", type=Path, required=True, help="发布内容目录 releases/rNNNN/files/<asset_id>")
    parser.add_argument("--skill-name", required=True, help="Skill 名，如 demo")
    parser.add_argument("--args", default="请按技能执行", help="`/skill:<name>` 后面的参数")
    parser.add_argument("--out", type=Path, help="把完整探针结果另存为 JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
