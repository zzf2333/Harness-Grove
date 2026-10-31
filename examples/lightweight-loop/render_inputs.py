"""把 host-report.md 的三条建议写成切片 2/3 的机读输入：变更包、演练批准、W3 的 review 记录（标准库）。

宿主显式准备这些工件，`tools/change.py` 不解析 Markdown。本脚本不读 tests/，三个 patch 与
`tests/test_change_lightweight.py` 的 PREFERENCE_PATCH / CLEANUP_PATCH / ASSUMPTION_PATCH 逐字节相同。

输入目录按 README 的临时工作区布局取：
- 报告：`<work>/lightweight-loop/host-report.md`
- 恢复后的会话副本：`<work>/lightweight-loop/pi-root/sessions/...`
- 共享配置副本（三个入口的软链目标）：`<work>/temper/config/AGENTS.md`

批准只覆盖 W1、W3，来源写 `sandbox_rehearsal`：这是演练，不是用户批准。W3 的 review 记
`observe` / `unknown`，因为改动只在临时沙箱应用，没有观察窗内的效果证据。
"""

import argparse
import hashlib
import json
from pathlib import Path

SAMPLE_DIR = "lightweight-loop"
SESSION_FILE = "pi-root/sessions/--home-demo-proj--/2026-10-28T09-00-00-000Z_7c1d0c4e-2b5a-4f36-9d2e-6a1b0c3d5e7f.jsonl"

# 偏好：把第 2 行改成“先给结论，再复述问题”。
PREFERENCE_PATCH = """--- a/config/AGENTS.md
+++ b/config/AGENTS.md
@@ -1,3 +1,3 @@
 先给结论。
-先复述问题再回答。
+先给结论，再复述问题。
 先给结论。
"""

# 清理：第 3 行与第 1 行重复，保留第 1 行原文，不改其他约束。
CLEANUP_PATCH = """--- a/config/AGENTS.md
+++ b/config/AGENTS.md
@@ -1,4 +1,3 @@
 先给结论。
 先复述问题再回答。
-先给结论。
 用户说先别做时，不要越界执行。
"""

# 假设：第 3、5 行在应用 PREFERENCE_PATCH 后仍未变，所以能接着打到当前生效内容上。
ASSUMPTION_PATCH = """--- a/config/AGENTS.md
+++ b/config/AGENTS.md
@@ -3,3 +3,3 @@
 先给结论。
-用户说先别做时，不要越界执行。
+用户说先别做时，不要越界执行，也不要顺手做相邻改动。
 复盘时先读账本。
"""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成轻量闭环示例的变更包、演练批准与 review 记录")
    parser.add_argument("--work", type=Path, required=True, help="README 建好的临时工作区")
    parser.add_argument("--asset-id", required=True, help="`registry.py adopt` 输出里的 asset id")
    args = parser.parse_args()

    sample = args.work / SAMPLE_DIR
    session = str(sample / SESSION_FILE)
    config = str(args.work / "temper/config/AGENTS.md")
    report = sample / "host-report.md"
    inputs = args.work / "inputs"

    change = {
        "schema_version": 1,
        "report": {"path": str(report), "sha256": sha256_bytes(report.read_bytes())},
        "items": [
            {
                "item_id": "W1",
                "evidence": [f"{session}:L6", f"{config}:L2"],
                "prediction": {
                    "why": "用户明确要求“以后每次回答都先给结论，再解释理由”（会话 L6）；"
                    "配置第 2 行要求先复述问题再回答，与新偏好冲突",
                    "expected": "先给结论，再复述问题；不丢“用户说先别做时，不要越界执行”这条约束",
                    "verify": "读一遍：措辞是否准确表达偏好、是否与其余行冲突；偏好不需要证明更优",
                    "baseline": "待观察",
                },
                "target": {"asset_id": args.asset_id},
                "patch": PREFERENCE_PATCH,
            },
            {
                "item_id": "W2",
                "evidence": [f"{config}:L3"],
                "prediction": {
                    "why": "配置第 1 行与第 3 行是同一句“先给结论。”，同一约束写了两处",
                    "expected": "同一约束只保留一处；保留第 1 行原文，不扩大授权",
                    "verify": "对照改前改后含义：仍有一条先给结论；收益待验证",
                },
                "target": {"asset_id": args.asset_id},
                "patch": CLEANUP_PATCH,
            },
            {
                "item_id": "W3",
                "evidence": [
                    f"{session}:L9",
                    f"{session}:L10",
                    f"{session}:L11",
                    f"{session}:L12",
                    f"{config}:L4",
                ],
                "prediction": {
                    "why": "原文有两条叫停（L9、L12），但相邻文件的越界写入只有一次：L10 助手调用 edit 改 README.md、"
                    "L11 返回“已写入 README.md”，且发生在 L9 明确叫停之后；L7、L8 改的是 config，不能算相邻越界。"
                    "材料只支持“一次可见越界 + 之后两条叫停”，不能计成两次独立越界；"
                    "配置第 4 行只写“先别做时不要越界执行”，没写不能顺手做相邻改动",
                    "expected": "越界后需要用户叫停的情况减少；不丢原有“先别做时不越界”约束",
                    "verify": "收益待验证：本轮不做行为实验，单条合成会话也说不出真实环境里的越界频率；"
                    "用户批准真实应用后，在观察窗内统计越界后需要叫停的次数",
                    "baseline": "待观察",
                },
                "target": {"asset_id": args.asset_id},
                "patch": ASSUMPTION_PATCH,
            },
        ],
    }
    change_path = inputs / "change.json"
    write_json(change_path, change)
    change_sha = sha256_bytes(change_path.read_bytes())

    approval = {
        "schema_version": 1,
        "approval_id": "approval-rehearsal-w1-w3",
        "source": "sandbox_rehearsal",
        "change_sha256": change_sha,
        "items": ["W1", "W3"],
        "note": "只在临时沙箱复跑示例的演练批准，不是用户批准；W2 未批准",
    }
    approval_path = inputs / "approval-rehearsal.json"
    write_json(approval_path, approval)

    review = {
        "verdict": "observe",
        "prediction_met": "unknown",
        "reason": "收益待验证：只在临时沙箱应用，Host 未加载；观察窗内没有该环境的会话，"
        "样例会话在应用之前，不能当观察证据；材料也只有一次可见的相邻文件越界写入（L10–L11），"
        "不能把两条叫停当成已发生的两次越界",
        "observations": [
            f"{session}:L9",
            f"{session}:L10",
            f"{session}:L11",
            f"{session}:L12",
            f"{config}:L4",
            str(change_path),
        ],
        "next": "用户批准真实应用后，按 prediction.verify 在观察窗内统计“越界后需要叫停”的次数，再决定保留或调整",
    }
    review_path = inputs / "review-W3.json"
    write_json(review_path, review)

    print(
        json.dumps(
            {
                "report": str(report),
                "change": str(change_path),
                "change_sha256": change_sha,
                "approval": str(approval_path),
                "review": str(review_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
