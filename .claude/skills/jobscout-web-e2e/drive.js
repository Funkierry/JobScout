// Drives jobscout-web in a headless browser to verify:
//  1. login/register flow works
//  2. sending a message produces a report card inline in the chat flow
//  3. the "打印 / 导出为 PDF" button opens a populated print window
//  4. the "下载 Markdown 文件" button triggers a real file download
//
// By default the browser intercepts only the run SSE endpoint and returns a
// deterministic report fixture. Auth, thread creation, rendering, printing,
// and downloading still use the real application. This keeps UI regression
// tests fast and free of model cost without asking the production skill to
// violate its research/source contract.
//
// Set JOBSCOUT_REAL_RESEARCH=1 for an explicit, costly quality pass using the
// real "字节跳动 后端开发 校招" workflow. That mode also checks question-table
// sizes, banned source domains, and the persisted tool-call trace.
// See SKILL.md for prerequisites (Gateway on :8001, jobscout-web on :5500).
const { chromium } = require("playwright");
const path = require("path");
const fs = require("fs");

const BASE = "http://localhost:5500";
const GATEWAY_BASE = "http://localhost:8001";
const EMAIL = `jobscout-pw-test-${Date.now()}@example.com`;
const PASSWORD = "TestPass123!";
const REAL_RESEARCH = process.env.JOBSCOUT_REAL_RESEARCH === "1";
const DOWNLOAD_DIR = path.join(__dirname, "downloads");
fs.mkdirSync(DOWNLOAD_DIR, { recursive: true });

const REPORT_FIXTURE = `# 测试公司 · 后端开发 面试准备包

> 招聘类型：校招 · 生成日期：2026-09-19

1) 公司速览

- 用于前端联调的固定内容。[来源](https://example.cn/company)

2) 岗位拆解（校招后端）

| 类别 | 内容 | 来源 |
|---|---|---|
| 必备技能 | 后端基础 | [来源](https://example.cn/job) |

3) 面试题预测（校招后端）

### 技术 / 岗位题

| # | 题目 | 标签 | 来源 |
|---|---|---|---|
| 1 | 示例技术题 | 通用 | [来源](https://example.cn/tech) |

### 行为题

| # | 题目 | 标签 | 来源 |
|---|---|---|---|
| 1 | 示例行为题 | 通用 | [来源](https://example.cn/behavior) |

4. 一周上岸计划（7 天冲刺）

- 这段模拟模型越界内容，必须被前端报告契约剔除。

5. 话术模板

- 这段也不得进入展示、打印或下载结果。
`;

