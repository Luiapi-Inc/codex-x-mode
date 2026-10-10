"""G3: executable model identity is not implied by a configured thread model."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import io
import sys
from contextlib import redirect_stdout

from bridge.native_protocol import inspect_generated_protocol, NativeProtocolError


class NativeProtocolGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "v2").mkdir()
        self._write("TurnCompletedNotification", "Turn", {"id": {}, "status": {}})
        self._write("ThreadReadResponse", "Turn", {"id": {}, "status": {}})
        self._write("ThreadReadResponse", "Thread", {"model": {
            "description": "Current configured model, not per-turn execution telemetry."
        }}, merge=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, filename, typename, props, *, merge=False):
        p = self.root / "v2" / (filename + ".json")
        o = json.loads(p.read_text()) if merge else {"definitions": {}}
        o["definitions"][typename] = {"properties": props, "type": "object"}
        p.write_text(json.dumps(o))

    def test_schema_with_no_turn_model_does_not_infer_identity_from_thread_model(self):
        cap = inspect_generated_protocol(self.root, "codex-cli fixture")
        self.assertEqual(cap["terminal_model_surface"], "unsupported")
        self.assertFalse(cap["turn_completed_has_model"])
        self.assertFalse(cap["readback_turn_has_model"])
        self.assertTrue(cap["thread_configured_model_only"])
        self.assertFalse(cap["terminal_identity_verified"])
        self.assertTrue(cap["schema_sha256"])

    def test_future_turn_model_is_only_potential_attestation_not_success_proof(self):
        self._write("TurnCompletedNotification", "Turn", {"id": {}, "status": {}, "model": {}})
        cap = inspect_generated_protocol(self.root, "fixture future")
        self.assertEqual(cap["terminal_model_surface"], "turn/completed.turn.model")
        self.assertFalse(cap["terminal_identity_verified"])
        self._write("TurnCompletedNotification", "Turn", {"id": {}, "status": {}})
        self._write("ThreadReadResponse", "Turn", {"id": {}, "status": {}, "model": {}}, merge=True)
        cap = inspect_generated_protocol(self.root, "fixture future")
        self.assertEqual(cap["terminal_model_surface"], "thread/read.turn.model")
        self.assertFalse(cap["terminal_identity_verified"])

    def test_missing_schema_fails_closed(self):
        (self.root / "v2" / "TurnCompletedNotification.json").unlink()
        with self.assertRaises(NativeProtocolError):
            inspect_generated_protocol(self.root, "fixture")

    def test_probe_cli_is_independent_of_private_config(self):
        from bridge import __main__ as entry
        with patch("bridge.native_protocol.probe_native_protocol_cli", return_value={
            "terminal_model_surface": "unsupported", "terminal_identity_verified": False,
        }) as run:
            output = io.StringIO()
            with patch.object(sys, "argv", ["bridge", "--config", "/nonexistent/config", "native-protocol-check"]):
                with redirect_stdout(output):
                    entry.main()
            self.assertFalse(json.loads(output.getvalue())["terminal_identity_verified"])
            run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
