#!/bin/bash
# 由 examples/lightweight-loop/README.md 的 bash 代码块原样拼出，命令逐字一致。
set -o pipefail
exec > >(tee /tmp/pi-worker/lwl/run-part1.log) 2>&1
cd /Users/saonian/.pi/worktrees/Harness-Grove/pi-worktree-0e04e320-fc10-431c-afc0-572a640d99d4-s0-0

echo "===== block 1（README 第 1 个 bash 块）====="
WORK=$(cd "$(mktemp -d)" && pwd -P)
echo "$WORK"
echo "----- block 1 exit=$?"

echo "===== block 2（README 第 2 个 bash 块）====="
SAMPLE=$WORK/lightweight-loop
cp -R examples/lightweight-loop "$SAMPLE"
mkdir -p "$WORK/temper/config" "$WORK/home/.pi/agent" "$WORK/home/.codex" "$WORK/home/.claude" \
    "$WORK/registry" "$WORK/phe" "$WORK/inputs" "$WORK/digest"
cp "$SAMPLE/config/AGENTS.md" "$WORK/temper/config/AGENTS.md"
ln -s "$WORK/temper/config/AGENTS.md" "$WORK/home/.pi/agent/AGENTS.md"
ln -s "$WORK/temper/config/AGENTS.md" "$WORK/home/.codex/AGENTS.md"
ln -s "$WORK/temper/config/AGENTS.md" "$WORK/home/.claude/CLAUDE.md"
echo "----- block 2 exit=$?"

echo "===== block 3（README 第 3 个 bash 块）====="
python3 -I tools/history.py digest --host pi --root "$SAMPLE/pi-root" \
    --since 2026-10-01 --out-dir "$WORK/digest"
cat "$WORK/digest/pi-index.json"
cat "$WORK/digest/pi-1.md"
echo "----- block 3 exit=$?"

echo "===== block 4（README 第 4 个 bash 块）====="
sed -n '2p;6p;9p;12p' "$SAMPLE/pi-root/sessions/--home-demo-proj--/2026-10-28T09-00-00-000Z_7c1d0c4e-2b5a-4f36-9d2e-6a1b0c3d5e7f.jsonl"
sed -n '1p;2p;3p;4p' "$WORK/temper/config/AGENTS.md"
cat "$SAMPLE/host-report.md"
echo "----- block 4 exit=$?"

echo "===== block 5（README 第 5 个 bash 块）====="
python3 -I tools/inventory.py --home "$WORK/home" --out "$WORK/registry/inventory.json"
python3 -I tools/registry.py adopt --inventory "$WORK/registry/inventory.json" \
    --registry "$WORK/registry/assets.json" --group "$WORK/temper/config/AGENTS.md"
ASSET_ID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["assets"][0]["asset_id"])' "$WORK/registry/assets.json")
echo "$ASSET_ID"
echo "----- block 5 exit=$?"

echo "===== block 6（README 第 6 个 bash 块）====="
python3 -I examples/lightweight-loop/render_inputs.py --work "$WORK" --asset-id "$ASSET_ID"
echo "----- block 6 exit=$?"

echo "===== block 7（README 第 7 个 bash 块）====="
python3 -I tools/change.py apply --change "$WORK/inputs/change.json" --item W1 \
    --approval "$WORK/inputs/approval-rehearsal.json" --registry "$WORK/registry/assets.json" \
    --phe-home "$WORK/phe" --home "$WORK/home"
python3 -I tools/change.py apply --change "$WORK/inputs/change.json" --item W3 \
    --approval "$WORK/inputs/approval-rehearsal.json" --registry "$WORK/registry/assets.json" \
    --phe-home "$WORK/phe" --home "$WORK/home"
echo "----- block 7 exit=$?"

echo "===== block 8（README 第 8 个 bash 块）====="
python3 -I tools/change.py status --phe-home "$WORK/phe"
echo "----- block 8 exit=$?"

echo "===== block 9（README 第 9 个 bash 块）====="
for f in "$WORK/home/.pi/agent/AGENTS.md" "$WORK/home/.codex/AGENTS.md" "$WORK/home/.claude/CLAUDE.md"; do
    echo "== $f"; cat "$f"; done
cmp "$WORK/home/.pi/agent/AGENTS.md" "$WORK/home/.codex/AGENTS.md"
cmp "$WORK/home/.pi/agent/AGENTS.md" "$WORK/home/.claude/CLAUDE.md"
grep -c '^先给结论。$' "$WORK/temper/config/AGENTS.md"
echo "----- block 9 exit=$?"

echo "===== block 10（README 第 10 个 bash 块）====="
python3 -I tools/change.py review-plan --phe-home "$WORK/phe"
echo "----- block 10 exit=$?"

echo "===== block 11（README 第 11 个 bash 块）====="
cat "$WORK/inputs/review-W3.json"
echo "----- block 11 exit=$?"

echo "===== block 12（README 第 12 个 bash 块）====="
python3 -I tools/change.py review --execution x0002 --record "$WORK/inputs/review-W3.json" --phe-home "$WORK/phe"
echo "----- block 12 exit=$?"

echo "===== block 13（README 第 13 个 bash 块）====="
python3 -I tools/change.py restore --execution x0002 --phe-home "$WORK/phe" --home "$WORK/home"
python3 -I tools/change.py restore --execution x0001 --phe-home "$WORK/phe" --home "$WORK/home"
echo "----- block 13 exit=$?"

echo "===== block 14（README 第 14 个 bash 块）====="
cmp "$WORK/home/.pi/agent/AGENTS.md" examples/lightweight-loop/config/AGENTS.md
cmp "$WORK/home/.codex/AGENTS.md" examples/lightweight-loop/config/AGENTS.md
cmp "$WORK/home/.claude/CLAUDE.md" examples/lightweight-loop/config/AGENTS.md
shasum -a 256 "$WORK/home/.pi/agent/AGENTS.md" "$WORK/temper/config/AGENTS.md" examples/lightweight-loop/config/AGENTS.md
ls -l "$WORK/home/.pi/agent/AGENTS.md" "$WORK/home/.codex/AGENTS.md" "$WORK/home/.claude/CLAUDE.md"
echo "----- block 14 exit=$?"
