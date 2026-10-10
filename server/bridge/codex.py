import errno
import json
import os
import queue
import shutil
import subprocess
import tempfile
from pathlib import Path
import threading
import time

from .core import project_config


class TaskCancelled(Exception):
    pass


class ModelSelectionError(RuntimeError):
    pass


class AppServer:
    """One owned stdio app-server process per dispatched turn."""
    def __init__(self, command, timeout=600, stop=None, cancel=None, *, env=None, cleanup=None):
        self.cleanup = cleanup
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, text=True, start_new_session=True, env=env)
        except Exception:
            if self.cleanup is not None:
                self.cleanup()
                self.cleanup = None
            raise
        self.messages = queue.Queue(maxsize=1024)
        self.notifications = []
        self.sequence = 0
        self.deadline = time.monotonic() + timeout
        self.stop = stop
        self.cancel = cancel
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(1024 * 1024 + 1)
                if not line:
                    break
                if len(line) > 1024 * 1024:
                    raise RuntimeError("Oversized app-server message")
                self.messages.put(json.loads(line), timeout=5)
        except (ValueError, RuntimeError, queue.Full) as exc:
            try:
                self.messages.put(exc, timeout=1)
            except queue.Full:
                pass
        finally:
            try:
                self.messages.put(None, timeout=1)
            except queue.Full:
                pass

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def receive(self):
        while True:
            remaining = self.deadline - time.monotonic()
            if self.cancel is not None and self.cancel():
                raise TaskCancelled("Task cancellation requested")
            if remaining <= 0 or self.stop is not None and self.stop.is_set():
                raise TimeoutError("Codex turn deadline or shutdown reached")
            try:
                message = self.messages.get(timeout=min(remaining, 1))
                break
            except queue.Empty:
                continue
        if message is None:
            raise RuntimeError("Codex app-server disconnected")
        if isinstance(message, Exception):
            raise message
        if "method" in message and "id" in message:
            if message["method"].endswith("requestApproval"):
                self.send({"id": message["id"], "result": {"decision": "cancel"}})
            else:
                self.send({"id": message["id"], "error": {"code": -32601, "message": "Client operation disabled"}})
            return self.receive()
        return message

    def call(self, method, params):
        self.sequence += 1
        request_id = self.sequence
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            message = self.receive()
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError("Codex app-server rejected " + method)
                return message["result"]
            if "method" in message:
                self.notifications.append(message)

    def close(self):
        import signal
        stopped = False
        try:
            if self.process.poll() is None:
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                except OSError:
                    pass
            if self.process.poll() is None:
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if self.process.poll() is None:
                        try:
                            os.killpg(self.process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        self.process.wait(timeout=5)
            stopped = self._wait_process_group_stopped(timeout=2)
            if not stopped and self.process.poll() is None:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError:
                    pass
                if self.process.poll() is None:
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                stopped = self._wait_process_group_stopped(timeout=2)
        except Exception:
            stopped = False
        finally:
            for handle in (self.process.stdin, self.process.stdout):
                try:
                    handle.close()
                except Exception:
                    pass
            self.reader.join(timeout=2)
        if stopped and self.cleanup is not None:
            try:
                self.cleanup()
                self.cleanup = None
            except Exception:
                pass
        elif self.cleanup is not None:
            owner = getattr(self.cleanup, "__self__", None)
            finalizer = getattr(owner, "_finalizer", None)
            if finalizer is not None and finalizer.alive:
                finalizer.detach()
            self.cleanup = None
        return stopped

    def _wait_process_group_stopped(self, timeout=2):
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
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))


