"""Read-only ChatGPT Direct workspace edit preview.

This module never mutates the project. Separate apply authorization, durable
writer claims and file-version CAS must be implemented before enabling edits.
"""
import difflib
import hashlib
import re

from .core import Fault
from .service import MAX_FILE_BYTES, read_project_file

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def preview_edit(config, project_id, path, new_content, *, expected_sha256, max_diff_bytes=131072):
    """Build an explicit bounded patch from a secure project-root file read."""
    if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
        raise Fault(400, "expected_sha256 must be a lowercase SHA-256 hex digest")
    if not isinstance(new_content, str):
        raise Fault(400, "new_content must be UTF-8 text")
    try:
        new_raw = new_content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise Fault(400, "new_content contains invalid Unicode") from exc
    if len(new_raw) > MAX_FILE_BYTES:
        raise Fault(413, "New content exceeds allowed file size")
    if (not isinstance(max_diff_bytes, int) or isinstance(max_diff_bytes, bool)
            or not 1 <= max_diff_bytes <= 262144):
        raise Fault(400, "Invalid max_diff_bytes")
    before = read_project_file(config, project_id, path, max_bytes=MAX_FILE_BYTES)
    original = before["content"]
    old_raw = original.encode("utf-8")
    current_sha = hashlib.sha256(old_raw).hexdigest()
    if expected_sha256 != current_sha:
        raise Fault(409, "File content changed; expected_sha256 mismatch")
    preview_lines = difflib.unified_diff(
        original.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{before['path']}", tofile=f"b/{before['path']}",
    )
    diff = "".join(preview_lines)
    if len(diff.encode("utf-8")) > max_diff_bytes:
        raise Fault(413, "Diff exceeds preview size; narrow the requested edit")
    return {
        "project_id": project_id,
        "path": before["path"],
        "old_sha256": current_sha,
        "new_sha256": hashlib.sha256(new_raw).hexdigest(),
        "old_size_bytes": len(old_raw),
        "new_size_bytes": len(new_raw),
        "diff": diff,
        "changed": current_sha != hashlib.sha256(new_raw).hexdigest(),
        "applied": False,
        "scope": "read-only",
    }
