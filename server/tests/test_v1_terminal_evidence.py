"""Native turn completion and executed-model attestation are separate decisions."""
import unittest

from bridge.terminal_evidence import classify_native_terminal


class TerminalEvidenceTests(unittest.TestCase):
    def test_matching_native_terminal_model_is_attested_not_just_configured(self):
        value = classify_native_terminal(
            selected_model="gpt-6.1-sol", terminal_status="completed",
            reported_model="gpt-6.1-sol", reroutes=[],
        )
        self.assertEqual(value["native_execution"], "terminal_completed")
        self.assertEqual(value["model_attestation"], "verified")
        self.assertEqual(value["proof_source"], "turn/completed.turn.model")
        self.assertTrue(value["acceptance_verified"])

    def test_absent_terminal_model_is_not_attested_by_thread_config(self):
        value = classify_native_terminal(
            selected_model="gpt-6.1-sol", terminal_status="completed",
            reported_model=None, reroutes=[], thread_config_matches=True,
        )
        self.assertEqual(value["native_execution"], "terminal_completed")
        self.assertEqual(value["model_attestation"], "unverified")
        self.assertEqual(value["reason"], "terminal_model_absent")
        self.assertFalse(value["acceptance_verified"])
        self.assertIsNone(value["proof_source"])
        self.assertTrue(value["thread_configured_model_matches_selected"])

    def test_reroute_notification_invalidates_acceptance_even_if_terminal_matches(self):
        value = classify_native_terminal(
            selected_model="gpt-6.1-sol", terminal_status="completed",
            reported_model="gpt-6.1-sol",
            reroutes=[{"from_model": "gpt-6.1-sol", "to_model": "gpt-5.5"}],
        )
        self.assertEqual(value["model_attestation"], "unverified")
        self.assertEqual(value["reason"], "native_model_rerouted")
        self.assertFalse(value["acceptance_verified"])

    def test_mismatched_model_and_failed_turn_do_not_count_as_accepted_success(self):
        mismatch = classify_native_terminal(
            selected_model="gpt-6.1-sol", terminal_status="completed",
            reported_model="gpt-5.5", reroutes=[],
        )
        self.assertEqual(mismatch["reason"], "terminal_model_mismatch")
        self.assertFalse(mismatch["acceptance_verified"])
        failed = classify_native_terminal(
            selected_model="gpt-6.1-sol", terminal_status="failed",
            reported_model="gpt-6.1-sol", reroutes=[],
        )
        self.assertEqual(failed["native_execution"], "terminal_failed")
        self.assertEqual(failed["model_attestation"], "verified")
        self.assertFalse(failed["acceptance_verified"])

    def test_unexpected_terminal_status_fails_closed(self):
        value = classify_native_terminal(
            selected_model="gpt-6.1-sol", terminal_status="queued",
            reported_model="gpt-6.1-sol", reroutes=[],
        )
        self.assertEqual(value["native_execution"], "unknown")
        self.assertFalse(value["acceptance_verified"])

    def test_unknown_schema_status_never_implies_verified_runtime(self):
        value = classify_native_terminal(
            selected_model="gpt-6.1-sol", terminal_status="completed",
            reported_model=None, reroutes=[], protocol_capability="unsupported",
        )
        self.assertEqual(value["protocol_capability"], "unsupported")
        self.assertEqual(value["model_attestation"], "unverified")
        self.assertFalse(value["acceptance_verified"])


if __name__ == "__main__":
    unittest.main()
