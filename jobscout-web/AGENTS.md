# JobScout standalone client

This directory uses plain JavaScript, HTML and CSS; no frontend build is required.
`runtime-config.js` may provide public `window.JOBSCOUT_CONFIG` addresses only.
Default to same-origin API requests. Preserve legacy split-origin behavior only
for HTTP localhost/127.0.0.1/[::1] on port 5500; an explicitly empty gatewayBase
always means same origin. Validate configured protocols and reject credentials.
The Capability Center link must use the deployment address, never a hardcoded
localhost URL for remote visitors.

`style.css` opens with the design tokens and every rule reads from them: surface
and ink colours, `--texture-dots*` surface lattices, the four `--shadow-*`
elevation steps, `--ring-*` focus glows, `--radius-*`, `--font-mono`, and the
motion vocabulary (`--ease-out` / `--ease-spring` / `--ease-in-out` with
`--dur-1`..`--dur-4`). Do not hardcode a shadow, radius, easing curve or
duration in a rule, and do not append an override block at the end of the
file — a declaration belongs in the rule it modifies, or the same property ends
up defined in two places that silently disagree.

`--font-mono` marks values the app *read* — counts, dates, elapsed times,
confidences, state labels, filenames, system labels — as against prose the app
wrote. Set `font-variant-numeric: tabular-nums` with it so a number does not
reflow as it updates. The stack is OS-resident on purpose: this client has no
build step and must not pull a webfont. Beware specificity when restyling a
field: the generic `input[type="text"]` rule is `(0,1,1)` and silently outranks
a bare class, so component rules for inputs are written `input.the-class`.

`@media print` at the end of the file is load-bearing, not decoration:
`openPrintWindow` opens a popup that links this same stylesheet, so the app's
canvas tint, elevation and screen chrome all reach the printed report unless
reset there. Check a report in print emulation after touching `.report-doc`,
body background or elevation.

Task-mode switching runs through `withViewTransition` in `app.js`. Its callback
must stay synchronous and must not return a promise, or the browser holds the
frozen frame for that work; callers must not assume the DOM changed by the time
it returns. Always attach handlers to the returned transition's promises — a
second switch skips the first and its rejected `ready` surfaces as a console
error that `test_ui.py` fails on. The transition marks
`document.documentElement.dataset.viewTransition` while it runs; screenshot
tests call `settle(page)` first, because Playwright's `animations="disabled"`
does not reach view-transition pseudo-elements and a mid-transition capture
blends two frames. New motion must stay inside the
`prefers-reduced-motion: reduce` block's reach — note it names `::backdrop`
explicitly, since `*` does not match it.

Read `/api/v1/auth/setup-status` before presenting an unauthenticated form. A new
instance uses `/initialize`; initialized instances obey `registration_enabled`.
Unavailable/invalid policy must not expose registration or submit credentials to
an assumed endpoint. Connection retry must work without filling required fields.

All browser tests intercept requests and use synthetic data. Run `test_pure.js`,
`test_deployment.py`, `test_ui.py`, `test_history_scroll.py`, and `test_concurrency.py`
with the backend Playwright environment. Keep runtime-config.js in their asset
fixtures. Live research and deployment smoke are separate, explicit operations.

Load core.js, api-client.js and tracker-state.js before app.js; serve them in browser
fixtures and Nginx. Tracker uses cursor pages (50 rows), server global counts and
stage filters; CSV/batch remain complete. Failed navigation restores its committed
cursor/filter. Session/request ownership guards must still reject late responses.
Batch result notices use the latest row_completed payload, even off-page; no-result
batches must not reuse a historical row. Keep this in test_concurrency.py.

CSV import uses the visible button/file input and existing CSRF-aware API helper;
keep its browser test and busy-state behavior. JobScout regression CI runs all five
standalone test entrypoints, with every browser request intercepted.

Generic progress (`进行中·具体阶段未知`) belongs in review counts/filters, never
asserts résumé screening, and has no inferred stage duration. Keep manual stage
behavior and server/client count parity. `trackerEvidenceLabel` replaces numeric
confidence percentages for portal and mail displays. Matching history, live
rendering, print and download additionally require the server's
`match_contract_version=1`; older reports prompt regeneration. Preserve explicit
zero-recommendation results and distinguish evidence scores from probabilities.

`trackerRecovery` uses stored portal outcomes even when mail supplies the displayed
stage. Recovery buttons reuse single refresh and same-record URL PATCH; never retry
automatically or create a duplicate application. Link edits validate HTTP(S), reject
credentials, preserve failures for retry, and fence late replies by session and dialog
version. Keep terminal checks disabled. Test desktop/mobile, stale replies, report
comparisons, Markdown download and print with intercepted requests in `test_ui.py`.
Version the stylesheet URL when CSS changes so existing sessions receive it;
the print window must reuse that same stylesheet URL.

`workbench.js` owns pending items, report-version prep notes and feedback UI. Load
it before app.js and include it in Docker/fixture assets. Also run `test_workbench.py`
and keep it in regression CI: production temporary-SQLite persistence, synthetic
reports, lost-response retries, mobile and logout fencing. Never attach prep forms
to ordinary chat bubbles. Bind report notes to a SHA-256 of sanitized Markdown
(HTTPS/localhost Web Crypto); do not treat that digest as evidence verification.
Keep notes out of report print/download. Failed writes retain inputs; stale reads
and writes must not restore content after logout, navigation or dialog replacement.
Scope workbench button styles explicitly; legacy tracker button selectors do not
cover the new pending/feedback/preparation containers. Check the deployed screenshot.
