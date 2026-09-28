# JobScout Web

JobScout Web is a build-free browser client for the DeerFlow Gateway. It keeps
the interaction in a chat thread, uploads resumes with DeerFlow's native file
metadata shape, force-activates the `/jobscout` skill on every turn, and renders
the final evidence-backed report inline with print and Markdown download actions.
The header switches between public-source interview preparation and private
Feishu Base resume matching. The progress tracker is a third workspace section.

## Linked target roles

Use the **目标岗位** selector to keep matching, interview preparation, and
application progress together. Create a target directly, save a verified role
from a Feishu matching report, associate an interview report, or use **关联目标**
on a tracker row. The target remembers its preparation and matching threads;
switching modes restores the linked thread for that mode instead of mixing
both tasks in one conversation. A saved Feishu role retains its table/record
identity and a bounded JD snapshot so interview preparation can start from the
selected role. That private JD is treated as user-provided context, not a public
source. Matching still requires a resume on the current turn.

The matching report offers **保存并准备** for recommended rows whose record IDs
match the Base response. Up to ten verified candidates are saved with the
matching thread so these actions work when that report is reopened. Older
reports without a saved candidate snapshot need a new matching run. From a
linked tracker row, **准备面试 / 准备下一轮** places the current stage into the
preparation composer for review before sending. Linking a target does not
submit an application: progress tracking still needs the user's own official
application list or detail URL. Deleting a target removes its links while
keeping the existing conversations and tracker rows.

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

## Application progress tracker

Choose **进度追踪**, add a company and a personal application list or detail URL,
then select **添加并识别**. If the page requires authentication, the tracker
opens a browser window for you to complete login or verification manually. It
then reads the authorized page and saves the detected role title, current stage,
source evidence, and check time to the tracker table. The application date is
read from the official page when its quoted submission evidence supports it;
there is no date field to fill in the add form.

When one application-list page clearly pairs multiple applied roles with their
own statuses, the tracker creates a separate row for each role. Refreshes update
those rows using the role title as the identity, so the same listing does not
create duplicates. Once a concrete role is saved, the unidentified placeholder
for that same page is removed. It only records roles and statuses supported by visible page
text. The table shows the current-stage wait as a lower bound from the first
time that stage was detected, not as a claimed official stage-start date. It
supports individual or batch refresh, company/role inline edits, custom
stages, deletion, and CSV export. Data is stored in the Gateway's per-user
SQLite database; this feature does not write to Feishu or another external
spreadsheet.

The tags above the tracker table filter by the row's current custom stage. Each
tag shows its record count; **全部** restores the full list. The selected tag
stays active while rows refresh, and the displayed count updates with the data.
Desktop tracking opens in **紧凑显示**: the add form is folded away when records
exist, tags stay in a single scrollable row, and the table scrolls below a fixed
header. Click a row's status pill for its full source excerpt, confidence, and
official-page link, or turn off **紧凑显示** for the expanded table. The add form
remains available through **添加记录**. On narrow screens, tracker rows become
cards so fields and actions remain readable.

The table marks terminated or rejected applications in red, normal progress
and offers in green, and unknown or review-needed rows in a neutral color.

On Windows, the repository root includes `scripts/run-jobscout-gateway-local.cmd`
and `scripts/run-jobscout-web-local.cmd` for starting the two local services.

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
