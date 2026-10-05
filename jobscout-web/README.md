# JobScout Web

JobScout Web is a build-free browser client for the DeerFlow Gateway. It keeps
the interaction in a chat thread, uploads resumes with DeerFlow's native file
metadata shape, force-activates the `/jobscout` skill on every turn, and renders
the final evidence-backed report inline with print and Markdown download actions.
The workspace combines application progress, public-source interview preparation,
and private Feishu Base resume matching. See the [guardrails](../docs/jobscout/guardrails.md)
for code-enforced boundaries and the [metrics](../docs/jobscout/metrics.md) for measured results.

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

The browser client opens on the application tracker after login. Its flat visual
style uses solid blue accents, neutral surfaces, clear type and borders. Summary
cards count all saved applications, confirmed active processes, review-needed
records and confirmed offers; they are calculated from current records, never
placeholder statistics. Unknown, conflicting and failed checks remain in review.
The left sidebar contains task-mode navigation, new-chat and
searchable DeerFlow thread history, plus the current account. The main area has
an empty-state prompt gallery, evidence/status header, inline reports, resume
drag-and-drop, and a persistent bottom composer. The sidebar becomes an
off-canvas drawer on narrow screens, while all existing auth, upload, stream,
print, Markdown download, and Feishu matching contracts remain unchanged.

Each history entry has a **删除对话** button. A confirmation dialog identifies
the conversation and explains that its history and uploaded files will be removed.
Targets and application records survive; only the deleted thread's target links
and saved matching candidates are removed. Active runs cannot be deleted. Failure
keeps the dialog open for retry; a local cleanup journal allows retry after native
thread deletion has already completed. Deleting the current conversation clears
the composer context. Late list/history responses cannot restore deleted entries
or overwrite a newer session's lists.

This uses `DELETE /api/jobscout/threads/{thread_id}` and the existing Gateway
thread deletion lifecycle, including ownership checks and run reservations.
Restart an older Gateway to load the new route, then refresh the browser.

## Concurrent refresh and conversation

A tracker refresh and an AI conversation can run together. Switch between the
tracker and the active conversation without losing its thread, messages or
progress. Each has a task notice and a separate **停止刷新 / 停止生成** button;
**返回对话** also works on mobile. Starting another conversation or changing its
target waits until the current generation finishes or stops.

Refresh completion shows a notice; **查看结果** opens the details on demand.
Dialogs live outside the hidden panels, so background completion cannot leave
an invisible modal blocking the composer. Logout cancels owned requests and
late responses cannot restore the previous account's content.

Ordinary API calls time out after 30 seconds. A single tracker request allows
10 minutes for the server's check and login budget. Batch streams stop waiting
after 45 seconds without data or a heartbeat; the server sends a heartbeat every
10 seconds. Chat stops waiting after 120 seconds without stream traffic, or
15 minutes for the whole turn. These are client wait limits, not latency claims.
Chat uses the native run cancellation endpoint and cancel-on-disconnect; tracker
disconnect cancels pending checks. Completed results remain saved. A cancellation
request can fail when disconnected from the server; the UI reports that rather
than claiming the server stopped. Restart Gateway after updating the backend.

## Run locally

For a fresh machine or server, use the [Docker deployment guide](../docs/jobscout/deployment.md).
It serves this client at `/`, proxies the Gateway on the same origin, and retains
the upstream Capability Center for Feishu setup. Fresh instances create their
first administrator directly in this UI. Registration follows the server's
`registration_enabled` policy; connection failures offer a retry before any
credentials are submitted.

`runtime-config.js` contains public `window.JOBSCOUT_CONFIG` only. Set
`gatewayBase: ""` for same-origin requests and `capabilityCenterUrl` for the
connection-management page. With no override, HTTP localhost on port 5500 keeps
the legacy Gateway :8001 / Capability Center :3000 behavior; other origins use
the current site. Never put provider keys in this browser-visible file.

Start the Gateway on `http://localhost:8001`, then serve this directory on port
5500:

```powershell
cd jobscout-web
python -m http.server 5500
```

The Gateway must allow `http://localhost:5500` in `GATEWAY_CORS_ORIGINS`.

## Application progress tracker

