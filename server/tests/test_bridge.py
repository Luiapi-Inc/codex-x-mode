import concurrent.futures
import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.codex import AppServer, list_models, run_task
from bridge.core import Fault, Store, build_output, output_events, prepare_response
from bridge.http import Server
from bridge.mcp import MODERN_VERSION, rpc_response
from bridge.schema import schema
from bridge.service import continue_task, create_task, status


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "state.sqlite3")
        self.payload = prepare_response(self.store, {"model": "fixture-model", "input": "Inspect source",
            "tools": [{"type": "function", "name": "read_file", "parameters": {"type": "object"}}]})

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_idempotency_and_conflict(self):
        first = self.store.create("backend", self.payload, "one")
        self.assertEqual(first["id"], self.store.create("backend", self.payload, "one")["id"])
        with self.assertRaises(Fault) as caught:
            self.store.create("backend", dict(self.payload, model="different"), "one")
        self.assertEqual(caught.exception.status, 409)

    def test_concurrent_claim_single_winner(self):
        self.store.create("backend", self.payload, "one")
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            jobs = list(pool.map(lambda index: self.store.claim("backend", f"claim-{index}"), range(12)))
        self.assertEqual(sum(job is not None for job in jobs), 1)

    def test_claim_retry_recovers_same_lease(self):
        self.store.create("backend", self.payload, "one")
        first = self.store.claim("backend", "claim-stable")
        retry = self.store.claim("backend", "claim-stable")
        self.assertEqual(first["id"], retry["id"])
        self.assertEqual(first["lease"], retry["lease"])
        self.store.finish_backend(first["id"], {"lease": first["lease"], "request_key": "done", "answer": "ok"})
        with self.assertRaises(Fault) as caught:
            self.store.claim("backend", "claim-stable")
        self.assertEqual(caught.exception.status, 409)

    def test_concurrent_completion_idempotency(self):
        self.store.create("backend", self.payload, "one")
        job = self.store.claim("backend")
        body = {"lease": job["lease"], "request_key": "completion", "answer": "Reviewed"}
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.store.finish_backend(job["id"], body), range(8)))
        self.assertEqual(len({json.dumps(result["result"], sort_keys=True) for result in results}), 1)
        with self.assertRaises(Fault):
            self.store.finish_backend(job["id"], dict(body, answer="changed"))

    def test_bad_lease_and_expired_turn(self):
        self.store.create("backend", self.payload, "one", ttl=0.02)
        job = self.store.claim("backend")
        with self.assertRaises(Fault) as caught:
            self.store.finish_backend(job["id"], {"lease": "wrong", "request_key": "done", "answer": "x"})
        self.assertEqual(caught.exception.status, 403)
        time.sleep(0.03)
        with self.assertRaises(Fault) as caught:
            self.store.finish_backend(job["id"], {"lease": job["lease"], "request_key": "done", "answer": "x"})
        self.assertEqual(caught.exception.status, 409)

    def test_offered_function_calls_and_continuation(self):
        self.store.create("backend", self.payload, "one")
        job = self.store.claim("backend")
        finished = self.store.finish_backend(job["id"], {"lease": job["lease"], "request_key": "done",
            "calls": [{"name": "read_file", "input": '{"path":"src/a.ts"}'}]})
        output = finished["result"]["output"][0]
        continued = prepare_response(self.store, {"model": "fixture-model", "previous_response_id": job["id"],
            "input": [{"type": "function_call_output", "call_id": output["call_id"], "output": "source"}]})
        self.assertEqual(continued["input"][1]["call_id"], continued["input"][2]["call_id"])
        events = list(output_events(finished["result"]))
        self.assertEqual(events[-1][0], "response.completed")
        self.assertIn("response.function_call_arguments.done", [name for name, _ in events])

    def test_custom_tool_and_restrictions(self):
        payload = dict(self.payload, tools=[{"type": "custom", "name": "patch"}])
        item = build_output(payload, {"calls": [{"name": "patch", "input": "raw patch"}]})[0]
        self.assertEqual(item["type"], "custom_tool_call")
        self.assertEqual(item["input"], "raw patch")
        for body in [{"calls": [{"name": "unknown", "input": "{}"}]},
                     {"calls": [{"name": "read_file", "input": "not-json"}]},
                     {"calls": [{"name": [], "input": "{}"}]}]:
            with self.assertRaises(Fault):
                build_output(self.payload, body)
        with self.assertRaises(Fault):
            build_output(dict(self.payload, tool_choice="none"), {"calls": [{"name": "read_file", "input": "{}"}]})
        with self.assertRaises(Fault):
            build_output(dict(self.payload, tool_choice="required"), {"answer": "done"})

    def test_reject_unsupported_features(self):
        for extra in [{"tools": [{"type": "web_search"}]}, {"text": []},
                      {"text": {"format": {"type": "json_schema"}}}, {"background": True}]:
            with self.assertRaises(Fault):
                prepare_response(self.store, dict(self.payload, **extra))

    def test_mcp_legacy_stdio_handshake_and_modern_discovery(self):
        config = {"projects": {}}
        state = {}
        initialized = rpc_response({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "legacy", "version": "1"}
        }}, config, self.store, state)
        self.assertEqual(initialized["result"]["protocolVersion"], "2025-11-25")
        listed = rpc_response({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, config, self.store, state)
        self.assertIn("tools", listed["result"])
        self.assertNotIn("resultType", listed["result"])

        meta = {
            "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
            "io.modelcontextprotocol/clientCapabilities": {},
        }
        discovered = rpc_response({"jsonrpc": "2.0", "id": 3, "method": "server/discover", "params": {"_meta": meta}}, config, self.store)
        self.assertEqual(discovered["result"]["resultType"], "complete")
        self.assertIn(MODERN_VERSION, discovered["result"]["supportedVersions"])
        self.assertNotIn("serverInfo", discovered["result"])
        self.assertIn("io.modelcontextprotocol/serverInfo", discovered["result"]["_meta"])

    def test_restart_unknown_execution(self):
        self.store.create("task", {"project_id": "demo", "resource_id": "/workspace/demo",
            "prompt": "fixture", "scope": "workspace-write"}, "task-one")
        job = self.store.claim("task")
        self.store.close()
        self.store = Store(Path(self.tmp.name) / "state.sqlite3")
        self.assertEqual(self.store.get(job["id"])["state"], "unknown")
        self.assertEqual(self.store.unknown_projects(), ["demo"])
        self.assertEqual(self.store.unknown_tasks(), [{
            "id": job["id"], "project_id": "demo", "scope": "workspace-write", "write_conflict": True,
        }])
        # Read-only execution and reads remain available during recovery.
        safe_read = self.store.create("task", {"project_id": "demo", "resource_id": "/workspace/demo",
            "prompt": "read", "scope": "read-only"}, "task-read")
        self.assertEqual(safe_read["state"], "queued")
        with self.assertRaises(Fault) as caught:
            self.store.create("task", {"project_id": "demo", "resource_id": "/workspace/demo",
                "prompt": "write", "scope": "workspace-write"}, "task-two")
        self.assertEqual(caught.exception.status, 409)
        # A different canonical resource is not blocked.
        other = self.store.create("task", {"project_id": "other", "resource_id": "/workspace/other",
            "prompt": "write", "scope": "workspace-write"}, "task-three")
        self.assertEqual(other["state"], "queued")

    def test_unknown_read_only_execution_does_not_block_writes(self):
        task = self.store.create("task", {"project_id": "demo", "resource_id": "/workspace/demo",
            "prompt": "fixture", "scope": "read-only"}, "readonly-one")
        self.store.claim("task")
        self.store.update_task(task["id"], state="unknown")
        self.assertEqual(self.store.unknown_projects(), [])
        writer = self.store.create("task", {"project_id": "demo", "resource_id": "/workspace/demo",
            "prompt": "write", "scope": "workspace-write"}, "writer-two")
        self.assertEqual(writer["state"], "queued")

    def test_cancel_is_idempotent_and_unknown_is_fail_closed(self):
        self.store.create("backend", self.payload, "one")
        turn = self.store.claim("backend", "claim")
        first = self.store.cancel_backend(turn["id"], turn["lease"], "cancel")
        retry = self.store.cancel_backend(turn["id"], turn["lease"], "cancel")
        self.assertEqual(first["state"], "cancelled")
        self.assertEqual(retry["state"], "cancelled")

        queued = self.store.create("task", {"project_id": "demo", "prompt": "queued", "scope": "read-only"}, "task-q")
        self.assertEqual(self.store.cancel_task(queued["id"], "cancel-q")["state"], "cancelled")

        running = self.store.create("task", {"project_id": "demo", "prompt": "running", "scope": "read-only"}, "task-r")
        running = self.store.claim("task")
        self.assertEqual(self.store.cancel_task(running["id"], "cancel-r")["state"], "cancelling")
        self.assertTrue(self.store.cancel_requested(running["id"]))


class TaskResourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        shared = root / "shared"
        other = root / "other"
        shared.mkdir()
        other.mkdir()
        self.config = {"projects": {
            "demo": {"cwd": str(shared.resolve()), "allow_write": True},
            "alias": {"cwd": str(shared.resolve()), "allow_write": True},
            "other": {"cwd": str(other.resolve()), "allow_write": True},
        }}
        self.store = Store(root / "state.sqlite3")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def create(self, project, scope, request_key):
        return create_task(self.config, self.store, {
            "project_id": project, "prompt": "Check the task", "scope": scope, "request_key": request_key,
        })

    def test_model_selection_is_part_of_task_identity_and_inherited(self):
        parent = create_task(self.config, self.store, {
            "project_id": "demo", "prompt": "Check", "scope": "read-only",
            "request_key": "model-parent", "model_version": "gpt-codex-test",
        })
        parent_row = self.store.get(parent["id"], "task")
        self.assertEqual(parent_row["payload"]["model_version"], "gpt-codex-test")
        self.store.update_task(parent["id"], state="completed", thread_id="thread-model")
        child = continue_task(self.config, self.store, parent["id"], {
            "prompt": "Continue", "request_key": "model-child",
        })
        self.assertEqual(self.store.get(child["id"], "task")["payload"]["model_version"], "gpt-codex-test")
        self.store.update_task(child["id"], state="completed", thread_id="thread-model")
        override = continue_task(self.config, self.store, parent["id"], {
            "prompt": "Use another model", "request_key": "model-override", "model_version": "gpt-codex-next",
        })
        self.assertEqual(self.store.get(override["id"], "task")["payload"]["model_version"], "gpt-codex-next")

    def test_unknown_writer_blocks_alias_but_allows_read_only_and_separate_root(self):
        writer = self.create("demo", "workspace-write", "writer")
        self.store.update_task(writer["id"], state="unknown")

        reader = self.create("alias", "read-only", "reader")
        self.assertEqual(reader["state"], "queued")
        with self.assertRaises(Fault) as caught:
            self.create("alias", "workspace-write", "conflicting-writer")
        self.assertEqual(caught.exception.status, 409)

        independent = self.create("other", "workspace-write", "independent-writer")
        self.assertEqual(independent["state"], "queued")

    def test_app_writer_claim_blocks_core_and_survives_restart_until_terminal_evidence(self):
        root = self.config["projects"]["demo"]["cwd"]
        claim = self.store.acquire_app_writer(
            resource_id=root, project_id="demo", resource_project_ids=["demo", "alias"],
            thread_id="app-thread",
        )
        self.assertEqual(claim["state"], "running")

        with self.assertRaises(Fault) as caught:
            self.create("alias", "workspace-write", "blocked-by-app")
        self.assertEqual(caught.exception.status, 409)
        reader = self.create("alias", "read-only", "read-during-app")
        self.assertEqual(reader["state"], "queued")

        self.store.close()
        self.store = Store(Path(self.tmp.name) / "state.sqlite3")
        recovered = self.store.app_writer_claim_for_thread("app-thread")
        self.assertEqual(recovered["state"], "unknown")
        self.assertEqual(self.store.unknown_projects(), ["demo"])
        with self.assertRaises(Fault):
            self.create("alias", "workspace-write", "still-blocked")

        self.store.set_app_writer_turn(claim["id"], "app-thread", "app-turn")
        self.store.finish_app_writer(
            claim["id"], "completed",
            {"thread_id": "app-thread", "turn_id": "app-turn", "terminal_status": "completed"},
        )
        released = self.create("alias", "workspace-write", "released-after-proof")
        self.assertEqual(released["state"], "queued")

    def test_app_claim_and_core_claim_are_serialized_across_shared_resource(self):
        queued = self.create("demo", "workspace-write", "queued-core-writer")
        claim = self.store.acquire_app_writer(
            resource_id=self.config["projects"]["demo"]["cwd"], project_id="demo",
            resource_project_ids=["demo", "alias"], thread_id="app-thread",
        )
        self.assertIsNone(self.store.claim("task"))
        self.store.set_app_writer_turn(claim["id"], "app-thread", "app-turn")
        self.store.finish_app_writer(
            claim["id"], "failed",
            {"thread_id": "app-thread", "turn_id": "app-turn", "terminal_status": "failed"},
        )
        running = self.store.claim("task")
        self.assertEqual(running["id"], queued["id"])

    def test_app_claim_refuses_a_running_or_unknown_core_writer(self):
        root = self.config["projects"]["demo"]["cwd"]
        job = self.create("demo", "workspace-write", "running-core-writer")
        running = self.store.claim("task")
        self.assertEqual(running["id"], job["id"])
        with self.assertRaises(Fault) as caught:
            self.store.acquire_app_writer(
                resource_id=root, project_id="demo", resource_project_ids=["demo", "alias"],
                thread_id="app-thread",
            )
        self.assertEqual(caught.exception.status, 409)

    def test_core_worker_never_runs_two_queued_writers_for_the_same_resource(self):
        first = self.create("demo", "workspace-write", "first-queued-writer")
        second = self.create("alias", "workspace-write", "second-queued-writer")
        running = self.store.claim("task")
        self.assertEqual(running["id"], first["id"])
        self.assertIsNone(self.store.claim("task"))
        self.store.update_task(first["id"], state="completed", result={"terminal": True})
        next_writer = self.store.claim("task")
        self.assertEqual(next_writer["id"], second["id"])

    def test_app_writer_claim_is_unique_across_sqlite_connections(self):
        second = Store(Path(self.tmp.name) / "state.sqlite3")
        try:
            def acquire(store, thread_id):
                try:
                    return store.acquire_app_writer(
                        resource_id=self.config["projects"]["demo"]["cwd"],
                        project_id="demo", resource_project_ids=["demo", "alias"],
                        thread_id=thread_id,
                    )
                except Fault:
                    return None

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda args: acquire(*args), ((self.store, "one"), (second, "two"))))
            self.assertEqual(sum(result is not None for result in results), 1)
            winner = next(result for result in results if result is not None)
            self.assertIn(winner["thread_id"], ("one", "two"))
        finally:
            second.close()

    def test_legacy_unknown_writer_uses_project_alias_mapping(self):
        legacy = self.store.create("task", {
            "project_id": "demo", "prompt": "Legacy", "scope": "workspace-write",
        }, "legacy-writer")
        self.store.update_task(legacy["id"], state="unknown")
        with self.assertRaises(Fault) as caught:
            self.create("alias", "workspace-write", "alias-writer")
        self.assertEqual(caught.exception.status, 409)

    def test_status_separates_uncertainty_from_write_blockers(self):
        reader = self.create("demo", "read-only", "reader")
        self.store.update_task(reader["id"], state="unknown")
        current = status(self.config, self.store)
        self.assertEqual(current["unknown_projects"], [])
        self.assertEqual(current["unknown_tasks"][0]["scope"], "read-only")
        self.assertFalse(current["unknown_tasks"][0]["write_conflict"])
        self.assertEqual(current["readiness"]["web_executor"], "implemented_unverified")

    def test_unknown_app_writer_is_visible_in_status(self):
        root = self.config["projects"]["demo"]["cwd"]
        claim = self.store.acquire_app_writer(
            resource_id=root, project_id="demo", resource_project_ids=["demo", "alias"],
            thread_id="unknown-app-thread",
        )
        self.store.set_app_writer_turn(claim["id"], "unknown-app-thread", "unknown-app-turn")
        self.store.mark_app_writer_unknown(claim["id"], {"reason": "test ambiguity"})
        current = status(self.config, self.store)
        self.assertEqual(current["unknown_projects"], ["demo"])
        self.assertEqual(current["unknown_app_writers"][0]["thread_id"], "unknown-app-thread")


class DispatchDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.project = root / "project"
        self.project.mkdir()
        self.store = Store(root / "state.sqlite3")
        self.config = {
            "projects": {"demo": {"cwd": str(self.project.resolve()), "allow_write": False}},
            "codex_command": ["fixture-codex"],
            "task_timeout_seconds": 1,
        }

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def new_task(self, key):
        return self.store.create("task", {
            "project_id": "demo", "prompt": "Inspect", "scope": "read-only",
        }, key)

    def test_model_catalog_reads_only_supported_account_models(self):
        self.config["codex_command"] = [sys.executable, str(Path(__file__).with_name("fake_app_server.py"))]
        models = list_models(self.config)["models"]
        self.assertEqual(models, [
            {
                "id": "fixture-model", "model": "fixture-model", "display_name": "Fixture Model",
                "is_default": True, "default_reasoning_effort": "medium",
                "supported_reasoning_efforts": ["medium"],
            },
            {
                "id": "chatgpt-web/gpt-5.6-sol", "model": "chatgpt-web/gpt-5.6-sol",
                "display_name": "Fixture ChatGPT Web Model", "is_default": False,
                "default_reasoning_effort": "high",
                "supported_reasoning_efforts": ["medium", "high"],
            },
        ])

    def test_model_catalog_labels_backend_and_does_not_claim_entitlement(self):
        self.config["codex_command"] = [sys.executable, str(Path(__file__).with_name("fake_app_server.py"))]
        catalog = list_models(self.config)
        self.assertEqual(catalog["backend"], "codex_app_server")
        self.assertEqual(catalog["catalog_source"], "model/list")
        self.assertTrue(catalog["catalog_integrity_verified"])
        self.assertFalse(catalog["model_entitlement_verified"])

    def test_unsupported_model_fails_before_turn_start(self):
        class UnsupportedModelServer:
            def __init__(self, *_args, **_kwargs):
                self.methods = []

            def call(self, method, _params):
                self.methods.append(method)
                if method == "initialize":
                    return {}
                if method == "model/list":
                    return {"data": [{"id": "available", "model": "available", "isDefault": True}], "nextCursor": None}
                raise AssertionError("turn must not start when the requested model is unavailable")

            def send(self, _message):
                pass

            def close(self):
                pass

        job = self.store.create("task", {
            "project_id": "demo", "prompt": "Inspect", "scope": "read-only", "model_version": "missing",
        }, "unsupported-model")
        with patch("bridge.codex.AppServer", UnsupportedModelServer):
            run_task(self.config, self.store, job)
        result = self.store.get(job["id"], "task")
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["result"]["failure_phase"], "model_catalog")
        self.assertFalse(result["result"]["execution_may_have_started"])

    def test_turn_start_disconnect_is_unknown_with_phase_and_no_raw_error(self):
        class FailingAppServer:
            def __init__(self, *_args, **_kwargs):
                pass

            def call(self, method, _params):
                if method == "initialize":
                    return {}
                if method == "model/list":
                    return {"data": [{"id": "fixture-model", "model": "fixture-model", "isDefault": True}], "nextCursor": None}
                if method == "thread/start":
                    return {"thread": {"id": "thread-fixture"}}
                if method == "turn/start":
                    raise RuntimeError("token=secret /private/path")
                raise AssertionError(method)

            def send(self, _message):
                pass

            def close(self):
                pass

        job = self.new_task("turn-start-failure")
        with patch("bridge.codex.AppServer", FailingAppServer):
            run_task(self.config, self.store, job)

        result = self.store.get(job["id"], "task")
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["result"]["failure_phase"], "turn_start")
        self.assertEqual(result["result"]["error_type"], "RuntimeError")
        self.assertTrue(result["result"]["execution_may_have_started"])
        self.assertNotIn("secret", str(result["result"]))
        self.assertNotIn(str(self.project), str(result["result"]))

    def test_pre_turn_failure_is_terminal_failed_with_phase(self):
        class FailingAppServer:
            def __init__(self, *_args, **_kwargs):
                pass

            def call(self, method, _params):
                if method == "initialize":
                    raise ValueError("private detail")
                raise AssertionError(method)

            def close(self):
                pass

        job = self.new_task("initialize-failure")
        with patch("bridge.codex.AppServer", FailingAppServer):
            run_task(self.config, self.store, job)

        result = self.store.get(job["id"], "task")
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["result"]["failure_phase"], "initialize")
        self.assertEqual(result["result"]["error_type"], "ValueError")
        self.assertFalse(result["result"]["execution_may_have_started"])

    def test_model_reroute_keeps_execution_unknown(self):
        class ReroutingAppServer:
            def __init__(self, *_args, **_kwargs):
                self.notifications = []

            def call(self, method, _params):
                if method == "initialize":
                    return {}
                if method == "model/list":
                    return {"data": [{"id": "fixture-model", "model": "fixture-model", "isDefault": True}], "nextCursor": None}
                if method == "thread/start":
                    return {"thread": {"id": "thread-fixture"}}
                if method == "turn/start":
                    self.notifications.extend([
                        {"method": "model/rerouted", "params": {"threadId": "thread-fixture", "turnId": "turn-fixture",
                                                                      "fromModel": "fixture-model", "toModel": "other-model"}},
                        {"method": "turn/completed", "params": {"threadId": "thread-fixture", "turn": {
                            "id": "turn-fixture", "status": "completed", "model": "other-model"}}},
                    ])
                    return {"turn": {"id": "turn-fixture", "status": "inProgress"}}
                raise AssertionError(method)

            def send(self, _message):
                pass

            def receive(self):
                return self.notifications.pop(0)

            def close(self):
                pass

        job = self.new_task("model-reroute")
        with patch("bridge.codex.AppServer", ReroutingAppServer):
            run_task(self.config, self.store, job)
        result = self.store.get(job["id"], "task")
        self.assertEqual(result["state"], "unknown")
        self.assertFalse(result["result"]["model_selection"]["model_identity_verified"])
        self.assertFalse(result["result"]["model_selection"]["inference_verified"])
        self.assertEqual(result["result"]["model_selection"]["reroutes"][0]["to_model"], "other-model")

    def test_missing_observed_model_fails_closed(self):
        class UnverifiedModelServer:
            def __init__(self, *_args, **_kwargs):
                self.notifications = []

            def call(self, method, _params):
                if method == "initialize":
                    return {}
                if method == "model/list":
                    return {"data": [{"id": "fixture-model", "model": "fixture-model", "isDefault": True}], "nextCursor": None}
                if method == "thread/start":
                    return {"thread": {"id": "thread-fixture"}}
                if method == "turn/start":
                    self.notifications.append({"method": "turn/completed", "params": {
                        "threadId": "thread-fixture", "turn": {"id": "turn-fixture", "status": "completed"},
                    }})
                    return {"turn": {"id": "turn-fixture", "status": "inProgress"}}
                raise AssertionError(method)

            def send(self, _message):
                pass

            def receive(self):
                return self.notifications.pop(0)

            def close(self):
                pass

        job = self.new_task("model-not-observed")
        with patch("bridge.codex.AppServer", UnverifiedModelServer):
            run_task(self.config, self.store, job)
        result = self.store.get(job["id"], "task")
        self.assertEqual(result["state"], "unknown")
        selection = result["result"]["model_selection"]
        self.assertIsNone(selection["observed_model"])
        self.assertFalse(selection["model_identity_verified"])
        self.assertFalse(selection["inference_verified"])

    def test_status_does_not_claim_plan_authorization_or_live_inference(self):
        value = status(self.config, self.store)
        self.assertEqual(value["readiness"]["web_executor"], "implemented_unverified")
        self.assertEqual(value["execution_backends"]["local_stdio"], "codex_app_server")
        self.assertEqual(value["execution_backends"]["web_http"], "codex_app_server")
        self.assertEqual(value["web_model_family"], "chatgpt-web")
        self.assertEqual(value["chatgpt_web_authorization"]["state"], "native_codex_owned")
        self.assertFalse(value["live_codex_verified"])


