# Codex X Mode Source of Truth

Date: 2026-10-05
Status: Authoritative architecture checkpoint for v0.2.14

## Required execution path

Web-originated dispatch MUST use the headless path:

```text
ChatGPT Web / Mobile
  -> Codex X Mode connector / MCP transport
  -> secure tunnel
  -> Codex X Mode bridge
  -> Native Codex app-server
  -> exact underlying ChatGPT model
  -> terminal result back through Codex X Mode
```

Do not use Chromium, Playwright, DOM automation, a browser profile, browser daemon, or a second connector in the primary path.

Do not route new Web-originated work through legacy `chatgpt_plan`. That backend exists only for reconciliation of persisted pre-v0.2.12 tasks.

## Headless-first runtime contract

Codex X Mode should remain idle-light. The resident runtime should be limited to the bridge and tunnel/client process required for remote access. Native Codex child processes should be started on demand and terminated when the task finishes or reaches a terminal/unknown state.

Browser compatibility may exist later only as an optional non-default adapter. It must never be required for normal Web or Mobile execution.

## Model/version contract

`chatgpt-web` is the public model family exposed by Codex X Mode. Concrete aliases are owned by the package, not copied blindly from every model visible to the account.

Current packaged registry:

- `chatgpt-web/5.5` -> `gpt-5.5`
- `chatgpt-web/5.6-luna` -> `gpt-5.6-luna`
- `chatgpt-web/5.6-sol` -> `gpt-5.6-sol`

Visible models are the intersection:

```text
packaged model registry ∩ authorized ChatGPT account catalog
```

This gives package-aware and entitlement-aware behavior without hard-coding plan names:

- a Free/Go-like account that has Luna but not Sol sees/uses Luna;
- a paid account that has Sol sees/uses Sol;
- a future package can add a Pro alias in the registry without changing routing core; it appears only when the authorized account catalog also contains the mapped model.

Generic `chatgpt-web` resolution is deterministic:

1. use `chatgpt_web_default_model` if that alias is both packaged and entitled;
2. otherwise choose the highest-priority packaged alias that is entitled;
3. fail closed when the account has no package-supported model.

Explicit aliases outside the package registry are rejected even if the upstream account catalog contains them.

At execution time the selected alias is resolved to one exact underlying model slug. Native Codex app-server model metadata is used to validate the execution model and reasoning-effort contract. Terminal completion is not accepted as exact-model evidence when terminal model identity is absent or a reroute is observed.

## Self-contained project boundary

Codex X Mode runtime MUST NOT require another ChatGPT Plugin, external Tool, external Skill, or external MCP package as a product dependency.

Everything required by Codex X Mode must live in and ship from this repository, including its Skill, MCP surfaces, bridge, model registry/routing logic, validation scripts, acceptance workflow, deployment metadata, and recovery documentation.

Platform primitives explicitly required by the architecture are allowed: ChatGPT account authorization/catalog access, Native Codex app-server, and the secure transport/tunnel used to reach the user-owned bridge.

Development/operator tools used to edit or inspect the repository are not runtime dependencies. Plugin Autopilot is development-only and must not be shipped as a Codex X Mode runtime dependency.

## Web and Mobile contract

Web and Mobile use the same remote MCP/tunnel path. Mobile does not run Python, Node, Codex, Chromium, or tunnel-client locally; it is only the ChatGPT client surface. The execution host runs the bridge and Native Codex.

Mobile availability therefore depends on the execution host/tunnel being reachable. A Mac-hosted deployment requires the Mac to be awake and online. A future always-on host may move the same runtime to a small server/VPS without changing the Mobile client contract.

Mobile must not be marked verified until an actual ChatGPT Mobile smoke test completes through the deployed connector. Architectural compatibility alone is not evidence of Mobile acceptance.

## Established evidence

- Headless Native Codex + SIWC/account authorization returned live account model catalog data with no browser process required.
- Live read-only v0.2.13 probe exposed only packaged+entitled aliases: `chatgpt-web/5.5`, `chatgpt-web/5.6-luna`, and `chatgpt-web/5.6-sol`.
- The same live probe resolved generic `chatgpt-web` to `chatgpt-web/5.6-sol` on the current entitled account, mapping to `gpt-5.6-sol` with `browser_required=false`.
- Regression coverage proves Free-like catalog fallback to Luna, paid/default behavior for Sol, future package registry extension for Pro without routing-core changes, exact alias rejection, dedup/retry invariants, and fail-closed terminal identity.
- v0.2.13 package/full test suite: 94/94 PASS after deterministic runtime bundle rebuild.
- The deployed v0.2.13 headless acceptance attempt reached `thread/start` and `turn/start` with `chatgpt-web/5.6-sol` -> `gpt-5.6-sol`, but the provider returned a terminal failure because the ChatGPT user had reached the Subscription Sharing usage limit. No inference replay was performed.
- v0.2.14 adds sanitized `terminal_error` persistence for failed turns while preserving fail-closed model verification when terminal model identity is unavailable.
- v0.2.14 focused Web dispatch tests: 13/13 PASS; full package/runtime suite: 95/95 PASS.
- v0.2.14 deterministic runtime bundle SHA-256: `eac45d7368483bef2a17d4d4b86d2722dd47c805e054a734e0c28700784a4933`.

These prove package logic, the account-catalog leg, and that the headless route reaches the provider. They do not yet prove a successful full deployed Web or Mobile terminal task.

## Current state

v0.2.14 source is implemented on branch `feat/headless-web-route` and remains `live_codex_verified=false` until a real deployed terminal task succeeds with exact model identity and no reroute. The currently deployed runtime/plugin remain v0.2.13 until this v0.2.14 checkpoint is committed, pushed, deployed, and published.

The previous v0.2.12 Native-Codex-only catalog assumption is superseded by the v0.2.13 package-registry + account-catalog design because raw Native Codex did not expose `chatgpt-web/*` aliases when the external route/browser helper was absent.

## Acceptance gates

Full architecture PASS requires evidence for all of:

- package registry contains only intended public aliases;
- visible catalog equals package policy ∩ authorized account catalog;
- generic `chatgpt-web` resolves deterministically to an entitled packaged alias;
- exact underlying model slug is pinned without substitution;
- valid reasoning effort is selected from execution-model metadata and sent to Native Codex;
- no reroute;
- real harmless read-only Web -> Bridge -> Native Codex -> ChatGPT backend terminal success;
- targeted + full tests green;
- package/build validation green;
- release/runtime hashes recorded;
- deployed version aligned across manifest, server, runtime, MCP config;
- health/status good;
- clean-install proof that no external Plugin/Skill/MCP package is required by the runtime;
- actual ChatGPT Mobile smoke before Mobile is marked verified.

## Safety

- Never replay an ambiguous `turn/start`.
- Preserve unknown tasks for reconciliation evidence.
- Keep credentials/tokens/tunnel keys/runtime databases outside Git.
- Never expose account tokens in logs or evidence.
- Prefer smallest safe changes and versioned releases.
