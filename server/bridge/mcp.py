import json
import sys
from dataclasses import dataclass

from .core import Fault, encoded
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


SERVER_INFO = {"name": "codex-x-mode", "version": "0.2.14"}
MODERN_VERSION = "2026-07-28"
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
SUPPORTED_VERSIONS = (MODERN_VERSION, *LEGACY_VERSIONS)


def _meta():
    return {"io.modelcontextprotocol/serverInfo": SERVER_INFO}


def _schema(properties=None, required=None, additional=False):
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": additional,
    }


TOOLS = [
    {
        "name": "codex_x_status",
        "description": "Read Codex X Mode service status, readiness, and unknown executions.",
        "inputSchema": _schema(),
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "codex_x_list_projects",
        "description": "List configured project IDs and whether workspace-write dispatch is allowed.",
        "inputSchema": _schema(),
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "codex_x_list_models",
        "description": "Read the active backend model catalog: HTTP uses token-scoped ChatGPT-plan listed models; local stdio uses Codex app-server model/list. Select an exact model_version; listing does not prove inference entitlement or change this ChatGPT conversation model.",
        "inputSchema": _schema(),
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "codex_x_list_project_directory",
        "description": "List one directory inside an allowlisted project without following paths outside the project root.",
        "inputSchema": _schema(
            {
                "project_id": {"type": "string"},
                "path": {"type": "string", "default": "."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 200},
            },
            ["project_id"],
        ),
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "codex_x_read_project_file",
        "description": "Read one UTF-8 text file inside an allowlisted project, with project-root and size enforcement.",
        "inputSchema": _schema(
            {
                "project_id": {"type": "string"},
                "path": {"type": "string"},
                "max_bytes": {"type": "integer", "minimum": 1, "maximum": 1048576, "default": 262144},
            },
            ["project_id", "path"],
        ),
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "codex_x_create_task",
        "description": "Create one explicitly authorized Codex task in an allowlisted project. request_key deduplicates retries.",
        "inputSchema": _schema(
            {
                "project_id": {"type": "string"},
                "prompt": {"type": "string"},
                "scope": {"type": "string", "enum": ["read-only", "workspace-write"]},
                "request_key": {"type": "string"},
                "model_version": {"type": "string", "description": "Exact active-backend model ID from codex_x_list_models. Web omission requires a configured exact default and valid SIWC authorization; unsupported IDs fail before queue acceptance. Local stdio retains the app-server default. The current conversation model is unchanged; completed execution needs exact terminal model identity without reroute."},
            },
            ["project_id", "prompt", "scope", "request_key"],
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "codex_x_read_task",
        "description": "Read a task state and captured evidence without sending another turn.",
        "inputSchema": _schema({"task_id": {"type": "string"}}, ["task_id"]),
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "codex_x_continue_task",
        "description": "Send an explicitly authorized follow-up to a completed task; project and scope cannot broaden.",
        "inputSchema": _schema(
            {"task_id": {"type": "string"}, "prompt": {"type": "string"}, "request_key": {"type": "string"},
             "model_version": {"type": "string", "description": "Optional exact active-backend model ID; omission retains the parent selected dispatch model. Backend and account registration cannot change. This does not change the current ChatGPT conversation model."}},
            ["task_id", "prompt", "request_key"],
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "codex_x_cancel_task",
        "description": "Cancel a queued task or request cancellation of a running task. Unknown executions must be recovered manually.",
        "inputSchema": _schema({"task_id": {"type": "string"}, "request_key": {"type": "string"}}, ["task_id", "request_key"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": True},
    },
    {
        "name": "codex_x_claim_backend_turn",
        "description": "Claim one pending backend turn. Retry with the same request_key to recover the exact same turn or null result after response loss.",
        "inputSchema": _schema({"request_key": {"type": "string"}}, ["request_key"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "codex_x_read_backend_context",
        "description": "Read one backend context chunk using the exact claim lease; continue with exact next_offset until null.",
        "inputSchema": _schema(
            {
                "turn_id": {"type": "string"},
                "lease": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            ["turn_id", "lease"],
        ),
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "codex_x_complete_backend_turn",
        "description": "Complete a claimed backend model response with answer text and/or offered tool calls; request_key is idempotent.",
        "inputSchema": _schema(
            {
                "turn_id": {"type": "string"},
                "lease": {"type": "string"},
                "request_key": {"type": "string"},
                "answer": {"type": "string"},
                "calls": {
                    "type": "array",
                    "items": _schema({"name": {"type": "string"}, "input": {"type": "string"}}, ["name", "input"]),
                },
            },
            ["turn_id", "lease", "request_key"],
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "codex_x_cancel_backend_turn",
        "description": "Cancel a claimed backend turn using its lease. request_key makes cancellation retry-safe.",
        "inputSchema": _schema(
            {"turn_id": {"type": "string"}, "lease": {"type": "string"}, "request_key": {"type": "string"}},
            ["turn_id", "lease", "request_key"],
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": True},
    },
]

TOOL_MAP = {tool["name"]: tool for tool in TOOLS}

RESOURCES = [
    {"uri": "codex-x://status", "name": "Codex X Mode status", "description": "Current bridge capabilities and blocked projects", "mimeType": "application/json"},
    {"uri": "codex-x://projects", "name": "Codex X Mode projects", "description": "Configured project IDs and write permissions", "mimeType": "application/json"},
]

PROMPTS = [
    {
        "name": "codex-x-backend",
        "description": "Handle one pending Codex backend turn without dispatch recursion.",
        "arguments": [{"name": "request_key", "description": "Stable claim retry key", "required": True}],
    },
    {
        "name": "codex-x-dispatch-read-only",
        "description": "Dispatch a read-only coding/review task with explicit project and evidence requirements.",
        "arguments": [
            {"name": "project_id", "description": "Configured project ID", "required": True},
            {"name": "goal", "description": "Exact authorized task goal and constraints", "required": True},
        ],
    },
]


def _tool_result(value, is_error=False):
    body = encoded(value)
    result = {"content": [{"type": "text", "text": body}], "structuredContent": value, "_meta": _meta()}
    if is_error:
        result["isError"] = True
    return result


def _call_tool(name, args, config, store):
    if name not in TOOL_MAP:
        raise Fault(404, "Unknown tool")
    if not isinstance(args, dict):
        raise Fault(400, "Tool arguments must be an object")
    if name == "codex_x_status":
        return status(config, store)
    if name == "codex_x_list_projects":
        return list_projects(config)
    if name == "codex_x_list_models":
        return list_models(config)
    if name == "codex_x_list_project_directory":
        return list_project_directory(config, args.get("project_id"), args.get("path", "."), args.get("limit", 200))
    if name == "codex_x_read_project_file":
        return read_project_file(config, args.get("project_id"), args.get("path"), args.get("max_bytes", 262144))
    if name == "codex_x_create_task":
        return create_task(config, store, args)
    if name == "codex_x_read_task":
        return read_task(store, args.get("task_id"))
    if name == "codex_x_continue_task":
        body = {key: args[key] for key in ("prompt", "request_key", "model_version") if key in args}
        return continue_task(config, store, args.get("task_id"), body)
    if name == "codex_x_cancel_task":
        return cancel_task(store, args.get("task_id"), {"request_key": args.get("request_key")})
    if name == "codex_x_claim_backend_turn":
        return claim_backend(store, {"request_key": args.get("request_key")})
    if name == "codex_x_read_backend_context":
        return read_backend_context(store, args.get("turn_id"), args.get("lease"), args.get("offset", 0))
    if name == "codex_x_complete_backend_turn":
        body = {key: args[key] for key in ("lease", "request_key") if key in args}
        if "answer" in args:
            body["answer"] = args["answer"]
        if "calls" in args:
            body["calls"] = args["calls"]
        return complete_backend(store, args.get("turn_id"), body)
    if name == "codex_x_cancel_backend_turn":
        return cancel_backend(store, args.get("turn_id"), {"lease": args.get("lease"), "request_key": args.get("request_key")})
    raise Fault(404, "Unknown tool")


def _validate_modern_meta(request):
    params = request.get("params") or {}
    if not isinstance(params, dict):
        raise RpcError(-32602, "Invalid params")
    meta = params.get("_meta") or {}
    if not isinstance(meta, dict):
        raise RpcError(-32602, "Invalid _meta")
    version = meta.get("io.modelcontextprotocol/protocolVersion")
    if version is None:
        return None
    if version not in SUPPORTED_VERSIONS:
        raise RpcError(-32022, "Unsupported protocol version", {"supported": list(SUPPORTED_VERSIONS), "requested": version})
    if version == MODERN_VERSION and not isinstance(meta.get("io.modelcontextprotocol/clientCapabilities"), dict):
        raise RpcError(-32602, "Missing client capabilities")
    return version


class RpcError(Exception):
    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code = code
        self.data = data


def _result(value):
    if isinstance(value, dict):
        value = dict(value)
        value.setdefault("_meta", _meta())
    return value


def handle_rpc(request, config, store, legacy_state=None):
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or "method" not in request:
        raise RpcError(-32600, "Invalid Request")
    method = request["method"]
    request_id = request.get("id")
    params = request.get("params") or {}
    if not isinstance(params, dict):
        raise RpcError(-32602, "Invalid params")

    if method == "initialize":
        requested = params.get("protocolVersion")
        selected = requested if requested in LEGACY_VERSIONS else LEGACY_VERSIONS[0]
        if legacy_state is not None:
            legacy_state["version"] = selected
        return request_id, {
            "protocolVersion": selected,
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "serverInfo": SERVER_INFO,
        }
    if method == "notifications/initialized":
        return None, None
    if method == "server/discover":
        _validate_modern_meta(request)
        return request_id, {
            "supportedVersions": list(SUPPORTED_VERSIONS),
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
        }
    if method == "ping":
        _validate_modern_meta(request)
        return request_id, _result({})

    if not legacy_state or not legacy_state.get("version"):
        _validate_modern_meta(request)

    if method == "tools/list":
        return request_id, _result({"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            value = _call_tool(name, args, config, store)
            return request_id, _tool_result(value)
        except Fault as exc:
            return request_id, _tool_result({"error": {"status": exc.status, "message": str(exc)}}, True)
    if method == "resources/list":
        return request_id, _result({"resources": RESOURCES})
    if method == "resources/read":
        uri = params.get("uri")
        if uri == "codex-x://status":
            value = status(config, store)
        elif uri == "codex-x://projects":
            value = list_projects(config)
        else:
            raise RpcError(-32602, "Unknown resource")
        return request_id, _result({"contents": [{"uri": uri, "mimeType": "application/json", "text": encoded(value)}]})
    if method == "prompts/list":
        return request_id, _result({"prompts": PROMPTS})
    if method == "prompts/get":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "codex-x-backend":
            key = args.get("request_key", "<stable-request-key>")
            message = f"Use Codex X Mode Backend. Claim with request_key {key}, read every context chunk using the returned lease, then complete or cancel the same turn without dispatching it recursively."
        elif name == "codex-x-dispatch-read-only":
            project = args.get("project_id", "<project-id>")
            goal = args.get("goal", "<goal and constraints>")
            message = f"Use Codex X Mode Dispatch for project {project} with read-only scope. Task: {goal}. Preserve task identity and report evidence without sending unrequested follow-ups."
        else:
            raise RpcError(-32602, "Unknown prompt")
        return request_id, _result({"description": next(p["description"] for p in PROMPTS if p["name"] == name), "messages": [{"role": "user", "content": {"type": "text", "text": message}}]})
    raise RpcError(-32601, "Method not found")


def _request_version(request, legacy_state=None):
    if not isinstance(request, dict):
        return None
    params = request.get("params") or {}
    meta = params.get("_meta") if isinstance(params, dict) else None
    version = meta.get("io.modelcontextprotocol/protocolVersion") if isinstance(meta, dict) else None
    if version:
        return version
    if legacy_state:
        return legacy_state.get("version")
    return None


def _stamp_modern_result(method, result):
    if not isinstance(result, dict):
        return result
    result = dict(result)
    result.setdefault("resultType", "complete")
    result.setdefault("_meta", _meta())
    if method in ("tools/list", "resources/list", "resources/read", "prompts/list"):
        result.setdefault("ttlMs", 0)
        result.setdefault("cacheScope", "private")
    return result


def rpc_response(request, config, store, legacy_state=None):
    request_id = request.get("id") if isinstance(request, dict) else None
    try:
        response_id, result = handle_rpc(request, config, store, legacy_state)
        if response_id is None:
            return None
        if _request_version(request, legacy_state) == MODERN_VERSION:
            result = _stamp_modern_result(request.get("method"), result)
        return {"jsonrpc": "2.0", "id": response_id, "result": result}
    except RpcError as exc:
        error = {"code": exc.code, "message": str(exc)}
        if exc.data is not None:
            error["data"] = exc.data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}
    except Exception:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": "Internal error"}}


def validate_http_version(request, header_version, method_header=None, name_header=None):
    if not isinstance(request, dict):
        raise RpcError(-32600, "Invalid Request")
    params = request.get("params") or {}
    meta = params.get("_meta") if isinstance(params, dict) else None
    body_version = meta.get("io.modelcontextprotocol/protocolVersion") if isinstance(meta, dict) else None
    if body_version != MODERN_VERSION:
        raise RpcError(-32022, "Unsupported protocol version", {"supported": [MODERN_VERSION], "requested": body_version})
    if not isinstance(meta.get("io.modelcontextprotocol/clientCapabilities"), dict):
        raise RpcError(-32602, "Missing client capabilities")
    if header_version != body_version:
        raise RpcError(-32020, "MCP-Protocol-Version header mismatch", {"header": header_version, "body": body_version})
    method = request.get("method")
    if method_header != method:
        raise RpcError(-32020, "Mcp-Method header mismatch", {"header": method_header, "body": method})
    expected_name = None
    if isinstance(params, dict):
        if method in ("tools/call", "prompts/get"):
            expected_name = params.get("name")
        elif method == "resources/read":
            expected_name = params.get("uri")
    if expected_name is not None and name_header != expected_name:
        raise RpcError(-32020, "Mcp-Name header mismatch", {"header": name_header, "body": expected_name})
    if expected_name is None and name_header not in (None, ""):
        raise RpcError(-32020, "Unexpected Mcp-Name header")


def serve_stdio(config, store):
    config = dict(config, _dispatch_origin="local")
    state = {}
    for raw in sys.stdin:
        try:
            request = json.loads(raw)
        except ValueError:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            response = rpc_response(request, config, store, state)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()
