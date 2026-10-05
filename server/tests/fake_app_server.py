"""Protocol fixture. Does not run Codex or execute any task commands."""
import json
import os
import sys


def emit(value):
    print(json.dumps(value), flush=True)


for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialized":
        continue
    if method == "initialize":
        assert request["params"]["clientInfo"]["name"] in ("codex_dual_bridge", "codex_x_mode")
        emit({"id": request["id"], "result": {}})
    elif method == "model/list":
        assert request["params"]["includeHidden"] is False
        emit({"id": request["id"], "result": {"data": [{
            "id": "fixture-model", "model": "fixture-model", "displayName": "Fixture Model",
            "hidden": False, "isDefault": True,
            "supportedReasoningEfforts": [{"reasoningEffort": "medium", "description": "Fixture"}],
        }], "nextCursor": None}})
    elif method in ("thread/start", "thread/resume"):
        assert request["params"]["approvalPolicy"] == "never"
        assert request["params"]["sandbox"] in ("read-only", "workspace-write")
        emit({"id": request["id"], "result": {"thread": {"id": request["params"].get("threadId", "fixture-thread")}}})
    elif method == "turn/start":
        params = request["params"]
        assert params["approvalPolicy"] == "never"
        assert params["model"] == "fixture-model"
        assert "sandboxPolicy" not in params
        emit({"id": request["id"], "result": {"turn": {"id": "fixture-turn", "status": "inProgress"}}})
        emit({"id": "fixture-approval", "method": "item/commandExecution/requestApproval",
              "params": {"threadId": params["threadId"], "turnId": "fixture-turn"}})
        decision = json.loads(sys.stdin.readline())
        assert decision["result"]["decision"] == "cancel"
        common = {"threadId": params["threadId"], "turnId": "fixture-turn"}
        answer = os.environ.get("ACCESS_TOKEN", "missing-fixture-token") if params["input"][0]["text"] == "fixture credential leak" else "Fixture answer"
        emit({"method": "item/completed", "params": dict(common, item={"type": "agentMessage", "text": answer})})
        emit({"method": "item/completed", "params": dict(common, item={"type": "commandExecution", "exitCode": 1, "status": "completed"})})
        emit({"method": "turn/completed", "params": {"threadId": params["threadId"],
              "turn": {"id": "fixture-turn", "status": "completed", "model": params["model"]}}})
