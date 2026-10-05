# MCP client and host readiness

Use the installed plugin's server/bridge/client.py for explicit transport
diagnostics when a native connector cannot provide equivalent evidence.
Run it in the environment that owns the installed runtime; do not infer remote
readiness from this chat's scratch environment.

- Stdio: python3 -m bridge.client --config /absolute/private/config.json
- HTTP: python3 -m bridge.client --url https://ACTUAL-HOST/mcp
- Supply CODEX_X_MCP_TOKEN through the target environment's secret handling.
  Never request it in chat or put it in a task prompt.
- Default compatibility protocol is 2025-11-25. Use --protocol 2026-07-28
  for modern discovery. The client supports the bundled JSON HTTP/stdio server;
  it does not implement arbitrary SSE/subscription transports.
- The CLI performs discovery/status/project listing only. It does not authorize
  task creation, backend claiming or other mutations.
- Read live_host_verified and live_codex_verified literally. A successful
  transport report does not establish a real Codex task or ChatGPT connection.
- After an ambiguous mutation response, recover the known task/turn/request
  identity before an explicit retry. Never create a replacement automatically.

Read server/WEB-CONNECTION.md in the installed package for HTTPS deployment,
authentication, tool scan and acceptance gates. The package includes static
Bearer authentication, not an OAuth authorization server. Verify the actual
host's auth choices before connecting. Retain connector/plugin identity.

Custom MCP apps are currently web only per OpenAI's help documentation checked
2026-10-04. Report mobile availability separately, and verify it against the
current host documentation when asked. Do not claim uploading a package deploys
a service or unlocks host support.
