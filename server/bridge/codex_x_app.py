import atexit
import errno
import json
import os
import signal
import subprocess
import threading
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path

from .codex import ModelSelectionError, _isolated_native_codex_env, _model_catalog, _select_model
from .core import Fault


SERVER_INFO = {"name": "codex-x-app", "title": "Codex X App", "version": "0.2.12"}
LOCAL_HOST_ID = "local"
WEB_MODEL_PREFIX = "chatgpt-web/"


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
    def __init__(self, command, *, env, timeout=60, cleanup=None):
        self.command = tuple(command)
        self.default_timeout = max(10, float(timeout))
        self.cleanup = cleanup
        self.active_sessions = 0
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

    def has_active_threads(self):
        cursor = None
        seen_cursors = set()
        for _ in range(20):
            params = {"limit": 100}
            if cursor is not None:
                params["cursor"] = cursor
            result = self.call("thread/list", params)
            if not isinstance(result, dict) or not isinstance(result.get("data"), list):
                raise Fault(503, "Native Codex thread activity cannot be verified before profile replacement")
            for thread in result["data"]:
                if not isinstance(thread, dict):
                    raise Fault(503, "Native Codex thread activity cannot be verified before profile replacement")
                status = thread.get("status")
                status_type = status.get("type") if isinstance(status, dict) else status
                if status_type == "active":
                    return True
                if status_type != "idle":
                    raise Fault(503, "Native Codex thread activity cannot be verified before profile replacement")
            cursor = result.get("nextCursor")
            if cursor is None:
                return False
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                raise Fault(503, "Native Codex thread activity cannot be verified before profile replacement")
            seen_cursors.add(cursor)
        raise Fault(503, "Native Codex thread activity exceeded the verification limit")

    def stopped(self):
        try:
            return self.process.poll() is not None and self._wait_process_group_stopped(timeout=0)
        except Exception:
            return False

    def _wait_process_group_stopped(self, timeout=5):
        deadline = time.monotonic() + max(0, float(timeout))
        while True:
            try:
                os.killpg(self.process.pid, 0)
            except ProcessLookupError:
                return True
            except OSError as exc:
                if exc.errno == errno.ESRCH:
                    return True
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))

    @staticmethod
    def _retain_cleanup_home(cleanup):
        # TemporaryDirectory's finalizer would otherwise delete auth.json if
        # this app object is collected while its subprocess may still be live.
        owner = getattr(cleanup, "__self__", None)
        finalizer = getattr(owner, "_finalizer", None)
        if finalizer is not None and finalizer.alive:
            finalizer.detach()

    def close(self):
        if getattr(self, "process", None) is None:
            return False
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
            stopped = self.stopped()
            if stopped and self.cleanup is not None:
                cleanup, self.cleanup = self.cleanup, None
                cleanup()
            elif not stopped and self.cleanup is not None:
                self._retain_cleanup_home(self.cleanup)
        return stopped


_MANAGED_LOCK = threading.RLock()
_MANAGED_APPS = {}


def _web_auth_identity(config):
    source_home = Path(
        config.get("native_codex_home")
        or os.environ.get("CODEX_HOME")
        or (Path.home() / ".codex")
    ).expanduser()
    auth_source = source_home / "auth.json"
    try:
        stat = auth_source.stat()
        if not auth_source.is_file():
            raise OSError("Native Codex auth file is unavailable")
    except OSError as exc:
        raise Fault(503, "Native Codex ChatGPT authentication is unavailable") from exc
    return (str(auth_source.resolve()), stat.st_ino, stat.st_mtime_ns, stat.st_size)


def _shutdown_managed_app():
    with _MANAGED_LOCK:
        apps = [entry[1] for entry in _MANAGED_APPS.values()]
        _MANAGED_APPS.clear()
    for app in apps:
        try:
            app.close()
        except Exception:
            pass


atexit.register(_shutdown_managed_app)


