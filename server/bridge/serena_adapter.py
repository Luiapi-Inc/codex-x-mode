"""Full Serena chatgpt-context MCP catalog and isolated per-project execution.

Catalog supports all upstream tool names, but only approved read-only operations
are executable in the first G4 acceptance slice. Mutations, process/shell,
global configuration and project switching require separately verified safety
contracts; they fail closed even when a client sends their names directly.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import tempfile
import time
import uuid

from .core import Fault, project_config, text
from .service import _open_project_path, _raise_path_error

CATALOG = json.loads((Path(__file__).parent / "serena_chatgpt_catalog.json").read_text())
if CATALOG.get("context") != "chatgpt":
    raise RuntimeError("Serena catalog is not from the ChatGPT context")
CATALOG_MAP = {item["name"]: item for item in CATALOG["tools"]}
if len(CATALOG_MAP) != 29:
    raise RuntimeError("Unexpected Serena 1.28.1 chatgpt tool catalog")

PREFIX = "codex_x_serena_"

# Explicit, reviewed read-only subset. Upstream annotations alone are not
# sufficient authorization; activating another project or reading global
# settings changes the security boundary.
SAFE_READ = frozenset((
    "read_file", "list_dir", "find_file", "search_for_pattern",
    "get_symbols_overview", "find_symbol", "find_referencing_symbols",
    "find_implementations", "find_declaration", "get_diagnostics_for_file",
    "read_memory", "list_memories", "initial_instructions",
))
UNSAFE_REMOTE = frozenset((
    "execute_shell_command", "activate_project", "get_current_config",
    "onboarding",
))
MUTATING = frozenset(CATALOG_MAP) - SAFE_READ - UNSAFE_REMOTE
SCOPED_WRITE = frozenset((
    "create_text_file", "replace_content", "replace_symbol_body",
    "insert_after_symbol", "insert_before_symbol", "rename_symbol",
    "safe_delete_symbol",
))
MEMORY_WRITE = frozenset(("write_memory", "delete_memory", "rename_memory", "edit_memory"))
SUPPORTED_WRITE = SCOPED_WRITE | MEMORY_WRITE

TOOLS = [{
    "name": PREFIX + item["name"],
    "description": "Serena (chatgpt context): " + item.get("description", "")[:500],
    "inputSchema": {
        **item["inputSchema"],
        "properties": {
            "project_id": {"type": "string"},
            **item["inputSchema"].get("properties", {}),
            **({"request_key": {"type": "string"}} if item["name"] in SUPPORTED_WRITE else {}),
            **({"expected_sha256": {"type": "string"}} if item["name"] in SCOPED_WRITE else {}),
        },
        "required": [
            "project_id", *item["inputSchema"].get("required", []),
            *(["request_key"] if item["name"] in SUPPORTED_WRITE else []),
            *(["expected_sha256"] if item["name"] in SCOPED_WRITE else []),
        ],
    },
    "annotations": {
        "readOnlyHint": item["name"] in SAFE_READ,
        "destructiveHint": item["name"] in MUTATING,
    },
} for item in CATALOG_MAP.values()]
MAP = {tool["name"]: tool["name"][len(PREFIX):] for tool in TOOLS}

PREVIEW_NAME = "codex_x_serena_preview_replace_in_files"
PREVIEW_TOOL = {
    "name": PREVIEW_NAME,
    "description": (
        "Preview a bounded literal multi-file replacement through Serena context chatgpt "
        "in a private source mirror; always dry-run and never modifies the original project."
    ),
    "inputSchema": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "project_id": {"type": "string"},
            "relative_path": {"type": "string"},
            "needle": {"type": "string"},
            "repl": {"type": "string"},
            "mode": {"type": "string", "enum": ["literal"]},
        },
        "required": ["project_id", "relative_path", "needle", "repl", "mode"],
    },
    "annotations": {"readOnlyHint": True, "destructiveHint": False},
}


def _settings(config):
    value = config.get("serena", {})
    if not isinstance(value, dict):
        raise Fault(503, "Invalid Serena configuration")
    if value.get("context", "chatgpt") != "chatgpt":
        raise Fault(503, "Serena context must be chatgpt")
    if type(value.get("enabled", False)) is not bool or type(value.get("allow_mutations", False)) is not bool:
        raise Fault(503, "Invalid Serena capability toggle")
    timeout = value.get("timeout_seconds", 45)
    if type(timeout) is not int or not 2 <= timeout <= 120:
        raise Fault(503, "Invalid Serena timeout configuration")
    command = value.get("command", ["serena", "start-mcp-server"])
    if not isinstance(command, (list, tuple)) or not command or not all(
        isinstance(part, str) and part and len(part) <= 4096 for part in command
    ):
        raise Fault(503, "Invalid Serena command configuration")
    if "serena" in config and set(value) - {
        "context", "enabled", "allow_mutations", "timeout_seconds", "command"
    }:
        raise Fault(503, "Unexpected Serena configuration fields")
    return value


def enabled(config):
    value = _settings(config)
    return value.get("enabled") is True


def _safe_relative_path(path):
    if not isinstance(path, str) or len(path) > 4096 or "\x00" in path:
        raise Fault(400, "Invalid Serena project-relative path")
    relative = Path(path)
    if relative.is_absolute() or any(part == ".." for part in relative.parts):
        raise Fault(403, "Serena path escapes the project root")
    return path


def _scoped_file(config, project_id, relative_path, *, directory=False):
    path = _safe_relative_path(relative_path)
    _, parent_fd, leaf, _ = _open_project_path(config, project_id, path, directory=directory)
    try:
        if leaf is not None:
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
            try:
                fd = os.open(leaf, flags, dir_fd=parent_fd)
            except OSError as exc:
                _raise_path_error(exc)
            try:
                import stat
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise Fault(400, "Serena file is not regular")
            finally:
                os.close(fd)
    finally:
        os.close(parent_fd)


def _validate_args(config, project_id, name, arguments):
    spec = CATALOG_MAP[name]["inputSchema"]
    if not isinstance(arguments, dict) or set(arguments) - set(spec.get("properties", {})):
        raise Fault(400, "Invalid Serena tool arguments")
    if any(k not in arguments for k in spec.get("required", [])):
        raise Fault(400, "Missing required Serena arguments")
    # Serena is scoped to exactly one configured project per launched child.
    project_config(config, project_id, "read-only")
    for field in ("relative_path",):
        if field in arguments:
            path = arguments[field]
            if not isinstance(path, str) or not path.strip():
                raise Fault(400, "Invalid Serena relative_path")
            if name in ("read_file", "get_symbols_overview", "find_referencing_symbols",
                        "find_implementations", "find_declaration", "get_diagnostics_for_file"):
                _scoped_file(config, project_id, path)
            elif name in ("list_dir",):
                _scoped_file(config, project_id, path, directory=True)
            elif name in ("find_symbol", "search_for_pattern", "find_file"):
                try:
                    _scoped_file(config, project_id, path)
                except Fault as exc:
                    if exc.status != 400:
                        raise
                    _scoped_file(config, project_id, path, directory=True)
            else:
                _safe_relative_path(path)
    for name_field in ("memory_name", "old_name", "new_name"):
        if name_field in arguments:
            val = arguments[name_field]
            if not isinstance(val, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", val) or val in (".", ".."):
                raise Fault(400, "Invalid Serena memory name")
    # Do not allow glob-based searches to escape project scope.
    for field in ("paths_include_glob", "paths_exclude_glob"):
        if field in arguments:
            globs = arguments[field]
            if not isinstance(globs, str) or len(globs) > 512:
                raise Fault(400, "Invalid Serena glob")
            if Path(globs).is_absolute() or ".." in Path(globs).parts:
                raise Fault(403, "Serena glob escapes project root")
    if name in ("find_symbol", "search_for_pattern", "find_file"):
        pass
    return dict(arguments)


def _mirror_project_for_read(root, scratch):
    """Mirror bounded project source to private scratch before starting Serena.

    Serena initializes .serena/project.yml and language-server caches even for
    read-only tools; mirroring prevents those writes in the operator workspace.
    Symlinks and special files are never followed or copied.
    """
    import stat
    root = Path(root)
    target = Path(scratch) / "workspace"
    target.mkdir(mode=0o700)
    total, files_seen = 0, 0
    for directory, children, filenames in os.walk(root, followlinks=False):
        relative = Path(directory).relative_to(root)
        if relative.parts[:2] == (".serena", "cache"):
            children.clear()
            continue
        children[:] = [name for name in children
                       if name not in (".git", "node_modules", ".venv", "__pycache__", ".next")
                       and not (Path(directory) / name).is_symlink()]
        dest_dir = target / relative
        dest_dir.mkdir(parents=True, exist_ok=True)
        for leaf in filenames:
            src = Path(directory) / leaf
            try:
                fd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, "rb") as stream:
                    meta = os.fstat(stream.fileno())
                    if not stat.S_ISREG(meta.st_mode):
                        continue
                    files_seen += 1
                    total += meta.st_size
                    if files_seen > 3000 or total > 32 * 1024 * 1024 or meta.st_size > 1024 * 1024:
                        raise Fault(413, "Serena read-only mirror exceeds project safety budget")
                    data = stream.read(1024 * 1024 + 1)
                    if len(data) != meta.st_size:
                        raise Fault(409, "Project changed during Serena read-only snapshot")
            except OSError as exc:
                _raise_path_error(exc)
            (dest_dir / leaf).write_bytes(data)
    return target


def _private_env():
    # Never leak unrelated service or developer credentials into LSP/Serena.
    allow = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE",
             "SYSTEMROOT", "USER", "SHELL", "TERM", "XDG_CONFIG_HOME",
             "XDG_CACHE_HOME", "XDG_DATA_HOME", "VIRTUAL_ENV")
    return {k: os.environ[k] for k in allow if k in os.environ}


def _rpc_process(command, root, name, arguments, timeout):
    if not isinstance(command, (list, tuple)) or not command or not all(
        isinstance(part, str) and part for part in command
    ):
        raise Fault(503, "Invalid configured Serena executable")
    args = [*command, "--project", str(root), "--context", "chatgpt",
            "--transport", "stdio", "--enable-web-dashboard", "False",
            "--enable-gui-log-window", "False", "--open-web-dashboard", "False",
            "--log-level", "ERROR"]
    # An environment allowlist does not protect operator credentials if HOME
    # remains the real user's directory. Serena/LSP children get a disposable
    # home and XDG roots, independent of private Codex/OpenAI auth on disk.
    private_home = tempfile.TemporaryDirectory(prefix="codex-serena-child-home-")
    env = _private_env()
    env["HOME"] = private_home.name
    for key, suffix in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_DATA_HOME", "data"),
    ):
        folder = Path(private_home.name) / suffix
        folder.mkdir(mode=0o700)
        env[key] = str(folder)
    try:
        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, start_new_session=True,
                                cwd=str(root), env=env, bufsize=0)
    except OSError as exc:
        private_home.cleanup()
        raise Fault(503, "Serena provider unavailable") from exc
    buffer = bytearray()
    deadline = time.monotonic() + timeout
    counter = 0

    def exchange(method, params, id_):
        nonlocal buffer
        payload = json.dumps({"jsonrpc": "2.0", "id": id_, "method": method, "params": params}).encode()+b"\n"
        try:
            proc.stdin.write(payload)
            proc.stdin.flush()
        except OSError as exc:
            raise Fault(503, "Serena provider disconnected") from exc
        while time.monotonic() < deadline:
            if b"\n" in buffer:
                line, _, remainder = buffer.partition(b"\n")
                buffer = bytearray(remainder)
                try:
                    result = json.loads(line)
                except ValueError:
                    continue
                if result.get("id") == id_:
                    if "error" in result:
                        raise Fault(503, "Serena MCP returned an error")
                    return result.get("result")
                continue
            timeout_left = min(0.4, max(0.0, deadline-time.monotonic()))
            if proc.poll() is not None and not select.select([proc.stdout], [], [], 0)[0]:
                raise Fault(503, "Serena provider terminated")
            ready, _, _ = select.select([proc.stdout], [], [], timeout_left)
            if ready:
                block = os.read(proc.stdout.fileno(), 65536)
                if not block:
                    raise Fault(503, "Serena provider closed the response stream")
                buffer.extend(block)
                if len(buffer) > 2 * 1024 * 1024:
                    raise Fault(413, "Serena MCP response too large")
        raise Fault(503, "Serena provider timed out")

    try:
        init = exchange("initialize", {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "codex-x-mode-g4", "version": "1.0.0"},
        }, 1)
        if not isinstance(init, dict) or "tools" not in init.get("capabilities", {}):
            raise Fault(503, "Serena MCP initialization invalid")
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized",
                                     "params": {}}).encode()+b"\n")
        proc.stdin.flush()
        listing = exchange("tools/list", {}, 2)
        active = {x["name"]: x for x in listing.get("tools", [])}
        if name not in active or active[name].get("annotations", {}).get("readOnlyHint") is not (name in SAFE_READ):
            raise Fault(503, "Serena declared tool capability mismatches policy")
        result = exchange("tools/call", {"name": name, "arguments": arguments}, 3)
        if not isinstance(result, dict) or result.get("isError"):
            raise Fault(503, "Serena symbolic operation unavailable")
        return result
    finally:
        # Kill the entire isolated process group, even when the MCP parent has
        # exited. Language-server grandchildren must not outlive writer release.
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired as exc:
            raise Fault(503, "Serena process group did not terminate") from exc
        if proc.stdin:proc.stdin.close()
        if proc.stdout:proc.stdout.close()
        private_home.cleanup()


def _project_fingerprint(root):
    """Bounded, symlink-safe project change inventory for reviewable edit receipts."""
    import stat
    entries = {}
    size = 0
    for dirpath, directories, files in os.walk(root, followlinks=False):
        directories[:] = [item for item in directories
                          if item not in (".git", "node_modules", ".venv", "__pycache__", ".next")]
        if Path(dirpath).relative_to(root).parts[:2] == (".serena", "cache"):
            directories.clear()
            continue
        for leaf in sorted(files):
            file = Path(dirpath) / leaf
            metadata = file.lstat()
            key = file.relative_to(root).as_posix()
            if stat.S_ISLNK(metadata.st_mode):
                entries[key] = "symlink"
            elif stat.S_ISREG(metadata.st_mode):
                size += metadata.st_size
                if len(entries) >= 3000 or size > 32 * 1024 * 1024:
                    raise Fault(413, "Project is too large for safe Serena change inventory")
                try:
                    fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    with os.fdopen(fd, "rb") as stream:
                        descriptor = os.fstat(stream.fileno())
                        if not stat.S_ISREG(descriptor.st_mode) or descriptor.st_size > 1024 * 1024:
                            raise Fault(413, "Project file exceeds Serena inspection limit")
                        entries[key] = hashlib.sha256(stream.read(1024 * 1024 + 1)).hexdigest()
                except OSError as exc:
                    _raise_path_error(exc)
            else:
                raise Fault(403, "Special project file prevents Serena mutation")
    return entries


def _validate_write_target(config, project_id, name, params, expected_sha256):
    if name not in SCOPED_WRITE:
        if expected_sha256 is not None:
            raise Fault(400, "Memory edits do not accept a file SHA-256")
        return None
    path = params.get("relative_path")
    if not isinstance(path, str) or not path or not path.strip():
        raise Fault(400, "Serena file edit requires relative_path")
    if name == "create_text_file":
        if expected_sha256 != "absent":
            raise Fault(400, "New Serena files require expected_sha256='absent'")
        _, parent_fd, leaf, _ = _open_project_path(config, project_id, path, directory=False)
        try:
            try:
                os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return None
            raise Fault(409, "Serena target already exists")
        finally:
            os.close(parent_fd)
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise Fault(400, "Existing Serena files require exact SHA-256")
    from .service import read_project_file
    result = read_project_file(config, project_id, path)
    actual = hashlib.sha256(result["content"].encode("utf-8")).hexdigest()
    if actual != expected_sha256:
        raise Fault(409, "Serena target changed since expected_sha256")
    return actual


def _run_write(config, store, project_id, name, arguments):
    if not isinstance(store, object) or store is None:
        raise Fault(503, "Durable Serena writer store is required")
    settings = config.get("serena", {})
    if settings.get("allow_mutations") is not True:
        raise Fault(403, "Serena mutations are not enabled")
    if config.get("mcp_policy", {}).get("mode") != "explicit":
        raise Fault(403, "Serena writes require exact MCP capability grant")
    project = project_config(config, project_id, "workspace-write")
    if name not in SUPPORTED_WRITE:
        raise Fault(403, "Serena privileged tool has no approved write contract")
    if not isinstance(arguments, dict):
        raise Fault(400, "Serena write arguments must be object")
    key = text(arguments.get("request_key"), "request_key", 200)
    expected = arguments.get("expected_sha256")
    arguments = {k:v for k,v in arguments.items()
                 if k not in ("request_key", "expected_sha256")}
    params = _validate_args(config, project_id, name, arguments)
    root = Path(project["cwd"])
    digest = hashlib.sha256(json.dumps(
        {"project_id": project_id, "tool": name, "arguments": params,
         "expected_sha256": expected},
        sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    request_id = project_id + ":" + key
    with store._immediate_transaction():
        store.db.execute("""CREATE TABLE IF NOT EXISTS serena_write_requests (
            request_key TEXT PRIMARY KEY, digest TEXT NOT NULL,
            state TEXT NOT NULL, result TEXT, claim_id TEXT NOT NULL
        )""")
        prior = store.db.execute("SELECT digest,state,result FROM serena_write_requests WHERE request_key=?",
                                 (request_id,)).fetchone()
        if prior is not None:
            if prior["digest"] != digest:
                raise Fault(409, "Serena request_key reused with different input")
            if prior["state"] == "completed":
                return json.loads(prior["result"])
            raise Fault(409, "Serena write request is pending or unknown; do not replay")
        old_hash = _validate_write_target(config, project_id, name, params, expected)
        before = _project_fingerprint(root)
        aliases = [alias for alias, entry in config.get("projects", {}).items()
                   if Path(entry["cwd"]).resolve() == root]
        thread = "serena_" + uuid.uuid4().hex
        turn = "serena_tool_" + uuid.uuid4().hex
        claim = store.acquire_app_writer(resource_id=str(root), project_id=project_id,
                                         resource_project_ids=aliases, thread_id=thread)
        store.set_app_writer_turn(claim["id"], thread, turn)
        store.db.execute(
            "INSERT INTO serena_write_requests(request_key,digest,state,result,claim_id) VALUES(?,?,'pending',NULL,?)",
            (request_id, digest, claim["id"]),
        )
    try:
        timeout = settings.get("timeout_seconds", 45)
        if type(timeout) is not int or not 2 <= timeout <= 120:
            raise Fault(503, "Invalid Serena timeout configuration")
        response = _rpc_process(settings.get("command", ["serena", "start-mcp-server"]),
                                root, name, params, timeout)
        after = _project_fingerprint(root)
        modified = sorted(set(before) | set(after))
        changed = [
            {"path": path, "before_sha256": before.get(path), "after_sha256": after.get(path)}
            for path in modified if before.get(path) != after.get(path)
        ]
        if len(changed) > 100:
            raise Fault(503, "Serena changed more files than permitted by G4 safety policy")
        if name in SCOPED_WRITE and old_hash is not None:
            if name != "rename_symbol" and not any(x["path"] == params["relative_path"] for x in changed):
                # A no-op edit is valid but must still have a matching CAS at invocation.
                pass
        outcome = {
            "provider": "serena", "context": "chatgpt", "tool": name,
            "project_id": project_id, "applied": True, "changed_files": changed,
            "writer_claim_id": claim["id"], "request_key": key,
            "read_only": False, "provider_content": response.get("content", []),
        }
        with store._immediate_transaction():
            store.finish_app_writer(claim["id"], "completed", {
                "terminal_status": "completed", "thread_id": thread,
                "turn_id": turn, "serena_rpc": "tools/call",
                "process_group_stopped": True, "changed_file_count": len(changed),
            })
            store.db.execute(
                "UPDATE serena_write_requests SET state='completed',result=? WHERE request_key=? AND state='pending'",
                (json.dumps(outcome, sort_keys=True), request_id),
            )
        return outcome
    except Exception:
        # Ambiguous side effects remain blocked for reconciliation; retrying
        # the same request never launches the provider a second time.
        store.mark_app_writer_unknown(claim["id"], {
            "reason": "Serena write outcome not independently verified",
            "provider": "serena", "tool": name, "thread_id": thread, "turn_id": turn,
        })
        raise


def preview_replace_in_files(config, project_id, arguments):
    """Request Serena's native dry-run exclusively against an ephemeral mirror.

    Even a misbehaving Serena implementation that ignores dry_run cannot mutate
    the original project, since it receives only the private snapshot path.
    """
    if not enabled(config):
        raise Fault(403, "Serena disabled")
    if not isinstance(arguments, dict) or set(arguments) != {
        "relative_path", "needle", "repl", "mode"
    }:
        raise Fault(400, "Unexpected Serena multi-file preview arguments")
    if arguments["mode"] != "literal":
        raise Fault(400, "Serena multi-file preview currently supports only literal mode")
    needle = text(arguments["needle"], "needle", 1000)
    repl = arguments["repl"]
    if not isinstance(repl, str) or len(repl) > 10000:
        raise Fault(400, "Invalid Serena preview replacement")
    path = arguments["relative_path"]
    if not isinstance(path, str) or not path.strip():
        raise Fault(400, "Invalid Serena preview path")
    _safe_relative_path(path)
    # The target may be an individual file or directory, but it must be
    # contained in an explicitly allowed project without following symlinks.
    try:
        _scoped_file(config, project_id, path)
    except Fault as exc:
        if exc.status != 400:
            raise
        _scoped_file(config, project_id, path, directory=True)
    settings = _settings(config)
    root = Path(project_config(config, project_id, "read-only")["cwd"])
    with tempfile.TemporaryDirectory(prefix="codex-serena-multifile-preview-") as staging:
        mirror = _mirror_project_for_read(root, staging)
        response = _rpc_process(
            settings.get("command", ["serena", "start-mcp-server"]),
            mirror, "replace_in_files", {
                "relative_path": path, "needle": needle, "repl": repl,
                "mode": "literal", "dry_run": True, "max_answer_chars": 65536,
            }, settings.get("timeout_seconds", 45),
        )
    content = response.get("content") or []
    if not isinstance(content, list):
        raise Fault(503, "Invalid Serena dry-run response")
    parts = [item["text"] for item in content
             if isinstance(item, dict) and item.get("type") == "text"
             and isinstance(item.get("text"), str)]
    text_value = "\n".join(parts)
    if len(text_value.encode("utf-8")) > 65536:
        raise Fault(413, "Serena multi-file preview output too large")
    return {
        "provider": "serena", "context": "chatgpt",
        "project_id": project_id, "tool": "replace_in_files",
        "dry_run": True, "applied": False, "read_only": True,
        "preview_text": text_value,
    }


def call_tool(config, project_id, name, arguments, store=None):
    if not enabled(config):
        raise Fault(403, "Serena disabled")
    if name not in CATALOG_MAP:
        raise Fault(404, "Unknown Serena tool")
    if name not in SAFE_READ:
        if name in SUPPORTED_WRITE:
            return _run_write(config, store, project_id, name, arguments)
        raise Fault(403, "Serena operation needs a separate privileged execution contract")
    # Transitional alias accepted by the first G4 wrapper; native catalog
    # itself consistently uses relative_path.
    if name == "find_symbol" and isinstance(arguments, dict) and "path" in arguments:
        if "relative_path" in arguments:
            raise Fault(400, "Conflicting Serena path arguments")
        arguments = {("relative_path" if k == "path" else k): v for k, v in arguments.items()}
    parameters = _validate_args(config, project_id, name, arguments)
    settings = config.get("serena", {})
    timeout = settings.get("timeout_seconds", 45)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 2 or timeout > 120:
        raise Fault(503, "Invalid Serena timeout configuration")
    command = settings.get("command", ["serena", "start-mcp-server"])
    root = Path(project_config(config, project_id, "read-only")["cwd"])
    with tempfile.TemporaryDirectory(prefix="codex-serena-readonly-") as staging:
        shadow = _mirror_project_for_read(root, staging)
        result = _rpc_process(command, shadow, name, parameters, timeout)
    content = result.get("content") or []
    text_parts = [item["text"] for item in content if isinstance(item, dict) and
                  item.get("type") == "text" and isinstance(item.get("text"), str)]
    body = "\n".join(text_parts)
    if len(body.encode("utf-8")) > 1024 * 1024:
        raise Fault(413, "Serena symbolic output exceeds limit")
    return {
        "provider": "serena", "context": "chatgpt", "project_id": project_id,
        "tool": name, "symbols_text": body,
        "content": content,
        "read_only": True,
    }


def find_symbol(config, project_id, path, name_path_pattern, *, timeout_seconds=None):
    text(name_path_pattern, "name_path_pattern", 200)
    if timeout_seconds is not None:
        raise Fault(400, "Per-request Serena timeout override is not allowed")
    return call_tool(config, project_id, "find_symbol", {
        "name_path_pattern": name_path_pattern,
        "relative_path": path,
        "include_body": False,
    })
