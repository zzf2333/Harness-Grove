import hashlib
import json
import pathlib
import sys

work = pathlib.Path(sys.argv[1])
session = work / "lightweight-loop/pi-root/sessions/--home-demo-proj--/2026-10-28T09-00-00-000Z_7c1d0c4e-2b5a-4f36-9d2e-6a1b0c3d5e7f.jsonl"
config = work / "temper/config/AGENTS.md"
report = work / "lightweight-loop/host-report.md"
repo_config = pathlib.Path("examples/lightweight-loop/config/AGENTS.md")
repo_report = pathlib.Path("examples/lightweight-loop/host-report.md")
raw = session.read_text(encoding="utf-8").splitlines()
lines = {str(i + 1): json.loads(line) for i, line in enumerate(raw)}
change = json.loads((work / "inputs/change.json").read_text(encoding="utf-8"))
approval = json.loads((work / "inputs/approval-rehearsal.json").read_text(encoding="utf-8"))
review = json.loads((work / "inputs/review-W3.json").read_text(encoding="utf-8"))
facts = [json.loads(line) for line in (work / "phe/ledger.jsonl").read_text(encoding="utf-8").splitlines()]
report_text = report.read_text(encoding="utf-8")
items = {item["item_id"]: item for item in change["items"]}

ok = []


def check(label, condition, detail=""):
    ok.append(condition)
    print(("[通过] " if condition else "[失败] ") + label + (f" —— {detail}" if detail else ""))


def text_of(line_no):
    message = lines[line_no]["message"]
    parts = []
    for part in message.get("content", []):
        if part.get("type") == "text":
            parts.append(part["text"])
        elif part.get("type") == "toolCall":
            parts.append(f"toolCall {part.get('name')} {json.dumps(part.get('arguments'), ensure_ascii=False)}")
    return " / ".join(parts)


print("== 会话原文：W3 引用到的行 ==")
for line_no in ("9", "10", "11", "12"):
    print(f"L{line_no} {lines[line_no].get('message', {}).get('role', lines[line_no].get('type'))}: {text_of(line_no)}")

print("\n== 配置原文 L1–L5（恢复后的共享文件）==")
for index, line in enumerate(config.read_text(encoding="utf-8").splitlines(), start=1):
    print(f"L{index} {line}")

stops = [n for n in ("9", "12") if "停" in text_of(n)]
adjacent = []
for line_no in sorted(lines, key=int):
    record = lines[line_no]
    if record.get("type") != "message":
        continue
    for part in record["message"].get("content", []):
        if part.get("type") == "toolCall" and part.get("name") in ("edit", "write"):
            path = str(part.get("arguments", {}).get("path", ""))
            if not path.startswith("config/"):
                adjacent.append((line_no, path))

def calls_of(line_no):
    return [part for part in lines[line_no]["message"].get("content", []) if part.get("type") == "toolCall"]


print("== 事件计数（本轮的纠正对象）==")
print(f"带“停”的用户叫停行：{stops}")
print(f"非 config 的助手写入调用：(行号, 路径) = {adjacent}")
call_ids = [call.get("id") for call in calls_of("10")]
check("两条叫停消息存在（L9、L12）", stops == ["9", "12"])
check("相邻文件写入只有一次，且在 L9 叫停之后", adjacent == [("10", "README.md")])
check("L11 是该调用的成功结果", lines["11"]["message"].get("role") == "toolResult"
      and lines["11"]["message"].get("isError") is False
      and lines["11"]["message"].get("toolCallId") in call_ids)

print("\n== change.json 的 W3 证据与三行说明 ==")
print(f"evidence = {json.dumps(items['W3']['evidence'], ensure_ascii=False)}")
for key in ("why", "expected", "verify", "baseline"):
    print(f"prediction.{key} = {items['W3']['prediction'][key]}")
check("W3 证据含 L10、L11（助手调用与工具结果）", any(p.endswith(":L10") for p in items["W3"]["evidence"])
      and any(p.endswith(":L11") for p in items["W3"]["evidence"]))
check("W3 三行说明不再写“两次因 Agent 顺手改相邻文件被叫停”", "两次因" not in items["W3"]["prediction"]["why"])
check("W3 三行说明写清一次可见越界与 L10/L11", "L10" in items["W3"]["prediction"]["why"]
      and "L11" in items["W3"]["prediction"]["why"])
check("报告不再写“两次因…被叫停”", "两次因" not in report_text)
check("报告写明 config 是独立基线、未提供 Retired、两条叫停不是两个独立事件",
      "不是本会话编辑后的快照" in report_text and "Retired" in report_text and "不是两个独立事件的证据" in report_text)
check("W3 收益仍标“收益待验证”", "收益待验证" in items["W3"]["prediction"]["verify"])

