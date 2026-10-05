"""Slow app-server fixture for cancellation tests. It never executes commands."""
import json
import sys
import time


def emit(value):
    print(json.dumps(value), flush=True)


for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialized":
        continue
    if method == "initialize":
        emit({"id": request["id"], "result": {}})
    elif method == "model/list":
        emit({"id": request["id"], "result": {"data": [{
            "id": "fixture-model", "model": "fixture-model", "isDefault": True,
        }], "nextCursor": None}})
    elif method in ("thread/start", "thread/resume"):
        emit({"id": request["id"], "result": {"thread": {"id": request["params"].get("threadId", "slow-thread")}}})
    elif method == "turn/start":
        params = request["params"]
        emit({"id": request["id"], "result": {"turn": {"id": "slow-turn", "status": "inProgress"}}})
        # Keep stdout quiet so the bridge's receive loop polls cancellation state.
        time.sleep(30)
