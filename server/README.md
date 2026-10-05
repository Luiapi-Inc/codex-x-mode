# Codex X Mode Bridge — self-contained runtime

Python 3.11+; standard library only. This runtime backs the bundled Codex X Mode plugin without requiring another plugin or skill.

It supports three surfaces on the same local state/config:

| Surface | Purpose | Auth / transport |
| --- | --- | --- |
| MCP stdio | Local plugin/Codex/Desktop hosts | local process, no network auth |
| MCP HTTP + GPT Actions | Web/mobile or Custom GPT through a user-controlled HTTPS reverse proxy | Bearer `mcp_key` / `gpt_key` |
| Responses provider | Local Codex backend turn relay | Bearer `provider_key`, keep private |

The bridge owns one Codex app-server subprocess per dispatched task. Web inference routing and actual model identity are not verified by this package alone; a runtime must not claim them from a requested model name or echoed response.

This remains a **prototype until live-verified with the user's actual Codex CLI and deployed HTTPS endpoint**. Existing full-suite evidence uses real loopback HTTP/SQLite plus explicit app-server protocol fixtures.

## Setup

```bash
python3 -m unittest discover -s tests -v
python3 -m bridge setup --project demo --cwd /absolute/path/to/repo
```

Default config path is `~/.config/codex-x-mode/bridge-private.json` with mode 600. `CODEX_X_MODE_CONFIG` or `--config` can override it. Setup generates distinct `gpt_key`, `provider_key`, and `mcp_key`; `workspace-write` remains disabled unless explicitly allowed for the project.

## Local MCP stdio

```bash
python3 -m bridge mcp-stdio
python3 -m bridge codex-x-app-mcp-stdio
```

The root `mcp.json` exposes two bundled MCP surfaces from this same runtime. `codex-x-mode` provides the 13 `codex_x_*` operations for status, model catalog, project reads, task lifecycle, and backend turn lifecycle; resources are `codex-x://status` and `codex-x://projects`, with prompts `codex-x-backend` and `codex-x-dispatch-read-only`. `codex-x-app` is a separate native-backed thread orchestration surface at `/codex-x-app/mcp` with 8 tools: list/read/create/fork threads, send/steer messages, set title/archive state, and wait for threads. Its protocol baseline is Codex CLI 0.160.1 and it does not depend on the external `codex-app-tools@openai-bundled` package.

Stdio supports MCP `2026-07-28` and legacy handshake revisions `2025-11-25`, `2025-06-18`, and `2025-03-26` for compatibility.

## HTTP MCP / Actions

HTTP-origin and local dispatch both execute through Native Codex app-server.
Web aliases are package policy mapped onto Native Codex `model/list`; Native Codex owns
authentication and inference. SIWC commands remain only for legacy persisted-task
reconciliation/optional tooling and are not required for new dispatch. See [SIWC.md](SIWC.md).

```bash
python3 -m bridge serve
```

Default bind: `127.0.0.1:8240`.

HTTP also supports stateless initialize/initialized compatibility for
2025-11-25, 2025-06-18 and 2025-03-26. Accepted notifications return HTTP 202
without a JSON-RPC response. Modern version/header matching stays enforced.
Origins are denied unless explicitly configured; origin-less server clients
continue to work.

## Bundled MCP client

`python3 -m bridge.client --config /absolute/private/config.json` checks stdio.
`python3 -m bridge.client --url https://ACTUAL-HOST/mcp` checks HTTP with
CODEX_X_MCP_TOKEN from the environment. Both commands perform only read-only
discovery/status checks. The Python MCPClient API also supports explicit tool,
resource and prompt operations, both protocol families, response ID validation,
bounded reads, timeout and owned-process cleanup. It refuses remote cleartext
and redirects and never retries a mutation automatically. This is a client
for this bridge's JSON/stdio surfaces, not a general SSE/subscription SDK.

