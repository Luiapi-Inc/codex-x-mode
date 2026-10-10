"""G4 Direct previews: path containment, CAS, diff bounds, no writes or dispatch."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.core import Fault, Store
from bridge.mcp import rpc_response
from bridge.workspace_direct import preview_edit


class DirectPreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.file = self.project / "hello.py"
        self.file.write_text("name = 'old'\n", encoding="utf-8")
        self.expected = hashlib.sha256(self.file.read_bytes()).hexdigest()
        self.store = Store(self.root / "jobs.sqlite3")
        self.config = {
            "projects": {"demo": {"cwd": str(self.project), "allow_write": False}},
            "mcp_policy": {"mode": "read-only"},
            "config_schema_version": 1,
        }

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_preview_contains_precise_hash_diff_and_does_not_change_file(self):
        prior = self.file.read_bytes()
        result = preview_edit(self.config, "demo", "hello.py", "name = 'new'\n",
                              expected_sha256=self.expected)
        self.assertEqual(result["old_sha256"], self.expected)
        self.assertEqual(result["new_sha256"], hashlib.sha256(b"name = 'new'\n").hexdigest())
        self.assertIn("-name = 'old'", result["diff"])
        self.assertIn("+name = 'new'", result["diff"])
        self.assertFalse(result["applied"])
        self.assertEqual(self.file.read_bytes(), prior)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 0)

    def test_missing_or_stale_hash_fails_closed(self):
        with self.assertRaises(Fault) as exc:
            preview_edit(self.config, "demo", "hello.py", "change", expected_sha256="0"*64)
        self.assertEqual(exc.exception.status, 409)
        with self.assertRaises(Fault):
            preview_edit(self.config, "demo", "hello.py", "change", expected_sha256="garbage")
        self.assertEqual(self.file.read_text(), "name = 'old'\n")

    def test_symlink_traversal_absolute_and_binary_file_denied(self):
        (self.project / "escape").symlink_to(self.root)
        (self.project / "binary.dat").write_bytes(b"\xff\xfe\x00")
        for path in ("../external", "/etc/passwd", "escape/jobs.sqlite3", "binary.dat"):
            with self.subTest(path=path), self.assertRaises(Fault):
                preview_edit(self.config, "demo", path, "replace",
                             expected_sha256="a"*64)

    def test_large_diff_is_rejected_not_silently_truncated(self):
        big = "line\n" * 100_000
        with self.assertRaises(Fault) as exc:
            preview_edit(self.config, "demo", "hello.py", big,
                         expected_sha256=self.expected, max_diff_bytes=1024)
        self.assertEqual(exc.exception.status, 413)
        self.assertEqual(self.file.read_text(), "name = 'old'\n")

    def test_one_mcp_read_only_catalog_discovers_preview_and_denies_mutation(self):
        status = rpc_response({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                              self.config, self.store, {"version": "2025-11-25"}, "unified")
        names = [entry["name"] for entry in status["result"]["tools"]]
        self.assertIn("codex_x_preview_project_edit", names)
        self.assertNotIn("codex_x_apply_project_edit", names)
        call = rpc_response({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "codex_x_preview_project_edit", "arguments": {
                "project_id": "demo", "path": "hello.py", "new_content": "new\n",
                "expected_sha256": self.expected,
            },
        }}, self.config, self.store, {"version": "2025-11-25"}, "unified")
        self.assertFalse(call["result"].get("isError"))
        self.assertIn("-name = 'old'", call["result"]["structuredContent"]["diff"])
        self.assertEqual(self.file.read_text(), "name = 'old'\n")

    def test_disabled_project_and_unrecognized_arguments_denied(self):
        with self.assertRaises(Fault):
            preview_edit(self.config, "outside", "hello.py", "new\n", expected_sha256=self.expected)
        with self.assertRaises(Fault):
            preview_edit(self.config, "demo", "hello.py", "new\n", expected_sha256=self.expected,
                         max_diff_bytes=True)


if __name__ == "__main__":
    unittest.main()
