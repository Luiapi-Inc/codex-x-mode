"""Read-only Native Codex protocol attestation capability audit.

Schema fields indicate what an installed app-server *may* report, never proof
that a particular inference actually used the selected executable model.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


class NativeProtocolError(RuntimeError):
    pass


def _schema(root, name):
    path = Path(root) / "v2" / (name + ".json")
    try:
        raw = path.read_bytes()
        if len(raw) > 4 * 1024 * 1024:
            raise NativeProtocolError("Generated Native schema exceeds inspection limit")
        obj = json.loads(raw)
        if not isinstance(obj, dict) or not isinstance(obj.get("definitions"), dict):
            raise ValueError("No definitions")
    except (OSError, ValueError) as exc:
        raise NativeProtocolError("Required Native schema unavailable or invalid: " + name) from exc
    return raw, obj


def _properties(schema, typename):
    node = schema.get("definitions", {}).get(typename)
    if not isinstance(node, dict) or not isinstance(node.get("properties"), dict):
        raise NativeProtocolError("Missing Native protocol type: " + typename)
    return node["properties"]


def inspect_generated_protocol(root, cli_version):
    """Inspect the installed CLI's actual v2 schema, not inferred model routing.

    `terminal_identity_verified` must remain False: schema compatibility is not
    per-execution terminal identity evidence, even if a new field is exposed.
    """
    term_raw, terminal = _schema(root, "TurnCompletedNotification")
    read_raw, read = _schema(root, "ThreadReadResponse")
    terminal_properties = _properties(terminal, "Turn")
    readback_properties = _properties(read, "Turn")
    thread_properties = _properties(read, "Thread")
    if not {"id", "status"}.issubset(terminal_properties) or not {"id", "status"}.issubset(readback_properties):
        raise NativeProtocolError("Required per-turn Native terminal fields unavailable")
    completed_has_model = "model" in terminal_properties
    readback_has_model = "model" in readback_properties
    surface = (
        "turn/completed.turn.model" if completed_has_model else
        "thread/read.turn.model" if readback_has_model else "unsupported"
    )
    # The two raw schemas are hashed together in deterministic name order.
    digest = hashlib.sha256(
        b"ThreadReadResponse.json\0" + read_raw +
        b"\0TurnCompletedNotification.json\0" + term_raw
    ).hexdigest()
    return {
        "codex_cli_version": cli_version,
        "schema_sha256": digest,
        "turn_completed_has_model": completed_has_model,
        "readback_turn_has_model": readback_has_model,
        "thread_configured_model_only": "model" in thread_properties,
        "terminal_model_surface": surface,
        "per_turn_model_attestation_supported_by_schema": surface != "unsupported",
        "terminal_identity_verified": False,
        "acceptance_gate": "UNVERIFIED" if surface != "unsupported" else "UNSUPPORTED",
        "inference_invoked": False,
    }


def probe_native_protocol_cli(command=("codex",), timeout_seconds=30):
    """Generate and inspect the installed CLI schema without invoking inference."""
    if (not isinstance(command, (list, tuple)) or not command or
            any(not isinstance(part, str) or not part for part in command)):
        raise NativeProtocolError("Invalid Native Codex CLI command")
    try:
        version = subprocess.run([*command, "--version"], check=True,
                                 capture_output=True, text=True, timeout=timeout_seconds).stdout.strip()
        with tempfile.TemporaryDirectory(prefix="codex-native-protocol-") as folder:
            subprocess.run([*command, "app-server", "generate-json-schema", "--out", folder],
                           check=True, capture_output=True, timeout=timeout_seconds)
            return inspect_generated_protocol(folder, version)
    except (OSError, subprocess.SubprocessError) as exc:
        raise NativeProtocolError("Installed Native Codex schema probe unavailable") from exc
