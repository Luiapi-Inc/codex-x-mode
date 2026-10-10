# Codex X Mode v1.0 RC5 — Serena connector and privileged-tool boundary

**Date:** 2026-10-10 (Asia/Bangkok)
**Status:** Local authenticated MCP integration verified. Production / Web/Mobile remote E2E: **NOT VERIFIED**. Release **NO-GO**.

## What was implemented

1. New `codex_x_serena_preview_replace_in_files` (read-only) proxy on the unified MCP Gateway when Serena is enabled. It accepts a single allowlisted `project_id`, existing project-relative file/directory target, bounded literal needle/replacement and no uncontrolled provider parameters.
2. The adapter **forces** Serena's `replace_in_files` to `dry_run=true`, with `max_answer_chars=65536`. Execution runs exclusively in an ephemeral project mirror. Even if the upstream provider ignores `dry_run`, it cannot change the original project through this adapter.
3. No raw `codex_x_serena_replace_in_files` mutation tool is exposed to MCP. Explicitly granting that native tool name does **not** enable its execution.
4. Serena subprocess security: provider HOME, XDG_CONFIG_HOME, XDG_CACHE_HOME and XDG_DATA_HOME are temporary directories, not the operator's credential storage. The child working directory is the explicitly selected project mirror for reads, or the allowlisted project for controlled writes. Unrelated API key environment variables remain filtered.
5. Core/App tool registry and Server authentication remain unchanged. The additional preview tool is conditional and read-only; the 29-tool native Serena catalog remains correctly represented as 13 approved native reads, 11 guarded source/memory operations, and 5 withheld privileged operations.

## Real executed verification

The authenticated **loopback** HTTP `/mode/mcp` route was exercised against a real installed Serena provider using `context=chatgpt` in a temporary project and a temporary private SQLite database.

| Check | Observed |
|---|---|
| Anonymous `tools/list` | HTTP 401 |
| Authenticated `tools/list` | HTTP 200; 25 registered tools |
| `codex_x_serena_find_symbol` | HTTP 200, successful response |
| `codex_x_serena_preview_replace_in_files` | HTTP 200, `dry_run=true`, `applied=false` |
| `codex_x_serena_execute_shell_command` | 403 denied before provider execution |
| Original project file hashes | Unchanged |
| Serena child HOME/CWD | Disposable private HOME; CWD restricted by read/write mode |

Separate actual Serena direct-adapter probes confirmed that `find_symbol` and `replace_in_files(dry_run=true)` still succeed after HOME/CWD isolation, and that two test source files remain byte-identical.

## Five withheld upstream operations: explicit security contracts

| Upstream operation | Policy / replacement | Release gate |
|---|---|---|
| `execute_shell_command` | **Denied** via MCP. Shell/SSH belongs in a separately authorized native persistent-PTY subsystem with per-project allowlists, explicit operator grants, lifecycle/exit receipts, secret isolation, session cancel/kill and audit; never proxy unrestricted shell just by client tool hints. | Native PTY/SSH architecture, tests and scoped authZ. |
| `activate_project` | **Denied**; each safe Serena call already binds `project_id` to an allowlisted root. | Any future project selection must be a local transaction, not arbitrary path activation. |
| `get_current_config` | **Denied**; raw Serena config may expose local user paths/settings. | Redacted read-only settings projection, identity/visibility filtering, negative leak tests. |
| `onboarding` | **Denied** online; initialization writes project config/cache. | Offline operator-approved onboarding into staging, diff/approval, path verification, reload/rollback. |
| `replace_in_files` | **Denied** for direct mutation. New bounded preview offers safe dry-run on mirror, but does not apply edits. | Explicit expected hashes for **every** affected file, signed/versioned multi-file plan, durable resource claim, protected staging/rollback, bounded blast radius, actual changed-file hashes, provider process termination, recovery on uncertain outcomes. |

The G4 RC5 milestone does **not** claim that all 29 native Serena operations may be invoked remotely. Its additional preview is a Codex X Mode wrapper, not a 30th native Serena tool.

## Regression and packaging evidence

- Test-driven RED/GREEN: new preview initially failed import; new Serena child HOME and CWD tests failed until isolation was implemented.
- Fixture HTTP and native process tests: token-required discovery, safe preview, deny direct mutations; malicious provider writes only to mirror.
- Explicit allowlist cannot unlock any of the five withheld tools.
- Source unit/integration suite: **199/199 PASS**.
- Clean-extracted RC5 suite: **199/199 PASS**.
- Python AST: **40 files**.
- Bundle byte parity: **49/49 files**.
- Deterministic bundle SHA-256: `f6cbb695de3ff58313e4dacddbafd9b56c1deb46b68a5a6c19d97654a5faf232`.
- RC5 SemVer Plugin/source/OpenAPI: `1.0.0-rc.5` (Python packaging `1.0.0rc5`).
- Isolated staging path: `/Users/luiapi/codex-x-mode-v1-rc5-staging-20261010`.

## Remaining blockers

- **ChatGPT Web/Mobile remote connector E2E is NOT VERIFIED.** The connected Codex X Mode Plugin currently reports deployed runtime `v0.2.23`, not RC5; `live_codex_verified=false`. A local loopback MCP request is not equivalent to a Web/Mobile app invocation.
- **G3**: Installed Native Codex protocol still lacks authoritative per-turn executed-model telemetry; model attestation stays `UNSUPPORTED/UNVERIFIED`.
- Remote Cloudflare Access identity/auth, v1 deployment parity, online rollback and App/Plugin publication all require explicit release authorization.

No production config or tunnel was changed, no service restarted, and no hosted Plugin published.
