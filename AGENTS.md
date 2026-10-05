# AGENTS.md

This file provides guidance to AI coding agents (Claude Code, Codex, and others) when working with code in this repository. It is the source of truth; the sibling `CLAUDE.md` imports it via `@AGENTS.md`.

It is the **monorepo orientation layer**: it maps the whole repo and points to the
module guides that own the depth. For anything inside a module, read that module's
guide rather than expecting full detail here:

- **[backend/AGENTS.md](backend/AGENTS.md)** — backend depth: harness/app split, agent &
  middleware chain, sandbox, MCP, skills, memory, IM channels, persistence/migrations,
  config system, test layout.
- **[frontend/AGENTS.md](frontend/AGENTS.md)** — frontend depth: Next.js App Router layout,
  thread/streaming data flow, code style, commands.

## JobScout deployment entry

This fork's public entry is JobScout. `python scripts/jobscout.py init|doctor|up|down|status|logs|backup|restore`
drives `docker/docker-compose.jobscout.yaml` with an isolated, gitignored `.jobscout/`
configuration directory. Keep root local development configs untouched. Nginx serves the
standalone UI at `/` and retains the upstream Next.js Capability Center on the same origin.
Gateway runs one worker; `/data` persists on a named volume. Public registration, mail and
scheduling are off in this preset. Optional noVNC is owner-only, published on loopback and
accessed remotely through SSH, never through the application's public proxy.
See `jobscout-web/AGENTS.md`, `docs/jobscout/deployment.md`, and the deployment CI.

## What is DeerFlow

DeerFlow is a LangGraph-based AI super-agent system with a full-stack architecture. The
backend runs a "super agent" with sandboxed execution, persistent memory, subagent
delegation, and extensible tools (built-in, MCP, community), all per-thread isolated. The
frontend is a Next.js chat UI. External IM platforms (Feishu, Slack, Telegram, Discord,
DingTalk) bridge into the same agent through the Gateway.

## Service Topology

A single `make dev` / Docker stack runs four cooperating services:

| Service         | Port   | Role                                                                 |
| --------------- | ------ | ------------------------------------------------------------------- |
| **Nginx**       | `2026` | Unified reverse-proxy entry point — open this in the browser        |
| **Gateway API** | `8001` | FastAPI REST API + embedded LangGraph-compatible agent runtime      |
| **Frontend**    | `3000` | Next.js web interface                                               |
| **Provisioner** | `8002` | Optional — only when sandbox is configured for provisioner/K8s mode |

Nginx is the single public entry: it proxies `/api/*` to the Gateway, rewriting
`/api/langgraph/*` onto the Gateway's native routes, and serves the frontend — see
[backend/AGENTS.md](backend/AGENTS.md) for the runtime and router detail. It compresses
HTML and configured textual assets, deliberately leaving SSE, fonts, images, audio, and
video uncompressed at the proxy layer.

Both compose files publish that entry as `"${BIND_HOST:-127.0.0.1}:${PORT:-2026}:2026"`
— **loopback by default**, matching the README's documented deployment model; a bare
`"${PORT}:2026"` binds `0.0.0.0`, which does not. The root `PORT` value is Docker ingress
configuration only; local orchestration pins Next.js to `3000` so loading `.env` cannot
make `make dev` wait on the wrong port. Nginx listening `default_server` on IPv4+IPv6 and
the Gateway binding `0.0.0.0:8001` are container-internal on purpose: the published nginx
port is the entire external surface. Any new published port needs an explicit bind
address; `backend/tests/test_compose_default_bind_host.py` pins this for every service in
both compose files.

## Repository Map

- `backend/`: `app/` owns Gateway and JobScout; `packages/harness/` owns the reusable Agent runtime; `packages/extension-api/` is the public extension contract.
- `jobscout-web/`: standalone JobScout client; `frontend/`: upstream Next.js Capability Center and client.
- `docker/`, `deploy/`, `scripts/`: deployment and orchestration.
- `skills/public/` is committed; `skills/custom/` is ignored. Managed integration skills are global under `.deer-flow/integrations/skills/{provider}/`; credentials/enabled state remain per-user.
- `contracts/`: shared JSON contracts; `docs/`: guides; `tests/`: public skill tests; backend and frontend own their test suites.

Third-party extensions are loaded from a top-level `plugins:` list in `config.yaml`
(operator-controlled on purpose — that list causes code to be imported, so it is deliberately
kept out of the API-writable `extensions_config.json`). Packaged extensions can contribute
middleware, task lifecycle, system-model observers, Gateway services, and FastAPI HTTP
routers; the [reference extension](examples/deerflow-extension-example/) demonstrates all
five. Manage them with `deerflow extensions install/upgrade/list/enable/disable/remove` or the root
`make extension-*` wrappers. Every mutation requires a Gateway restart, and both build
hooks and extension code execute with Gateway privileges, so only trusted operator sources
belong in this path. The manager transaction, accepted source forms, lock discipline, and
contribution contract live in
[the extensions guide](backend/packages/harness/deerflow/extensions/AGENTS.md).

Runtime config lives at the **repo root**: copy `config.example.yaml` → `config.yaml`
(main app config) and `extensions_config.example.json` → `extensions_config.json` (MCP
servers + skills). Both real files are gitignored and may be edited at runtime via the
Gateway API. Config schema and resolution order are documented in
[backend/AGENTS.md](backend/AGENTS.md).

