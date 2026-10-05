from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from bridge import codex, service, siwc
from bridge.core import Fault, Store
from bridge.mcp import _call_tool
from bridge.service import create_task, continue_task
from tests.test_siwc import credentials


ACCOUNT_CATALOG = {
    "backend": "chatgpt_plan",
    "catalog_source": "responses_api_models",
    "catalog_integrity_verified": True,
    "model_entitlement_verified": False,
    "models": [
        {
            "id": "gpt-5.5",
            "model": "gpt-5.5",
            "display_name": "GPT-5.5",
            "is_default": False,
            "supported_reasoning_efforts": [],
        },
        {
            "id": "gpt-5.6-luna",
            "model": "gpt-5.6-luna",
            "display_name": "GPT-5.6-Luna",
            "is_default": False,
            "supported_reasoning_efforts": [],
        },
        {
            "id": "gpt-5.6-sol",
            "model": "gpt-5.6-sol",
            "display_name": "GPT-5.6-Sol",
            "is_default": False,
            "supported_reasoning_efforts": [],
        },
        {
            "id": "gpt-6-astra",
            "model": "gpt-6-astra",
            "display_name": "GPT-6-Astra",
            "is_default": True,
            "supported_reasoning_efforts": [],
        },
    ],
}


class WebDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        project = root / "project"
        project.mkdir()
        self.store = Store(root / "state.sqlite3")
        self.config = {
            "_dispatch_origin": "web",
            "projects": {"demo": {"cwd": str(project.resolve()), "allow_write": True}},
            "codex_command": [sys.executable, str(Path(__file__).with_name("fake_app_server.py"))],
            "chatgpt_web_default_model": "chatgpt-web/5.6-sol",
            "task_timeout_seconds": 3,
        }
        self.credentials = credentials("fixture-host")
        self.auth = patch.object(siwc, "get_credentials", return_value=self.credentials)
        self.catalog = patch.object(siwc, "list_models", return_value=ACCOUNT_CATALOG)
        self.auth_mock = self.auth.start()
        self.catalog_mock = self.catalog.start()
        self.addCleanup(self.auth.stop)
        self.addCleanup(self.catalog.stop)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def body(self, **changes):
        value = {
            "project_id": "demo",
            "prompt": "Inspect",
            "scope": "read-only",
            "request_key": "fixture-request",
        }
        value.update(changes)
        return value

    def test_web_acceptance_pins_headless_alias_account_and_backend(self):
        job = create_task(self.config, self.store, self.body(model_version="chatgpt-web/5.6-sol"))
        payload = self.store.get(job["id"])["payload"]
        self.assertEqual(payload["execution_backend"], "chatgpt_web_headless")
        self.assertEqual(payload["dispatch_origin"], "web")
        self.assertEqual(payload["selected_model"]["id"], "chatgpt-web/5.6-sol")
        self.assertEqual(payload["selected_model"]["model"], "gpt-5.6-sol")
        self.assertEqual(payload["siwc_registration"], siwc.registration(self.credentials))

    def test_web_family_alias_uses_configured_default_or_entitlement_fallback(self):
        job = create_task(self.config, self.store, self.body(model_version="chatgpt-web"))
        payload = self.store.get(job["id"])["payload"]
        self.assertEqual(payload["selected_model"]["id"], "chatgpt-web/5.6-sol")
        self.assertEqual(payload["selected_model"]["source"], "configured_default")

        other = dict(self.config, chatgpt_web_default_model=None)
        fallback = create_task(other, self.store, self.body(
            request_key="missing-default", model_version="chatgpt-web"
        ))
        fallback_payload = self.store.get(fallback["id"])["payload"]
        self.assertEqual(fallback_payload["selected_model"]["id"], "chatgpt-web/5.6-sol")
        self.assertEqual(fallback_payload["selected_model"]["source"], "entitlement_fallback")

    def test_free_like_catalog_resolves_family_to_luna(self):
        free_catalog = dict(ACCOUNT_CATALOG)
        free_catalog["models"] = [item for item in ACCOUNT_CATALOG["models"] if item["id"] == "gpt-5.6-luna"]
        self.catalog_mock.return_value = free_catalog
        job = create_task(self.config, self.store, self.body(
            request_key="free-luna", model_version="chatgpt-web"
        ))
        payload = self.store.get(job["id"])["payload"]
        self.assertEqual(payload["selected_model"]["id"], "chatgpt-web/5.6-luna")
        self.assertEqual(payload["selected_model"]["model"], "gpt-5.6-luna")
        self.assertEqual(payload["selected_model"]["source"], "entitlement_fallback")

    def test_web_rejects_non_alias_and_unlisted_model_before_queueing(self):
        with self.assertRaises(Fault) as caught:
            create_task(self.config, self.store, self.body(model_version="gpt-5.6-sol"))
        self.assertEqual(caught.exception.status, 400)
        with self.assertRaises(Fault) as caught:
            create_task(self.config, self.store, self.body(
                model_version="chatgpt-web/not-listed", request_key="not-listed"
            ))
        self.assertEqual(caught.exception.status, 400)
        with self.assertRaises(Fault) as caught:
            create_task(self.config, self.store, self.body(
                model_version="chatgpt-web/6-astra", request_key="blocked-upstream-model"
            ))
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)

    def test_retry_recovers_same_job_before_credentials_or_catalog_changes(self):
        body = self.body()
        job = create_task(self.config, self.store, body)
        self.config["chatgpt_web_default_model"] = "chatgpt-web/changed"
        self.auth_mock.side_effect = AssertionError("retry must not reread credentials")
        self.catalog_mock.side_effect = AssertionError("retry must not reread catalog")
        for state in ("queued", "completed", "unknown"):
            with self.subTest(state=state):
                self.store.update_task(job["id"], state=state)
                self.assertEqual(create_task(self.config, self.store, body)["id"], job["id"])
        with self.assertRaises(Fault) as caught:
            create_task(self.config, self.store, dict(body, prompt="different"))
        self.assertEqual(caught.exception.status, 409)

    def test_concurrent_duplicate_acceptance_creates_one_job_and_one_account_catalog_read(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            jobs = list(pool.map(lambda _: create_task(self.config, self.store, self.body()), range(8)))
        self.assertEqual(len({job["id"] for job in jobs}), 1)
        self.assertEqual(self.auth_mock.call_count, 1)
        self.assertEqual(self.catalog_mock.call_count, 1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)

    def test_mcp_web_origin_exposes_only_packaged_aliases_and_headless_backend(self):
        catalog = _call_tool("codex_x_list_models", {}, self.config, self.store)
        self.assertEqual(catalog["backend"], "chatgpt_web_headless")
        self.assertEqual(catalog["route_prefix"], "chatgpt-web/")
        self.assertEqual([item["id"] for item in catalog["models"]], [
            "chatgpt-web/5.5", "chatgpt-web/5.6-luna", "chatgpt-web/5.6-sol"
        ])
        self.assertEqual([item["model"] for item in catalog["models"]], [
            "gpt-5.5", "gpt-5.6-luna", "gpt-5.6-sol"
        ])
        self.assertNotIn("chatgpt-web/6-astra", catalog["supported_models"])
        self.assertTrue(catalog["models"][2]["is_default"])
        job = _call_tool("codex_x_create_task", self.body(), self.config, self.store)
        payload = self.store.get(job["id"])["payload"]
        self.assertEqual(payload["execution_backend"], "chatgpt_web_headless")
        self.assertEqual(payload["dispatch_origin"], "web")

    def test_future_package_registry_can_add_pro_without_core_routing_change(self):
        registry = Path(self.tmp.name) / "web_models.next.json"
        registry.write_text('''{
          "schema_version": 1,
          "models": [
            {"id": "chatgpt-web/5.5", "slug": "gpt-5.5"},
            {"id": "chatgpt-web/5.6-sol", "slug": "gpt-5.6-sol"},
            {"id": "chatgpt-web/pro", "slug": "gpt-pro-fixture"}
          ]
        }''')
        next_catalog = dict(ACCOUNT_CATALOG)
        next_catalog["models"] = list(ACCOUNT_CATALOG["models"]) + [{
            "id": "gpt-pro-fixture",
            "model": "gpt-pro-fixture",
            "display_name": "GPT Pro Fixture",
            "is_default": False,
            "supported_reasoning_efforts": [],
        }]
        self.catalog_mock.return_value = next_catalog
        with patch.object(service, "WEB_MODEL_REGISTRY_PATH", registry):
            catalog = _call_tool("codex_x_list_models", {}, self.config, self.store)
        self.assertIn("chatgpt-web/pro", [item["id"] for item in catalog["models"]])

    def test_followup_preserves_alias_and_blocks_origin_switch(self):
        parent = create_task(self.config, self.store, self.body())
        self.store.update_task(parent["id"], state="completed", thread_id="fixture-thread")
        body = {"prompt": "Continue", "request_key": "fixture-followup"}
        child = continue_task(self.config, self.store, parent["id"], body)
        payload = self.store.get(child["id"])["payload"]
        self.assertEqual(payload["model_version"], "chatgpt-web/5.6-sol")
        self.assertEqual(payload["selected_model"]["model"], "gpt-5.6-sol")
        self.assertEqual(payload["dispatch_origin"], "web")
        with self.assertRaises(Fault):
            continue_task(dict(self.config, _dispatch_origin="local"), self.store, parent["id"],
                          dict(body, request_key="different-origin"))

    def test_headless_dispatch_completes_with_underlying_model_and_catalog_effort(self):
        job = create_task(self.config, self.store, self.body())
        codex.run_task(self.config, self.store, self.store.claim("task"))
        result = self.store.get(job["id"])
        self.assertEqual(result["state"], "completed")
        selection = result["result"]["model_selection"]
        self.assertEqual(selection["backend"], "chatgpt_web_headless")
        self.assertEqual(selection["dispatch_origin"], "web")
        self.assertEqual(selection["catalog_source"], "responses_api_models")
        self.assertEqual(selection["id"], "chatgpt-web/5.6-sol")
        self.assertEqual(selection["model"], "gpt-5.6-sol")
        self.assertEqual(selection["reasoning_effort"], "high")
        self.assertEqual(selection["observed_model"], "gpt-5.6-sol")
        self.assertEqual(selection["reroutes"], [])
        self.assertTrue(selection["model_identity_verified"])
        self.assertTrue(selection["inference_verified"])

    def test_missing_terminal_model_identity_remains_unknown_without_replay(self):
        job = create_task(self.config, self.store, self.body(scope="workspace-write"))

        class MissingIdentity:
            def __init__(self, *_args, **_kwargs):
                self.notifications = []

            def call(self, method, params):
                if method == "initialize":
                    return {}
                if method == "model/list":
                    return {"data": [{
                        "id": "gpt-5.6-sol",
                        "model": "gpt-5.6-sol",
                        "isDefault": False,
                        "defaultReasoningEffort": "high",
                        "supportedReasoningEfforts": [{"reasoningEffort": "high"}],
                    }], "nextCursor": None}
                if method == "thread/start":
                    return {"thread": {"id": "fixture-thread"}}
                if method == "turn/start":
                    if params.get("model") != "gpt-5.6-sol" or params.get("effort") != "high":
                        raise AssertionError(params)
                    return {"turn": {"id": "fixture-turn"}}
                raise AssertionError(method)

            def send(self, _message):
                pass

            def close(self):
                pass

            def receive(self):
                return {"method": "turn/completed", "params": {
                    "threadId": "fixture-thread",
                    "turn": {"id": "fixture-turn", "status": "completed"},
                }}

        with patch.object(codex, "AppServer", MissingIdentity):
            codex.run_task(self.config, self.store, self.store.claim("task"))
        self.assertEqual(self.store.get(job["id"])["state"], "unknown")
        self.assertEqual(create_task(self.config, self.store, self.body(scope="workspace-write"))["id"], job["id"])

    def test_request_cannot_choose_execution_backend(self):
        with self.assertRaises(Fault) as caught:
            create_task(self.config, self.store, self.body(execution_backend="chatgpt_plan"))
        self.assertEqual(caught.exception.status, 400)


if __name__ == "__main__":
    unittest.main()
