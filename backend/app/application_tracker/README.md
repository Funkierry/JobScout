# Application Tracker — Phases 1–3

## Online tracker workflow

The JobScout web tracker accepts a company and a personal application
list/detail URL. After adding it, the browser workflow inspects the page and
grounds detected role names and current statuses in visible page text. When a
logged-in listing page clearly associates multiple applied roles with their
own statuses, it creates one progress-table row per role; repeated refreshes
update those rows instead of creating duplicates. Once concrete roles are saved,
the unidentified placeholder for that same page is removed. Each generated row reopens
the same private source page and targets its saved role name. Verbatim submission-date
evidence is validated against the page before `applied_at` is saved, separately
for each role; job publication and deadline dates are rejected. The table supports inline company and role
edits; user-managed stage names/order; per-row and batch
refresh; deletion; and UTF-8 CSV export. A manually selected stage is preserved
on later refreshes, while automatic stages follow recognized status changes.
The UI shows waiting time from `changed_at`, the first observed time of the
current status, as a lower bound; manual stages have no inferred start time.
Keep the URL private: it may identify a personal application, and do not paste
passwords or session tokens into the URL.

The same per-user SQLite store also keeps JobScout target roles in
`jobscout_opportunities`. `jobscout_opportunity_threads` links each target to
owned preparation or matching conversations, and
`jobscout_opportunity_applications` links it to an owned tracker row. Each
conversation or tracker row belongs to at most one target. Deleting a target
only deletes these links; it does not delete the underlying conversation or
application. Feishu-sourced targets use the table and record IDs as a stable
identity and keep a bounded JD snapshot. `jobscout_match_candidates` stores
up to ten verified report candidates per owned matching thread so report
actions survive a page reload. None of these links writes to Feishu or turns a
role recommendation into an application.

The Phase 1 offline extraction path accepts page text captured elsewhere. It
does not open websites, reuse cookies, write the online database, or mount an
HTTP route. It asks a configured DeerFlow model for a typed status, verifies
that quoted status and evidence exist in the input, and writes a normalized
record as CSV.

## Input

Extraction input is UTF-8 JSONL with one object per line:

```json
{"case_id":"demo-1","company":"示例科技","role":"AI 产品经理","url":"https://careers.example.com/applications/1","applied_at":"2026-09-01","notes":"校招","page_text":"当前状态：在线测评待完成"}
```

The committed examples are synthetic and live under
`tests/fixtures/application_tracker/`. Do not commit real application pages,
account identifiers, cookies, or personal details.

## Browser access (Phase 2)

Install the repository's locked browser extra and Chromium once:

```powershell
uv sync --locked --extra browser
uv run --locked --extra browser playwright install chromium
```

Then check one real application URL from `backend/`:

```powershell
uv run --locked --extra browser python scripts/application_tracker_browser_check.py `
  "https://your-application-portal.example/applications/123"
```

The first visit is headless. If deterministic rules find a login URL, password
input, an isolated login action on a private application/progress route,
CAPTCHA, or risk-control page, that context is closed and the same local profile
is reopened in a visible Chromium window. Complete login manually; the script
polls every 1.5 seconds for at most five minutes and never attempts to bypass a
CAPTCHA. Profiles are separated by hashed user and hostname keys. A timed-out
login is returned directly as `login_required` without spending model calls on
a page that is still inaccessible.

The command prints only progress and a result summary. Use
`--snapshot-output .deer-flow/application-tracker/snapshot.txt` only when you
explicitly want to retain the page text locally. Never commit that output.

Browser settings may be placed in `.env`:

```dotenv
APPLICATION_TRACKER_BROWSER_PROFILE_DIR=.deer-flow/application-tracker/browser_profile
APPLICATION_TRACKER_BROWSER_HEADLESS=true
APPLICATION_TRACKER_LOGIN_TIMEOUT_SECONDS=300
APPLICATION_TRACKER_LOGIN_POLL_SECONDS=1.5
APPLICATION_TRACKER_NAVIGATION_TIMEOUT_MS=30000
APPLICATION_TRACKER_PAGE_TEXT_TIMEOUT_MS=10000
APPLICATION_TRACKER_USER_ID=local-user
```

Initial navigation, redirects, and HTTP(S) subresources share the same public
URL guard. Local/private addresses are denied in production; only the offline
browser integration test opts into localhost.

## Agent workflow (Phase 3)

Phase 3 is an app-layer LangGraph workflow rather than another generic
DeerFlow lead agent. It keeps authenticated browser state scoped to one
application and is not registered in `langgraph.json`.

```text
START -> open_page -> text available? -> structured extraction
                     |                        |
                     no                       high confidence -> END
                     |                        |
                     +-----> agent fallback <- low/unknown
                                |
                    click / screenshot / human login
                                |
                         update_record -> END
