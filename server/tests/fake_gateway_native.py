"""Fake Native Codex app-server for one-MCP G3 process integration; no inference."""
import json
import sys


def send(id_, value):
    print(json.dumps({"id": id_, "result": value}), flush=True)


for line in sys.stdin:
    req = json.loads(line)
    method = req.get("method")
    if method == "initialize":
        send(req["id"], {})
    elif method == "model/list":
        send(req["id"], {"data": [
            {"id": "chatgpt-web/gpt-fixture", "model": "chatgpt-web/gpt-fixture",
             "displayName": "Fixture", "isDefault": True,
             "defaultReasoningEffort": "medium",
             "supportedReasoningEfforts": [{"reasoningEffort": "medium"}]},
            {"id": "gpt-non-web", "model": "gpt-non-web",
             "displayName": "Local", "isDefault": False,
             "defaultReasoningEffort": "medium",
             "supportedReasoningEfforts": [{"reasoningEffort": "medium"}]},
        ], "nextCursor": None})
    elif method == "thread/list":
        send(req["id"], {"data": [], "nextCursor": None, "backwardsCursor": None})
    elif method == "initialized":
        continue
    else:
        # Running inference is forbidden in this integration fixture.
        print(json.dumps({"id": req.get("id"), "error": {"code": -32601, "message": "Unsupported fixture operation"}}), flush=True)