def _isolated_native_codex_env(config):
    """Create a clean Codex home that reuses only Native Codex auth state.

    Codex X Mode never reads or interprets the credential contents. The opaque
    auth file is copied with mode 600 so user-level custom provider/catalog
    configuration cannot influence Web execution.
    """
    source_home = Path(config.get("native_codex_home") or os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser()
    auth_source = source_home / "auth.json"
    if not auth_source.is_file():
        raise ModelSelectionError("Native Codex ChatGPT authentication is unavailable")
    isolated = tempfile.TemporaryDirectory(prefix="codex-x-native-app-server-")
    auth_target = Path(isolated.name) / "auth.json"
    try:
        shutil.copy2(auth_source, auth_target)
        auth_target.chmod(0o600)
    except Exception:
        isolated.cleanup()
        raise
    env = dict(os.environ)
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_BRIDGE_PROVIDER_KEY", "ACCESS_TOKEN"):
        env.pop(key, None)
    env["CODEX_HOME"] = isolated.name
    return env, isolated.cleanup


def _app_server(config, timeout=600, stop=None, cancel=None, *, backend="codex_app_server", native_isolated=False):
    if backend != "codex_app_server":
        raise ModelSelectionError("Task execution backend is invalid")

    command = config["codex_command"] + ["-c", 'model_provider="openai"', "app-server", "--listen", "stdio://"]
    if native_isolated:
        env, cleanup = _isolated_native_codex_env(config)
        try:
            return AppServer(command, timeout, stop, cancel, env=env, cleanup=cleanup)
        except Exception:
            cleanup()
            raise
    return AppServer(command, timeout, stop, cancel)


def _model_catalog(app):
    models, cursors = [], set()
    cursor = None
    for _ in range(20):
        params = {"includeHidden": False, "limit": 100}
        if cursor is not None:
            params["cursor"] = cursor
        page = app.call("model/list", params)
        if not isinstance(page, dict) or not isinstance(page.get("data"), list):
            raise ModelSelectionError("Codex model catalog is unavailable")
        for item in page["data"]:
            if not isinstance(item, dict):
                raise ModelSelectionError("Codex model catalog is invalid")
            model_id, model = item.get("id"), item.get("model")
            if not isinstance(model_id, str) or not model_id or not isinstance(model, str) or not model:
                raise ModelSelectionError("Codex model catalog is incomplete")
            models.append(item)
        cursor = page.get("nextCursor")
        if cursor is None:
            return models
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise ModelSelectionError("Codex model catalog pagination is invalid")
        cursors.add(cursor)
    raise ModelSelectionError("Codex model catalog exceeded the supported page limit")


def _select_model(models, requested=None, *, required_prefix=None, default_model=None, reasoning_effort=None):
    eligible = [
        item for item in models
        if required_prefix is None or (isinstance(item.get("id"), str) and item["id"].startswith(required_prefix))
    ]
    family_alias = required_prefix[:-1] if isinstance(required_prefix, str) and required_prefix.endswith("/") else None
    if requested == family_alias:
        requested = None
    if requested is None:
        if default_model is not None:
            matches = [item for item in eligible if item.get("id") == default_model]
            source = "configured_default"
        else:
            matches = [item for item in eligible if item.get("isDefault", item.get("is_default")) is True]
            source = "account_default"
        if len(matches) != 1:
            raise ModelSelectionError("Codex account has no unambiguous default model")
        selected = matches[0]
    else:
        matches = [item for item in eligible if item.get("id") == requested]
        if len(matches) != 1:
            raise ModelSelectionError("Requested model is unavailable to the Codex account")
        selected = matches[0]
        source = "requested"
    if required_prefix is not None:
        model_id = selected.get("id")
        model = selected.get("model")
        if (not isinstance(model_id, str) or not model_id.startswith(required_prefix)
                or not model_id[len(required_prefix):].strip()
                or model != model_id):
            raise ModelSelectionError("Native Codex did not expose an exact Web model identity")
    raw_supported = selected.get("supportedReasoningEfforts", selected.get("supported_reasoning_efforts", []))
    if isinstance(raw_supported, list):
        supported = [
            value if isinstance(value, str) else value.get("reasoningEffort")
            for value in raw_supported
            if isinstance(value, str) or isinstance(value, dict)
        ]
        supported = [value for value in supported if isinstance(value, str)]
    else:
        supported = []
    default_effort = selected.get("defaultReasoningEffort", selected.get("default_reasoning_effort"))
    if not isinstance(default_effort, str):
        default_effort = None
    effort = reasoning_effort or default_effort
    if effort is None and len(supported) == 1:
        effort = supported[0]
    if effort is not None and supported and effort not in supported:
        raise ModelSelectionError("Selected model does not support the requested reasoning effort")
    if required_prefix is not None and (not effort or effort not in supported):
        raise ModelSelectionError("Selected Web reasoning effort is not verified by Native Codex metadata")
    return {
        "id": selected["id"],
        "model": selected["model"],
        "display_name": selected.get("displayName", selected.get("display_name"))
        if isinstance(selected.get("displayName", selected.get("display_name")), str) else selected["id"],
        "source": source,
        "default_reasoning_effort": default_effort,
        "supported_reasoning_efforts": supported,
        "reasoning_effort": effort,
    }


def list_models(config, required_prefix=None, *, native_isolated=False):
    """Return the Native Codex app-server catalog without claiming inference entitlement."""
    app = None
    try:
        app = _app_server(
            config,
            timeout=config.get("model_list_timeout_seconds", 60),
            backend="codex_app_server",
            native_isolated=native_isolated,
        )
        app.call("initialize", {"clientInfo": {"name": "codex_x_mode", "version": "1.0.0-rc.8"}})
        app.send({"method": "initialized", "params": {}})
        items = _model_catalog(app)
        if required_prefix is not None:
            filtered = []
            for item in items:
                model_id = item.get("id")
                if isinstance(model_id, str) and model_id.startswith(required_prefix):
                    if item.get("model") != model_id:
                        raise ModelSelectionError("Native Codex Web model ID and execution model differ")
                    filtered.append(item)
            items = filtered
        return {
            "backend": "codex_app_server",
            "catalog_source": "model/list",
            "catalog_integrity_verified": True,
            "model_entitlement_verified": False,
            "required_prefix": required_prefix,
            "models": [{
                "id": item["id"],
                "model": item["model"],
                "display_name": item.get("displayName") if isinstance(item.get("displayName"), str) else item["id"],
                "is_default": item.get("isDefault") is True,
                "default_reasoning_effort": item.get("defaultReasoningEffort") if isinstance(item.get("defaultReasoningEffort"), str) else None,
                "supported_reasoning_efforts": [
                    value.get("reasoningEffort") for value in item.get("supportedReasoningEfforts", [])
                    if isinstance(value, dict) and isinstance(value.get("reasoningEffort"), str)
                ] if isinstance(item.get("supportedReasoningEfforts", []), list) else [],
            } for item in items],
        }
    finally:
        if app:
            app.close()


def run_task(config, store, job, stop=None):
    app = None
    phase = "load_task"
    turn_start_attempted = False
    selected_model = None
    model_reroutes = []
    try:
        payload = job["payload"]
        backend = payload.get("execution_backend", "codex_app_server")
        dispatch_origin = payload.get("dispatch_origin")
        if dispatch_origin is None:
            # Preserve legacy persisted tasks for reconciliation only.
            dispatch_origin = "web" if backend in ("chatgpt_web_headless", "chatgpt_plan") else "local"
        if config.get("_dispatch_origin") == "web" and "execution_backend" not in payload:
            raise ModelSelectionError("Legacy task origin is unverified; reconcile it before web dispatch")
        phase = "validate_project"
        project = project_config(config, payload["project_id"], payload["scope"])

        if backend in ("chatgpt_web_headless", "chatgpt_plan"):
            phase = "reject_legacy_web_route"
            raise ModelSelectionError("Legacy Web provider routes are disabled; reconcile and resubmit through Native Codex")
        if backend != "codex_app_server":
            phase = "validate_backend"
            raise ModelSelectionError("Task execution backend is invalid")
        if dispatch_origin == "web":
            snapshot = payload.get("selected_model")
            native_v1 = config.get("web_model_policy") == "native"
            if (not isinstance(snapshot, dict)
                    or not isinstance(snapshot.get("id"), str)
                    or not snapshot["id"]
                    or (not native_v1 and not snapshot["id"].startswith("chatgpt-web/"))
                    or snapshot.get("model") != snapshot["id"]):
                raise ModelSelectionError("Web task has no validated Native Codex model selection")
            selected_model = dict(snapshot)

        phase = "launch_app_server"
        launch_args = (config, config.get("task_timeout_seconds", 600), stop,
                       lambda: store.cancel_requested(job["id"]))
        app = _app_server(
            *launch_args,
            backend="codex_app_server",
            native_isolated=dispatch_origin == "web",
        )
        phase = "initialize"
        app.call("initialize", {"clientInfo": {"name": "codex_x_mode", "title": "Codex X Mode", "version": "1.0.0-rc.8"}})
        app.send({"method": "initialized", "params": {}})

        if dispatch_origin == "web":
            phase = "native_model_catalog"
            runtime_model = _select_model(
                _model_catalog(app),
                selected_model["id"],
                required_prefix=None if config.get("web_model_policy") == "native" else "chatgpt-web/",
                reasoning_effort=selected_model.get("reasoning_effort"),
            )
            if (runtime_model["model"] != selected_model["model"]
                    or (config.get("web_model_policy") == "native"
                        and (runtime_model["id"] != runtime_model["model"]
                             or runtime_model["reasoning_effort"] not in runtime_model["supported_reasoning_efforts"]))):
                raise ModelSelectionError("Web model no longer matches the Native Codex catalog")
            selected_model["reasoning_effort"] = runtime_model.get("reasoning_effort")
            selected_model["default_reasoning_effort"] = runtime_model.get("default_reasoning_effort")
            selected_model["supported_reasoning_efforts"] = runtime_model.get("supported_reasoning_efforts", [])
        else:
            phase = "model_catalog"
            selected_model = _select_model(_model_catalog(app), payload.get("model_version"))

        thread_params = {
            "cwd": project["cwd"],
            "approvalPolicy": "never",
            "sandbox": "read-only" if payload["scope"] == "read-only" else "workspace-write",
            "model": selected_model["model"],
        }
        parent_id = payload.get("parent_task_id")
        if parent_id:
            thread_params["threadId"] = store.get(parent_id, "task")["thread_id"]
        phase = "thread_start"
        thread = app.call("thread/resume" if parent_id else "thread/start", thread_params)["thread"]
        store.update_task(job["id"], thread_id=thread["id"])

        phase = "turn_start"
        turn_start_attempted = True
        turn_params = {
            "threadId": thread["id"],
            "cwd": project["cwd"],
            "approvalPolicy": "never",
            "model": selected_model["model"],
            "input": [{"type": "text", "text": payload["prompt"]}],
        }
        if isinstance(selected_model.get("reasoning_effort"), str):
            turn_params["effort"] = selected_model["reasoning_effort"]
        turn = app.call("turn/start", turn_params)["turn"]
        store.update_task(job["id"], turn_id=turn["id"])

        phase = "wait_for_turn"
        answer, observations = "", []
        while True:
            message = app.notifications.pop(0) if app.notifications else app.receive()
            params = message.get("params", {})
            if params.get("threadId") != thread["id"]:
                continue
            if params.get("turnId", params.get("turn", {}).get("id")) != turn["id"]:
                continue
            if message.get("method") == "model/rerouted":
                model_reroutes.append({
                    "from_model": params.get("fromModel"),
                    "to_model": params.get("toModel"),
                })
            if message.get("method") == "item/completed":
                item = params.get("item", {})
                if item.get("type") == "agentMessage":
                    answer = str(item.get("text", ""))[:50000]
                if item.get("type") == "commandExecution" and len(observations) < 50:
                    observations.append({
                        "type": "commandExecution",
                        "exit_code": item.get("exitCode"),
                        "status": item.get("status"),
                    })
            if message.get("method") == "turn/completed":
                final = params["turn"]
                state = {"completed": "completed", "failed": "failed", "interrupted": "interrupted"}.get(final["status"], "unknown")
                observed_model = final.get("model") if isinstance(final.get("model"), str) else None
                observed_source = "turn/completed.turn.model" if observed_model is not None else None
                # Recent Native app-server releases may omit model in
                # turn/completed. Thread-level model is useful diagnostic
                # provenance, but cannot attest to actual terminal inference.
                thread_readback = None
                if (observed_model is None and dispatch_origin == "web"
                        and config.get("web_model_policy") == "native"):
                    try:
                        native_read = app.call("thread/read", {
                            "threadId": thread["id"], "includeTurns": True,
                        })
                        native_thread = native_read.get("thread", {})
                        matched = [value for value in native_thread.get("turns", [])
                                   if isinstance(value, dict) and value.get("id") == turn["id"]]
                        if native_thread.get("id") == thread["id"] and len(matched) == 1:
                            matching_turn = matched[0]
                            readback_model = matching_turn.get("model")
                            valid_turn_model = (
                                isinstance(readback_model, str) and bool(readback_model)
                                and matching_turn.get("status") == final["status"]
                            )
                            if valid_turn_model:
                                observed_model = readback_model
                                observed_source = "thread/read.turn.model"
                            thread_readback = {
                                "source": "thread/read",
                                "thread_model_matches_selected":
                                    native_thread.get("model") == selected_model["model"],
                                "turn_status": matching_turn.get("status"),
                                "per_turn_model_present": valid_turn_model,
                                "terminal_model_identity_verified":
                                    valid_turn_model and readback_model == selected_model["model"]
                                    and not model_reroutes,
                            }
                    except Exception as exc:
                        thread_readback = {"source": "thread/read",
                                           "readback_error_type": type(exc).__name__,
                                           "terminal_model_identity_verified": False}
                from .terminal_evidence import classify_native_terminal
                verification = classify_native_terminal(
                    selected_model=selected_model["model"],
                    terminal_status=final["status"],
                    reported_model=observed_model,
                    reroutes=model_reroutes,
                    thread_config_matches=None if thread_readback is None
                        else thread_readback.get("thread_model_matches_selected"),
                    model_proof_source=observed_source,
                )
                model_verified = verification["model_attestation"] == "verified"
                inference_verified = verification["acceptance_verified"]
                if not model_verified:
                    state = "unknown"
                store.update_task(
                    job["id"],
                    state=state,
                    result={
                        "answer": answer,
                        "observations": observations,
                        "codex_status": final["status"],
                        "terminal_error": final.get("error"),
                        "execution_evidence": verification,
                        "model_selection": {
                            "backend": backend,
                            "dispatch_origin": dispatch_origin,
                            "catalog_source": "model/list",
                            "id": selected_model["id"],
                            "model": selected_model["model"],
                            "source": selected_model["source"],
                            "reasoning_effort": selected_model.get("reasoning_effort"),
                            "supported_reasoning_efforts": selected_model.get("supported_reasoning_efforts", []),
                            "catalog_integrity_verified": True,
                            "explicitly_sent": True,
                            "observed_model": observed_model,
                            "thread_readback": thread_readback,
                            "reroutes": model_reroutes,
                            "model_identity_verified": model_verified,
                            "inference_verified": inference_verified,
                            "native_codex_owns_inference": True,
                        },
                    },
                )
                phase = "terminal_event_verified"
                break
    except TaskCancelled:
        store.update_task(job["id"], state="unknown" if turn_start_attempted else "cancelled", result={
            "cancel_requested": True,
            "failure_phase": phase,
            "execution_may_have_started": turn_start_attempted,
            "error": "Native turn stop was not confirmed" if turn_start_attempted else None,
        })
    except ModelSelectionError as exc:
        store.update_task(job["id"], state="failed", result={
            "error": str(exc),
            "failure_phase": phase,
            "error_type": type(exc).__name__,
            "execution_may_have_started": False,
        })
    except FileNotFoundError:
        store.update_task(job["id"], state="unknown" if turn_start_attempted else "failed", result={
            "error": "Codex executable unavailable on bridge host",
            "failure_phase": phase,
            "error_type": "FileNotFoundError",
            "execution_may_have_started": turn_start_attempted,
        })
    except Exception as exc:
        store.update_task(job["id"], state="unknown" if turn_start_attempted else "failed", result={
            "error": "Codex connection failed",
            "failure_phase": phase,
            "error_type": type(exc).__name__,
            "execution_may_have_started": turn_start_attempted,
        })
    finally:
        if app:
            # A Native terminal notification establishes turn completion, but
            # writer safety also requires the owned app-server process to stop.
            # Missing model identity still fails task acceptance.
            process_stopped = app.close()
            if process_stopped is True:
                store.finalize_unverified_terminal_task(job["id"])


def worker(config, store, stop):
    while not stop.is_set():
        job = store.claim("task")
        if job:
            run_task(config, store, job, stop)
        else:
            stop.wait(0.2)
