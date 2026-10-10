# Codex X Mode — Source of Truth

Date: 2026-10-05
Status: Authoritative architecture checkpoint

## Required execution path

Web-originated dispatch MUST use:

```text
ChatGPT Web
  -> Codex X Mode bridge
  -> Native Codex app-server
  -> exact chatgpt-web/<selected-version>
  -> project-owned Web route adapter
  -> ChatGPT Web backend
```

Do not map Web-originated work to `chatgpt_plan`.
Do not silently substitute `gpt-6-astra` or another non-`chatgpt-web/*` model.

## Model/version contract

`chatgpt-web` is a family prefix. A concrete model version must be selectable from Native Codex `model/list`.

Observed catalog examples:

- `chatgpt-web/gpt-5.6-sol`: default effort `high`; supported observed efforts `medium`, `high`.
- `chatgpt-web/gpt-5.6-sol-instant`: default effort `low`.

Web-origin model discovery must expose exact `chatgpt-web/*` IDs from Native Codex. The bridge must preserve default/supported reasoning-effort metadata and explicitly send a supported effort to `turn/start`. It must not inherit an incompatible global value such as `model_reasoning_effort=max`.

Any reroute away from the selected exact model is an acceptance failure.

### Web-originated Codex X App routing

For HTTP/MCP Web-originated App calls, select the model from the current Native Codex `model/list` response and require a concrete `chatgpt-web/<version>` entry whose `id` exactly equals its executable `model` field. Send that exact ID on `thread/start` and `thread/fork`, then send both the exact ID and an effort supported by that entry on every `turn/start`. A family-only value such as `chatgpt-web` is not an executable model ID. A follow-up may steer an active turn only when its existing exact Web model and effort are verified; otherwise fail closed.

Every Web-originated Codex X App app-server process must be separate from the local-stdio process and use a fresh temporary `CODEX_HOME` containing only the opaque Native Codex `auth.json` needed for the built-in ChatGPT route. Set owner-only permissions, remove competing provider credential/base-URL environment overrides, and do not inherit the operator's provider catalog or endpoint configuration. Recreate the Web process when its Native auth source changes only after proving there is no active App request or Native turn; unknown thread status blocks replacement. Remove the temporary home only after process-group termination is verified; if auth, isolation, active-turn state, or stop state is unknown, fail closed and retain the home until stop can be proven. Local stdio App calls continue using the operator's normal Native Codex profile.

Catalog selection and sending explicit turn parameters do not prove entitlement or terminal execution identity. Keep requested, selected, and per-turn observed model evidence distinct; missing or mismatched terminal evidence leaves the operation unverified and never triggers a fallback model.

Core workspace-write tasks and Codex X App workspace-write turns share one SQLite resource-ownership boundary. Serialize claim checks with `BEGIN IMMEDIATE`; a Core task in running/cancelling/unknown state or an App claim in running/unknown state blocks another writer for the same canonical project root or alias. App turns acquire a durable claim before `turn/start`; timeouts, transport failures, restart, or unverified turn identity retain it as unknown. Release an App claim only after Native `thread/read` reports the thread idle and the exact claimed turn has an explicit terminal status. A cancellation request or idle thread without matching terminal-turn evidence does not release ownership. Unknown App writers remain visible in status and block Core/App writes until terminal evidence is recovered.

## Self-contained project boundary

Codex X Mode runtime MUST NOT require another ChatGPT Plugin, external Tool, external Skill, or external MCP package as a product dependency.

Everything required by Codex X Mode must live in and ship from this repository, including its Skill, MCP server/client surfaces, bridge, model-routing adapter, validation scripts, acceptance workflow, deployment metadata, and recovery documentation.

Platform primitives explicitly required by the architecture (Native Codex and ChatGPT Web transport) are allowed. Development/operator tools used to edit or inspect the repository are not runtime dependencies.

Do not solve a missing capability by adding another Plugin/Skill dependency. Implement or vendor the capability in this repository with appropriate provenance/license.

## Established evidence

- Native Codex `model/list` exposed exact `chatgpt-web/*` IDs.
- A direct Native Codex probe of `chatgpt-web/gpt-5.6-sol` with `effort=high` completed successfully.
- A prior Web-model task failed when `model_reasoning_effort=max` was inherited, proving effort must be selected from model metadata.
- v0.2.11 reached real `thread/start`/`turn/start`, but its Web-origin backend selection still uses `chatgpt_plan`.

The direct probe proves only the lower model-routing leg. It is not full E2E acceptance.

## Current state

This repository is bootstrapped from the exact v0.2.11 package archive previously deployed/tested.

v0.2.11 is a reference checkpoint only. It is NOT architecture acceptance because Web-origin dispatch still routes to `chatgpt_plan`.

## Next implementation

The next release must:

1. Route Web-origin dispatch through `codex_app_server`.
2. Discover/filter exact `chatgpt-web/*` models from Native Codex `model/list`.
3. Select the requested exact version without substitution.
4. Carry selected-model default/supported reasoning effort.
5. Send a valid effort explicitly on `turn/start`.
6. Capture requested/selected model and reroute evidence.
7. Preserve ambiguous/unknown task safety; never replay uncertain `turn/start`.
8. Add regression tests for backend, family filtering, version, effort, reroute handling, and follow-up invariants.
9. Prove a real harmless read-only E2E: ChatGPT Web -> Bridge -> Native Codex -> chatgpt-web/<version> -> ChatGPT Web.
10. Ship as a new semver release (`>=0.2.12`); never overwrite `0.2.11`.

## Acceptance gates

PASS requires evidence for all of:

- exact Web model versions exposed from live Native Codex catalog
- exact requested version selected
- backend = `codex_app_server`
- valid effort selected from model metadata and sent to Native Codex
- no reroute
- real read-only E2E terminal success
- targeted + full tests green
- package/build validation green
- release/runtime hashes recorded
- deployed version aligned across manifest, server, runtime, MCP config
- health/status good
- clean-install proof that no external Plugin/Skill/MCP package is required by Codex X Mode runtime

## Safety

- Never replay an ambiguous `turn/start`.
- Preserve unknown tasks for reconciliation evidence.
- Keep credentials/tokens/tunnel keys/runtime databases outside Git.
- Prefer smallest safe changes and versioned releases.
