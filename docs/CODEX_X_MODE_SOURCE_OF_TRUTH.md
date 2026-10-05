# Codex X Mode Source of Truth

Date: 2026-10-05
Status: Authoritative architecture checkpoint for v0.2.18

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
- v0.2.15 binds the existing Secure MCP Tunnel `tunnel_6ac183e0dc2881918df4195ad20fe4b3` to the ChatGPT developer-mode app whose listing URL ID is `plugin_asdk_app_6ac3bd2aa6208191b7bdd51602f548a2` and whose `.app.json` connector ID is `asdk_app_6ac3bd2aa6208191b7bdd51602f548a2`, while preserving the outer Codex X Mode plugin identity.
- ChatGPT live discovery on that binding reports all 13 MCP tools (`Read 7` + `Write 6`).
- v0.2.15 source/full suite: 95/95 PASS; deterministic runtime bundle SHA-256: `d158b2a379cc2e251a790c1104ce777478f0e0fe767c2bb9f634874f11c790a5`.
- v0.2.16 configures Codex Desktop to reuse the live loopback HTTP MCP bridge through `.mcp.json` with `CODEX_X_MCP_TOKEN`, avoiding a second stdio owner and preserving fail-closed bearer authentication.
- v0.2.16 full suite: 96/96 PASS; deterministic runtime bundle SHA-256: `d6a77deaaf51f6d68503e94e204cd0e65c9ab0fa3f7f829104b1405f7d1c6a06`. Account release read-back showed the compatibility `.mcp.json` was still derived from the authoritative root stdio `mcp.json`, so the Codex HTTP MCP change was not effective in that release.
- v0.2.17 moves the effective plugin MCP identity to authoritative root `mcp.json` as credential-free loopback Streamable HTTP and keeps bearer-token handling client-side; full suite: 96/96 PASS; deterministic bundle SHA-256: `b365efdaa74cd3e3d9924e7319b8d5e0562adf151c5c460d93ff57476dc3bc83`.
- v0.2.17 account release `pluginrel_6ac3cb1642788191bb13a918a0a10cee` preserves outer plugin `plugins_6ac10a6f500881918a222dd8b7693752` as USER/PRIVATE. Read-back confirms root `mcp.json` is Streamable HTTP at `http://127.0.0.1:8240/mcp`; the Mac LaunchAgent and authenticated MCP client report runtime v0.2.17, 13 tools, and transport available.
- v0.2.18 adds a Codex-native `model_catalog_json` generator that clones installed Codex 0.156.1 bundled metadata for the packaged `chatgpt-web/*` aliases and wires the existing private Responses provider as `Codex X Mode`; full suite: 99/99 PASS; deterministic runtime bundle SHA-256: `047b4d5cb14d332cf791d1a975d587fb68df543c18df4d0d38de1f2709d36e51`.
- v0.2.18 local Codex smoke selected `chatgpt-web/5.6-luna` with provider `custom_gpt_bridge`, produced a Codex X Mode backend turn whose turn metadata named the same alias, and returned the exact completed text to `codex exec`. Account release `pluginrel_6ac3d8b35f048191909dc62096a9048c` preserves outer plugin identity and USER/PRIVATE audience; deployed runtime reports v0.2.18 and all 13 MCP tools.

These prove package logic, the account-catalog leg, and that the headless route reaches the provider. They do not yet prove a successful full deployed Web or Mobile terminal task.

## Current state

v0.2.18 is published and deployed from branch `feat/headless-web-route`. It keeps the v0.2.17 authoritative loopback Streamable HTTP MCP connection and adds Codex-native host model selection through a generated `model_catalog_json` plus the existing private Responses provider. User-level Codex config now selects `custom_gpt_bridge`, points at the generated catalog, and keeps the provider credential outside the config through `CODEX_BRIDGE_PROVIDER_KEY`. Global Codex catalog read-back exposes exactly the three packaged aliases, and a local read-only Codex smoke completed through the provider/backend-turn path with `chatgpt-web/5.6-luna`. Account read-back confirms plugin v0.2.18 release `pluginrel_6ac3d8b35f048191909dc62096a9048c`, USER/PRIVATE audience, the new catalog source, and the 77,797-byte runtime bundle. The Mac LaunchAgent runs the v0.2.18 runtime and authenticated MCP transport reports all 13 tools with status `up`. Codex Desktop must be fully restarted to reload startup provider/catalog configuration. `live_codex_verified` remains false because the local provider smoke is not the stricter real Web-originated terminal task with exact underlying terminal model identity and no reroute.

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
