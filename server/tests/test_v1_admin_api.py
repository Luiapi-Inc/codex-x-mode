"""Admin boundary: real loopback HTTP, bearer separation, CAS, confirmation, audit, rollback."""
import http.client
import json
import secrets
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.admin import AdminServer
from bridge.core import Store
from bridge.http import Server


class AdminApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name).resolve()
        self.path = root / "private.json"
        self.config = {
            "config_schema_version": 1,
            "mcp_key": secrets.token_urlsafe(32),
            "gpt_key": secrets.token_urlsafe(32),
            "provider_key": secrets.token_urlsafe(32),
            "admin_key": secrets.token_urlsafe(32),
            "mcp_policy": {"mode": "read-only"},
            "web_model_policy": "native",
            "projects": {},
            "allowed_origins": [],
            "codex_command": ["codex"],
        }
        self.path.write_text(json.dumps(self.config))
        self.path.chmod(0o600)
        self.store = Store(root / "state.sqlite3")
        self.mcp = Server(("127.0.0.1", 0), self.config, self.store, start_worker=False)
        self.admin = AdminServer(("127.0.0.1", 0), self.config, self.store,
                                 self.mcp, config_path=self.path)
        self.runner = threading.Thread(target=self.admin.serve_forever, daemon=True)
        self.runner.start()

    def tearDown(self):
        self.admin.shutdown()
        self.runner.join(timeout=3)
        self.admin.server_close()
        self.mcp.server_close()
        self.store.close()
        self.temp.cleanup()

    def request(self, method, path, obj=None, *, key="admin", headers=None):
        base = {"Content-Type": "application/json"}
        if key:
            k = {"admin": self.config["admin_key"],
                 "mcp": self.config["mcp_key"], "gpt": self.config["gpt_key"]}[key]
            base["Authorization"] = "Bearer " + k
        base.update(headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.admin.server_port, timeout=4)
        try:
            conn.request(method, path, None if obj is None else json.dumps(obj), base)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def change(self):
        return {"mcp_policy": {"mode": "explicit", "allowed_tools": ["codex_x_status"]}}

    def test_disabled_or_shared_admin_secret_cannot_start_listener(self):
        for patch_value in (
            {"admin_key": ""},
            {"admin_key": self.config["mcp_key"]},
            {"admin_key": self.config["gpt_key"]},
        ):
            config = dict(self.config, **patch_value)
            with self.subTest(admin_key_kind=repr(patch_value)[:20]), self.assertRaises(ValueError):
                AdminServer(("127.0.0.1", 0), config, self.store,
                            self.mcp, config_path=self.path)
        with self.assertRaises(ValueError):
            AdminServer(("0.0.0.0", 0), self.config, self.store,
                        self.mcp, config_path=self.path)

    def test_rejected_external_proxy_and_admin_path_not_shared_with_mcp(self):
        for headers in (
            {"X-Forwarded-For": "1.2.3.4"},
            {"Forwarded": "for=1.2.3.4"},
            {"CF-Access-Jwt-Assertion": "unsigned-token"},
        ):
            status, _ = self.request("GET", "/admin/v1/config", headers=headers)
            self.assertEqual(status, 403)
        # MCP and Admin auth domains are different listeners.
        from bridge.mcp import rpc_response
        with self.mcp.policy_lock:
            res = rpc_response({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                               self.mcp.config, self.store, {"version": "2025-11-25"}, "unified")
        self.assertFalse(any(t["name"].startswith("admin") for t in res["result"]["tools"]))

    def test_mutation_requires_explicit_confirmation_and_admin_only(self):
        revision = self.admin.kernel.snapshot()["revision"]
        for key in (None, "mcp", "gpt", "admin"):
            status, _ = self.request("POST", "/admin/v1/config/apply", {
                "changes": self.change(), "expected_revision": revision,
            }, key=key)
            self.assertEqual(status, 403 if key == "admin" else 401)
        status, _ = self.request("POST", "/admin/v1/config/rollback", {
            "target_revision": "a" * 64, "expected_revision": revision,
        })
        self.assertEqual(status, 404)
        self.assertEqual(self.admin.kernel.snapshot()["revision"], revision)

    def test_fail_closed_auth_origin_host_and_no_admin_side_effect(self):
        revision = self.admin.kernel.snapshot()["revision"]
        for key in (None, "mcp", "gpt"):
            status, _ = self.request("GET", "/admin/v1/config", key=key)
            self.assertEqual(status, 401)
        for extra in (
            {"Origin": "https://evil.example"},
            {"Sec-Fetch-Site": "cross-site"},
            {"Host": "admin.example.com"},
            {"X-Forwarded-Host": "admin.example.com"},
        ):
            status, _ = self.request("POST", "/admin/v1/config/preview", {
                "changes": self.change(), "expected_revision": revision
            }, headers=extra)
            self.assertEqual(status, 403)
        self.assertEqual(self.admin.kernel.snapshot()["revision"], revision)

    def test_read_redacted_preview_and_apply_with_capability_reload(self):
        status, snapshot = self.request("GET", "/admin/v1/config")
        self.assertEqual(status, 200)
        revision = snapshot["revision"]
        self.assertEqual(snapshot["config"]["admin_key"], "[REDACTED]")
        self.assertNotIn(self.config["admin_key"], json.dumps(snapshot))
        status, preview = self.request("POST", "/admin/v1/config/preview", {
            "changes": self.change(), "expected_revision": revision,
        })
        self.assertEqual(status, 200)
        self.assertTrue(preview["confirmation"])
        status, _ = self.request("POST", "/admin/v1/config/apply", {
            "changes": self.change(), "expected_revision": revision,
            "confirmation": preview["confirmation"],
        })
        self.assertEqual(status, 200)
        self.assertEqual(self.mcp.config["mcp_policy"]["mode"], "explicit")
        self.assertEqual(json.loads(self.path.read_text())["mcp_policy"]["mode"], "explicit")
        status, stale = self.request("POST", "/admin/v1/config/apply", {
            "changes": self.change(), "expected_revision": revision,
            "confirmation": preview["confirmation"],
        })
        self.assertEqual(status, 409)
        self.assertEqual(stale["error"]["message"], "Stale configuration revision")
        audit = self.store.db.execute("SELECT action,outcome FROM admin_config_audit ORDER BY id").fetchall()
        self.assertTrue(any(r["action"] == "apply" and r["outcome"] == "success" for r in audit))

    def test_policy_is_enforced_for_subsequent_mcp_calls_after_hot_apply(self):
        from bridge.mcp import rpc_response
        def catalog():
            payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
            with self.mcp.policy_lock:
                return [item["name"] for item in rpc_response(
                    payload, self.mcp.config, self.store,
                    {"version": "2025-11-25"}, "unified",
                )["result"]["tools"]]
        self.assertIn("codex_x_list_models", catalog())
        revision = self.admin.kernel.snapshot()["revision"]
        code, prev = self.request("POST", "/admin/v1/config/preview", {
            "changes": self.change(), "expected_revision": revision,
        })
        self.assertEqual(code, 200)
        self.assertEqual(self.request("POST", "/admin/v1/config/apply", {
            "changes": self.change(), "expected_revision": revision,
            "confirmation": prev["confirmation"],
        })[0], 200)
        self.assertEqual(catalog(), ["codex_x_status"])

    def test_new_key_requires_offline_apply_for_reversible_rollback(self):
        self.config.pop("allowed_origins")
        self.mcp.config.pop("allowed_origins")
        revision = self.admin.kernel.snapshot()["revision"]
        changes = {"allowed_origins": ["https://example.org"]}
        code, prev = self.request("POST", "/admin/v1/config/preview", {
            "changes": changes, "expected_revision": revision,
        })
        self.assertEqual(code, 200)
        code, _ = self.request("POST", "/admin/v1/config/apply", {
            "changes": changes, "expected_revision": revision,
            "confirmation": prev["confirmation"],
        })
        self.assertEqual(code, 403)
        self.assertEqual(self.admin.kernel.snapshot()["revision"], revision)

    def test_confirmation_bound_to_change_and_exact_revision(self):
        revision = self.admin.kernel.snapshot()["revision"]
        status, preview = self.request("POST", "/admin/v1/config/preview", {
            "changes": self.change(), "expected_revision": revision,
        })
        self.assertEqual(status, 200)
        status, _ = self.request("POST", "/admin/v1/config/apply", {
            "changes": {"mcp_policy": {"mode": "read-only"}},
            "expected_revision": revision, "confirmation": preview["confirmation"],
        })
        self.assertEqual(status, 403)
        status, _ = self.request("POST", "/admin/v1/config/apply", {
            "changes": self.change(), "expected_revision": revision, "confirmation": "forged",
        })
        self.assertEqual(status, 403)
        status, _ = self.request("POST", "/admin/v1/config/apply", {
            "changes": {"projects": {}}, "expected_revision": revision,
            "confirmation": preview["confirmation"],
        })
        self.assertEqual(status, 403)
        self.assertEqual(self.admin.kernel.snapshot()["revision"], revision)

    def test_revision_conflict_preserves_other_writers_update(self):
        from bridge.config_kernel import ConfigFault
        revision = self.admin.kernel.snapshot()["revision"]
        _, preview = self.request("POST", "/admin/v1/config/preview", {
            "changes": self.change(), "expected_revision": revision,
        })
        original = self.admin.kernel.apply
        def competing_update(changes, *, expected_revision):
            original({"allowed_origins": ["https://other.example"]},
                     expected_revision=expected_revision)
            raise ConfigFault(409, "Stale configuration revision")
        with patch.object(self.admin.kernel, "apply", side_effect=competing_update):
            status, _ = self.request("POST", "/admin/v1/config/apply", {
                "changes": self.change(), "expected_revision": revision,
                "confirmation": preview["confirmation"],
            })
        self.assertEqual(status, 409)
        disk = json.loads(self.path.read_text())
        self.assertEqual(disk["allowed_origins"], ["https://other.example"])
        self.assertEqual(disk["mcp_policy"], {"mode": "read-only"})

    def test_rollback_requires_second_preview_and_restores_disk_and_runtime(self):
        before = self.admin.kernel.snapshot()["revision"]
        status, preview = self.request("POST", "/admin/v1/config/preview", {
            "changes": self.change(), "expected_revision": before,
        })
        self.assertEqual(status, 200)
        self.assertEqual(self.request("POST", "/admin/v1/config/apply", {
            "changes": self.change(), "expected_revision": before,
            "confirmation": preview["confirmation"],
        })[0], 200)
        changed = self.admin.kernel.snapshot()["revision"]
        status, revert = self.request("POST", "/admin/v1/config/rollback/preview", {
            "target_revision": before, "expected_revision": changed,
        })
        self.assertEqual(status, 200)
        status, done = self.request("POST", "/admin/v1/config/rollback", {
            "target_revision": before, "expected_revision": changed,
            "confirmation": revert["confirmation"],
        })
        self.assertEqual(status, 200)
        self.assertEqual(self.mcp.config["mcp_policy"], {"mode": "read-only"})
        self.assertEqual(json.loads(self.path.read_text())["mcp_policy"], {"mode": "read-only"})
        self.assertNotEqual(done["revision"], changed)

    def test_online_project_change_refused_and_failed_health_rolls_back(self):
        before = self.admin.kernel.snapshot()["revision"]
        status, prev = self.request("POST", "/admin/v1/config/preview", {
            "changes": {"projects": {}}, "expected_revision": before,
        })
        self.assertEqual(status, 403)
        status, prev = self.request("POST", "/admin/v1/config/preview", {
            "changes": self.change(), "expected_revision": before,
        })
        self.assertEqual(status, 200)
        with patch.object(self.admin, "verify_live_policy", side_effect=RuntimeError("health failed")):
            status, _ = self.request("POST", "/admin/v1/config/apply", {
                "changes": self.change(), "expected_revision": before,
                "confirmation": prev["confirmation"],
            })
        self.assertEqual(status, 503)
        self.assertEqual(self.mcp.config["mcp_policy"], {"mode": "read-only"})
        self.assertEqual(json.loads(self.path.read_text())["mcp_policy"], {"mode": "read-only"})


if __name__ == "__main__":
    unittest.main()