def _managed_app(config, timeout=60):
    command = tuple(_command(config, "app-server", "--stdio"))
    origin = "web" if config.get("_dispatch_origin") == "web" else "local"
    identity = (command, _web_auth_identity(config)) if origin == "web" else (command,)
    with _MANAGED_LOCK:
        existing = _MANAGED_APPS.get(origin)
        if existing is not None and existing[0] != identity:
            current = existing[1]
            if getattr(current, "active_sessions", 0) > 0:
                raise Fault(409, "Cannot replace the Native Codex profile during an active App request")
            if current.healthy() and current.has_active_threads():
                raise Fault(409, "Cannot replace the Native Codex profile while a Native Codex turn is active")
        if existing is not None and (existing[0] != identity or not existing[1].healthy()):
            stale = existing[1]
            try:
                stopped = stale.close()
            except Exception as exc:
                stopped = stale.stopped()
                if not stopped:
                    raise Fault(503, "Previous Native Codex app-server stop cannot be verified") from exc
            if not stopped:
                raise Fault(503, "Previous Native Codex app-server stop cannot be verified")
            _MANAGED_APPS.pop(origin, None)
        if origin not in _MANAGED_APPS:
            app = None
            cleanup = None
            try:
                if origin == "web":
                    env, cleanup = _isolated_native_codex_env(config)
                else:
                    env = _native_env()
                app = _ManagedNativeApp(command, env=env, timeout=timeout, cleanup=cleanup)
                cleanup = None
                app.call("initialize", {"clientInfo": {"name": "codex_x_mode_codex_x_app", "title": "Codex X App", "version": "0.2.12"}}, timeout=max(10, timeout))
                app.send({"method": "initialized", "params": {}})
            except Exception as exc:
                try:
                    app.close()
                except Exception:
                    pass
                if cleanup is not None:
                    cleanup()
                raise Fault(503, "Native Codex app-server is unavailable") from exc
            _MANAGED_APPS[origin] = (identity, app)
        return _MANAGED_APPS[origin][1]


@contextmanager
def _session(config, timeout=60):
    with _MANAGED_LOCK:
        app = _managed_app(config, timeout=timeout)
        app.active_sessions = getattr(app, "active_sessions", 0) + 1
    try:
        yield app
    except Fault:
        raise
    except Exception as exc:
        try:
            stopped = app.close()
        except Exception:
            stopped = app.stopped()
        if stopped:
            with _MANAGED_LOCK:
                for origin, entry in tuple(_MANAGED_APPS.items()):
                    if entry[1] is app:
                        _MANAGED_APPS.pop(origin, None)
        raise Fault(503, "Native Codex app-server operation failed; no automatic replay") from exc
    finally:
        with _MANAGED_LOCK:
            app.active_sessions = max(0, getattr(app, "active_sessions", 1) - 1)


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


def _project_resource_identity(config, project_id, project):
    try:
        root = Path(project["cwd"]).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise Fault(409, "Canonical project resource cannot be verified") from exc
    if not root.is_dir():
        raise Fault(409, "Canonical project resource is not a directory")
    aliases = []
    for alias, candidate in config.get("projects", {}).items():
        try:
            if Path(candidate["cwd"]).resolve(strict=True) == root:
                aliases.append(alias)
        except (KeyError, OSError, RuntimeError, TypeError):
            continue
    return str(root), sorted(set(aliases or [project_id]))


def _turn_status(turn):
    status = turn.get("status") if isinstance(turn, dict) else None
    return status.get("type") if isinstance(status, dict) else status


_TERMINAL_NATIVE_TURN_STATUSES = {"completed", "failed", "interrupted", "cancelled"}


