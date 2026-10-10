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
python3 -m bridge.client --url http://127.0.0.1:8240/mode/mcp
python3 -m bridge.client --url https://YOUR-ACTUAL-HOST/mode/mcp
python3 -m bridge.client --url https://YOUR-ACTUAL-HOST/mode/mcp --protocol 2026-07-28
```

The client reads CODEX_X_MCP_TOKEN. Obtain its value locally through your secret
manager/private configuration; never paste it in chat or commit it. The doctor
performs discovery, resources/prompts listing, status and project listing only.
It does not create or claim work. It reports transport readiness, not live
Codex or ChatGPT acceptance. Remote HTTP without TLS and redirects are rejected.
There are no automatic mutation retries.

## HTTPS host

Use an existing user-controlled domain/certificate and persistent host. Run the
bridge as the same unprivileged OS user that owns the configured repositories
and Codex authentication, with private config mode 600 and restrictive umask.
Use server/nginx-locations.conf inside that host's existing HTTPS server block.
The bridge remains on loopback. Expose only /mode/mcp, authenticated
compatibility MCP routes when needed, /healthz and explicit Actions routes.
Keep /v1/ inaccessible through the public proxy. Disable
access logs on routes carrying backend leases and prompts.

Absent Origin is accepted for server-to-server clients; supplied Origin is
denied unless explicitly present in allowed_origins in private config. Do not
add a wildcard. Configure only a host's observed required origins.

## ChatGPT web

Inspect the actual account's custom-app availability and authentication choices.
In its supported developer/app configuration, enter the real HTTPS
`/mode/mcp` URL, configure authenticated access using a mechanism the host
actually supports, and scan tools. The bridge implements distinct static Bearer keys; it does not
implement an OAuth authorization server. If the host requires OAuth, a compatible
authenticated gateway is required; do not select no-auth on this private server.
Preserve the existing connector/plugin identity. Do not replace .app.json with a
new connector ID or invent an endpoint.

Verify all 21 Core/App tools are discovered through the one MCP connection.
Call codex_x_status and codex_x_list_projects first. Then test one explicitly authorized read-only
task against real Codex, capture its task ID/result/command outcomes, and verify
backend claim/context/complete using a real issued turn. A completed reasoning
turn with a failed captured command is not a passing command test.

`GET /models` and `codex_x_list_models` use Native Codex app-server
`model/list`. Web discovery exposes exact `chatgpt-web/<version>` IDs and
supported/default effort metadata; dispatch sends the exact ID and a supported
effort through Native Codex. Web-originated Codex X App operations that start
or continue a turn validate the requested or existing thread model against the
same Native catalog; new turns explicitly pin the exact ID and supported
effort. Steering an active Web-originated App turn also requires the thread's
current effort to equal the selected supported effort; a mismatch or missing
value is rejected because Native `turn/steer` cannot change model or effort.
The catalog does not prove completed inference or
change the current ChatGPT conversation model. Missing/mismatched terminal
identity or reroutes leave execution `unknown` without replay. Real inference
remains unverified by local fixture tests.

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
