# Codex X Mode Source of Truth

Date: 2026-10-06
Status: Authoritative architecture checkpoint for v0.2.22 Cloudflare Named Tunnel candidate

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

## HARD GUARDRAIL — Native Codex owns inference

This rule supersedes the SIWC / Subscription Sharing inference experiments from v0.2.13–v0.2.19 and MUST NOT be relaxed unless the user explicitly changes the architecture.

Native Codex owns authentication, account entitlement, model discovery, reasoning effort, `thread/start`, `turn/start`, terminal events, and model execution. Codex X Mode is the orchestrator/bridge only.

The core execution path MUST NOT:

- use SIWC / Subscription Sharing as the primary inference backend;
- use a Codex-X-Mode-owned custom Responses provider as the primary model executor;
- depend on `Subscription Sharing usage limit` for release acceptance or wait for that limit to reset;
- derive core runtime entitlement from partner-app/SIWC `/v1/models`;
- reintroduce Chromium, Playwright, `codex-chatgpt-web`, browser automation, or a second connector as the primary runtime path.

The model surface MUST be derived from `package model policy ∩ models actually exposed by Native Codex for the signed-in account`. Public `chatgpt-web/*` aliases remain package-owned and map deterministically to exact Native Codex model IDs.

Architecture PASS requires a new harmless Web-origin read-only task to run through Native-Codex-owned inference, complete successfully, show no reroute, and provide exact requested/selected/terminal model identity. SIWC/provider probes do not satisfy this gate.

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
packaged model registry ∩ Native Codex model/list for the signed-in account
```

This gives package-aware and account-aware behavior without hard-coding plan names or calling a partner-app catalog:

- an account for which Native Codex exposes Luna but not Sol sees/uses Luna;
- an account for which Native Codex exposes Sol sees/uses Sol;
- a future package can add a Pro alias in the registry without changing routing core; it appears only when Native Codex `model/list` contains the mapped model.

Generic `chatgpt-web` resolution is deterministic:

1. use `chatgpt_web_default_model` if that alias is packaged and visible in Native Codex;
2. otherwise choose the highest-priority packaged alias visible in Native Codex;
3. fail closed when Native Codex exposes no package-supported model.

Explicit aliases outside the package registry are rejected even if Native Codex exposes them.

At execution time the selected alias is resolved to one exact underlying model slug. Native Codex app-server model metadata is used to validate the execution model and reasoning-effort contract. Terminal completion is not accepted as exact-model evidence when terminal model identity is absent or a reroute is observed.

## Self-contained project boundary

Codex X Mode runtime MUST NOT require another ChatGPT Plugin, external Tool, external Skill, or external MCP package as a product dependency.

Everything required by Codex X Mode must live in and ship from this repository, including its Skill, MCP surfaces, bridge, model registry/routing logic, validation scripts, acceptance workflow, deployment metadata, and recovery documentation.

Platform primitives explicitly required by the architecture are allowed: ChatGPT account authentication as consumed by Native Codex, Native Codex app-server/model discovery, and the secure transport/tunnel used to reach the user-owned bridge. Codex X Mode itself must not source new-work entitlement from a separate SIWC/partner-app catalog.

Development/operator tools used to edit or inspect the repository are not runtime dependencies. Plugin Autopilot is development-only and must not be shipped as a Codex X Mode runtime dependency.

## Codex X App tool contract

Starting with v0.2.20, the package also owns a second MCP surface named `codex-x-app` and a bundled Skill named `codex-x-app-tool`. This surface is part of Codex X Mode itself; it MUST NOT require the proprietary/external `codex-app-tools@openai-bundled` package at runtime.

The protocol baseline for this release is **Codex CLI 0.160.1**. `codex-x-app` adapts directly to the Native Codex app-server protocol through one lazy managed `codex app-server --stdio` process owned by the Codex X Mode runtime. It does not require the app-server daemon/proxy path and exposes only operations backed by the generated 0.160.1 protocol:

- `list_threads`
- `read_thread`
- `create_thread`
- `fork_thread`
- `send_message_to_thread`
- `set_thread_title`
- `set_thread_archived`
- `wait_threads`

`automation_update`, `set_thread_pinned`, and `handoff_thread` are intentionally not exposed until a future Native Codex protocol supplies real primitives and regression coverage exists. The MCP endpoint is `/codex-x-app/mcp`; the existing Codex X Mode endpoint remains `/mcp`. Both surfaces reuse the same resident bridge and bearer-key boundary, so adding Codex X App does not require another browser, connector, or resident model runtime.

Starting with v0.2.21, `skills/codex-x-app-tool/agents/openai.yaml` explicitly declares a `dependencies.tools` MCP dependency with `value: codex-x-app` for both CHAT and CODEX products. This is package metadata only: the dependency resolves to the MCP server bundled by this plugin and MUST NOT be interpreted as permission to load `codex-app-tools@openai-bundled` or any other external runtime dependency. A conversation that loaded an older plugin release may retain a cached tool schema; fresh host/session discovery is required before claiming the newly bound MCP namespace is visible through `api_tool`.

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
- v0.2.18 clean-install/self-contained verification against exact commit `a1aba16409bd3784a2c3be4671817bdd962d5a17` passed from `git archive HEAD` in a fresh HOME with Python site packages disabled: full suite **99/99 PASS**, bundled MCP stdio exposed **13 tools / 2 resources / 2 prompts**, fresh SIWC failed closed as `authorization_required`, and the package generated exactly the three `chatgpt-web/*` Codex picker aliases without another Plugin/Skill/MCP package or browser automation runtime. Evidence: `docs/evidence/2026-10-06-v0.2.18-clean-install.md`.
- v0.2.19 fixes a Web-execution/desktop-picker configuration collision exposed by task `task_7096b4c71b0a4510a8f574f4bea760b2`: the v0.2.18 task failed before execution at `native_model_catalog` because the headless app-server inherited user-level `model_catalog_json` aliases while strict execution validation requested underlying `gpt-*` IDs. v0.2.19 gives each Web app-server an ephemeral isolated `CODEX_HOME`; a no-inference live diagnostic then exposed `gpt-5.6-sol`, excluded `chatgpt-web/5.6-sol`, and selected the exact underlying model. Focused Web suite **14/14 PASS**; first full suite **100/100 PASS**. Evidence: `docs/evidence/2026-10-06-v0.2.19-web-app-server-isolation.md`.
- v0.2.19 clean-install/self-contained verification against exact commit `dbbd0087f3074996ab90adb1f5d42d21dd5ff8b9` passed from `git archive HEAD` in a fresh HOME with Python site packages disabled: full suite **100/100 PASS**, bundled MCP stdio exposed **13 tools / 2 resources / 2 prompts**, fresh SIWC failed closed as `authorization_required`, and the package generated exactly `chatgpt-web/5.6-sol`, `chatgpt-web/5.6-luna`, and `chatgpt-web/5.5` without another Plugin/Skill/MCP package or browser automation runtime. Private plugin surface was aligned to v0.2.19 before one new live Web task. That task (`task_6edcc287478246a9acc2a5df25662213`) selected `chatgpt-web/5.6-sol` -> `gpt-5.6-sol` with no reroute but ended fail-closed as `unknown` because the provider returned the Subscription Sharing usage-limit error before terminal model identity was available. No retry was performed. Evidence: `docs/evidence/2026-10-06-v0.2.19-clean-install-live-e2e.md`.

These historical checkpoints document how the architecture evolved. For all new work, the v0.2.20+ Native-Codex-owned contract supersedes the earlier SIWC/account-catalog and custom-provider execution designs.

## Current state

v0.2.22 is the active working-tree candidate on branch `feat/headless-web-route`. It adds backward-compatible public MCP aliases `/mode/mcp` and `/app/mcp`, while preserving the loopback `/mcp` and `/codex-x-app/mcp` endpoints used by the Plugin-owned MCP declarations. The full runtime/package suite is **111/111 PASS**, compile and `git diff --check` pass, and the deterministic runtime bundle double-build SHA-256 is `db2743b9372f88bc8d8039f0a7107913055acce3260f361f2e95bcd19bbc7790`.

The Mac LaunchAgent runs `$HOME/.local/share/codex-x-mode/releases/0.2.22/server` and health reports `up`. A Cloudflare Named Tunnel named `codex-x-mode` routes `codex-x-mode.lott0.online` to loopback with an ingress allowlist for only `/mode/mcp` and `/app/mcp`. Canonical-host boundary verification returns 404 for the hostname root and `/v1/*`, while unauthenticated POSTs to both MCP aliases return 401. The runtime package exposes `codex-x-mode 0.2.22` with 13 tools and `codex-x-app 0.2.22` with 8 tools under the same bearer-auth boundary; authenticated hosted-connector verification remains part of the Plugin migration gate.

The Plugin package continues to own both MCP declarations through `mcp.json` / compatibility `.mcp.json`; users must not add duplicate MCP entries to user-level Codex configuration. Remote authentication remains fail-closed and the private bridge bearer is not shipped in Plugin files.

The hosted private Plugin is still the previous v0.2.21 release `pluginrel_6ac4280598ac8191a6218291e7c92400` while the v0.2.22 candidate is being completed. Its existing `.app.json` binding therefore must not be treated as evidence that ChatGPT Web is already using the new Cloudflare endpoints. A supported authenticated remote MCP binding is required before publishing/migrating the hosted connection; the bridge must not be made anonymous and the private static bearer must not be embedded in Plugin metadata.

The hosted v0.2.22 archive/file handoff itself has been proven independently: GitHub Actions transfers return reusable connector files, and the hosted ZIPs read back with v0.2.22 manifests, canonical `/mode/mcp` + `/app/mcp` hosted endpoints, loopback `.mcp.json`, and the bundled operational Skills. The latest retry narrowed the remaining blocker to the Plugin Creator write binding: read-only `get_plugin_metadata` succeeds and confirms hosted v0.2.21 / `pluginrel_6ac4280598ac8191a6218291e7c92400`, while `update_plugin` still resolves as `Resource not found` immediately after rediscovery. The verified latest handoff used GitHub Actions run `37467361990`, artifact `11415572157`; the artifact wrapper digest was `cbc1ba3f74f6e3d603a13ae2a6090e8f6bf53071dcf26ef7ec8a8d5f0d24a29c` and the extracted plugin ZIP SHA-256 was `c9513a9ef9edd04608bf996ef57c4551229ee1fca6d073d98e80b0afe88d0cd9`. No mutation occurred, so v0.2.21 remains authoritative until a guarded Plugin Creator update and release read-back succeed.

`live_codex_verified` remains `false`. No successful terminal inference with exact observed model identity has been proven under the corrected Native-Codex-owned path yet, and Mobile E2E remains unverified.

## Acceptance gates

Full architecture PASS requires evidence for all of:

- package registry contains only intended public aliases;
- visible Web catalog equals package policy ∩ Native Codex `model/list` for the signed-in account;
- generic `chatgpt-web` resolves deterministically to a packaged model visible to Native Codex;
- exact underlying model slug is pinned without substitution;
- Native Codex owns authentication, entitlement/model discovery, thread/turn execution, and inference for new work;
- SIWC, Subscription Sharing, `chatgpt_plan`, and custom Responses-provider routing are never selected for new work;
- valid reasoning effort is selected from Native Codex execution-model metadata;
- no reroute;
- real harmless read-only Web -> Bridge -> Native Codex -> ChatGPT backend terminal success with exact terminal model identity;
- targeted + full tests green;
- package/build validation green;
- release/runtime hashes recorded;
- deployed version aligned across manifest, server, runtime, both MCP surfaces, Skill metadata, and hosted private plugin;
- `codex-x-app-tool` resolves to bundled MCP identifier `codex-x-app` and fresh host/session discovery exposes the intended tool surface;
- health/status good;
- clean-install proof that no external Plugin/Skill/MCP package is required by the runtime;
- actual ChatGPT Mobile smoke before Mobile is marked verified.

## Safety

- Never replay an ambiguous `turn/start`.
- Preserve unknown tasks for reconciliation evidence.
- Keep credentials/tokens/tunnel keys/runtime databases outside Git.
- Never expose account tokens in logs or evidence.
- Prefer smallest safe changes and versioned releases.
