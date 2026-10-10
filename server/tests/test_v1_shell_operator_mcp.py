"""G5 full shell is private operator MCP only, never the public gateway."""
import http.client
import json
import secrets
import tempfile
import threading
import time
import unittest
from pathlib import Path

from bridge.core import Store
from bridge.http import Server
from bridge.shell_operator import OperatorShellServer


class OperatorShellMCPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name).resolve()
        self.project = root / "project"
        self.project.mkdir()
        self.db = Store(root / "private.sqlite3")
        self.config = {
            "config_schema_version": 1,
            "projects": {"demo": {"cwd": str(self.project), "allow_write": True}},
            "shell": {"enabled": True, "executable": "/bin/sh"},
            "shell_key": secrets.token_urlsafe(36),
            "mcp_key": secrets.token_urlsafe(36),
            "admin_key": secrets.token_urlsafe(36),
            "gpt_key": secrets.token_urlsafe(36),
            "provider_key": secrets.token_urlsafe(36),
            "mcp_policy": {"mode": "read-only"},
        }
        self.mcp = Server(("127.0.0.1", 0), self.config, self.db, start_worker=False)
        self.operator = OperatorShellServer(("127.0.0.1", 0), self.config, self.db)
        self.thread = threading.Thread(target=self.operator.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.operator.shutdown()
        self.thread.join(timeout=4)
        self.operator.server_close()
        self.mcp.server_close()
        self.db.close()
        self.temp.cleanup()

    def rpc(self, method, params=None, role="shell", headers=None,
            target="/operator/mcp"):
        cred = {
            "shell": self.config["shell_key"], "mcp": self.config["mcp_key"],
            "admin": self.config["admin_key"],
        }
        http_headers = {"Content-Type": "application/json"}
        if role is not None:
            http_headers["Authorization"] = "Bearer " + cred[role]
        http_headers.update(headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.operator.server_port, timeout=8)
        try:
            conn.request("POST", target, json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": method,
                "params": params or {},
            }), http_headers)
            res = conn.getresponse()
            return res.status, json.loads(res.read())
        finally:
            conn.close()

    def test_anonymous_mcp_admin_tokens_and_proxy_headers_denied(self):
        for role in (None, "mcp", "admin"):
            self.assertEqual(self.rpc("tools/list", role=role)[0], 401)
        for extra in (
            {"Origin": "https://evil.example"},
            {"Host": "untrusted.example"},
            {"X-Forwarded-For": "1.2.3.4"},
            {"CF-Access-Jwt-Assertion": "fake"},
            {"Sec-Fetch-Site": "cross-site"},
        ):
            self.assertEqual(self.rpc("tools/list", headers=extra)[0], 403)

    def test_shell_tool_names_not_on_public_mcp_even_if_shell_enabled(self):
        from bridge.mcp import rpc_response
        listed = rpc_response(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            self.mcp.config, self.db, {"version": "2025-11-25"}, "unified",
        )
        self.assertFalse(any(t["name"].startswith("codex_x_shell_")
                             for t in listed["result"]["tools"]))
        directly_called = rpc_response(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                "name": "codex_x_shell_open", "arguments": {
                    "project_id": "demo", "request_key": "public-shell-must-not-run",
                },
            }},
            self.mcp.config, self.db, {"version": "2025-11-25"}, "unified",
        )
        self.assertTrue(directly_called["result"]["isError"])
        self.assertEqual(
            directly_called["result"]["structuredContent"]["error"]["status"], 404,
        )
        status, tools = self.rpc("tools/list")
        self.assertEqual(status, 200)
        names = {item["name"] for item in tools["result"]["tools"]}
        self.assertEqual(names, {
            "codex_x_shell_open", "codex_x_shell_write", "codex_x_shell_read",
            "codex_x_shell_resize", "codex_x_shell_signal", "codex_x_shell_close",
            "codex_x_shell_status", "codex_x_shell_list",
        })

    def test_authenticated_shell_real_commands_full_lifecycle(self):
        status, response = self.rpc("tools/call", {"name": "codex_x_shell_open",
            "arguments": {"project_id": "demo", "request_key": "mcp-open"}})
        self.assertEqual(status, 200)
        self.assertFalse(response["result"].get("isError"), response)
        sid = response["result"]["structuredContent"]["session_id"]
        status, sent = self.rpc("tools/call", {"name": "codex_x_shell_write", "arguments": {
            "session_id": sid, "data": "printf 'OPERATOR_SHELL_OK\\n'\n",
            "request_key": "mcp-input",
        }})
        self.assertEqual(status, 200)
        self.assertFalse(sent["result"].get("isError"), sent)
        received = ""
        for _ in range(50):
            _, read = self.rpc("tools/call", {"name": "codex_x_shell_read",
                "arguments": {"session_id": sid, "cursor": 0}})
            received = read["result"]["structuredContent"]["output"]
            if "OPERATOR_SHELL_OK" in received:
                break
            time.sleep(.05)
        self.assertIn("OPERATOR_SHELL_OK", received)
        _, resized = self.rpc("tools/call", {"name": "codex_x_shell_resize", "arguments": {
            "session_id": sid, "columns": 104, "rows": 40,
        }})
        self.assertEqual(resized["result"]["structuredContent"]["rows"], 40)
        _, ended = self.rpc("tools/call", {"name": "codex_x_shell_close", "arguments": {
            "session_id": sid,
        }})
        self.assertFalse(ended["result"]["structuredContent"]["active"])
        self.assertEqual(self.db.unknown_app_writers(), [])

    def test_disabled_shell_and_unknown_tool_fail_closed(self):
        self.operator.manager.config["shell"]["enabled"] = False
        code, denied = self.rpc("tools/call", {"name": "codex_x_shell_open", "arguments": {
            "project_id": "demo", "request_key": "nope",
        }})
        self.assertEqual(code, 200)
        self.assertEqual(denied["result"]["structuredContent"]["error"]["status"], 403)
        code, bad = self.rpc("tools/call", {"name": "codex_x_serena_execute_shell_command",
                                          "arguments": {"command": "pwd"}})
        self.assertEqual(code, 200)
        self.assertEqual(bad["result"]["structuredContent"]["error"]["status"], 404)

    def test_rejects_external_bind_and_shared_shell_key(self):
        with self.assertRaises(ValueError):
            OperatorShellServer(("0.0.0.0", 0), self.config, self.db)
        copy = dict(self.config, shell_key=self.config["mcp_key"])
        with self.assertRaises(ValueError):
            OperatorShellServer(("127.0.0.1", 0), copy, self.db)


if __name__ == "__main__":
    unittest.main()
