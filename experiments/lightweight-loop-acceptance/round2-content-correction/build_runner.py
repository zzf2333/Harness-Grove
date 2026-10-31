"""从 README 机械拼出 runner.sh，并核对 15 个 bash 块逐字进入脚本（本轮证据的可复跑入口）。

用法（在仓库根）：python3 experiments/lightweight-loop-acceptance/round2-content-correction/build_runner.py

runner.sh 结构：README 第 1–14 个 bash 块原样 → 证据采集段（不属于 README，只读命令与复制 $WORK 里的文件）
→ README 第 15 个 bash 块原样。每块后打印 `----- block N exit=$?`。
"""

import hashlib
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[3]
EXP = pathlib.Path(__file__).resolve().parent
README = REPO / "examples/lightweight-loop/README.md"
OUT = EXP / "outputs"

HEADER = """#!/bin/bash
# 由 examples/lightweight-loop/README.md 的 15 个 bash 块按顺序原样拼出，命令逐字一致。
# 证据采集段不属于 README：只跑只读命令、复制 $WORK 里的文件，不改变沙箱状态。
set -u
cd "{repo}"
OUT="{out}"
mkdir -p "$OUT"
"""

CAPTURE = '''
echo "===== 证据采集（不属于 README 的 15 个 bash 块；只读命令 + 复制 $WORK 里的文件）====="
cp "$WORK/inputs/change.json" "$OUT/change.json"
cp "$WORK/inputs/approval-rehearsal.json" "$OUT/approval-rehearsal.json"
cp "$WORK/inputs/review-W3.json" "$OUT/review-W3.json"
cp "$WORK/registry/assets.json" "$OUT/registry-assets.json"
cp "$WORK/digest/pi-index.json" "$OUT/pi-index.json"
cp "$WORK/digest/pi-1.md" "$OUT/pi-1.md"
cp "$WORK/phe/ledger.jsonl" "$OUT/ledger.jsonl"
shasum -a 256 "$SAMPLE/host-report.md" examples/lightweight-loop/host-report.md | tee "$OUT/host-report.sha256"
shasum -a 256 "$WORK/inputs/change.json" | tee "$OUT/change.sha256"
python3 -I tools/change.py status --phe-home "$WORK/phe" > "$OUT/status-final.json"
python3 -I tools/change.py review-plan --phe-home "$WORK/phe" > "$OUT/review-plan-final.json"
python3 -I - "$WORK" > "$OUT/checks.txt" <<'PY'
{checks}
PY
echo "----- 证据采集 exit=$?"
'''


def block(title: str, body: str, exit_note: str) -> str:
    return f'echo "===== {title} ====="\n{body}echo "----- {exit_note} exit=$?"\n'


def main() -> int:
    text = README.read_text(encoding="utf-8")
    blocks = re.findall(r"```bash\n(.*?)```", text, re.S)
    if len(blocks) != 15:
        raise SystemExit(f"README 的 bash 块不是 15 个：{len(blocks)}")

    checks = (EXP / "checks_inline.py").read_text(encoding="utf-8").rstrip("\n")
    if '"""' in checks or "'''" in checks:
        raise SystemExit("checks_inline.py 不能含三引号：它嵌在 runner 的 heredoc 里")

    parts = [HEADER.format(repo=REPO, out=OUT)]
    for index, body in enumerate(blocks[:14], start=1):
        parts.append(block(f"block {index}（README 第 {index} 个 bash 块）", body, f"block {index}"))
    parts.append(CAPTURE.format(checks=checks))
    parts.append(block("block 15（README 第 15 个 bash 块）", blocks[14], "block 15"))
    parts.append('echo "WORK 已删除：$([ -e "$WORK" ] && echo 否 || echo 是)"\n')
    runner = "".join(parts)
    (EXP / "runner.sh").write_text(runner, encoding="utf-8")

    lines = []
    for index, body in enumerate(blocks, start=1):
        readme_sha = hashlib.sha256(body.encode()).hexdigest()
        lines.append(f"block {index}: README {readme_sha} 在 runner.sh 中逐字出现={body in runner}")
    (OUT / "blocks-source.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not all(line.endswith("True") for line in lines):
        raise SystemExit("有 bash 块没有被逐字复制进 runner.sh")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
