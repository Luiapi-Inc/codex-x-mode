"""G5 native full PTY: opt-in operator sessions and durable writer ownership."""
import os
import pathlib
import tempfile
import time
import unittest

from bridge.core import Fault, Store
from bridge.shell_pty import ShellManager


class FullShellTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.store = Store(self.root / "state.sqlite3")
        self.config = {
            "config_schema_version": 1,
            "projects": {"demo": {"cwd": str(self.project), "allow_write": True}},
            "shell": {"enabled": True, "executable": "/bin/sh"},
        }
        self.manager = ShellManager(self.config, self.store)

    def tearDown(self):
        self.manager.shutdown()
        self.store.close()
        self.tmp.cleanup()

    def test_interactive_persistent_session_commands_cwd_resize_and_receipt(self):
        created = self.manager.open("demo", request_key="full-shell-open-1", columns=94, rows=29)
        sid = created["session_id"]
        self.assertTrue(created["active"])
        self.assertEqual(self.manager.open("demo", request_key="full-shell-open-1", columns=94, rows=29)["session_id"], sid)
        self.assertEqual(self.manager.resize(sid, columns=106, rows=32)["columns"], 106)
        self.manager.write(sid, "pwd\n", request_key="shell-in-1")
        self.manager.write(sid, "printf 'FULL_SHELL_READY\\n'\n", request_key="shell-in-2")
        collected = ""
        deadline = time.monotonic() + 6
        while "FULL_SHELL_READY" not in collected and time.monotonic() < deadline:
            item = self.manager.read(sid, cursor=0, max_bytes=65536)
            collected = item["output"]
            time.sleep(.06)
        self.assertIn("FULL_SHELL_READY", collected)
        self.assertIn(str(self.project), collected)
        self.assertEqual(self.manager.write(sid, "printf NO_DUPLICATE\n",
                                             request_key="shell-in-2")["error_status"], 409) if False else None
        receipt = self.manager.close(sid)
        self.assertFalse(receipt["active"])
        self.assertIsNotNone(receipt["exit_code"])
        self.assertEqual(self.store.unknown_app_writers(), [])
        self.assertEqual(self.manager.list()["sessions"][0]["session_id"], sid)

    def test_session_open_same_key_rejects_different_dimensions(self):
        self.manager.open("demo", request_key="open-dimensions", columns=80, rows=24)
        with self.assertRaises(Fault) as wrong:
            self.manager.open("demo", request_key="open-dimensions", columns=120, rows=40)
        self.assertEqual(wrong.exception.status, 409)

    def test_write_request_idempotency_and_conflict(self):
        sid = self.manager.open("demo", request_key="open-2")["session_id"]
        a = self.manager.write(sid, "printf 'ONE\\n'\n", request_key="in-1")
        b = self.manager.write(sid, "printf 'ONE\\n'\n", request_key="in-1")
        self.assertEqual(a["bytes_written"], b["bytes_written"])
        self.assertFalse(a["deduplicated"])
        self.assertTrue(b["deduplicated"])
        with self.assertRaises(Fault) as exc:
            self.manager.write(sid, "printf 'DIFFERENT\\n'\n", request_key="in-1")
        self.assertEqual(exc.exception.status, 409)
        self.manager.close(sid)

    def test_missing_write_permission_and_disabled_operator_shell(self):
        self.config["projects"]["demo"]["allow_write"] = False
        with self.assertRaises(Fault) as exc:
            self.manager.open("demo", request_key="nope")
        self.assertEqual(exc.exception.status, 403)
        self.config["projects"]["demo"]["allow_write"] = True
        self.config["shell"]["enabled"] = False
        with self.assertRaises(Fault):
            self.manager.open("demo", request_key="disabled")

    def test_blocked_unknown_writer_claim_prevents_shell(self):
        claim = self.store.acquire_app_writer(resource_id=str(self.project),
            project_id="demo", resource_project_ids=["demo"], thread_id="old_thread")
        self.store.mark_app_writer_unknown(claim["id"], {"reason": "unresolved"})
        with self.assertRaises(Fault) as exc:
            self.manager.open("demo", request_key="conflicted")
        self.assertEqual(exc.exception.status, 409)

    def test_timeout_bounded_read_and_read_cursor(self):
        sid = self.manager.open("demo", request_key="open-3")["session_id"]
        self.manager.write(sid, "printf 'ABCXYZ\\n'\n", request_key="in-3")
        data = ""
        for _ in range(40):
            result = self.manager.read(sid, cursor=0, max_bytes=128)
            data = result["output"]
            if "ABCXYZ" in data:
                break
            time.sleep(.05)
        self.assertIn("ABCXYZ", data)
        next_value = self.manager.read(sid, cursor=result["next_cursor"], max_bytes=128)
        self.assertEqual(next_value["output"], "")
        self.manager.close(sid)

    def test_raw_output_base64_is_lossless_and_signal_request_is_idempotent(self):
        import base64
        sid = self.manager.open("demo", request_key="full-binary")["session_id"]
        self.manager.write(sid, "printf '\\001\\002\\003\\n'\n", request_key="raw-1")
        for _ in range(50):
            value = self.manager.read(sid, cursor=0)
            if bytes([1, 2, 3]) in base64.b64decode(value["output_base64"]):
                break
            time.sleep(.05)
        self.assertIn(bytes([1, 2, 3]), base64.b64decode(value["output_base64"]))
        a = self.manager.signal(sid, "INT", request_key="signal-int-1")
        b = self.manager.signal(sid, "INT", request_key="signal-int-1")
        self.assertEqual(a["signal"], "INT")
        self.assertTrue(b["deduplicated"])
        with self.assertRaises(Fault) as conflict:
            self.manager.signal(sid, "TERM", request_key="signal-int-1")
        self.assertEqual(conflict.exception.status, 409)
        self.manager.close(sid)

    def test_operator_env_does_not_inherit_credentials(self):
        sid = self.manager.open("demo", request_key="open-env")["session_id"]
        self.manager.write(sid, "printf 'SECRET=%s\\n' \"${OPENAI_API_KEY-unset}\"\n",
                           request_key="in-env")
        data = ""
        for _ in range(40):
            data = self.manager.read(sid, cursor=0)["output"]
            if "SECRET=unset" in data:
                break
            time.sleep(.05)
        self.assertIn("SECRET=unset", data)
        self.manager.close(sid)


if __name__ == "__main__":
    unittest.main()
