#!/bin/bash
# M1 D1 沙箱演练：temper 副本 → 登记 → 导入 → 发布 → 四个部署目标（claude、codex、pi、shared）→ 漂移中止 → 回滚。
# 对真实 temper 只读（cp -RL）；前后对真实 temper 与 Agent 目录做 Hash 快照。
# 用法：bash tools/m1/rehearse_d1.sh | tee experiments/m1-d1-rehearsal/run-$(date +%Y%m%dT%H%M%S).log
set -u
cd "$(dirname "$0")/../.."
S=$(mktemp -d /tmp/phe-rehearsal-XXXX); S=$(cd "$S" && pwd -P)
H=$S/home; P=$S/phe; T=$S/temper; R=$S/registry.json
echo "commit=$(git rev-parse --short HEAD) sandbox=$S"
snap(){ { find ~/AI/temper/config -type f -exec shasum {} + 2>/dev/null | sort
  for d in ~/.claude ~/.codex ~/.pi/agent ~/.agents/skills; do
    find "$d" -maxdepth 2 -type l -exec sh -c 'echo "$1 -> $(readlink "$1")"' _ {} \; ; done | sort; } | shasum | cut -d' ' -f1; }
BEFORE=$(snap)
step(){ local name=$1; shift; "$@" >"$S/$name.out" 2>&1; local rc=$?; echo "$name rc=$rc :: $(tail -1 "$S/$name.out")"; }

cp -RL ~/AI/temper/config "$T"
mkdir -p "$H/.claude/skills" "$H/.codex" "$H/.pi/agent/skills" "$H/.agents/skills"
for a in .claude/CLAUDE.md .codex/AGENTS.md .pi/agent/AGENTS.md; do ln -s "$T/AGENTS.md" "$H/$a"; done
for s in forge-accept forge-discover forge-iterate review-eval forge-dialogue; do
  ln -s "$T/skills/$s" "$H/.claude/skills/$s"; ln -s "$T/skills/$s" "$H/.agents/skills/$s"; done
for s in temper-weekly temper-apply; do ln -s "$T/skills/$s" "$H/.pi/agent/skills/$s"; done
echo "sandbox links=$(find "$H" -type l | wc -l | tr -d ' ') pointing_to_real_temper=$(find "$H" "$T" -type l -exec readlink {} \; | grep -c "$HOME/AI/temper")"

step inventory1 python3 -I tools/inventory.py --home "$H" --out "$S/inv1.json"
step adopt_agents python3 -I tools/registry.py adopt --inventory "$S/inv1.json" --registry "$R" --group "$T/AGENTS.md"
for s in forge-accept forge-dialogue forge-discover forge-iterate review-eval; do
  step "adopt_$s" python3 -I tools/registry.py adopt --inventory "$S/inv1.json" --registry "$R" --group "$T/skills/$s"; done
for s in temper-weekly temper-apply; do
  step "adopt_$s" python3 -I tools/registry.py adopt --inventory "$S/inv1.json" --registry "$R" --path "$H/.pi/agent/skills/$s"; done
step import python3 -I tools/release.py import --registry "$R" --inventory "$S/inv1.json" --phe-home "$P" --home "$H"
step release python3 -I tools/release.py release --phe-home "$P" --home "$H"
for a in claude codex pi shared; do
  step "deploy_$a" python3 -I tools/release.py deploy --agent "$a" --release r0001 --registry "$R" --phe-home "$P" --home "$H"; done
echo "links_to_release=$(find "$H" -type l -exec readlink {} \; | grep -c "$P/releases/r0001") of $(find "$H" -type l | wc -l | tr -d ' ')"
LINKS_DEPLOYED=$(find "$H" -type l -exec readlink {} \; | sort | shasum)

# 带外修改：改发布文件后再部署必须中止，链接不动。
F=$P/releases/r0001/files/asset-0001/AGENTS.md
chmod u+w "$(dirname "$F")" "$F"; printf '\n# out-of-band\n' >> "$F"
step deploy_after_tamper python3 -I tools/release.py deploy --agent claude --release r0001 --registry "$R" --phe-home "$P" --home "$H"
[ "$(find "$H" -type l -exec readlink {} \; | sort | shasum)" = "$LINKS_DEPLOYED" ] && echo "links unchanged after aborted deploy"

# 回滚四个部署目标（倒序），之后对基线 drift 必须为 0。
for j in $(ls -r "$P"/journal/*.json); do step "rollback_$(basename "$j" .json)" python3 -I tools/release.py rollback --journal "$j" --phe-home "$P" --home "$H"; done
step inventory2 python3 -I tools/inventory.py --home "$H" --out "$S/inv2.json"
python3 -I tools/registry.py drift --inventory "$S/inv2.json" --registry "$R" --out "$S/drift2.json"
echo "drift_after_rollback=$(python3 -I -c "import json;print(json.load(open('$S/drift2.json'))['changes'])") bindings=$(python3 -I -c "import json;print(json.load(open('$P/bindings.json')))")"

step guard_real_home python3 -I tools/release.py release --phe-home "$P" --home "$HOME"
AFTER=$(snap)
echo "real_env before=$BEFORE after=$AFTER $([ "$BEFORE" = "$AFTER" ] && echo SAME || echo CHANGED)"
