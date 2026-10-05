# Validation — Codex X Mode Bridge 0.2.4 candidate

Validated on 2026-10-03 UTC from current account release
`pluginrel_6ac138390f2081918b7d16f192fdf985` (v0.2.2).

## Reproduction and repair

Unmodified v0.2.2: 26 tests, 2 failures reproduced (stale MCP version expectation;
SSE poll reused a request key whose null claim is intentionally retry-stable).
Corrected v0.2.2 tests: 26/26 PASS. SSE context reads now provide the exact lease.
No authorization, recovery, or execution behavior was changed.

## v0.2.3 verification

- `python3 -m unittest discover -s tests -v`: 27/27 PASS.
- `python3 -m compileall -q bridge tests`: PASS.
- Version consistency regression checks portable/compatibility manifests,
  pyproject, MCP serverInfo, status, generated OpenAPI, OpenAPI template,
  and exact server source parity in the downloadable runtime bundle.
- Actual local stdio subprocess: initialize reports 0.2.3; exact 12-tool
  discovery; status, project listing, directory listing, and file reading PASS;
  2 resources and 2 prompts discovered. No mutation tools invoked in this smoke.
- Temporary smoke configuration, database, project and subprocess cleaned up.
- Bundled runtime rebuilt without bytecode/cache files.

## Evidence boundary

- Real Codex CLI/app-server: NOT RUN (integration suite uses fixtures).
- Public HTTPS MCP endpoint: NOT RUN.
- Live ChatGPT/Codex host connection and GPT Actions import: NOT RUN.
- Existing top-level bytecode/cache files cannot be deleted by the account
  overlay editor; omission does not remove them from the saved release.

Publication is release-guarded. Account source read-back is verified separately
from local runtime checks; saving a release does not deploy a remote bridge.

## v0.2.4 candidate verification (2026-10-04)

- Current source was downloaded from account release `pluginrel_6ac19901937c8191831f5d95a578a5f4` (v0.2.3); candidate source preserves its manifests, skills, connectors, icons, and other package files.
- `python3 -B -m unittest discover -s tests -v`: **30/30 PASS**.
- `PYTHONPYCACHEPREFIX=/tmp/codex-x-mode-v0.2.4-pycache python3 -m compileall -q bridge tests`: **PASS**.
- Three added project-read regressions PASS: file reads and directory listings remain bound to opened directory descriptors during a symlink swap; directory listing scans at most `limit + 1` entries.
- Version consistency test verifies every package/runtime version is 0.2.4 and bundled runtime source matches `server/` without bytecode/cache files.
- Local MCP stdio smoke reports version 0.2.4, discovers exactly 12 tools, 2 resources, and 2 prompts, and successfully calls status and project listing. Smoke config, database, project, and process were temporary and cleaned up.
- Real Codex CLI, public HTTPS MCP, and live GPT Actions integration: **NOT RUN**; `live_codex_verified` remains false.
- The current account archive contains existing `.pyc` cache files. The candidate archive omits caches, but Plugin Creator updates overlay files and cannot delete omitted paths; a later account update will need to account for those retained entries.


## v0.2.5 verification (2026-10-04)

Source: current account release pluginrel_6ac1eb5140e481919a61778795235240 (0.2.4).

- python3 -B -m unittest discover -s tests -v: 47/47 PASS, exit 0.
- 17 new client integration/failure tests use real loopback sockets and owned
  stdio subprocesses. HTTPS uses a temporary local certificate trusted through
  SSL_CERT_FILE; an untrusted certificate is rejected. No public TLS claim.
- Read-only CLI verified over HTTP, HTTPS and stdio; no task created by doctor.
- Auth roles, origin denial, cleartext/redirect refusal, response ID mismatch,
  timeout/process cleanup, all four protocol revisions, scope denial, task
  dedup/conflicts/cancellation/follow-up, unknown-state recovery and backend
  lease/chunking/completion/cancellation are covered.
- Dispatch end-to-end uses the explicit fake_app_server.py subprocess fixture.
  Its captured exit_code=1 remains 1 even though its reasoning turn completes.
- Python AST, JSON parsing and packaged skill YAML frontmatter: PASS.
- Version/bundle consistency is checked by the full suite.
- Real Codex CLI, public HTTPS deployment, live ChatGPT tool scan and Actions:
  NOT RUN. No actual endpoint or deployment host was supplied/discovered.
- Mobile custom MCP: BLOCKED by documented host availability (web only).
- Existing connector ID, plugin identity, scope, permissions and defaultPrompt
  are preserved; no remote endpoint or OAuth capability is invented.
- Download archive transfer returned 403; current text source was read through
  the source API instead. Unchanged binaries are preserved by overlay; the
  runtime download bundle was rebuilt from the current server source.

## v0.2.6 candidate verification (2026-10-04)

