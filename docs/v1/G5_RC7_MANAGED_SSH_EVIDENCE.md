# Codex X Mode v1.0 RC7 — G5.1 Managed SSH & Remote PTY

Date: 2026-10-10 (Asia/Bangkok)
Status: Local implementation and fixture-based acceptance PASSED. Real remote SSH handshake NOT VERIFIED. Production NO-GO.

## Transport and operator boundary

- `server/bridge/ssh_remote.py` implements `SSHManager` using the existing native PTY process manager.
- A single named SSH profile supplies allowlisted project ID, remote hostname, remote username, port, private identity file and a pinned known_hosts file.
- `codex_x_ssh_open`, `codex_x_ssh_write`, `codex_x_ssh_read`, `codex_x_ssh_resize`, `codex_x_ssh_signal`, `codex_x_ssh_close`, `codex_x_ssh_status` and `codex_x_ssh_list` are available only through the separate 127.0.0.1 operator listener after SSH is explicitly enabled.
- The public `/mode/mcp`, `/mcp`, `/app/mcp` and Cloudflare routes do not advertise or execute SSH operator tools.
- The operator listener requires the distinct operator `shell_key`; managed SSH also requires `shell.enabled=true`, `ssh.enabled=true` and an allowlisted project with `allow_write=true`.

## SSH hardening

- Built against locally installed OpenSSH_10.3p1 (LibreSSL 3.3.6). Verified OpenSSH argument parsing with `ssh -G` (no network connection).
- Exact host-key pin is required in a plain known_hosts line. Default port 22 uses `host`, a non-default port uses `[host]:port`. Hashed, wildcard and certificate-authority patterns are not accepted by the local preflight.
- SSH known_hosts and identity files must be regular owner-owned files with no group/other access. Symlinks are rejected with O_NOFOLLOW.
- The exact host key file and identity bytes are copied to a disposable owner-only directory for each session, avoiding profile file changes between preflight and execution. These snapshots are deleted when the SSH process exits.
- SSH is started with StrictHostKeyChecking=yes, UserKnownHostsFile pointing to the pinned snapshot, GlobalKnownHostsFile=/dev/null, BatchMode=yes, PasswordAuthentication=no, KbdInteractiveAuthentication=no and IdentitiesOnly=yes.
- OpenSSH user configuration is disabled with `-F /dev/null`; agent/X11 forwarding, command/proxy jumps, local commands, multiplexing and forwardings are disabled.
- Only a configured named profile can supply destination and username. The MCP caller cannot inject arbitrary SSH flags, destinations, password prompts or remote command arguments.
- SSH sessions reuse durable Core/App-compatible writer ownership, input/signal request-key deduplication, PTY byte cursors, resize, close and terminal execution receipts.
- The receipt records SSH profile ID, remote host/port/user, pinned known_hosts SHA-256, output SHA-256 and exit code, without storing SSH private key or command text.

## Private configuration (illustrative; never commit secrets)

New private `bridge setup` configurations contain `ssh.enabled=false`, `ssh.executable=/usr/bin/ssh`, and an empty `ssh.profiles` map. Operator-approved offline config preview/apply must explicitly enable both `shell` and `ssh` and add a pinned profile, e.g.:

```json
{
  "shell": {"enabled": true, "executable": "/bin/sh"},
  "ssh": {
    "enabled": true,
    "executable": "/usr/bin/ssh",
    "profiles": {
      "staging": {
        "project_id": "demo",
        "host": "staging.internal",
        "user": "deploy",
        "port": 22,
        "known_hosts_file": "/private/secure/staging_known_hosts",
        "identity_file": "/private/secure/staging_id_ed25519"
      }
    }
  }
}
```

The example paths above are placeholders. Acquire the server's SSH host key from an **independent trusted source** before pinning. Do not trust an unauthenticated ssh-keyscan response by itself.

## Observed tests and limits

- TDD RED/GREEN: missing SSH adapter, operator tool registration and key-snapshot cleanup initially failed, then passed.
- 10 focused SSH tests cover interactive PTY fixture, exact strict SSH argument selection, invalid destination/user/port, missing/incorrect/symlinked host pin, weak file permissions, project writer collision, process exit 255, operator MCP transport, public tool denial, config validation and key-snapshot deletion.
- All Python regression tests: **222/222 PASS**. Clean-extracted RC7 tests: **222/222 PASS**.
- Python AST parse: **46 files PASS**. Deterministic bundle build and extraction byte parity: **55/55 files PASS**.
- Plugin/OpenAPI version: **1.0.0-rc.7**; Python package **1.0.0rc7**.
- RC7 bundle SHA-256: **bd29d39865646de7e3e5b84fde957081a063c111109f7b8be742cf154dfd1d47**.
- Isolated package: `/Users/luiapi/codex-x-mode-v1-rc7-staging-20261010`.

## NOT VERIFIED / remaining security gates

- No actual remote SSH server or authenticated handshake was exercised in this milestone. OpenSSH can verify the pinned host key when a live connection is made, but **remote connection status remains `unverified`** until separately proven.
- SSH connectivity/session availability, host identity and remote shell environment are not attested by merely starting the local SSH client.
- Remote shell commands use the SSH account's privileges and are NOT confined to the configured local project path.
- A local PTY is persistent across MCP calls but does not support session reattachment across bridge restart. Detached remote jobs are not independently supervised.
- No public internet ingress, Cloudflare tunnel exposure, Web/Mobile remote shell E2E, live production migration, or Plugin publication was performed.
- G3 Native per-turn executed-model attestation remains independently blocked; overall v1 release NO-GO.