def _start_workspace_write_turn(app, store, config, project_id, project, thread_id, turn_params):
    if store is None:
        raise Fault(503, "Durable workspace-write ownership is unavailable")
    resource_id, aliases = _project_resource_identity(config, project_id, project)
    claim = store.acquire_app_writer(
        resource_id=resource_id,
        project_id=project_id,
        resource_project_ids=aliases,
        thread_id=thread_id,
    )
    try:
        response = app.call("turn/start", turn_params)
    except Exception as exc:
        store.mark_app_writer_unknown(claim["id"], {
            "thread_id": thread_id,
            "turn_id": None,
            "reason": "Native turn/start outcome is ambiguous",
        })
        raise Fault(503, "Native workspace-write outcome is unknown; no automatic replay") from exc
    turn = response.get("turn") if isinstance(response, dict) else None
    turn_id = turn.get("id") if isinstance(turn, dict) else None
    if not isinstance(turn_id, str) or not turn_id:
        store.mark_app_writer_unknown(claim["id"], {
            "thread_id": thread_id,
            "reason": "Native turn/start returned no verifiable turn identity",
        })
        raise Fault(503, "Native workspace-write outcome is unknown; turn identity is missing")
    store.set_app_writer_turn(claim["id"], thread_id, turn_id)
    try:
        thread = _thread(app, thread_id, True)
    except Exception as exc:
        store.mark_app_writer_unknown(claim["id"], {
            "thread_id": thread_id,
            "turn_id": turn_id,
            "reason": "Native thread/read after turn/start could not verify writer state",
        })
        raise Fault(503, "Native workspace-write outcome is unknown; no automatic replay") from exc
    thread_status = _summary(thread)["status"]
    if thread_status in ("active", "idle"):
        _settle_app_writer_from_thread(store, thread)
    else:
        store.mark_app_writer_unknown(claim["id"], {
            "thread_id": thread_id,
            "turn_id": turn_id,
            "reason": "Native thread/read returned unverifiable activity status",
        })
    return turn


def _terminal_turn_for_claim(thread, claim):
    if _summary(thread)["status"] != "idle" or not claim or not claim.get("turn_id"):
        return None
    turns = thread.get("turns")
    if not isinstance(turns, list):
        return None
    for turn in turns:
        if isinstance(turn, dict) and turn.get("id") == claim["turn_id"]:
            status = _turn_status(turn)
            if status in _TERMINAL_NATIVE_TURN_STATUSES:
                return status
    return None


def _settle_app_writer_from_thread(store, thread):
    claim = store.app_writer_claim_for_thread(thread.get("id"))
    if claim is None:
        return
    terminal_status = _terminal_turn_for_claim(thread, claim)
    if terminal_status is None:
        if _summary(thread)["status"] == "idle":
            store.mark_app_writer_unknown(claim["id"], {
                "thread_id": claim.get("thread_id"),
                "turn_id": claim.get("turn_id"),
                "reason": "Native thread is idle without matching terminal turn evidence",
            })
        return
    store.finish_app_writer(claim["id"], terminal_status, {
        "thread_id": claim.get("thread_id"),
        "turn_id": claim.get("turn_id"),
        "terminal_status": terminal_status,
        "source": "Native thread/read terminal turn",
    })


def _text_input(prompt):
    if not isinstance(prompt, str) or not prompt.strip():
        raise Fault(400, "prompt must be a non-empty string")
    return [{"type": "text", "text": prompt}]


def _web_model(config, app, requested=None):
    """Resolve a Web-originated App turn to an exact Native Codex Web model."""
    try:
        models = _model_catalog(app)
    except ModelSelectionError as exc:
        raise Fault(503, "Native Codex Web model catalog is unavailable") from exc
    mode = config.get("web_model_policy", "legacy-prefixed")
    if mode not in ("native", "legacy-prefixed"):
        raise Fault(503, "Invalid Native Codex model policy")
    required_prefix = None if mode == "native" else WEB_MODEL_PREFIX
    # For native mode, exclude non-executable aliases and incomplete efforts.
    if mode == "native":
        models = [
            item for item in models
            if item.get("model") == item.get("id")
            and isinstance(item.get("id"), str) and item["id"]
            and isinstance(item.get("supportedReasoningEfforts"), list)
            and any(isinstance(v, dict) and v.get("reasoningEffort") == item.get("defaultReasoningEffort")
                    for v in item["supportedReasoningEfforts"])
        ]
    configured_default = config.get("chatgpt_web_default_model")
    if not isinstance(configured_default, str) or not any(
        isinstance(item.get("id"), str)
        and item["id"] == configured_default
        and (required_prefix is None or item["id"].startswith(required_prefix))
        and item["model"] == item["id"]
        for item in models
    ):
        configured_default = None
    try:
        selected = _select_model(
            models,
            requested,
            required_prefix=required_prefix,
            default_model=configured_default,
            reasoning_effort=config.get("chatgpt_web_reasoning_effort"),
        )
        if mode == "native" and selected["reasoning_effort"] not in selected["supported_reasoning_efforts"]:
            raise ModelSelectionError("Native effort is not supported")
        return selected
    except ModelSelectionError as exc:
        raise Fault(400, "Exact Native Codex model or reasoning effort is unavailable") from exc


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