Skill quality review note:
- `skills/public/skill-reviewer/` is the built-in read-only skill quality reviewer.
  It uses the harness-layer `review_skill_package` tool and contracts in
  `contracts/skill_review/`. Model-visible review data is compact and
  tag-neutralized; full raw payloads stay in tool artifacts. See
  [backend/AGENTS.md](backend/AGENTS.md) for the non-activation, SkillScan, and
  `skill-creator` ownership boundaries.
- CI waivers live in `.github/skill-review-waivers.v1.json` and are enforced by
  `scripts/review_changed_public_skills.py`. Pull requests may validate waiver
  edits from their head revision, but only the manifest from the trusted base
  revision can suppress that run. Entries match one error finding exactly,
  include the reviewed file's SHA-256 and an expiry date, remain visible in CI
  output, and can never waive blocker findings. An entry may also preapprove
  future full-file SHA-256 values, effective only once the manifest change lands
  in the trusted base — so relying on a waiver takes two merges: the manifest
  first, the skill change after, then promote the consumed hash to `file_sha256`
  in a follow-up cleanup.

Scheduled-task note:
- The scheduled-task MVP adds a workspace page at `/workspace/scheduled-tasks` plus a background scheduler service gated by `config.yaml -> scheduler.enabled`.
- Scheduled background runs are intentionally non-interactive: the lead-agent toolset excludes `ask_clarification` when `context.non_interactive=true`. That key, `disable_clarification`, and `github_token` are honored only for internally-authenticated callers; client-supplied copies are dropped from both `body.context` and `body.config`.
- Busy scheduled occurrences are persisted as `queued`; `launching` is a short lease-fenced claim, `running` remains the normal Gateway run lifecycle, and `scheduler.queue_timeout_seconds` bounds the durable wait. Do not reintroduce skip-on-overlap or count waiting rows against `max_concurrent_runs`.

## Commands: Root vs. Module

Run root `make help` for orchestration. First local setup is `make config`, then
`make install`, then `make dev`; dev never generates configuration implicitly.
Root `make setup` is the wizard, `make doctor` diagnoses setup, and
`make support-bundle` produces redacted diagnostics. `make start` uses optimized
local services (`SKIP_FRONTEND_BUILD=1` reuses a build); `make stop` stops them.
`make up/down` controls production Docker; `make docker-start/docker-stop/docker-logs`
controls development Docker. Startup uses the built environment with `uv run
--no-sync`, waits for health, and prints status/logs on failure. Docker lifecycle
commands resolve `DEER_FLOW_ROOT` from the current checkout.

Backend commands run inside `backend/`: `make test`, `make test-blocking-io`,
`make lint`, `make format`, or `python -m pytest tests/<file>.py -q`.
Frontend commands run inside `frontend/`: `pnpm dev`, `pnpm check`, `pnpm test`.
Host pnpm must go through `scripts/pnpm.py`: Windows tries `pnpm.cmd` first,
POSIX tries `pnpm` first, then the corresponding Corepack fallback. It uses the
frontend working directory and pinned package manager.

Gateway runs on port 8001; direct Next.js development uses port 3000. Docker logs
come from Compose; local logs appear in each service terminal. See module guides
for complete commands, runtime configuration and test contracts.

## Where to Go Next

- Backend work → **[backend/AGENTS.md](backend/AGENTS.md)**
- Frontend work → **[frontend/AGENTS.md](frontend/AGENTS.md)**
- Setup & install → **[Install.md](Install.md)**, **[CONTRIBUTING.md](CONTRIBUTING.md)**
- Project overview & usage → **[README.md](README.md)** (translations: `README_zh.md`,
  `README_ja.md`, `README_fr.md`, `README_ru.md`)
- Security policy → **[SECURITY.md](SECURITY.md)**
- Changes → **[CHANGELOG.md](CHANGELOG.md)**
- Cutting a release → **[RELEASING.md](RELEASING.md)**

## Cross-Cutting Conventions

These apply repo-wide; module guides own the module-specific detail.

- **Documentation update policy** — keep docs in sync with code: update `README.md` for
  user-facing changes and the relevant `AGENTS.md` for development/architecture changes in
  the same change set.
- **Test-driven development** — features and bug fixes ship with tests. Backend tests live
  in `backend/tests/` (TDD is mandatory there; see [backend/AGENTS.md](backend/AGENTS.md));
  frontend tests live in `frontend/tests/`.
- **Format before pushing** — run `make format` (backend) / `pnpm check` (frontend). Backend
  CI enforces `ruff format --check`, so formatting must be clean before a push.
- **Skill text encoding** — treat `SKILL.md` and other textual skill resources as UTF-8;
  Python utilities that read or write them must pass `encoding="utf-8"` rather than
  relying on the platform locale.
- **Version sources must stay in lockstep** — a release version must match identically in
  `backend/pyproject.toml`, `frontend/package.json`, and `deploy/helm/deer-flow/Chart.yaml`
  (`version` + `appVersion`). Pushing a `v*` git tag triggers CI that runs
  `scripts/verify_versions.sh` and **blocks all publishing** if any source drifts. Before
  bumping a version, run `scripts/bump_version.sh <ver>` (aligns all four at once) and
  `scripts/verify_versions.sh <ver>` to catch drift early. See [RELEASING.md](RELEASING.md).
- **Don't edit `CLAUDE.md`** — it only contains `@AGENTS.md`. All agent guidance changes
  belong here in `AGENTS.md`; `CLAUDE.md` is a thin import shim.
