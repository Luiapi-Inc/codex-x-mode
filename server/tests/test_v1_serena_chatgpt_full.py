"""Full ChatGPT Serena catalog with scoped, explicit write admission."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.core import Fault, Store
from bridge.mcp import rpc_response
from bridge.serena_adapter import (
    CATALOG, MAP, SAFE_READ, MUTATING, UNSAFE_REMOTE, call_tool
)


class FullSerenaChatGPTTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / "demo"
        self.project.mkdir()
        self.file = self.project / "file.py"
        self.file.write_text("value = 1\n", encoding="utf-8")
        self.store = Store(self.root / "state.sqlite3")
        self.conf = {
            "projects": {"demo": {"cwd": str(self.project), "allow_write": True}},
            "config_schema_version": 1,
            "mcp_policy": {"mode": "read-only"},
            "serena": {"enabled": True, "command": [
                sys.executable, str(Path(__file__).with_name("fake_serena_mcp.py"))],
                "timeout_seconds": 6},
        }

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def rpc(self, method, args=None):
        return rpc_response({"jsonrpc": "2.0", "id": 4, "method": method,
                             "params": args or {}}, self.conf, self.store,
                            {"version": "2025-11-25"}, "unified")

    def test_chatgpt_manifest_covers_all_29_actual_native_serena_tools(self):
        self.assertEqual(CATALOG["context"], "chatgpt")
        self.assertEqual(len(MAP), 29)
        self.assertEqual(set(MAP.values()), {t["name"] for t in CATALOG["tools"]})
        self.assertIn("execute_shell_command", UNSAFE_REMOTE)
        self.assertIn("activate_project", UNSAFE_REMOTE)
        self.assertIn("rename_symbol", MUTATING)
        self.assertIn("find_referencing_symbols", SAFE_READ)

    def test_read_only_full_serena_tools_visible_but_no_shell_or_mutation(self):
        names = [t["name"] for t in self.rpc("tools/list")["result"]["tools"]]
        self.assertTrue(all("codex_x_serena_" + name in names for name in SAFE_READ))
        for name in ("execute_shell_command", "activate_project", "replace_symbol_body",
                     "rename_symbol", "replace_in_files", "write_memory"):
            self.assertNotIn("codex_x_serena_" + name, names)
        for name in ("execute_shell_command", "rename_symbol"):
            response = self.rpc("tools/call", {"name": "codex_x_serena_" + name,
                                            "arguments": {"project_id": "demo", "command": "true"}})
            self.assertEqual(response["result"]["structuredContent"]["error"]["status"], 403)

    def test_context_argument_forced_chatgpt_and_no_secret_provider_env(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "SECRET-FOR-TEST", "GITHUB_TOKEN": "SECRET2"}):
            result = call_tool(self.conf, "demo", "find_symbol", {
                "name_path_pattern": "value", "relative_path": "file.py",
            })
        result_body = json.loads(result["symbols_text"])
        self.assertEqual(result["context"], "chatgpt")
        self.assertEqual(result_body["context"], "chatgpt")
        self.assertFalse(result_body["exposed_secret"])
        self.assertEqual(result_body["relative_path"], "file.py")
        self.assertFalse((self.project / ".serena").exists())
        self.assertEqual((self.project / "file.py").read_text(), "value = 1\n")

    def test_serena_child_uses_disposable_home_not_operator_auth(self):
        private_home = self.root / "operator-home"
        secrets_dir = private_home / ".codex"
        secrets_dir.mkdir(parents=True)
        (secrets_dir / "auth.json").write_text('{"private":"not-for-serena"}')
        with patch.dict("os.environ", {"HOME": str(private_home)}):
            result = call_tool(self.conf, "demo", "find_symbol", {
                "name_path_pattern": "value", "relative_path": "file.py",
            })
        info = json.loads(result["symbols_text"])
        self.assertFalse(info["operator_auth_visible"])
        self.assertNotEqual(Path(info["child_cwd"]), Path.cwd())
        self.assertNotEqual(Path(info["child_cwd"]), self.project)
        self.assertTrue((secrets_dir / "auth.json").exists())

    def test_explicit_scoped_serena_edit_has_cas_idempotency_and_evidence(self):
        self.conf["mcp_policy"] = {"mode": "explicit", "allowed_tools": [
            "codex_x_serena_replace_content"]}
        self.conf["serena"]["allow_mutations"] = True
        before = hashlib.sha256(self.file.read_bytes()).hexdigest()
        args = {
            "project_id": "demo", "relative_path": "file.py",
            "mode": "literal", "needle": "value = 1", "repl": "value = 2",
            "request_key": "change-value-once", "expected_sha256": before,
        }
        result = self.rpc("tools/call", {"name": "codex_x_serena_replace_content", "arguments": args})
        self.assertFalse(result["result"].get("isError"), result)
        content = result["result"]["structuredContent"]
        self.assertTrue(content["applied"])
        self.assertEqual(self.file.read_text(), "value = 2\n")
        self.assertTrue(content["changed_files"])
        with patch("bridge.serena_adapter._rpc_process") as run:
            again = self.rpc("tools/call", {"name": "codex_x_serena_replace_content", "arguments": args})
            self.assertFalse(again["result"].get("isError"), again)
            self.assertEqual(again["result"]["structuredContent"], content)
            run.assert_not_called()
        self.assertEqual(self.file.read_text(), "value = 2\n")

    def test_write_denied_without_expected_hash_or_explicit_permission(self):
        self.conf["serena"]["allow_mutations"] = True
        self.conf["mcp_policy"] = {"mode": "explicit", "allowed_tools": ["codex_x_serena_replace_content"]}
        before = self.file.read_bytes()
        for args in (
            {"relative_path": "file.py", "mode": "literal", "needle": "value = 1",
             "repl": "value = 2", "request_key": "a"},
            {"relative_path": "file.py", "mode": "literal", "needle": "value = 1",
             "repl": "value = 2", "request_key": "b", "expected_sha256": "0"*64},
        ):
            with self.subTest(args=args):
                response = self.rpc("tools/call", {
                    "name": "codex_x_serena_replace_content",
                    "arguments": {"project_id": "demo", **args},
                })
                self.assertTrue(response["result"]["isError"])
                self.assertEqual(self.file.read_bytes(), before)
        self.conf["serena"]["allow_mutations"] = False
        self.assertNotIn("codex_x_serena_replace_content",
                         [x["name"] for x in self.rpc("tools/list")["result"]["tools"]])

    def test_failed_serena_write_keeps_ambiguous_claim_and_prevents_replay(self):
        self.conf["serena"]["allow_mutations"] = True
        self.conf["mcp_policy"] = {"mode": "explicit", "allowed_tools": [
            "codex_x_serena_replace_content"]}
        h = hashlib.sha256(self.file.read_bytes()).hexdigest()
        args = {"project_id": "demo", "relative_path": "file.py",
                "mode": "literal", "needle": "NOT_PRESENT", "repl": "2",
                "request_key": "ambiguous-serena", "expected_sha256": h}
        denied = self.rpc("tools/call", {
            "name": "codex_x_serena_replace_content", "arguments": args})
        self.assertEqual(denied["result"]["structuredContent"]["error"]["status"], 503)
        self.assertTrue(self.store.unknown_app_writers())
        with patch("bridge.serena_adapter._rpc_process") as run:
            replay = self.rpc("tools/call", {
                "name": "codex_x_serena_replace_content", "arguments": args})
            self.assertEqual(replay["result"]["structuredContent"]["error"]["status"], 409)
            run.assert_not_called()
        self.assertEqual(self.file.read_text(), "value = 1\n")

    def test_mutation_symlink_target_and_cross_project_escape_fail_before_provider(self):
        self.conf["serena"]["allow_mutations"] = True
        self.conf["mcp_policy"] = {"mode": "explicit", "allowed_tools": [
            "codex_x_serena_replace_content"]}
        (self.project / "escape.py").symlink_to(self.root / "state.sqlite3")
        for target in ("escape.py", "../state.sqlite3", "/etc/passwd"):
            with self.subTest(path=target), patch("bridge.serena_adapter._rpc_process") as proc:
                denied = self.rpc("tools/call", {
                    "name": "codex_x_serena_replace_content", "arguments": {
                        "project_id": "demo", "relative_path": target,
                        "mode": "literal", "needle": "a", "repl": "b",
                        "request_key": "forbidden-" + target,
                        "expected_sha256": hashlib.sha256(self.file.read_bytes()).hexdigest(),
                    }})
                self.assertTrue(denied["result"].get("isError"))
                proc.assert_not_called()

    def test_wrong_context_is_not_silently_accepted(self):
        from bridge.config_kernel import ConfigKernel, ConfigFault
        settings = {"projects": {"demo": {"cwd": str(self.project), "allow_write": False}},
                    "serena": {"enabled": True, "context": "desktop-app"}}
        with self.assertRaises(ConfigFault) as invalid:
            ConfigKernel._validate(settings)
        self.assertEqual(invalid.exception.status, 400)

    def test_unknown_writer_blocks_serena_edit_even_with_explicit_allow(self):
        self.conf["serena"]["allow_mutations"] = True
        self.conf["mcp_policy"] = {"mode": "explicit", "allowed_tools": ["codex_x_serena_replace_content"]}
        job = self.store.create("task", {"project_id": "demo", "resource_id": str(self.project),
                                         "scope": "workspace-write"}, "other-writer")
        self.store.update_task(job["id"], state="unknown")
        args = {"project_id": "demo", "relative_path": "file.py", "mode": "literal",
                "needle": "value = 1", "repl": "value = 2", "request_key": "blocked-serena",
                "expected_sha256": hashlib.sha256(self.file.read_bytes()).hexdigest()}
        response = self.rpc("tools/call", {"name": "codex_x_serena_replace_content",
                                          "arguments": args})
        self.assertEqual(response["result"]["structuredContent"]["error"]["status"], 409)
        self.assertEqual(self.file.read_text(), "value = 1\n")


if __name__ == "__main__":
    unittest.main()
