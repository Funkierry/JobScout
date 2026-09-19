// One-off Node smoke test for app.js's pure functions — no browser available
// this session, so this is a stand-in for at least catching logic bugs in
// the SSE-message parsing and Markdown renderer before a real browser test.
// Not a permanent test suite; delete once real browser testing is possible.

const assert = require("assert");
const {
  markdownToHtml,
  extractLastVisibleAiText,
  looksLikeReport,
  normalizeReportMarkdownForRender,
  sanitizeJobScoutReportMarkdown,
  withSkillPrefix,
} = require("./app.js");

// ---- extractLastVisibleAiText: real captured SSE `values` payload (Stage 4 curl test) ----
const realMessages = [
  { content: "只回复两个字:你好", type: "human", additional_kwargs: { timestamp: "2026-09-18T16:27:06Z" }, id: "a1" },
  { content: "<system-reminder>\n<current_date>2026-09-19</current_date>\n</system-reminder>", type: "system", additional_kwargs: { hide_from_ui: true }, id: "a2" },
  { content: "I'm sorry, but I couldn't understand your request.", type: "ai", additional_kwargs: {}, id: "a3" },
];
const extracted = extractLastVisibleAiText(realMessages);
assert.strictEqual(extracted, "I'm sorry, but I couldn't understand your request.", "should extract the last non-hidden ai message");
console.log("PASS: extractLastVisibleAiText ignores hide_from_ui and picks the last ai message");

// hidden ai message must not leak even if it's last
const withHiddenAiLast = [
  ...realMessages,
  { content: "internal planning note", type: "ai", additional_kwargs: { hide_from_ui: true }, id: "a4" },
];
assert.strictEqual(extractLastVisibleAiText(withHiddenAiLast), "I'm sorry, but I couldn't understand your request.");
console.log("PASS: a hidden ai message (even if last) is skipped");

// ---- extractLastVisibleAiText: ask_clarification is return_direct=True, so
// the run ends with a ToolMessage (type "tool"), not a type "ai" message —
// a real bug reported live by the user: every clarification question was
// silently swallowed because this function only checked type "ai". Shape
// below is copied from a real captured Gateway response (Stage 4). ----
const clarificationMessages = [
  { content: "我想准备字节跳动的面试", type: "human", additional_kwargs: {}, id: "b1" },
  { content: "", type: "ai", additional_kwargs: {}, tool_calls: [{ name: "ask_clarification", id: "call_1" }], id: "b2" },
  {
    content: "为了给你做一份有针对性的「面试准备包」，请补充下面信息：",
    additional_kwargs: { deerflow_tool_meta: { status: "success" } },
    type: "tool",
    name: "ask_clarification",
    tool_call_id: "call_1",
    id: "clarification:call_1",
  },
];
assert.strictEqual(
  extractLastVisibleAiText(clarificationMessages),
  "为了给你做一份有针对性的「面试准备包」，请补充下面信息：",
  "a return_direct tool message (ask_clarification) must be picked up, not just type ai"
);
console.log("PASS: extractLastVisibleAiText picks up a return_direct ToolMessage (ask_clarification)");

// ---- looksLikeReport ----
assert.strictEqual(looksLikeReport("请提供岗位链接"), false, "a clarification question is not a report");
const fakeReport = `# 字节跳动 · 后端开发工程师 面试准备包

## 公司速览
...

## 岗位拆解
...

## 面试题预测
...
`;
assert.strictEqual(looksLikeReport(fakeReport), true, "a report with 3 section headers should be detected");
console.log("PASS: looksLikeReport distinguishes a report from a clarification question");

// Regression: a real research run used numbered, standalone section titles
// instead of Markdown `##` headings. It was a complete report but stayed as a
// plain chat bubble because the detector only accepted the template's exact
// heading syntax.
const numberedReport = `字节跳动 后端开发 校招｜面试准备包

1) 公司速览
内容

2) 岗位拆解（校招后端｜降级标注已开启）
内容

3) 面试题预测（校招后端）
内容
`;
assert.strictEqual(looksLikeReport(numberedReport), true, "numbered standalone report sections should be detected");
console.log("PASS: looksLikeReport accepts numbered standalone section titles");

const normalizedNumberedReport = normalizeReportMarkdownForRender(numberedReport);
assert.ok(normalizedNumberedReport.startsWith("# 字节跳动 后端开发 校招｜面试准备包"));
assert.ok(normalizedNumberedReport.includes("## 公司速览"));
assert.ok(normalizedNumberedReport.includes("## 岗位拆解（校招后端｜降级标注已开启）"));
assert.ok(normalizedNumberedReport.includes("## 面试题预测（校招后端）"));
console.log("PASS: numbered report sections normalize into semantic Markdown headings for rendering");

