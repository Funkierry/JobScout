// Verifies the two new composer features in jobscout-web:
//  1. Drag-and-drop a file anywhere onto the chat card attaches it (same as
//     clicking the paperclip button) — the overlay shows on dragenter, hides
//     on drop, and the attached-file chip shows the right filename.
//  2. ArrowUp in the (empty) composer recalls the last sent message; ArrowDown
//     un-recalls back to the empty draft.
// See SKILL.md for prerequisites (Gateway on :8001, jobscout-web on :5500).
const { chromium } = require("playwright");
const path = require("path");
const fs = require("fs");

const BASE = "http://localhost:5500";
const EMAIL = `jobscout-pw-feat-${Date.now()}@example.com`;
const PASSWORD = "TestPass123!";

const FIXTURE_PATH = path.join(__dirname, "fixture-resume.txt");
fs.writeFileSync(FIXTURE_PATH, "姓名: 测试\n教育背景: 测试大学\n", "utf-8");

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext();
  const page = await context.newPage();
  const errors = [];
  page.on("console", (msg) => { if (msg.type() === "error") errors.push(msg.text()); });
  page.on("pageerror", (err) => errors.push("pageerror: " + err.message));

  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.click("#authToggle");
  await page.fill("#authEmail", EMAIL);
  await page.fill("#authPassword", PASSWORD);
  await page.click("#authSubmit");
  await page.waitForSelector("#chatView:not(.hidden)", { timeout: 15000 });
  console.log("== logged in ==");

  console.log("== drag-and-drop test ==");
  const fileBuffer = fs.readFileSync(FIXTURE_PATH);
  const fileContentBase64 = fileBuffer.toString("base64");

  // Build a real DataTransfer with a real File inside the page context, then
  // dispatch dragenter/drop with it — this exercises the actual app.js
  // handlers, not a mocked stand-in. Same handle reused for both events.
  const dtHandle = await page.evaluateHandle(
    ({ base64, name }) => {
      const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
      const file = new File([bytes], name, { type: "text/plain" });
      const dt = new DataTransfer();
      dt.items.add(file);
      return dt;
    },
    { base64: fileContentBase64, name: "fixture-resume.txt" }
  );

  await page.dispatchEvent("#chatCard", "dragenter", { dataTransfer: dtHandle });
  const overlayVisibleDuringDrag = await page.evaluate(() =>
    !document.getElementById("dropOverlay").classList.contains("hidden")
  );
  console.log("overlay visible on dragenter:", overlayVisibleDuringDrag);

  await page.dispatchEvent("#chatCard", "drop", { dataTransfer: dtHandle });

  await page.waitForSelector("#attachedChip:not(.hidden)", { timeout: 5000 });
  const chipText = await page.textContent("#attachedChipText");
  const overlayHiddenAfterDrop = await page.evaluate(() =>
    document.getElementById("dropOverlay").classList.contains("hidden")
  );
  console.log("attached chip text:", chipText.trim());
  console.log("overlay hidden after drop:", overlayHiddenAfterDrop);
  if (!chipText.includes("fixture-resume.txt")) throw new Error("drag-drop did not attach the dropped file");
  if (!overlayHiddenAfterDrop) throw new Error("drop overlay did not hide after drop");

  console.log("== clearing attachment before sending (don't actually upload the fixture) ==");
  await page.click("#attachedChipRemove");
  // waitForFunction's 2nd positional param is `arg` (passed into the page
  // function), not `options` — {timeout} there is silently ignored in favor
  // of the 30s default. `undefined` must be explicit to reach `options`.
  await page.waitForFunction(
    () => document.getElementById("attachedChip").classList.contains("hidden"),
    undefined,
    { timeout: 5000 }
  );

  console.log("== send a real (placeholder) message to populate send history ==");
  const msg =
    "不要调研、不要搜索,直接原样按以下要求纯格式化输出一份最小示例报告用于前端联调测试," +
    "必须严格包含这些一级/二级标题,内容随便写几句占位文字即可,不要调用任何工具:\n\n" +
    "# 测试公司 · 测试岗位 面试准备包\n\n## 公司速览\n占位内容。\n\n## 岗位拆解\n占位内容。\n\n" +
    "## 面试题预测\n占位内容。\n\n## 差距分析\n未提供简历,跳过。";
  await page.fill("#composerInput", msg);
  await page.click("#composerSend");
  await page.waitForSelector(".report-row", { timeout: 90000 });
  console.log("== report arrived, composer should be re-enabled now ==");
  await page.waitForFunction(() => !document.getElementById("composerInput").disabled, undefined, { timeout: 10000 });

  console.log("== ArrowUp recall test ==");
  const valueBeforeRecall = await page.inputValue("#composerInput");
  console.log("composer value before ArrowUp (expect empty):", JSON.stringify(valueBeforeRecall));
  await page.click("#composerInput");
  await page.keyboard.press("ArrowUp");
  const recalled = await page.inputValue("#composerInput");
  console.log("composer value after ArrowUp matches sent message:", recalled === msg);
  if (recalled !== msg) throw new Error("ArrowUp did not recall the last sent message");

  console.log("== ArrowDown un-recall test ==");
  await page.keyboard.press("ArrowDown");
  const afterDown = await page.inputValue("#composerInput");
  console.log("composer value after ArrowDown (expect empty):", JSON.stringify(afterDown));
  if (afterDown !== "") throw new Error("ArrowDown did not restore the empty draft");

  console.log("== console errors observed ==");
  console.log(errors.length ? errors.join("\n") : "(none)");

  await browser.close();
  fs.unlinkSync(FIXTURE_PATH);
  console.log("== DONE, ALL CHECKS PASSED ==");
})().catch((err) => {
  console.error("DRIVE FAILED:", err);
  process.exit(1);
});