- Unknown executions no longer block all tasks in a project. Read-only tasks
  proceed; workspace-write is blocked only against an unknown possible writer
  on the same canonical resource. Project aliases, including legacy tasks
  without stored resource identities, resolve to a shared conflict identity.
- Status separates unknown task records from projects with unknown writer
  conflicts. Readiness fields report Codex, Web executor, model identity and
  end-to-end verification as unverified.
- Intent instructions route status, new tasks, continuation, cancellation and
  bound backend inference without asking the user to choose Backend/Dispatch.
- `python3 -B -m unittest` on the 24 offline-safe core, resource-locking,
  package-consistency, project-read-boundary and failure-handling tests:
  **24/24 PASS**.
- `PYTHONPYCACHEPREFIX=/tmp/codex-x-mode-0.2.6-pycache python3 -m compileall
  -q server/bridge server/tests`: **PASS**.
- MCP stdio smoke: protocol `2025-11-25`, 12 tools, status version `0.2.6`,
  project list PASS; no task created. `live_codex_verified=false` as expected.
- The full socket-based HTTP/HTTPS integration suite is not runnable here:
  socket creation is denied by the execution sandbox.
- Real Codex, actual Web model identity and deployed-service behavior remain
  unverified.

## v0.2.7 candidate verification (2026-10-04)

- Dispatch failures retain a bounded failure phase, exception class and whether
  a turn start may have occurred. Raw exception text, prompt content, paths and
  tokens are excluded. Pre-turn failures become `failed`; ambiguous turn-start
  or later failures remain `unknown` and are not replayed.
- `codex_x_list_models` and `GET /models` expose the Codex app-server model
  catalog for dispatch selection. Model IDs are exact; unsupported IDs and an
  ambiguous default fail before thread/turn start. The catalog does not prove
  model entitlement and does not control the ChatGPT Web conversation model.
- Completed dispatch model identity is verified only when the terminal event
  reports the exact selected model and no reroute. Missing/mismatched identity
  remains `unknown`; it is not replayed. Status marks the Web executor
  `not_implemented` and leaves model/end-to-end readiness unverified.
- Offline-safe core/resource/dispatch/project-boundary/recovery/version suite:
  **35/35 PASS**. This includes absent model identity, reroute, unsupported
  model, resource-scoped unknown lock, stable claim and project-path race tests.
- `PYTHONPYCACHEPREFIX=/tmp/codex-x-mode-v027-pycache python3 -m compileall
  -q server/bridge server/tests`: **PASS**.
- Local MCP stdio smoke: protocol `2025-11-25`, **13 tools**, **2 resources**,
  **2 prompts**, status version `0.2.7`. `codex_x_list_models` returned the
  named fake app-server catalog and `model_entitlement_verified=false`; no task
  was created. Temporary project/config/database/process were cleaned up.
- Version consistency verifies root/compatibility manifests, pyproject, MCP
  serverInfo, status, generated OpenAPI and exact runtime bundle parity; **PASS**.
- Socket-based HTTP/HTTPS integration tests: **NOT RUN** because sandbox socket
  creation is denied. A prior aggregate command intentionally encountered this
  denial in one HTTP test; it was excluded from the final offline-safe count.
- Real Codex CLI, ChatGPT-plan OAuth/SIWC, real entitlement, public HTTPS,
  ChatGPT Web tool scan and Web → Codex → Web: **NOT RUN**.
- Account plugin update is separate from hosted bridge deployment. At last
  live read the bridge was still `0.2.3`, `live_codex_verified=false`, and
  `unknown_projects=["smoke"]`.

## v0.2.8 verification (2026-10-05)

- `python3 -B -m unittest discover -s tests -v`: **94/94 PASS**, exit 0
  (16.092 seconds). These are local tests, not live OpenAI inference results.
- `PYTHONPYCACHEPREFIX=<temporary-cache> python3 -m compileall -q bridge
  tests scripts`: **PASS**, exit 0. Python AST, JSON, TOML, YAML, packaged skill
  frontmatter, manifest identity and preserved connector configuration: PASS.
- HTTP/HTTPS integration uses real loopback sockets. HTTPS verifies a temporary
  trusted local certificate and rejects an untrusted certificate. This runtime
  permits those socket tests; earlier v0.2.6/v0.2.7 socket restrictions are
  historical and do not describe this verification environment.
- SIWC tests cover actual locally generated RSA-signed ID tokens, PKCE/state/
  nonce callback checks, scopes, issuer/audience/account binding, rotation,
  malformed credentials, permissions and symlinks, protected import, stable
  host identity, and refresh serialization across threads and processes.
  OpenAI discovery, JWKS, token and model endpoints are mocked.
