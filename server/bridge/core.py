import hashlib
import json
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path


class Fault(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def text(value, label, limit=50000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise Fault(400, f"Invalid {label}")
    return value


def fields(body, required, optional=()):
    if not isinstance(body, dict) or set(body) - set(required) - set(optional):
        raise Fault(400, "Unexpected request fields")
    if not set(required) <= set(body):
        raise Fault(400, "Missing request fields")


TERMINAL = {"completed", "failed", "expired", "interrupted", "unknown", "cancelled"}


class Store:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL,
            payload TEXT NOT NULL, result TEXT, request_key TEXT NOT NULL,
            digest TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
            lease TEXT, claim_key TEXT, completion_key TEXT, completion_digest TEXT,
            cancel_key TEXT, cancel_digest TEXT, thread_id TEXT, turn_id TEXT, UNIQUE(kind,request_key))""")
        self._migrate_columns()
        with self.db:
            self.db.execute("UPDATE jobs SET state='unknown',result=? WHERE kind='task' AND state IN ('running','cancelling')",
                            (encoded({"error": "Server restarted; recover in Codex before retrying"}),))

    def _migrate_columns(self):
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(jobs)")}
        for name in ("claim_key", "cancel_key", "cancel_digest"):
            if name not in columns:
                self.db.execute(f"ALTER TABLE jobs ADD COLUMN {name} TEXT")
        self.db.commit()

    def close(self):
        self.db.close()

    def _decode(self, row):
        if row is None:
            raise Fault(404, "Unknown job")
        row = dict(row)
        row["payload"] = json.loads(row["payload"])
        row["result"] = json.loads(row["result"]) if row["result"] else None
        return row

    def expire(self):
        with self.lock, self.db:
            self.db.execute("UPDATE jobs SET state='expired' WHERE kind='backend' AND state IN ('queued','claimed') AND expires<=?",
                            (time.time(),))

    def get(self, job_id, kind=None):
        self.expire()
        with self.lock:
            row = self._decode(self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
        if kind and row["kind"] != kind:
            raise Fault(404, "Unknown job")
        return row

    def find_by_request(self, kind, request_key):
        self.expire()
        with self.lock:
            row = self.db.execute("SELECT * FROM jobs WHERE kind=? AND request_key=?", (kind, request_key)).fetchone()
            return self._decode(row) if row else None

    def create(self, kind, payload, key, ttl=600):
        text(key, "request_key", 200)
        raw = encoded(payload)
        digest_payload = dict(payload)
        # The alias list is derived from host configuration, not user input.
        digest_payload.pop("resource_project_ids", None)
        digest = hashlib.sha256(encoded(digest_payload).encode()).hexdigest()
        with self.lock, self.db:
            prior = self.db.execute("SELECT * FROM jobs WHERE kind=? AND request_key=?", (kind, key)).fetchone()
            if prior:
                prior = self._decode(prior)
                if prior["digest"] != digest:
                    raise Fault(409, "Request key already used for different input")
                return prior
            if kind == "task":
                # Unknown executions only conflict when both may write the same
                # canonical resource. Read-only tasks are sandboxed and cannot
                # be poisoned by uncertainty about another task's outcome.
                if payload.get("scope") != "read-only":
                    rows = self.db.execute(
                        "SELECT id,payload FROM jobs WHERE kind='task' AND state='unknown'"
                    ).fetchall()
                    resource = payload.get("resource_id", payload.get("project_id"))
                    for row in rows:
                        prior_payload = json.loads(row["payload"])
                        prior_resource = prior_payload.get("resource_id", prior_payload.get("project_id"))
                        aliases = set(payload.get("resource_project_ids", [payload.get("project_id")]))
                        prior_aliases = set(prior_payload.get(
                            "resource_project_ids", [prior_payload.get("project_id")]))
                        same_resource = (
                            prior_resource == resource
                            or bool(aliases.intersection(prior_aliases))
                        )
                        if same_resource and prior_payload.get("scope") != "read-only":
                            raise Fault(409, f"Workspace write blocked by unknown task {row['id']}; reconcile it before writing")
            pending = self.db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','claimed','running','cancelling')").fetchone()[0]
            if pending >= 32:
                raise Fault(429, "Queue full")
            parent_id = payload.get("parent_task_id")
            if parent_id:
                parent = self._decode(self.db.execute("SELECT * FROM jobs WHERE id=? AND kind='task'", (parent_id,)).fetchone())
                if parent["state"] != "completed" or not parent["thread_id"]:
                    raise Fault(409, "Parent must be completed with a known Codex thread")
                busy = self.db.execute("SELECT 1 FROM jobs WHERE kind='task' AND state IN ('queued','running','cancelling') AND json_extract(payload,'$.parent_task_id')=?",
                                       (parent_id,)).fetchone()
                if busy:
                    raise Fault(409, "A follow-up for this parent is already active")
            job_id = ("resp_" if kind == "backend" else "task_") + uuid.uuid4().hex
            now = time.time()
            self.db.execute("INSERT INTO jobs(id,kind,state,payload,request_key,digest,created,expires) VALUES (?,?,?,?,?,?,?,?)",
                            (job_id, kind, "queued", raw, key, digest, now, now + ttl))
            return self._decode(self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def claim(self, kind, request_key=None):
        self.expire()
        if request_key is not None:
            text(request_key, "request_key", 200)
        with self.lock, self.db:
            if kind == "backend" and request_key is not None:
                prior = self.db.execute("SELECT * FROM jobs WHERE kind='backend' AND claim_key=? ORDER BY created DESC LIMIT 1", (request_key,)).fetchone()
                if prior:
                    prior = self._decode(prior)
                    if prior["state"] == "claimed" and time.time() < prior["expires"]:
                        return prior
                    raise Fault(409, "Claim request key already used")
            row = self.db.execute("SELECT * FROM jobs WHERE kind=? AND state='queued' ORDER BY created LIMIT 1", (kind,)).fetchone()
            if not row:
                return None
            lease = secrets.token_urlsafe(24)
            state = "claimed" if kind == "backend" else "running"
            if kind == "backend":
                updated = self.db.execute("UPDATE jobs SET state=?,lease=?,claim_key=? WHERE id=? AND state='queued'",
                                          (state, lease, request_key, row["id"]))
            else:
                updated = self.db.execute("UPDATE jobs SET state=?,lease=? WHERE id=? AND state='queued'", (state, lease, row["id"]))
            if updated.rowcount != 1:
                return None
            return self._decode(self.db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())

    def finish_backend(self, job_id, body):
        fields(body, ("lease", "request_key"), ("answer", "calls"))
        text(body["lease"], "lease", 200)
        text(body["request_key"], "request_key", 200)
        with self.lock, self.db:
            job = self.get(job_id, "backend")
            if not job["lease"] or not secrets.compare_digest(body["lease"], job["lease"]):
                raise Fault(403, "Invalid claim")
            digest = hashlib.sha256(encoded(body).encode()).hexdigest()
            if job["state"] == "completed":
                if body["request_key"] == job["completion_key"] and digest == job["completion_digest"]:
                    return job
                raise Fault(409, "Turn already completed")
            if job["state"] != "claimed" or time.time() >= job["expires"]:
                raise Fault(409, "Turn no longer active")
            output = build_output(job["payload"], body)
            result = envelope(job, output)
            self.db.execute("UPDATE jobs SET state='completed',result=?,completion_key=?,completion_digest=? WHERE id=?",
                            (encoded(result), body["request_key"], digest, job_id))
            return self.get(job_id, "backend")


    def cancel_backend(self, job_id, lease, request_key):
        text(lease, "lease", 200)
        text(request_key, "request_key", 200)
        body = {"lease": lease, "request_key": request_key}
        digest = hashlib.sha256(encoded(body).encode()).hexdigest()
        with self.lock, self.db:
            job = self.get(job_id, "backend")
            if not job["lease"] or not secrets.compare_digest(lease, job["lease"]):
                raise Fault(403, "Invalid claim")
            if job["state"] == "cancelled":
                if request_key == job.get("cancel_key") and digest == job.get("cancel_digest"):
                    return job
                raise Fault(409, "Turn already cancelled")
            if job["state"] != "claimed" or time.time() >= job["expires"]:
                raise Fault(409, "Turn no longer active")
            self.db.execute("UPDATE jobs SET state='cancelled',result=?,cancel_key=?,cancel_digest=? WHERE id=?",
                            (encoded({"cancelled": True}), request_key, digest, job_id))
            return self.get(job_id, "backend")

    def cancel_task(self, job_id, request_key):
        text(request_key, "request_key", 200)
        body = {"request_key": request_key}
        digest = hashlib.sha256(encoded(body).encode()).hexdigest()
        with self.lock, self.db:
            job = self.get(job_id, "task")
            if job["state"] == "cancelled":
                if request_key == job.get("cancel_key") and digest == job.get("cancel_digest"):
                    return job
                raise Fault(409, "Task already cancelled")
            if job["state"] == "unknown":
                raise Fault(409, "Unknown execution must be recovered before cancellation")
            if job["state"] in ("completed", "failed", "expired", "interrupted"):
                raise Fault(409, "Task is already terminal")
            state = "cancelled" if job["state"] == "queued" else "cancelling"
            result = {"cancelled": True, "phase": "queued"} if state == "cancelled" else {"cancel_requested": True}
            self.db.execute("UPDATE jobs SET state=?,result=?,cancel_key=?,cancel_digest=? WHERE id=?",
                            (state, encoded(result), request_key, digest, job_id))
            return self.get(job_id, "task")

    def cancel_requested(self, job_id):
        with self.lock:
            row = self.db.execute("SELECT state FROM jobs WHERE id=? AND kind='task'", (job_id,)).fetchone()
            return bool(row and row["state"] == "cancelling")

    def unknown_projects(self):
        with self.lock:
            rows = self.db.execute(
                "SELECT DISTINCT json_extract(payload,'$.project_id') AS project_id "
                "FROM jobs WHERE kind='task' AND state='unknown' "
                "AND COALESCE(json_extract(payload,'$.scope'),'workspace-write')!='read-only' "
                "ORDER BY project_id"
            ).fetchall()
            return [row["project_id"] for row in rows if row["project_id"]]

    def unknown_tasks(self):
        with self.lock:
            rows = self.db.execute(
                "SELECT id,payload FROM jobs WHERE kind='task' AND state='unknown' ORDER BY created"
            ).fetchall()
            tasks = []
            for row in rows:
                payload = json.loads(row["payload"])
                tasks.append({
                    "id": row["id"],
                    "project_id": payload.get("project_id"),
                    "scope": payload.get("scope", "unknown"),
                    "write_conflict": payload.get("scope") != "read-only",
                })
            return tasks

    def update_task(self, job_id, state=None, result=None, thread_id=None, turn_id=None):
        updates, values = [], []
        for key, value in (("state", state), ("result", encoded(result) if result is not None else None),
                           ("thread_id", thread_id), ("turn_id", turn_id)):
            if value is not None:
                updates.append(key + "=?")
                values.append(value)
        with self.lock, self.db:
            self.db.execute("UPDATE jobs SET " + ",".join(updates) + " WHERE id=?", (*values, job_id))


def prepare_response(store, payload):
    if not isinstance(payload, dict):
        raise Fault(400, "Expected object")
    text(payload.get("model"), "model", 200)
    if not isinstance(payload.get("stream", False), bool):
        raise Fault(400, "stream must be boolean")
    inputs = payload.get("input", [])
    if isinstance(inputs, str):
        inputs = [{"role": "user", "content": inputs}]
    if not isinstance(inputs, list) or not all(isinstance(x, dict) for x in inputs):
        raise Fault(400, "Invalid input")
    tools = payload.get("tools", [])
    if not isinstance(tools, list):
        raise Fault(400, "Invalid tools")
    names = set()
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") not in ("function", "custom"):
            raise Fault(501, "Only function and custom tools are supported")
        name = text(tool.get("name"), "tool name", 200)
        if name in names:
            raise Fault(400, "Duplicate tool name")
        names.add(name)
    if payload.get("background") or payload.get("conversation"):
        raise Fault(501, "Background and conversation resources are unsupported")
    text_options = payload.get("text", {})
    if not isinstance(text_options, dict) or not isinstance(text_options.get("format", {}), dict):
        raise Fault(400, "Invalid text format")
    if text_options.get("format", {}).get("type", "text") != "text":
        raise Fault(501, "Structured response formats are unsupported")
    prior = payload.get("previous_response_id")
    if prior:
        old = store.get(prior, "backend")
        if old["state"] != "completed":
            raise Fault(409, "Previous response is not completed")
        inputs = old["payload"]["input"] + old["result"]["output"] + inputs
    prepared = dict(payload, input=inputs, tools=tools)
    if len(encoded(prepared).encode()) > 4 * 1024 * 1024:
        raise Fault(413, "Context too large")
    return prepared


def build_output(payload, body):
    answer = body.get("answer", "")
    calls = body.get("calls", [])
    if not isinstance(answer, str) or len(answer) > 50000 or not isinstance(calls, list) or len(calls) > 16:
        raise Fault(400, "Invalid output")
    if not answer.strip() and not calls:
        raise Fault(400, "Provide answer or calls")
    choice = payload.get("tool_choice", "auto")
    if choice == "none" and calls:
        raise Fault(400, "Tool calls forbidden by request")
    if choice == "required" and not calls:
        raise Fault(400, "A tool call is required")
    if payload.get("parallel_tool_calls") is False and len(calls) > 1:
        raise Fault(400, "Parallel tool calls disabled")
    tools = {tool["name"]: tool for tool in payload["tools"]}
    output = []
    if answer.strip():
        output.append({"type": "message", "id": "msg_" + uuid.uuid4().hex, "status": "completed", "role": "assistant",
                       "content": [{"type": "output_text", "text": answer, "annotations": []}]})
    for call in calls:
        fields(call, ("name", "input"))
        name = text(call["name"], "tool name", 200)
        tool = tools.get(name)
        if not tool or isinstance(choice, dict) and choice.get("name") != call["name"]:
            raise Fault(400, "Tool not offered by this turn")
        value = text(call["input"], "tool input")
        if tool["type"] == "function":
            try:
                arguments = json.loads(value)
            except ValueError as exc:
                raise Fault(400, "Function input must be JSON") from exc
            if not isinstance(arguments, dict):
                raise Fault(400, "Function input must be a JSON object")
        item = {"type": "function_call" if tool["type"] == "function" else "custom_tool_call",
                "id": "fc_" + uuid.uuid4().hex, "call_id": "call_" + uuid.uuid4().hex,
                "name": call["name"], "status": "completed"}
        item["arguments" if tool["type"] == "function" else "input"] = value
        output.append(item)
    return output


def envelope(job, output, status="completed"):
    return {"id": job["id"], "object": "response", "created_at": int(job["created"]), "status": status,
            "model": job["payload"]["model"], "output": output, "error": None, "incomplete_details": None,
            "usage": None, "parallel_tool_calls": job["payload"].get("parallel_tool_calls", True),
            "tools": job["payload"].get("tools", []), "tool_choice": job["payload"].get("tool_choice", "auto")}


def output_events(response):
    for index, item in enumerate(response["output"]):
        initial = dict(item, status="in_progress")
        if item["type"] == "message":
            initial["content"] = []
        else:
            initial["arguments" if item["type"] == "function_call" else "input"] = ""
        yield "response.output_item.added", {"output_index": index, "item": initial}
        if item["type"] == "message":
            part = item["content"][0]
            common = {"item_id": item["id"], "output_index": index, "content_index": 0}
            yield "response.content_part.added", dict(common, part={"type": "output_text", "text": "", "annotations": []})
            for offset in range(0, len(part["text"]), 2048):
                yield "response.output_text.delta", dict(common, delta=part["text"][offset:offset + 2048])
            yield "response.output_text.done", dict(common, text=part["text"])
            yield "response.content_part.done", dict(common, part=part)
        elif item["type"] == "function_call":
            common = {"item_id": item["id"], "output_index": index}
            yield "response.function_call_arguments.delta", dict(common, delta=item["arguments"])
            yield "response.function_call_arguments.done", dict(common, arguments=item["arguments"])
        yield "response.output_item.done", {"output_index": index, "item": item}
    yield "response.completed", {"response": response}


def project_config(config, project_id, scope):
    project = config.get("projects", {}).get(project_id)
    if not project:
        raise Fault(403, "Project not allowed")
    if scope not in ("read-only", "workspace-write"):
        raise Fault(400, "Invalid scope")
    if scope == "workspace-write" and not project.get("allow_write", False):
        raise Fault(403, "Project write access disabled")
    root = Path(project["cwd"])
    if not root.is_absolute() or not root.is_dir() or root.resolve() != root:
        raise Fault(503, "Configured project root is unavailable or changed")
    return project
