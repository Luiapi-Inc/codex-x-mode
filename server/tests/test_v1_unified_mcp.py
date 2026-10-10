"""v1 one-connection Core + App MCP contract; no Native Codex runs."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.core import Store
from bridge.http import Server
from bridge.mcp import rpc_response


def rpc(method, params=None):
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}


class UnifiedMcpContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "test.sqlite3")
        self.config = {
            "mcp_key": "m" * 40, "gpt_key": "g" * 40, "provider_key": "p" * 40,
            "projects": {}, "codex_command": ["codex"],
        }

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_arguments_validation_precedes_all_dispatchers_on_every_surface(self):
        invalid = (None, [], True, False, 0, 1, "", "value")
        cases = (
            ("codex_x", "codex_x_create_task"),
            ("codex_x_app", "create_thread"),
            ("unified", "codex_x_create_task"),
            ("unified", "codex_x_app_create_thread"),
        )
        for surface, name in cases:
            for args in invalid:
                with self.subTest(surface=surface, name=name, args=args):
                    with patch("bridge.mcp._call_tool") as core, patch("bridge.mcp.call_codex_x_app_tool") as app:
                        response = rpc_response(rpc("tools/call", {"name": name, "arguments": args}),
                                                self.config, self.store, {"version": "2025-11-25"}, surface)
                    self.assertEqual(response["error"]["code"], -32602)
                    core.assert_not_called()
                    app.assert_not_called()
            with self.subTest(surface=surface, name=name, args="missing"):
                with patch("bridge.mcp._call_tool", return_value={}) as core, \
                     patch("bridge.mcp.call_codex_x_app_tool", return_value={}) as app:
                    response = rpc_response(rpc("tools/call", {"name": name}),
                                            self.config, self.store, {"version": "2025-11-25"}, surface)
                self.assertNotIn("error", response)
                self.assertEqual(core.call_count + app.call_count, 1)
                invoked = core if core.called else app
                self.assertEqual(invoked.call_args.args[1], {})

    def test_argument_validation_http_returns_json_rpc_error_and_does_not_dispatch(self):
        server = Server(("127.0.0.1", 0), self.config, self.store, start_worker=False)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for path, name in (("/mcp", "codex_x_create_task"), ("/app/mcp", "create_thread"),
                               ("/mode/mcp", "codex_x_create_task"),
                               ("/mode/mcp", "codex_x_app_create_thread")):
                for bad in (None, [], False, 7, "unsafe"):
                    with self.subTest(path=path, bad=bad):
                        body = json.dumps(rpc("tools/call", {"name": name, "arguments": bad}))
                        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                        with patch("bridge.mcp._call_tool") as core, patch("bridge.mcp.call_codex_x_app_tool") as app:
                            conn.request("POST", path, body, {"Authorization": "Bearer " + self.config["mcp_key"],
                                                               "Content-Type": "application/json"})
                            response = conn.getresponse()
                            payload = json.loads(response.read())
                        conn.close()
                        self.assertEqual(payload["error"]["code"], -32602)
                        self.assertEqual(response.status, 200)
                        core.assert_not_called()
                        app.assert_not_called()
        finally:
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()

    def test_one_surface_has_all_21_namespaced_tools(self):
        response = rpc_response(rpc("tools/list"), self.config, self.store,
                                {"version": "2025-11-25"}, "unified")
        tools = response["result"]["tools"]
        names = [tool["name"] for tool in tools]
        self.assertEqual(len(names), 21)
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(sum(name.startswith("codex_x_app_") for name in names), 8)
        self.assertIn("codex_x_status", names)
        self.assertIn("codex_x_app_list_threads", names)
        self.assertNotIn("list_threads", names)
        self.assertEqual(len(rpc_response(rpc("tools/list"), self.config, self.store,
                                          {"version": "2025-11-25"}, "codex_x")["result"]["tools"]), 13)
        self.assertEqual(len(rpc_response(rpc("tools/list"), self.config, self.store,
                                          {"version": "2025-11-25"}, "codex_x_app")["result"]["tools"]), 8)

    def test_unified_calls_core_and_app_without_provider_fallback(self):
        core = rpc_response(rpc("tools/call", {"name": "codex_x_status"}),
                            self.config, self.store, {"version": "2025-11-25"}, "unified")
        self.assertFalse(core["result"].get("isError", False))
        self.assertEqual(core["result"]["structuredContent"]["status"], "up")
        with patch("bridge.mcp.call_codex_x_app_tool", return_value={"data": []}) as app:
            result = rpc_response(rpc("tools/call", {
                "name": "codex_x_app_list_threads", "arguments": {"limit": 4}
            }), self.config, self.store, {"version": "2025-11-25"}, "unified")
        app.assert_called_once_with("list_threads", {"limit": 4}, self.config, self.store)
        self.assertFalse(result["result"].get("isError", False))
        self.assertEqual(result["result"]["structuredContent"], {"data": []})

        with patch("bridge.mcp.call_codex_x_app_tool") as app:
            denied = rpc_response(rpc("tools/call", {"name": "list_threads"}),
                                  self.config, self.store, {"version": "2025-11-25"}, "unified")
            self.assertTrue(denied["result"]["isError"])
            self.assertEqual(denied["result"]["structuredContent"]["error"]["status"], 404)
            app.assert_not_called()

    def test_http_mode_is_unified_while_legacy_endpoints_remain_compatible(self):
        server = Server(("127.0.0.1", 0), self.config, self.store)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for path, count in (("/mode/mcp", 21), ("/mcp", 13), ("/app/mcp", 8)):
                body = json.dumps(rpc("tools/list")).encode("utf-8")
                conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                conn.request("POST", path, body, {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + self.config["mcp_key"],
                })
                resp = conn.getresponse()
                payload = json.loads(resp.read())
                conn.close()
                self.assertEqual(resp.status, 200)
                self.assertEqual(len(payload["result"]["tools"]), count)
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            conn.request("POST", "/mode/mcp", json.dumps(rpc("tools/list")),
                         {"Content-Type": "application/json"})
            resp = conn.getresponse()
            self.assertEqual(resp.status, 401)
            resp.read()
            conn.close()
        finally:
            server.shutdown()
            thread.join(timeout=4)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