See [WEB-CONNECTION.md](WEB-CONNECTION.md) for deployment and separate host,
authentication and mobile acceptance gates.

- `POST /mcp` — Codex X Mode MCP `2026-07-28`, Bearer `mcp_key`
- `POST /codex-x-app/mcp` — Codex X App MCP, Bearer `mcp_key`
- Actions routes (`/status`, `/projects`, project reads, `/tasks...`, `/backend...`) — Bearer `gpt_key`
- `/v1/...` — private Responses provider, Bearer `provider_key`

For web/mobile, reverse-proxy only intended MCP/Actions routes over HTTPS. Keep `/v1/` blocked publicly. This package does not invent or ship a public endpoint.

## Backend flow

Run Codex with Native Codex authentication/provider ownership. The wrapper no longer points Codex back at the bridge Responses provider:

```bash
python3 -m bridge codex-catalog
python3 -m bridge codex -- --sandbox read-only
```

The `codex-catalog` command remains a compatibility/model-picker utility only. New inference must not select `custom_gpt_bridge`, must not depend on `CODEX_BRIDGE_PROVIDER_KEY`, and must not call the bridge `/v1` surface as its model provider. Native Codex account/provider state is authoritative.

Backend handling:

1. Claim with a stable `request_key`.
2. A successful claim binds the key to that turn/lease. A null claim also persists the key as null, so retrying it cannot capture a later turn.
3. Read all context chunks using the claimed `turn_id` **and lease**, following exact `next_offset` until null.
4. Return answer text and/or only tool calls that the original request offered.
5. Complete with a stable completion key, or explicitly cancel with the active lease.

The null-claim ledger is deliberately fail-closed. If a process failure occurs after underlying claim but before the ledger is updated, retry does not claim new work; the unresolved turn expires/requires recovery rather than risking cross-turn substitution.

Supported provider surface remains limited to text context, ordinary function tools, raw custom tools, SSE/nonstreaming Responses, and completed `previous_response_id` continuation. Unsupported advanced Responses features fail explicitly.

## Dispatch flow

A task is tied to an allowlisted `project_id`, immutable scope (`read-only` or authorized `workspace-write`), and stable request key.

- same key + same payload => same task;
- same key + changed payload => conflict;
- follow-up keeps the parent project/scope;
- queued cancellation is immediate;
- running cancellation terminates the owned app-server process and finishes as `cancelled` when observed;
- an execution that becomes `unknown` remains available for reconciliation. Read-only tasks may proceed under the enforced read-only sandbox. A new workspace writer is blocked only when the unknown task may write to the same canonical project root. Aliases of the same root share one conflict identity.

A task reaching `completed` means the Codex reasoning turn completed. Captured command/test failures remain failures and must not be relabeled PASS.

## Project read boundary

Project directory/file tools accept only project-relative paths. The server opens each directory component relative to an already-open descriptor with `O_NOFOLLOW`; symlink components are rejected, so later renames cannot redirect an operation outside the configured root. Directory listing scans at most `limit + 1` entries. UTF-8 file reads use the opened descriptor and remain bounded to at most 1 MiB even if the file changes during the read.

## State and cleanup

SQLite state is stored beside the private config and inherits restrictive umask. The v0.2.2 backend claim ledger is created lazily in the same SQLite database. Stop the bridge before deleting state. Unknown tasks intentionally retain IDs for recovery.

## Verification

See `VALIDATION.md` for current candidate checks and runtime limitations. A plugin package update does not deploy or upgrade a separately hosted bridge service.

## Sources

- MCP specification `2026-07-28`: https://modelcontextprotocol.io/
- Codex app-server lifecycle: https://learn.chatgpt.com/docs/app-server
- GPT Actions: https://developers.openai.com/api/docs/actions/getting-started
- Responses streaming: https://developers.openai.com/api/docs/guides/streaming-responses

The bridge contracts are authored integration code, not universal official Codex APIs.
