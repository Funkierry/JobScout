// Synthetic SSE + minimal DOM integration. Runs with Node, no server/network.
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

function element(tag = "div") {
  return {
    tag, children: [], events: {}, innerHTML: "",
    appendChild(child) { this.children.push(child); },
    addEventListener(event, fn) { this.events[event] = fn; },
  };
}

async function main() {
  const chat = element();
  const context = vm.createContext({ console, TextDecoder, module: { exports: {} } });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "app.js"), "utf8"), context);
  context.document = { createElement: element, getElementById: () => chat };
  context.bubbles = [];
  vm.runInContext(`
    removeWelcomeState = () => {};
    scrollChatToBottom = () => {};
    addChatBubble = (role, text) => bubbles.push({ role, text });
    downloadMarkdown = text => { downloaded = text; };
    openPrintWindow = html => { printed = html; };
  `, context);
  const report = {
    type: "ai", content: "# 合成公司 · 后端 面试准备包\n\n## 公司速览\n[来源](https://example.cn/a)\n\n## 岗位拆解\n猜测（来源未核验）",
    additional_kwargs: { jobscout_evidence: { version: 2, accepted_count: 1, mode: "interview_prep", run_id: "fixture", removed_count: 1 } },
  };
  const human = { type: "human", content: "准备面试", additional_kwargs: { jobscout_mode: "prep" } };
  context.history = [human, report];
  vm.runInContext("renderHistoryMessages(history)", context);
  const [article, actions] = chat.children[0].children;
  assert(article.innerHTML.includes('href="https://example.cn/a"'));
  actions.children[0].events.click();
  actions.children[1].events.click();
  assert.strictEqual(context.downloaded, report.content + "\n");
  assert.strictEqual(context.printed, article.innerHTML);

  chat.children = [];
  context.bubbles = [];
  context.history = [human, { ...report, additional_kwargs: {} }];
  vm.runInContext("renderHistoryMessages(history)", context);
  assert.strictEqual(chat.children.length, 0, "unverified history must have no report/export card");
  assert(context.bubbles.some(({ text }) => text.includes("来源校验")));
  assert(!context.bubbles.some(({ text }) => text.includes("https://")));

  for (const message of [report, { ...report, additional_kwargs: {} }]) {
    const frame = Buffer.from(`event: values\ndata: ${JSON.stringify({ messages: [human, message] })}\n\n`);
    let consumed = false;
    context.api = async (_url, request) => {
      assert.strictEqual(request.json.config.context.jobscout_mode, "interview_prep");
      assert.strictEqual(request.json.input.messages[0].additional_kwargs.jobscout_mode, "prep");
      return { ok: true, body: { getReader: () => ({ read: async () => {
        if (consumed) return { done: true };
        consumed = true;
        return { done: false, value: frame };
      } }) } };
    };
    const live = await vm.runInContext('streamRunToText("fixture-thread", "准备面试", [])', context);
    if (message.additional_kwargs.jobscout_evidence) assert.strictEqual(live, report.content);
    else assert(live.includes("来源校验") && !live.includes("https://"));
  }
  // Base scores and their evidence must survive the same history/export path.
  const base = {
    type: "ai",
    content: "# 简历 × 飞书岗位匹配报告\n\n## 候选人画像\n已校验片段\n\n## 推荐岗位\n| 排名 | 匹配度 | 公司 / 岗位 | 核心匹配 | 主要差距 | 建议动作 | 记录标识 |\n|---|---|---|---|---|---|---|\n| 1 | 30/100 | 示例 / 开发 | 见下文 | 证据缺失项不计分 | 复核 | rec1 |\n\n## 匹配依据\n| 维度 | 得分 | 简历原文 |\n|---|---|---|\n| 技能 | 30 | Python |\n\n## 风险与数据边界\n无逐字依据不计分。",
    additional_kwargs: { jobscout_evidence: { version: 2, mode: "base_match", run_id: "fixture-base", accepted_count: 1, removed_count: 0 } },
  };
  context.base = base;
  for (const verified of [true, false]) {
    chat.children = [];
    context.bubbles = [];
    context.history = [
      { type: "human", content: "匹配岗位", additional_kwargs: { jobscout_mode: "match" } },
      verified ? base : { ...base, additional_kwargs: {} },
    ];
    vm.runInContext("renderHistoryMessages(history)", context);
    if (verified) {
      const [baseArticle, baseActions] = chat.children[0].children;
      assert(baseArticle.innerHTML.includes("30/100") && baseArticle.innerHTML.includes("Python"));
      baseActions.children[0].events.click();
      baseActions.children[1].events.click();
      assert.strictEqual(context.downloaded, base.content + "\n");
      assert.strictEqual(context.printed, baseArticle.innerHTML);
    } else {
      assert.strictEqual(chat.children.length, 0);
      assert(context.bubbles.some(({ text }) => text.includes("来源校验")));
    }
  }
  vm.runInContext('currentMode = "match"; lastBaseContext = { thread_id: "owned", context_ref: "server-ref" };', context);
  for (const thread of ["owned", "different"]) {
    let consumed = false;
    const frame = Buffer.from("event: values\ndata: " + JSON.stringify({ messages: [base] }) + "\n\n");
    context.api = async (_url, request) => {
      const config = request.json.config.context;
      assert.strictEqual(config.jobscout_evidence_version, 2);
      assert.strictEqual(config.jobscout_base_context_ref, thread === "owned" ? "server-ref" : undefined);
      return { ok: true, body: { getReader: () => ({ read: async () => {
        if (consumed) return { done: true };
        consumed = true;
        return { done: false, value: frame };
      } }) } };
    };
    context.testThread = thread;
    assert.strictEqual(await vm.runInContext('streamRunToText(testThread, "匹配", [])', context), base.content);
  }
  console.log("PASS: real history renderer, print/download actions, and values SSE use guarded content");
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
