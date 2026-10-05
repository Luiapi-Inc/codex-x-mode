# HTTPS connection and host verification

This single-operator bridge needs a persistent host with Python 3.11+, the
actual Codex CLI/authentication, access to allowlisted repositories, and a
writable private state directory. Uploading a plugin does not deploy it.
Do not place SQLite state or the CLI worker in an ephemeral serverless function.

## Local readiness

Run from the installed package's server directory:

```bash
python3 -m bridge --config /absolute/private/bridge-private.json setup --project demo --cwd /absolute/repository
python3 -m bridge.client --config /absolute/private/bridge-private.json
python3 -m bridge --config /absolute/private/bridge-private.json serve
```

Setup creates a new file and never overwrites an existing private configuration.
The stdio check and HTTP service must run sequentially against the same config:
the runtime lock prevents two owners. Check a running HTTP service using the
MCP key supplied through a secure environment rather than a command-line token:

```bash
python3 -m bridge.client --url http://127.0.0.1:8240/mcp
python3 -m bridge.client --url https://YOUR-ACTUAL-HOST/mcp
python3 -m bridge.client --url https://YOUR-ACTUAL-HOST/mcp --protocol 2026-07-28
```

The client reads CODEX_X_MCP_TOKEN. Obtain its value locally through your secret
manager/private configuration; never paste it in chat or commit it. The doctor
performs discovery, resources/prompts listing, status and project listing only.
It does not create or claim work. It reports transport readiness, not live
Codex or ChatGPT acceptance. Remote HTTP without TLS and redirects are rejected.
There are no automatic mutation retries.

For Codex Desktop on the execution host, the plugin compatibility config in `.mcp.json` targets `http://127.0.0.1:8240/mcp` and reads the bearer from `CODEX_X_MCP_TOKEN`. This reuses the already-running bridge instead of starting a second `mcp-stdio` owner against the same private config. Keep the token machine-local and out of plugin files. The portable `mcp.json` retains bundled stdio for standalone local hosts where no other bridge owns the config lock.

## HTTPS host

Use an existing user-controlled domain/certificate and persistent host. Run the
bridge as the same unprivileged OS user that owns the configured repositories
and Codex authentication, with private config mode 600 and restrictive umask.
Use server/nginx-locations.conf inside that host's existing HTTPS server block.
The bridge remains on loopback. Expose only /mcp, /healthz and the explicit
Actions routes. Keep /v1/ inaccessible through the public proxy. Disable
access logs on routes carrying backend leases and prompts.

Absent Origin is accepted for server-to-server clients; supplied Origin is
denied unless explicitly present in allowed_origins in private config. Do not
add a wildcard. Configure only a host's observed required origins.

## ChatGPT web

For Secure MCP Tunnel, create or select the ChatGPT developer-mode MCP app
against the existing tunnel ID; do not use the OpenAI control-plane tunnel URL
as an MCP server URL. The tunnel must already be associated with the intended
OpenAI organization and ChatGPT workspace.

The bridge keeps its static Bearer authentication on the loopback HTTP target.
ChatGPT-side `No Authentication` is acceptable only for Secure MCP Tunnel when
the tunnel-client profile injects the bridge Bearer value through both MCP
`extra_headers.Authorization` and `discovery_extra_headers.Authorization`.
Do not remove bridge authentication or expose the loopback target publicly.

The package's `.app.json` must map to the registered ChatGPT developer-mode app
returned by the binding flow. Updating that registered app mapping is an explicit
binding migration; preserve the outer Codex X Mode plugin identity and the
existing tunnel identity. Never invent either identifier.

Verify all 13 tools are discovered. Call codex_x_status and
codex_x_list_projects first. Then test one explicitly authorized read-only
task against real Codex, capture its task ID/result/command outcomes, and verify
backend claim/context/complete using a real issued turn. A completed reasoning
turn with a failed captured command is not a passing command test.

`GET /models` and HTTP `codex_x_list_models` expose the signed-in account's
token-scoped model catalog. Web dispatch uses SIWC and the public Responses
provider; local stdio keeps the app-server catalog/provider. Neither changes
the current ChatGPT conversation model or establishes plan entitlement.
The exact selection is sent to thread/turn start. Missing/mismatched terminal
model identity or reroutes leave execution `unknown` without replay.
See [SIWC.md](SIWC.md); real authorization and inference remain unverified.

For a Custom GPT Actions connection, generate the actual-host schema:

```bash
python3 -m bridge schema --url https://YOUR-ACTUAL-HOST --output openapi.json
```

Use the distinct gpt_key in the host's authentication configuration, and import
that schema with the bundled Instructions. Actions and MCP are separate host
integrations; success with one does not establish success with the other.

## Mobile and evidence

Mobile MCP or Custom GPT Actions availability must be checked in the actual
host/account. Package changes and local HTTP tests do not establish mobile
acceptance.

Reference (checked 2026-10-04):
https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt

Local integration tests cover real loopback sockets and owned stdio processes;
dispatch uses a named app-server fixture. A public URL, valid TLS certificate,
host authentication and real Codex are independent acceptance gates.
