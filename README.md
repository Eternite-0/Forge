# FORGE v4 — Persistent Router Agent System

FORGE is a self-built, general-purpose autonomous Agent desktop application.

## Engineering status — 2026-11-15

Milestones 1–4 provide persistence, governance, a Provider-backed Router, a model registry, Quest DAGs and durable events. Milestone 5 adds the native Worker Loop, approved dynamic teams, governed tools and independent execution evidence. Milestone 6 adds bounded context archives, sourced recall and unified model-call budget admission; its final local suite passed 372 tests and one bounded real-provider continuation succeeded after five recorded diagnostic failures. See the [M5 report](docs/PHASE1_MILESTONE5_REPORT.md) and [M6 report](docs/PHASE1_MILESTONE6_REPORT.md). Milestone 7 remains in progress; its [backend hardening round](docs/PHASE1_MILESTONE7_BACKEND_HARDENING.md) passed 488 local tests, while desktop acceptance is deferred and final integration and packaging remain to be verified.

See [current status](docs/STATUS.md), the [Phase 0 audit report](docs/PHASE0_AUDIT.md), and the report's dependency-ordered development plan. The release references `codex/MASTER_PROMPT.md`, but that file is missing from the supplied archive.

**The user has one persistent conversation.** A persistent Router Agent owns that conversation and coordinates multiple long-running Quests and hidden Worker Sessions. Workers have isolated execution contexts, but they are never presented as separate user conversations.

## Product invariants

1. One persistent user-facing conversation per workspace.
2. One persistent Router identity and resumable state per workspace.
3. Multiple durable Quests can run behind that conversation.
4. Worker Sessions are private execution contexts, not user-facing chats.
5. Worker events are selectively relayed into the single conversation timeline.
6. Model Provider, Model Profile, Role Template, Agent Instance, Quest and Session are separate domain entities.
7. Model Registry uses provider metadata, FORGE benchmark results, online performance and explicit user overrides.
8. Team composition combines reusable role templates with bounded dynamic role synthesis.
9. FORGE owns its Agent Loop, Router, Worker Runtime, Tool Engine, Memory, Governance and Evidence.
10. Codex, DSH and Claude Code are architectural references only, never execution backends.

## What this release contains

- Typed Python domain layer and repository interfaces.
- SQLite persistence with schema versioning and transaction boundaries.
- Persistent `main` conversation and Router identity.
- Quest lifecycle state machine and hidden Worker Session records.
- Durable event log with sequence cursor.
- Model Registry and explainable capability-fit team composition baseline.
- Provider gateway and explicit unconfigured provider; Mock protocol tests are labelled.
- FastAPI API with input validation and consistent error handling.
- React single-conversation UI with background Quest panel.
- Automated tests for persistence, lifecycle, router classification and team composition.
- Architecture, threat model, ADRs, detailed roadmap and Codex execution prompts.

## Important maturity statement

This is an engineering foundation with bounded, locally tested context recovery and recall. The Router uses a configured Provider or explicitly fails. Mock protocol tests, synthetic experiments and real-provider demonstrations are identified separately. User history and active Quest counts remain bounded; large histories can fail closed, and general task correctness and final desktop integration remain open. A proposal record alone never proves execution.

## Backend setup

The model-driven Router requires explicit provider configuration. Set the process environment
variables `FORGE_PROVIDER_ID`, `FORGE_PROVIDER_KIND` (for example `openai_compatible`),
`FORGE_PROVIDER_BASE_URL`, `FORGE_PROVIDER_CREDENTIAL_REF` (for example
`FORGE_PROVIDER_KEY_EXAMPLE`), `FORGE_ROUTER_MODEL_ID`, and
`FORGE_ROUTER_CONTEXT_LIMIT` (an operator-supplied maximum context window). Put the actual
credential only in the process environment variable named by the credential reference. Set
`FORGE_PROVIDER_SUPPORTS_TOOLS=true` and `FORGE_PROVIDER_SUPPORTS_JSON_SCHEMA=true` only when
the provider supports them. Without configuration the Core starts but Router message calls fail
explicitly; no keyword or mock provider is selected automatically. The optional `request_id`
in a message request allows safe retry: reuse it only for the same input.
For live calls, register an enabled model with the same provider and model ID and explicitly set
both `tool_calling_support` and `structured_output_support` to supported. Unknown capabilities
are rejected. Values submitted through the model API are recorded as user overrides.