// Regression: a live run returned the required evidence-backed sections but
// then appended generic coaching material that the user never requested.
// The JobScout UI must expose one stable report contract even when the model
// drifts: keep the research sections, drop preambles and extra top-level
// numbered chapters, and use the same cleaned Markdown for rendering/export.
const driftedReport = `# 校招求职情报包（字节跳动｜后端开发）

0. 使用说明
- 跑完“7 日上岸计划”

1. 公司速览
- 公司事实

2. 岗位拆解（后端开发｜校招）
| 类别 | 内容 |
|---|---|
| 必备技能 | Go |

3. 面试题预测（8+ 技术/岗位题，4+ 行为题）
技术/岗位题
| 题目 | 来源 |
|---|---|
| LRU | [来源](https://example.cn) |

4. 一周上岸计划（7 天冲刺）
- D1 刷题

5. 话术模板
- 自我介绍

执行凭据（工具收据）
- [r1 task]

Sources
- 重复来源
`;
const sanitizedReport = sanitizeJobScoutReportMarkdown(driftedReport);
assert.ok(sanitizedReport.includes("1. 公司速览"), "required company section remains");
assert.ok(sanitizedReport.includes("3. 面试题预测"), "required interview section remains");
assert.ok(!sanitizedReport.includes("使用说明"), "generic preamble is removed");
assert.ok(!sanitizedReport.includes("一周上岸计划"), "unrequested coaching plan is removed");
assert.ok(!sanitizedReport.includes("话术模板"), "unrequested scripts are removed");
assert.ok(!sanitizedReport.includes("执行凭据"), "internal receipt appendix is removed");
assert.ok(!sanitizedReport.includes("\nSources\n"), "duplicate source appendix is removed");
console.log("PASS: report sanitization keeps contract sections and removes model-added chapters");

// Regression (2026-09-19 real-world find): the model can mention the section
// names in an offer sentence ("要不要我做带来源的公司速览/岗位拆解/面试题预测版本?")
// without producing a real report — that must NOT render as a report card.
const fakeOffer = "需要我基于真实 JD 和公开来源,生成带来源链接的“公司速览/岗位拆解/面试题预测”的专属版本吗?";
assert.strictEqual(looksLikeReport(fakeOffer), false, "mentioning section names in prose is not a report");
console.log("PASS: looksLikeReport rejects a prose mention of section names that aren't real headings");

// ---- withSkillPrefix: every outgoing chat message must force-activate the
// jobscout skill (autonomous discovery is not deterministic) ----
const p1 = withSkillPrefix("我想准备字节跳动后端开发工程师的社招面试");
assert.ok(p1.startsWith("/jobscout\n"), "a plain free-text message gets the /jobscout prefix prepended");
assert.ok(p1.includes("我想准备字节跳动后端开发工程师的社招面试"), "original user text is preserved");
console.log("PASS: withSkillPrefix prefixes a plain message");

const p2 = withSkillPrefix("/jobscout 已经在用了");
assert.strictEqual((p2.match(/\/jobscout/g) || []).length, 1, "must not double-prefix a message that already starts with /jobscout");
console.log("PASS: withSkillPrefix does not double-prefix");

const p3 = withSkillPrefix("");
assert.ok(p3.startsWith("/jobscout"), "even an empty/whitespace-only message still gets prefixed (composer falls back to a default text before calling this)");
console.log("PASS: withSkillPrefix handles empty input without throwing");

// ---- markdownToHtml: render a realistic jobscout report shape ----
const sampleReport = `# 字节跳动 · 后端开发工程师 面试准备包

> 招聘类型:社招 · 生成日期:2026-09-19

## 公司速览

**业务与产品**
- 字节跳动是一家科技公司,旗下产品包括抖音、今日头条等 [来源](https://example.cn/a)

## 岗位拆解

| 类别 | 内容 | 来源 |
|---|---|---|
| 必备技能 | Go/Java, 分布式系统 | [来源](https://example.cn/b) |
| 加分项 | K8s 经验 | [来源](https://example.cn/c) |

> 以上为基于岗位名称的通用画像,非该公司该岗位专属。

## 面试题预测

### 技术题

| # | 题目 | 标签 | 来源 |
|---|---|---|---|
| 1 | 讲讲 Go 的 GMP 调度模型 | 通用,非字节跳动专属 | [来源](https://example.cn/d) |
`;

const html = markdownToHtml(sampleReport);
assert.ok(html.includes("<h1>字节跳动 · 后端开发工程师 面试准备包</h1>"), "h1 title");
assert.ok(html.includes("<h2>公司速览</h2>"), "h2 section");
assert.ok(html.includes("<blockquote>"), "blockquote rendered");
assert.ok(html.includes("<table>") && html.includes("<th>类别</th>"), "table with header row rendered");
assert.ok(html.includes('<a href="https://example.cn/a" target="_blank" rel="noopener">来源</a>'), "markdown link rendered as anchor");
assert.ok(html.includes("<strong>业务与产品</strong>"), "bold rendered");
assert.ok(!html.includes("**"), "no raw markdown bold markers leak into HTML");
assert.ok(!html.includes("](") , "no raw markdown link syntax leaks into HTML");
console.log("PASS: markdownToHtml renders headings/blockquote/table/links/bold correctly");

console.log("\nAll pure-function checks passed.");
