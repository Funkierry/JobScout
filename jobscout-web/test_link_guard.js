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
    additional_kwargs: { jobscout_links: { version: 1, mode: "interview_prep", run_id: "fixture", removed_count: 1 } },
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
    if (message.additional_kwargs.jobscout_links) assert.strictEqual(live, report.content);
    else assert(live.includes("来源校验") && !live.includes("https://"));
  }
  console.log("PASS: real history renderer, print/download actions, and values SSE use guarded content");
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
