import http.client
import json
import sqlite3
import threading
import time
import unittest

from bridge.core import Fault
from bridge.http import Server
from bridge.mcp import TOOL_MAP, _call_tool
from bridge.schema import schema
from bridge.service import claim_backend, read_backend_context


class LegacyStore:
    """Minimal v0.2.1-like claim behavior: null claims are not persisted."""

    def __init__(self):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.jobs = {}
        self.queue = []
        self.claim_calls = 0

    def close(self):
        self.db.close()

    def add_backend(self, job_id="resp_1"):
        job = {
            "id": job_id,
            "kind": "backend",
            "state": "queued",
            "payload": {"model": "fixture", "input": [{"role": "user", "content": "secret context"}], "tools": []},
            "result": None,
            "request_key": "provider",
            "created": time.time(),
            "expires": time.time() + 600,
            "lease": None,
            "claim_key": None,
            "thread_id": None,
            "turn_id": None,
        }
        self.jobs[job_id] = job
        self.queue.append(job_id)
        return job

    def claim(self, kind, request_key=None):
        self.claim_calls += 1
        assert kind == "backend"
        for job_id in list(self.queue):
            job = self.jobs[job_id]
            if job["state"] == "queued":
                job["state"] = "claimed"
                job["lease"] = "lease-1"
                job["claim_key"] = request_key
                self.queue.remove(job_id)
                return dict(job)
        return None

    def get(self, job_id, kind=None):
        job = self.jobs.get(job_id)
        if not job or (kind and job["kind"] != kind):
            raise Fault(404, "Unknown job")
        return dict(job)

    def unknown_projects(self):
        return []


class RecoveryHardeningTests(unittest.TestCase):
    def setUp(self):
        self.store = LegacyStore()

    def tearDown(self):
        self.store.close()

    def test_null_claim_retry_never_captures_later_turn(self):
        first = claim_backend(self.store, {"request_key": "claim-empty"})
        self.assertIsNone(first["turn"])
        self.assertEqual(self.store.claim_calls, 1)

        self.store.add_backend()
        retry = claim_backend(self.store, {"request_key": "claim-empty"})
        self.assertIsNone(retry["turn"])
        self.assertEqual(self.store.claim_calls, 1, "retry must not call the underlying claim again")

        later = claim_backend(self.store, {"request_key": "claim-new"})
        self.assertEqual(later["turn"]["id"], "resp_1")
        self.assertEqual(self.store.claim_calls, 2)

    def test_context_requires_exact_lease_in_service_mcp_and_openapi(self):
        self.store.add_backend()
        turn = claim_backend(self.store, {"request_key": "claim-one"})["turn"]

        with self.assertRaises(Fault) as caught:
            read_backend_context(self.store, turn["id"], "wrong", 0)
        self.assertEqual(caught.exception.status, 403)

        value = read_backend_context(self.store, turn["id"], turn["lease"], 0)
        self.assertEqual(value["id"], turn["id"])
        self.assertIn("secret context", value["chunk"])

        tool = TOOL_MAP["codex_x_read_backend_context"]
        self.assertIn("lease", tool["inputSchema"]["required"])
        via_mcp = _call_tool(
            "codex_x_read_backend_context",
            {"turn_id": turn["id"], "lease": turn["lease"], "offset": 0},
            {},
            self.store,
        )
        self.assertEqual(via_mcp["id"], turn["id"])

        spec = schema("https://bridge.example.invalid")
        self.assertEqual(spec["info"]["version"], "1.0.0-rc.3")
        params = spec["paths"]["/backend/{id}/context"]["get"]["parameters"]
        lease = next(p for p in params if p["name"] == "lease")
        self.assertTrue(lease["required"])

    def test_http_context_requires_lease_and_accepts_valid_lease(self):
        self.store.add_backend()
        config = {"gpt_key": "g" * 40, "mcp_key": "m" * 40, "provider_key": "p" * 40, "projects": {}}
        server = Server(("127.0.0.1", 0), config, self.store, start_worker=False)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def request(method, path, body=None):
                conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                headers = {"Authorization": "Bearer " + config["gpt_key"], "Content-Type": "application/json"}
                payload = json.dumps(body) if body is not None else None
                conn.request(method, path, payload, headers)
                response = conn.getresponse()
                data = json.loads(response.read().decode())
                status = response.status
                conn.close()
                return status, data

            status, claimed = request("POST", "/backend/claim", {"request_key": "claim-http"})
            self.assertEqual(status, 200)
            turn = claimed["turn"]
            self.assertEqual(request("GET", f"/backend/{turn['id']}/context")[0], 400)
            self.assertEqual(request("GET", f"/backend/{turn['id']}/context?lease=wrong")[0], 403)
            ok_status, ok = request("GET", f"/backend/{turn['id']}/context?lease={turn['lease']}")
            self.assertEqual(ok_status, 200)
            self.assertEqual(ok["id"], turn["id"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
