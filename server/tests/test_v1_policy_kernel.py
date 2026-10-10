"""G2/G3 security contract: a configured gateway never infers authorization from discovery."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.core import Store
from bridge.mcp import rpc_response, UNIFIED_TOOLS


class GatewayPolicyContractTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.home.name) / "state.sqlite3")
        self.config = {
            "projects": {}, "codex_command": ["codex"],
            "mcp_policy": {"mode": "read-only"},
        }

    def tearDown(self):
        self.store.close()
        self.home.cleanup()

    def rpc(self, method, params=None, config=None):
        return rpc_response(
            {"jsonrpc": "2.0", "id": 5, "method": method, "params": params or {}},
            config or self.config, self.store, {"version": "2025-11-25"}, "unified",
        )

    def test_read_only_discovery_filters_all_mutations(self):
        response = self.rpc("tools/list")
        tools = response["result"]["tools"]
        self.assertGreater(len(tools), 1)
        self.assertTrue(all(t["annotations"]["readOnlyHint"] is True for t in tools))
        self.assertNotIn("codex_x_create_task", [t["name"] for t in tools])
        self.assertNotIn("codex_x_app_create_thread", [t["name"] for t in tools])
        self.assertTrue(any(t["name"] == "codex_x_status" for t in tools))

    def test_read_only_denies_disallowed_tool_before_provider_or_state(self):
        with patch("bridge.mcp._call_tool") as core, patch("bridge.mcp.call_codex_x_app_tool") as app:
            for name in ("codex_x_create_task", "codex_x_app_create_thread"):
                response = self.rpc("tools/call", {"name": name, "arguments": {}})
                self.assertTrue(response["result"]["isError"])
                self.assertEqual(response["result"]["structuredContent"]["error"]["status"], 403)
            core.assert_not_called()
            app.assert_not_called()

    def test_explicit_capability_grants_are_exact_and_fails_closed(self):
        self.config["mcp_policy"] = {"mode": "explicit", "allowed_tools": ["codex_x_status"]}
        names = [t["name"] for t in self.rpc("tools/list")["result"]["tools"]]
        self.assertEqual(names, ["codex_x_status"])
        with patch("bridge.mcp._call_tool", return_value={"status": "up"}) as invoked:
            self.assertEqual(self.rpc("tools/call", {"name": "codex_x_status"})["result"]["structuredContent"]["status"], "up")
            invoked.assert_called_once()
        with patch("bridge.mcp._call_tool") as invoked:
            denied = self.rpc("tools/call", {"name": "codex_x_read_task", "arguments": {"task_id": "other"}})
            self.assertEqual(denied["result"]["structuredContent"]["error"]["status"], 403)
            invoked.assert_not_called()

    def test_invalid_policy_cannot_permit_tool_call_or_discovery(self):
        for policy in (
            {"mode": "none"}, {"mode": "explicit", "allowed_tools": "all"},
            {"mode": "explicit", "allowed_tools": ["missing_tool"]},
            {"mode": "read-only", "allowed_tools": ["codex_x_create_task"]},
            {"mode": "explicit", "allowed_tools": ["codex_x_status"], "admin": True},
        ):
            with self.subTest(policy=policy):
                cfg = copy.deepcopy(self.config)
                cfg["mcp_policy"] = policy
                with patch("bridge.mcp._call_tool") as invoked:
                    discovered = self.rpc("tools/list", config=cfg)
                    called = self.rpc("tools/call", {"name": "codex_x_status"}, config=cfg)
                    self.assertIn("error", discovered)
                    self.assertTrue(called.get("error") or called["result"].get("isError"))
                    invoked.assert_not_called()

    def test_schema_v1_cannot_run_without_explicit_policy(self):
        cfg = dict(self.config)
        cfg["config_schema_version"] = 1
        cfg.pop("mcp_policy")
        listing = self.rpc("tools/list", config=cfg)
        self.assertEqual(listing["error"]["code"], -32003)
        with patch("bridge.mcp._call_tool") as invoked:
            called = self.rpc("tools/call", {"name": "codex_x_create_task"}, config=cfg)
            self.assertEqual(called["error"]["code"], -32003)
            invoked.assert_not_called()

    def test_legacy_config_without_policy_keeps_existing_catalog_until_migration(self):
        cfg = dict(self.config)
        cfg.pop("mcp_policy")
        self.assertEqual(len(self.rpc("tools/list", config=cfg)["result"]["tools"]), len(UNIFIED_TOOLS))


if __name__ == "__main__":
    unittest.main()
