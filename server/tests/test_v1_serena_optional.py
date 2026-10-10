"""G4 Serena optional subprocess contract — read-only, project scoped, fail isolated."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.core import Fault, Store
from bridge.mcp import rpc_response
from bridge.serena_adapter import find_symbol


class SerenaOptionalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "demo"
        self.project.mkdir()
        (self.project / "module.py").write_text("def alpha():\n    return 2\n")
        self.store = Store(self.root / "jobs.sqlite3")
        self.cfg = {
            "config_schema_version": 1, "projects": {"demo": {"cwd": str(self.project), "allow_write": False}},
            "mcp_policy": {"mode": "read-only"},
            "serena": {"enabled": False},
        }

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _rpc(self, method, params=None):
        return rpc_response({"jsonrpc": "2.0", "id": 9, "method": method,
                             "params": params or {}}, self.cfg, self.store,
                            {"version": "2025-11-25"}, "unified")

    def enable_fixture(self):
        self.cfg["serena"] = {"enabled": True,
                              "command": [sys.executable, str(Path(__file__).with_name("fake_serena_mcp.py"))],
                              "timeout_seconds": 4}

    def test_optional_provider_not_advertised_when_disabled(self):
        names = [x["name"] for x in self._rpc("tools/list")["result"]["tools"]]
        self.assertNotIn("codex_x_serena_find_symbol", names)
        denied = self._rpc("tools/call", {"name": "codex_x_serena_find_symbol",
                                           "arguments": {"project_id": "demo", "path": "module.py",
                                                         "name_path_pattern": "alpha"}})
        self.assertEqual(denied["result"]["structuredContent"]["error"]["status"], 403)
        self.assertIn("codex_x_read_project_file", names)

    def test_enabled_serena_calls_only_find_symbol_through_bounded_mcp(self):
        self.enable_fixture()
        names = [x["name"] for x in self._rpc("tools/list")["result"]["tools"]]
        self.assertIn("codex_x_serena_find_symbol", names)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "DO-NOT-LEAK"}):
            result = self._rpc("tools/call", {"name": "codex_x_serena_find_symbol",
                        "arguments": {"project_id": "demo", "path": "module.py",
                                      "name_path_pattern": "alpha"}})
        self.assertFalse(result["result"].get("isError"), result)
        content = result["result"]["structuredContent"]
        data = json.loads(content["symbols_text"])
        self.assertEqual(data["name_path_pattern"], "alpha")
        self.assertEqual(data["relative_path"], "module.py")
        self.assertFalse(data["include_body"])
        self.assertFalse(data["exposed_secret"])
        self.assertEqual((self.project / "module.py").read_text(), "def alpha():\n    return 2\n")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)

    def test_provider_down_does_not_break_core(self):
        self.cfg["serena"] = {"enabled": True, "command": ["/missing/serena"],
                              "timeout_seconds": 2}
        failed = self._rpc("tools/call", {"name": "codex_x_serena_find_symbol",
                                            "arguments": {"project_id": "demo", "path": "module.py",
                                                          "name_path_pattern": "alpha"}})
        self.assertTrue(failed["result"]["isError"])
        self.assertEqual(failed["result"]["structuredContent"]["error"]["status"], 503)
        core = self._rpc("tools/call", {"name": "codex_x_list_projects"})
        self.assertFalse(core["result"].get("isError"))

    def test_symlink_escape_and_unconfigured_project_rejected_before_serena_spawn(self):
        self.enable_fixture()
        (self.project / "escape.py").symlink_to(self.root / "jobs.sqlite3")
        for project, path in (("demo", "escape.py"), ("other", "module.py"),
                              ("demo", "../jobs.sqlite3")):
            with self.subTest(project=project, path=path), self.assertRaises(Fault):
                find_symbol(self.cfg, project, path, "alpha")

    def test_only_declared_read_only_args_allowed(self):
        self.enable_fixture()
        with self.assertRaises(Fault):
            find_symbol(self.cfg, "demo", "module.py", "", timeout_seconds=2)
        with self.assertRaises(Fault):
            find_symbol(self.cfg, "demo", "module.py", "alpha", timeout_seconds=True)


if __name__ == "__main__":
    unittest.main()
