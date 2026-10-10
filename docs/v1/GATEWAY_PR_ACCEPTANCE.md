# Codex X Mode v1 — First Gateway PR Evidence

Date: 2026-10-10. Scope: first incremental v1 gateway PR; not v1 release acceptance.

## Implemented contract

- `POST /mode/mcp`: authenticated single-connection registry of 13 Core + 8 namespaced App tools.
- `POST /mcp`: legacy 13 Core tools. `POST /app/mcp` and `/codex-x-app/mcp`: legacy 8 App tools.
- MCP tool arguments omitted by the client default to `{}`. Explicit non-object arguments fail with JSON-RPC `-32602` before any dispatcher runs.
- HTTP MCP requests require configured server-side bearer authentication; no anonymous administrative write route is introduced.
- App threads use Native Codex app-server. Project and scope authorization is checked before reads and mutations. Newly created thread ownership is persisted in SQLite; foreign or unprovable Native threads are denied.
- Web-originated discovery and execution use exact `chatgpt-web/<version>` IDs directly from Native Codex `model/list`. The selected ID and execution model must match; an unprefixed model or alias remap fails closed. Every selected effort must appear in the Native Codex model's supported-effort metadata before the task is queued.
- Persisted tasks using the retired `chatgpt_plan` or `chatgpt_web_headless` route are failed before any provider or app-server is accessed; they are never replayed through a different provider.

## Locally executed verification

- `cd server && python3 -m unittest discover -s tests -q`: **101 tests passed**.
- `cd server && python3 -m unittest -q tests.test_v1_unified_mcp tests.test_codex_x_app`: **20 tests passed**.
- `cd server && python3 -m unittest -v tests.test_native_web_dispatch`: **19 tests passed**, including empty-family rejection and fail-closed legacy-route coverage.
- `python3 -m compileall -q server/bridge server/tests`: passed.
- `python3 server/scripts/build_bundle.py`: archive SHA-256 `93ed5384d0e95057541b727cd59e989a0fd18bcd26c7d80d91298e984c01b34e`.
- `git diff --check`: passed.
- `git diff --check`: passed.

## Limits and remaining v1 work

These tests are local contract/fixture tests. They do not prove live Native Codex inference, remote ChatGPT Web/Mobile connectivity, Serena, Direct/Agent Dispatch, React Dashboard, Admin API, hosted authentication, or release readiness. The current `live_codex_verified` status remains false. Pre-existing Native threads without verifiable scope metadata or gateway-owned ownership records are intentionally inaccessible through the App surface. The Native Codex `thread/read` response does not provide the sandbox field, so persisted ownership is required for resumed gateway-created threads.

The static Web model alias registry and its catalog-generation command have been removed. Web model versions are advertised only when the connected Native Codex catalog returns the exact `chatgpt-web/*` identity. The independent Codex Security diff scan covers the exact `main..PR head` range; its final coverage and findings are recorded in the PR description before merge is considered.

This PR is a development increment. Production deployment and release publication require separate approval and completion of the v1 Master Plan gates.
