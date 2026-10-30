#!/bin/bash
# 由 examples/lightweight-loop/README.md 的 bash 代码块原样拼出，命令逐字一致。
set -o pipefail
exec > >(tee /tmp/pi-worker/lwl/run-part2.log) 2>&1
cd /Users/saonian/.pi/worktrees/Harness-Grove/pi-worktree-0e04e320-fc10-431c-afc0-572a640d99d4-s0-0

echo "===== block 15（README 第 15 个 bash 块）====="
python3 -I tools/change.py status --phe-home "$WORK/phe"
rm -rf "$WORK"
echo "----- block 15 exit=$?"
