"""Private, offline configuration transactions for a single-operator runtime.

This kernel deliberately does not expose HTTP routes or reload a live process.
The future Admin API must separately authenticate/authorize callers, require
operator confirmation, and coordinate applying staged changes to the runtime.
"""
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from urllib.parse import urlsplit

from .policy import permitted_tool_names


class ConfigFault(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


_MUTABLE = frozenset({"mcp_policy", "allowed_origins", "projects", "web_model_policy", "serena", "shell"})
_PROJECT_ID = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


def _revision(raw):
    return hashlib.sha256(raw).hexdigest()


def _redact(node):
    if isinstance(node, dict):
        return {
            name: ("[REDACTED]" if any(part in name.lower() for part in
                   ("secret", "password", "credential", "token", "_key", "auth")) else _redact(value))
            for name, value in node.items()
        }
    if isinstance(node, list):
        return [_redact(value) for value in node]
    return node


def _sync_dir(path):
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class ConfigKernel:
    def __init__(self, path, *, store=None):
        self.path = Path(path).absolute()
        self.store = store

    def _load(self):
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(str(self.path), flags)
            with os.fdopen(fd, "rb") as stream:
                mode = os.fstat(stream.fileno()).st_mode
                if not stat.S_ISREG(mode) or stat.S_IMODE(mode) & 0o077:
                    raise ConfigFault(403, "Private config must be an owner-only regular file")
                raw = stream.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ConfigFault(413, "Private configuration too large")
            config = json.loads(raw)
        except (OSError, ValueError) as exc:
            raise ConfigFault(503, "Private configuration unavailable or invalid") from exc
        if not isinstance(config, dict):
            raise ConfigFault(503, "Private configuration must be an object")
        return raw, config

    def snapshot(self):
        raw, config = self._load()
        return {"revision": _revision(raw), "config": _redact(config)}

    @staticmethod
    def _validate(next_config):
        if "shell" in next_config:
            shell = next_config["shell"]
            if (not isinstance(shell, dict)
                or set(shell) - {"enabled", "executable"}
                or type(shell.get("enabled")) is not bool
                or not isinstance(shell.get("executable", "/bin/sh"), str)
                or not shell.get("executable", "/bin/sh").startswith("/")):
                raise ConfigFault(400, "Invalid operator shell config")
        if "serena" in next_config:
            from .serena_adapter import _settings
            from .core import Fault
            try:
                _settings(next_config)
            except Fault as exc:
                raise ConfigFault(400, "Invalid Serena context/provider configuration") from exc
        if next_config.get("config_schema_version") == 1 and "mcp_policy" not in next_config:
            raise ConfigFault(400, "v1 requires an MCP capability policy")
        if "web_model_policy" in next_config and next_config["web_model_policy"] not in ("native", "legacy-prefixed"):
            raise ConfigFault(400, "Invalid Native Codex model policy")
        if "mcp_policy" in next_config:
            from .mcp import UNIFIED_TOOLS, CODEX_X_APP_TOOLS
            catalog = (*UNIFIED_TOOLS, *CODEX_X_APP_TOOLS)
            try:
                permitted_tool_names(next_config, catalog, catalog=catalog)
            except Exception as exc:
                raise ConfigFault(400, "Invalid MCP capability policy") from exc

        if "allowed_origins" in next_config:
            origins = next_config["allowed_origins"]
            if not isinstance(origins, list) or not all(isinstance(v, str) for v in origins):
                raise ConfigFault(400, "Invalid allowed origins")
            if len(origins) != len(set(origins)):
                raise ConfigFault(400, "Duplicate allowed origin")
            for origin in origins:
                parts = urlsplit(origin)
                if not (parts.scheme == "https" or
                        (parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1"))):
                    raise ConfigFault(400, "Origin must be HTTPS or loopback")
                if not parts.hostname or parts.username or parts.password or parts.path not in ("", "/") or parts.query or parts.fragment or "*" in origin:
                    raise ConfigFault(400, "Invalid allowed origin")

        projects = next_config.get("projects")
        if not isinstance(projects, dict):
            raise ConfigFault(400, "Missing projects configuration")
        for name, entry in projects.items():
            if not isinstance(name, str) or not _PROJECT_ID.fullmatch(name):
                raise ConfigFault(400, "Invalid project identifier")
            if not isinstance(entry, dict) or set(entry) != {"cwd", "allow_write"}:
                raise ConfigFault(400, "Invalid project configuration")
            if type(entry["allow_write"]) is not bool or not isinstance(entry["cwd"], str):
                raise ConfigFault(400, "Invalid project permission/path")
            root = Path(entry["cwd"])
            try:
                if not root.is_absolute() or not root.resolve(strict=True).is_dir():
                    raise ValueError("invalid root")
            except (OSError, RuntimeError, ValueError) as exc:
                raise ConfigFault(400, "Project root unavailable") from exc

    def _candidate(self, changes, expected_revision):
        raw, config = self._load()
        revision = _revision(raw)
        if revision != expected_revision:
            raise ConfigFault(409, "Stale configuration revision")
        if (not isinstance(changes, dict) or not changes or
                set(changes) - _MUTABLE):
            raise ConfigFault(403, "Configuration change not permitted")
        updated = copy.deepcopy(config)
        updated.update(copy.deepcopy(changes))
        self._validate(updated)
        changed = sorted(k for k in changes if updated[k] != config.get(k))
        next_raw = (json.dumps(updated, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()
        return raw, config, updated, changed, next_raw

    def preview(self, changes, *, expected_revision):
        raw, _, updated, changed, next_raw = self._candidate(changes, expected_revision)
        return {
            "revision": _revision(raw), "next_revision": _revision(next_raw),
            "changed_keys": changed,
            "proposed": {key: _redact(updated[key]) for key in changed},
        }

    def _guard_project_change(self, config, updated):
        if updated.get("projects") == config.get("projects"):
            return
        if self.store is None:
            raise ConfigFault(409, "Project changes require active writer reconciliation")
        # Conservative global barrier: no project-root/permission edits during
        # queued, executing, cancelling, unknown, or App writer activity.
        with self.store.lock:
            tasks = self.store.db.execute(
                "SELECT 1 FROM jobs WHERE kind='task' "
                "AND state IN ('queued','running','cancelling','unknown') LIMIT 1"
            ).fetchone()
            writers = self.store.db.execute(
                "SELECT 1 FROM app_writer_claims WHERE state IN ('running','unknown') LIMIT 1"
            ).fetchone()
        if tasks is not None or writers is not None:
            raise ConfigFault(409, "Active or unknown run prevents project configuration change")

    def read_history(self, revision):
        """Read a private snapshot by exact digest without trusting a path."""
        if not isinstance(revision, str) or len(revision) != 64 or any(
            ch not in "0123456789abcdef" for ch in revision
        ):
            raise ConfigFault(400, "Invalid history revision")
        directory = self.path.with_name(self.path.name + ".history")
        if directory.is_symlink() or not directory.is_dir() or stat.S_IMODE(directory.stat().st_mode) & 0o077:
            raise ConfigFault(404, "Private history unavailable")
        target = directory / (revision + ".json")
        try:
            fd = os.open(str(target), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as stream:
                mode = os.fstat(stream.fileno()).st_mode
                if not stat.S_ISREG(mode) or stat.S_IMODE(mode) & 0o077:
                    raise ConfigFault(403, "Insecure history entry")
                raw = stream.read(1024 * 1024 + 1)
        except OSError as exc:
            raise ConfigFault(404, "Private history unavailable") from exc
        if _revision(raw) != revision:
            raise ConfigFault(409, "Private history digest mismatch")
        try:
            value = json.loads(raw)
        except ValueError as exc:
            raise ConfigFault(409, "Invalid history content") from exc
        if not isinstance(value, dict):
            raise ConfigFault(409, "Invalid history content")
        return value

    def apply(self, changes, *, expected_revision):
        lock_path = str(self.path) + ".mutation.lock"
        flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise ConfigFault(503, "Configuration lock unavailable") from exc
        tmp = None
        try:
            if stat.S_IMODE(os.fstat(fd).st_mode) & 0o077:
                raise ConfigFault(403, "Insecure configuration lock")
            fcntl.flock(fd, fcntl.LOCK_EX)
            old_raw, config, updated, changed, new_raw = self._candidate(changes, expected_revision)
            self._guard_project_change(config, updated)
            if not changed:
                return self.snapshot()
            history = self.path.with_name(self.path.name + ".history")
            if history.exists():
                if history.is_symlink() or not history.is_dir() or stat.S_IMODE(history.stat().st_mode) & 0o077:
                    raise ConfigFault(403, "Insecure configuration history")
            else:
                history.mkdir(mode=0o700)
            previous = history / (_revision(old_raw) + ".json")
            if previous.exists():
                if previous.is_symlink() or previous.read_bytes() != old_raw:
                    raise ConfigFault(409, "Configuration history collision")
            else:
                backup_fd = os.open(str(previous), os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
                with os.fdopen(backup_fd, "wb") as stream:
                    stream.write(old_raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                _sync_dir(history)
            tmp_fd, tmp_name = tempfile.mkstemp(prefix=".config-", dir=str(self.path.parent))
            tmp = Path(tmp_name)
            with os.fdopen(tmp_fd, "wb") as stream:
                stream.write(new_raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
            tmp = None
            _sync_dir(self.path.parent)
            return {"revision": _revision(new_raw), "changed_keys": changed, "applied": True,
                    "requires_reload": True}
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
            os.close(fd)
