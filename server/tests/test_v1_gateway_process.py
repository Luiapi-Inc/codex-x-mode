"""G3 one-connector integration using real HTTP + fake Native Codex app-server."""
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from bridge.codex_x_app import _shutdown_managed_app
from bridge.core import Store
from bridge.http import Server


class UnifiedGatewayProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        home = root / "home"
        home.mkdir()
        (home / "auth.json").write_text('{"fake":"no-real-auth"}')
        (home / "auth.json").chmod(0o600)
        self.store = Store(root / "db.sqlite3")
        self.config = {
            "gpt_key": "g" * 40,
            "mcp_key": "m" * 40,
            "provider_key": "p" * 40,
            "projects": {},
            "mcp_policy": {"mode": "read-only"},
            "web_model_policy": "native",
            "native_codex_home": str(home),
            "codex_command": [sys.executable, str(Path(__file__).with_name("fake_gateway_native.py"))],
        }
        self.server = Server(("127.0.0.1", 0), self.config, self.store, start_worker=False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()
        _shutdown_managed_app()
        self.store.close()
        self.tmp.cleanup()

    def call(self, method, params=None, *, bearer=True):
        request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        headers = {"Content-Type": "application/json"}
        if bearer:
            headers["Authorization"] = "Bearer " + self.config["mcp_key"]
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            conn.request("POST", "/mode/mcp", json.dumps(request), headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def test_one_authenticated_connector_reaches_core_and_real_native_app_subprocess(self):
        status, response = self.call("tools/list")
        self.assertEqual(status, 200)
        names = [t["name"] for t in response["result"]["tools"]]
        self.assertIn("codex_x_list_models", names)
        self.assertIn("codex_x_app_list_threads", names)
        self.assertNotIn("codex_x_app_create_thread", names)
        status, response = self.call("tools/call", {"name": "codex_x_status"})
        self.assertEqual(status, 200)
        self.assertEqual(response["result"]["structuredContent"]["status"], "up")
        status, response = self.call("tools/call", {"name": "codex_x_list_models"})
        self.assertEqual(status, 200)
        catalog = response["result"]["structuredContent"]
        self.assertIn("chatgpt-web/gpt-fixture", catalog["supported_models"])
        self.assertIn("gpt-non-web", catalog["supported_models"])
        self.assertIsNone(catalog["route_prefix"])
        status, response = self.call("tools/call", {"name": "codex_x_app_list_threads"})
        self.assertEqual(status, 200)
        self.assertEqual(response["result"]["structuredContent"]["threads"], [])

    def test_authenticated_serena_preview_one_mcp_transport(self):
        project = Path(self.tmp.name) / "serena-project"
        project.mkdir()
        (project / "one.py").write_text("value = old\n")
        (project / "two.py").write_text("value = old\n")
        original = {file.name: file.read_bytes() for file in project.iterdir()}
        self.config["projects"] = {
            "demo": {"cwd": str(project.resolve()), "allow_write": False}
        }
        self.config["serena"] = {
            "context": "chatgpt", "enabled": True, "allow_mutations": False,
            "timeout_seconds": 5,
            "command": [sys.executable,
                        str(Path(__file__).with_name("fake_serena_mcp.py"))],
        }
        # HTTP server snapshots startup config to protect live policy.
        # Set only the ephemeral test server's config, never production state.
        self.server.config.update({
            "projects": self.config["projects"],
            "serena": self.config["serena"],
        })
        unauth_status, _ = self.call("tools/list", bearer=False)
        self.assertEqual(unauth_status, 401)
        auth_status, listing = self.call("tools/list")
        self.assertEqual(auth_status, 200)
        names = [tool["name"] for tool in listing["result"]["tools"]]
        self.assertIn("codex_x_serena_preview_replace_in_files", names)
        self.assertIn("codex_x_serena_find_symbol", names)
        self.assertNotIn("codex_x_serena_execute_shell_command", names)
        self.assertNotIn("codex_x_serena_replace_in_files", names)
        http_status, answer = self.call("tools/call", {
            "name": "codex_x_serena_preview_replace_in_files",
            "arguments": {
                "project_id": "demo", "relative_path": ".",
                "needle": "old", "repl": "new", "mode": "literal",
            },
        })
        self.assertEqual(http_status, 200)
        self.assertFalse(answer["result"].get("isError"), answer)
        result = answer["result"]["structuredContent"]
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["applied"])
        self.assertEqual(result["context"], "chatgpt")
        self.assertEqual(
            {file.name: file.read_bytes() for file in project.iterdir()},
            original,
        )
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0,
        )

    def test_mutation_and_anonymous_calls_have_no_privileged_side_effect(self):
        status, _ = self.call("tools/list", bearer=False)
        self.assertEqual(status, 401)
        status, response = self.call("tools/call", {
            "name": "codex_x_app_create_thread", "arguments": {"prompt": "must-not-run"}
        })
        self.assertEqual(status, 200)
        self.assertEqual(response["result"]["structuredContent"]["error"]["status"], 403)
        self.assertEqual(self.store.unknown_projects(), [])
