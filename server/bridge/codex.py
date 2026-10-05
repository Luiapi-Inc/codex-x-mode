import json
import os
import queue
import subprocess
import threading
import time

from .core import project_config
from . import siwc


class TaskCancelled(Exception):
    pass


class ModelSelectionError(RuntimeError):
    pass


class AppServer:
    """One owned stdio app-server process per dispatched turn."""
    def __init__(self, command, timeout=600, stop=None, cancel=None, *, env=None):
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, start_new_session=True, env=env)
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
        import os
        import signal
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
        for handle in (self.process.stdin, self.process.stdout):
            handle.close()
        self.reader.join(timeout=2)


def _app_server(config, timeout=600, stop=None, cancel=None, *, backend="codex_app_server", credentials=None):
    if backend == "chatgpt_plan":
        if credentials is None:
            raise siwc.SiwcError("ChatGPT plan authorization is required")
        env = dict(os.environ)
        for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_BRIDGE_PROVIDER_KEY", "ACCESS_TOKEN"):
            env.pop(key, None)
        env["ACCESS_TOKEN"] = credentials["access_token"]
        command = config["codex_command"] + ["app-server", "--listen", "stdio://"]
        settings = {
            "model_provider": '"openai_chatgpt_plan"',
            "model_providers.openai_chatgpt_plan.name": '"ChatGPT plan"',
            "model_providers.openai_chatgpt_plan.base_url": '"https://api.openai.com/v1"',
            "model_providers.openai_chatgpt_plan.env_key": '"ACCESS_TOKEN"',
            "model_providers.openai_chatgpt_plan.wire_api": '"responses"',
            "model_providers.openai_chatgpt_plan.requires_openai_auth": "false",
            "model_providers.openai_chatgpt_plan.supports_websockets": "false",
            "model_providers.openai_chatgpt_plan.request_max_retries": "0",
            "model_providers.openai_chatgpt_plan.stream_max_retries": "0",
            "shell_environment_policy": '{inherit="core",ignore_default_excludes=false,filters={ACCESS_TOKEN="exclude"},set={},experimental_use_profile=false}',
        }
        for key, value in settings.items():
            command.extend(["-c", key + "=" + value])
        return AppServer(command, timeout, stop, cancel, env=env)
    if backend != "codex_app_server":
        raise ModelSelectionError("Task execution backend is invalid")
    command = config["codex_command"] + ["-c", 'model_provider="openai"', "app-server"]
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


def _select_model(models, requested=None):
    if requested is None:
        matches = [item for item in models if item.get("isDefault") is True]
        if len(matches) != 1:
            raise ModelSelectionError("Codex account has no unambiguous default model")
        selected = matches[0]
        source = "account_default"
    else:
        matches = [item for item in models if item.get("id") == requested]
        if len(matches) != 1:
            raise ModelSelectionError("Requested model is unavailable to the Codex account")
        selected = matches[0]
        source = "requested"
    return {
        "id": selected["id"],
        "model": selected["model"],
        "display_name": selected.get("displayName") if isinstance(selected.get("displayName"), str) else selected["id"],
        "source": source,
    }


def list_models(config):
    """Return the Codex app-server catalog without claiming model entitlement."""
    app = None
    try:
        app = _app_server(config, timeout=config.get("model_list_timeout_seconds", 60))
        app.call("initialize", {"clientInfo": {"name": "codex_x_mode", "version": "0.2.11"}})
        app.send({"method": "initialized", "params": {}})
        items = _model_catalog(app)
        return {
            "backend": "codex_app_server",
            "catalog_source": "model/list",
            "catalog_integrity_verified": True,
            "model_entitlement_verified": False,
            "models": [{
                "id": item["id"],
                "model": item["model"],
                "display_name": item.get("displayName") if isinstance(item.get("displayName"), str) else item["id"],
                "is_default": item.get("isDefault") is True,
                "supported_reasoning_efforts": [
                    value.get("reasoningEffort") for value in item.get("supportedReasoningEfforts", [])
                    if isinstance(value, dict) and isinstance(value.get("reasoningEffort"), str)
                ] if isinstance(item.get("supportedReasoningEfforts", []), list) else [],
            } for item in items],
        }
    finally:
        if app:
            app.close()


def _redact_tokens(value, credentials):
    if credentials is None:
        return value
    if isinstance(value, str):
        for key in ("access_token", "refresh_token", "id_token"):
            token = credentials.get(key)
            if isinstance(token, str) and token:
                value = value.replace(token, "[REDACTED]")
        return value
    if isinstance(value, list):
        return [_redact_tokens(item, credentials) for item in value]
    if isinstance(value, dict):
        return {key: _redact_tokens(item, credentials) for key, item in value.items()}
    return value


