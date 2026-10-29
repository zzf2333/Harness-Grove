#!/usr/bin/env python3
"""生成 M2 合成输入并做本地结构/引用核对；不是正式冻结器，不调用模型/run/judge。

演练常量不是模型实测、预算批准或历史任务证据。正式前提缺口写入 inputs/blockers.json。
--check 仅校验本夹具，不替代 Skill 的人工前置确认。默认生成后保留现场。
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
# CLI 与 importlib 测试都需要显式加载相邻模块；必须先禁用 bytecode 并设置路径。
from host_acceptance_schema import REFERENCES, REQUIRED_FIELDS  # noqa: E402
from phe_judge import validate_schedule  # noqa: E402

ID = "m2-host-synthetic"
BROKEN = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
ORACLE = "from calc import add\nassert add(2, 3) == 5\nassert add(-2, 3) == 1\n"
REAL = "修改 add 后必须满足加法含负数输入的契约。\n"
REDUNDANT = "文件使用 UTF-8 编码。\n"
# 以下值只供文件交接的结构核对，绝不作为真实运行参数或预算授权。
CONSTANTS = {
    "model_id": "synthetic-not-a-model",
    "cli_version": "synthetic-not-a-cli",
    "provider": "synthetic-no-provider",
    "cost_per_invocation": 1.0,
    "tau_gap": 0.25,
    "tau_mad": 0.30,
}
BLOCKERS = [
    {
        "fields": ["task.provenance", "task.task_id", "task.original_request_sha256"],
        "missing": "可追溯的历史任务及人工确认的修复版本",
        "reason": "本夹具是手写合成任务；本地测试不能把它变成 historical_reconstructed",
        "step": "Skill §1 前置确认；协议前置检查",
    },
    {
        "fields": [
            "target.cli_version",
            "target.executable_sha256",
            "target.provider",
            "target.model_id",
            "target.model_settings_sha256",
            "target.argv_sha256",
        ],
        "missing": "真实 Target 的完整模型 ID、提供方、有效设置、可执行文件和参数绑定",
        "reason": "字段采用明确演练常量和本地拒绝执行脚本；没有启动 Target 或核验服务端模型",
        "step": "Skill §1/§2 模型与权限确认",
    },
    {
        "fields": [
            "target.isolation_flags",
            "target.auto_memory_disabled",
            "target.unexcludable_user_content_sha256",
            "harness.effective_load_chain_sha256",
        ],
        "missing": "真实加载链、自动记忆关闭、不可排除用户内容和子目录写隔离证据",
        "reason": "帮助与 OS 探针不能证明 Host 会话生效；未读取私人配置内容，也未启动会话",
        "step": "协议隔离/前置检查；Skill §1",
    },
    {
        "fields": [
            "experiment.budget.sizing_source",
            "experiment.budget.max_turns_per_invocation",
            "experiment.budget.max_process_seconds_per_invocation",
            "experiment.budget.max_estimated_cost_usd_per_invocation",
            "experiment.budget.max_estimated_cost_usd_total",
        ],
        "missing": "spike-000 用量与时长推导、用户批准的费用/回合数",
        "reason": "本轮禁止模型调用；sizing.md 明示未测，数值仅为演练常量",
        "step": "协议预算；Skill §2 冻结前补齐预算",
    },
    {
        "fields": [
            "gates.go_requires.aa_token_median_max_relative_gap",
            "gates.go_requires.aa_pooled_tokens_max_relative_mad",
            "gates.go_requires.aa_token_gates_rationale",
        ],
        "missing": "用户确定的 Token 噪声门槛和理由",
        "reason": "0.25/0.30 只复用协议建议作演练常量，未获真实实验批准",
        "step": "Skill §2；协议门槛",
    },
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def put(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def json_file(path, data):
    return put(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def git(target, *args):
    # 不读用户 Git 配置、不触发 hooks/签名，仅初始化本轮合成仓库。
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(
        [
            "git",
            "-C",
            str(target),
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "user.name=PHE Fixture",
            "-c",
            "user.email=phe@example.invalid",
            *args,
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def set_field(p, dotted, value):
    keys = dotted.split(".")
    for key in keys[:-1]:
        p = p.setdefault(key, {})
    p[keys[-1]] = value


def get_field(p, dotted):
    for key in dotted.split("."):
        p = p[key]
    return p


def null_fields(p, prefix=""):
    if isinstance(p, dict):
        return [field for key, value in p.items() for field in null_fields(value, f"{prefix}.{key}".strip("."))]
    if isinstance(p, list):
        return [field for i, value in enumerate(p) for field in null_fields(value, f"{prefix}[{i}]")]
    return [prefix] if p is None else []


def generate(root, experiment_id):
    root = root.resolve()
    root.mkdir(exist_ok=False)
    repo, target, inputs = root / "repo", root / "target", root / "inputs"
    for rel in ("docs/M-1_PROTOCOL.md", "skill/harness-evolve/SKILL.md", "examples/m-1/proposal.template.json"):
        dest = repo / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, dest)
    for path in sorted((REPO / "tools").glob("*.py")):
        dest = repo / "tools" / path.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
    for name in ("prepare_host_acceptance.py", "host_acceptance_schema.py"):
        dest = repo / "tools/m2" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(__file__).with_name(name), dest)
    (repo / "experiments" / experiment_id).mkdir(parents=True)
    (root / "tmp").mkdir()
    put(target / "calc.py", BROKEN)
    git(target, "init", "--template=", "-q")
    git(target, "add", "calc.py")
    git(target, "commit", "-qm", "synthetic task start")
    commit = git(target, "rev-parse", "HEAD")
    put(target / "calc.py", FIXED)
    patch = git(target, "diff", "--no-ext-diff", "--no-textconv", "--", "calc.py") + "\n"
    put(target / "calc.py", BROKEN)
    put(inputs / "known-good.patch", patch)
    put(inputs / "oracle.py", ORACLE)
    put(inputs / "request.md", "手写合成任务：修复 calc.add，使其返回两个整数的和，包括负数。\n")
    put(inputs / "dependencies.txt", "Python 标准库；无外部依赖。\n")
    json_file(inputs / "snapshot.json", {"calc.py": digest(target / "calc.py")})
    nonce = "0123456789abcdef0123456789abcdef"  # 合成常量，绝不当作不可推断的真实 nonce
    put(inputs / "nonce.txt", nonce)
    required = f"完成后写 .phe-calibration-receipt，内容必须精确等于 {nonce}。\n"
    for name, text in {"real": REAL, "required": required, "redundant": REDUNDANT}.items():
        put(inputs / f"rules/{name}.md", text)
    original = "# 合成 Harness\n" + REAL + REDUNDANT
    baseline = original + required + REDUNDANT
    put(inputs / "harness/original.md", original)
    put(inputs / "harness/baseline.md", baseline)
    variants = {
        "A1": baseline,
        "A2": baseline,
        "B_real": baseline.replace(REAL, "", 1),
        "B_required": baseline.replace(required, "", 1),
        "B_redundant": original + required,
    }
    for group, text in variants.items():
        put(inputs / f"harness/{group}.md", text)
    json_file(inputs / "load-chain.json", {"synthetic": True, "actual_host_loading": "unverified"})
    json_file(inputs / "model-settings.json", {"synthetic": True, "actual_model": "unverified"})
    put(inputs / "user-content.txt", "仅合成内容；未读取真实用户配置。\n")
    put(inputs / "target.py", 'raise SystemExit("Synthetic fixture must never execute a Target")\n')
    put(inputs / "sizing.md", "未执行 spike-000。180s/20 turns/1.0 USD 仅演练常量，无测量与批准。\n")
    argv = ["python3", str(inputs / "target.py")]
    json_file(inputs / "argv.json", argv)
    config = {
        "experiment_id": experiment_id,
        "base_dir": str(target),
        "harness": {g: str(inputs / f"harness/{g}.md") for g in variants},
        "prompt": (inputs / "request.md").read_text(),
        "target_argv": argv,
        "claude_args": [],
        "settings": {},
        "env": {},
        "oracle_tests": [{"src": str(inputs / "oracle.py"), "dest": "oracle.py"}],
        "oracle_argv": ["python3", "oracle.py"],
        "oracle_cwd": ".",
        "nonce_sha256": digest(inputs / "nonce.txt"),
    }
    json_file(inputs / "config.json", config)
    p = json.loads((repo / "examples/m-1/proposal.template.json").read_text())
    p["experiment_id"] = experiment_id
    p["task"]["provenance"] = "handwritten_synthetic"
    p["fixture"] = {
        "synthetic": True,
        "never_execute": True,
        "constants": CONSTANTS,
        "formal_freeze_ready": False,
        "blockers": BLOCKERS,
    }
    values = {
        "paths.phe_repo_abs": str(repo),
        "paths.target_repo_abs": str(target),
        "paths.temp_root_abs": str(root / "tmp"),
        "paths.artifact_root_relative_to_phe_repo": f"experiments/{experiment_id}",
        "target.cli_version": CONSTANTS["cli_version"],
        "target.provider": CONSTANTS["provider"],
        "target.model_id": CONSTANTS["model_id"],
        "target.isolation_flags": [],
        "target.auto_memory_disabled": False,
        "task.task_id": "synthetic-add",
        "task.start_commit": commit,
        "rules.real_rule.id": "synthetic-add-rule",
        "experiment.budget.max_turns_per_invocation": 20,
        "experiment.budget.max_process_seconds_per_invocation": 180,
        "experiment.budget.max_experiment_wall_seconds": 7125,
        "experiment.budget.max_estimated_cost_usd_per_invocation": CONSTANTS["cost_per_invocation"],
        "experiment.budget.max_estimated_cost_usd_total": 25 * CONSTANTS["cost_per_invocation"],
        "gates.go_requires.aa_token_median_max_relative_gap": CONSTANTS["tau_gap"],
        "gates.go_requires.aa_pooled_tokens_max_relative_mad": CONSTANTS["tau_mad"],
        "gates.go_requires.aa_token_gates_rationale": "仅协议建议值作为演练常量；未获用户确定",
    }

    for field, value in values.items():
        set_field(p, field, value)
    for field, rel in REFERENCES.items():
        set_field(p, field, digest(inputs / rel))
    p["experiment"]["budget"]["sizing_source"].update(
        {"experiment_id": "synthetic-sizing-not-spike000", "sizing_file": str(inputs / "sizing.md")}
    )
    p["task"]["oracle_files_sha256"] = {str(inputs / "oracle.py"): digest(inputs / "oracle.py")}
    json_file(inputs / "references.json", REFERENCES)
    json_file(inputs / "blockers.json", BLOCKERS)
    json_file(inputs / "proposal.json", p)
    return inputs / "proposal.json"


def check(path):
    p = json.loads(path.read_text())
    inputs = Path(p["paths"]["phe_repo_abs"]).parent / "inputs"
    refs = json.loads((inputs / "references.json").read_text())
    problems = [f"null: {field}" for field in null_fields(p)]
    for section, keys in REQUIRED_FIELDS.items():
        for key in keys.split():
            field = f"{section}.{key}" if section else key
            try:
                get_field(p, field)
            except (KeyError, TypeError):
                problems.append(f"missing: {field}")
    if refs != REFERENCES:
        problems.append("references: expected fixed fixture mapping")
    try:
        validate_schedule(p)
    except (ValueError, KeyError, TypeError) as exc:
        problems.append(f"schedule: {exc}")
    for field, rel in REFERENCES.items():
        source = inputs / rel
        try:
            actual = get_field(p, field)
        except (KeyError, TypeError):
            actual = None
        if not source.is_file() or actual != digest(source):
            problems.append(f"hash: {field}")
    oracle = inputs / "oracle.py"
    expected_oracles = {str(oracle): digest(oracle)} if oracle.is_file() else {}
    if not expected_oracles or p["task"].get("oracle_files_sha256") != expected_oracles:
        problems.append("hash: task.oracle_files_sha256")
    target = Path(p["paths"]["target_repo_abs"])
    snapshot = json.loads((inputs / "snapshot.json").read_text())
    for rel, expected in snapshot.items():
        if not (target / rel).is_file() or digest(target / rel) != expected:
            problems.append(f"snapshot: {rel}")
    if git(target, "rev-parse", "HEAD") != p["task"]["start_commit"]:
        problems.append("task.start_commit")
    sidecar = path.with_name("proposal.sha256")
    if sidecar.exists() and sidecar.read_text().strip() != digest(path):
        problems.append("proposal.sha256")
    return {
        "structural_ok": not problems,
        "problems": problems,
        "formal_freeze_ready": False,
        "formal_blockers": p["fixture"]["blockers"],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, help="不存在的临时子目录；不覆盖")
    ap.add_argument("--experiment-id", default=ID)
    ap.add_argument("--check", type=Path)
    args = ap.parse_args()
    try:
        if args.check:
            proposal = args.check
        else:
            if (
                not args.experiment_id
                or Path(args.experiment_id).name != args.experiment_id
                or args.experiment_id in (".", "..")
            ):
                raise ValueError("experiment-id 必须是单一目录名")
            root = args.root or Path(tempfile.mkdtemp(prefix="phe-m2-inputs-")) / "corpus"
            # 只接受系统临时目录；不把本工具变成任意输出根写入器。
            resolved = root.resolve()
            # 这里只核对根范围，实际输出仍由 generate 的排他 mkdir 创建。
            temp_roots = (Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve())  # noqa: S108
            if not any(parent in resolved.parents for parent in temp_roots):
                raise ValueError("root 必须是系统临时目录内不存在的子目录")
            proposal = generate(resolved, args.experiment_id)
        result = check(proposal)
        result["proposal"] = str(proposal)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["structural_ok"] else 1
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f"prepare_host_acceptance: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
