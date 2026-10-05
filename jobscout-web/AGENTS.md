# JobScout standalone client

This directory uses plain JavaScript, HTML and CSS; no frontend build is required.
`runtime-config.js` may provide public `window.JOBSCOUT_CONFIG` addresses only.
Default to same-origin API requests. Preserve legacy split-origin behavior only
for HTTP localhost/127.0.0.1/[::1] on port 5500; an explicitly empty gatewayBase
always means same origin. Validate configured protocols and reject credentials.
The Capability Center link must use the deployment address, never a hardcoded
localhost URL for remote visitors.

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

CSV import uses the visible button/file input and existing CSRF-aware API helper;
keep its browser test and busy-state behavior. JobScout regression CI runs all five
standalone test entrypoints, with every browser request intercepted.
