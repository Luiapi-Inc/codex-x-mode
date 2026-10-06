import json
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from .codex import worker
from .core import Fault, TERMINAL, encoded, envelope, output_events, prepare_response
from .mcp import LEGACY_VERSIONS, MODERN_VERSION, RpcError, rpc_response, validate_http_version
from .service import (
    cancel_backend,
    cancel_task,
    claim_backend,
    complete_backend,
    continue_task,
    create_task,
    list_project_directory,
    list_models,
    list_projects,
    read_backend_context,
    read_project_file,
    read_task,
    status,
)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, config, store, start_worker=True):
        super().__init__(address, Handler)
        self.config, self.store = dict(config, _dispatch_origin="web"), store
        self.stop_worker = threading.Event()
        self.worker_thread = None
        if start_worker:
            self.worker_thread = threading.Thread(target=worker, args=(self.config, store, self.stop_worker), daemon=True)
            self.worker_thread.start()

    def server_close(self):
        self.stop_worker.set()
        if self.worker_thread:
            self.worker_thread.join(timeout=15)
            if self.worker_thread.is_alive():
                raise RuntimeError("Worker did not stop; preserve database until recovery")
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass  # Do not log prompts, bearer tokens or source excerpts.

    def auth(self, role):
        expected = self.server.config.get(role + "_key")
        if not isinstance(expected, str) or not expected:
            raise Fault(503, f"{role} authentication is not configured")
        actual = self.headers.get("Authorization", "")
        if not secrets.compare_digest(actual, "Bearer " + expected):
            raise Fault(401, "Unauthorized")

    def body(self, limit=300000):
        if self.headers.get("Transfer-Encoding"):
            raise Fault(400, "Chunked request bodies unsupported")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise Fault(400, "Invalid content length") from exc
        if length <= 0 or length > limit:
            raise Fault(413, "Request body size invalid")
        self.connection.settimeout(10)
        try:
            value = json.loads(self.rfile.read(length))
        except (ValueError, OSError) as exc:
            raise Fault(400, "Invalid JSON body") from exc
        if not isinstance(value, dict):
            raise Fault(400, "Expected JSON object")
        return value

    def reply(self, status_code, value, content_type="application/json; charset=utf-8"):
        body = encoded(value).encode()
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_GET(self):
        self.handle_route("GET")

    def do_POST(self):
        self.handle_route("POST")

    def handle_route(self, method):
        try:
            self.route(method)
        except Fault as exc:
            self.reply(exc.status, {"error": {"message": str(exc)}})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            self.reply(500, {"error": {"message": "Bridge error; no automatic replay"}})

    def route(self, method):
        url = urlsplit(self.path)
        path, store = url.path, self.server.store
        if method == "GET" and path == "/healthz":
            self.reply(200, {"status": "up", "live_codex_verified": False})
            return

        if method == "GET" and (path in ("/mcp", "/mode/mcp", "/codex-x-app/mcp", "/app/mcp") or path.startswith("/.well-known/")):
            raise Fault(404, "OAuth discovery metadata is not advertised")

        if method == "POST" and path in ("/mcp", "/mode/mcp", "/codex-x-app/mcp", "/app/mcp"):
            self.auth("mcp")
            request = self.body(1024 * 1024)
            try:
                origin = self.headers.get("Origin")
                if origin and origin not in self.server.config.get("allowed_origins", []):
                    raise Fault(403, "Origin is not allowed")
                params = request.get("params", {})
                meta = params.get("_meta", {}) if isinstance(params, dict) else {}
                body_version = meta.get("io.modelcontextprotocol/protocolVersion") if isinstance(meta, dict) else None
                header_version = self.headers.get("MCP-Protocol-Version")
                if body_version == MODERN_VERSION or header_version == MODERN_VERSION:
                    validate_http_version(request, header_version, self.headers.get("Mcp-Method"), self.headers.get("Mcp-Name"))
                    state = None
                else:
                    # Stateless compatibility: clients carry their negotiated revision.
                    # No session IDs or shared per-client state are issued.
                    version = header_version or "2025-03-26"
                    if version not in LEGACY_VERSIONS or body_version not in (None, version):
                        raise RpcError(-32022, "Unsupported or mismatched protocol version")
                    state = {"version": version}
                surface = "codex_x_app" if path in ("/codex-x-app/mcp", "/app/mcp") else "codex_x"
                response = rpc_response(request, self.server.config, store, state, surface)
            except RpcError as exc:
                error = {"code": exc.code, "message": str(exc)}
                if exc.data is not None:
                    error["data"] = exc.data
                self.reply(400, {"jsonrpc": "2.0", "id": request.get("id"), "error": error})
                return
            if response is None:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
            else:
                self.reply(200, response)
            return

        if path.startswith("/v1/"):
            self.auth("provider")
            if method == "POST" and path == "/v1/responses":
                request = prepare_response(store, self.body(4 * 1024 * 1024))
                key = self.headers.get("Idempotency-Key") or uuid.uuid4().hex
                job = store.create("backend", request, key, self.server.config.get("backend_timeout_seconds", 600))
                self.respond_provider(job, request.get("stream", False))
                return
            if method == "GET" and path.startswith("/v1/responses/"):
                job = store.get(path.rsplit("/", 1)[1], "backend")
                self.reply(200, job["result"] or envelope(job, [], "in_progress" if job["state"] not in TERMINAL else "failed"))
                return
            raise Fault(404, "Unknown provider route")

        self.auth("gpt")
        if path == "/status" and method == "GET":
            self.reply(200, status(self.server.config, store))
            return
        if path == "/projects" and method == "GET":
            self.reply(200, list_projects(self.server.config))
            return
        if path == "/models" and method == "GET":
            self.reply(200, list_models(self.server.config))
            return
        if path.startswith("/projects/") and method == "GET":
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[2] in ("directory", "file"):
                project_id = unquote(parts[1])
                query = parse_qs(url.query, keep_blank_values=True)
                rel = query.get("path", ["."])[0]
                if parts[2] == "directory":
                    try:
                        limit = int(query.get("limit", ["200"])[0])
                    except ValueError as exc:
                        raise Fault(400, "Invalid limit") from exc
                    self.reply(200, list_project_directory(self.server.config, project_id, rel, limit))
                    return
                try:
                    max_bytes = int(query.get("max_bytes", ["262144"])[0])
                except ValueError as exc:
                    raise Fault(400, "Invalid max_bytes") from exc
                self.reply(200, read_project_file(self.server.config, project_id, rel, max_bytes))
                return

        if path == "/backend/claim" and method == "POST":
            self.reply(200, claim_backend(store, self.body()))
            return
        if path.startswith("/backend/"):
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[2] == "context" and method == "GET":
                query = parse_qs(url.query, keep_blank_values=True)
                lease = query.get("lease", [None])[0]
                try:
                    offset = int(query.get("offset", ["0"])[0])
                except ValueError as exc:
                    raise Fault(400, "Invalid offset") from exc
                self.reply(200, read_backend_context(store, parts[1], lease, offset))
                return
            if len(parts) == 3 and parts[2] == "complete" and method == "POST":
                self.reply(200, complete_backend(store, parts[1], self.body()))
                return
            if len(parts) == 3 and parts[2] == "cancel" and method == "POST":
                self.reply(200, cancel_backend(store, parts[1], self.body()))
                return

        if method == "POST" and path == "/tasks":
            self.reply(202, create_task(self.server.config, store, self.body()))
            return
        if path.startswith("/tasks/"):
            parts = path.strip("/").split("/")
            if method == "GET" and len(parts) == 2:
                self.reply(200, read_task(store, parts[1]))
                return
            if method == "POST" and len(parts) == 3 and parts[2] == "followups":
                self.reply(202, continue_task(self.server.config, store, parts[1], self.body()))
                return
            if method == "POST" and len(parts) == 3 and parts[2] == "cancel":
                self.reply(200, cancel_task(store, parts[1], self.body()))
                return
        raise Fault(404, "Unknown route")

    def respond_provider(self, job, stream):
        sequence = 0
        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.close_connection = True

        def event(name, data):
            nonlocal sequence
            value = dict(data, type=name, sequence_number=sequence)
            sequence += 1
            self.wfile.write(("event: " + name + "\ndata: " + encoded(value) + "\n\n").encode())
            self.wfile.flush()

        try:
            if stream:
                event("response.created", {"response": envelope(job, [], "in_progress")})
                event("response.in_progress", {"response": envelope(job, [], "in_progress")})
            heartbeat = time.monotonic()
            while job["state"] not in TERMINAL:
                if stream and time.monotonic() - heartbeat >= 5:
                    self.wfile.write(b": waiting-for-custom-gpt\n\n")
                    self.wfile.flush()
                    heartbeat = time.monotonic()
                time.sleep(0.1)
                job = self.server.store.get(job["id"], "backend")
            if job["state"] == "completed":
                if stream:
                    for name, data in output_events(job["result"]):
                        event(name, data)
                else:
                    self.reply(200, job["result"])
            else:
                result = envelope(job, [], "failed")
                result["error"] = {"code": "backend_unavailable", "message": "Backend turn expired or was cancelled"}
                if stream:
                    event("response.failed", {"response": result})
                else:
                    self.reply(504, result)
        except (BrokenPipeError, ConnectionResetError):
            with self.server.store.lock, self.server.store.db:
                self.server.store.db.execute("UPDATE jobs SET state='expired' WHERE id=? AND state IN ('queued','claimed')", (job["id"],))
            raise