- Web routing, exact visible-model validation, persisted account/backend/model
  binding, idempotency before catalog access, concurrent deduplication,
  follow-up binding, queued-account/catalog changes, legacy unbound HTTP jobs,
  resource-scoped unknown-writer locks and token redaction: PASS. Dispatch uses
  an explicitly named fake Codex app-server subprocess fixture.
- CLI setup, stable repeated `siwc-host-id`, missing-authorization status and
  malformed protected import rejection: PASS. Local stdio MCP smoke reports
  version 0.2.8, **13 tools**, **2 resources**, **2 prompts**. Its model catalog
  uses the fake app-server and `model_entitlement_verified=false`. No task was
  created; owned subprocesses and temporary project/config/database were
  cleaned up.
- Runtime bundle is rebuilt deterministically from source, excludes bytecode,
  caches and credentials, and is checked for exact source/version parity.
- Provider arguments and child environment filtering are checked against the
  fixture. Real Codex CLI availability, ACCESS_TOKEN shell filtering, actual
  provider compatibility and exact-model inference remain **UNVERIFIED**.
- Live status on 2026-10-05 is **BLOCKED**: `tunnel_client_not_seen`, HTTP 404
  (tunnel client absent for 300 seconds). No live deployment, real OAuth login,
  entitlement check or Web-to-Codex-to-Web test was performed. The historical
  v0.2.3 `smoke` unknown execution has not been reconciled.
- Plugin publication and hosted runtime deployment are separate operations.
  Readiness remains `implemented_unverified` / `live_codex_verified=false`.
  Existing account-source bytecode and v0.2.6 ZIP cannot be deleted through the
  overlay update API; they are excluded from the new source/runtime bundles.

## v0.2.9 verification (2026-10-05)

- `python3 -m unittest discover -s tests`: **95/95 PASS**, exit 0 in the complete
  package layout. The extra regression covers Secure MCP Tunnel discovery GETs;
  macOS temporary project/credential fixtures now use canonical paths while the
  production non-canonical/symlink root guard remains unchanged.
- `tunnel-client doctor --profile codex-x-mode --explain`: **RESULT ok** on the
  target Mac. The MCP target is reachable, OAuth metadata is intentionally not
  advertised and all discovery candidates return HTTP 404.
- Managed tunnel runtime for `tunnel_6ac183e0dc2881918df4195ad20fe4b3`
  reported `process_running=true`, `healthy=true`, `ready=true`, and
  `runtime_state=ready`.
- Live ChatGPT MCP transport is verified for read-only control calls:
  `codex_x_status` returned service version 0.2.8 from the hotfixed deployed
  runtime and `codex_x_list_projects` returned project `codex-x-mode` with
  `allow_write=true`.
- Real ChatGPT-plan dispatch/inference is **NOT RUN**. Live status reports
  `chatgpt_plan_authorization.state=authorization_required`; SIWC authorization
  must complete before model listing or task dispatch can be accepted.
- `live_codex_verified` therefore remains false until a completed real Codex
  turn proves exact terminal model identity without reroute.

## v0.2.10 verification (2026-10-05)

- Live SIWC authorization on the target Mac reached `state=authorized` with
  verified scopes. The ChatGPT-plan model catalog was readable and included
  `gpt-6-astra`; catalog integrity was verified, while entitlement remained
  intentionally unverified until inference.
- The first authorized read-only dispatch was accepted but failed before
  inference at `thread_start` with `execution_may_have_started=false`.
  A bounded app-server diagnostic against Codex CLI 0.156.1 identified the
  protocol rejection: `thread/start.sandbox` now accepts `read-only`,
  `workspace-write`, or `danger-full-access`, not the legacy camel-case enum.
- The bridge now sends `read-only` / `workspace-write` for the thread-start
  field. The later `turn/start.sandboxPolicy` object remains unchanged because
  it is a distinct protocol type.
- The fake app-server regression rejects any other thread-start sandbox value.
  `python3 -m unittest tests.test_siwc_dispatch -v`: **13/13 PASS**.
- The runtime bundle was rebuilt from the patched source.
  `python3 -m unittest discover -s tests`: **95/95 PASS**, exit 0 (17.320 s)
  with loopback socket permission. `python3 -m compileall -q bridge tests
  scripts`: **PASS**, exit 0.
- Real post-deploy inference is **NOT RUN in this package candidate**. The live
  acceptance criterion remains a completed terminal turn with exact observed
  model identity and no reroute.

## v0.2.11 verification (2026-10-05)

- The v0.2.10 live retry passed `thread/start` but Codex CLI 0.156.1 rejected
  `turn/start` before returning a turn id. Bridge state remained conservatively
  `unknown` because the call boundary had been crossed; that task was not
  replayed.
- A provider-isolated protocol diagnostic (localhost-only provider endpoint,
  dummy credential) captured the exact app-server rejection:
  `readOnly.access is no longer supported; use permissionProfile for restricted
  reads`.
