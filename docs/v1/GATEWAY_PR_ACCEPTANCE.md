# Codex X Mode v1 — First Gateway PR Evidence

Date: 2026-10-10. Scope: first incremental v1 gateway PR; not v1 release acceptance.

## Implemented contract

- `POST /mode/mcp`: authenticated single-connection registry of 13 Core + 8 namespaced App tools.
- `POST /mcp`: legacy 13 Core tools. `POST /app/mcp` and `/codex-x-app/mcp`: legacy 8 App tools.
- MCP tool arguments omitted by the client default to `{}`. Explicit non-object arguments fail with JSON-RPC `-32602` before any dispatcher runs.
- HTTP MCP requests require configured server-side bearer authentication; no anonymous administrative write route is introduced.
- App threads use Native Codex app-server. Project and scope authorization is checked before reads and mutations. Newly created thread ownership is persisted in SQLite; foreign or unprovable Native threads are denied.
- Web-originated discovery and execution use exact `chatgpt-web/<version>` IDs directly from Native Codex `model/list`. The selected ID and execution model must match; an unprefixed model or alias remap fails closed. Reasoning effort comes from that native entry's supported/default metadata.

## Locally executed verification

- `cd server && python3 -m unittest discover -s tests -v`: **118 tests passed**.
- `cd server && python3 -m unittest discover -s tests -p test_v1_unified_mcp.py -v`: **5 passed**.
- `cd server && python3 -m unittest discover -s tests -p test_codex_x_app.py -v`: **15 passed**.
- `cd server && python3 -m unittest discover -s tests -p test_siwc_dispatch.py -v`: **16 passed**.
- `python3 -m compileall -q server/bridge server/tests`: passed.
- `python3 server/scripts/build_bundle.py`: archive SHA-256 `ae4317c4edfde479c43d5ff42cea153973bdbb9e73bac2120d3c827db6097f8b`.
- `git diff --check`: passed.

## Limits and remaining v1 work

These tests are local contract/fixture tests. They do not prove live Native Codex inference, remote ChatGPT Web/Mobile connectivity, Serena, Direct/Agent Dispatch, React Dashboard, Admin API, hosted authentication, or release readiness. The current `live_codex_verified` status remains false. Pre-existing Native threads without verifiable scope metadata or gateway-owned ownership records are intentionally inaccessible through the App surface. The Native Codex `thread/read` response does not provide the sandbox field, so persisted ownership is required for resumed gateway-created threads.

The static Web model alias registry and its catalog-generation command have been removed. Web model versions are advertised only when the connected Native Codex catalog returns the exact `chatgpt-web/*` identity. Codex Security diff scan coverage and findings will be recorded before merge is considered.

This PR is a development increment. Production deployment and release publication require separate approval and completion of the v1 Master Plan gates.
