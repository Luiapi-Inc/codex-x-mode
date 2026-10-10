# Codex X Mode v1.0 RC8 — G5.2 Real SSH Loopback Acceptance & Restart Recovery

Date: 2026-10-11 (Asia/Bangkok)
Status: Local real-OpenSSH/localhost acceptance PASS; external-host acceptance NOT TESTED; production release NO-GO.

## Executed real SSH tests — MacBook localhost only

- Installed OpenSSH client 10.3p1 and local `/usr/sbin/sshd` were exercised.
- `server/scripts/verify_ssh_loopback.py` creates temporary ED25519 SSH server and client keys, authorized_keys and an exact `[127.0.0.1]:random-port` known_hosts pin.
- The script starts a dedicated unprivileged `sshd` on `127.0.0.1` using a temporary config. It never touches system sshd config, production listener, operator's real SSH keys or external hosts.
- `SSHManager` (not a mock transport) launches the installed OpenSSH client and authenticates to the temporary sshd with pinned host key and public-key identity.
- The remote interactive PTY accepted a command and returned a unique marker; sshd log independently reported `Accepted publickey` for the account.
- With a different but well-formed pinned host key, actual OpenSSH aborted with exit code 255 and a host-key verification error.
- With the correct host key but a different client identity, actual OpenSSH rejected public-key authentication with exit code 255.
- The script terminates sshd, SSH client and all test sessions and deletes the temporary key material on completion.
- This establishes **a real localhost SSH cryptographic handshake and remote PTY command**, not connectivity to any production/external SSH server.

## Restart, detached sessions and unknown state

- A new ShellManager/SSHManager process can inspect durable SQLite session records it does not own via `status` and `list` without reattaching or mutating them.
- Persisted `starting` or `running` records from another owner are reported as `detached_unverified`, with `active=false`, `reconciliation_required=true` and `can_reattach=false`.
- Existing writer ownership is not released on the basis of a stored state, elapsed time, PID or process-name guess; repeated requests with the same request key cannot launch duplicate shells.
- Terminal records carrying authoritative exit receipts can be inspected as history after a restart; they do not imply PTY reattachment.
- SSH status also records the persisted profile identifier when inspecting a detached record but does not pretend a fresh host-key or public-key handshake was observed.
- This is **fail-closed recovery visibility**, not restart-surviving PTY transport. An actual daemon crash can leave a remote job running; reconciliation remains operator-controlled.

## Credential and request-key safety

- Found and repaired a race between very fast SSH process exits and registration of copied private-key snapshots. Snapshots are registered before the PTY watcher starts and removed after process exit.
- Repeated idempotent SSH open requests do not replace the original credential snapshot or create abandoned copies.
- A reused open request key is rejected (409) if remote target, username, port, selected identity bytes or known_hosts fingerprint differs from the original session intent.
- The stable request fingerprint is hashed and stored only as evidence; private key bytes and plaintext commands are not written to receipt data.

## Reproducible tests and packaging

- Source suite **229/229 PASS**.
- Clean-extracted RC8 suite **229/229 PASS**.
- Python AST parse: **49 files PASS**, including the manual localhost acceptance script.
- Rebuilt deterministic bundle twice with identical SHA-256.
- Byte parity against clean extraction **57/57 files PASS**.
- RC8 surfaces: Plugin/Private Plugin/OpenAPI **1.0.0-rc.8**, Python **1.0.0rc8**.
- RC8 bundle SHA-256 **58b6b63eb648b2289af25f27610a28a48b0e8dbeec46e58ff598b5549d6c639d**.
- Isolated stage `/Users/luiapi/codex-x-mode-v1-rc8-staging-20261011`.

## Release blockers

- Actual independent remote host-key identity verification on a user-authorized server, remote SSH authentication and network interruption E2E remain NOT TESTED.
- SSHManager intentionally reports `connection_state=unverified` for live sessions: there is no portable, authoritative per-session handshake attestation in its API yet. The localhost script provides external test evidence only.
- Persisted PTY reattachment, remote process tree control after daemon crash and securely resuming incomplete remote commands remain NOT IMPLEMENTED.
- No public operator shell or SSH exposure through the ChatGPT connector/Cloudflare tunnel; no production configuration, runtime restart or deployment changes.
- G3 Native executed-model per-turn identity and ChatGPT Web/Mobile E2E remain separate release blockers.

**Release status: NO-GO.**
