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

## v1.0 RC2 — Native protocol capability (NO-GO)

Use `cd server && python3 -m bridge native-protocol-check` to inspect the installed Codex app-server schema without requiring private bridge configuration or triggering inference. The installed CLI used in RC2 verification does **not** expose per-turn executed model identity through `turn/completed` or matching `thread/read.turn`; its thread-level `model` is only configuration metadata. RC2 therefore retains a strict `UNSUPPORTED` model-attestation gate, not a false PASS. See [RC2 protocol schema evidence](docs/v1/G3_RC2_PROTOCOL_SCHEMA_GATE.md).

## v1.0 RC3 — Execution evidence and model attestation (NO-GO)

The v1 RC3 bridge records the Native turn's terminal execution outcome separately from *proof of which model executed it*. A matching answer or configured thread model is not per-turn executed-model attestation. The installed Native CLI schema does not provide this telemetry. The G3 acceptance gate stays **UNSUPPORTED**, despite 171 passing tests and reproducible clean packaging. See [RC3 evidence](docs/v1/G3_RC3_TERMINAL_EVIDENCE_CONTRACT.md).

## v1.0 RC4 — Serena ChatGPT context (development only)

G4 pins the built-in Serena context `chatgpt`. The real upstream catalog contains 29 tools; 13 read-only and 11 guarded edit/memory operations have gateway contracts. Five higher-risk operations remain blocked. New setups default to `serena.context=chatgpt`, `serena.enabled=false`, `serena.allow_mutations=false`. Read-only Serena uses a temporary project mirror so language-server cache cannot alter source. See [G4 RC4 evidence](docs/v1/G4_RC4_SERENA_CHATGPT_INTEGRATION.md).

## v1.0 RC5 — Safe Serena multi-file preview (development only)

With Serena `context=chatgpt` enabled, the unified MCP Gateway additionally exposes `codex_x_serena_preview_replace_in_files` as a **read-only**, bounded, literal multi-file preview. It forces native Serena `dry_run=true` inside an ephemeral source mirror; there is **no** direct multi-file apply privilege through this wrapper. Serena children use disposable HOME/XDG directories and scoped CWD instead of operator credential storage.

Authenticated loopback Serena MCP passed local integration, but **not** ChatGPT Web/Mobile remote E2E. Five high-risk upstream tool names remain intentionally unavailable to remote MCP regardless of allowlist. See [RC5 Serena connector security evidence](docs/v1/G4_RC5_CONNECTOR_SECURITY_EVIDENCE.md).
