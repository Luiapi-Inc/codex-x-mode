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
python3 -m bridge.client --url https://codex-x-mode.lott0.online/mode/mcp
python3 -m bridge.client --url https://codex-x-mode.lott0.online/app/mcp
```

The client reads CODEX_X_MCP_TOKEN. Obtain its value locally through your secret
manager/private configuration; never paste it in chat or commit it. The doctor
performs discovery, resources/prompts listing, status and project listing only.
It does not create or claim work. It reports transport readiness, not live
Codex or ChatGPT acceptance. Remote HTTP without TLS and redirects are rejected.
There are no automatic mutation retries.

For Codex Desktop on the execution host, the Plugin owns both MCP declarations. Authoritative portable `mcp.json` installs the loopback Streamable HTTP identities and the compatibility `.mcp.json` carries the `CODEX_X_MCP_TOKEN` environment-variable binding. Do not add duplicate MCP entries to user-level Codex configuration. This reuses the already-running bridge instead of starting a second `mcp-stdio` owner against the same private config. Keep the token machine-local and out of plugin files. Standalone stdio remains an explicit runtime mode when no other bridge owns the config lock; it is not the installed Plugin's primary MCP transport.

## HTTPS host

Use an existing user-controlled domain/certificate and persistent host. Run the
bridge as the same unprivileged OS user that owns the configured repositories
and Codex authentication, with private config mode 600 and restrictive umask.
The bridge remains on loopback. The production remote transport is the Cloudflare Named Tunnel `codex-x-mode` with hostname `codex-x-mode.lott0.online`. Its ingress allowlists only `/mode/mcp` and `/app/mcp`, both forwarded to the loopback bridge; the catch-all returns 404. Keep `/v1/`, `/healthz`, Actions routes, and all other bridge routes inaccessible through this public hostname. Tunnel credentials and configuration stay machine-local and outside Git.

Absent Origin is accepted for server-to-server clients; supplied Origin is
denied unless explicitly present in allowed_origins in private config. Do not
add a wildcard. Configure only a host's observed required origins.

## ChatGPT web

The Plugin package owns MCP installation metadata; do not make users add duplicate MCP URLs in ChatGPT or Codex configuration. The execution host exposes the two stable Cloudflare Named Tunnel endpoints:

- `https://codex-x-mode.lott0.online/mode/mcp` for `codex-x-mode`
- `https://codex-x-mode.lott0.online/app/mcp` for `codex-x-app`

Remote access stays authenticated. Never put the bridge's static `mcp_key` into `mcp.json`, `.mcp.json`, `.app.json`, Skill metadata, or hosted Plugin files. A ChatGPT remote MCP binding must use a supported authenticated server integration; write-capable/user-specific remote MCP must not be downgraded to anonymous access just to avoid connection setup. The Cloudflare tunnel is transport only and does not replace the MCP authorization boundary.

The package's `.app.json` maps only verified registered ChatGPT MCP identities. Changing a registered mapping is an explicit binding migration; preserve the outer Codex X Mode Plugin identity and do not invent connector IDs.

Verify all 13 tools are discovered. Call codex_x_status and
codex_x_list_projects first. Then test one explicitly authorized read-only
task against real Codex, capture its task ID/result/command outcomes, and verify
backend claim/context/complete using a real issued turn. A completed reasoning
turn with a failed captured command is not a passing command test.

`GET /models` and HTTP `codex_x_list_models` expose package-supported aliases mapped onto Native Codex `model/list`. Web and local dispatch both execute through Native Codex app-server; Native Codex owns authentication, reasoning metadata, and inference. This does not change the current ChatGPT conversation model. The exact underlying model is sent to thread/turn start. Missing/mismatched terminal model identity or reroutes leave execution `unknown` without replay. SIWC/custom Responses-provider routing is legacy-recovery only; see [SIWC.md](SIWC.md).

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
