import json
from urllib.parse import urlsplit


def schema(base_url):
    url = urlsplit(base_url)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("Supply the actual HTTPS bridge URL without credentials, query or fragment")
    string = {"type": "string"}
    body_schema = lambda required, properties: {"type": "object", "required": required,
                                                "additionalProperties": False, "properties": properties}
    paths = {}

    def add(path, method, operation, summary, request=None, params=None, consequential=False, success=200):
        value = {
            "operationId": operation,
            "summary": summary,
            "x-openai-isConsequential": consequential,
            "responses": {str(success): {"description": "Current result", "content": {"application/json": {"schema": {"type": "object", "additionalProperties": True}}}}},
        }
        if request is not None:
            value["requestBody"] = {"required": True, "content": {"application/json": {"schema": request}}}
        if params:
            value["parameters"] = params
        paths.setdefault(path, {})[method] = value

    job_param = {"name": "id", "in": "path", "required": True, "schema": string}
    project_param = {"name": "project_id", "in": "path", "required": True, "schema": string}
    add("/status", "get", "getStatus", "Read bridge capability and recovery status.")
    add("/projects", "get", "listProjects", "List configured projects and whether writes are allowed.")
    add("/models", "get", "listModels", "Read the Native Codex model/list catalog. Web dispatch exposes only exact chatgpt-web/<version> IDs with their supported/default reasoning-effort metadata. Listing does not prove terminal inference or change the current ChatGPT conversation model.")
    add("/projects/{project_id}/directory", "get", "listProjectDirectory", "List a directory inside an allowlisted project without escaping its root.",
        params=[project_param,
                {"name": "path", "in": "query", "required": False, "schema": {"type": "string", "default": "."}},
                {"name": "limit", "in": "query", "required": False, "schema": {"type": "integer", "minimum": 1, "maximum": 500, "default": 200}}])
    add("/projects/{project_id}/file", "get", "readProjectFile", "Read one UTF-8 text file inside an allowlisted project.",
        params=[project_param,
                {"name": "path", "in": "query", "required": True, "schema": string},
                {"name": "max_bytes", "in": "query", "required": False, "schema": {"type": "integer", "minimum": 1, "maximum": 1048576, "default": 262144}}])
    add("/backend/claim", "post", "claimBackendTurn", "Claim one pending Codex model request. Retry the same request_key to recover the exact same turn or null result after response loss.",
        body_schema(["request_key"], {"request_key": string}))
    add("/backend/{id}/context", "get", "readBackendContext", "Read a lease-bound context chunk. Continue until next_offset is null.",
        params=[job_param,
                {"name": "lease", "in": "query", "required": True, "schema": string},
                {"name": "offset", "in": "query", "required": False, "schema": {"type": "integer", "minimum": 0}}])
    call = body_schema(["name", "input"], {"name": string, "input": {"type": "string", "description": "JSON object string for function tools; raw input for custom tools."}})
    add("/backend/{id}/complete", "post", "completeBackendTurn", "Return an answer or offered tool calls to the waiting Codex client.",
        body_schema(["lease", "request_key"], {"lease": string, "request_key": string, "answer": string,
                                                "calls": {"type": "array", "items": call}}), [job_param], True)
    add("/backend/{id}/cancel", "post", "cancelBackendTurn", "Cancel a claimed backend turn. Retry with the same request_key for idempotent recovery.",
        body_schema(["lease", "request_key"], {"lease": string, "request_key": string}), [job_param], True)
    add("/tasks", "post", "createCodexTask", "Start a user-authorized task in a configured project. Reuse request_key only for retries.",
        body_schema(["project_id", "prompt", "scope", "request_key"],
                    {"project_id": string, "prompt": string, "scope": {"type": "string", "enum": ["read-only", "workspace-write"]},
                     "request_key": string, "model_version": {"type": "string", "description": "Exact chatgpt-web/<version> ID returned by GET /models for Web-origin dispatch. Omission uses a configured exact Web default when valid or an unambiguous Native Codex account default. Unsupported model IDs or efforts fail before queue acceptance. Retries recover the original job; completed execution must report the exact terminal model without reroute or remains unknown."}}), consequential=True, success=202)
    add("/tasks/{id}", "get", "readCodexTask", "Read a task status and available evidence without sending another turn.", params=[job_param])
    add("/tasks/{id}/followups", "post", "continueCodexTask", "Send an explicitly authorized follow-up after the parent completes. Scope stays unchanged.",
        body_schema(["prompt", "request_key"], {"prompt": string, "request_key": string,
                                                   "model_version": {"type": "string", "description": "Optional exact Native Codex model ID returned by GET /models; omission retains the parent's selected model. Execution remains with the parent's backend and dispatch origin. This does not change the current ChatGPT conversation model."}}), [job_param], True, success=202)
    add("/tasks/{id}/cancel", "post", "cancelCodexTask", "Cancel a queued task or request cancellation of a running task. Unknown execution remains fail-closed.",
        body_schema(["request_key"], {"request_key": string}), [job_param], True)
    return {"openapi": "3.1.0", "info": {"title": "Codex X Mode Bridge", "version": "1.0.0-rc.4"},
            "servers": [{"url": base_url.rstrip("/")}], "paths": paths,
            "security": [{"bearerAuth": []}],
            "components": {"securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}}}


def dump(base_url):
    return json.dumps(schema(base_url), indent=2)