class SchemaTests(unittest.TestCase):
    def test_schema_has_auth_and_consequential_mutations(self):
        value = schema("https://bridge.example.invalid")
        self.assertEqual(value["security"], [{"bearerAuth": []}])
        self.assertTrue(value["paths"]["/tasks"]["post"]["x-openai-isConsequential"])
        self.assertTrue(value["paths"]["/tasks/{id}/cancel"]["post"]["x-openai-isConsequential"])
        self.assertIn("/status", value["paths"])
        self.assertIn("/models", value["paths"])
        properties = value["paths"]["/tasks"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]
        self.assertIn("model_version", properties)
        model_contract = properties["model_version"]["description"]
        self.assertIn("chatgpt-web/<version>", model_contract)
        self.assertIn("Native Codex", model_contract)
        self.assertNotIn("ChatGPT-plan", model_contract)
        self.assertNotIn("chatgpt_plan_default_model", model_contract)
        with self.assertRaises(ValueError):
            schema("http://localhost")


class AppServerDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        project = root / "project"
        project.mkdir()
        self.store = Store(root / "state.sqlite3")
        self.config = {
            "projects": {"demo": {"cwd": str(project.resolve()), "allow_write": False}},
            "codex_command": ["unavailable-fixture"],
            "task_timeout_seconds": 3,
        }

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _fake_managed_app_server(self, cleanup, *, wait_status):
        class Process:
            pid = 4321

            def __init__(self):
                self.returncode = None
                self.stdin = tempfile.TemporaryFile(mode="w+")
                self.stdout = tempfile.TemporaryFile(mode="w+")

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                self.returncode = 0
                return 0

        class Reader:
            def join(self, timeout=None):
                pass

        app = AppServer.__new__(AppServer)
        app.process = Process()
        app.reader = Reader()
        app.cleanup = cleanup
        app._wait_process_group_stopped = lambda timeout=2: wait_status
        return app

    def test_app_server_auth_home_cleanup_requires_verified_process_group_stop(self):
        home = tempfile.TemporaryDirectory()
        path = Path(home.name)
        (path / "auth.json").write_text("opaque", encoding="utf-8")
        app = self._fake_managed_app_server(home.cleanup, wait_status=True)
        with patch("bridge.codex.os.killpg"):
            self.assertTrue(app.close())
        self.assertFalse(path.exists())

    def test_app_server_does_not_signal_process_group_after_leader_has_exited(self):
        app = self._fake_managed_app_server(None, wait_status=True)
        app.process.returncode = 0
        with patch("bridge.codex.os.killpg") as killpg:
            self.assertTrue(app.close())
        killpg.assert_not_called()

    def test_app_server_keeps_auth_home_when_exited_leader_has_unverified_group(self):
        home = tempfile.TemporaryDirectory()
        path = Path(home.name)
        (path / "auth.json").write_text("opaque", encoding="utf-8")
        app = self._fake_managed_app_server(home.cleanup, wait_status=False)
        app.process.returncode = 0
        with patch("bridge.codex.os.killpg") as killpg:
            self.assertFalse(app.close())
        killpg.assert_not_called()
        del app
        del home
        self.assertTrue((path / "auth.json").is_file())
        (path / "auth.json").unlink()
        path.rmdir()

    def test_app_server_retains_auth_home_if_process_group_stop_is_unknown(self):
        home = tempfile.TemporaryDirectory()
        path = Path(home.name)
        (path / "auth.json").write_text("opaque", encoding="utf-8")
        app = self._fake_managed_app_server(home.cleanup, wait_status=False)
        with patch("bridge.codex.os.killpg"):
            self.assertFalse(app.close())
        del app
        del home
        self.assertTrue((path / "auth.json").is_file())
        (path / "auth.json").unlink()
        path.rmdir()

    def test_missing_codex_fails_without_success_claim(self):
        job = self.store.create("task", {"project_id": "demo", "prompt": "x", "scope": "read-only"}, "one")
        run_task(self.config, self.store, self.store.claim("task"))
        self.assertEqual(self.store.get(job["id"])["state"], "failed")

    def test_app_server_fixture_read_only_and_followup(self):
        self.config["codex_command"] = [sys.executable, str(Path(__file__).with_name("fake_app_server.py"))]
        self.store.create("task", {"project_id": "demo", "prompt": "fixture review", "scope": "read-only"}, "one")
        job = self.store.claim("task")
        run_task(self.config, self.store, job)
        first = self.store.get(job["id"])
        self.assertEqual(first["state"], "completed")
        self.assertEqual(first["result"]["answer"], "Fixture answer")
        self.assertEqual(first["result"]["model_selection"]["model"], "fixture-model")
        self.assertTrue(first["result"]["model_selection"]["model_identity_verified"])
        self.assertTrue(first["result"]["model_selection"]["inference_verified"])
        self.assertEqual(first["result"]["observations"][0]["exit_code"], 1)
        self.store.create("task", {"project_id": "demo", "prompt": "fixture followup", "scope": "read-only",
                                  "parent_task_id": first["id"]}, "two")
        child = self.store.claim("task")
        run_task(self.config, self.store, child)
        self.assertEqual(self.store.get(child["id"])["thread_id"], first["thread_id"])
        self.assertEqual(self.store.get(child["id"])["state"], "completed")

    def test_running_task_cancel_terminates_owned_app_server(self):
        self.config["codex_command"] = [sys.executable, str(Path(__file__).with_name("fake_slow_app_server.py"))]
        self.config["task_timeout_seconds"] = 10
        self.config["projects"]["demo"]["allow_write"] = True
        self.store.create("task", {"project_id": "demo", "prompt": "slow fixture", "scope": "workspace-write"}, "slow-one")
        job = self.store.claim("task")
        thread = threading.Thread(target=run_task, args=(self.config, self.store, job), daemon=True)
        thread.start()
        deadline = time.monotonic() + 3
        while self.store.get(job["id"])["turn_id"] is None and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertIsNotNone(self.store.get(job["id"])["turn_id"])
        self.assertEqual(self.store.cancel_task(job["id"], "cancel-running")["state"], "cancelling")
        thread.join(timeout=4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.store.get(job["id"])["state"], "unknown")
        self.assertIn("demo", self.store.unknown_projects())

    def test_app_server_fixture_explicit_write_scope(self):
        self.config["codex_command"] = [sys.executable, str(Path(__file__).with_name("fake_app_server.py"))]
        self.config["projects"]["demo"]["allow_write"] = True
        job = self.store.create("task", {"project_id": "demo", "prompt": "fixture edit", "scope": "workspace-write"}, "one")
        run_task(self.config, self.store, self.store.claim("task"))
        self.assertEqual(self.store.get(job["id"])["state"], "completed")


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "state.sqlite3")
        root = Path(self.tmp.name) / "project"
        root.mkdir()
        (root / "hello.txt").write_text("hello MCP\n")
        (Path(self.tmp.name) / "secret.txt").write_text("outside")
        (root / "leak.txt").symlink_to(Path(self.tmp.name) / "secret.txt")
        self.config = {"gpt_key": "g" * 40, "provider_key": "p" * 40, "mcp_key": "m" * 40,
                       "projects": {"demo": {"cwd": str(root.resolve()), "allow_write": False}},
                       "codex_command": [sys.executable, str(Path(__file__).with_name("fake_app_server.py"))],
                       "chatgpt_web_default_model": "chatgpt-web/gpt-5.6-sol",
                       "backend_timeout_seconds": 2}
        self.server = Server(("127.0.0.1", 0), self.config, self.store, start_worker=False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.store.close()
        self.tmp.cleanup()

    def request(self, method, path, body=None, role="gpt", extra_headers=None):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Authorization": "Bearer " + self.config[role + "_key"], "Content-Type": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        client.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = client.getresponse()
        data = response.read().decode()
        status = response.status
        client.close()
        return status, json.loads(data) if not data.startswith("event:") else data

    def mcp_headers(self, method, name=None):
        headers = {"MCP-Protocol-Version": MODERN_VERSION, "Mcp-Method": method}
        if name is not None:
            headers["Mcp-Name"] = name
        return headers

    def test_role_separation_and_project_scope(self):
        self.assertEqual(self.request("GET", "/projects", role="provider")[0], 401)
        self.assertEqual(self.request("POST", "/v1/responses", {"model": "x", "input": "x"})[0], 401)
        body = {"project_id": "unlisted", "prompt": "review", "scope": "read-only", "request_key": "one"}
        self.assertEqual(self.request("POST", "/tasks", body)[0], 403)
        self.assertEqual(self.request("POST", "/tasks", dict(body, project_id="demo", scope="workspace-write"))[0], 403)

    def test_unadvertised_oauth_discovery_returns_404_before_gpt_auth(self):
        for path in ("/mcp", "/.well-known/oauth-protected-resource/mcp"):
            status, body = self.request("GET", path, role="mcp")
            self.assertEqual(status, 404)
            self.assertIn("not advertised", body["error"]["message"])

    def test_task_acceptance_dedup_and_status(self):
        body = {"project_id": "demo", "prompt": "review", "scope": "read-only", "request_key": "one"}
        status, job = self.request("POST", "/tasks", body)
        self.assertEqual(status, 202)
        self.assertEqual(job["state"], "queued")
        self.assertEqual(job["id"], self.request("POST", "/tasks", body)[1]["id"])
        self.assertEqual(self.request("GET", "/tasks/" + job["id"])[1]["state"], "queued")
        self.assertEqual(self.request("POST", "/tasks/" + job["id"] + "/followups", {"prompt": "more", "request_key": "two"})[0], 409)

    def test_web_catalog_unavailable_blocks_dispatch_but_preserves_project_reads(self):
        body = {"project_id": "demo", "prompt": "review", "scope": "read-only", "request_key": "no-catalog"}
        with patch("bridge.service.codex_list_models", side_effect=OSError("fixture catalog unavailable")):
            self.assertEqual(self.request("GET", "/models")[0], 503)
            self.assertEqual(self.request("POST", "/tasks", body)[0], 503)
            self.assertEqual(self.request("GET", "/projects/demo/file?path=hello.txt")[0], 200)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)
        self.assertEqual(self.request("POST", "/tasks", dict(body, execution_backend="codex_app_server"))[0], 400)

    def test_project_reads_are_scoped_and_symlinks_cannot_escape(self):
        status_code, listing = self.request("GET", "/projects/demo/directory?path=.")
        self.assertEqual(status_code, 200)
        self.assertIn("hello.txt", [item["name"] for item in listing["entries"]])
        status_code, file_value = self.request("GET", "/projects/demo/file?path=hello.txt")
        self.assertEqual(status_code, 200)
        self.assertEqual(file_value["content"], "hello MCP\n")
        self.assertEqual(self.request("GET", "/projects/demo/file?path=leak.txt")[0], 403)
        self.assertEqual(self.request("GET", "/projects/demo/file?path=../secret.txt")[0], 403)

    def test_backend_claim_retry_returns_same_lease(self):
        self.store.create("backend", prepare_response(self.store, {"model": "fixture", "input": "x"}), "provider-key")
        first = self.request("POST", "/backend/claim", {"request_key": "claim-http"})[1]["turn"]
        retry = self.request("POST", "/backend/claim", {"request_key": "claim-http"})[1]["turn"]
        self.assertEqual(first["id"], retry["id"])
        self.assertEqual(first["lease"], retry["lease"])

    def test_mcp_http_auth_version_tools_resources_and_prompts(self):
        meta = {
            "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
            "io.modelcontextprotocol/clientCapabilities": {},
            "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
        }
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": meta}}
        self.assertEqual(self.request("POST", "/mcp", request, role="gpt", extra_headers=self.mcp_headers("tools/list"))[0], 401)
        status_code, listed = self.request("POST", "/mcp", request, role="mcp", extra_headers=self.mcp_headers("tools/list"))
        self.assertEqual(status_code, 200)
        names = {tool["name"] for tool in listed["result"]["tools"]}
        self.assertIn("codex_x_create_task", names)
        self.assertIn("codex_x_list_models", names)
        self.assertIn("codex_x_claim_backend_turn", names)
        self.assertIn("codex_x_read_project_file", names)

        call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "codex_x_read_project_file",
            "arguments": {"project_id": "demo", "path": "hello.txt"},
            "_meta": meta,
        }}
        status_code, called = self.request("POST", "/mcp", call, role="mcp", extra_headers=self.mcp_headers("tools/call", "codex_x_read_project_file"))
        self.assertEqual(status_code, 200)
        self.assertEqual(called["result"]["structuredContent"]["content"], "hello MCP\n")

        resources = {"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {"_meta": meta}}
        prompts = {"jsonrpc": "2.0", "id": 4, "method": "prompts/list", "params": {"_meta": meta}}
        resource_result = self.request("POST", "/mcp", resources, role="mcp", extra_headers=self.mcp_headers("resources/list"))[1]["result"]
        prompt_result = self.request("POST", "/mcp", prompts, role="mcp", extra_headers=self.mcp_headers("prompts/list"))[1]["result"]
        self.assertEqual(len(resource_result["resources"]), 2)
        self.assertEqual(len(prompt_result["prompts"]), 2)
        self.assertEqual(listed["result"]["resultType"], "complete")
        self.assertEqual(listed["result"]["ttlMs"], 0)
        self.assertEqual(listed["result"]["cacheScope"], "private")
        self.assertEqual(listed["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["version"], "0.2.12")

        mismatch = dict(request)
        mismatch["id"] = 5
        self.assertEqual(self.request("POST", "/mcp", mismatch, role="mcp", extra_headers={
            "MCP-Protocol-Version": "2025-11-25", "Mcp-Method": "tools/list"
        })[0], 400)

    def test_backend_sse_tool_loop_and_chunking(self):
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(self.request, "POST", "/v1/responses",
                {"model": "fixture-model", "stream": True, "input": "x" * 25000,
                 "tools": [{"type": "function", "name": "read_file"}]}, "provider")
            turn = None
            deadline = time.monotonic() + 2
            attempt = 0
            while turn is None and time.monotonic() < deadline:
                attempt += 1
                turn = self.request("POST", "/backend/claim", {"request_key": f"claim-loop-{attempt}"})[1]["turn"]
                time.sleep(0.01)
            self.assertIsNotNone(turn)
            first = self.request("GET", "/backend/" + turn["id"] + "/context?lease=" + turn["lease"])[1]
            second = self.request("GET", "/backend/" + turn["id"] + "/context?lease=" + turn["lease"] + "&offset=" + str(first["next_offset"]))[1]
            self.assertIsNone(second["next_offset"])
            self.assertEqual(len(first["chunk"] + second["chunk"]), first["total"])
            status, done = self.request("POST", "/backend/" + turn["id"] + "/complete",
                {"lease": turn["lease"], "request_key": "complete", "calls": [{"name": "read_file", "input": "{}"}]})
            self.assertEqual(status, 200)
            status, stream = future.result(timeout=3)
            self.assertEqual(status, 200)
            events = [json.loads(line[6:]) for line in stream.splitlines() if line.startswith("data: ")]
            self.assertEqual(events[-1]["type"], "response.completed")
            self.assertEqual([event["sequence_number"] for event in events], list(range(len(events))))
            self.assertEqual(events[-1]["response"]["output"][0]["name"], "read_file")

    def test_backend_timeout(self):
        self.server.config["backend_timeout_seconds"] = 0.02
        status, response = self.request("POST", "/v1/responses", {"model": "fixture", "input": "x"}, "provider")
        self.assertEqual(status, 504)
        self.assertEqual(response["status"], "failed")

if __name__ == "__main__":
    unittest.main()
