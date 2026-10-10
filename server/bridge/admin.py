"""Local-only administrative HTTP plane.

This is NOT the public Cloudflare Access integration: remote forwarded requests
fail closed. Only a separate loopback listener, a distinct admin bearer secret,
explicit preview confirmation, server-side CAS and audited live policy swaps
are supported. Projects and provider model changes stay offline-only.
"""
import hashlib
import hmac
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .config_kernel import ConfigFault, ConfigKernel
from .core import Fault, encoded
from .mcp import UNIFIED_TOOLS
from .policy import permitted_tool_names


_HOT_KEYS = frozenset(("mcp_policy", "allowed_origins"))


class AdminServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, config, store, mcp_server, *, config_path):
        if address[0] != "127.0.0.1":
            raise ValueError("Admin API is only allowed on 127.0.0.1")
        if not isinstance(config.get("admin_key"), str) or len(config["admin_key"]) < 32:
            raise ValueError("An independent admin_key with at least 32 characters is required")
        if config["admin_key"] in (config.get("mcp_key"), config.get("gpt_key"), config.get("provider_key")):
            raise ValueError("Admin key must differ from MCP and other bridge keys")
        super().__init__(address, AdminHandler)
        self.store = store
        self.mcp_server = mcp_server
        self.kernel = ConfigKernel(config_path, store=store)
        self.admin_key = config["admin_key"]
        self.apply_lock = threading.RLock()
        # Pure metadata audit. Do not store tokens, prompts or config values.
        with store.lock, store.db:
            store.db.execute("""CREATE TABLE IF NOT EXISTS admin_config_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL,
                action TEXT NOT NULL, outcome TEXT NOT NULL,
                old_revision TEXT, new_revision TEXT, detail TEXT NOT NULL
            )""")

    def audit(self, action, outcome, old_revision=None, new_revision=None, detail=""):
        with self.store.lock, self.store.db:
            self.store.db.execute(
                "INSERT INTO admin_config_audit(timestamp,action,outcome,old_revision,new_revision,detail) "
                "VALUES(?,?,?,?,?,?)",
                (time.time(), action, outcome, old_revision, new_revision, detail),
            )

    def confirmation(self, action, revision, changes, *, issued=None):
        issued = int(time.time()) if issued is None else issued
        message = encoded({"action": action, "revision": revision, "changes": changes, "issued": issued})
        digest = hmac.new(self.admin_key.encode(), message.encode(), hashlib.sha256).hexdigest()
        return f"{issued}.{digest}"

    def verify_confirmation(self, token, action, revision, changes):
        if not isinstance(token, str):
            raise Fault(403, "Invalid confirmation")
        try:
            timestamp, signature = token.split(".", 1)
            issued = int(timestamp)
            if len(signature) != 64 or issued > time.time() + 5 or time.time() - issued > 120:
                raise ValueError
        except ValueError as exc:
            raise Fault(403, "Invalid confirmation") from exc
        expected = self.confirmation(action, revision, changes, issued=issued)
        if not secrets.compare_digest(expected, token):
            raise Fault(403, "Invalid confirmation")

    def verify_live_policy(self, config):
        permitted_tool_names(config, UNIFIED_TOOLS)

    def _hot_changes(self, changes):
        if not isinstance(changes, dict) or not changes or set(changes) - _HOT_KEYS:
            raise Fault(403, "Online changes limited to MCP policy and allowed origins")

    def _history_changes(self, revision):
        # Path/symlink/permission and exact digest checks are owned by the kernel.
        data = self.kernel.read_history(revision)
        return {k: data[k] for k in _HOT_KEYS if k in data}

    def preview(self, action, changes, revision):
        self._hot_changes(changes)
        result = self.kernel.preview(changes, expected_revision=revision)
        result["confirmation"] = self.confirmation(action, revision, changes)
        result["hot_reload"] = True
        return result

    def apply(self, action, changes, revision, token):
        self._hot_changes(changes)
        self.verify_confirmation(token, action, revision, changes)
        with self.apply_lock, self.mcp_server.policy_lock:
            # Rolling back a newly introduced key would need an explicit
            # deletion operation. Keep online transitions reversible.
            if any(key not in self.mcp_server.config for key in changes):
                raise Fault(403, "New configuration fields require offline apply")
            snapshot = self.kernel.snapshot()
            if snapshot["revision"] != revision:
                raise Fault(409, "Stale configuration revision")
            old_config = dict(self.mcp_server.config)
            # Do not take this path to modify auth identity, projects, or provider
            # runtime. The legacy background worker retains its own config.
            applied_revision = None
            try:
                outcome = self.kernel.apply(changes, expected_revision=revision)
                applied_revision = outcome["revision"]
                new_raw, new_config = self.kernel._load()
                if hashlib.sha256(new_raw).hexdigest() != outcome["revision"]:
                    raise RuntimeError("Configuration changed during reload")
                updated_config = dict(old_config)
                updated_config.update({k: new_config[k] for k in _HOT_KEYS if k in new_config})
                self.verify_live_policy(updated_config)
                self.mcp_server.config = updated_config
                self.audit(action, "success", revision, outcome["revision"])
                return {"revision": outcome["revision"], "hot_reload": True}
            except Exception as exc:
                # A failed CAS before applying cannot authorize an overwrite of
                # someone else's revision. Only roll back our own exact revision.
                if applied_revision is None:
                    if isinstance(exc, ConfigFault):
                        raise
                    raise Fault(503, "Configuration apply unavailable") from exc
                try:
                    present = self.kernel.snapshot()["revision"]
                    if present != applied_revision:
                        raise RuntimeError("Configuration changed after apply")
                    previous = {k: old_config[k] for k in _HOT_KEYS if k in old_config}
                    self.kernel.apply(previous, expected_revision=applied_revision)
                    self.mcp_server.config = old_config
                    self.audit(action, "rolled_back", revision, self.kernel.snapshot()["revision"])
                except Exception:
                    self.mcp_server.config = dict(old_config, mcp_policy={"mode": "read-only"})
                    self.audit(action, "rollback_failed", revision)
                raise Fault(503, "Configuration health check failed; rollback attempted") from exc


class AdminHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def reply(self, status, value):
        data = encoded(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def _authorize(self):
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            raise Fault(403, "Remote admin is not enabled")
        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1 or hosts[0] not in (
            "127.0.0.1", "localhost", f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}",
        ):
            raise Fault(403, "Admin Host is not permitted")
        # Do not accidentally accept a tunnel/proxy request as local. A future
        # remote route must verify Cloudflare Access JWT signature/claims itself.
        if any(h.lower() in (
            "forwarded", "x-forwarded-for", "x-forwarded-host",
            "x-forwarded-proto", "cf-access-jwt-assertion", "cf-connecting-ip"
        ) for h in self.headers.keys()):
            raise Fault(403, "Remote admin ingress is disabled")
        if any(len(self.headers.get_all(name, [])) > 1
               for name in ("Origin", "Sec-Fetch-Site", "Content-Length", "Content-Type")):
            raise Fault(400, "Duplicate Admin request headers")
        origin = self.headers.get("Origin")
        if origin is not None and origin not in (
            f"http://127.0.0.1:{self.server.server_port}",
            f"http://localhost:{self.server.server_port}",
        ):
            raise Fault(403, "Admin Origin is not allowed")
        if self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
            raise Fault(403, "Cross-site admin request denied")
        given = self.headers.get_all("Authorization", [])
        if len(given) != 1 or not secrets.compare_digest(
            given[0], "Bearer " + self.server.admin_key
        ):
            raise Fault(401, "Unauthorized")

    def body(self):
        if self.headers.get("Transfer-Encoding"):
            raise Fault(400, "Chunked admin requests not supported")
        if self.headers.get("Content-Type", "").split(";", 1)[0].lower() != "application/json":
            raise Fault(415, "Expected JSON")
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise Fault(400, "Invalid content length") from exc
        if not 0 < size <= 65536:
            raise Fault(413, "Admin request body size invalid")
        self.connection.settimeout(5)
        try:
            data = json.loads(self.rfile.read(size))
        except (ValueError, OSError) as exc:
            raise Fault(400, "Invalid admin JSON") from exc
        if not isinstance(data, dict):
            raise Fault(400, "Expected JSON object")
        return data

    def do_GET(self):
        self.handle_request("GET")

    def do_POST(self):
        self.handle_request("POST")

    def handle_request(self, method):
        try:
            self._authorize()
            path = urlsplit(self.path)
            if path.query or path.fragment:
                raise Fault(400, "Admin query parameters disabled")
            if method == "GET" and path.path == "/admin/v1/config":
                self.reply(200, self.server.kernel.snapshot())
                return
            if method == "POST" and path.path in (
                "/admin/v1/config/preview", "/admin/v1/config/apply",
                "/admin/v1/config/rollback/preview", "/admin/v1/config/rollback",
            ):
                body = self.body()
                if path.path.endswith("/rollback/preview") or path.path.endswith("/rollback"):
                    if set(body) not in ({"target_revision", "expected_revision"},
                                         {"target_revision", "expected_revision", "confirmation"}):
                        raise Fault(400, "Unexpected rollback fields")
                    changes = self.server._history_changes(body["target_revision"])
                    action = "rollback"
                else:
                    if set(body) not in ({"changes", "expected_revision"},
                                         {"changes", "expected_revision", "confirmation"}):
                        raise Fault(400, "Unexpected configuration fields")
                    changes = body["changes"]
                    action = "apply"
                revision = body["expected_revision"]
                if not isinstance(revision, str) or len(revision) != 64:
                    raise Fault(400, "Invalid revision")
                if path.path.endswith("/preview"):
                    self.reply(200, self.server.preview(action, changes, revision))
                else:
                    if "confirmation" not in body:
                        raise Fault(403, "Explicit confirmation required")
                    self.reply(200, self.server.apply(action, changes, revision, body["confirmation"]))
                return
            raise Fault(404, "Unknown Admin API route")
        except (ConfigFault, Fault) as exc:
            self.reply(exc.status, {"error": {"message": str(exc)}})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            self.reply(503, {"error": {"message": "Administrative operation unavailable"}})
