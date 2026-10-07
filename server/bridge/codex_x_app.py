import atexit
import json
import os
import signal
import subprocess
import threading
import time
from collections import deque
from contextlib import contextmanager

from .core import Fault


SERVER_INFO = {"name": "codex-x-app", "title": "Codex X App", "version": "0.2.23"}
LOCAL_HOST_ID = "local"


def _schema(properties=None, required=None, additional=False):
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": additional,
    }


TOOLS = [
    {
        "name": "list_threads",
        "description": "List Native Codex app threads on this host through Codex app-server 0.160.1.",
        "inputSchema": _schema({
            "hostId": {"type": "string", "description": "Optional local host selector; only 'local' is supported."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
            "cursor": {"type": "string"},
            "archived": {"type": "boolean"},
            "searchTerm": {"type": "string"},
        }),
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "read_thread",
        "description": "Read one Native Codex app thread and optionally its turns.",
        "inputSchema": _schema({
            "threadId": {"type": "string"},
            "hostId": {"type": "string"},
            "includeTurns": {"type": "boolean", "default": True},
        }, ["threadId"]),
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "create_thread",
        "description": "Create one Native Codex app thread and start its first turn. Defaults to read-only sandbox.",
        "inputSchema": _schema({
            "prompt": {"type": "string"},
            "title": {"type": "string"},
            "model": {"type": "string"},
            "projectId": {"type": "string", "description": "Configured Codex X Mode project. Omit only when exactly one project exists."},
            "scope": {"type": "string", "enum": ["read-only", "workspace-write"], "default": "read-only"},
        }, ["prompt"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "fork_thread",
        "description": "Fork a Native Codex app thread. Optionally start a turn and/or set a title on the fork.",
        "inputSchema": _schema({
            "threadId": {"type": "string"},
            "hostId": {"type": "string"},
            "prompt": {"type": "string"},
            "title": {"type": "string"},
            "model": {"type": "string"},
        }, ["threadId"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "send_message_to_thread",
        "description": "Send a prompt to a Native Codex app thread. Active turns are steered; idle threads start a new turn.",
        "inputSchema": _schema({
            "threadId": {"type": "string"},
            "hostId": {"type": "string"},
            "prompt": {"type": "string"},
        }, ["threadId", "prompt"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "set_thread_title",
        "description": "Set the title/name of a Native Codex app thread.",
        "inputSchema": _schema({
            "threadId": {"type": "string"},
            "hostId": {"type": "string"},
            "title": {"type": "string"},
        }, ["threadId", "title"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "set_thread_archived",
        "description": "Archive or unarchive a Native Codex app thread.",
        "inputSchema": _schema({
            "threadId": {"type": "string"},
            "hostId": {"type": "string"},
            "archived": {"type": "boolean"},
        }, ["threadId", "archived"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "wait_threads",
        "description": "Wait until the specified Native Codex app threads are no longer active or the timeout expires.",
        "inputSchema": _schema({
            "threadIds": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 32},
            "hostId": {"type": "string"},
            "timeoutMs": {"type": "integer", "minimum": 0, "maximum": 600000, "default": 60000},
        }, ["threadIds"]),
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
]

TOOL_MAP = {tool["name"]: tool for tool in TOOLS}


def _native_env():
    env = dict(os.environ)
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "ACCESS_TOKEN", "CODEX_BRIDGE_PROVIDER_KEY"):
        env.pop(key, None)
    return env


def _command(config, *parts):
    base = config.get("codex_command", ["codex"])
    if not isinstance(base, list) or not base or not all(isinstance(item, str) and item for item in base):
        raise Fault(503, "Native Codex command is not configured")
    return [*base, "-c", 'model_provider="openai"', *parts]


class _ManagedNativeApp:
    def __init__(self, command, *, env, timeout=60):
        self.command = tuple(command)
        self.default_timeout = max(10, float(timeout))
        self.sequence = 0
        self.sequence_lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.condition = threading.Condition()
        self.responses = {}
        self.notifications = deque(maxlen=4096)
        self.closed = False
        self.error = None
        try:
            self.process = subprocess.Popen(
                list(command),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                start_new_session=True,
                env=env,
            )
        except Exception:
            raise
        self.reader = threading.Thread(target=self._read_loop, name="codex-x-app-reader", daemon=True)
        self.reader.start()

    def _send(self, message):
        with self.write_lock:
            if self.process.poll() is not None or self.process.stdin is None:
                raise RuntimeError("Native Codex app-server disconnected")
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()

    def _read_loop(self):
        try:
            while True:
                line = self.process.stdout.readline(1024 * 1024 + 1)
                if not line:
                    break
                if len(line) > 1024 * 1024:
                    raise RuntimeError("Oversized Native Codex app-server message")
                message = json.loads(line)
                if "method" in message and "id" in message:
                    if str(message["method"]).endswith("requestApproval"):
                        self._send({"id": message["id"], "result": {"decision": "cancel"}})
                    else:
                        self._send({"id": message["id"], "error": {"code": -32601, "message": "Client operation disabled"}})
                    continue
                if "id" in message:
                    with self.condition:
                        self.responses[message["id"]] = message
                        self.condition.notify_all()
                else:
                    self.notifications.append(message)
        except Exception as exc:
            self.error = exc
        finally:
            with self.condition:
                self.closed = True
                self.condition.notify_all()

    def send(self, message):
        self._send(message)

    def call(self, method, params, timeout=None):
        with self.sequence_lock:
            self.sequence += 1
            request_id = self.sequence
        self._send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + (self.default_timeout if timeout is None else max(1, float(timeout)))
        with self.condition:
            while request_id not in self.responses:
                if self.closed:
                    raise RuntimeError("Native Codex app-server disconnected") from self.error
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Native Codex app-server request timed out")
                self.condition.wait(timeout=min(remaining, 1.0))
            message = self.responses.pop(request_id)
        if "error" in message:
            raise RuntimeError("Native Codex app-server rejected " + method)
        return message.get("result", {})

    def healthy(self):
        return not self.closed and self.process.poll() is None

    def close(self):
        if getattr(self, "process", None) is None:
            return
        try:
            if self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=5)
        finally:
            for handle in (self.process.stdin, self.process.stdout):
                try:
                    if handle is not None:
                        handle.close()
                except Exception:
                    pass
            self.reader.join(timeout=2)
            with self.condition:
                self.closed = True
                self.condition.notify_all()


_MANAGED_LOCK = threading.RLock()
_MANAGED_APP = None
_MANAGED_COMMAND = None


def _shutdown_managed_app():
    global _MANAGED_APP, _MANAGED_COMMAND
    with _MANAGED_LOCK:
        app = _MANAGED_APP
        _MANAGED_APP = None
        _MANAGED_COMMAND = None
    if app is not None:
        try:
            app.close()
        except Exception:
            pass


atexit.register(_shutdown_managed_app)


def _managed_app(config, timeout=60):
    global _MANAGED_APP, _MANAGED_COMMAND
    command = tuple(_command(config, "app-server", "--stdio"))
    with _MANAGED_LOCK:
        if _MANAGED_APP is not None and (_MANAGED_COMMAND != command or not _MANAGED_APP.healthy()):
            stale = _MANAGED_APP
            _MANAGED_APP = None
            _MANAGED_COMMAND = None
            try:
                stale.close()
            except Exception:
                pass
        if _MANAGED_APP is None:
            app = None
            try:
                app = _ManagedNativeApp(command, env=_native_env(), timeout=timeout)
                app.call("initialize", {"clientInfo": {"name": "codex_x_mode_codex_x_app", "title": "Codex X App", "version": "0.2.23"}}, timeout=max(10, timeout))
                app.send({"method": "initialized", "params": {}})
            except Exception as exc:
                try:
                    app.close()
                except Exception:
                    pass
                raise Fault(503, "Native Codex app-server is unavailable") from exc
            _MANAGED_APP = app
            _MANAGED_COMMAND = command
        return _MANAGED_APP


@contextmanager
def _session(config, timeout=60):
    app = _managed_app(config, timeout=timeout)
    try:
        yield app
    except Fault:
        raise
    except Exception as exc:
        with _MANAGED_LOCK:
            global _MANAGED_APP, _MANAGED_COMMAND
            if _MANAGED_APP is app:
                _MANAGED_APP = None
                _MANAGED_COMMAND = None
        try:
            app.close()
        except Exception:
            pass
        raise Fault(503, "Native Codex app-server operation failed") from exc


def _host(args):
    host = args.get("hostId")
    if host not in (None, "", LOCAL_HOST_ID):
        raise Fault(400, "Only the local Native Codex host is supported")


def _project(config, project_id=None, scope="read-only"):
    projects = config.get("projects")
    if not isinstance(projects, dict) or not projects:
        raise Fault(503, "No Codex X Mode project is configured")
    if project_id is None:
        if len(projects) != 1:
            raise Fault(400, "projectId is required when multiple projects are configured")
        project_id = next(iter(projects))
    project = projects.get(project_id)
    if not isinstance(project, dict) or not isinstance(project.get("cwd"), str):
        raise Fault(404, "Unknown project")
    if scope not in ("read-only", "workspace-write"):
        raise Fault(400, "Invalid scope")
    if scope == "workspace-write" and not project.get("allow_write", False):
        raise Fault(403, "workspace-write is not allowed for this project")
    return project_id, project


def _text_input(prompt):
    if not isinstance(prompt, str) or not prompt.strip():
        raise Fault(400, "prompt must be a non-empty string")
    return [{"type": "text", "text": prompt}]


def _thread(app, thread_id, include_turns=True):
    if not isinstance(thread_id, str) or not thread_id:
        raise Fault(400, "threadId is required")
    result = app.call("thread/read", {"threadId": thread_id, "includeTurns": bool(include_turns)})
    thread = result.get("thread")
    if not isinstance(thread, dict):
        raise Fault(503, "Native Codex returned an invalid thread")
    return thread


def _require_native_thread(thread):
    provider = thread.get("modelProvider")
    if provider != "openai":
        raise Fault(409, "Thread is not owned by the Native OpenAI Codex provider")


def _summary(thread):
    status = thread.get("status")
    status_type = status.get("type") if isinstance(status, dict) else status
    return {
        "hostId": LOCAL_HOST_ID,
        "threadId": thread.get("id"),
        "name": thread.get("name"),
        "cwd": thread.get("cwd"),
        "model": thread.get("model"),
        "modelProvider": thread.get("modelProvider"),
        "status": status_type,
        "updatedAt": thread.get("updatedAt"),
    }


def list_threads(config, args):
    _host(args)
    params = {"limit": int(args.get("limit", 50))}
    if params["limit"] < 1 or params["limit"] > 100:
        raise Fault(400, "limit out of range")
    for key in ("cursor", "archived", "searchTerm"):
        if key in args:
            params[key] = args[key]
    with _session(config) as app:
        result = app.call("thread/list", params)
    data = result.get("data", [])
    return {
        "hostId": LOCAL_HOST_ID,
        "threads": [_summary(item) for item in data if isinstance(item, dict)],
        "nextCursor": result.get("nextCursor"),
        "backwardsCursor": result.get("backwardsCursor"),
    }


def read_thread(config, args):
    _host(args)
    with _session(config) as app:
        thread = _thread(app, args.get("threadId"), args.get("includeTurns", True))
    return {"hostId": LOCAL_HOST_ID, "thread": thread}


def create_thread(config, args):
    scope = args.get("scope", "read-only")
    project_id, project = _project(config, args.get("projectId"), scope)
    prompt = args.get("prompt")
    title = args.get("title")
    model = args.get("model")
    start = {
        "cwd": project["cwd"],
        "modelProvider": "openai",
        "approvalPolicy": "never",
        "sandbox": scope,
        "ephemeral": False,
    }
    if isinstance(model, str) and model:
        start["model"] = model
    with _session(config) as app:
        response = app.call("thread/start", start)
        thread = response.get("thread")
        if not isinstance(thread, dict):
            raise Fault(503, "Native Codex returned an invalid thread")
        _require_native_thread(thread)
        if isinstance(title, str) and title.strip():
            app.call("thread/set-name", {"threadId": thread["id"], "name": title.strip()})
        turn_params = {
            "threadId": thread["id"],
            "cwd": project["cwd"],
            "approvalPolicy": "never",
            "input": _text_input(prompt),
        }
        if isinstance(model, str) and model:
            turn_params["model"] = model
        turn = app.call("turn/start", turn_params).get("turn", {})
        thread = _thread(app, thread["id"], False)
    return {
        "hostId": LOCAL_HOST_ID,
        "projectId": project_id,
        "thread": _summary(thread),
        "turnId": turn.get("id"),
    }


def fork_thread(config, args):
    _host(args)
    thread_id = args.get("threadId")
    with _session(config) as app:
        source = _thread(app, thread_id, False)
        _require_native_thread(source)
        params = {"threadId": thread_id, "modelProvider": "openai"}
        model = args.get("model")
        if isinstance(model, str) and model:
            params["model"] = model
        response = app.call("thread/fork", params)
        thread = response.get("thread")
        if not isinstance(thread, dict):
            raise Fault(503, "Native Codex returned an invalid fork")
        _require_native_thread(thread)
        title = args.get("title")
        if isinstance(title, str) and title.strip():
            app.call("thread/set-name", {"threadId": thread["id"], "name": title.strip()})
        turn_id = None
        if "prompt" in args:
            turn_params = {"threadId": thread["id"], "input": _text_input(args.get("prompt"))}
            if isinstance(model, str) and model:
                turn_params["model"] = model
            turn_id = app.call("turn/start", turn_params).get("turn", {}).get("id")
        thread = _thread(app, thread["id"], False)
    return {"hostId": LOCAL_HOST_ID, "thread": _summary(thread), "turnId": turn_id}


def send_message_to_thread(config, args):
    _host(args)
    prompt = args.get("prompt")
    with _session(config) as app:
        thread = _thread(app, args.get("threadId"), True)
        _require_native_thread(thread)
        turns = thread.get("turns") if isinstance(thread.get("turns"), list) else []
        active = [turn for turn in turns if isinstance(turn, dict) and turn.get("status") == "inProgress" and isinstance(turn.get("id"), str)]
        if active:
            turn_id = app.call("turn/steer", {
                "threadId": thread["id"],
                "expectedTurnId": active[-1]["id"],
                "input": _text_input(prompt),
            }).get("turnId")
            mode = "steer"
        else:
            result = app.call("turn/start", {"threadId": thread["id"], "input": _text_input(prompt)})
            turn_id = result.get("turn", {}).get("id")
            mode = "new_turn"
    return {"hostId": LOCAL_HOST_ID, "threadId": thread["id"], "turnId": turn_id, "mode": mode}


def set_thread_title(config, args):
    _host(args)
    title = args.get("title")
    if not isinstance(title, str) or not title.strip():
        raise Fault(400, "title must be a non-empty string")
    with _session(config) as app:
        thread = _thread(app, args.get("threadId"), False)
        _require_native_thread(thread)
        app.call("thread/set-name", {"threadId": thread["id"], "name": title.strip()})
        thread = _thread(app, thread["id"], False)
    return {"hostId": LOCAL_HOST_ID, "thread": _summary(thread)}


def set_thread_archived(config, args):
    _host(args)
    archived = args.get("archived")
    if not isinstance(archived, bool):
        raise Fault(400, "archived must be boolean")
    with _session(config) as app:
        thread = _thread(app, args.get("threadId"), False)
        _require_native_thread(thread)
        method = "thread/archive" if archived else "thread/unarchive"
        app.call(method, {"threadId": thread["id"]})
    return {"hostId": LOCAL_HOST_ID, "threadId": thread["id"], "archived": archived}


def wait_threads(config, args):
    _host(args)
    ids = args.get("threadIds")
    if not isinstance(ids, list) or not ids or len(ids) > 32 or any(not isinstance(item, str) or not item for item in ids):
        raise Fault(400, "threadIds must contain 1-32 thread IDs")
    ids = list(dict.fromkeys(ids))
    timeout_ms = args.get("timeoutMs", 60000)
    if not isinstance(timeout_ms, int) or timeout_ms < 0 or timeout_ms > 600000:
        raise Fault(400, "timeoutMs out of range")
    deadline = time.monotonic() + timeout_ms / 1000.0
    states = {}
    with _session(config, timeout=max(30, timeout_ms / 1000.0 + 15)) as app:
        while True:
            all_done = True
            for thread_id in ids:
                thread = _thread(app, thread_id, False)
                _require_native_thread(thread)
                summary = _summary(thread)
                states[thread_id] = summary
                if summary["status"] == "active":
                    all_done = False
            if all_done:
                return {"hostId": LOCAL_HOST_ID, "completed": True, "timedOut": False, "threads": [states[item] for item in ids]}
            if time.monotonic() >= deadline:
                return {"hostId": LOCAL_HOST_ID, "completed": False, "timedOut": True, "threads": [states[item] for item in ids]}
            time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))


def call_tool(name, args, config):
    if name not in TOOL_MAP:
        raise Fault(404, "Unknown codex-x-app tool")
    if not isinstance(args, dict):
        raise Fault(400, "Tool arguments must be an object")
    if name == "list_threads":
        return list_threads(config, args)
    if name == "read_thread":
        return read_thread(config, args)
    if name == "create_thread":
        return create_thread(config, args)
    if name == "fork_thread":
        return fork_thread(config, args)
    if name == "send_message_to_thread":
        return send_message_to_thread(config, args)
    if name == "set_thread_title":
        return set_thread_title(config, args)
    if name == "set_thread_archived":
        return set_thread_archived(config, args)
    if name == "wait_threads":
        return wait_threads(config, args)
    raise Fault(404, "Unknown codex-x-app tool")