Choose **进度追踪**, add a company and a personal application list or detail URL,
then select **添加并识别**. An individual check opens one browser window and
reuses it for manual login or verification when needed. It reads the authorized
page and same-host application JSON, then saves the detected role title, current stage,
source evidence, and check time to the tracker table. The application date is
read from the official page when its quoted submission evidence supports it;
there is no date field to fill in the add form.

A portal homepage with a visible **登录 / 注册** entry can also trigger the
manual-login window. If the homepage still does not reveal a personal
application, use the URL of **我的投递 / 申请记录** or a specific application detail
page instead of the site's root URL.

When one application-list page clearly pairs multiple applied roles with their
own statuses, the tracker creates a separate row for each role. Refreshes update
those rows using the role title as the identity, so the same listing does not
create duplicates. Once a concrete role is saved, the unidentified placeholder
for that same page is removed. Roles and statuses require verbatim DOM or JSON
evidence in the same application record; confidence comes from code rules.
Batch refresh processes each host serially and at most two host groups concurrently
(configurable). Expired sessions are marked **需登录** for a subsequent individual check.
See [tracker stability](../docs/jobscout/tracker-stability.md) for limits and replay commands.
The table shows the current-stage wait as a lower bound from the first
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
header in both compact and expanded density. The table fills the remaining
desktop height and supports the mouse wheel and keyboard PageDown; it does not
limit the list to the visible rows. Summary cards start collapsed; **展开概览**
opens them and the browser remembers that display preference. Click a row's
status pill for its full source excerpt, confidence, and
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
final report contains company research, role analysis, evidence-backed questions,
and an evidence-boundary section. There is no question quota; missing evidence
may leave a section empty. Resume gap analysis requires readable source evidence.

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

From the repository root:

```powershell
node jobscout-web/test_pure.js
backend/.venv/Scripts/python.exe -B jobscout-web/test_ui.py
backend/.venv/Scripts/python.exe -B jobscout-web/test_history_scroll.py
backend/.venv/Scripts/python.exe -B jobscout-web/test_concurrency.py
backend/.venv/Scripts/python.exe -B -m unittest discover -s skills/public/jobscout/scripts -p "test_*.py"
```

These checks use fixture/mock data. Real research is a separate, user-initiated
operation that can incur model and search costs; see the explicit baseline
commands and unrun metrics in [metrics.md](../docs/jobscout/metrics.md).

The browser check needs the installed Playwright Chromium runtime. Every HTTP
request is fulfilled from local code or synthetic API fixtures; unexpected URLs
are rejected. It covers desktop/mobile layouts, filters, source details,
schedule settings, notification read state, mode switching, keyboard navigation,
empty/error states and login layouts. Screenshots stay in `local_eval/ui/`.
The history/scroll check uses 100 synthetic applications and covers confirmation,
cancel, busy/error/retry, target preservation, HTML-safe titles, wheel/keyboard
scrolling, sticky headings, density and overview toggles, and mobile cards.
Its screenshots and measured row counts stay in `local_eval/jobscout_history_scroll/`.
To compare an older layout with the same fixtures, pass `--baseline-ref <commit>`.
`test_pure.js` also runs deferred-response tests for list ordering, account changes,
duplicate delete clicks and history selection.
`test_concurrency.py` uses synthetic streams and a virtual clock to cover concurrent
chat/refresh, desktop/mobile switching, background completion, explicit cancellation,
idle timeout, early stream termination, and logout during a delayed report save.

## Mail and scheduled refresh

**招聘邮件** displays grounded events and their source; it remains disabled until
local read-only credentials and sender-domain filters are configured.
**定时刷新** stores per-user frequency, daily limits and optional model fallback.
**通知** lists confirmed before/after status changes with source excerpts and
owner-scoped read state. See [mail](../docs/jobscout/mail-channel.md) and
[scheduling](../docs/jobscout/scheduled-refresh.md) for defaults and activation.

## Feishu / Lark connection

DeerFlow's managed Lark CLI and the `lark-base` skill are the intended path for
reading a private Feishu Base; the app does not bypass login or scrape a shared
page. Each real user must first finish application setup and user authorization
under **Capability Center → Plugins → Lark / Feishu**. The matching panel shows
the live connection state and links back to Capability Center. A real Base link
with at least one readable job row is still required for a live acceptance run.