const MATCH_REPORT_FIXTURE = `# 简历 × 飞书岗位匹配报告

## 候选人画像
- AI 产品与技术复合背景

## 推荐岗位
| 排名 | 匹配度 | 公司 / 岗位 | 核心匹配 | 主要差距 | 建议动作 | 记录标识 |
|---|---:|---|---|---|---|---|
| 1 | 88 | 示例科技 / AI 产品经理 | Agent、RAG | 商业化经验 | 补充指标 | rec1 |

## 匹配依据
- 使用统一的 100 分匹配口径。

## 风险与数据边界
- 本次只读取 1 条确定性测试记录。

## 一周上岸计划
- 这段必须被岗位匹配报告契约剔除。
`;

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage();
  const errors = [];
  let activeThreadId = null;
  let baseContextRequests = 0;
  page.on("console", (msg) => { if (msg.type() === "error") errors.push(msg.text()); });
  page.on("pageerror", (err) => errors.push("pageerror: " + err.message));
  page.on("request", (request) => {
    const match = request.url().match(/\/api\/threads\/([^/]+)\/runs\/stream$/);
    if (match) activeThreadId = match[1];
  });

  if (!REAL_RESEARCH) {
    await page.route("**/api/threads/*/runs/stream", async (route) => {
      const isMatchingTurn = (route.request().postData() || "").includes("飞书 Base 岗位匹配");
      const body = `event: values\ndata: ${JSON.stringify({
        messages: [{ type: "ai", content: isMatchingTurn ? MATCH_REPORT_FIXTURE : REPORT_FIXTURE, additional_kwargs: {} }],
      })}\n\n`;
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream; charset=utf-8",
        headers: {
          "Access-Control-Allow-Credentials": "true",
          "Access-Control-Allow-Origin": BASE,
          "Cache-Control": "no-cache",
        },
        body,
      });
    });
    await page.route("**/api/jobscout/base-context", async (route) => {
      baseContextRequests++;
      await route.fulfill({
        status: 200,
        contentType: "application/json; charset=utf-8",
        headers: {
          "Access-Control-Allow-Credentials": "true",
          "Access-Control-Allow-Origin": BASE,
        },
        body: JSON.stringify({
          table_id: "tblJobs",
          table_name: "校招岗位库",
          view_id: "vewOpen",
          fields: ["公司", "岗位", "任职要求"],
          records: [{ record_id: "rec1", 公司: "示例科技", 岗位: "AI 产品经理", 任职要求: "Agent、RAG" }],
          record_count: 1,
          has_more: false,
          context_truncated: false,
        }),
      });
    });
  }

  console.log("== nav ==");
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.screenshot({ path: "shot-01-auth.png" });

  console.log("== register ==");
  await page.click("#authToggle"); // switch to register mode
  await page.fill("#authEmail", EMAIL);
  await page.fill("#authPassword", PASSWORD);
  await page.click("#authSubmit");

  await page.waitForSelector("#chatView:not(.hidden)", { timeout: 15000 });
  console.log("== logged in, chat view visible ==");
  await page.screenshot({ path: "shot-02-chat-welcome.png" });

  console.log(REAL_RESEARCH ? "== send real research request ==" : "== send deterministic UI fixture request ==");
  const msg = REAL_RESEARCH
    ? "字节跳动 后端开发 校招"
    : "测试公司 后端开发 校招";
  await page.fill("#composerInput", msg);
  await page.click("#composerSend");

  const reportTimeout = REAL_RESEARCH ? 12 * 60 * 1000 : 15000;
  console.log(`== waiting for report card (up to ${reportTimeout / 1000}s) ==`);
  await page.waitForSelector(".report-row", { timeout: reportTimeout });
  await page.screenshot({ path: "shot-03-report-card.png", fullPage: true });
  console.log("== report card rendered ==");
  const forbiddenGenericSections = /60\s*[-–—]\s*90\s*秒自我介绍|7\s*天冲刺|一周上岸计划|话术模板|专项准备资料|清单与打卡|STAR\s*模板/i;
  const renderedReportText = await page.locator(".report-row").innerText();
  if (forbiddenGenericSections.test(renderedReportText)) {
    throw new Error("rendered report exposed unrequested generic coaching sections");
  }

  if (REAL_RESEARCH) {
    const quality = await page.evaluate(() => {
      const root = document.querySelector(".report-row");
      const links = [...root.querySelectorAll("a[href]")].map((a) => a.href);
      const banned = links.filter((href) =>
        /(?:wikipedia|glassdoor|linkedin|indeed|teamblind|levels\.fyi|reddit)\./i.test(href)
      );
      const canonicalHeading = (value) => value
        .replace(/[／]/g, "/")
        .replace(/\s+/g, "")
        .replace(/[（(].*$/, "")
        .toLowerCase();
      const tableStats = (headingText) => {
        const heading = [...root.querySelectorAll("h1, h2, h3, h4, p")].find((node) =>
          canonicalHeading(node.textContent) === canonicalHeading(headingText)
        );
        if (!heading) return { rows: 0, rowsWithoutSource: 0 };
        let node = heading.nextElementSibling;
        while (node && node.tagName !== "TABLE" && !/^H[1-3]$/.test(node.tagName)) {
          node = node.nextElementSibling;
        }
        if (!node || node.tagName !== "TABLE") return { rows: 0, rowsWithoutSource: 0 };
        const rows = [...node.querySelectorAll("tbody tr")];
        return {
          rows: rows.length,
          rowsWithoutSource: rows.filter((row) => !row.querySelector("a[href]")).length,
        };
      };
      const technical = tableStats("技术/岗位题");
      const behavior = tableStats("行为题");
      return {
        technicalRows: technical.rows,
        behaviorRows: behavior.rows,
        questionRowsWithoutSource: technical.rowsWithoutSource + behavior.rowsWithoutSource,
        sourceLinks: links.length,
        uniqueSourceHosts: new Set(links.map((href) => new URL(href).hostname)).size,
        banned,
        text: root.innerText,
      };
    });
    console.log("Research quality:", quality);
    if (quality.technicalRows < 8) throw new Error(`expected >=8 technical rows, got ${quality.technicalRows}`);
    if (quality.behaviorRows < 4) throw new Error(`expected >=4 behavior rows, got ${quality.behaviorRows}`);
    if (quality.questionRowsWithoutSource) {
      throw new Error(`${quality.questionRowsWithoutSource} interview question row(s) lack a clickable source`);
    }
    if (quality.sourceLinks < 3) throw new Error(`expected >=3 source links, got ${quality.sourceLinks}`);
    if (quality.uniqueSourceHosts < 3) {
      throw new Error(`expected sources from >=3 independent hosts, got ${quality.uniqueSourceHosts}`);
    }
    if (quality.banned.length) throw new Error(`report used banned source(s): ${quality.banned.join(", ")}`);
    if (/维基百科|Wikipedia/i.test(quality.text)) throw new Error("report cited a banned encyclopedia source");

    if (!activeThreadId) throw new Error("could not capture the active thread id");
    const state = await page.evaluate(async ({ gateway, threadId }) => {
      const response = await fetch(`${gateway}/api/threads/${threadId}/state`, {
        credentials: "include",
      });
      if (!response.ok) throw new Error(`state read failed: HTTP ${response.status}`);
      return response.json();
    }, { gateway: GATEWAY_BASE, threadId: activeThreadId });
    const toolNames = (state.values?.messages || []).flatMap((message) =>
      (message.tool_calls || []).map((call) => call.name)
    );
    console.log("Persisted tool calls:", toolNames);
    const taskCallCount = toolNames.filter((name) => name === "task").length;
    if (taskCallCount !== 3) throw new Error(`expected exactly 3 task calls, got ${taskCallCount}`);
    if (toolNames.includes("ask_clarification")) {
      throw new Error("complete request unexpectedly called ask_clarification");
    }
  }

  console.log("== click print button, check popup ==");
  const [popup] = await Promise.all([
    context.waitForEvent("page", { timeout: 10000 }),
    page.click(".report-actions .primary-btn"),
  ]);
  await popup.waitForLoadState("load");
  const popupText = await popup.evaluate(() => document.body.innerText);
  await popup.screenshot({ path: "shot-04-print-window.png" });
  console.log("Print window text snippet:", popupText.slice(0, 200).replace(/\n/g, " | "));
  await popup.close();

  console.log("== click download button, check real file lands ==");
  const [download] = await Promise.all([
    page.waitForEvent("download", { timeout: 10000 }),
    page.click(".report-actions .link-btn"),
  ]);
  const downloadPath = path.join(DOWNLOAD_DIR, download.suggestedFilename());
  await download.saveAs(downloadPath);
  const downloaded = fs.readFileSync(downloadPath, "utf-8");
  if (forbiddenGenericSections.test(downloaded)) {
    throw new Error("downloaded Markdown exposed unrequested generic coaching sections");
  }
  console.log("Downloaded file:", download.suggestedFilename(), "bytes:", downloaded.length);
  console.log("Downloaded file head:", downloaded.slice(0, 120).replace(/\n/g, " | "));

  if (!REAL_RESEARCH) {
    console.log("== deterministic Feishu Base matching flow ==");
    await page.click("#newChatBtn");
    await page.click("#matchModeBtn");
    await page.fill("#baseUrlInput", "https://example.feishu.cn/base/test-token");
    await page.setInputFiles("#composerFile", {
      name: "resume.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("姓名：测试候选人\n技能：Agent、RAG\n", "utf-8"),
    });
    await page.fill("#composerInput", "优先 AI 产品经理岗位");
    await page.click("#composerSend");
    await page.waitForSelector(".report-row", { timeout: 15000 });
    const matchText = await page.locator(".report-row").innerText();
    if (!matchText.includes("简历 × 飞书岗位匹配报告") || !matchText.includes("rec1")) {
      throw new Error("matching mode did not render the expected traceable result");
    }
    if (matchText.includes("一周上岸计划")) {
      throw new Error("matching report exposed a section outside its output contract");
    }
    if (baseContextRequests !== 1) {
      throw new Error(`expected one Base context request, got ${baseContextRequests}`);
    }
    await page.screenshot({ path: "shot-05-base-matching.png", fullPage: true });
    console.log("== Base matching report rendered and sanitized ==");
  }

  console.log("== console errors observed ==");
  console.log(errors.length ? errors.join("\n") : "(none)");

  await browser.close();
  console.log("== DONE ==");
})().catch((err) => {
  console.error("DRIVE FAILED:", err);
  process.exit(1);
});
