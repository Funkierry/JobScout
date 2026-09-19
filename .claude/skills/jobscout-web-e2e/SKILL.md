---
name: jobscout-web-e2e
description: Launch the DeerFlow Gateway and the jobscout-web static frontend, then drive them with headless Chromium (Playwright) to verify the core interaction loop end to end — register/login, send a message, report card renders inline in the chat, print window opens correctly styled, Markdown download produces a real file. Use this whenever jobscout-web (app.js/index.html/style.css) changes and you need real-browser proof it still works, not just a syntax check.
---

# jobscout-web E2E (Playwright)

Verifies the actual user path through `jobscout-web/`, not just that the files
parse. See `jobscout-web/README.md` for the product and test contract.

## 1. Launch the Gateway

From the repo root:

```bash
cd backend
mkdir -p .deer-flow sandbox
PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 \
  DEER_FLOW_HOME="$(pwd)/.deer-flow" \
  nohup uv run --locked uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001 --reload \
  > .gateway.log 2>&1 &
```

Wait for readiness, then verify:

```bash
for i in $(seq 1 30); do curl -sf http://localhost:8001/health -o /dev/null && break; sleep 2; done
curl -s http://localhost:8001/health   # -> {"status":"healthy","service":"deer-flow-gateway"}
```

Requires `config.yaml` and `.env` to already exist at the repo root (`make config`
if not) with `GATEWAY_CORS_ORIGINS=http://localhost:5500,http://127.0.0.1:5500` —
jobscout-web talks to the Gateway cross-origin (`:5500` -> `:8001`), unlike
DeerFlow's own frontend which is same-origin via Next.js rewrites. This var is
read once at process start, so it needs a full Gateway *restart* after editing
`.env`, not just a save (unlike `config.yaml`, which hot-reloads on mtime change).

Stop it later with: `lsof -ti:8001 -sTCP:LISTEN | xargs -r kill` (or, on Windows,
find and kill the PID bound to 8001).

## 2. Launch jobscout-web

```bash
cd jobscout-web
nohup python -m http.server 5500 > .static.log 2>&1 &
curl -sf http://localhost:5500/ -o /dev/null && echo ok
```

Stop with: `lsof -ti:5500 -sTCP:LISTEN | xargs -r kill`.

## 3. Install Playwright (one-time per machine)

```bash
cd .claude/skills/jobscout-web-e2e
npm install
npx playwright install chromium
```

**If `npx playwright install` times out downloading from `cdn.playwright.dev`**
(seen repeatedly on the original dev machine — Node's https client stalls
against that CDN even though `curl -L` to the exact same URL finishes a 200MB+
download in ~20s; looks like a process-based network policy affecting
`node.exe` specifically, never fully root-caused): bypass Playwright's own
downloader and place the binaries yourself.

```bash
# Get the exact revision this playwright-core expects:
REV=$(node -e "console.log(require('./node_modules/playwright-core/browsers.json').browsers.find(b=>b.name==='chromium').revision)")
VER=$(node -e "console.log(require('./node_modules/playwright-core/browsers.json').browsers.find(b=>b.name==='chromium').browserVersion)")
BASE="https://cdn.playwright.dev/builds/cft/$VER/win64"
CACHE="$LOCALAPPDATA/ms-playwright"   # %LOCALAPPDATA%\ms-playwright on Windows

mkdir -p "$CACHE/chromium-$REV" && cd "$CACHE/chromium-$REV"
curl -sS -L --max-time 60 -o chrome-win64.zip "$BASE/chrome-win64.zip"

mkdir -p "$CACHE/chromium_headless_shell-$REV" && cd "$CACHE/chromium_headless_shell-$REV"
curl -sS -L --max-time 60 -o shell.zip "$BASE/chrome-headless-shell-win64.zip"
```

Then extract both (PowerShell, since Git Bash has no `unzip` here):

```powershell
Expand-Archive -Path "$env:LOCALAPPDATA\ms-playwright\chromium-<REV>\chrome-win64.zip" -DestinationPath "$env:LOCALAPPDATA\ms-playwright\chromium-<REV>" -Force
Expand-Archive -Path "$env:LOCALAPPDATA\ms-playwright\chromium_headless_shell-<REV>\shell.zip" -DestinationPath "$env:LOCALAPPDATA\ms-playwright\chromium_headless_shell-<REV>" -Force
```