The Router reads a bounded snapshot and calls the provider outside the database write
transaction. A successful decision and its messages commit together; provider errors, invalid
decisions, or budget failures leave no messages or Quest changes for that turn. The caller can
retry with the same request ID. The context budget uses a conservative UTF-8 byte estimate,
reserves output and request overhead, and also respects a known registry context window. Tool
access is limited to read-only Core queries. Worker execution separately requires approved plan/team contracts and governed tool permissions.

Python 3.11+ recommended.

```bash
cd backend
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate
python -m pip install --require-hashes -r requirements-lock.txt
python -m pip install --no-deps --no-build-isolation -e .
pytest
python -m forge_api
```

The lock file is verified on Windows with Python 3.13; other platforms and Python versions have not been verified. The editable installation requires build tooling already available. API documentation endpoints are disabled.

### Core authentication and development bootstrap

Core requires a fresh cryptographically random 32-byte process secret; `.env` is explicitly rejected. For manual browser development, supply it as 64 hex characters in `FORGE_CORE_SECRET` using a protected input method and enter the same secret into the UI's password field. Never place it in command history, source files, URLs, logs, or persistent browser storage. In the native desktop integration, the parent supplies the secret through a private pipe and the UI receives the derived session bearer in memory. Restarting Core invalidates old bearer tokens. Native integration is still undergoing final verification.

The unauthenticated public loopback `/health` returns only liveness and a random per-process session challenge; an authenticated request also receives Router identity and background status. A trusted client computes the lowercase hex HMAC-SHA256 over the UTF-8 challenge using the decoded 32-byte secret, and sends `Authorization: Bearer <digest>` on API requests. Requests without an Origin are allowed only with authentication and a loopback peer and Host; browser requests must also use an explicitly allowed Origin. These boundaries protect against unauthorized webpages and other untrusted clients; they do not protect secrets from an attacker who can read the same OS user's process memory. Keep the development server and any allowed origin trusted.

The authenticated user API supports `POST /api/approvals`, `GET /api/approvals?quest_id=...`, `GET /api/approvals/{id}`, and POSTs to `/{id}/decision` or `/{id}/revoke`. Workspace grants use `GET`/`POST /api/quests/{id}/grants` and `POST /api/quests/{id}/grants/revoke`. Decisions and grants record the authenticated local user, not a caller-supplied actor. The bearer represents that user; it must never be exposed to a model or registered as an Agent tool. These routes do not execute actions or accept trusted evidence. The M7 desktop approval and scope controls are under integration testing.

### Workspace and Settings

`python -m forge_api` is the shared Core launcher. From the repository root on Windows, `./scripts/run-backend.ps1` sets up the virtual environment and uses that launcher without shell activation. Pass `-WorkspaceRoot 'D:\YourWorkspace'` to the script, or `python -m forge_api --workspace-root 'D:\YourWorkspace'` directly. Workspace roots must be absolute paths.

The default Workspace is independent of the checkout, installation and shell directory:

| OS | Default Workspace root |
| --- | --- |
| Windows | `%LOCALAPPDATA%/FORGE/workspaces/default` |
| macOS | `~/Library/Application Support/FORGE/workspaces/default` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/forge/workspaces/default` |

The database defaults to `<WorkspaceRoot>/data/forge.sqlite3`. Settings are loaded as follows:

1. Choose the root using `--workspace-root`, then the process variable `FORGE_WORKSPACE_ROOT`, then the OS default (in that order). A root in `.env` is rejected because the root determines which file to read.
2. Load `<WorkspaceRoot>/.env` if present. `FORGE_ENV_FILE` in the process environment can select another file; relative file names resolve from the root. An explicitly selected missing file is an error. The shell directory's `.env` is ignored. Copy `backend/.env.example` to the selected Workspace root.
3. Process variables override file values. Supported settings are `FORGE_WORKSPACE_ID`, `FORGE_DB_PATH`, `FORGE_HOST` and `FORGE_PORT`; empty values and invalid ports fail startup. `.env` does not expand variables or modify the process environment.
4. Resolve relative database paths from the root and normalize them to absolute paths. The default Workspace ID is a hash of the normalized root; an explicit stable ID supports moving a Workspace. A database already bound to another ID is rejected.

The CLI and desktop Core bootstrap use the same Workspace settings and a new process secret. Direct `uvicorn forge_api.main:app` requires the same secret and loopback binding; prefer the shared launcher.

After a workspace restore, a restore fence blocks writes until an offline review succeeds. Close the desktop and Core, inspect the restored workspace and rollback directory, then use the packaged Python executable with the exact manifest hash and rollback directory shown by the application (replace every placeholder):

```powershell
& 'C:\path\to\packaged\python.exe' -I -m forge_api.maintenance review-restore --workspace-root 'C:\path\to\workspace' --manifest-sha256 '<64-lowercase-hex>' --rollback-directory '.forge-previous-<32-hex>' --reason '<10+ character review reason>'
```

The review validates the source hash, rollback directory name, reason, historical receipts and budget state; unresolved effects or reservations cause it to fail closed. Acknowledging an unknown native operation is a separate action and does not clear the restore fence or establish that the operation succeeded.

Legacy databases are **not discovered or moved automatically**. If an earlier version used `backend/data/forge.sqlite3` or another CWD-relative location, stop its Core process, back up the database with SQLite's backup API (or copy the database and WAL files together while stopped), and set `FORGE_DB_PATH` to its absolute path. The first successful open migrates it in place to the current schema without changing the Router ID or existing records. Migration failure rolls back the entire pending chain; unknown future versions and ambiguous version metadata are rejected. Downgrades are unsupported.

Events and pending Outbox rows commit in the same transaction as their associated business changes. Durable per-consumer delivery uses leases, fencing, bounded retries, Inbox deduplication and dead-letter/replay records. Historical events migrate as pending. External tool effects require their own durable intents and receipts; event delivery alone does not make external effects exactly once.

### Approved Worker execution

Workers consume an approved Quest DAG and a separately approved team. Tool scopes, provider/model identity and token/tool/time/concurrency budgets are bound to those contracts. File writes additionally require a workspace grant; HTTPS/MCP actions require exact action approval and an operator allowlist. `FORGE_TOOL_NETWORK_HOSTS_JSON` is a JSON host list (at most 64); `FORGE_MCP_SERVERS_JSON` maps server names to `{"url":"https://example.invalid/mcp","tools":["example_tool"]}` (at most 16 servers), with the HTTPS host included in the allowlist. Defaults are empty and deny network access. These values are operator configuration, never model output. `jsonschema` validates Worker result contracts; it does not judge domain correctness. Help/reassignment produces a pending team proposal for authenticated approval. An uncertain action cannot be silently retried or discarded during replacement. Unsupported or failed fixed JSON criteria require manual review; infrastructure verification retries are bounded to three attempts.

An approved dependent Worker may read a bounded receipt from a successful direct
predecessor only when its plan node, approved role and effective Worker tool
scope all permit `worker_read_action_receipt`. Its initial context contains at
most 32 upstream references and an omission indicator; a reference is not proof
until the read checks the current plan and receipt provenance. Returned content
remains untrusted and retains the source execution mode (`live` or `mock`); a
mock receipt is not promoted to live evidence. Completion rechecks the recorded
tool result. Temporary capacity pressure queues work without treating it as a
semantic failure retry. Lease generations fence ownership; the separate bounded
failure-retry count is recorded by retry events, and authorization retries do
not consume that failure limit.

Model calls through the Gateway share a durable admission ledger, including Router, planner, team composer, benchmark and Worker calls. Configure these trusted process environment variables before enabling a provider:

| Variable | Meaning |
| --- | --- |
| `FORGE_MODEL_TOKEN_LIMIT` | Total token reservation ceiling; defaults to `0`, which rejects model calls until explicitly configured. |
| `FORGE_MODEL_CONCURRENCY_LIMIT` | Concurrent admitted attempts; defaults to `1`. |
| `FORGE_MODEL_REMOTE_MAX_SECONDS` | Optional trusted upper bound on remote execution duration. Without one, an attempt with unknown outcome retains its concurrency slot; a client timeout alone cannot establish that remote execution stopped. |
| `FORGE_MODEL_SCOPE_LIMITS_JSON` | Optional array of `{"scope":"quest","scope_id":"<quest-id>","token_limit":10000,"concurrency_limit":1}` entries; scope may be `workspace`, `quest`, or `worker`. Approved task contracts also impose their own limits. |
| `FORGE_PRICE_BOUNDS_JSON` | Optional operator-provided provider/model price ceilings. Unknown prices remain unknown; token-only admission applies when no cost ceiling is configured. |
| `FORGE_MODEL_COST_LIMIT_MICROUNITS`, `FORGE_MODEL_COST_CURRENCY` | Optional global currency ceiling and three-letter currency; both require trusted price bounds and reconciled historical cost. |
| `FORGE_MODEL_HISTORICAL_COST_MICROUNITS` | Operator-reconciled pre-cutover global cost in the configured currency. Unknown historical cost is never assumed to be zero. |
| `FORGE_MODEL_SCOPE_COST_RECONCILIATIONS_JSON` | Optional array of `{"scope":"quest","scope_id":"<quest-id>","currency":"USD","cost_microunits":0}` entries for operator-reconciled historical scope cost. |

An unknown or interrupted remote outcome remains reserved until a trusted remote execution bound and isolation period permit recovery. Without a trusted bound the slot remains reserved; there is currently no operator interface for reconciling an individual unknown attempt. Do not infer that a timeout cancelled the remote request. Current real-provider demonstrations and reproducible research scripts are described in the [M5 report](docs/PHASE1_MILESTONE5_REPORT.md).

### Backend checks

```powershell
# From backend, after installing .[dev]
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts --config pyproject.toml
.\.venv\Scripts\python.exe -m ruff format --check src tests scripts
.\.venv\Scripts\python.exe -m pip check
```

`python-dotenv` is an explicit runtime dependency for Workspace configuration. No model provider or external Agent execution backend is required for these checks.

## Desktop UI setup

Node 22.18+ is required for the desktop test runner's native TypeScript stripping;
the current Windows checks use Node 24.19.0.

```bash
cd desktop
npm install
npm test
npm run build
npm run dev
```

The React/Vite UI can run in browser mode for development. The Tauri shell and Core bootstrap are being integrated; a final native integration and packaging pass is required before calling it a desktop release.

## API quick smoke test

```bash
curl http://127.0.0.1:8765/health
# Authenticated requests require the per-session bearer described above.
```

## Start Codex

### Entire session checkpoints

This repository uses Entire CLI 0.13.4 with the ref-based checkpoint backend
and telemetry disabled. On Windows, install the stable CLI using the [official
PowerShell installation instructions](https://docs.entire.com/installation),
then restart the terminal if the installer updates PATH. The non-Scoop default
installation location is `%USERPROFILE%\.local\bin`; a custom installation can
use any suitable directory on PATH. Verify with `entire version`.

Enable Entire in the repository with `entire enable` and install the Codex agent
hooks as directed by the [official agent setup](https://docs.entire.com/agents/setup).
Codex must support hooks and any applicable hook-trust approval must be granted
before hooks run. Enabling without history import begins tracking new sessions;
it does not import previous conversations. With this repository's ref backend
and automatic push policy, checkpoint refs are pushed with Git pushes. The
shared settings retain Betterleaks' default scanner and add a generic 32-hex
pattern plus email and phone redaction. Redaction has limits: never put provider
credentials or other secrets into chat; use the configured credential store or
protected environment instead.

The locally verified Codex 0.139.0 supports six of the seven hooks generated by
Entire 0.13.4, and those six have been trusted. Its `SessionEnd` event is not
supported by this Codex version, so Entire may still report one hook needing
approval. The official seven-hook template is retained; after upgrading to a
Codex version that supports this event, review hook trust again via `/hooks`.
This limitation does not prevent the six supported hooks or Git hooks from
working.

Open this folder as the project root and first read:

1. `docs/PRODUCT_ARCHITECTURE.md`
2. `docs/DOMAIN_AND_LIFECYCLE.md`
3. `docs/ROUTER_AND_WORKER_PROTOCOL.md`
4. `docs/MODEL_REGISTRY_AND_TEAM_COMPOSITION.md`
5. `docs/CONTEXT_MEMORY_GOVERNANCE.md`
6. `docs/ROADMAP.md`
7. `codex/MASTER_PROMPT.md`

Follow the user's continuous development authorization and the acceptance gates in `docs/STATUS.md`. The referenced `codex/MASTER_PROMPT.md` remains missing from the supplied release.

## Native desktop build (after Rust toolchain installation)

```bash
cd desktop
npm install
npm run tauri dev
```

The Tauri Rust shell and v2 capability manifest are included. Preliminary native startup has been observed on Windows; final target-OS integration and packaging checks remain pending.