def _authorize_thread(config, thread, store=None):
    _require_native_thread(thread)
    cwd = thread.get("cwd")
    sandbox = thread.get("sandbox")
    if not isinstance(cwd, str) or not cwd or not Path(cwd).is_absolute():
        raise Fault(403, "Thread project ownership cannot be verified")
    registered = store.app_thread_scope(thread.get("id")) if store is not None else None
    if registered is not None:
        if Path(registered["cwd"]).resolve() != Path(cwd).resolve():
            raise Fault(403, "Thread cwd differs from registered ownership")
        if sandbox is not None and sandbox != registered["scope"]:
            raise Fault(403, "Thread scope differs from registered ownership")
        sandbox = registered["scope"]
    if sandbox not in ("read-only", "workspace-write"):
        raise Fault(403, "Thread sandbox scope cannot be verified")
    thread_path = Path(cwd).resolve()
    projects = config.get("projects", {})
    if not isinstance(projects, dict):
        raise Fault(403, "Thread project ownership cannot be verified")
    for project_id, project in projects.items():
        if registered is not None and project_id != registered["project_id"]:
            continue
        if not isinstance(project, dict) or not isinstance(project.get("cwd"), str):
            continue
        root = Path(project["cwd"])
        if not root.is_absolute() or not thread_path.is_relative_to(root.resolve()):
            continue
        if sandbox == "workspace-write" and not project.get("allow_write", False):
            raise Fault(403, "Thread workspace-write access is not allowed")
        return project_id, project, sandbox
    raise Fault(403, "Thread is outside the configured projects")


def _record_new_thread(store, response, project_id, project, scope):
    if store is None:
        raise Fault(503, "Durable App thread ownership is not available")
    thread = response.get("thread")
    policy = response.get("sandbox")
    expected_policy = "readOnly" if scope == "read-only" else "workspaceWrite"
    root = Path(project["cwd"]).resolve()
    if (not isinstance(thread, dict) or not isinstance(thread.get("id"), str)
            or not thread["id"] or not isinstance(policy, dict)
            or policy.get("type") != expected_policy
            or response.get("cwd") != project["cwd"]
            or thread.get("cwd") != project["cwd"]):
        raise Fault(403, "Native Codex did not prove the requested thread scope")
    if scope == "workspace-write":
        extra_roots = policy.get("writableRoots", [])
        if (not isinstance(extra_roots, list) or any(
            not isinstance(value, str) or not Path(value).is_absolute()
            or not Path(value).resolve().is_relative_to(root) for value in extra_roots
        )):
            raise Fault(403, "Native Codex returned unapproved writable roots")
    store.record_app_thread(thread["id"], project_id, str(root), scope)


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


def list_threads(config, args, store=None):
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
    authorized = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            _authorize_thread(config, item, store)
        except Fault:
            continue
        authorized.append(_summary(item))
    return {
        "hostId": LOCAL_HOST_ID,
        "threads": authorized,
        "nextCursor": result.get("nextCursor"),
        "backwardsCursor": result.get("backwardsCursor"),
    }


def read_thread(config, args, store=None):
    _host(args)
    with _session(config) as app:
        thread = _thread(app, args.get("threadId"), args.get("includeTurns", True))
        _authorize_thread(config, thread, store)
    return {"hostId": LOCAL_HOST_ID, "thread": thread}