- The thread already receives the explicit `read-only` or `workspace-write`
  sandbox at `thread/start`. v0.2.11 therefore stops sending the obsolete
  per-turn `sandboxPolicy` override; `turn/start` inherits the thread sandbox.
  The same provider-isolated diagnostic then returned an in-progress turn,
  proving the request shape is accepted without making a real inference.
- The fake app-server regression now requires the thread sandbox and rejects a
  redundant `turn/start.sandboxPolicy` field.
- `python3 -m unittest tests.test_siwc_dispatch -v`: **13/13 PASS**.
  `python3 -m unittest discover -s tests`: **95/95 PASS**, exit 0 (17.259 s)
  with loopback socket permission. `python3 -m compileall -q bridge tests
  scripts`: **PASS**, exit 0.
- Real post-deploy inference remains **NOT RUN in this package candidate** and
  still requires terminal exact-model evidence before `live_codex_verified`
  may become true.

## v0.2.12 package verification (2026-10-05)

- Web-origin acceptance now persists `execution_backend=codex_app_server` and
  `dispatch_origin=web`; new Web tasks do not use SIWC/`chatgpt_plan`. The old
  plan backend is retained only to reconcile persisted pre-v0.2.12 tasks.
- `codex_x_list_models` uses Native Codex `model/list`; Web-origin discovery is
  restricted to exact `chatgpt-web/*` IDs. Acceptance snapshots the exact model,
  supported/default reasoning efforts and selected effort. Execution revalidates
  that snapshot against an independent app-server catalog before `thread/start`.
- `turn/start` explicitly carries the accepted supported effort. The fixture Web
  model requires `high`, so an inherited/global incompatible effort such as `max`
  is covered by fail-closed regression tests.
- Exact-model verification remains fail-closed: a completed turn without terminal
  model identity or with a `model/rerouted` notification remains `unknown` and is
  not replayed as acceptance evidence.
- Focused Web/HTTP/app-server/client regression suite: **44/44 PASS**.
- `python3 -B -m compileall -q server/bridge server/tests server/scripts`: **PASS**.
- `python3 -B -m unittest discover -s tests -v`: **92/92 PASS**, exit 0
  (25.066 s) after rebuilding the deterministic runtime bundle.
- Runtime bundle SHA-256 after the first v0.2.12 source rebuild:
  `e606b7aed64868dba3209c1aa36e12c7a9babdb484508b56654235a549c4c20f`.
  The final release hash must be re-recorded after this validation text is bundled.
- **Not yet architecture acceptance:** the clean-install test must still prove the
  headless Native Codex route works without Chromium, Playwright, browser automation,
  a browser profile/daemon, or a second connector. Real Web → Bridge → Native Codex →
  ChatGPT Web exact-terminal-model acceptance is therefore still **NOT PROVEN**.

## v0.2.13 headless package/model-registry verification (2026-10-05)

- New Web tasks use product-facing backend `chatgpt_web_headless`; browser runtime is
  explicitly not required. Legacy `chatgpt_plan` remains only for persisted old-task
  reconciliation.
- Web model policy is package-owned in `bridge/web_models.json`. Visible aliases equal
  the intersection of the packaged registry and the authorized ChatGPT account catalog.
- Current packaged aliases are `chatgpt-web/5.5`, `chatgpt-web/5.6-luna`, and
  `chatgpt-web/5.6-sol`. Upstream account models outside that package policy remain hidden.
- Generic `chatgpt-web` uses an entitled configured default when available; otherwise
  it falls back deterministically to the highest-priority entitled packaged alias.
- Regression coverage proves Free-like fallback to Luna, paid/default Sol behavior,
  exact alias rejection, duplicate/retry stability, terminal model identity fail-closed,
  and future package addition of a Pro alias without changing routing core.
- Headless live read-only account-catalog probe returned packaged+entitled aliases
  `5.5`, `5.6-luna`, and `5.6-sol`; generic `chatgpt-web` resolved to
  `chatgpt-web/5.6-sol` -> `gpt-5.6-sol` with `browser_required=false`.
- Focused Web/model-policy suite: **12/12 PASS**.
- `python3 -B -m compileall -q server/bridge server/tests server/scripts`: **PASS**.
- `python3 -B -m unittest discover -s tests -v`: **94/94 PASS**, exit 0 after
  rebuilding the deterministic runtime bundle.
- Final runtime bundle SHA-256 is recorded in the external release evidence file `docs/evidence/2026-10-05-v0.2.13-headless.md`; it is intentionally not embedded here because this validation file is itself part of the bundle.
- Mobile uses the same remote MCP/tunnel path architecturally, but an actual ChatGPT
  Mobile app smoke has **NOT YET BEEN RUN** and must not be reported as verified.
- Full deployed Web terminal-task acceptance is also still **NOT PROVEN**; keep
  `live_codex_verified=false` until exact terminal-model evidence exists.
