# Codex X Mode v1 — First Gateway PR Evidence

Date: 2026-10-10. Scope: first incremental v1 gateway PR; not v1 release acceptance.

## Implemented contract

- `POST /mode/mcp`: authenticated single-connection registry of 13 Core + 8 namespaced App tools.
- `POST /mcp`: legacy 13 Core tools. `POST /app/mcp` and `/codex-x-app/mcp`: legacy 8 App tools.
- MCP tool arguments omitted by the client default to `{}`. Explicit non-object arguments fail with JSON-RPC `-32602` before any dispatcher runs.
- HTTP MCP requests require configured server-side bearer authentication; no anonymous administrative write route is introduced.
- App threads use Native Codex app-server. Project and scope authorization is checked before reads and mutations. Newly created thread ownership is persisted in SQLite; foreign or unprovable Native threads are denied.
- Web-originated model routing uses the packaged `chatgpt-web/*` aliases and Native Codex's catalog/inference authority. No silent custom-provider substitution is permitted.

## Locally executed verification

- `cd server && python3 -m unittest discover -s tests -v`: **120 tests passed**.
- `cd server && python3 -m unittest discover -s tests -p test_v1_unified_mcp.py -v`: **5 passed**.
- `cd server && python3 -m unittest discover -s tests -p test_codex_x_app.py -v`: **15 passed**.
- `python3 -m compileall -q server/bridge server/tests`: passed.
- `python3 server/scripts/build_bundle.py`: deterministic archive SHA-256 `0757aeb123cd3ee9822c3bde8da1e6dc7f645923ec2925b7926320bcf94621b4` on successive runs.
- `git diff --check`: passed.

## Limits and remaining v1 work

These tests are local contract/fixture tests. They do not prove live Native Codex inference, remote ChatGPT Web/Mobile connectivity, Serena, Direct/Agent Dispatch, React Dashboard, Admin API, hosted authentication, or release readiness. The current `live_codex_verified` status remains false. Pre-existing Native threads without verifiable scope metadata or gateway-owned ownership records are intentionally inaccessible through the App surface. The Native Codex `thread/read` response does not provide the sandbox field, so persisted ownership is required for resumed gateway-created threads.

Independent Heavy Route worker dispatch was attempted, but the tool hook rejected the three spawn requests: `TokenX denied this Agent call: matching routing decision is missing`. No worker review is claimed.

This PR is a development increment. Production deployment and release publication require separate approval and completion of the v1 Master Plan gates.
