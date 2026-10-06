---
name: codex-x-mode-maintainer
description: Diagnose, repair, validate, and recover the Codex X Mode Plugin/MCP stack end-to-end. Use when Codex X Mode status, discovery, bundled MCP tools, bridge/runtime, secure tunnel, hosted plugin metadata, Codex X App binding, model routing, deployment, or fresh-session visibility is broken, stale, inconsistent, or needs an authorized automatic fix/redeploy. Also use for post-change verification of Codex X Mode Plugin/MCP health.
---

# Codex X Mode Maintainer

Maintain Codex X Mode from its own source of truth. Work from the active checked-out `Luiapi-Inc/Codex-X-Mode` repository; when a conventional local checkout exists under `$HOME/codex-x-mode`, verify its identity before any mutation.

## Establish authority first

1. Read the repository `AGENTS.md`.
2. Read `docs/CODEX_X_MODE_SOURCE_OF_TRUTH.md` before changing routing, MCP, release, deployment, or acceptance behavior.
3. Inspect `git status --short --branch`, exact HEAD, and current branch. Preserve unrelated user changes.
4. Treat repository source and current authoritative evidence as truth. Treat installed plugin caches, old conversations, generated bundles, and memories as observations that can be stale.
5. Preserve exact user constraints, especially read-only requests. A diagnose/status request does not authorize edits or deployment. An explicit fix/repair request authorizes the smallest necessary source/config change and its required validation. Deploy/redeploy only when requested or when the authorized full-auto repair cannot be completed without aligning the deployed target.

Read [repair-playbook.md](references/repair-playbook.md) when diagnosing a failure or alignment mismatch.

## Preserve the architecture

Keep the primary path:

```text
ChatGPT Web / Mobile
  -> Codex X Mode connector / MCP
  -> secure tunnel
  -> Codex X Mode bridge
  -> Native Codex app-server
  -> exact underlying ChatGPT model
  -> result through Codex X Mode
```

Native Codex owns authentication, account entitlement/model discovery, reasoning metadata, thread/turn execution, terminal events, and inference.

Never repair the core path by introducing or restoring:

- SIWC / Subscription Sharing as the inference backend;
- `chatgpt_plan` or a Codex-X-Mode-owned custom Responses provider for new work;
- Chromium, Playwright, DOM/profile/browser-daemon automation;
- `codex-chatgpt-web`, a second connector, or an external Plugin/Skill/MCP package as a runtime dependency;
- silent substitution of a requested `chatgpt-web/*` alias or its exact underlying Native Codex model.

Keep `codex-x-app` backed directly by the bundled Native Codex app-server adapter. Do not substitute `codex-app-tools@openai-bundled`.

## Run the automatic maintenance loop

For an authorized repair, continue through the loop without asking the user to choose Backend versus Dispatch or to approve routine reversible steps already inside scope.

### 1. Discover

- Verify repository, branch, HEAD, worktree state, package version, and relevant manifests.
- Inspect source configuration for both MCP surfaces: `/mcp` and `/codex-x-app/mcp`.
- Inspect the deployed bridge/runtime, tunnel reachability, hosted plugin metadata, Skill/MCP binding, and fresh-session discovery only where the available environment can prove them.
- Prefer read-only health/status/list operations before mutations.
- Do not expose bearer tokens, account tokens, leases, private config, or runtime databases.

### 2. Diagnose

Classify the first failing boundary rather than patching the visible symptom:

`source -> package -> deployed runtime -> tunnel -> hosted plugin -> fresh host/session discovery -> Native Codex`.

Compare version, tool counts/names, endpoint identity, model policy, and binding metadata at adjacent boundaries. Detect stale conversation/plugin schema separately from a real deployment mismatch.

### 3. Repair

- Change the smallest authoritative source/configuration that owns the failure.
- Never edit installed/cache/generated copies as the source fix.
- Preserve backward compatibility, idempotency, unknown-task safety, and fail-closed model verification.
- Add or update regression tests for behavior changes.
- Keep machine-local secrets/state out of Git.
- If a release is required, align semver across every versioned source identified by repository tests/contracts before packaging.
- Do not overwrite historical evidence to manufacture alignment.

### 4. Validate source

Run the smallest focused tests first. Then run the repository-required compile/full suite and `git diff --check` when behavior changed. Use current repository guidance for exact commands rather than hard-coding historical counts.

Do not claim PASS from an earlier release or memory.

### 5. Align runtime and plugin

When deployment is within the authorized repair scope:

- build/package from the validated source checkpoint;
- validate the actual artifact, not only the working tree;
- deploy/restart only the affected Codex X Mode runtime components;
- preserve the idle-light design: resident bridge/tunnel only, Native Codex children on demand;
- align runtime, both MCP surfaces, hosted private plugin metadata, bundled Skills, and model registry to the intended release;
- verify health after restart/redeploy.

Avoid starting a second conflicting bridge/MCP owner when the resident runtime already owns the private configuration.

### 6. Verify end-to-end

Verify progressively:

1. local/source tests and version consistency;
2. runtime health;
3. authenticated MCP discovery for the intended endpoint(s);
4. `codex-x-app` read-only probe when relevant;
5. fresh host/session Plugin/MCP discovery when stale schema is suspected;
6. exactly one new harmless read-only Web-origin task only when live inference is in scope and all preceding gates pass;
7. Mobile only from an actual Mobile smoke.

For live inference acceptance, require exact requested alias -> selected Native Codex model -> terminal observed model, successful terminal completion, and no reroute. Never replay an ambiguous or unknown `turn/start`.

If the environment cannot execute a gate, report it as NOT RUN or BLOCKED instead of inferring success.

## Recovery rules

- Stop mutation at the first unknown outcome and reconcile state before retrying.
- Reuse idempotency/request keys only for the identical mutation and payload.
- Preserve unknown tasks and evidence; do not convert uncertainty into a retry.
- A stale conversation schema is not evidence that source or deployed MCP is stale. Require fresh discovery before changing architecture.
- Do not delete caches or credentials merely to make discovery appear clean.
- If a required authority, credential, account action, or protected deployment step is unavailable, stop at that boundary and report the exact blocker.

## Evidence and completion

Finish with concise evidence:

- repository path, branch, and exact SHA;
- root cause and repaired ownership boundary;
- files changed;
- focused/full test and compile results actually executed;
- source/package/runtime/plugin versions actually observed;
- MCP endpoint/tool discovery actually observed;
- deployment/restart result if performed;
- Web/Mobile acceptance status as PASS, FAIL, NOT RUN, or BLOCKED;
- remaining risks or gaps.

Do not call the task complete until the relevant repository completion gates pass. Do not commit, push, publish, merge, or mutate external infrastructure unless that action is part of the user's authorized request.
