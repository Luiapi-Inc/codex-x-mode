import http.client
import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from bridge import codex_x_app
from bridge.core import Fault, Store
from bridge.http import Server
from bridge.mcp import rpc_response


class FakeApp:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        value = self.responses.get(method)
        if callable(value):
            return value(params)
        if isinstance(value, list):
            if not value:
                raise AssertionError(f"No fake response left for {method}")
            return value.pop(0)
        if value is None:
            return {}
        return value


@contextmanager
def fake_session(app):
    yield app


class CodexXAppContractTests(unittest.TestCase):
    def setUp(self):
        codex_x_app._shutdown_managed_app()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = Store(root / "state.sqlite3")
        self.config = {
            "codex_command": ["codex"],
            "projects": {"demo": {"cwd": str(root.resolve()), "allow_write": True}},
            "mcp_key": "m" * 40,
            "gpt_key": "g" * 40,
            "provider_key": "p" * 40,
        }

    def tearDown(self):
        codex_x_app._shutdown_managed_app()
        self.store.close()
        self.tmp.cleanup()

    def test_codex_x_app_surface_lists_native_backed_subset_only(self):
        state = {}
        initialized = rpc_response({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
        }, self.config, self.store, state, "codex_x_app")
        self.assertEqual(initialized["result"]["serverInfo"]["name"], "codex-x-app")
        listed = rpc_response({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                              self.config, self.store, state, "codex_x_app")
        names = [tool["name"] for tool in listed["result"]["tools"]]
        self.assertEqual(names, [
            "list_threads", "read_thread", "create_thread", "fork_thread",
            "send_message_to_thread", "set_thread_title", "set_thread_archived", "wait_threads",
        ])
        self.assertNotIn("automation_update", names)
        self.assertNotIn("set_thread_pinned", names)
        self.assertNotIn("handoff_thread", names)

    def test_codex_x_surface_remains_separate(self):
        state = {}
        rpc_response({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
        }, self.config, self.store, state)
        listed = rpc_response({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                              self.config, self.store, state)
        names = [tool["name"] for tool in listed["result"]["tools"]]
        self.assertEqual(len(names), 13)
        self.assertTrue(all(name.startswith("codex_x_") for name in names))

    def test_native_env_drops_custom_provider_credentials(self):
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "secret", "OPENAI_BASE_URL": "http://custom",
            "ACCESS_TOKEN": "secret", "CODEX_BRIDGE_PROVIDER_KEY": "secret", "PATH": "/bin",
        }, clear=True):
            env = codex_x_app._native_env()
        for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "ACCESS_TOKEN", "CODEX_BRIDGE_PROVIDER_KEY"):
            self.assertNotIn(key, env)
        self.assertEqual(env["PATH"], "/bin")
        command = codex_x_app._command(self.config, "app-server", "--stdio")
        self.assertIn('model_provider="openai"', command)
        self.assertEqual(command[-2:], ["app-server", "--stdio"])

    def test_managed_stdio_app_reuses_initialized_process(self):
        created = []

        class Managed:
            def __init__(self, command, *, env, timeout=60):
                self.command = tuple(command)
                self.env = dict(env)
                self.timeout = timeout
                self.calls = []
                self.sent = []
                self.closed = False
                created.append(self)

            def call(self, method, params, timeout=None):
                self.calls.append((method, params, timeout))
                return {}

            def send(self, message):
                self.sent.append(message)

            def healthy(self):
                return not self.closed

            def close(self):
                self.closed = True

        with patch("bridge.codex_x_app._ManagedNativeApp", Managed):
            first = codex_x_app._managed_app(self.config)
            second = codex_x_app._managed_app(self.config)

        self.assertIs(first, second)
        self.assertEqual(len(created), 1)
        self.assertEqual(list(created[0].command[-2:]), ["app-server", "--stdio"])
        self.assertEqual([call[0] for call in created[0].calls], ["initialize"])
        self.assertEqual(created[0].sent, [{"method": "initialized", "params": {}}])

    def test_create_thread_uses_native_provider_and_read_only_default(self):
        thread = {
            "id": "thread-1", "name": None, "cwd": self.config["projects"]["demo"]["cwd"],
            "model": "gpt-5.6-sol", "modelProvider": "openai", "status": {"type": "active"}, "updatedAt": 1,
        }
        fake = FakeApp({
            "thread/start": {"thread": dict(thread)},
            "turn/start": {"turn": {"id": "turn-1", "status": "inProgress", "items": []}},
            "thread/read": {"thread": dict(thread)},
        })
        with patch("bridge.codex_x_app._session", return_value=fake_session(fake)):
            result = codex_x_app.create_thread(self.config, {"projectId": "demo", "prompt": "Inspect only"})
        self.assertEqual(result["thread"]["modelProvider"], "openai")
        self.assertEqual(result["turnId"], "turn-1")
        start = next(params for method, params in fake.calls if method == "thread/start")
        self.assertEqual(start["modelProvider"], "openai")
        self.assertEqual(start["sandbox"], "read-only")
        turn = next(params for method, params in fake.calls if method == "turn/start")
        self.assertEqual(turn["input"], [{"type": "text", "text": "Inspect only"}])

    def test_send_message_steers_active_turn(self):
        thread = {
            "id": "thread-1", "modelProvider": "openai", "status": {"type": "active"},
            "turns": [{"id": "turn-active", "status": "inProgress", "items": []}],
        }
        fake = FakeApp({
            "thread/read": {"thread": thread},
            "turn/steer": {"turnId": "turn-active"},
        })
        with patch("bridge.codex_x_app._session", return_value=fake_session(fake)):
            result = codex_x_app.send_message_to_thread(self.config, {"threadId": "thread-1", "prompt": "New direction"})
        self.assertEqual(result["mode"], "steer")
        params = next(params for method, params in fake.calls if method == "turn/steer")
        self.assertEqual(params["expectedTurnId"], "turn-active")

    def test_rejects_non_native_thread(self):
        fake = FakeApp({"thread/read": {"thread": {"id": "legacy", "modelProvider": "custom_gpt_bridge", "turns": []}}})
        with patch("bridge.codex_x_app._session", return_value=fake_session(fake)):
            with self.assertRaises(Fault) as caught:
                codex_x_app.send_message_to_thread(self.config, {"threadId": "legacy", "prompt": "x"})
        self.assertEqual(caught.exception.status, 409)

    def test_http_codex_x_app_mcp_surface(self):
        server = Server(("127.0.0.1", 0), self.config, self.store, start_worker=False)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "http-test", "version": "1"},
            }}
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            body = json.dumps(request)
            conn.request("POST", "/codex-x-app/mcp", body=body, headers={
                "Authorization": "Bearer " + self.config["mcp_key"],
                "Content-Type": "application/json", "Content-Length": str(len(body.encode())),
            })
            response = conn.getresponse()
            payload = json.loads(response.read())
            conn.close()
            self.assertEqual(response.status, 200)
            self.assertEqual(payload["result"]["serverInfo"]["name"], "codex-x-app")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
