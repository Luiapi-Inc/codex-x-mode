# Codex X Mode v1 — G2/G3 Implementation Evidence

Date: 2026-10-10 (Asia/Bangkok)
Status: LOCAL DEVELOPMENT VERIFIED. Not a v1 release, no production deployment.

## G2 — Policy and Configuration Kernel

- server/bridge/policy.py: per-invocation tool authorization, exact allowlist, read-only mode, fail-closed validation; status/projects resources follow the policy.
- server/bridge/config_kernel.py: owner-only private config descriptor reads, secret-redacted snapshots, revision SHA-256, CAS, schema validation, private backup history, atomic fsync+replace, project writer guard.
- CLI config-show, config-preview, config-apply --confirm: local/offline; apply refuses an active runtime lock and returns requires_reload=true.
- Fresh v1 setup defaults to config_schema_version=1, mcp_policy=read-only, web_model_policy=native.
- Unversioned legacy configurations preserve previous capabilities for migration; never treat them as the v1 security baseline.

## G3 — Unified MCP and Native Codex

- Existing authenticated /mode/mcp exposes Core and namespaced App tools through one connection; policies revalidate every tools/call instead of relying on tools/list.
- Native v1 model resolution uses exact Native executable IDs and effort metadata from an isolated auth-only Codex home; legacy prefixed model mode is retained only for transition.
- Source revalidates exact model and effort before Native thread and turn execution; no model/provider silent fallback.
- Real loopback HTTP one-connection smoke used Native Codex CLI 0.162.0-alpha.17.2 with a temporary private database, no inference.
- Observed 10 read-only tool registrations, 0 mutation tools; Core catalog HTTP 200 with 7 exact models, App thread list HTTP 200 with 0 authorized threads.
- Operator profile had 11 models including 4 chatgpt-web aliases; isolated auth-only profile had 7 exact models and no such aliases. Old code's prefix filter caused an empty catalog.

## Validation

- TDD RED observed for policy, Config Kernel import, and Native model policy selection.
- Full Python suite: 149 tests PASS, ~21.7 seconds.
- Python AST parsing: 27 files PASS.
- Deterministic runtime bundle SHA-256: 4b11b29f4c882c8d2cd98826461b03c4dfdd028e909d251948869769ff33609b.
- Git diff --check PASS; known HTTPError ResourceWarning outputs from tests did not fail suite.

## Outstanding release gates

- Real Web-origin Native turn terminal inference with matching requested/selected/observed model remains UNVERIFIED.
- Actual ChatGPT Web/Mobile E2E, remote tunnel authorization, Cloudflare policy, online config reload/rollback, Dashboard/Admin identity not tested.
- Provider completion in fixtures does not establish production entitlement or acceptance.
- No production restart/deployment, Plugin publication, Cloudflare change or release authorization.

## Next

Finish independent security/contract review for G2 Admin API prior to exposing privileged configuration actions. G3 requires real harmless Native read-only turn plus ChatGPT Web/Mobile one-connector E2E to graduate from local integration to architecture PASS.\n