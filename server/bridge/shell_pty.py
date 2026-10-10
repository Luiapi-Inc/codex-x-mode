"""Operator-only native full interactive PTY on a separate loopback surface.

An operator shell has the account's OS privileges; working directory is not a
sandbox. Do not tunnel or expose this over the public MCP gateway.
"""
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pty
import secrets
import select
import signal
import struct
import subprocess
import tempfile
import termios
import threading
import time
import uuid

from .core import Fault, project_config

_MAX_BUFFER = 1024 * 1024


def _integer(value, field, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise Fault(400, f"Invalid {field}")
    return value


def _token(value, name):
    if not isinstance(value, str) or not 1 <= len(value) <= 200 or "\0" in value:
        raise Fault(400, f"Invalid {name}")
    return value


class ShellManager:
    """Own all PTY processes and releases only proven-terminated writer claims."""

    def __init__(self, config, store):
        self.config = config
        self.store = store
        self.lock = threading.RLock()
        self.sessions = {}
        self.home = tempfile.TemporaryDirectory(prefix="codex-x-operator-shell-")
        with store._immediate_transaction():
            store.db.execute("""CREATE TABLE IF NOT EXISTS operator_shell_sessions (
                id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE,
                project_id TEXT NOT NULL, claim_id TEXT NOT NULL,
                columns INTEGER NOT NULL, rows INTEGER NOT NULL,
                state TEXT NOT NULL, receipt TEXT, created REAL NOT NULL
            )""")
            store.db.execute("""CREATE TABLE IF NOT EXISTS operator_shell_input (
                session_id TEXT NOT NULL, request_key TEXT NOT NULL,
                digest TEXT NOT NULL, bytes_written INTEGER NOT NULL,
                PRIMARY KEY(session_id,request_key)
            )""")

    def _settings(self):
        data = self.config.get("shell", {})
        if not isinstance(data, dict) or data.get("enabled") is not True:
            raise Fault(403, "Full operator shell is disabled")
        executable = data.get("executable", "/bin/sh")
        if (not isinstance(executable, str) or not executable.startswith("/")
                or not Path(executable).is_file() or not os.access(executable, os.X_OK)):
            raise Fault(503, "Native shell executable unavailable")
        return executable

    def _env(self, session_id):
        folder = Path(self.home.name) / session_id
        folder.mkdir(mode=0o700)
        env = {
            "HOME": str(folder), "TERM": "xterm-256color",
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
            "TMPDIR": tempfile.gettempdir() + "/",
        }
        return env

    def open(self, project_id, *, request_key, columns=80, rows=24):
        with self.lock:
            shell = self._settings()
            _token(request_key, "request_key")
            cols = _integer(columns, "columns", 20, 300)
            lines = _integer(rows, "rows", 5, 120)
            project = project_config(self.config, project_id, "workspace-write")
            root = Path(project["cwd"])
            with self.store.lock:
                prior = self.store.db.execute(
                    "SELECT * FROM operator_shell_sessions WHERE request_key=?",
                    (request_key,),
                ).fetchone()
            if prior is not None:
                if (prior["project_id"] != project_id
                        or prior["columns"] != cols or prior["rows"] != lines):
                    raise Fault(409, "Operator session request key conflicts")
                if prior["id"] in self.sessions:
                    return self.status(prior["id"])
                raise Fault(409, "Previous shell session requires recovery; never replay")
            sid = "shell_" + uuid.uuid4().hex
            aliases = [name for name, value in self.config["projects"].items()
                       if Path(value["cwd"]).resolve() == root]
            claim = self.store.acquire_app_writer(
                resource_id=str(root), project_id=project_id,
                resource_project_ids=aliases, thread_id=sid,
            )
            self.store.set_app_writer_turn(claim["id"], sid, sid)
            # Durable intent before any subprocess launch.
            with self.store._immediate_transaction():
                self.store.db.execute(
                    "INSERT INTO operator_shell_sessions(id,request_key,project_id,claim_id,columns,rows,state,created) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (sid, request_key, project_id, claim["id"], cols, lines, "starting", time.time()),
                )
            master = slave = None
            try:
                master, slave = pty.openpty()
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", lines, cols, 0, 0))
                proc = subprocess.Popen(
                    [shell], cwd=str(root), env=self._env(sid),
                    stdin=slave, stdout=slave, stderr=slave,
                    start_new_session=True, close_fds=True,
                )
                os.close(slave)
                slave = None
            except Exception:
                if master is not None:
                    os.close(master)
                if slave is not None:
                    os.close(slave)
                # No child was successfully launched; preserve a failed start receipt.
                with self.store._immediate_transaction():
                    self.store.finish_app_writer(claim["id"], "failed", {
                        "terminal_status": "failed", "thread_id": sid,
                        "turn_id": sid, "spawn_failed": True,
                    })
                    self.store.db.execute(
                        "UPDATE operator_shell_sessions SET state='failed' WHERE id=?", (sid,),
                    )
                raise Fault(503, "Native PTY process could not start")
            entry = {
                "id": sid, "project_id": project_id, "root": str(root),
                "claim_id": claim["id"], "proc": proc, "fd": master,
                "chunks": bytearray(), "base": 0, "total": 0,
                "digest": hashlib.sha256(), "columns": cols, "rows": lines,
                "state": "running", "exit_code": None,
            }
            self.sessions[sid] = entry
            with self.store._immediate_transaction():
                self.store.db.execute(
                    "UPDATE operator_shell_sessions SET state='running' WHERE id=?", (sid,),
                )
            watcher = threading.Thread(target=self._watch, args=(sid,), daemon=True)
            watcher.start()
            return self.status(sid)

    def _watch(self, sid):
        entry = self.sessions[sid]
        fd = entry["fd"]
        while True:
            try:
                ready, _, _ = select.select([fd], [], [], .1)
                if ready:
                    try:
                        data = os.read(fd, 65536)
                    except OSError:
                        data = b""
                    if data:
                        with self.lock:
                            entry["digest"].update(data)
                            entry["total"] += len(data)
                            entry["chunks"].extend(data)
                            if len(entry["chunks"]) > _MAX_BUFFER:
                                overflow = len(entry["chunks"]) - _MAX_BUFFER
                                del entry["chunks"][:overflow]
                                entry["base"] += overflow
                    elif entry["proc"].poll() is not None:
                        break
                if entry["proc"].poll() is not None:
                    # Drain one last opportunity for buffered terminal bytes.
                    if not ready:
                        break
            except (OSError, ValueError):
                break
        with self.lock:
            try:
                exit_code = entry["proc"].wait(timeout=1)
            except subprocess.TimeoutExpired:
                return
            self._finish(entry, exit_code)

    def _finish(self, entry, code):
        if entry["state"] != "running":
            return
        # The interactive shell can leave process-group children running.
        # Terminate remaining group members before releasing the writer claim.
        try:
            os.killpg(entry["proc"].pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            os.killpg(entry["proc"].pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        entry["exit_code"] = code
        entry["state"] = "exited"
        sid = entry["id"]
        try:
            os.close(entry["fd"])
        except OSError:
            pass
        outcome = "completed" if code == 0 else "failed"
        receipt = {
            "terminal_status": outcome, "thread_id": sid, "turn_id": sid,
            "exit_code": code, "output_sha256": entry["digest"].hexdigest(),
            "output_bytes": entry["total"], "process_exited": True,
            "project_id": entry["project_id"],
        }
        try:
            with self.store._immediate_transaction():
                self.store.finish_app_writer(entry["claim_id"], outcome, receipt)
                self.store.db.execute(
                    "UPDATE operator_shell_sessions SET state=?,receipt=? WHERE id=?",
                    (outcome, json.dumps(receipt), sid),
                )
        except Exception:
            self.store.mark_app_writer_unknown(entry["claim_id"], {
                "reason": "Shell exit receipt persistence failed", "session": sid,
            })
            entry["state"] = "unknown"

    def _entry(self, sid):
        if not isinstance(sid, str) or sid not in self.sessions:
            raise Fault(404, "Unknown or detached operator shell")
        return self.sessions[sid]

    def status(self, sid):
        with self.lock:
            value = self._entry(sid)
            return {
                "session_id": sid, "project_id": value["project_id"],
                "active": value["state"] == "running",
                "state": value["state"], "exit_code": value["exit_code"],
                "output_bytes": value["total"], "columns": value["columns"],
                "rows": value["rows"],
            }

    def list(self):
        with self.lock:
            return {"sessions": [self.status(sid) for sid in self.sessions]}

    def write(self, sid, data, *, request_key):
        with self.lock:
            value = self._entry(sid)
            _token(request_key, "request_key")
            if not isinstance(data, str) or len(data.encode("utf-8")) > 32768 or not data:
                raise Fault(400, "PTY stdin must be nonempty UTF-8 up to 32 KiB")
            digest = hashlib.sha256(data.encode("utf-8")).hexdigest()
            with self.store.lock:
                prior = self.store.db.execute(
                    "SELECT digest,bytes_written FROM operator_shell_input "
                    "WHERE session_id=? AND request_key=?", (sid, request_key),
                ).fetchone()
            if prior is not None:
                if prior["digest"] != digest:
                    raise Fault(409, "PTY input request_key has a different payload")
                return {"session_id": sid, "bytes_written": prior["bytes_written"],
                        "deduplicated": True}
            if value["state"] != "running" or value["proc"].poll() is not None:
                raise Fault(409, "Operator shell has terminated")
            raw = data.encode("utf-8")
            # Persist before writing: a lost response must never replay stdin.
            with self.store._immediate_transaction():
                self.store.db.execute(
                    "INSERT INTO operator_shell_input(session_id,request_key,digest,bytes_written) "
                    "VALUES(?,?,?,?)", (sid, request_key, digest, len(raw)),
                )
            try:
                written = os.write(value["fd"], raw)
                if written != len(raw):
                    raise OSError("PTY partial input")
            except OSError:
                raise Fault(409, "PTY input outcome uncertain; do not replay")
            return {"session_id": sid, "bytes_written": written, "deduplicated": False}

    def read(self, sid, *, cursor=0, max_bytes=32768):
        with self.lock:
            v = self._entry(sid)
            _integer(cursor, "cursor", 0, 2**63 - 1)
            _integer(max_bytes, "max_bytes", 1, 65536)
            if cursor < v["base"]:
                raise Fault(409, "PTY output cursor expired; output is bounded")
            if cursor > v["total"]:
                raise Fault(400, "PTY output cursor exceeds available output")
            offset = cursor - v["base"]
            raw = bytes(v["chunks"][offset:offset+max_bytes])
            # PTY bytes may include ANSI escapes and partial UTF-8 codepoints.
            return {"session_id": sid, "output": raw.decode("utf-8", "replace"),
                    "output_base64": base64.b64encode(raw).decode("ascii"),
                    "next_cursor": cursor + len(raw),
                    "active": v["state"] == "running",
                    "truncated_before": v["base"]}

    def resize(self, sid, *, columns, rows):
        with self.lock:
            v = self._entry(sid)
            if v["state"] != "running":
                raise Fault(409, "PTY is no longer active")
            cols, lines = _integer(columns, "columns", 20, 300), _integer(rows, "rows", 5, 120)
            fcntl.ioctl(v["fd"], termios.TIOCSWINSZ, struct.pack("HHHH", lines, cols, 0, 0))
            os.killpg(v["proc"].pid, signal.SIGWINCH)
            v["columns"], v["rows"] = cols, lines
            return self.status(sid)

    def signal(self, sid, name, *, request_key):
        allowed = {"INT": signal.SIGINT, "TERM": signal.SIGTERM, "HUP": signal.SIGHUP,
                   "TSTP": signal.SIGTSTP, "CONT": signal.SIGCONT}
        if name not in allowed:
            raise Fault(400, "Unsupported PTY signal")
        _token(request_key, "request_key")
        digest = hashlib.sha256(("signal:" + name).encode()).hexdigest()
        with self.lock:
            v = self._entry(sid)
            with self.store.lock:
                prior = self.store.db.execute(
                    "SELECT digest FROM operator_shell_input WHERE session_id=? AND request_key=?",
                    (sid, request_key),
                ).fetchone()
            if prior is not None:
                if prior["digest"] != digest:
                    raise Fault(409, "Operator signal request key conflicts")
                return {"session_id": sid, "signal": name, "deduplicated": True}
            if v["state"] != "running":
                raise Fault(409, "PTY is no longer active")
            with self.store._immediate_transaction():
                self.store.db.execute(
                    "INSERT INTO operator_shell_input(session_id,request_key,digest,bytes_written) "
                    "VALUES(?,?,?,0)", (sid, request_key, digest),
                )
            try:
                os.killpg(v["proc"].pid, allowed[name])
            except ProcessLookupError as exc:
                raise Fault(409, "PTY signal outcome uncertain; no replay") from exc
            return {"session_id": sid, "signal": name, "deduplicated": False}

    def close(self, sid):
        with self.lock:
            v = self._entry(sid)
            if v["state"] != "running":
                return self.status(sid)
            proc = v["proc"]
        try:
            os.killpg(proc.pid, signal.SIGHUP)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=2)
        with self.lock:
            self._finish(v, proc.returncode)
            return self.status(sid)

    def shutdown(self):
        with self.lock:
            sessions = list(self.sessions)
        for sid in sessions:
            try:
                self.close(sid)
            except Exception:
                pass
        self.home.cleanup()
