import json
import sys
from dataclasses import dataclass

from .core import Fault, encoded
from .policy import permitted_tool_names
from .serena_adapter import (
    TOOLS as SERENA_TOOLS, MAP as SERENA_MAP, PREVIEW_TOOL as SERENA_PREVIEW_TOOL,
    PREVIEW_NAME as SERENA_PREVIEW_NAME, preview_replace_in_files,
    enabled as serena_enabled, call_tool as call_serena_tool,
)
from .codex_x_app import SERVER_INFO as CODEX_X_APP_SERVER_INFO, TOOLS as CODEX_X_APP_TOOLS, call_tool as call_codex_x_app_tool
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


SERVER_INFO = {"name": "codex-x-mode", "version": "1.0.0-rc.6"}
MODERN_VERSION = "2026-07-28"
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
SUPPORTED_VERSIONS = (MODERN_VERSION, *LEGACY_VERSIONS)


def _meta(surface="codex_x"):
    server_info = CODEX_X_APP_SERVER_INFO if surface == "codex_x_app" else SERVER_INFO
    return {"io.modelcontextprotocol/serverInfo": server_info}


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
        "description": "Read Native Codex app-server model/list with exact executable models and supported reasoning efforts, filtered by configured model policy. Catalog visibility does not prove inference entitlement.",
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
        "name": "codex_x_preview_project_edit",
        "description": "Create a bounded read-only unified diff for an existing project file; requires an exact current SHA-256 digest and never writes.",
        "inputSchema": _schema(
            {
                "project_id": {"type": "string"},
                "path": {"type": "string"},
                "new_content": {"type": "string"},
                "expected_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "max_diff_bytes": {"type": "integer", "minimum": 1, "maximum": 262144, "default": 131072},
            }, ["project_id", "path", "new_content", "expected_sha256"]
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
                "model_version": {"type": "string", "description": "Exact model ID from codex_x_list_models. Web uses an exact executable Native model exposed under the configured model policy and its supported effort. Unsupported IDs or efforts fail before queue acceptance. Native Codex owns authentication and inference; completion requires exact terminal model identity without reroute."},
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
             "model_version": {"type": "string", "description": "Optional exact Native Codex model ID; omission retains the parent selected dispatch model. Backend and dispatch origin cannot change. This does not change the current ChatGPT conversation model."}},
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

# v1: preserve Core names, namespace App tools to prevent collisions.
# Keep original App MCP surface intact for existing clients.
UNIFIED_APP_PREFIX = "codex_x_app_"
UNIFIED_APP_TOOL_MAP = {
    UNIFIED_APP_PREFIX + tool["name"]: tool["name"] for tool in CODEX_X_APP_TOOLS
}
UNIFIED_TOOLS = [*TOOLS, *(
    {**tool, "name": UNIFIED_APP_PREFIX + tool["name"]}
    for tool in CODEX_X_APP_TOOLS
)]
if len({tool["name"] for tool in UNIFIED_TOOLS}) != len(UNIFIED_TOOLS):
    raise RuntimeError("Unified MCP tool registry contains duplicate names")

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


def _tool_result(value, is_error=False, surface="codex_x"):
    body = encoded(value)
    result = {"content": [{"type": "text", "text": body}], "structuredContent": value, "_meta": _meta(surface)}
    if is_error:
        result["isError"] = True
    return result


def _call_tool(name, args, config, store):
    if name not in TOOL_MAP and name not in SERENA_MAP and name != SERENA_PREVIEW_NAME:
        raise Fault(404, "Unknown tool")
    if not isinstance(args, dict):
        raise Fault(400, "Tool arguments must be an object")
    if name == SERENA_PREVIEW_NAME:
        if not isinstance(args, dict):
            raise Fault(400, "Tool arguments must be an object")
        return preview_replace_in_files(
            config, args.get("project_id"),
            {k: v for k, v in args.items() if k != "project_id"},
        )
    if name in SERENA_MAP:
        return call_serena_tool(config, args.get("project_id"), SERENA_MAP[name],
                                {k: v for k, v in args.items() if k != "project_id"}, store=store)
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
    if name == "codex_x_preview_project_edit":
        from .workspace_direct import preview_edit
        return preview_edit(config, args.get("project_id"), args.get("path"),
                            args.get("new_content"), expected_sha256=args.get("expected_sha256"),
                            max_diff_bytes=args.get("max_diff_bytes", 131072))
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


def _call_unified_tool(name, args, config, store):
    if name in TOOL_MAP or name in SERENA_MAP or name == SERENA_PREVIEW_NAME:
        return _call_tool(name, args, config, store)
    original = UNIFIED_APP_TOOL_MAP.get(name)
    if original is None:
        raise Fault(404, "Unknown tool")
    if not isinstance(args, dict):
        raise Fault(400, "Tool arguments must be an object")
    return call_codex_x_app_tool(original, args, config, store)


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


def _result(value, surface="codex_x"):
    if isinstance(value, dict):
        value = dict(value)
        value.setdefault("_meta", _meta(surface))
    return value


