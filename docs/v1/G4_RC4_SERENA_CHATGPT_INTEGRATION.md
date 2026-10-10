# Codex X Mode v1.0 RC4 — G4 Serena Integration

Date: 2026-10-10 (Asia/Bangkok)
Status: Locally verified integration. NOT production release authorization.

## Exact Serena context

- Serena CLI 1.7.0; Serena MCP serverInfo 1.28.1.
- `serena start-mcp-server --project <allowlisted-root> --context chatgpt --transport stdio` was verified on MacBook.
- The real `chatgpt` context advertises 29 tools. Names and schemas captured in `server/bridge/serena_chatgpt_catalog.json`.
- G4 forces `--context chatgpt` on all Serena children. Mismatched private configuration is rejected.

## Tool admission and scope

- 13 approved read-only symbolic/file/memory tools, including symbols, references, diagnostics and memory reads.
- 11 guarded symbol/file/memory write tools. Admission requires explicit MCP capability, project allow_write, Serena allow_mutations, durable resource-wide writer claim, stable request_key, and for scoped source edits, exact expected SHA-256.
- 5 risky/unverified operations remain blocked through remote MCP: execute_shell_command, activate_project, get_current_config, onboarding, replace_in_files.
- Thus 24 of 29 catalogued operations have G4 gateway contracts; this is NOT 29/29 unconditional remote execution.
- No unrestricted shell or project switching is exposed. Native Shell/SSH execution remains a separate planned subsystem.

## Read/write isolation

- Serena initializes `.serena/project.yml`, local config and LSP cache even during read-only symbolic calls. Read-only Serena tools therefore run against a private bounded source-tree mirror, never directly on the original project path.
- Source mirrors reject symlinks and special files, and enforce a 3,000-file/32-MiB inspection cap. They are destroyed after the request.
- Write calls run on the original allowlisted project only after writer ownership is acquired and intent is durably recorded. Exact MCP reply, terminated process group, and bounded changed-file inventory are required before releasing a writer claim.
- Lost or ambiguous outcomes are recorded as unknown and never blindly replayed. Secrets from unrelated provider credentials are stripped from child environment.
- The Serena optional provider does not disable Core/Native Codex when unavailable.

## Config

- New private setup sets serena.enabled=false, serena.context=chatgpt, serena.allow_mutations=false, timeout_seconds=45.
- The operator must enable read-only Serena through offline config CAS/apply. Mutations additionally require exact mcp_policy.allowed_tools entries and allow_write projects.
- No hot remote configuration of Serena executable, permissions or project roots is allowed.

## Verification executed

- Actual temporary-project Serena calls succeeded: find_symbol, get_symbols_overview, find_referencing_symbols, get_diagnostics_for_file, list_memories.
- Verified original project unchanged during real read-only calls, including no `.serena` initialization/cache artifacts.
- Actual temporary-project Serena replace_content changed the intended source and recorded modified-file hashes; writer ownership was released.
- Negative tests cover project/path/symlink escape, stale/missing file hash, duplicate requests, unknown conflicting writer, failed provider and replay prevention, wrong context, optional-provider failure and credential isolation.
- Full Python suite: 191/191 PASS. Clean extracted suite: 191/191 PASS. Python AST: 39 files PASS. Bundle extraction byte parity: 48/48 PASS.
- Plugin/package RC4 versions: 1.0.0-rc.4 (Python 1.0.0rc4).
- Deterministic bundle SHA-256: 83fc86a290fe55add2e4257d0fc89c573b5b3f8f7d7aa66091351c7795641039.
- Isolated local artifact: /Users/luiapi/codex-x-mode-v1-rc4-staging-20261010.

## Release blockers

- Full 29/29 Serena remote tool exposure is not verified; 5 high-risk tools need independent security contracts.
- Live ChatGPT Web/Mobile Serena E2E, Cloudflare auth and production package parity have not been tested.
- G3 Native per-turn executed-model identity remains UNSUPPORTED by the installed Native App Server.
- No production service restart, config change, Plugin publishing, deployment or release GO in this work.
