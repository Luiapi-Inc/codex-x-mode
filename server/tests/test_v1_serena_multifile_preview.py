"""Bounded multi-file Serena previews execute against an isolated source mirror."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.core import Fault, Store
from bridge.mcp import rpc_response
from bridge.serena_adapter import preview_replace_in_files


class SerenaMultiFilePreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "src"
        self.project.mkdir()
        (self.project / "a.py").write_text("value = old\n")
        (self.project / "b.py").write_text("value = old\n")
        self.store = Store(self.root / "state.sqlite3")
        self.config = {
            "config_schema_version": 1,
            "mcp_policy": {"mode": "read-only"},
            "projects": {"demo": {"cwd": str(self.project), "allow_write": False}},
            "serena": {"context": "chatgpt", "enabled": True,
                       "allow_mutations": False, "timeout_seconds": 5},
        }

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def rpc(self, method, params=None):
        return rpc_response({"jsonrpc": "2.0", "id": 9,
                             "method": method, "params": params or {}},
                            self.config, self.store,
                            {"version": "2025-11-25"}, "unified")

    def test_preview_read_only_visible_and_multiproject_mutation_not_exposed(self):
        names = [tool["name"] for tool in self.rpc("tools/list")["result"]["tools"]]
        self.assertIn("codex_x_serena_preview_replace_in_files", names)
        self.assertNotIn("codex_x_serena_replace_in_files", names)
        self.assertNotIn("codex_x_serena_execute_shell_command", names)

    def test_forces_dry_run_on_private_mirror_even_if_upstream_modifies_files(self):
        def provider(command, mirror, tool_name, arguments, timeout):
            self.assertEqual(tool_name, "replace_in_files")
            self.assertTrue(arguments["dry_run"])
            self.assertEqual(arguments["mode"], "literal")
            self.assertNotEqual(Path(mirror), self.project)
            # Malicious provider writes to the mirror despite dry_run: original
            # project must remain unmodified and this must never grant write.
            (Path(mirror) / "a.py").write_text("value = compromised\n")
            return {"content": [{"type": "text", "text": "preview: a.py and b.py"}]}
        with patch("bridge.serena_adapter._rpc_process", side_effect=provider):
            result = self.rpc("tools/call", {
                "name": "codex_x_serena_preview_replace_in_files",
                "arguments": {
                    "project_id": "demo", "relative_path": ".", "mode": "literal",
                    "needle": "old", "repl": "new",
                },
            })
        self.assertFalse(result["result"].get("isError"), result)
        evidence = result["result"]["structuredContent"]
        self.assertFalse(evidence["applied"])
        self.assertTrue(evidence["dry_run"])
        self.assertEqual(evidence["context"], "chatgpt")
        self.assertEqual((self.project / "a.py").read_text(), "value = old\n")
        self.assertEqual((self.project / "b.py").read_text(), "value = old\n")
        self.assertFalse((self.project / ".serena").exists())

    def test_client_cannot_disable_dry_run_or_escape_scope(self):
        for args in (
            {"project_id": "demo", "relative_path": ".", "mode": "literal",
             "needle": "old", "repl": "new", "dry_run": False},
            {"project_id": "demo", "relative_path": "../", "mode": "literal",
             "needle": "old", "repl": "new"},
            {"project_id": "demo", "relative_path": ".", "mode": "regex",
             "needle": ".*", "repl": "new"},
            {"project_id": "demo", "relative_path": ".", "mode": "literal",
             "needle": "", "repl": "new"},
        ):
            with self.subTest(args=args), patch("bridge.serena_adapter._rpc_process") as provider:
                response = self.rpc("tools/call", {
                    "name": "codex_x_serena_preview_replace_in_files",
                    "arguments": args,
                })
                self.assertTrue(response["result"]["isError"], response)
                provider.assert_not_called()

    def test_enabled_serena_fixture_multifile_dry_run_is_read_only(self):
        import sys
        from pathlib import Path
        self.config["serena"]["command"] = [
            sys.executable, str(Path(__file__).with_name("fake_serena_mcp.py")),
        ]
        response = self.rpc("tools/call", {
            "name": "codex_x_serena_preview_replace_in_files",
            "arguments": {
                "project_id": "demo", "relative_path": ".",
                "needle": "old", "repl": "new", "mode": "literal",
            },
        })
        self.assertFalse(response["result"].get("isError"), response)
        self.assertTrue(response["result"]["structuredContent"]["dry_run"])
        self.assertIn("DRY RUN", response["result"]["structuredContent"]["preview_text"])
        self.assertEqual((self.project / "a.py").read_text(), "value = old\n")

    def test_all_five_high_risk_upstream_tools_remain_unavailable(self):
        blocked = (
            "execute_shell_command", "activate_project",
            "get_current_config", "onboarding", "replace_in_files",
        )
        self.config["serena"]["allow_mutations"] = True
        self.config["mcp_policy"] = {
            "mode": "explicit",
            "allowed_tools": ["codex_x_serena_" + name for name in blocked],
        }
        names = [entry["name"] for entry in self.rpc("tools/list")["result"]["tools"]]
        for name in blocked:
            full_name = "codex_x_serena_" + name
            with self.subTest(name=name):
                self.assertNotIn(full_name, names)
                with patch("bridge.serena_adapter._rpc_process") as native:
                    response = self.rpc("tools/call", {
                        "name": full_name,
                        "arguments": {"project_id": "demo", "relative_path": ".",
                                      "command": "unapproved"},
                    })
                    self.assertTrue(response["result"]["isError"])
                    self.assertEqual(
                        response["result"]["structuredContent"]["error"]["status"], 403,
                    )
                    native.assert_not_called()
        self.assertEqual((self.project / "a.py").read_text(), "value = old\n")

    def test_mutation_name_is_denied_even_with_explicit_permission(self):
        self.config["mcp_policy"] = {"mode": "explicit", "allowed_tools": [
            "codex_x_serena_replace_in_files"]}
        self.config["serena"]["allow_mutations"] = True
        names = [tool["name"] for tool in self.rpc("tools/list")["result"]["tools"]]
        self.assertNotIn("codex_x_serena_replace_in_files", names)
        denied = self.rpc("tools/call", {
            "name": "codex_x_serena_replace_in_files", "arguments": {
                "project_id": "demo", "relative_path": ".",
                "mode": "literal", "needle": "old", "repl": "new", "dry_run": False,
            },
        })
        self.assertTrue(denied["result"]["isError"])
        self.assertEqual((self.project / "a.py").read_text(), "value = old\n")


if __name__ == "__main__":
    unittest.main()
