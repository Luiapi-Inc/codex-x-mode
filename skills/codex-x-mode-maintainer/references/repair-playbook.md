# Codex X Mode Repair Playbook

Use this checklist after reading the live `AGENTS.md` and `docs/CODEX_X_MODE_SOURCE_OF_TRUTH.md`. Current source and runtime evidence supersede examples here.

## Boundary matrix

| Symptom | Prove first | Likely owner | Safe repair direction |
|---|---|---|---|
| Source/package versions disagree | Read version consistency sources and tests | Repository source | Align versioned source, then rebuild and retest |
| Runtime reports an older version | Compare source artifact hash/version with deployed release | Deployment | Build validated source and redeploy only the intended release |
| `/mcp` unavailable | Check resident bridge health, listener, auth-safe client status | Bridge/runtime or tunnel | Repair runtime config/process; do not bypass bearer boundary |
| `/codex-x-app/mcp` unavailable | Confirm source declaration, runtime version, endpoint discovery | Package/runtime | Align bundled Codex X App surface and redeploy |
| `codex-x-app` tools absent in ChatGPT | Verify hosted Skill dependency and fresh-session discovery | Plugin metadata/discovery | Fix bundled binding or publish aligned plugin; use a fresh host/session |
| Old 13-tool/SIWC-era descriptions appear | Compare fresh discovery with current runtime | Conversation cache | Do not redesign source from cached schema; verify in a fresh session |
| `list_threads` fails | Probe Native Codex app-server compatibility without inference | Native adapter/runtime | Fix protocol adapter or supported-version contract |
| Expected `chatgpt-web/*` alias missing | Compare package registry with Native Codex `model/list` | Model policy/entitlement | Expose only their intersection; never synthesize entitlement |
| Explicit alias reroutes | Inspect alias mapping, selected model, terminal model evidence | Routing | Fail closed and fix deterministic mapping/validation |
| Runtime health down after deploy | Inspect current release path/service state/logs without secrets | Deployment/runtime | Roll forward with the validated package or restore the last known-good authorized release |
| HTTP 401/403 | Verify credential presence/permissions without printing secret values | Auth boundary | Repair credential wiring/permissions; never weaken authentication |
| Tunnel unreachable | Separate local bridge health from remote reachability | Secure transport | Repair the configured tunnel; do not add a second connector |
| Duplicate owner/lock error | Identify the resident owner before starting anything | Lifecycle | Reuse/restart the intended resident service; do not spawn a competing owner |
| Unknown task/turn outcome | Read canonical task/turn state and evidence | Lifecycle/recovery | Reconcile first; never replay an ambiguous mutation |

## Investigation order

1. Capture branch, SHA, worktree state, and source version.
2. Read current architecture and release evidence.
3. Establish whether the failure exists in source or only after packaging.
4. Establish deployed runtime identity and health.
5. Verify local/authenticated MCP discovery.
6. Verify tunnel reachability separately from MCP semantics.
7. Verify hosted plugin release/bindings.
8. Use fresh session discovery for schema visibility.
9. Test Native Codex integration without inference where possible.
10. Use one harmless live read-only inference only after every preceding gate passes and the task authorizes it.

## Invariants worth testing

For routing or MCP behavior changes, keep regression coverage that proves:

- new Web work selects the Native Codex app-server path;
- SIWC/custom-provider routing is rejected for new work;
- package model aliases are intersected with Native Codex models;
- explicit unsupported aliases fail closed;
- requested/selected/terminal model identity must match;
- missing terminal model identity cannot become PASS;
- request/idempotency keys cannot mutate a different request;
- ambiguous/unknown execution is not replayed;
- both MCP surfaces expose only their supported bundled tools;
- `codex-x-app` does not require an external Codex App plugin/package.

## Evidence discipline

Prefer exact command output, API/tool result, SHA, release identifier, endpoint identity, and deterministic hash. Never treat a queued task, process start, cached schema, historical PASS, or memory note as current acceptance evidence.
