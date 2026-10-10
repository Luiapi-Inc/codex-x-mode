# Codex X Mode v1 — Repository Instructions

## Authority and workflow

- v1 development uses `main` and `docs/v1/MASTER_DEVELOPMENT_PLAN.md` as the current design specification.
- The `archive/v0.2.23-legacy` branch is historical reference only. v0.x APIs, tool counts, routes and model mappings are not mandatory v1 behavior.
- Work in this order: Requirement → Plan → Implementation → Tests → Evidence → Review.
- Inspect source and active worktrees before changes; preserve user WIP.
- Keep changes narrow, test behavior changes, and avoid speculative refactors.

## Runtime and security

- Expose a single, curated ChatGPT MCP gateway. Keep the Admin API separate from MCP permissions.
- Native Codex controls Native Codex inference; optional providers cannot impersonate it.
- Route selection must not silently substitute models or providers.
- Authenticate and authorize every mutation server-side. Enforce project allowlists and canonical path containment.
- Before non-idempotent execution, persist intent and acquire appropriate resource ownership transactionally.
- Unknown execution after a lost response must be reconciled; do not replay, forcibly release claims, or overwrite shared workspace state on assumption.
- Preserve durable tasks, audit trails, secrets isolation, exact execution receipts, and acceptance evidence.
- A provider reporting `completed` is not proof that the accepted work contract passed.
- Never expose secrets, unrestricted shell/filesystem tools, internal provider APIs or state DB publicly.

## Development and release boundaries

- Implementation uses isolated tests/worktrees for risky work, and preserves any active workspace writer claims.
- Do not deploy, restart production services, alter Cloudflare, publish Plugins, delete unrelated files or migrate live databases without explicit authorization.
- Native tool/model inventory and API support must be verified against the active installed versions.
- Every milestone needs actual executed test evidence. Unit tests are not a substitute for Web/Mobile remote E2E.
- Never claim a release is complete without independent acceptance and an explicit GO.
