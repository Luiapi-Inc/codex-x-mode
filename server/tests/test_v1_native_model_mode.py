"""G3 v1 model policy: exact executable Native IDs from isolated profile."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge import service, codex, codex_x_app
from bridge.core import Store, Fault


def native_models():
    return [
        {"id": "gpt-6.1-sol", "model": "gpt-6.1-sol", "display_name": "Sol",
         "is_default": True, "default_reasoning_effort": "high",
         "supported_reasoning_efforts": ["medium", "high"]},
        {"id": "gpt-6-astra", "model": "gpt-6-astra", "display_name": "Astra",
         "is_default": False, "default_reasoning_effort": "high",
         "supported_reasoning_efforts": ["high"]},
    ]


class NativeModelModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.store = Store(self.root / "db.sqlite3")
        self.config = {
            "_dispatch_origin": "web",
            "web_model_policy": "native",
            "projects": {"demo": {"cwd": str(self.root), "allow_write": False}},
            "codex_command": ["codex"],
        }

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_v1_catalog_allows_exact_native_models_from_isolated_auth_only(self):
        def fake(*args, **kwargs):
            self.assertIsNone(kwargs["required_prefix"])
            self.assertTrue(kwargs["native_isolated"])
            return {"catalog_source": "model/list", "model_entitlement_verified": False,
                    "models": native_models()}
        with patch.object(service, "codex_list_models", side_effect=fake):
            result = service.list_models(self.config)
            self.assertEqual(result["supported_models"], ["gpt-6.1-sol", "gpt-6-astra"])
            self.assertEqual(result["resolved_default_model"], "gpt-6.1-sol")
            self.assertIsNone(result["route_prefix"])
            selected = service._web_model_snapshot(self.config, None)
            self.assertEqual(selected["model"], "gpt-6.1-sol")
            self.assertEqual(selected["reasoning_effort"], "high")
            with self.assertRaises(Fault):
                service._web_model_snapshot(self.config, "gpt-not-visible")

    def test_v1_dispatch_executes_exact_native_model_without_fallback(self):
        home = self.root / "codex-home"
        home.mkdir()
        (home / "auth.json").write_text('{"fixture":true}')
        (home / "auth.json").chmod(0o600)
        self.config["native_codex_home"] = str(home)

        class StubNative:
            def __init__(self, *_args, **kwargs):
                self.notifications = []
                self.cleanup = kwargs.get("cleanup")
                self.started_model = None

            def call(self, method, params):
                if method == "initialize":
                    return {}
                if method == "model/list":
                    return {"data": [
                        {"id": "gpt-6.1-sol", "model": "gpt-6.1-sol",
                         "isDefault": True, "defaultReasoningEffort": "high",
                         "supportedReasoningEfforts": [{"reasoningEffort": "high"}]},
                    ], "nextCursor": None}
                if method == "thread/start":
                    assert params["model"] == "gpt-6.1-sol"
                    assert params["sandbox"] == "read-only"
                    return {"thread": {"id": "native-thread"}}
                if method == "turn/start":
                    assert params["model"] == "gpt-6.1-sol"
                    assert params["effort"] == "high"
                    self.started_model = params["model"]
                    return {"turn": {"id": "native-turn"}}
                raise AssertionError(method)

            def send(self, _message):
                pass

            def receive(self):
                return {"method": "turn/completed", "params": {
                    "threadId": "native-thread",
                    "turn": {"id": "native-turn", "status": "completed",
                             "model": self.started_model},
                }}

            def close(self):
                if self.cleanup:
                    self.cleanup()
                    self.cleanup = None
                return True

        with patch.object(service, "codex_list_models", return_value={
            "models": native_models(), "catalog_source": "model/list",
            "model_entitlement_verified": False
        }):
            job = service.create_task(self.config, self.store, {
                "project_id": "demo", "prompt": "Read only",
                "scope": "read-only", "request_key": "native-v1-exact",
            })
        with patch.object(codex, "AppServer", StubNative):
            codex.run_task(self.config, self.store, self.store.claim("task"))
        result = self.store.get(job["id"])
        self.assertEqual(result["state"], "completed", result.get("result"))
        model = result["result"]["model_selection"]
        self.assertEqual(model["id"], "gpt-6.1-sol")
        self.assertEqual(model["observed_model"], "gpt-6.1-sol")
        self.assertTrue(model["model_identity_verified"])
        self.assertEqual(model["reroutes"], [])

    def test_v1_app_provider_selects_actual_native_executable_id_and_effort(self):
        class App:
            def call(self, method, params):
                assert method == "model/list"
                return {"data": [{
                    "id": "gpt-6.1-sol", "model": "gpt-6.1-sol",
                    "isDefault": True, "defaultReasoningEffort": "high",
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "medium"}, {"reasoningEffort": "high"}
                    ]
                }], "nextCursor": None}
        selection = codex_x_app._web_model(self.config, App())
        self.assertEqual(selection["model"], "gpt-6.1-sol")
        self.assertEqual(selection["reasoning_effort"], "high")


if __name__ == "__main__":
    unittest.main()