def handle_rpc(request, config, store, legacy_state=None, surface="codex_x"):
    if surface not in ("codex_x", "codex_x_app", "unified"):
        raise RpcError(-32602, "Unknown MCP surface")
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or "method" not in request:
        raise RpcError(-32600, "Invalid Request")
    method = request["method"]
    request_id = request.get("id")
    params = request.get("params") or {}
    if not isinstance(params, dict):
        raise RpcError(-32602, "Invalid params")

    server_info = CODEX_X_APP_SERVER_INFO if surface == "codex_x_app" else SERVER_INFO
    tools = list(UNIFIED_TOOLS if surface == "unified"
                 else CODEX_X_APP_TOOLS if surface == "codex_x_app" else TOOLS)
    if surface != "codex_x_app" and serena_enabled(config):
        from .serena_adapter import SAFE_READ, SUPPORTED_WRITE
        writable = (config.get("serena", {}).get("allow_mutations") is True
                    and config.get("mcp_policy", {}).get("mode") == "explicit")
        exposed = SAFE_READ | (SUPPORTED_WRITE if writable else frozenset())
        tools.extend(tool for tool in SERENA_TOOLS
                     if tool["name"].removeprefix("codex_x_serena_") in exposed)
        tools.append(SERENA_PREVIEW_TOOL)
    resources = [] if surface == "codex_x_app" else RESOURCES
    prompts = [] if surface == "codex_x_app" else PROMPTS

    # Discoverability is not mutation permission. Resolve policy separately
    # for every RPC invocation to prevent stale tools/list from granting calls.
    try:
        allowed = permitted_tool_names(config, tools, catalog=(
            *UNIFIED_TOOLS, *CODEX_X_APP_TOOLS, *SERENA_TOOLS, SERENA_PREVIEW_TOOL,
        ))
    except Fault as exc:
        raise RpcError(-32003, str(exc)) from exc

    if method == "initialize":
        requested = params.get("protocolVersion")
        selected = requested if requested in LEGACY_VERSIONS else LEGACY_VERSIONS[0]
        if legacy_state is not None:
            legacy_state["version"] = selected
        return request_id, {
            "protocolVersion": selected,
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "serverInfo": server_info,
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
        return request_id, _result({}, surface)

    if not legacy_state or not legacy_state.get("version"):
        _validate_modern_meta(request)

    if method == "tools/list":
        return request_id, _result({"tools": [tool for tool in tools if tool["name"] in allowed]}, surface)
    if method == "tools/call":
        name = params.get("name")
        args = params["arguments"] if "arguments" in params else {}
        if name not in {item["name"] for item in tools}:
            if name in SERENA_MAP:
                return request_id, _tool_result({"error": {"status": 403, "message": "Serena tool not enabled"}}, True, surface)
            return request_id, _tool_result({"error": {"status": 404, "message": "Unknown tool"}}, True, surface)
        if name not in allowed:
            return request_id, _tool_result({"error": {"status": 403, "message": "MCP tool not permitted"}}, True, surface)
        if not isinstance(args, dict):
            raise RpcError(-32602, "Tool arguments must be an object")
        try:
            if surface == "codex_x_app":
                value = call_codex_x_app_tool(name, args, config, store)
            elif surface == "unified":
                value = _call_unified_tool(name, args, config, store)
            else:
                value = _call_tool(name, args, config, store)
            return request_id, _tool_result(value, surface=surface)
        except Fault as exc:
            return request_id, _tool_result({"error": {"status": exc.status, "message": str(exc)}}, True, surface)
    if method == "resources/list":
        required = {"codex-x://status": "codex_x_status", "codex-x://projects": "codex_x_list_projects"}
        visible = [item for item in resources if required.get(item["uri"]) in allowed]
        return request_id, _result({"resources": visible}, surface)
    if method == "resources/read":
        if surface == "codex_x_app":
            raise RpcError(-32602, "Unknown resource")
        uri = params.get("uri")
        if uri == "codex-x://status":
            if "codex_x_status" not in allowed:
                raise RpcError(-32003, "MCP resource not permitted")
            value = status(config, store)
        elif uri == "codex-x://projects":
            if "codex_x_list_projects" not in allowed:
                raise RpcError(-32003, "MCP resource not permitted")
            value = list_projects(config)
        else:
            raise RpcError(-32602, "Unknown resource")
        return request_id, _result({"contents": [{"uri": uri, "mimeType": "application/json", "text": encoded(value)}]}, surface)
    if method == "prompts/list":
        return request_id, _result({"prompts": prompts}, surface)
    if method == "prompts/get":
        if surface == "codex_x_app":
            raise RpcError(-32602, "Unknown prompt")
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
        return request_id, _result({"description": next(p["description"] for p in prompts if p["name"] == name), "messages": [{"role": "user", "content": {"type": "text", "text": message}}]}, surface)
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


def _stamp_modern_result(method, result, surface="codex_x"):
    if not isinstance(result, dict):
        return result
    result = dict(result)
    result.setdefault("resultType", "complete")
    result.setdefault("_meta", _meta(surface))
    if method in ("tools/list", "resources/list", "resources/read", "prompts/list"):
        result.setdefault("ttlMs", 0)
        result.setdefault("cacheScope", "private")
    return result


def rpc_response(request, config, store, legacy_state=None, surface="codex_x"):
    request_id = request.get("id") if isinstance(request, dict) else None
    try:
        response_id, result = handle_rpc(request, config, store, legacy_state, surface)
        if response_id is None:
            return None
        if _request_version(request, legacy_state) == MODERN_VERSION:
            result = _stamp_modern_result(request.get("method"), result, surface)
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


def serve_stdio(config, store, surface="codex_x"):
    config = dict(config, _dispatch_origin="local")
    state = {}
    for raw in sys.stdin:
        try:
            request = json.loads(raw)
        except ValueError:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            response = rpc_response(request, config, store, state, surface)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()
