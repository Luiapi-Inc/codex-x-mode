# Codex X Mode v1 — Local Admin API Security & Configuration Evidence

Date: 2026-10-10 (Asia/Bangkok)
Status: Local-only development implementation; NOT approved for production or Cloudflare remote ingress.

## Surface

- Separate optional listener bound to `127.0.0.1` and a port different from MCP.
- Start via `python3 -m bridge --config /private/bridge.json serve --port 8240 --admin-port 8241` using an existing private v1 config. This command is an example only; no production service was restarted.
- A distinct `admin_key` is required; missing, weak or re-used MCP/GPT/provider keys reject startup. New v1 setups generate an independent admin key and start admin disabled until the operator explicitly selects `--admin-port`.
- Local-only endpoints: `GET /admin/v1/config`, `POST /admin/v1/config/preview`, `/apply`, `/rollback/preview`, `/rollback`.
- No endpoints for generic shell, filesystem or secrets; config snapshots are redacted. Credential values are never returned. Admin routes are not on the MCP listener.

## Authorization and mutation semantics

- Every request checks loopback peer, exact localhost Host, optional same-origin browser Origin, Fetch Metadata, and a separate Admin bearer credential. Proxy/Cloudflare forwarded request headers are rejected; there is NO Cloudflare Access JWT verification in this implementation.
- Mutations require separate preview and a short-lived HMAC-signed confirmation bound to action + exact revision + exact patch, followed by revision CAS and atomic persistence.
- Hot reload is restricted to existing `mcp_policy` and `allowed_origins`; projects, secrets and model-policy changes require offline config management and restart after independent review.
- Policy swaps acquire MCP request admission lock and revalidate tool permissions. A failed health check rolls back disk and runtime; if exact previously applied revision cannot be established, the runtime fails closed to read-only instead of overwriting another writer.
- Private config history is owner-only, digest-checked and used for guarded rollback. SQLite audit records action/outcome/revision only; no raw config, prompts or credentials.

## Verification evidence

- TDD RED: `ModuleNotFoundError: bridge.admin` before implementation.
- Admin API tests exercise 401/403 negatives, Host/Origin/proxy restriction, distinct secret, confirmation forgery, CAS/replay, local hot apply, separate MCP policy, health rollback, history rollback and concurrent-writer conflict.
- See CI and final test suite outcomes in the associated development commit, rather than assuming deployed runtime parity.

## Blocked gates

- Remote Administration remains DISABLED: Cloudflare Access JWT signature, issuer/audience/key rotation, MFA and ingress negative tests have not been implemented or exercised. A proxy header is never accepted as identity.
- No browser Dashboard acceptance or online changes of projects/provider credentials.
- No production deployment; changing `main` source does not change the resident runtime.

## Native Codex G3 gate

The active runtime reports `version=0.2.23`, `live_codex_verified=false`. Development manifests and package surface still show `0.2.12`, and Native Codex is `codex-cli 0.162.0-alpha.17.2`. Read-only local MCP discovery/model-list tests are supported evidence; they are not real turn execution. Do not trigger a live model turn under an unaligned deployed package/Plugin state merely to claim acceptance. First prepare an isolated verified release candidate and align the requested/selected/observed terminal evidence contract; then perform the single harmless read-only acceptance turn in an authorized environment.
