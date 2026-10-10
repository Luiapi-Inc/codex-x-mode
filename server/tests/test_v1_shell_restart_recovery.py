"""G5.2 restart observation: durable claims survive without unsafe auto-reattach."""
import tempfile
import unittest
from pathlib import Path

from bridge.core import Fault, Store
from bridge.shell_pty import ShellManager


class ShellRestartRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "project").mkdir()
        self.store = Store(self.root / "private.sqlite3")
        self.config = {"projects": {"demo": {
            "cwd": str(self.root / "project"), "allow_write": True}},
            "shell": {"enabled": True, "executable": "/bin/sh"}}
        self.owner = ShellManager(self.config, self.store)

    def tearDown(self):
        self.owner.shutdown()
        self.store.close()
        self.tmp.cleanup()

    def test_detached_record_does_not_reattach_replay_or_release_active_writer(self):
        sid = self.owner.open("demo", request_key="restart-open")["session_id"]
        observer = ShellManager(self.config, self.store)
        try:
            report = observer.status(sid)
            self.assertEqual(report["state"], "detached_unverified")
            self.assertFalse(report["active"])
            self.assertTrue(report["reconciliation_required"])
            self.assertFalse(report["can_reattach"])
            detached = observer.list()["detached_sessions"]
            self.assertEqual([x["session_id"] for x in detached], [sid])
            self.assertTrue(self.store.app_writer_claim_for_thread(sid))
            with self.assertRaises(Fault) as replay:
                observer.open("demo", request_key="restart-open")
            self.assertEqual(replay.exception.status, 409)
            with self.assertRaises(Fault) as writer:
                observer.open("demo", request_key="other-write")
            self.assertEqual(writer.exception.status, 409)
        finally:
            observer.shutdown()
        self.assertTrue(self.owner.status(sid)["active"])
        self.owner.close(sid)
        observer = ShellManager(self.config, self.store)
        try:
            historical = observer.status(sid)
            self.assertEqual(historical["state"], "completed" if
                             historical["exit_code"] == 0 else "failed")
            self.assertFalse(historical["reconciliation_required"])
            self.assertFalse(historical["can_reattach"])
            self.assertFalse(self.store.app_writer_claim_for_thread(sid))
        finally:
            observer.shutdown()

    def test_stale_starting_record_is_not_auto_reconciled(self):
        claim = self.store.acquire_app_writer(
            resource_id=str(self.root / "project"), project_id="demo",
            resource_project_ids=["demo"], thread_id="shell_stale_start")
        self.store.set_app_writer_turn(claim["id"], "shell_stale_start", "shell_stale_start")
        with self.store._immediate_transaction():
            self.store.db.execute(
                "INSERT INTO operator_shell_sessions "
                "(id,request_key,project_id,claim_id,columns,rows,state,created) "
                "VALUES(?,?,?,?,?,?,?,?)",
                ("shell_stale_start", "interrupted-start", "demo", claim["id"],
                 80, 24, "starting", 0),
            )
        observer = ShellManager(self.config, self.store)
        try:
            report = observer.status("shell_stale_start")
            self.assertEqual(report["state"], "detached_unverified")
            self.assertTrue(report["reconciliation_required"])
            self.assertEqual(
                self.store.db.execute(
                    "SELECT state FROM app_writer_claims WHERE id=?",
                    (claim["id"],)).fetchone()["state"], "running")
        finally:
            observer.shutdown()

    def test_unknown_session_does_not_claim_recovery(self):
        with self.assertRaises(Fault) as denied:
            self.owner.status("shell_nonexistent")
        self.assertEqual(denied.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
