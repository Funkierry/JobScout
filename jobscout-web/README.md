# JobScout Web

JobScout Web is a build-free browser client for the DeerFlow Gateway. It keeps
the interaction in a chat thread, uploads resumes with DeerFlow's native file
metadata shape, force-activates the `/jobscout` skill on every turn, and renders
the final evidence-backed report inline with print and Markdown download actions.
The header switches between public-source interview preparation and private
Feishu Base resume matching.

## Main workspace

The browser client uses a full-height, ChatGPT-style workspace rather than a
standalone form. The left sidebar contains task-mode navigation, new-chat and
searchable DeerFlow thread history, plus the current account. The main area has
an empty-state prompt gallery, evidence/status header, inline reports, resume
drag-and-drop, and a persistent bottom composer. The sidebar becomes an
off-canvas drawer on narrow screens, while all existing auth, upload, stream,
print, Markdown download, and Feishu matching contracts remain unchanged.

## Run locally

Start the Gateway on `http://localhost:8001`, then serve this directory on port
5500:

```powershell
cd jobscout-web
python -m http.server 5500
```

The Gateway must allow `http://localhost:5500` in `GATEWAY_CORS_ORIGINS`.

## Report contract

A complete request needs a company, a searchable role direction, and one of
校招/社招/实习. Optional JD, resume, city, business line, and technology stack
never block research. The skill starts three research tasks in parallel and the
final report contains company research, role analysis, and two non-empty
interview-question tables; resume gap analysis appears only when a readable
resume is attached.

The UI accepts both semantic Markdown headings and numbered model output. Before
display, print, or download, it removes known unsolicited top-level coaching
chapters such as generic seven-day plans, scripts, checklists, and internal tool
receipts. It does not rewrite numbered content inside valid report sections.

In 岗位匹配 mode, the current turn must include a resume plus an HTTPS Feishu or
Lark Base/Wiki URL. The Gateway uses the logged-in user's authorization, selects
only job-relevant fields, limits records and text size, and returns a bounded
context. The Agent treats every cell as untrusted data and produces only the
candidate profile, ranked roles, matching evidence, and data-boundary sections.

## Test

```powershell
node test_pure.js
cd ..\.claude\skills\jobscout-web-e2e
node drive.js
```

The browser test uses a deterministic SSE fixture by default, so it exercises
the real auth/thread/render/print/download path without model or search cost.
An explicit live research pass is available when needed:

```powershell
$env:JOBSCOUT_REAL_RESEARCH='1'; node drive.js
```

Live mode is costly and checks the 8+4 question minimum, per-row clickable
sources, at least three independent source hosts, exactly three `task` calls,
and zero `ask_clarification` calls.

## Feishu / Lark connection

DeerFlow's managed Lark CLI and the `lark-base` skill are the intended path for
reading a private Feishu Base; the app does not bypass login or scrape a shared
page. Each real user must first finish application setup and user authorization
under **Capability Center → Plugins → Lark / Feishu**. The matching panel shows
the live connection state and links back to Capability Center. A real Base link
with at least one readable job row is still required for a live acceptance run.
