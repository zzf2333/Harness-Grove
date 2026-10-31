/**
 * R3a Host 验收探针（Node 侧）：用真实 Pi SDK 发现 Skill，并用真实 `session.prompt` 触发显式展开。
 *
 * 不调真实模型：模型层用本地 faux provider（零网络、零费用），固定返回一句合法文本；探针把送进模型的
 * 消息整段回传，用来核对展开正文与 baseDir。不 mock loader 与 prompt 展开，不执行任何工具（只声明 `read`
 * 工具以满足 skills 进 system prompt 的条件，faux 回复不含 toolCall，所以没有工具结果）。
 *
 * 由 `tools/r3a/skill_host_probe.py` 用一次性隔离的 `HOME` / `PI_CODING_AGENT_DIR` / cwd 启动；本文件不读写
 * 真实用户配置。stdout 只输出一份 JSON，其余输出走 stderr。
 */

import { pathToFileURL } from "node:url";

const sdkRoot = process.env.PROBE_SDK_ROOT;
const agentDir = process.env.PROBE_AGENT_DIR;
const cwd = process.env.PROBE_CWD;
const skillName = process.env.PROBE_SKILL_NAME;
const skillArgs = process.env.PROBE_SKILL_ARGS ?? "";
const aiRoot = process.env.PROBE_AI_ROOT;

if (!sdkRoot || !aiRoot || !agentDir || !cwd || !skillName) {
  throw new Error("缺少 PROBE_SDK_ROOT / PROBE_AI_ROOT / PROBE_AGENT_DIR / PROBE_CWD / PROBE_SKILL_NAME");
}

const pi = await import(pathToFileURL(`${sdkRoot}/dist/index.js`).href);
const ai = await import(pathToFileURL(`${aiRoot}/dist/index.js`).href);

function section(text, open, close) {
  const start = text.indexOf(open);
  const end = text.indexOf(close, start);
  return start < 0 || end < 0 ? null : text.slice(start, end + close.length);
}

function skillPromptText(captured) {
  for (const request of captured) {
    for (const message of request.messages ?? []) {
      const parts = Array.isArray(message.content) ? message.content : [{ text: message.content }];
      for (const part of parts) {
        if (typeof part.text === "string" && part.text.includes(`<skill name="${skillName}"`)) {
          return part.text;
        }
      }
    }
  }
  return null;
}

const captured = [];
const faux = ai.fauxProvider();
faux.setResponses([
  (context) => {
    captured.push(JSON.parse(JSON.stringify(context)));
    return ai.fauxAssistantMessage("stub: 已按技能处理");
  },
]);
const runtime = await pi.ModelRuntime.create({ modelsPath: null, refreshOnCreate: false });
runtime.registerNativeProvider(faux.provider);

const loader = new pi.DefaultResourceLoader({ cwd, agentDir });
await loader.reload();
const discovered = loader.getSkills();

const { session } = await pi.createAgentSession({
  cwd,
  modelRuntime: runtime,
  model: faux.getModel(),
  resourceLoader: loader,
  sessionManager: pi.SessionManager.inMemory(),
  tools: ["read"],
});
const systemPrompt = session.systemPrompt;
await session.prompt(`/skill:${skillName} ${skillArgs}`.trim());

console.log(
  JSON.stringify({
    agent_dir: agentDir,
    cwd,
    skills: discovered.skills.map((skill) => ({
      name: skill.name,
      description: skill.description,
      filePath: skill.filePath,
      baseDir: skill.baseDir,
    })),
    diagnostics: discovered.diagnostics,
    skills_section: section(systemPrompt, "<skills>", "</skills>"),
    expanded: skillPromptText(captured),
    model_calls: captured.length,
    tool_results: session.messages.filter((message) => message.role === "toolResult").length,
  }),
);
session.dispose();
