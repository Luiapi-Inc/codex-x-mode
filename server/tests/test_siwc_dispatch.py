"""Transport and dispatch regressions with explicitly fake plan credentials."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from bridge import codex, siwc
from bridge.core import Fault, Store
from bridge.mcp import _call_tool
from bridge.service import create_task, continue_task, list_models
from tests.test_siwc import credentials


CATALOG = {"models": [{"slug": "fixture-model", "display_name": "Fixture Model", "visibility": "list"}]}


class SiwcDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        project = root / "project"; project.mkdir()
        self.store = Store(root / "state.sqlite3")
        self.config = {"_dispatch_origin": "web", "projects": {"demo": {"cwd": str(project.resolve()), "allow_write": True}},
                       "codex_command": [sys.executable, str(Path(__file__).with_name("fake_app_server.py"))],
                       "chatgpt_plan_default_model": "fixture-model", "task_timeout_seconds": 3}
        self.creds = credentials("fixture-host")
        self.auth = patch.object(siwc, "get_credentials", return_value=self.creds)
        self.catalog = patch.object(siwc, "_request_json", return_value=CATALOG)
        self.auth_mock = self.auth.start(); self.catalog_mock = self.catalog.start()
        self.addCleanup(self.auth.stop); self.addCleanup(self.catalog.stop)

    def tearDown(self):
        self.store.close(); self.tmp.cleanup()

    def body(self, **changes):
        value = {"project_id": "demo", "prompt": "Inspect", "scope": "read-only", "request_key": "fixture-request"}
        value.update(changes)
        return value

    def test_web_requires_valid_authorization_and_exact_catalog_model_before_queueing(self):
        self.auth_mock.side_effect = siwc.SiwcError("ChatGPT plan authorization is required")
        with self.assertRaises(Fault) as caught:
            create_task(self.config, self.store, self.body())
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)
        self.auth_mock.side_effect = None
        for config, model in [(dict(self.config, chatgpt_plan_default_model=None), None), (self.config, "missing")]:
            with self.subTest(model=model), self.assertRaises(Fault):
                body = self.body()
                if model is not None: body["model_version"] = model
                create_task(config, self.store, body)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)

    def test_acceptance_pins_backend_account_and_model_without_storing_tokens(self):
        job = create_task(self.config, self.store, self.body(model_version="fixture-model"))
        payload = self.store.get(job["id"])["payload"]
        self.assertEqual(payload["execution_backend"], "chatgpt_plan")
        self.assertEqual(payload["selected_model"]["model"], "fixture-model")
        self.assertEqual(payload["siwc_registration"], siwc.registration(self.creds))
        for token in ("fixture-access", "fixture-refresh", "fixture-id-token"):
            self.assertNotIn(token, json.dumps(payload))
        self.assertEqual(self.catalog_mock.call_args.kwargs["headers"], {"Authorization": "Bearer fixture-access"})

    def test_retry_recovers_same_job_before_credentials_catalog_or_default_changes(self):
        body = self.body()
        job = create_task(self.config, self.store, body)
        self.config["chatgpt_plan_default_model"] = "changed-default"
        self.auth_mock.side_effect = AssertionError("retry must not read credentials")
        self.catalog_mock.side_effect = AssertionError("retry must not read the changed catalog")
        for state in ("queued", "completed", "unknown"):
            with self.subTest(state=state):
                self.store.update_task(job["id"], state=state)
                self.assertEqual(create_task(self.config, self.store, body)["id"], job["id"])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)
        with self.assertRaises(Fault) as caught:
            create_task(self.config, self.store, dict(body, prompt="different"))
        self.assertEqual(caught.exception.status, 409)

    def test_concurrent_duplicate_acceptance_creates_only_one_job(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            jobs = list(pool.map(lambda _: create_task(self.config, self.store, self.body()), range(8)))
        self.assertEqual(len({job["id"] for job in jobs}), 1)
        self.assertEqual(self.catalog_mock.call_count, 1)

    def test_request_cannot_choose_or_change_backend_and_local_does_not_need_siwc(self):
        with self.assertRaises(Fault) as caught:
            create_task(self.config, self.store, self.body(execution_backend="codex_app_server"))
        self.assertEqual(caught.exception.status, 400)
        self.auth_mock.side_effect = AssertionError("local dispatch must not use plan credentials")
        local = dict(self.config, _dispatch_origin="local")
        job = create_task(local, self.store, self.body())
        self.assertEqual(self.store.get(job["id"])["payload"]["execution_backend"], "codex_app_server")
        self.assertEqual(list_models(local)["backend"], "codex_app_server")
        with self.assertRaises(Fault) as caught:
            create_task(self.config, self.store, self.body())
        self.assertEqual(caught.exception.status, 409)

    def test_mcp_web_origin_uses_plan_catalog_and_server_selected_backend(self):
        catalog = _call_tool("codex_x_list_models", {}, self.config, self.store)
        self.assertEqual(catalog["backend"], "chatgpt_plan")
        job = _call_tool("codex_x_create_task", self.body(), self.config, self.store)
        self.assertEqual(self.store.get(job["id"])["payload"]["execution_backend"], "chatgpt_plan")

    def test_followup_preserves_selected_model_and_blocks_account_or_backend_switch(self):
        parent = create_task(self.config, self.store, self.body())
        self.store.update_task(parent["id"], state="completed", thread_id="fixture-thread")
        self.config["chatgpt_plan_default_model"] = "changed"
        body = {"prompt": "Continue", "request_key": "fixture-followup"}
        child = continue_task(self.config, self.store, parent["id"], body)
        payload = self.store.get(child["id"])["payload"]
        self.assertEqual(payload["model_version"], "fixture-model")
        self.auth_mock.side_effect = AssertionError("followup retry must not consult the new account")
        self.assertEqual(continue_task(self.config, self.store, parent["id"], body)["id"], child["id"])
        self.auth_mock.side_effect = None
        self.store.update_task(child["id"], state="completed", thread_id="fixture-thread")
        self.auth_mock.return_value = dict(self.creds, client_id="other-registration")
        with self.assertRaisesRegex(Fault, "account registration"):
            continue_task(self.config, self.store, parent["id"], dict(body, request_key="different-account"))
        with self.assertRaisesRegex(Fault, "execution backend"):
            continue_task(dict(self.config, _dispatch_origin="local"), self.store, parent["id"], dict(body, request_key="different-backend"))

    def test_plan_child_receives_only_env_token_and_fixed_public_provider(self):
        with patch.object(codex, "AppServer") as server, patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-api-key", "ACCESS_TOKEN": "wrong-inherited-token"}):
            codex._app_server(self.config, backend="chatgpt_plan", credentials=self.creds)
        command = server.call_args.args[0]
        env = server.call_args.kwargs["env"]
        self.assertIn('model_provider="openai_chatgpt_plan"', command)
        self.assertIn('model_providers.openai_chatgpt_plan.base_url="https://api.openai.com/v1"', command)
        self.assertIn('model_providers.openai_chatgpt_plan.requires_openai_auth=false', command)
        self.assertIn('model_providers.openai_chatgpt_plan.request_max_retries=0', command)
        self.assertEqual(env["ACCESS_TOKEN"], "fixture-access")
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("fixture-access", json.dumps(command))

    def test_plan_dispatch_completes_with_exact_fixture_model_provenance(self):
        job = create_task(self.config, self.store, self.body())
        codex.run_task(self.config, self.store, self.store.claim("task"))
        result = self.store.get(job["id"])
        self.assertEqual(result["state"], "completed")
        selection = result["result"]["model_selection"]
        self.assertEqual(selection["backend"], "chatgpt_plan")
        self.assertEqual(selection["catalog_source"], "responses_api_models")
        self.assertEqual(selection["observed_model"], "fixture-model")
        self.assertTrue(selection["model_identity_verified"])
        self.assertEqual(result["result"]["observations"][0]["exit_code"], 1)
        self.assertNotIn("fixture-access", json.dumps(result))

    def test_child_credential_echo_is_redacted_before_persistent_or_mcp_result(self):
        job = create_task(self.config, self.store, self.body(prompt="fixture credential leak"))
        codex.run_task(self.config, self.store, self.store.claim("task"))
        row = self.store.get(job["id"])
        self.assertEqual(row["state"], "completed")
        self.assertEqual(row["result"]["answer"], "[REDACTED]")
        result = _call_tool("codex_x_read_task", {"task_id": job["id"]}, self.config, self.store)
        self.assertNotIn("fixture-access", json.dumps(result))

    def test_unbound_legacy_http_task_is_rejected_before_app_server_launch(self):
        job = self.store.create("task", {"project_id": "demo", "prompt": "Legacy", "scope": "read-only"}, "legacy")
        with patch.object(codex, "AppServer") as server:
            codex.run_task(self.config, self.store, self.store.claim("task"))
            server.assert_not_called()
        self.assertEqual(self.store.get(job["id"])["state"], "failed")

    def test_queued_account_switch_or_catalog_removal_fails_before_child_launch(self):
        for cause in ("account", "catalog"):
            with self.subTest(cause=cause):
                self.auth_mock.return_value = self.creds; self.catalog_mock.return_value = CATALOG
                job = create_task(self.config, self.store, self.body(request_key=cause))
                if cause == "account": self.auth_mock.return_value = dict(self.creds, subject="other-subject")
                else: self.catalog_mock.return_value = {"models": []}
                with patch.object(codex, "AppServer") as server:
                    codex.run_task(self.config, self.store, self.store.claim("task"))
                    server.assert_not_called()
                result = self.store.get(job["id"])
                self.assertEqual(result["state"], "failed")
                self.assertFalse(result["result"]["execution_may_have_started"])

    def test_plan_missing_terminal_model_identity_is_unknown_without_replay(self):
        job = create_task(self.config, self.store, self.body(scope="workspace-write"))
        class MissingIdentity:
            def __init__(self, *_args, **_kwargs): self.notifications = []
            def call(self, method, params):
                if method == "initialize": return {}
                if method == "thread/start":
                    self.model = params["model"]
                    return {"thread": {"id": "fixture-thread"}}
                if method == "turn/start": return {"turn": {"id": "fixture-turn"}}
                raise AssertionError("plan path must not use bundled model/list")
            def send(self, _message): pass
            def close(self): pass
            def receive(self):
                return {"method": "turn/completed", "params": {"threadId": "fixture-thread", "turn": {"id": "fixture-turn", "status": "completed"}}}
        with patch.object(codex, "AppServer", MissingIdentity):
            codex.run_task(self.config, self.store, self.store.claim("task"))
        self.assertEqual(self.store.get(job["id"])["state"], "unknown")
        self.assertEqual(create_task(self.config, self.store, self.body(scope="workspace-write"))["id"], job["id"])
        with self.assertRaises(Fault) as caught:
            create_task(self.config, self.store, self.body(scope="workspace-write", request_key="conflicting-writer"))
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(create_task(self.config, self.store, self.body(request_key="reader"))["state"], "queued")