def create_thread(config, args, store=None):
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
        web_model = _web_model(config, app, model) if config.get("_dispatch_origin") == "web" else None
        if web_model is not None:
            start["model"] = web_model["model"]
        response = app.call("thread/start", start)
        thread = response.get("thread")
        if not isinstance(thread, dict):
            raise Fault(503, "Native Codex returned an invalid thread")
        _require_native_thread(thread)
        _record_new_thread(store, response, project_id, project, scope)
        if isinstance(title, str) and title.strip():
            app.call("thread/set-name", {"threadId": thread["id"], "name": title.strip()})
        turn_params = {
            "threadId": thread["id"],
            "cwd": project["cwd"],
            "approvalPolicy": "never",
            "input": _text_input(prompt),
        }
        if web_model is not None:
            turn_params["model"] = web_model["model"]
            turn_params["effort"] = web_model["reasoning_effort"]
        elif isinstance(model, str) and model:
            turn_params["model"] = model
        if scope == "workspace-write":
            turn = _start_workspace_write_turn(
                app, store, config, project_id, project, thread["id"], turn_params,
            )
        else:
            turn = app.call("turn/start", turn_params).get("turn", {})
        thread = _thread(app, thread["id"], False)
    return {
        "hostId": LOCAL_HOST_ID,
        "projectId": project_id,
        "thread": _summary(thread),
        "turnId": turn.get("id"),
    }


def fork_thread(config, args, store=None):
    _host(args)
    thread_id = args.get("threadId")
    with _session(config) as app:
        source = _thread(app, thread_id, False)
        project_id, project, scope = _authorize_thread(config, source, store)
        params = {"threadId": thread_id, "modelProvider": "openai"}
        model = args.get("model")
        web_model = None
        if config.get("_dispatch_origin") == "web":
            web_model = _web_model(config, app, model if isinstance(model, str) and model else source.get("model"))
            model = web_model["model"]
        if isinstance(model, str) and model:
            params["model"] = model
        response = app.call("thread/fork", params)
        thread = response.get("thread")
        if not isinstance(thread, dict):
            raise Fault(503, "Native Codex returned an invalid fork")
        _require_native_thread(thread)
        _record_new_thread(store, response, project_id, project, scope)
        title = args.get("title")
        if isinstance(title, str) and title.strip():
            app.call("thread/set-name", {"threadId": thread["id"], "name": title.strip()})
        turn_id = None
        if "prompt" in args:
            turn_params = {"threadId": thread["id"], "input": _text_input(args.get("prompt"))}
            if web_model is not None:
                turn_params["model"] = web_model["model"]
                turn_params["effort"] = web_model["reasoning_effort"]
            elif isinstance(model, str) and model:
                turn_params["model"] = model
            if scope == "workspace-write":
                turn = _start_workspace_write_turn(
                    app, store, config, project_id, project, thread["id"], turn_params,
                )
                turn_id = turn["id"]
            else:
                turn_id = app.call("turn/start", turn_params).get("turn", {}).get("id")
        thread = _thread(app, thread["id"], False)
    return {"hostId": LOCAL_HOST_ID, "thread": _summary(thread), "turnId": turn_id}


def send_message_to_thread(config, args, store=None):
    _host(args)
    prompt = args.get("prompt")
    with _session(config) as app:
        thread = _thread(app, args.get("threadId"), True)
        project_id, project, scope = _authorize_thread(config, thread, store)
        web_model = _web_model(config, app, thread.get("model")) if config.get("_dispatch_origin") == "web" else None
        turns = thread.get("turns") if isinstance(thread.get("turns"), list) else []
        active = [turn for turn in turns if isinstance(turn, dict) and turn.get("status") == "inProgress" and isinstance(turn.get("id"), str)]
        thread_status = _summary(thread)["status"]
        if thread_status not in ("active", "idle"):
            raise Fault(503, "Native Codex thread activity cannot be verified")
        if (thread_status == "active") != bool(active):
            raise Fault(409, "Native Codex thread status conflicts with its turn list")
        if active:
            if web_model is not None and thread.get("effort") != web_model["reasoning_effort"]:
                raise Fault(409, "Cannot verify the active Native Codex Web reasoning effort")
            claim = None
            if scope == "workspace-write":
                claim = store.app_writer_claim_for_thread(thread["id"]) if store is not None else None
                if (claim is None or claim.get("state") != "running"
                        or claim.get("turn_id") != active[-1]["id"]):
                    raise Fault(409, "Active workspace-write turn has no matching durable owner")
            try:
                steer_result = app.call("turn/steer", {
                    "threadId": thread["id"],
                    "expectedTurnId": active[-1]["id"],
                    "input": _text_input(prompt),
                })
                turn_id = steer_result.get("turnId") if isinstance(steer_result, dict) else None
            except Exception as exc:
                if claim is not None:
                    store.mark_app_writer_unknown(claim["id"], {
                        "thread_id": thread["id"], "turn_id": active[-1]["id"],
                        "reason": "Native turn/steer outcome is ambiguous",
                    })
                    raise Fault(503, "Native workspace-write outcome is unknown; no automatic replay") from exc
                raise
            if claim is not None and turn_id != active[-1]["id"]:
                store.mark_app_writer_unknown(claim["id"], {
                    "thread_id": thread["id"], "turn_id": active[-1]["id"],
                    "reason": "Native turn/steer did not confirm the claimed active turn",
                })
                raise Fault(503, "Native workspace-write outcome is unknown; turn identity changed")
            mode = "steer"
        else:
            turn_params = {"threadId": thread["id"], "input": _text_input(prompt)}
            if web_model is not None:
                turn_params["model"] = web_model["model"]
                turn_params["effort"] = web_model["reasoning_effort"]
            if scope == "workspace-write":
                turn = _start_workspace_write_turn(
                    app, store, config, project_id, project, thread["id"], turn_params,
                )
                turn_id = turn["id"]
            else:
                result = app.call("turn/start", turn_params)
                turn_id = result.get("turn", {}).get("id")
            mode = "new_turn"
    return {"hostId": LOCAL_HOST_ID, "threadId": thread["id"], "turnId": turn_id, "mode": mode}


