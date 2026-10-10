# Codex X Mode v1.0

**Status: development planning / staging; not a production v1 release.**

Codex X Mode v1 is a clean-slate personal coding control plane: a single authenticated MCP gateway for ChatGPT Web/Mobile; Native Codex execution; optional Serena and Agent providers; durable runs/recovery; and a local/remote admin dashboard.

The code currently checked out on `main` is transitional implementation and is not yet v1 acceptance. The v1 design is authoritative; implementation and acceptance are separate gates.

- Design: [docs/v1/MASTER_DEVELOPMENT_PLAN.md](docs/v1/MASTER_DEVELOPMENT_PLAN.md)
- Engineering instructions: [AGENTS.md](AGENTS.md)
- Legacy archive: `archive/v0.2.23-legacy`
- Release approval: **NOT GRANTED**


## V1 G2/G3 development status

The [G2/G3 evidence](docs/v1/G2_G3_IMPLEMENTATION_EVIDENCE.md) records locally verified Policy/Config Kernel, unified MCP, and isolated Native Codex model catalog integration. The production release is not authorized.

New configurations default to schema v1, read-only MCP admission and Native model policy. Local offline configuration CLI: `config-show`, `config-preview --changes FILE --expected-revision SHA`, and `config-apply --changes FILE --expected-revision SHA --confirm`. Apply returns `requires_reload=true`; it does not restart or deploy the bridge.
