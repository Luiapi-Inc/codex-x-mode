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
        self.assertEqual(result["result"]["execution_evidence"]["native_execution"], "terminal_completed")
        self.assertTrue(result["result"]["execution_evidence"]["acceptance_verified"])
        self.assertEqual(model["reroutes"], [])

        class NoTerminalModel(StubNative):
            def receive(self):
                return {"method": "turn/completed", "params": {
                    "threadId": "native-thread", "turn": {"id": "native-turn", "status": "completed"},
                }}

            def call(self, method, params):
                if method == "thread/read":
                    return {"thread": {
                        "id": "native-thread", "model": self.started_model,
                        "turns": [{"id": "native-turn", "status": "completed"}],
                    }}
                return super().call(method, params)

        with patch.object(service, "codex_list_models", return_value={
            "models": native_models(), "catalog_source": "model/list",
            "model_entitlement_verified": False
        }):
            other = service.create_task(self.config, self.store, {
                "project_id": "demo", "prompt": "Read only again",
                "scope": "read-only", "request_key": "native-v1-unverified-readback",
            })
        with patch.object(codex, "AppServer", NoTerminalModel):
            codex.run_task(self.config, self.store, self.store.claim("task"))
        uncertain = self.store.get(other["id"])
        self.assertEqual(uncertain["state"], "failed")
        evidence = uncertain["result"]["model_selection"]
        self.assertFalse(evidence["model_identity_verified"])
        self.assertFalse(evidence["inference_verified"])
        self.assertEqual(uncertain["result"]["execution_evidence"]["native_execution"], "terminal_completed")
        self.assertEqual(uncertain["result"]["execution_evidence"]["reason"], "terminal_model_absent")
        self.assertFalse(uncertain["result"]["execution_evidence"]["acceptance_verified"])
        self.assertTrue(uncertain["result"]["execution_evidence"]["thread_configured_model_matches_selected"])
        self.assertIsNone(evidence["observed_model"])
        self.assertTrue(evidence["thread_readback"]["thread_model_matches_selected"])
        self.assertEqual(evidence["thread_readback"]["turn_status"], "completed")
        self.assertFalse(evidence["thread_readback"]["terminal_model_identity_verified"])

        class FutureTurnModelReadback(NoTerminalModel):
            def call(self, method, params):
                if method == "thread/read":
                    return {"thread": {
                        "id": "native-thread", "model": self.started_model,
                        "turns": [{"id": "native-turn", "status": "completed",
                                   "model": self.started_model}],
                    }}
                return super().call(method, params)

        with patch.object(service, "codex_list_models", return_value={
            "models": native_models(), "catalog_source": "model/list",
            "model_entitlement_verified": False
        }):
            future = service.create_task(self.config, self.store, {
                "project_id": "demo", "prompt": "Future protocol readback",
                "scope": "read-only", "request_key": "native-future-readback",
            })
        with patch.object(codex, "AppServer", FutureTurnModelReadback):
            codex.run_task(self.config, self.store, self.store.claim("task"))
        observed = self.store.get(future["id"])
        self.assertEqual(observed["state"], "completed", observed.get("result"))
        self.assertTrue(observed["result"]["model_selection"]["model_identity_verified"])
        self.assertEqual(observed["result"]["execution_evidence"]["proof_source"],
                         "thread/read.turn.model")
        self.assertTrue(observed["result"]["execution_evidence"]["acceptance_verified"])

        class MismatchedPerTurnModel(FutureTurnModelReadback):
            def call(self, method, params):
                if method == "thread/read":
                    return {"thread": {"id": "native-thread", "model": self.started_model,
                                       "turns": [{"id": "native-turn", "status": "completed",
                                                  "model": "gpt-5.5"}]}}
                return super().call(method, params)

        with patch.object(service, "codex_list_models", return_value={
            "models": native_models(), "catalog_source": "model/list",
            "model_entitlement_verified": False
        }):
            mismatch_job = service.create_task(self.config, self.store, {
                "project_id": "demo", "prompt": "Read only mismatch",
                "scope": "read-only", "request_key": "native-mismatched-readback",
            })
        with patch.object(codex, "AppServer", MismatchedPerTurnModel):
            codex.run_task(self.config, self.store, self.store.claim("task"))
        mismatch_result = self.store.get(mismatch_job["id"])
        self.assertEqual(mismatch_result["state"], "failed")
        self.assertEqual(mismatch_result["result"]["execution_evidence"]["reason"],
                         "terminal_model_mismatch")
        self.assertFalse(mismatch_result["result"]["execution_evidence"]["acceptance_verified"])

        class UnrelatedTurnModel(FutureTurnModelReadback):
            def call(self, method, params):
                if method == "thread/read":
                    return {"thread": {"id": "native-thread", "model": self.started_model,
                                       "turns": [{"id": "not-our-turn", "status": "completed",
                                                  "model": self.started_model}]}}
                return super().call(method, params)

        with patch.object(service, "codex_list_models", return_value={
            "models": native_models(), "catalog_source": "model/list",
            "model_entitlement_verified": False
        }):
            unrelated = service.create_task(self.config, self.store, {
                "project_id": "demo", "prompt": "Read only unrelated",
                "scope": "read-only", "request_key": "native-unrelated-readback",
            })
        with patch.object(codex, "AppServer", UnrelatedTurnModel):
            codex.run_task(self.config, self.store, self.store.claim("task"))
        unrelated_result = self.store.get(unrelated["id"])
        self.assertEqual(unrelated_result["state"], "failed")
        self.assertFalse(unrelated_result["result"]["model_selection"]["model_identity_verified"])

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