print("\n== 批准与来源 ==")
print(f"source = {approval['source']}，items = {approval['items']}，change_sha256 = {approval['change_sha256']}")
print(f"change.json 实际 sha256 = {hashlib.sha256((work / 'inputs/change.json').read_bytes()).hexdigest()}")
check("批准来源是 sandbox_rehearsal，只批 W1/W3",
      approval["source"] == "sandbox_rehearsal" and approval["items"] == ["W1", "W3"])
check("批准绑定的 hash 等于这份 change.json",
      approval["change_sha256"] == hashlib.sha256((work / "inputs/change.json").read_bytes()).hexdigest())

print("\n== 账本 ==")
print(f"事实 {len(facts)} 条，kind 顺序 = {[fact['kind'] for fact in facts]}")
check("来源全部 sandbox_rehearsal",
      all(fact.get("source") == "sandbox_rehearsal" for fact in facts if "source" in fact))
check("W2 从未入账", not any(fact.get("proposal_id") == "W2" for fact in facts))
ledger_text = (work / "phe/ledger.jsonl").read_text(encoding="utf-8")
check("账本没有把批准或应用写成效果已验证",
      "已验证" not in ledger_text and '"verified"' not in ledger_text and '"effect"' not in ledger_text)
check("非 review 事实没有 prediction_met",
      not any("prediction_met" in fact for fact in facts if fact["kind"] != "review"))
applied = {fact["proposal_id"]: fact for fact in facts if fact["kind"] == "apply"}
check("入账的三行说明与 change.json 原文一致",
      all(applied[item_id]["prediction"] == items[item_id]["prediction"] for item_id in ("W1", "W3")))
records = [fact for fact in facts if fact["kind"] == "review"]
print(f"review 记录：{json.dumps([{k: r['record'][k] for k in ('verdict', 'prediction_met')} for r in records], ensure_ascii=False)}")
check("review 只有一条、记在 x0002、保持 observe/unknown",
      len(records) == 1 and records[0]["execution_id"] == "x0002"
      and records[0]["record"]["verdict"] == "observe" and records[0]["record"]["prediction_met"] == "unknown")
check("review 的 observations 含 L10/L11",
      all(any(str(p).endswith(suffix) for p in records[0]["record"]["observations"])
          for suffix in (":L9", ":L10", ":L11", ":L12")))

print("\n== 应用与恢复的字节 ==")
print(f"x0001 before = {applied['W1']['before_sha256']}（原文件）")
print(f"x0001 after  = {applied['W1']['after_sha256']}")
print(f"x0002 after  = {applied['W3']['after_sha256']}（W1+W3 生效）")
for entry in (".pi/agent/AGENTS.md", ".codex/AGENTS.md", ".claude/CLAUDE.md"):
    path = work / "home" / entry
    print(f"{entry}: sha256={hashlib.sha256(path.read_bytes()).hexdigest()} 软链={path.is_symlink()}")
print(f"共享文件 sha256 = {hashlib.sha256(config.read_bytes()).hexdigest()}")
restores = {fact["execution_id"]: fact for fact in facts if fact["kind"] == "restore"}
check("W1、W3 应用后字节不同于原始，且两次应用不同",
      applied["W1"]["after_sha256"] != applied["W1"]["before_sha256"]
      and applied["W3"]["after_sha256"] != applied["W1"]["after_sha256"])
check("恢复链回到原字节：x0001 restored = 原文件，x0002 restored = W1 之后",
      restores["x0001"]["restored_sha256"] == applied["W1"]["before_sha256"]
      and restores["x0002"]["restored_sha256"] == applied["W1"]["after_sha256"])
repo_sha = hashlib.sha256(repo_config.read_bytes()).hexdigest()
entries = {entry: hashlib.sha256((work / "home" / entry).read_bytes()).hexdigest()
           for entry in (".pi/agent/AGENTS.md", ".codex/AGENTS.md", ".claude/CLAUDE.md")}
check("恢复后三个入口与共享文件、仓库原文逐字节相同",
      set(entries.values()) == {repo_sha} and hashlib.sha256(config.read_bytes()).hexdigest() == repo_sha,
      repo_sha)
check("恢复后三个入口仍是软链", all((work / "home" / name).is_symlink() for name in entries))
check("恢复后没有残留发布链（active 为空）", restores["x0001"]["release"] is None)

print("\n== 报告 hash ==")
report_sha = hashlib.sha256(report.read_bytes()).hexdigest()
print(f"沙箱内报告 sha256 = {report_sha}")
print(f"仓库内报告 sha256 = {hashlib.sha256(repo_report.read_bytes()).hexdigest()}")
print(f"change.json 记录的 report.sha256 = {change['report']['sha256']}")
check("报告 hash 一致（沙箱副本、仓库原文、change.json 记录）",
      report_sha == hashlib.sha256(repo_report.read_bytes()).hexdigest() == change["report"]["sha256"])

print(f"\n全部 {len(ok)} 项检查：{'通过' if all(ok) else '有失败'}")
print(f"WORK = {work}")
raise SystemExit(0 if all(ok) else 1)
