# Codex X Mode v1.0 RC6 — Full Operator Shell

Date: 2026-10-10 (Asia/Bangkok)
Status: Local operator PTY implemented. NO production deploy, NO public shell; release NO-GO.

## What works
- Native interactive POSIX PTY with an actual shell, persistent across RPC calls.
- Eight dedicated tools: codex_x_shell_open, _write, _read, _resize, _signal, _close, _status and _list.
- Shell output includes UTF-8 and raw Base64, bounded byte cursors, exit-code and hash receipts.
- Input and signal request keys are deduplicated before execution, with command payload hashes rather than raw commands in SQLite.
- A durable Core/App-compatible workspace-wide writer claim is reserved before launch, and released after shell exit is observed.
- Conflicting Core/App/unknown writer claims block new shells. Unresolved process status remains fail-closed.
- Shell child receives a temporary HOME and minimal environment without inherited provider API tokens.

## Security boundary
- Shell listener is a separate opt-in local HTTP MCP endpoint at 127.0.0.1:<shell-port>/operator/mcp.
- It requires a distinct operator shell_key and denies remote bind, unauthorized bearer, proxy headers, Host/Origin mismatch.
- Shell tools are NOT included in the public /mode/mcp, /mcp or /app/mcp catalogs; even direct public calls cannot launch one.
- New setup defaults to shell.enabled=false, shell.executable=/bin/sh and a separate generated shell_key.
- A full OS-user shell is NOT a sandbox. Project scope establishes initial working directory and writer ownership, but commands can access any filesystem location the macOS account can access.
- No complete process-tree containment or file-system isolation is claimed; detached descendants may escape the PTY process group.
- PTY lifecycle is persistent across RPC calls but not reattachable after bridge process restart; unknown writer state is preserved for manual reconciliation.
- Native SSH identity/host-key/session-management has NOT been implemented. An operator can run ssh from the local interactive shell if installed.

## Development invocation (after authorized offline config change)
cd server
python3 -m bridge --config /absolute/private.json serve --port 8240 --shell-port 8242
Separate shell bearer role: python3 -m bridge --config /absolute/private.json show-key shell
Do NOT forward port 8242 over Cloudflare, a reverse proxy, or public Internet.

## Verification
- Focused RED/GREEN for PTY process manager and separate OperatorShellServer.
- Tests cover terminal I/O, resizing, signal and input deduplication, binary output, disabled shell, project write permission, unresolved claim rejection, HTTP bearer separation, origin/host/proxy denial, and public MCP absence.
- Full source test suite 212/212 PASS, clean-extracted RC6 suite 212/212 PASS.
- Python AST 44 files, deterministic build and extract byte parity 53/53 files.
- Plugin/package/OpenAPI prerelease 1.0.0-rc.6, Python 1.0.0rc6.
- Staging /Users/luiapi/codex-x-mode-v1-rc6-staging-20261010

## Outstanding gates
- ChatGPT Web/Mobile remote shell E2E intentionally NOT run / NOT exposed.
- Managed SSH transport, strong OS sandboxing, terminal reconnect across runtime restarts, detached process supervision NOT accepted.
- Native Codex per-turn executed-model G3 acceptance remains blocked by upstream.
- No production restart, plugin publication, Cloudflare change or release GO.