```

The six scoped tools are `open_page`, `get_page_text`, `screenshot`, `click`,
`request_human_login`, and `update_record`. `click` accepts only element refs
from the latest observation and blocks destructive labels such as withdrawing
an application or accepting/declining an offer. If deterministic login rules
miss a nonstandard page, the agent may request the visible login window once
as a fallback. After login, the agent keeps that same visible browser context
while it reopens and reads the application page; it does not switch browser
mode mid-session, because some portals bind authentication to the active
browser environment. The window closes when the check finishes. Screenshot
bytes are kept in memory and sent to the configured vision-capable model only
when the agent explicitly asks for visual fallback. Visually inferred records
are capped at confidence `0.69`, forcing later manual confirmation.

Run one record from `backend/`:

```powershell
uv run --locked --extra browser python scripts/application_tracker_agent_check.py `
  "https://your-application-portal.example/applications/123" `
  --company "Example Co" `
  --role "AI Product Manager" `
  --applied-at "2026-09-01"
```

Additional `.env` settings:

```dotenv
APPLICATION_TRACKER_AGENT_MODEL=gpt-4o-mini
APPLICATION_TRACKER_CONFIDENCE_THRESHOLD=0.7
APPLICATION_TRACKER_MAX_AGENT_STEPS=6
APPLICATION_TRACKER_MAX_PAGE_CHARS=50000
APPLICATION_TRACKER_MAX_SCREENSHOT_BYTES=5000000
```

## Extract

Run from `backend/`. `--model` is a DeerFlow model profile from the root
`config.yaml`. If omitted, `APPLICATION_TRACKER_MODEL` is used, then DeerFlow's
first configured model. Provider credentials continue to come from the normal
environment-variable references in `config.yaml`.

```powershell
.\.venv\Scripts\python.exe scripts\application_tracker_extract.py `
  --input tests\fixtures\application_tracker\offline_cases.jsonl `
  --output .deer-flow\application-tracker\offline-results.csv
```

The output columns are, in order:

```text
company, role, url, status, raw_status, confidence, evidence, applied_at, applied_at_evidence,
checked_at, changed_at, check_result
```

## Evaluate

The small synthetic evaluation set covers every normalized status once. This
command invokes the configured model sequentially, writes per-case details to
JSON, and optionally writes the normalized predictions to CSV:

```powershell
.\.venv\Scripts\python.exe scripts\application_tracker_eval.py `
  --report .deer-flow\application-tracker\eval-report.json `
  --predictions .deer-flow\application-tracker\eval-predictions.csv
```

The report includes exact status accuracy, Pydantic-schema success rate,
verbatim-evidence grounding rate, and `check_result` accuracy. Unit tests use
stub models and require no network or provider credentials; the evaluation
command itself calls the configured LLM.

## Privacy boundary

Only a bounded prefix of `page_text` is sent to the configured model. The
extractor labels both metadata and page content as untrusted and never logs the
page text. The production browser and agent never log cookies or page text;
their screenshots are not written to disk. Page observations and fallback screenshots are sent to
the configured model, so use a provider consistent with the privacy policy for
your application data. Database persistence and UI updates start in Phase 4.

## Stage 0 private baseline

The optional `scripts/application_tracker_snapshot.py` captures the existing
persistent-profile page text, bounded XHR/fetch JSON, final URL, and a viewport
screenshot under the gitignored `local_eval/tracker_snapshots/` directory. It
does not invoke a model. `scripts/application_tracker_annotate.py` creates a
private JSONL label set; `scripts/application_tracker_baseline.py` replays that
set through the current structured extractor only when `--allow-model-cost` is
explicitly supplied. The latter is an offline extraction baseline, not a
browser-to-result end-to-end score. Collection steps, privacy limits, metric
definitions, and pending real results are in `docs/jobscout/metrics.md`.
