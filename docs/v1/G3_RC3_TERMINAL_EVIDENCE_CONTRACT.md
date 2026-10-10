# Codex X Mode v1 RC3 — Execution vs. Model Attestation Contract

**Date:** 2026-10-10 (Asia/Bangkok)
**Status:** Development RC3 verification PASS; exact per-turn executed-model identity remains UNSUPPORTED by installed Native CLI. Production release NO-GO.

## Verified upstream limitation

The installed CLI is `codex-cli 0.162.0-alpha.17.2`. Its generated v2 schemas omit `model` in `TurnCompletedNotification.turn` and matching `ThreadReadResponse.turn`. The `ThreadReadResponse.thread.model` field is explicitly the *configured/latest persisted thread model*, not per-turn telemetry.

Upstream discussion: https://github.com/openai/codex/issues/33880 requests exposing provider-reported per-turn model in the app-server protocol. At the time of the review, it is open. The lack of `model/rerouted` notifications does not prove a provider did not reroute or that a particular model served an inference.

**Schema fingerprint:** `29ac65de235428a597c2baf48860abdef0174b1cb87099ef91085c30a9239e92`.

## Implementation

- `server/bridge/terminal_evidence.py` classifies **Native execution completion**, **model attestation**, **proof source**, **reason**, and **acceptance verdict** independently. It never derives per-turn model attestation from thread configured model or from answer correctness.
- `server/bridge/codex.py` persists the classifier output as `result.execution_evidence` in addition to existing Native event and model-selection evidence.
- When Native terminal reports `completed` but per-turn model telemetry is missing, `native_execution=terminal_completed`, `model_attestation=unverified`, and `acceptance_verified=false`. The task remains unaccepted; prior fail-closed writer reconciliation rules remain unchanged.
- Native reroute event, observed model mismatch, failed turn, or unknown terminal status never passes acceptance.
- If a future Native App Server returns an *explicit per-turn* model field in a matching `thread/read` turn (same thread ID, turn ID and terminal status), the adapter can identify that field separately from the configured thread model. Actual future upstream field semantics require explicit validation before treating that source as production-authoritative. **The installed CLI does not expose this field.**
- No new privileged tools, authentication changes or production migrations.

## Test evidence

- RED/GREEN tests added for the separate evidence classifier, missing-model fail-closed state, reroutes, mismatches and failed turns.
- Native adapter fixtures cover exact thread/turn matching, missing terminal model, hypothetical per-turn readback, mismatched readback, and unrelated turn rejection. These are *fixtures*, not proof of currently deployed Native per-turn telemetry.
- Full source test suite: **171/171 PASS**.
- Clean-extracted RC3 test suite: **171/171 PASS**.
- Python AST parsing: **33 files PASS**.
- Reproducible bundle and extraction byte parity: **41/41 files PASS**.
- RC3 package/plugin SemVer: `1.0.0-rc.3`; Python PEP 440: `1.0.0rc3`.
- Bundle SHA-256: `d6a90a900f67a50d703a735f62924c2cd3a1a54c4a9b1323f7c72d7d226474f3`.
- Isolated RC staging: `/Users/luiapi/codex-x-mode-v1-rc3-staging-20261010`.
- Clean-bundle `native-protocol-check` again reports `acceptance_gate=UNSUPPORTED` and `inference_invoked=false`.

## Acceptance gates

1. **G3 exact terminal-model proof — BLOCKED UPSTREAM.** Await an upstream Codex App Server protocol that explicitly guarantees provider-reported executed-model identity at matching per-turn scope.
2. Once available, perform a new isolated Native read-only turn with requested/selected/observed model proof, exact correlation, reroute capture, and immutable test-project fingerprint.
3. Real ChatGPT Web and Mobile remote connector/authorization E2E, hosted manifest/runtime parity, and rollback rehearsal remain **NOT VERIFIED**.
4. Remote Admin and Cloudflare Access JWT signature/claims validation also remain separate gates.
5. G4 Direct/Serena design and work can progress independently on read-only/local safety surfaces, but it does not waive G3 or authorize production deployment.

**No deployment, restart, Cloudflare edit, hosted Plugin publication, or production database modification was performed.**