def run_task(config, store, job, stop=None):
    app = None
    phase = "load_task"
    turn_start_attempted = False
    selected_model = None
    model_reroutes = []
    try:
        payload = job["payload"]
        backend = payload.get("execution_backend", "codex_app_server")
        if config.get("_dispatch_origin") == "web" and "execution_backend" not in payload:
            raise ModelSelectionError("Legacy task origin is unverified; reconcile it before web dispatch")
        phase = "validate_project"
        project = project_config(config, payload["project_id"], payload["scope"])
        credentials = None
        if backend == "chatgpt_plan":
            phase = "chatgpt_plan_authorization"
            credentials = siwc.get_credentials(config)
            if payload.get("siwc_registration") != siwc.registration(credentials):
                raise siwc.SiwcError("Task account registration no longer matches the authorized account")
            phase = "model_catalog"
            catalog = siwc.list_models(config, credentials=credentials)
            snapshot = payload.get("selected_model")
            if not isinstance(snapshot, dict) or not isinstance(snapshot.get("id"), str):
                raise siwc.SiwcError("ChatGPT plan task has no validated model selection")
            selected_model = siwc.select_model(catalog, snapshot["id"])
            if selected_model["model"] != snapshot.get("model"):
                raise siwc.SiwcError("Task model selection no longer matches the account catalog")
            selected_model["source"] = snapshot["source"]
        phase = "launch_app_server"
        launch_args = (config, config.get("task_timeout_seconds", 600), stop, lambda: store.cancel_requested(job["id"]))
        app = (_app_server(*launch_args, backend=backend, credentials=credentials) if backend == "chatgpt_plan"
               else _app_server(*launch_args, backend=backend))
        phase = "initialize"
        app.call("initialize", {"clientInfo": {"name": "codex_x_mode", "title": "Codex X Mode", "version": "0.2.11"}})
        app.send({"method": "initialized", "params": {}})
        if backend == "codex_app_server":
            phase = "model_catalog"
            try:
                selected_model = _select_model(_model_catalog(app), payload.get("model_version"))
            except ModelSelectionError:
                raise
            except Exception as exc:
                raise ModelSelectionError("Codex model catalog could not be verified") from exc
        params = {"cwd": project["cwd"], "approvalPolicy": "never",
                  "sandbox": "read-only" if payload["scope"] == "read-only" else "workspace-write",
                  "model": selected_model["model"]}
        if backend == "chatgpt_plan":
            params["modelProvider"] = "openai_chatgpt_plan"
        parent_id = payload.get("parent_task_id")
        if parent_id:
            params["threadId"] = store.get(parent_id, "task")["thread_id"]
        phase = "thread_start"
        thread = app.call("thread/resume" if parent_id else "thread/start", params)["thread"]
        store.update_task(job["id"], thread_id=thread["id"])
        phase = "turn_start"
        turn_start_attempted = True
        turn = app.call("turn/start", {"threadId": thread["id"], "cwd": project["cwd"], "approvalPolicy": "never",
                                       "model": selected_model["model"],
                                       "input": [{"type": "text", "text": payload["prompt"]}]})["turn"]
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
                    observations.append({"type": "commandExecution", "exit_code": item.get("exitCode"),
                                         "status": item.get("status")})
            if message.get("method") == "turn/completed":
                final = params["turn"]
                state = {"completed": "completed", "failed": "failed", "interrupted": "interrupted"}.get(final["status"], "unknown")
                observed_model = final.get("model") if isinstance(final.get("model"), str) else None
                model_verified = not model_reroutes and observed_model == selected_model["model"]
                inference_verified = final["status"] == "completed" and model_verified
                if not model_verified:
                    state = "unknown"
                store.update_task(job["id"], state=state,
                                  result=_redact_tokens({"answer": answer, "observations": observations, "codex_status": final["status"],
                                          "model_selection": {
                                              "backend": backend,
                                              "catalog_source": "responses_api_models" if backend == "chatgpt_plan" else "model/list",
                                              "id": selected_model["id"],
                                              "model": selected_model["model"],
                                              "source": selected_model["source"],
                                              "catalog_integrity_verified": True,
                                              "explicitly_sent": True,
                                              "observed_model": observed_model,
                                              "reroutes": model_reroutes,
                                              "model_identity_verified": model_verified,
                                              "inference_verified": inference_verified,
                                          }}, credentials))
                phase = "terminal_event_verified"
                break
    except TaskCancelled:
        store.update_task(job["id"], state="cancelled", result={
            "cancelled": True, "failure_phase": phase, "execution_may_have_started": turn_start_attempted,
        })
    except (ModelSelectionError, siwc.SiwcError) as exc:
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
        # Preserve the phase and exception class without logging prompts, tokens,
        # paths, or raw exception text. Never replay an ambiguous turn start.
        store.update_task(job["id"], state="unknown" if turn_start_attempted else "failed", result={
            "error": "Codex connection failed",
            "failure_phase": phase,
            "error_type": type(exc).__name__,
            "execution_may_have_started": turn_start_attempted,
        })
    finally:
        if app:
            app.close()


def worker(config, store, stop):
    while not stop.is_set():
        job = store.claim("task")
        if job:
            run_task(config, store, job, stop)
        else:
            stop.wait(0.2)
