"""G2 config kernel contract: local-only staging, CAS, atomic write and safe authority."""
import io
import json
import os
import sys
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch
import stat
import tempfile
import unittest
from pathlib import Path

from bridge.config_kernel import ConfigKernel, ConfigFault
from bridge.core import Store


class ConfigKernelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / "repo"
        self.project.mkdir()
        self.path = self.root / "private.json"
        self.original = {
            "mcp_key": "super-secret-token-" * 3,
            "gpt_key": "other-private-" * 3,
            "provider_key": "provider-private-" * 3,
            "projects": {"demo": {"cwd": str(self.project), "allow_write": False}},
            "mcp_policy": {"mode": "read-only"},
        }
        self.path.write_text(json.dumps(self.original), encoding="utf-8")
        self.path.chmod(0o600)
        self.store = Store(self.root / "state.sqlite3")
        self.kernel = ConfigKernel(self.path, store=self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_snapshot_and_preview_do_not_reveal_secret_values(self):
        snap = self.kernel.snapshot()
        self.assertNotIn(self.original["mcp_key"], json.dumps(snap))
        assert snap["config"]["mcp_key"] == "[REDACTED]"
        preview = self.kernel.preview(
            {"mcp_policy": {"mode": "explicit", "allowed_tools": ["codex_x_status"]}},
            expected_revision=snap["revision"],
        )
        self.assertEqual(preview["changed_keys"], ["mcp_policy"])
        self.assertNotIn(self.original["mcp_key"], json.dumps(preview))
        self.assertNotEqual(preview["revision"], preview["next_revision"])
        self.assertEqual(self.path.read_text(), json.dumps(self.original))

    def test_apply_cas_and_private_history_atomicity(self):
        old = self.kernel.snapshot()
        result = self.kernel.apply({"mcp_policy": {"mode": "explicit", "allowed_tools": ["codex_x_status"]}},
                                   expected_revision=old["revision"])
        self.assertEqual(result["revision"], self.kernel.snapshot()["revision"])
        self.assertNotEqual(result["revision"], old["revision"])
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(self.path.read_text())["mcp_policy"]["mode"], "explicit")
        old_archive = self.root / "private.json.history" / (old["revision"] + ".json")
        self.assertTrue(old_archive.exists())
        self.assertEqual(stat.S_IMODE(old_archive.stat().st_mode), 0o600)
        self.assertEqual(json.loads(old_archive.read_text()), self.original)
        with self.assertRaises(ConfigFault) as conflict:
            self.kernel.apply({"mcp_policy": {"mode": "read-only"}}, expected_revision=old["revision"])
        self.assertEqual(conflict.exception.status, 409)
        self.assertEqual(self.kernel.snapshot()["revision"], result["revision"])

    def test_invalid_policy_and_secret_write_fail_before_disk_mutation(self):
        initial = self.path.read_bytes()
        rev = self.kernel.snapshot()["revision"]
        cases = [
            {"mcp_key": "replace-secrets"}, {"projects": {"demo": {"cwd": "/missing", "allow_write": True}}},
            {"mcp_policy": None},
            {"web_model_policy": "auto"},
            {"mcp_policy": {"mode": "explicit", "allowed_tools": ["does_not_exist"]}},
            {"mcp_policy": {"mode": "explicit", "allowed_tools": ["codex_x_status"], "admin": True}},
            {"allowed_origins": ["*"]},
            {"allowed_origins": ["http://evil.example"]},
        ]
        for patch in cases:
            with self.subTest(patch=patch), self.assertRaises(ConfigFault):
                self.kernel.apply(patch, expected_revision=rev)
            self.assertEqual(self.path.read_bytes(), initial)

    def test_project_authorization_change_blocks_unknown_writer(self):
        current = self.kernel.snapshot()["revision"]
        job = self.store.create("task", {"project_id": "demo", "resource_id": str(self.project),
                                         "scope": "workspace-write", "prompt": "inspect"}, "x-key")
        self.store.update_task(job["id"], state="unknown")
        with self.assertRaises(ConfigFault) as blocked:
            self.kernel.apply({"projects": {"demo": {"cwd": str(self.project), "allow_write": True}}},
                              expected_revision=current)
        self.assertEqual(blocked.exception.status, 409)
        self.assertEqual(self.kernel.snapshot()["revision"], current)

    def test_offline_cli_show_preview_apply_and_runtime_lock(self):
        from bridge import __main__ as entry
        expected = self.kernel.snapshot()["revision"]
        changes = self.root / "change.json"
        changes.write_text(json.dumps({"mcp_policy": {
            "mode": "explicit", "allowed_tools": ["codex_x_status"]
        }}))
        def invoke(*args):
            output = io.StringIO()
            with patch.object(sys, "argv", ["bridge", "--config", str(self.path), *args]):
                with redirect_stdout(output):
                    entry.main()
            return json.loads(output.getvalue())

        current = invoke("config-show")
        self.assertEqual(current["revision"], expected)
        self.assertNotIn(self.original["gpt_key"], json.dumps(current))
        staged = invoke("config-preview", "--changes", str(changes),
                        "--expected-revision", expected)
        self.assertIn("mcp_policy", staged["changed_keys"])
        self.assertEqual(self.kernel.snapshot()["revision"], expected)

        from unittest.mock import patch as monkeypatch
        with monkeypatch.object(entry.fcntl, "flock", side_effect=BlockingIOError):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                invoke("config-apply", "--changes", str(changes),
                       "--expected-revision", expected, "--confirm")
        self.assertEqual(self.kernel.snapshot()["revision"], expected)
        applied = invoke("config-apply", "--changes", str(changes),
                         "--expected-revision", expected, "--confirm")
        self.assertTrue(applied["requires_reload"])
        self.assertNotEqual(applied["revision"], expected)

    def test_symlink_config_and_insecure_mode_rejected(self):
        alias = self.root / "alias.json"
        alias.symlink_to(self.path)
        with self.assertRaises(ConfigFault):
            ConfigKernel(alias).snapshot()
        self.path.chmod(0o644)
        with self.assertRaises(ConfigFault):
            self.kernel.snapshot()


if __name__ == "__main__":
    unittest.main()
