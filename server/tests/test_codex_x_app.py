import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from bridge import __main__ as bridge_main, codex_x_app
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

    def test_codex_wrapper_drops_custom_provider_credentials(self):
        config_path = Path(self.tmp.name) / "bridge.json"
        config_path.write_text(json.dumps({
            "gpt_key": "g" * 40, "provider_key": "p" * 40, "mcp_key": "m" * 40,
            "codex_command": ["codex"], "projects": {},
        }))
        config_path.chmod(0o600)
        captured = {}

        def run(command, *, env):
            captured["command"] = command
            captured["env"] = dict(env)
            return 0

        old_umask = os.umask(0o077)
        try:
            with patch.dict(os.environ, {
                "OPENAI_API_KEY": "api-secret", "OPENAI_BASE_URL": "https://custom.invalid",
                "ACCESS_TOKEN": "access-secret", "CODEX_BRIDGE_PROVIDER_KEY": "bridge-secret",
            }, clear=True):
                with patch.object(bridge_main.subprocess, "call", side_effect=run):
                    with patch.object(sys, "argv", ["bridge", "--config", str(config_path), "codex"]):
                        with self.assertRaises(SystemExit):
                            bridge_main.main()
        finally:
            os.umask(old_umask)
        for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "ACCESS_TOKEN", "CODEX_BRIDGE_PROVIDER_KEY"):
            self.assertNotIn(key, captured["env"])

    def test_managed_stdio_app_reuses_initialized_process(self):
        created = []

        class Managed:
            def __init__(self, command, *, env, timeout=60, cleanup=None):
                self.command = tuple(command)
                self.env = dict(env)
                self.timeout = timeout
                self.cleanup = cleanup
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
                if self.cleanup is not None:
                    self.cleanup()
                    self.cleanup = None
                return True

        with patch("bridge.codex_x_app._ManagedNativeApp", Managed):
            first = codex_x_app._managed_app(self.config)
            second = codex_x_app._managed_app(self.config)

        self.assertIs(first, second)
        self.assertEqual(len(created), 1)
        self.assertEqual(list(created[0].command[-2:]), ["app-server", "--stdio"])
        self.assertEqual([call[0] for call in created[0].calls], ["initialize"])
        self.assertEqual(created[0].sent, [{"method": "initialized", "params": {}}])

    def test_web_managed_app_has_separate_isolated_profile_and_cleans_up_on_shutdown(self):
        created = []
        cleanups = []
        native_threads = []

        class Managed:
            def __init__(self, command, *, env, timeout=60, cleanup=None):
                self.command = tuple(command)
                self.env = dict(env)
                self.cleanup = cleanup
                self.closed = False
                self.active_sessions = 0
                created.append(self)

            def call(self, method, params, timeout=None):
                if method == "thread/list":
                    return {"data": list(native_threads)}
                return {}

            def send(self, message):
                pass

            def healthy(self):
                return not self.closed

            def has_active_threads(self):
                for thread in native_threads:
                    status = thread.get("status", {}).get("type")
                    if status == "active":
                        return True
                    if status != "idle":
                        raise Fault(503, "Native Codex thread activity cannot be verified")
                return False

            def close(self):
                self.closed = True
                if self.cleanup is not None:
                    self.cleanup()
                    self.cleanup = None
                return True

        def isolated_env(config):
            return ({"PATH": "/bin", "CODEX_HOME": "/tmp/codex-x-web-home"},
                    lambda: cleanups.append("web-home-removed"))

        local_config = dict(self.config, _dispatch_origin="local")
        auth_source = Path(self.tmp.name) / "auth.json"
        auth_source.write_text("{}", encoding="utf-8")
        web_config = dict(self.config, _dispatch_origin="web", native_codex_home=self.tmp.name)
        with patch("bridge.codex_x_app._ManagedNativeApp", Managed), \
             patch("bridge.codex_x_app._isolated_native_codex_env", side_effect=isolated_env):
            local_first = codex_x_app._managed_app(local_config)
            local_second = codex_x_app._managed_app(local_config)
            web_first = codex_x_app._managed_app(web_config)
            web_second = codex_x_app._managed_app(web_config)

            self.assertIs(local_first, local_second)
            self.assertIs(web_first, web_second)
            self.assertIsNot(local_first, web_first)
            self.assertEqual(local_first.env, codex_x_app._native_env())
            self.assertEqual(web_first.env["CODEX_HOME"], "/tmp/codex-x-web-home")
            self.assertEqual(cleanups, [])

            auth_source.write_text('{"credential_revision":2}', encoding="utf-8")
            native_threads.append({"id": "active-web-thread", "status": {"type": "active"}})
            with self.assertRaises(Fault):
                codex_x_app._managed_app(web_config)
            self.assertEqual(cleanups, [])

            native_threads[:] = [{"id": "unknown-web-thread", "status": {"type": "unknown"}}]
            with self.assertRaises(Fault):
                codex_x_app._managed_app(web_config)
            self.assertEqual(cleanups, [])

            native_threads.clear()
            refreshed_web = codex_x_app._managed_app(web_config)
            self.assertIsNot(refreshed_web, web_first)
            self.assertEqual(cleanups, ["web-home-removed"])

            codex_x_app._shutdown_managed_app()

        self.assertEqual(len(created), 3)
        self.assertEqual(cleanups, ["web-home-removed", "web-home-removed"])

    def test_managed_app_cleanup_waits_for_confirmed_process_stop(self):
        class Process:
            pid = 123

            def __init__(self):
                self.returncode = None
                self.stdin = tempfile.TemporaryFile(mode="w+")
                self.stdout = tempfile.TemporaryFile(mode="w+")

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                self.returncode = 0
                return self.returncode

        class Reader:
            def join(self, timeout=None):
                pass

        app = codex_x_app._ManagedNativeApp.__new__(codex_x_app._ManagedNativeApp)
        app.process = Process()
        app.reader = Reader()
        app.condition = threading.Condition()
        app.cleanup = lambda: cleanups.append("stopped")
        cleanups = []

        def killpg(pid, sig):
            if sig == 0:
                raise ProcessLookupError

        with patch("bridge.codex_x_app.os.killpg", side_effect=killpg):
            app.close()

        self.assertEqual(app.process.poll(), 0)
        self.assertEqual(cleanups, ["stopped"])

    def test_managed_app_retains_auth_home_when_process_stop_is_unverified(self):
        class Process:
            pid = 456

            def __init__(self):
                self.returncode = None
                self.stdin = tempfile.TemporaryFile(mode="w+")
                self.stdout = tempfile.TemporaryFile(mode="w+")

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                raise codex_x_app.subprocess.TimeoutExpired("fake-app-server", timeout)

        class Reader:
            def join(self, timeout=None):
                pass

        home = tempfile.TemporaryDirectory()
        home_path = Path(home.name)
        (home_path / "auth.json").write_text("{}", encoding="utf-8")
        app = codex_x_app._ManagedNativeApp.__new__(codex_x_app._ManagedNativeApp)
        app.process = Process()
        app.reader = Reader()
        app.condition = threading.Condition()
        app.cleanup = home.cleanup
        app._wait_process_group_stopped = lambda timeout=5: False

        with patch("bridge.codex_x_app.os.killpg"):
            with self.assertRaises(codex_x_app.subprocess.TimeoutExpired):
                app.close()

        self.assertFalse(app.stopped())
        self.assertTrue((home_path / "auth.json").is_file())
        del app
        del home
        self.assertTrue(home_path.is_dir())
        (home_path / "auth.json").unlink()
        home_path.rmdir()

    def test_create_thread_uses_native_provider_and_read_only_default(self):
        thread = {
            "id": "thread-1", "name": None, "cwd": self.config["projects"]["demo"]["cwd"],
            "model": "gpt-5.6-sol", "modelProvider": "openai", "status": {"type": "active"}, "updatedAt": 1,
        }
        fake = FakeApp({
            "thread/start": {"thread": dict(thread), "cwd": thread["cwd"], "sandbox": {"type": "readOnly"}},
            "turn/start": {"turn": {"id": "turn-1", "status": "inProgress", "items": []}},
            "thread/read": {"thread": dict(thread)},
        })
        with patch("bridge.codex_x_app._session", return_value=fake_session(fake)):
            result = codex_x_app.create_thread(self.config, {"projectId": "demo", "prompt": "Inspect only"}, self.store)
        self.assertEqual(result["thread"]["modelProvider"], "openai")
        self.assertEqual(result["turnId"], "turn-1")
        start = next(params for method, params in fake.calls if method == "thread/start")
        self.assertEqual(start["modelProvider"], "openai")
        self.assertEqual(start["sandbox"], "read-only")
        turn = next(params for method, params in fake.calls if method == "turn/start")
        self.assertEqual(turn["input"], [{"type": "text", "text": "Inspect only"}])

    def test_web_create_thread_uses_exact_native_web_model_and_supported_effort(self):
        config = dict(self.config, _dispatch_origin="web")
        root = config["projects"]["demo"]["cwd"]
        model_id = "chatgpt-web/gpt-5.6-sol"
        thread = {
            "id": "thread-web", "name": None, "cwd": root,
            "model": model_id, "modelProvider": "openai",
            "status": {"type": "active"}, "updatedAt": 1,
        }
        fake = FakeApp({
            "model/list": {"data": [{
                "id": model_id, "model": model_id, "isDefault": True,
                "supportedReasoningEfforts": [{"reasoningEffort": "high"}],
                "defaultReasoningEffort": "high",
            }]},
            "thread/start": {"thread": dict(thread), "cwd": root, "sandbox": {"type": "readOnly"}},
            "turn/start": {"turn": {"id": "turn-web"}},
            "thread/read": {"thread": dict(thread)},
        })
        with patch("bridge.codex_x_app._session", return_value=fake_session(fake)):
            codex_x_app.create_thread(config, {"projectId": "demo", "prompt": "Inspect"}, self.store)
        start = next(params for method, params in fake.calls if method == "thread/start")
        turn = next(params for method, params in fake.calls if method == "turn/start")
        self.assertEqual(start["model"], model_id)
        self.assertEqual(turn["model"], model_id)
        self.assertEqual(turn["effort"], "high")

    def test_web_create_thread_rejects_non_web_model_before_thread_creation(self):
        config = dict(self.config, _dispatch_origin="web")
        model_id = "chatgpt-web/gpt-5.6-sol"
        fake = FakeApp({"model/list": {"data": [{
            "id": model_id, "model": model_id, "isDefault": True,
            "supportedReasoningEfforts": [{"reasoningEffort": "high"}],
            "defaultReasoningEffort": "high",
        }]}})
        with patch("bridge.codex_x_app._session", return_value=fake_session(fake)):
            with self.assertRaises(Fault):
                codex_x_app.create_thread(
                    config,
                    {"projectId": "demo", "prompt": "Inspect", "model": "gpt-5.6-sol"},
                    self.store,
                )
        self.assertIn("model/list", [method for method, _ in fake.calls])
        self.assertNotIn("thread/start", [method for method, _ in fake.calls])

    def test_web_send_message_rejects_non_web_parent_model_before_turn(self):
        config = dict(self.config, _dispatch_origin="web")
        root = config["projects"]["demo"]["cwd"]
        thread = {
            "id": "thread-local-model", "cwd": root, "model": "gpt-5.6-sol",
            "modelProvider": "openai", "sandbox": "read-only", "turns": [],
        }
        fake = FakeApp({"thread/read": {"thread": thread}})
        with patch("bridge.codex_x_app._session", return_value=fake_session(fake)):
            with self.assertRaises(Fault):
                codex_x_app.send_message_to_thread(
                    config, {"threadId": thread["id"], "prompt": "Continue"}, self.store,
                )
        self.assertNotIn("turn/start", [method for method, _ in fake.calls])
        self.assertNotIn("turn/steer", [method for method, _ in fake.calls])

    def test_web_send_message_pins_parent_model_and_supported_effort(self):
        config = dict(self.config, _dispatch_origin="web")
        root = config["projects"]["demo"]["cwd"]
        model_id = "chatgpt-web/gpt-5.6-sol"
        thread = {
            "id": "thread-web-parent", "cwd": root, "model": model_id,
            "modelProvider": "openai", "sandbox": "read-only", "turns": [],
        }
        fake = FakeApp({
            "thread/read": {"thread": thread},
            "model/list": {"data": [{
                "id": model_id, "model": model_id, "isDefault": True,
                "supportedReasoningEfforts": [{"reasoningEffort": "high"}],
                "defaultReasoningEffort": "high",
            }]},
            "turn/start": {"turn": {"id": "turn-web-followup"}},
        })
        with patch("bridge.codex_x_app._session", return_value=fake_session(fake)):
            codex_x_app.send_message_to_thread(
                config, {"threadId": thread["id"], "prompt": "Continue"}, self.store,
            )
        turn = next(params for method, params in fake.calls if method == "turn/start")
        self.assertEqual(turn["model"], model_id)
        self.assertEqual(turn["effort"], "high")

    def test_web_send_message_refuses_to_steer_active_turn_with_unverified_effort(self):
        config = dict(self.config, _dispatch_origin="web")
        root = config["projects"]["demo"]["cwd"]
        model_id = "chatgpt-web/gpt-5.6-sol"
        for active_effort in (None, "low"):
            with self.subTest(active_effort=active_effort):
                thread = {
                    "id": "thread-web-active", "cwd": root, "model": model_id,
                    "effort": active_effort, "modelProvider": "openai", "sandbox": "read-only",
                    "turns": [{"id": "turn-active", "status": "inProgress", "items": []}],
                }
                fake = FakeApp({
                    "thread/read": {"thread": thread},
                    "model/list": {"data": [{
                        "id": model_id, "model": model_id, "isDefault": True,
                        "supportedReasoningEfforts": [{"reasoningEffort": "high"}],
                        "defaultReasoningEffort": "high",
                    }]},
                    "turn/steer": {"turnId": "turn-active"},
                })
                with patch("bridge.codex_x_app._session", return_value=fake_session(fake)):
                    with self.assertRaises(Fault):
                        codex_x_app.send_message_to_thread(
                            config, {"threadId": thread["id"], "prompt": "Continue"}, self.store,
                        )
                self.assertNotIn("turn/steer", [method for method, _ in fake.calls])

        verified_thread = {
            "id": "thread-web-active-verified", "cwd": root, "model": model_id,
            "effort": "high", "modelProvider": "openai", "sandbox": "read-only",
            "turns": [{"id": "turn-active", "status": "inProgress", "items": []}],
        }
        verified_fake = FakeApp({
            "thread/read": {"thread": verified_thread},
            "model/list": {"data": [{
                "id": model_id, "model": model_id, "isDefault": True,
                "supportedReasoningEfforts": [{"reasoningEffort": "high"}],
                "defaultReasoningEffort": "high",
            }]},
            "turn/steer": {"turnId": "turn-active"},
        })
        with patch("bridge.codex_x_app._session", return_value=fake_session(verified_fake)):
            result = codex_x_app.send_message_to_thread(
                config, {"threadId": verified_thread["id"], "prompt": "Continue"}, self.store,
            )
        self.assertEqual(result["mode"], "steer")
        self.assertIn("turn/steer", [method for method, _ in verified_fake.calls])

    def test_web_fork_with_prompt_pins_parent_model_and_supported_effort(self):
        config = dict(self.config, _dispatch_origin="web")
        root = config["projects"]["demo"]["cwd"]
        model_id = "chatgpt-web/gpt-5.6-sol"
        source = {
            "id": "thread-web-source", "cwd": root, "model": model_id,
            "modelProvider": "openai", "sandbox": "read-only",
        }
        fork = {**source, "id": "thread-web-fork"}
        fake = FakeApp({
            "thread/read": [{"thread": source}, {"thread": fork}],
            "model/list": {"data": [{
                "id": model_id, "model": model_id, "isDefault": True,
                "supportedReasoningEfforts": [{"reasoningEffort": "high"}],
                "defaultReasoningEffort": "high",
            }]},
            "thread/fork": {
                "thread": fork, "cwd": root, "sandbox": {"type": "readOnly"},
            },
            "turn/start": {"turn": {"id": "turn-web-fork"}},
        })
        with patch("bridge.codex_x_app._session", return_value=fake_session(fake)):
            codex_x_app.fork_thread(
                config,
                {"threadId": source["id"], "prompt": "Continue"},
                self.store,
            )
        fork_params = next(params for method, params in fake.calls if method == "thread/fork")
        turn = next(params for method, params in fake.calls if method == "turn/start")
        self.assertEqual(fork_params["model"], model_id)
        self.assertEqual(turn["model"], model_id)
        self.assertEqual(turn["effort"], "high")

    def test_created_thread_scope_survives_restart_when_native_read_omits_sandbox(self):
        root = self.config["projects"]["demo"]["cwd"]
        thread = {"id": "persisted", "cwd": root, "modelProvider": "openai"}
        fake = FakeApp({
            "thread/start": {"thread": thread, "cwd": root, "sandbox": {"type": "readOnly"}},
            "turn/start": {"turn": {"id": "turn-1"}},
            "thread/read": {"thread": thread},
        })
        with patch("bridge.codex_x_app._session", return_value=fake_session(fake)):
            codex_x_app.create_thread(self.config, {"prompt": "Inspect"}, self.store)
        second_store = Store(Path(self.tmp.name) / "state.sqlite3")
        try:
            with patch("bridge.codex_x_app._session", return_value=fake_session(fake)):
                read = codex_x_app.read_thread(self.config, {"threadId": "persisted"}, second_store)
            self.assertEqual(read["thread"]["id"], "persisted")
            self.assertEqual(second_store.app_thread_scope("persisted")["scope"], "read-only")
            fake.responses["thread/read"] = {"thread": {**thread, "sandbox": "workspace-write"}}
            with patch("bridge.codex_x_app._session", return_value=fake_session(fake)):
                with self.assertRaises(Fault) as exc:
                    codex_x_app.send_message_to_thread(self.config, {"threadId": "persisted", "prompt": "Edit"}, second_store)
            self.assertEqual(exc.exception.status, 403)
        finally:
            second_store.close()

    def test_create_thread_requires_native_scope_proof_before_first_turn(self):
        root = self.config["projects"]["demo"]["cwd"]
        thread = {"id": "unproved", "cwd": root, "modelProvider": "openai"}
        fake = FakeApp({"thread/start": {"thread": thread, "cwd": root}})
        with patch("bridge.codex_x_app._session", return_value=fake_session(fake)):
            with self.assertRaises(Fault) as exc:
                codex_x_app.create_thread(self.config, {"prompt": "Inspect"}, self.store)
        self.assertEqual(exc.exception.status, 403)
        self.assertIsNone(self.store.app_thread_scope("unproved"))
        self.assertEqual([method for method, _ in fake.calls], ["thread/start"])

    def test_send_message_steers_active_turn(self):
        thread = {
            "id": "thread-1", "modelProvider": "openai", "status": {"type": "active"},
            "cwd": self.config["projects"]["demo"]["cwd"], "sandbox": "read-only",
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

    def test_native_thread_listing_filters_other_projects_and_unprovable_scope(self):
        root = Path(self.tmp.name)
        allowed = {"id": "allowed", "cwd": str(root), "modelProvider": "openai", "sandbox": "read-only"}
        foreign = {**allowed, "id": "foreign", "cwd": str(root.parent)}
        unproved = {**allowed, "id": "unproved", "sandbox": None}
        custom = {**allowed, "id": "custom", "modelProvider": "custom"}
        fake = FakeApp({"thread/list": {"data": [allowed, foreign, unproved, custom]}})
        with patch("bridge.codex_x_app._session", return_value=fake_session(fake)):
            value = codex_x_app.list_threads(self.config, {})
        self.assertEqual([item["threadId"] for item in value["threads"]], ["allowed"])

    def test_native_thread_read_in_allowed_project_subdirectory(self):
        root = Path(self.tmp.name)
        nested = root / "src"
        nested.mkdir()
        thread = {"id": "local", "cwd": str(nested), "modelProvider": "openai", "sandbox": "read-only"}
        fake = FakeApp({"thread/read": {"thread": thread}})
        with patch("bridge.codex_x_app._session", return_value=fake_session(fake)):
            result = codex_x_app.read_thread(self.config, {"threadId": "local"})
        self.assertEqual(result["thread"]["id"], "local")

    def test_native_thread_symlink_escape_denied(self):
        root = Path(self.tmp.name)
        nested_link = root / "escape"
        nested_link.symlink_to(root.parent, target_is_directory=True)
        thread = {"id": "escape", "cwd": str(nested_link), "modelProvider": "openai", "sandbox": "read-only"}
        fake = FakeApp({"thread/read": {"thread": thread}})
        with patch("bridge.codex_x_app._session", return_value=fake_session(fake)):
            with self.assertRaises(Fault) as exc:
                codex_x_app.read_thread(self.config, {"threadId": "escape"})
        self.assertEqual(exc.exception.status, 403)

    def test_native_thread_cross_project_read_and_mutation_denied(self):
        root = Path(self.tmp.name)
        thread = {"id": "foreign", "cwd": str(root.parent), "modelProvider": "openai", "sandbox": "read-only"}
        for action, args in (
            (codex_x_app.read_thread, {"threadId": "foreign"}),
            (codex_x_app.fork_thread, {"threadId": "foreign", "prompt": "alter"}),
            (codex_x_app.send_message_to_thread, {"threadId": "foreign", "prompt": "alter"}),
            (codex_x_app.set_thread_title, {"threadId": "foreign", "title": "alter"}),
            (codex_x_app.set_thread_archived, {"threadId": "foreign", "archived": True}),
            (codex_x_app.wait_threads, {"threadIds": ["foreign"], "timeoutMs": 0}),
        ):
            with self.subTest(action=action.__name__):
                fake = FakeApp({"thread/read": {"thread": thread}})
                with patch("bridge.codex_x_app._session", return_value=fake_session(fake)):
                    with self.assertRaises(Fault) as exc:
                        action(self.config, args)
                self.assertEqual(exc.exception.status, 403)
                self.assertEqual([call[0] for call in fake.calls], ["thread/read"])

    def test_native_thread_missing_scope_and_write_escalation_denied(self):
        root = str(Path(self.tmp.name))
        for payload in (
            {"id": "unproven", "cwd": root, "modelProvider": "openai"},
            {"id": "writable", "cwd": root, "modelProvider": "openai", "sandbox": "workspace-write"},
        ):
            self.config["projects"]["demo"]["allow_write"] = False
            fake = FakeApp({"thread/read": {"thread": payload}})
            with patch("bridge.codex_x_app._session", return_value=fake_session(fake)):
                with self.assertRaises(Fault) as exc:
                    codex_x_app.send_message_to_thread(self.config, {"threadId": payload["id"], "prompt": "write"})
            self.assertEqual(exc.exception.status, 403)
            self.assertEqual([call[0] for call in fake.calls], ["thread/read"])

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
