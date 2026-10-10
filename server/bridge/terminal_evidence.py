"""Separates Native turn termination, model attestation, and accepted success.

A response computed correctly or a configured thread model does not prove the
per-turn executed model. This classification remains fail-closed when upstream
app-server omits terminal model telemetry.
"""


def classify_native_terminal(
    *, selected_model, terminal_status, reported_model, reroutes,
    thread_config_matches=None, protocol_capability="unknown",
    model_proof_source="turn/completed.turn.model",
):
    states = {
        "completed": "terminal_completed",
        "failed": "terminal_failed",
        "interrupted": "terminal_interrupted",
    }
    execution = states.get(terminal_status, "unknown")
    source = model_proof_source if model_proof_source in (
        "turn/completed.turn.model", "thread/read.turn.model"
    ) else None
    # No provenance => even a matching string cannot be used as proof.
    if reroutes:
        attestation, reason = "unverified", "native_model_rerouted"
    elif not isinstance(reported_model, str) or not reported_model:
        attestation, reason = "unverified", "terminal_model_absent"
        source = None
    elif reported_model != selected_model:
        attestation, reason = "unverified", "terminal_model_mismatch"
    elif source is None:
        attestation, reason = "unverified", "per_turn_source_unverified"
    else:
        attestation, reason = "verified", "terminal_model_matches_selected"

    return {
        "native_execution": execution,
        "model_attestation": attestation,
        "reason": reason,
        "proof_source": source,
        "thread_configured_model_matches_selected": (
            thread_config_matches if type(thread_config_matches) is bool else None
        ),
        "protocol_capability": protocol_capability,
        "acceptance_verified": execution == "terminal_completed" and attestation == "verified",
    }
