# Codex X Mode v1.0 RC1 — Package Alignment and Native Read-Only Acceptance Evidence

Date: 2026-10-10 (Asia/Bangkok)
Status: **RC1 built and tested locally; G3 terminal inference identity UNVERIFIED; NO-GO for production.**

## Release-candidate scope

- Target plugin SemVer: `1.0.0-rc.1` in `plugin.json`, `.codex-plugin/plugin.json` and protocol/OpenAPI/runtime version surfaces.
- Python PEP 440: `1.0.0rc1` in `server/pyproject.toml` (equivalent prerelease representation).
- Rebuilt deterministic `assets/codex-x-mode-bridge.tar.gz` SHA-256: `579e799ac883fdca4bd881c1b69d102afcda803316277b106c1479ce774447f7`.
- Isolated extracted RC: `/Users/luiapi/codex-x-mode-v1-rc1-staging-20261010/final-verified` (not production).
- Standalone extraction and byte parity across all 37 bundled files: PASS.
- Development source full suite: **161/161 PASS**; clean-extracted RC full suite: **161/161 PASS**; AST checks: 29 files; deterministic bundle: PASS.
- The active resident runtime previously reported **0.2.23**, not RC1. No production runtime, hosted Plugin, Cloudflare tunnel, or API credentials were changed.

## Actual G3 one-connection / Native execution evidence

Two authorized, bounded, read-only Native Codex turns were executed in a **temporary project** using a temporary local MCP server on `/mode/mcp`, temporary state database and an isolated auth-only Native Codex profile. Only the temporary project `probe.txt` was read. The test did not use the production database.

| Evidence | Observation |
|---|---|
| User request scope | `read-only` (project write disabled) |
| Native selected model | `gpt-6.1-sol` |
| Native `turn/completed` | `completed` |
| Answer content | Exactly matched fixture content |
| Filesystem integrity | `probe.txt` SHA-256 unchanged, one project file |
| Recorded model reroutes | 0 |
| `turn/completed.turn.model` | **Absent** |
| `thread/read.thread.model` | `gpt-6.1-sol` |
| Matching `thread/read.turn.status` | `completed` |
| Matching `thread/read.turn.model` | **Absent** |
| Accepted terminal model identity | **UNVERIFIED** |
| Task final status | `failed` (accepted_success=false; safe writer reconciliation) |

These are observations from real Native Codex app-server, **not fixture-only results**. The model was selected and requested exactly, and the thread-level model agrees; however, neither the terminal notification nor the matching turn readback asserted the actual *executed* model. This is insufficient under the no-reroute, exact-terminal-model acceptance contract. It must not be retroactively represented as an inferred success.

## Diagnostic improvement in RC1

For v1 Native Web-origin tasks whose terminal notification omits a model, the bridge now performs an explicit same-thread `thread/read` to capture narrow provenance: readback source, whether the thread-level model matches the selected model, and status of the matching turn. Missing or inaccessible readback does not undo the Native terminal result; thread metadata **never sets** `model_identity_verified=true`. A task remains unaccepted until terminal evidence is authoritative. This path is unit-tested for fail-closed behavior.

## Remaining blockers

1. Native app-server release and protocol must provide an authoritative per-turn executed model in terminal notification or equivalent verifiable evidence. Thread-level configured model is insufficient on its own.
2. Review/update terminal-model acceptance only against explicitly documented, supported Native Codex response data. Avoid guessing a model from selection, catalog or thread metadata.
3. Run a fresh isolated G3 read-only acceptance against that capability and ensure exact requested/selected/observed IDs, no reroutes, matching turn identity, and no project changes.
4. Independently verify real ChatGPT Web/Mobile remote connector, authentication, deployed code/hash parity and rollback before GO.

**No deploy, no v1 release, and no inferred terminal model identity were made in this work.**
