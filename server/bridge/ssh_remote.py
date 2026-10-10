"""G5.1 pinned-profile OpenSSH transport over the existing operator PTY.

All destination/credentials originate in offline private configuration.
No remote shell endpoint is published in the public ChatGPT MCP gateway.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from .core import Fault, project_config
from .shell_pty import ShellManager, _token

_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,252}$")
_USER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.-]{0,63}$")
_NAME = re.compile(r"^[a-zA-Z0-9_.-]{1,70}$")


def _private_regular(path, *, label, max_bytes):
    if not isinstance(path, str) or not path.startswith('/') or len(path) > 4096:
        raise Fault(403, "Invalid SSH " + label + " path")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0))
        with os.fdopen(fd, 'rb') as stream:
            metadata = os.fstat(stream.fileno())
            if (not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != os.getuid()
                    or stat.S_IMODE(metadata.st_mode) & 0o077
                    or metadata.st_size < 12 or metadata.st_size > max_bytes):
                raise Fault(403, "SSH " + label + " must be owner-only regular file")
            value = stream.read(max_bytes+1)
            if len(value) != metadata.st_size:
                raise Fault(409, "SSH " + label + " changed during inspection")
            return value
    except OSError as exc:
        raise Fault(403, "SSH " + label + " is unavailable or not secure") from exc


def _host_key_entry(host, port, raw):
    target = host if port == 22 else f'[{host}]:{port}'
    try:
        lines = raw.decode('ascii').splitlines()
    except UnicodeDecodeError as exc:
        raise Fault(403, "Invalid SSH known-hosts encoding") from exc
    matched = False
    for line in lines:
        chunks = line.strip().split()
        if not chunks or line.lstrip().startswith('#'):
            continue
        if chunks[0] == '@revoked' and len(chunks) >= 2 and target in chunks[1].split(','):
            raise Fault(403, "SSH host key was revoked")
        # Plain exact known-hosts entries only: no wildcard, hashed hosts,
        # CA directives or user-provided ssh_config interpolation.
        if len(chunks) < 3 or chunks[0] != target or chunks[1] not in (
                'ssh-ed25519', 'ecdsa-sha2-nistp256', 'ecdsa-sha2-nistp384',
                'ecdsa-sha2-nistp521', 'ssh-rsa'):
            continue
        try:
            payload = base64.b64decode(chunks[2], validate=True)
            count = int.from_bytes(payload[:4], 'big')
            key_type = payload[4:4+count].decode('ascii')
            if count > 64 or key_type != chunks[1] or len(payload) < 36:
                continue
        except (ValueError, UnicodeDecodeError):
            continue
        matched = True
    if not matched:
        raise Fault(403, "SSH host key is not pinned for the exact destination")
    return hashlib.sha256(raw).hexdigest()


def validate_ssh_profile(config, profile_id):
    settings = config.get('ssh', {})
    if not isinstance(settings, dict) or settings.get('enabled') is not True:
        raise Fault(403, "Managed SSH is disabled")
    if set(settings) - {'enabled', 'profiles', 'executable'}:
        raise Fault(503, "Unrecognized SSH settings")
    if not isinstance(profile_id, str) or not _NAME.fullmatch(profile_id):
        raise Fault(403, "Invalid SSH profile ID")
    profiles = settings.get('profiles', {})
    if not isinstance(profiles, dict) or profile_id not in profiles:
        raise Fault(403, "SSH profile not allowed")
    entry = profiles[profile_id]
    if (not isinstance(entry, dict) or set(entry) != {
        'project_id', 'host', 'user', 'port', 'known_hosts_file', 'identity_file'
    }):
        raise Fault(503, "Invalid SSH profile schema")
    host, username, port = entry['host'], entry['user'], entry['port']
    if not isinstance(host, str) or not _HOST.fullmatch(host):
        raise Fault(403, "Invalid SSH target hostname")
    if not isinstance(username, str) or not _USER.fullmatch(username):
        raise Fault(403, "Invalid SSH username")
    if type(port) is not int or not 1 <= port <= 65535:
        raise Fault(403, "Invalid SSH port")
    project_config(config, entry['project_id'], 'workspace-write')
    key_bytes = _private_regular(entry['identity_file'], label='identity', max_bytes=32768)
    known_bytes = _private_regular(entry['known_hosts_file'], label='known_hosts', max_bytes=1024*1024)
    known_hash = _host_key_entry(host, port, known_bytes)
    executable = settings.get('executable', '/usr/bin/ssh')
    if (not isinstance(executable, str) or not executable.startswith('/')
            or not Path(executable).is_file() or not os.access(executable, os.X_OK)):
        raise Fault(503, "Configured SSH binary unavailable")
    return dict(entry), executable, known_bytes, key_bytes, known_hash


class SSHManager(ShellManager):
    def __init__(self, config, store):
        super().__init__(config, store)
        self.ssh_snapshots = {}

    def open(self, profile_id, *, request_key, columns=80, rows=24):
        _token(request_key, 'request_key')
        profile, executable, known_bytes, key_bytes, known_hash = validate_ssh_profile(
            self.config, profile_id)
        with self.lock:
            intent_sha256 = hashlib.sha256(json.dumps({
                "profile_id": profile_id,
                "project_id": profile["project_id"],
                "host": profile["host"], "user": profile["user"],
                "port": profile["port"], "executable": executable,
                "host_key_sha256": known_hash,
                "identity_sha256": hashlib.sha256(key_bytes).hexdigest(),
            }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            request_id = 'ssh:' + profile_id + ':' + request_key
            with self.store.lock:
                previous = self.store.db.execute(
                    "SELECT id FROM operator_shell_sessions WHERE request_key=?",
                    (request_id,),
                ).fetchone()
            if previous is not None and previous["id"] in self.sessions:
                before = self.sessions[previous["id"]].get("launch_metadata") or {}
                if before.get("ssh_intent_sha256") != intent_sha256:
                    raise Fault(409, "SSH request key conflicts with changed pinned profile")
            # Per-open immutable authentication snapshots. Never load ~/.ssh/config,
            # agent forwarding, ssh-agent or external ProxyCommand mechanisms.
            snapshot = Path(self.home.name) / ('ssh-keys-' + os.urandom(9).hex())
            snapshot.mkdir(mode=0o700)
            known = snapshot / 'known_hosts'
            known.write_bytes(known_bytes)
            known.chmod(0o600)
            identity = snapshot / 'identity'
            identity.write_bytes(key_bytes)
            identity.chmod(0o600)
            argv = [
                executable, '-F', '/dev/null', '-tt',
                '-o', 'StrictHostKeyChecking=yes',
                '-o', 'UserKnownHostsFile=' + str(known),
                '-o', 'GlobalKnownHostsFile=/dev/null',
                '-o', 'BatchMode=yes',
                '-o', 'PasswordAuthentication=no',
                '-o', 'KbdInteractiveAuthentication=no',
                '-o', 'IdentitiesOnly=yes',
                '-o', 'ForwardAgent=no',
                '-o', 'ForwardX11=no',
                '-o', 'PermitLocalCommand=no',
                '-o', 'ClearAllForwardings=yes',
                '-o', 'ControlMaster=no',
                '-o', 'ProxyCommand=none',
                '-o', 'ProxyJump=none',
                '-o', 'RequestTTY=force',
                '-o', 'ConnectTimeout=10',
                '-i', str(identity), '-p', str(profile['port']),
                '-l', profile['user'], '--', profile['host'],
            ]
            receipt = {
                'transport': 'ssh', 'profile_id': profile_id,
                'host': profile['host'], 'port': profile['port'],
                'remote_user': profile['user'], 'known_hosts_sha256': known_hash,
                'host_key_policy': 'strict-pinned', 'remote_session_attested': False,
                'ssh_intent_sha256': intent_sha256,
            }
            registration = []
            try:
                result = super().open(
                    profile['project_id'], request_key=request_id,
                    columns=columns, rows=rows,
                    _launch_argv=argv, _launch_metadata=receipt,
                    _on_registered=lambda sid: (
                        registration.append(sid),
                        self.ssh_snapshots.__setitem__(sid, snapshot),
                    ),
                )
            except Exception:
                if snapshot.exists():
                    shutil.rmtree(snapshot)
                raise
            if not registration:
                # Idempotent open reused an existing session; never overwrite
                # the original key snapshot with a newly copied credential.
                shutil.rmtree(snapshot)
            return self.status(result['session_id'])

    def _finish(self, entry, code):
        # Delete the copied private key/known-host pins when the SSH process
        # has exited, before releasing its durable writer ownership.
        snapshot = self.ssh_snapshots.pop(entry['id'], None)
        if snapshot is not None:
            if snapshot.parent != Path(self.home.name) or snapshot.is_symlink():
                raise Fault(503, 'Unexpected SSH credential snapshot path')
            shutil.rmtree(snapshot)
        return super()._finish(entry, code)

    def status(self, sid):
        outcome = super().status(sid)
        entry = self.sessions.get(sid)
        details = entry.get('launch_metadata') if entry else None
        if details:
            outcome.update({
                'transport': 'ssh', 'profile_id': details['profile_id'],
                'host': details['host'], 'port': details['port'],
                'connection_state': 'unverified' if outcome['active']
                    else 'process_exited',
            })
        elif entry is None:
            with self.store.lock:
                record = self.store.db.execute(
                    "SELECT request_key FROM operator_shell_sessions WHERE id=?",
                    (sid,)).fetchone()
            key = record["request_key"] if record else ""
            if key.startswith("ssh:"):
                outcome.update({
                    'transport': 'ssh',
                    'profile_id': key[4:].split(':', 1)[0],
                    'connection_state': 'detached_unverified'
                        if outcome["reconciliation_required"] else 'process_exited',
                })
        return outcome