Both `chrome-win64/chrome.exe` (used by non-headless launches) and
`chrome-headless-shell-win64/chrome-headless-shell.exe` (the default for
`chromium.launch()`, i.e. headless) are needed — `playwright install` fetches
both, so a manual bypass must too, or `launch()` fails with "Executable
doesn't exist at ...chrome-headless-shell.exe" even though the full browser
downloaded fine.

## 4. Run the driver

```bash
cd .claude/skills/jobscout-web-e2e
node drive.js
```

`drive.js` defaults to a deterministic UI regression:

- Registers a fresh, timestamped test account (`jobscout-pw-test-<ts>@example.com`)
  so it never collides with a real account or leaves reusable state behind.
- Logs in, waits for the chat view.
- Intercepts only the `runs/stream` SSE request and returns a deterministic
  report fixture. Auth, thread creation, the frontend stream parser, report
  rendering, printing, and downloading still run through the real browser and
  application. No model call or research API cost is incurred.
- Waits for `.report-row` to appear (the report card, inline in the chat flow —
  not a separate page).
- Clicks "打印 / 导出为 PDF", captures the popup window, screenshots it, and
  reads its text to confirm the print window actually has the report content
  (not blank/unstyled).
- The fixture deliberately appends model-drift chapters (a seven-day plan and
  scripts), then asserts the report contract removes them from the page.
- Clicks "下载 Markdown 文件", captures the real Playwright `download` event,
  saves it, and checks the content is the same contract-cleaned Markdown used
  by the rendered and printed views.
- Prints any browser console errors collected during the whole run — a lone
  `401` from `/api/v1/auth/me` before login is expected (the app probes for an
  existing session) and is not a bug.

For a deliberate live quality pass, use the same driver with real research
enabled (this can take many minutes and incur substantial model/search cost):

```bash
JOBSCOUT_REAL_RESEARCH=1 node drive.js
```

PowerShell:

```powershell
$env:JOBSCOUT_REAL_RESEARCH='1'; node drive.js
```

Live mode sends `字节跳动 后端开发 校招`, waits up to 12 minutes, and asserts
that the finished report has at least 8 technical/role questions, 4 behavioral
questions, a clickable source on every question row, sources from at least
three independent hosts, no banned international source domains or unsolicited
coaching chapters, exactly three persisted `task` calls, and no persisted
`ask_clarification` call. Keep this mode explicit: it validates the research/
tool flow and must not become an ordinary UI test dependency.

Screenshots land in this directory as `shot-01..04-*.png`; downloaded files in
`downloads/`. Both are gitignored — inspect them locally, don't commit them.

`drive-newfeatures.js` (same setup, run the same way — `node drive-newfeatures.js`)
covers the composer's drag-and-drop and history-recall behavior:

- Builds a real `DataTransfer` + `File` inside the page context and dispatches
  `dragenter`/`drop` on `#chatCard` — confirms the `#dropOverlay` shows during
  the drag and hides after drop, and that the dropped file lands in
  `#attachedChip` (same effect as the paperclip button), all through the real
  `app.js` handlers, not a mocked stand-in.
- Sends one placeholder-report message (same trick as `drive.js`), then
  presses `ArrowUp` in the now-empty composer and asserts the exact sent text
  comes back, then `ArrowDown` and asserts it returns to an empty draft.

## Gotchas

- **React-style controlled inputs aren't a concern here** — jobscout-web is
  plain DOM (`el.value = ...` + dispatching the actual `submit`/`click` would
  also work), but `drive.js` uses Playwright's `fill`/`click` anyway since
  that's the default-safe choice.
- **The print window is a real second `page`.** Playwright surfaces
  `window.open()` as a `context.waitForEvent('page')`, not something on the
  original `page` object — see `drive.js` for the pairing with the button
  click.
- **The download is a real OS-level file**, not a data URL captured in JS —
  `page.waitForEvent('download')` + `download.saveAs(...)` is required to get
  at it; reading `document` state alone won't show it.
- **Don't skip the report-card wait.** Deterministic mode allows 15 seconds;
  explicit live-research mode allows 12 minutes because it performs three real
  research tasks before synthesis.
