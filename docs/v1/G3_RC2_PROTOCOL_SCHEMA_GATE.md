# Codex X Mode v1.0 RC2 — Native Codex Protocol Capability Gate

**Date:** 2026-10-10 (Asia/Bangkok)
**Status:** RC2 source/package verification PASS; G3 exact terminal-model attestation **UNSUPPORTED** with installed Native CLI; production release **NO-GO**.

## Purpose and security contract

The G3 acceptance gate requires a verifiable per-turn **executed model** identity rather than relying on the model selected at thread start. A configured thread model, model/list visibility, a successful answer, and a missing model/rerouted event do **not** prove which model executed the final turn. The bridge preserves failure/unknown reconciliation behavior rather than fabricate evidence.

## Native CLI/schema inspected

- Installed CLI: `codex-cli 0.162.0-alpha.17.2`
- Command: `codex app-server generate-json-schema --out <temporary-directory>` (schema-only; no inference).
- Generated protocol: v2 `TurnCompletedNotification.json` and `ThreadReadResponse.json`.
- Protocol schema digest (both required files in deterministic name order): `29ac65de235428a597c2baf48860abdef0174b1cb87099ef91085c30a9239e92`.
- `TurnCompletedNotification.definitions.Turn.properties`: ID/status/items/timestamps, **no model**.
- `ThreadReadResponse.definitions.Turn.properties`: **no model**.
- `ThreadReadResponse.definitions.Thread.properties.model`: present, but explicitly described by generated schema as **current configured or latest persisted model**, *not per-turn execution telemetry*.
- `ModelReroutedNotification` is part of the generated protocol, with correlation fields `threadId`, `turnId`, `fromModel`, `toModel`, and `reason`. That a notification schema exists does not establish that a particular inference used any specific model.

## v1 implementation

- `server/bridge/native_protocol.py` inspects installed Native protocol schemas by invoking `codex --version` and `codex app-server generate-json-schema` in a temporary directory; no provider key, project access, MCP connection, Native thread or inference is required.
- `python3 -m bridge native-protocol-check` works without private configuration and produces machine-readable JSON including schema SHA-256, terminal/readback model field support, and acceptance gate.
- `server/tests/test_v1_native_protocol.py` follows RED/GREEN tests for schema without turn model, hypothetical future model fields (capability != successful attestation), invalid/missing schema fail-closed, and config-independent CLI inspection.
- This probe does not replace live model verification. A future schema exposing a per-turn model would only mean the surface is *potentially* available; actual terminal event/readback must still be checked for exact turn identity, selected/observed ID, and reroutes.

## Actual inspection result

```
acceptance_gate = UNSUPPORTED
terminal_model_surface = unsupported
turn_completed_has_model = false
readback_turn_has_model = false
thread_configured_model_only = true
terminal_identity_verified = false
inference_invoked = false
```

## RC2 package integrity

- Plugin + private-plugin SemVer: `1.0.0-rc.2`.
- Python PEP 440: `1.0.0rc2`.
- OpenAPI and protocol runtime metadata: `1.0.0-rc.2`.
- Deterministic `assets/codex-x-mode-bridge.tar.gz` SHA-256: `311a5208617ee175ad5b0a05f25bc8786294b14d8ca371387883260774acdf2d`.
- Source tests: **165/165 PASS**. Clean-extracted bundle tests: **165/165 PASS**. Byte parity: **39/39 files**. AST parse: **31 files**.
- Isolated artifact: `/Users/luiapi/codex-x-mode-v1-rc2-staging-20261010/`.

## Operational status / next gate

- RC1 live real Native read-only execution previously produced matching answer and no changed project files, with `gpt-6.1-sol` selected, but the terminal event omitted the executed model. G3 remains **UNVERIFIED**, not accepted. No additional inference was invoked for this protocol check.
- Do **not** weaken the acceptance gate or copy configured `thread.model` into `observed_model`.
- A future CLI/protocol providing authoritative per-turn execution telemetry must first pass `native-protocol-check` capability, then a fresh **bounded read-only turn** with exact observed-model evidence. Only after that should ChatGPT Web/Mobile remote E2E and deployment gates be considered.
- This development change neither replaces the deployed v0.2.23 bridge nor publishes the Plugin nor changes Cloudflare.
