# Codex X Mode v1.0

**Status: development planning / staging; not a production v1 release.**

Codex X Mode v1 is a clean-slate personal coding control plane: a single authenticated MCP gateway for ChatGPT Web/Mobile; Native Codex execution; optional Serena and Agent providers; durable runs/recovery; and a local/remote admin dashboard.

The code currently checked out on `main` is transitional implementation and is not yet v1 acceptance. The v1 design is authoritative; implementation and acceptance are separate gates.

- Design: [docs/v1/MASTER_DEVELOPMENT_PLAN.md](docs/v1/MASTER_DEVELOPMENT_PLAN.md)
- Engineering instructions: [AGENTS.md](AGENTS.md)
- Legacy archive: `archive/v0.2.23-legacy`
- Release approval: **NOT GRANTED**


## v1.0 RC1 source package (NO-GO)

Source, private Plugin manifest, bridge protocol and OpenAPI metadata are aligned to `1.0.0-rc.1` (`1.0.0rc1` in Python packaging). A clean-extracted RC runs all 161 tests. Native live read-only turns completed with exact selected model and unchanged workspace, but *terminal* model identity was not supplied; G3 remains unverified. See [RC1 live Native evidence](docs/v1/G3_RC1_LIVE_NATIVE_EVIDENCE.md). No deployment or Plugin publication is authorized.

## V1 G2/G3 development status

The [G2/G3 evidence](docs/v1/G2_G3_IMPLEMENTATION_EVIDENCE.md) records locally verified Policy/Config Kernel, unified MCP, and isolated Native Codex model catalog integration. The production release is not authorized.

New configurations default to schema v1, read-only MCP admission and Native model policy. Local offline configuration CLI: `config-show`, `config-preview --changes FILE --expected-revision SHA`, and `config-apply --changes FILE --expected-revision SHA --confirm`. Apply returns `requires_reload=true`; it does not restart or deploy the bridge.

## Local Admin API (development, opt-in)

V1 can start a separate loopback-only admin listener after local setup creates a distinct `admin_key`:

```sh
python3 -m bridge --config /path/to/private.json serve --port 8240 --admin-port 8241
```

The local Admin HTTP endpoints provide a redacted config snapshot and a guarded `preview` / signed `apply` / `rollback` flow. Live updates support only existing `mcp_policy` and `allowed_origins` fields, with revision CAS and audit. This is **not** a Cloudflare Access-integrated remote Admin API; remote ingress must remain disabled until independently verified signed Access JWT enforcement exists.

See [Admin security evidence](docs/v1/G2_ADMIN_API_SECURITY_EVIDENCE.md).
