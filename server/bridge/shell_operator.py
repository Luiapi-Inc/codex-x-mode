"""Opt-in localhost-only operator PTY MCP. Never attached to /mode/mcp."""
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from .core import Fault
from .shell_pty import ShellManager
from .ssh_remote import SSHManager


def schema(properties, required=()):
    return {"type": "object", "properties": properties,
            "required": list(required), "additionalProperties": False}


SID = {"session_id": {"type": "string"}}
DEFINITIONS = {
    "open": ({"project_id": {"type": "string"}, "request_key": {"type": "string"},
              "columns": {"type": "integer"}, "rows": {"type": "integer"}}, ("project_id", "request_key")),
    "write": ({**SID, "data": {"type": "string"}, "request_key": {"type": "string"}},
              ("session_id", "data", "request_key")),
    "read": ({**SID, "cursor": {"type": "integer"}, "max_bytes": {"type": "integer"}},
             ("session_id",)),
    "resize": ({**SID, "columns": {"type": "integer"}, "rows": {"type": "integer"}},
               ("session_id", "columns", "rows")),
    "signal": ({**SID, "signal": {"type": "string"}, "request_key": {"type": "string"}},
               ("session_id", "signal", "request_key")),
    "close": (SID, ("session_id",)),
    "status": (SID, ("session_id",)),
    "list": ({}, ()),
}
TOOLS = [
    {"name": "codex_x_shell_" + name,
     "description": "Native full operator PTY: " + name + ". Local and separately authenticated.",
     "inputSchema": schema(props, required),
     "annotations": {"readOnlyHint": name in ("read", "status", "list")}}
    for name, (props, required) in DEFINITIONS.items()
]
SSH_TOOLS = [
    {**tool,
     "name": tool["name"].replace("codex_x_shell_", "codex_x_ssh_"),
     "description": "Managed pinned-profile SSH remote PTY: " + tool["name"],
     "inputSchema": (
         schema({
             "profile_id": {"type": "string"}, "request_key": {"type": "string"},
             "columns": {"type": "integer"}, "rows": {"type": "integer"},
         }, ("profile_id", "request_key"))
         if tool["name"] == "codex_x_shell_open" else tool["inputSchema"]
     ),
    }
    for tool in TOOLS
]
TOOL_MAP = {t["name"]: t for t in (*TOOLS, *SSH_TOOLS)}


class OperatorShellServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, config, store):
        if address[0] != "127.0.0.1":
            raise ValueError("Operator Shell must bind only to 127.0.0.1")
        key = config.get("shell_key")
        if not isinstance(key, str) or len(key) < 32:
            raise ValueError("Independent operator shell credential required")
        if key in (config.get("mcp_key"), config.get("admin_key"),
                   config.get("gpt_key"), config.get("provider_key")):
            raise ValueError("Operator shell credential must be distinct")
        super().__init__(address, OperatorShellHandler)
        self.shell_key = key
        self.manager = ShellManager(config, store)
        self.ssh_manager = SSHManager(config, store)
        self.operator_tools = list(TOOLS)
        if config.get("ssh", {}).get("enabled") is True:
            self.operator_tools.extend(SSH_TOOLS)
        self.operator_tool_map = {tool["name"]: tool for tool in self.operator_tools}

    def server_close(self):
        self.ssh_manager.shutdown()
        self.manager.shutdown()
        super().server_close()


class OperatorShellHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def reply(self, code, value):
        raw = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
        self.send_response(code)
        for key, val in (
            ("Content-Type", "application/json"), ("Content-Length", str(len(raw))),
            ("Cache-Control", "no-store"), ("Connection", "close"),
            ("X-Content-Type-Options", "nosniff"),
            ("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"),
        ):
            self.send_header(key, val)
        self.end_headers()
        self.wfile.write(raw)
        self.close_connection = True

    def authorize(self):
        if self.client_address[0] != "127.0.0.1":
            raise Fault(403, "Operator API local only")
        host = self.headers.get_all("Host", [])
        if len(host) != 1 or host[0] not in (
            "localhost", "127.0.0.1",
            f"localhost:{self.server.server_port}",
            f"127.0.0.1:{self.server.server_port}",
        ):
            raise Fault(403, "Invalid Host")
        if any(k.lower() in (
            "x-forwarded-host", "x-forwarded-for", "x-forwarded-proto",
            "forwarded", "cf-access-jwt-assertion", "cf-connecting-ip",
        ) for k in self.headers):
            raise Fault(403, "Operator proxy ingress disabled")
        origins = self.headers.get_all("Origin", [])
        if len(origins) > 1 or (origins and origins[0] not in (
            f"http://localhost:{self.server.server_port}",
            f"http://127.0.0.1:{self.server.server_port}",
        )):
            raise Fault(403, "Invalid Origin")
        if self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
            raise Fault(403, "Cross-site operator request denied")
        credentials = self.headers.get_all("Authorization", [])
        if len(credentials) != 1 or not secrets.compare_digest(
            credentials[0], "Bearer " + self.server.shell_key
        ):
            raise Fault(401, "Operator bearer required")

    def do_POST(self):
        try:
            target = urlsplit(self.path)
            if target.path != "/operator/mcp" or target.query:
                raise Fault(404, "Unknown operator endpoint")
            self.authorize()
            if self.headers.get("Transfer-Encoding"):
                raise Fault(400, "Chunked payload unsupported")
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                raise Fault(415, "Expected JSON")
            length = self.headers.get("Content-Length", "")
            if not length.isdigit() or not 0 < int(length) <= 65536:
                raise Fault(413, "Invalid operator request length")
            request = json.loads(self.rfile.read(int(length)))
            if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
                raise Fault(400, "Invalid JSON RPC")
            params = request.get("params") or {}
            if not isinstance(params, dict):
                raise Fault(400, "Invalid parameters")
            method = request.get("method")
            if method == "initialize":
                result = {"protocolVersion": "2025-11-25",
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": "codex-x-operator-shell", "version": "1.0.0"}}
            elif method == "tools/list":
                result = {"tools": self.server.operator_tools}
            elif method == "ping":
                result = {}
            elif method == "tools/call":
                result = self.tool_call(params)
            else:
                raise Fault(404, "Unsupported operator method")
            self.reply(200, {"jsonrpc": "2.0", "id": request.get("id"), "result": result})
        except (ValueError, UnicodeError):
            self.reply(400, {"error": {"message": "Invalid operator JSON"}})
        except Fault as exc:
            self.reply(exc.status, {"error": {"message": str(exc)}})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            self.reply(503, {"error": {"message": "Operator call unavailable; do not replay"}})

    def tool_call(self, params):
        name = params.get("name")
        if name not in self.server.operator_tool_map:
            return self.tool_error(404, "Unknown operator tool")
        args = params.get("arguments", {})
        if not isinstance(args, dict):
            return self.tool_error(400, "Expected tool arguments object")
        schema_ = self.server.operator_tool_map[name]["inputSchema"]
        if set(args) - set(schema_["properties"]) or any(
            field not in args for field in schema_["required"]
        ):
            return self.tool_error(400, "Unexpected or missing tool argument")
        try:
            value = self.invoke(name.removeprefix("codex_x_"), args)
            result = {"structuredContent": value}
        except Fault as exc:
            result = self.tool_error(exc.status, str(exc))
        result["content"] = [{"type": "text",
                              "text": json.dumps(result["structuredContent"])}]
        return result

    @staticmethod
    def tool_error(code, message):
        return {"isError": True, "structuredContent": {
            "error": {"status": code, "message": message}}}

    def invoke(self, name, args):
        if name.startswith("ssh_"):
            m = self.server.ssh_manager
            name = name.removeprefix("ssh_")
            remote = True
        elif name.startswith("shell_"):
            m = self.server.manager
            name = name.removeprefix("shell_")
            remote = False
        else:
            raise Fault(404, "Unknown operator transport")
        if name == "open":
            if remote:
                return m.open(args["profile_id"], request_key=args["request_key"],
                              columns=args.get("columns", 80), rows=args.get("rows", 24))
            return m.open(args["project_id"], request_key=args["request_key"],
                          columns=args.get("columns", 80), rows=args.get("rows", 24))
        if name == "write":
            return m.write(args["session_id"], args["data"], request_key=args["request_key"])
        if name == "read":
            return m.read(args["session_id"], cursor=args.get("cursor", 0),
                          max_bytes=args.get("max_bytes", 32768))
        if name == "resize":
            return m.resize(args["session_id"], columns=args["columns"], rows=args["rows"])
        if name == "signal":
            return m.signal(args["session_id"], args["signal"], request_key=args["request_key"])
        if name == "close":
            return m.close(args["session_id"])
        if name == "status":
            return m.status(args["session_id"])
        if name == "list":
            return m.list()
        raise Fault(404, "Unknown operator operation")
