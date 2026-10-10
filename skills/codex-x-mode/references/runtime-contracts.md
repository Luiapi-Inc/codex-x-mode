# Codex X Mode runtime contracts

The bundled MCP/bridge is the canonical runtime. Live tool schemas remain authoritative.

## Native MCP

Portable package: `mcp.json`. Local server: `python3 -m bridge mcp-stdio` with cwd `${PLUGIN_ROOT}/server`.

| Tool | Purpose | Mutation |
| --- | --- | --- |
| `codex_x_status` | Status and unknown-project blockers | No |
| `codex_x_list_projects` | Configured project IDs/write flags | No |
| `codex_x_list_models` | Native Codex model catalog and exact Web model IDs | No |
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

Web-origin and local dispatch use Native Codex app-server `model/list`.
Web-origin discovery exposes only exact `chatgpt-web/<version>` IDs whose
executable model equals the ID. A concrete version must follow the family
prefix; family-only IDs and unlisted models fail before queue acceptance.
Web reasoning effort must be present in the selected model's supported-effort
metadata and is sent explicitly. When model selection is omitted, the bridge
uses a valid configured exact Web default or a unique Native Codex account
default; it never falls back to another provider. Catalog listing does not
prove entitlement or control the ChatGPT conversation's model.

Web-originated Codex X App operations that start a turn apply the same exact
model contract. New threads and forks that start a turn resolve their
requested/default or parent model against Native Codex `model/list`, then send
the exact ID and supported effort.
Messages to an existing thread require that thread's model to remain an exact
listed Web model; idle turns send the ID and effort explicitly, while steering
keeps the active turn's already selected Native model. Local stdio App calls
retain local Native Codex model behavior.

The bridge records requested and selected model identity. A completed task is
accepted only when the terminal event reports that exact model without reroute.
An absent/mismatched identity leaves execution `unknown`; it is not retried
automatically. Backend and origin are fixed by the gateway. Retries recover the
original task before catalog refresh; follow-ups retain the parent's selection.
Persisted jobs naming retired `chatgpt_plan` or `chatgpt_web_headless` routes
fail before provider launch and must not be replayed through another route.
Status reports `readiness.web_executor = "implemented_unverified"` and keeps
live model/end-to-end verification unverified. Source tests use named fixtures.

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

`python3 -m bridge serve` binds loopback by default. `/mode/mcp` is the canonical authenticated unified MCP endpoint with 13 Core + 8 namespaced App tools. `/mcp` and `/app/mcp` remain authenticated compatibility endpoints. `/status`, `/projects`, project reads and `/tasks...` are GPT Actions. `/v1/...` is the private local Responses provider and must not be exposed via public reverse proxy.

A public web/mobile integration requires a user-controlled HTTPS deployment and explicit endpoint binding. No public URL is invented or embedded.

## Multipart transport

For an explicit inert staged part, emit only the required ACK and retain transaction identity/order. Do not execute until explicit commit of complete transaction. Echoing a declared digest is not digest verification.

## Evidence

Task acceptance is not completion. Command start is not PASS. Report exact IDs and actual evidence. Preserve failing command/test exit codes even if reasoning completes normally.

## Optional compatibility adapters

Other user-selected Codex connectors may be adapted, but are not required by this plugin. Do not silently substitute them when native runtime is unavailable.