def set_thread_title(config, args, store=None):
    _host(args)
    title = args.get("title")
    if not isinstance(title, str) or not title.strip():
        raise Fault(400, "title must be a non-empty string")
    with _session(config) as app:
        thread = _thread(app, args.get("threadId"), False)
        _authorize_thread(config, thread, store)
        app.call("thread/set-name", {"threadId": thread["id"], "name": title.strip()})
        thread = _thread(app, thread["id"], False)
    return {"hostId": LOCAL_HOST_ID, "thread": _summary(thread)}


def set_thread_archived(config, args, store=None):
    _host(args)
    archived = args.get("archived")
    if not isinstance(archived, bool):
        raise Fault(400, "archived must be boolean")
    with _session(config) as app:
        thread = _thread(app, args.get("threadId"), False)
        _authorize_thread(config, thread, store)
        method = "thread/archive" if archived else "thread/unarchive"
        app.call(method, {"threadId": thread["id"]})
    return {"hostId": LOCAL_HOST_ID, "threadId": thread["id"], "archived": archived}


def wait_threads(config, args, store=None):
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
                thread = _thread(app, thread_id, True)
                _authorize_thread(config, thread, store)
                summary = _summary(thread)
                states[thread_id] = summary
                if summary["status"] == "active":
                    all_done = False
                elif summary["status"] == "idle":
                    _settle_app_writer_from_thread(store, thread)
                    unresolved = store.app_writer_claim_for_thread(thread_id) if store is not None else None
                    if unresolved is not None:
                        raise Fault(503, "Native App writer outcome remains unknown; keep the resource blocked")
                else:
                    raise Fault(503, "Native Codex thread activity cannot be verified")
            if all_done:
                return {"hostId": LOCAL_HOST_ID, "completed": True, "timedOut": False, "threads": [states[item] for item in ids]}
            if time.monotonic() >= deadline:
                return {"hostId": LOCAL_HOST_ID, "completed": False, "timedOut": True, "threads": [states[item] for item in ids]}
            time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))


def call_tool(name, args, config, store=None):
    if name not in TOOL_MAP:
        raise Fault(404, "Unknown codex-x-app tool")
    if not isinstance(args, dict):
        raise Fault(400, "Tool arguments must be an object")
    if name == "list_threads":
        return list_threads(config, args, store)
    if name == "read_thread":
        return read_thread(config, args, store)
    if name == "create_thread":
        return create_thread(config, args, store)
    if name == "fork_thread":
        return fork_thread(config, args, store)
    if name == "send_message_to_thread":
        return send_message_to_thread(config, args, store)
    if name == "set_thread_title":
        return set_thread_title(config, args, store)
    if name == "set_thread_archived":
        return set_thread_archived(config, args, store)
    if name == "wait_threads":
        return wait_threads(config, args, store)
    raise Fault(404, "Unknown codex-x-app tool")
