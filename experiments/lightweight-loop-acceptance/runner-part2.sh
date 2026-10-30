#!/bin/bash
# README 第 15 个 bash 块，命令逐字一致；WORK 沿用第 1 块建好的临时工作区（同一次使用时是同一个 shell）。
set -o pipefail
exec > >(tee /tmp/pi-worker/lwl/run-part2.log) 2>&1
cd /Users/saonian/.pi/worktrees/Harness-Grove/pi-worktree-0e04e320-fc10-431c-afc0-572a640d99d4-s0-0
WORK=/private/var/folders/49/3ffdfzyd1m73r41mf734k14h0000gn/T/tmp.VFWWIb6tAG
echo "===== block 15（README 第 15 个 bash 块）====="
python3 -I tools/change.py status --phe-home "$WORK/phe"
chmod -R u+w "$WORK"
rm -rf "$WORK"
echo "----- block 15 exit=$?"
echo "WORK 已删除：$([ -e /private/var/folders/49/3ffdfzyd1m73r41mf734k14h0000gn/T/tmp.VFWWIb6tAG ] && echo 否 || echo 是)"
