# Codex X Mode runtime contracts

The bundled MCP/bridge is the canonical runtime. Live tool schemas remain authoritative.

## Native MCP

Portable package: `mcp.json`. Local server: `python3 -m bridge mcp-stdio` with cwd `${PLUGIN_ROOT}/server`.

| Tool | Purpose | Mutation |
| --- | --- | --- |
| `codex_x_status` | Status and unknown-project blockers | No |
| `codex_x_list_projects` | Configured project IDs/write flags | No |
| `codex_x_list_models` | Active backend model catalog for exact dispatch selection | No |
| `codex_x_list_project_directory` | Safe project-relative directory listing | No |
| `codex_x_read_project_file` | Safe UTF-8 file read | No |
| `codex_x_create_task` | Create authorized task | Yes |
| `codex_x_read_task` | Read task state/evidence | No |
| `codex_x_continue_task` | Authorized follow-up | Yes |
| `codex_x_cancel_task` | Cancel/request cancellation | Yes |
| `codex_x_claim_backend_turn` | Claim pending model request | Yes, retry-safe |
| `codex_x_read_backend_context` | Read lease-bound backend context page | No |
| `codex_x_complete_backend_turn` | Complete model response | Yes, idempotent |
| `codex_x_cancel_backend_turn` | Cancel claimed backend turn | Yes, idempotent |

Resources: `codex-x://status`, `codex-x://projects`. Prompts: `codex-x-backend`, `codex-x-dispatch-read-only`.

## Dispatch model selection boundary

Local stdio `codex_x_list_models` uses Native Codex app-server `model/list`. Web-origin MCP and `GET /models` expose only the intersection of the packaged registry in `server/bridge/web_models.json` and the authorized ChatGPT account catalog from `https://api.openai.com/v1/models`. Treat account-catalog visibility as dispatch availability; do not infer a plan name or broader entitlement from it.

For Web-origin dispatch, `model_version` is either the `chatgpt-web` family alias or an exact packaged `chatgpt-web/<version>` alias. Generic `chatgpt-web` resolves to the configured default when it is packaged and visible, otherwise to the highest-priority packaged+visible alias. Explicit aliases outside the package registry or absent from the authorized catalog fail before queue acceptance. The bridge pins the corresponding underlying model slug.

New Web tasks run on `chatgpt_web_headless`; local stdio tasks run on `codex_app_server`. Clients cannot choose the execution backend. Immediately before `thread/start`, the worker uses Native Codex `model/list` to validate the exact underlying slug and supported reasoning effort. Completion counts as exact-model evidence only when the terminal event reports the same model without reroute; absent or mismatched identity remains fail-closed/`unknown` and is never retried automatically.

Retries recover the original accepted task before credential or catalog refresh. Follow-ups retain the original backend, account registration, and selected model. Before child launch, the worker revalidates the authorized account registration and model-catalog visibility. Legacy `chatgpt_plan` is reconciliation-only for persisted pre-v0.2.12 work and is never selected for new Web tasks.

Status keeps `readiness.web_executor = "implemented_unverified"` and `live_codex_verified=false` until a real deployed terminal task succeeds with exact model identity and no reroute.

HTTP uses MCP `2026-07-28`; stdio supports modern plus legacy handshake revisions. Modern HTTP validates protocol/method/name headers and uses a distinct Bearer `mcp_key`.

## Configuration

`python3 -m bridge setup --project <id> --cwd <absolute-existing-project-root>` creates mode-600 configuration. Default: `~/.config/codex-x-mode/bridge-private.json`; `CODEX_X_MODE_CONFIG` overrides. Keys are separated: `provider_key`, `gpt_key`, `mcp_key`. Never place keys or active leases in Instructions/Knowledge/generated user-visible files/chat.

## Project boundary

Every project is an explicit allowlist entry with an absolute canonical cwd and optional `allow_write`. Reads accept only project-relative paths and walk directories relative to opened file descriptors with symlink following disabled. File reads open a regular file before checking and enforcing the byte limit; directory scans inspect at most `limit + 1` entries. `workspace-write` is rejected unless explicitly allowed.

## Backend recovery

A provider request enters the backend queue with provider idempotency identity. Claim requires a stable `request_key`.

- First claim records the claim key **even when no turn is available**.
- A null result remains bound to that key. Retrying the same key later returns null and cannot claim a turn that arrived afterward.
- A successful claim changes one queued job to `claimed`, creates a private lease, and binds the key to that job.
- Retrying a successful claim key while active returns the same job and lease.
- Reusing a consumed/expired successful claim key conflicts instead of claiming another turn.
- Read context with `turn_id` **and its active lease**, following exact `next_offset` until null; missing/wrong lease fails closed.
- Completion requires lease + stable completion key; identical replay is idempotent and changed payload conflicts.
- Cancellation likewise requires active lease + stable cancellation key.

The null-claim ledger is fail-closed across ambiguous response loss. If process failure occurs between underlying claim and ledger update, retry does not take another turn; unresolved execution must expire/recover instead of risking identity substitution.

A model response may contain answer text and/or offered tool calls. The original Codex client executes returned calls under its sandbox/approval policy. Completing a model response is not proof that the outer coding task is complete.

## Dispatch recovery

Create/follow-up mutations use stable request keys. Same key + same payload returns the same job; same key + changed payload conflicts.

Lifecycle can include `queued`, `running`, `cancelling`, `completed`, `failed`, `cancelled`, `interrupted`, `unknown`. If restart/connection loss may have occurred after execution began, task becomes `unknown`; retain its ID and reconcile that execution. New read-only tasks may proceed because the server enforces the read-only sandbox. Block workspace-write tasks only when an unknown possible writer targets the same canonical resource. Legacy unknown tasks without scope are conservatively treated as possible writers. Retry of the original create key still returns the same task.

Cancellation: queued → `cancelled`; running → `cancelling` and owned process is terminated when observed; unknown → cancellation rejected until recovery.

## HTTP bridge

`python3 -m bridge serve` binds loopback by default. `/mcp` is authenticated modern MCP. `/status`, `/projects`, project reads, `/tasks...`, `/backend...` are GPT Actions. `/v1/...` is the private local Responses provider and must not be exposed via public reverse proxy.

A public web/mobile integration requires a user-controlled HTTPS deployment and explicit endpoint binding. No public URL is invented or embedded.

## Multipart transport

For an explicit inert staged part, emit only the required ACK and retain transaction identity/order. Do not execute until explicit commit of complete transaction. Echoing a declared digest is not digest verification.

## Evidence

Task acceptance is not completion. Command start is not PASS. Report exact IDs and actual evidence. Preserve failing command/test exit codes even if reasoning completes normally.

## Optional compatibility adapters

Other user-selected Codex connectors may be adapted, but are not required by this plugin. Do not silently substitute them when native runtime is unavailable.
