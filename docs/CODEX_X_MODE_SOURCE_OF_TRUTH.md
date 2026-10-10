# Codex X Mode — Architecture Authority

**Effective date:** 2026-10-10
**Product:** Codex X Mode v1.0
**Status:** V1 design and implementation authority

The authoritative architecture and development roadmap for v1 are:

- [v1 Master Development Plan](v1/MASTER_DEVELOPMENT_PLAN.md)
- [Repository Engineering Rules](../AGENTS.md)
- [G0 Recovery Evidence](v1/G0_RECOVERY_EVIDENCE.md)

This file previously contained v0.x-specific requirements. Those legacy constraints are **not** binding for the v1 implementation. Historical v0.2.23 source is preserved on `archive/v0.2.23-legacy`; historical v0.2.12 source remains in Git history.

## Invariants adopted for v1

1. Explicit identity and project-specific authorization for every privileged operation.
2. Durable execution records, safe retries, authoritative reconciliation and writer ownership.
3. Scoped filesystem access with path-containment and version/hash checks for edits.
4. Provider/model identities and terminal evidence recorded separately from acceptance decisions.
5. Separate MCP and Admin security boundaries, with no public raw shell, secrets or private provider API.
6. Real tests, negative security cases and Web/Mobile acceptance before releasing v1.

Unlike v0.x, v1 may replace tool counts, names, route topology, provider adapters, packaging internals and other implementation choices when justified by verified evidence.
