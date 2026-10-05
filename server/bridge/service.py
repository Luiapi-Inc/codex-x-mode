import errno
import json
import os
import secrets
import stat
from pathlib import Path

from .codex import list_models as codex_list_models
from .core import Fault, encoded, fields, project_config, text
from . import siwc


MAX_FILE_BYTES = 1024 * 1024
MAX_DIR_ENTRIES = 500


def public_job(job):
    return {key: job[key] for key in ("id", "state", "result", "thread_id", "turn_id", "created")}


def status(config, store):
    unknown = store.unknown_projects()
    authorization = siwc.authorization_status(config) if config.get("siwc_credentials_file") or config.get("_config_path") else {"state": "authorization_required"}
    return {
        "status": "up",
        "version": "0.2.11",
        "live_codex_verified": False,
        "projects": len(config.get("projects", {})),
        "unknown_projects": unknown,
        "unknown_tasks": store.unknown_tasks() if hasattr(store, "unknown_tasks") else [],
        "readiness": {
            "mcp_transport": "available",
            "codex_runtime": "unverified",
            "web_executor": "implemented_unverified",
            "model_identity": "unverified",
            "end_to_end": "unverified",
        },
        "execution_backends": {
            "local_stdio": "codex_app_server",
            "web_http": "chatgpt_plan",
        },
        "chatgpt_plan_authorization": authorization,
        "capabilities": {
            "backend": ["claim", "context", "complete", "cancel"],
            "dispatch": ["models", "create", "read", "continue", "cancel"],
            "project_reads": ["list_directory", "read_file"],
            "mcp": ["tools", "resources", "prompts"],
        },
    }


def list_projects(config):
    return {
        "projects": [
            {"id": key, "allow_write": bool(value.get("allow_write"))}
            for key, value in sorted(config.get("projects", {}).items())
        ]
    }


def list_models(config):
    if _backend(config) == "chatgpt_plan":
        try:
            return siwc.list_models(config)
        except (siwc.SiwcError, OSError) as exc:
            raise Fault(503, str(exc) if isinstance(exc, siwc.SiwcError) else "Protected credentials are unavailable") from None
    return codex_list_models(config)


def _backend(config):
    return "chatgpt_plan" if config.get("_dispatch_origin") == "web" else "codex_app_server"


def _retry_task(store, request_key, payload):
    """Recover original request identity before any remote credential/catalog read."""
    prior = store.find_by_request("task", request_key)
    if prior is None:
        return None
    original = prior["payload"]
    # Selection and account binding are derived once on acceptance. An omitted
    # model must keep that original selection even if the configured default changes.
    keys = ("project_id", "prompt", "scope", "parent_task_id", "model_version", "resource_id")
    if any(original.get(key) != payload.get(key) for key in keys) or original.get("execution_backend", "codex_app_server") != payload["execution_backend"]:
        raise Fault(409, "Request key already used for different input")
    return public_job(prior)


def _accept_task(config, store, payload, request_key):
    text(request_key, "request_key", 200)
    payload["execution_backend"] = _backend(config)
    # Serialize acceptance around the catalog read so concurrent duplicates never
    # observe different derived selection/account snapshots or create two jobs.
    with store.lock:
        prior = _retry_task(store, request_key, payload)
        if prior is not None:
            return prior
        if payload["execution_backend"] == "chatgpt_plan":
            try:
                credentials = siwc.get_credentials(config)
                parent_id = payload.get("parent_task_id")
                if parent_id:
                    parent = store.get(parent_id, "task")["payload"]
                    if parent.get("execution_backend", "codex_app_server") != "chatgpt_plan" or parent.get("siwc_registration") != siwc.registration(credentials):
                        raise Fault(409, "Follow-up cannot change the parent execution backend or account registration")
                catalog = siwc.list_models(config, credentials=credentials)
                payload["selected_model"] = siwc.select_model(catalog, payload.get("model_version"), config.get("chatgpt_plan_default_model"))
                payload["siwc_registration"] = siwc.registration(credentials)
            except (siwc.SiwcError, OSError) as exc:
                raise Fault(503, str(exc) if isinstance(exc, siwc.SiwcError) else "Protected credentials are unavailable") from None
        return public_job(store.create("task", payload, request_key))


def _resource_identity(config, project_id, project):
    root = Path(project["cwd"]).resolve(strict=True)
    aliases = []
    for alias, value in config.get("projects", {}).items():
        try:
            if Path(value["cwd"]).resolve(strict=True) == root:
                aliases.append(alias)
        except (KeyError, OSError, RuntimeError):
            continue
    return str(root), sorted(aliases or [project_id])


def _project_root(config, project_id):
    project = project_config(config, project_id, "read-only")
    try:
        root = Path(project["cwd"]).resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise Fault(409, "Configured project path is unavailable") from exc
    if not root.is_dir():
        raise Fault(409, "Configured project path is not a directory")
    return root


def _directory_open_flags():
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise Fault(503, "Secure project reads are unsupported on this platform")
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _raise_path_error(exc):
    if isinstance(exc, FileNotFoundError):
        raise Fault(404, "Path not found") from exc
    if isinstance(exc, PermissionError) or getattr(exc, "errno", None) == errno.ELOOP:
        raise Fault(403, "Path cannot be opened without following symlinks") from exc
    if isinstance(exc, NotADirectoryError):
        raise Fault(400, "Path component is not a directory") from exc
    raise Fault(403, "Project path cannot be opened") from exc


def _open_project_path(config, project_id, relative_path, *, directory):
    text(project_id, "project_id", 200)
    if relative_path is None:
        relative_path = "."
    if not isinstance(relative_path, str) or len(relative_path) > 4096 or "\x00" in relative_path:
        raise Fault(400, "Invalid path")
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise Fault(400, "Path must be project-relative")
    components = tuple(part for part in candidate.parts if part not in ("", "."))
    if any(part == ".." for part in components):
        raise Fault(403, "Path escapes project root")
    if not directory and not components:
        raise Fault(400, "File path is required")

    root = _project_root(config, project_id)
    flags = _directory_open_flags()
    try:
        current_fd = os.open(root, flags)
    except OSError as exc:
        _raise_path_error(exc)
    parent_components = components if directory else components[:-1]
    try:
        for component in parent_components:
            try:
                next_fd = os.open(component, flags, dir_fd=current_fd)
            except OSError as exc:
                _raise_path_error(exc)
            os.close(current_fd)
            current_fd = next_fd
    except Exception:
        os.close(current_fd)
        raise
    relative = "/".join(components) if components else "."
    leaf = None if directory else components[-1]
    return root, current_fd, leaf, relative


def list_project_directory(config, project_id, path=".", limit=200):
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > MAX_DIR_ENTRIES:
        raise Fault(400, f"limit must be between 1 and {MAX_DIR_ENTRIES}")
    _, directory_fd, _, relative = _open_project_path(config, project_id, path, directory=True)
    entries = []
    truncated = False
    try:
        with os.scandir(os.dup(directory_fd)) as children:
            for child in children:
                if len(entries) == limit:
                    truncated = True
                    break
                try:
                    if child.is_symlink():
                        kind = "symlink"
                    elif child.is_dir(follow_symlinks=False):
                        kind = "directory"
                    elif child.is_file(follow_symlinks=False):
                        kind = "file"
                    else:
                        kind = "other"
                except OSError:
                    kind = "unavailable"
                entries.append({"name": child.name, "type": kind})
    except OSError as exc:
        _raise_path_error(exc)
    finally:
        os.close(directory_fd)
    entries.sort(key=lambda item: item["name"])
    return {"project_id": project_id, "path": relative, "entries": entries, "truncated": truncated}


def read_project_file(config, project_id, path, max_bytes=262144):
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1 or max_bytes > MAX_FILE_BYTES:
        raise Fault(400, f"max_bytes must be between 1 and {MAX_FILE_BYTES}")
    _, parent_fd, leaf, relative = _open_project_path(config, project_id, path, directory=False)
    file_fd = None
    try:
        file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            file_fd = os.open(leaf, file_flags, dir_fd=parent_fd)
        except OSError as exc:
            _raise_path_error(exc)
        metadata = os.fstat(file_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise Fault(400, "Path is not a regular file")
        if metadata.st_size > max_bytes:
            raise Fault(413, f"File exceeds max_bytes ({metadata.st_size} > {max_bytes})")
        with os.fdopen(file_fd, "rb") as source:
            file_fd = None
            raw = source.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise Fault(413, f"File exceeds max_bytes ({len(raw)} > {max_bytes})")
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Fault(415, "File is not UTF-8 text") from exc
    except OSError as exc:
        _raise_path_error(exc)
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)
    return {"project_id": project_id, "path": relative, "size_bytes": len(raw), "content": content}


def _claim_record(store, request_key):
    # Persist both successful and null claim results so retries after a lost
    # response cannot capture a later backend turn.
    store.db.execute("""CREATE TABLE IF NOT EXISTS backend_claim_requests (
        request_key TEXT PRIMARY KEY,
        job_id TEXT
    )""")
    return store.db.execute(
        "SELECT job_id FROM backend_claim_requests WHERE request_key=?",
        (request_key,),
    ).fetchone()


def claim_backend(store, body):
    fields(body, ("request_key",))
    request_key = text(body["request_key"], "request_key", 200)
    with store.lock, store.db:
        prior = _claim_record(store, request_key)
        if prior is not None:
            job_id = prior["job_id"]
            if job_id is None:
                return {"turn": None}
            job = store.get(job_id, "backend")
            if job.get("claim_key") != request_key or job["state"] != "claimed":
                raise Fault(409, "Claim request key already used")
        else:
            # Insert the retry identity before the underlying claim. If the
            # process fails after the claim but before the mapping is updated,
            # recovery fails closed to null instead of taking another turn.
            store.db.execute(
                "INSERT INTO backend_claim_requests(request_key,job_id) VALUES (?,NULL)",
                (request_key,),
            )
            job = store.claim("backend", request_key=request_key)
            if job:
                store.db.execute(
                    "UPDATE backend_claim_requests SET job_id=? WHERE request_key=?",
                    (job["id"], request_key),
                )
    result = None if not job else {
        "id": job["id"],
        "lease": job["lease"],
        "expires": job["expires"],
        "context_length": len(encoded(job["payload"])),
    }
    return {"turn": result}


def read_backend_context(store, job_id, lease, offset=0):
    text(lease, "lease", 200)
    if not isinstance(offset, int) or isinstance(offset, bool):
        raise Fault(400, "Invalid offset")
    job = store.get(job_id, "backend")
    if not job["lease"] or not secrets.compare_digest(lease, job["lease"]):
        raise Fault(403, "Invalid claim")
    if job["state"] != "claimed":
        raise Fault(409, "Claim the turn before reading context")
    snapshot = encoded(job["payload"])
    if offset < 0 or offset > len(snapshot):
        raise Fault(400, "Offset outside context")
    end = min(offset + 20000, len(snapshot))
    return {
        "id": job["id"],
        "offset": offset,
        "chunk": snapshot[offset:end],
        "next_offset": end if end < len(snapshot) else None,
        "total": len(snapshot),
    }


def complete_backend(store, job_id, body):
    job = store.finish_backend(job_id, body)
    return {"id": job["id"], "state": job["state"]}


def cancel_backend(store, job_id, body):
    fields(body, ("lease", "request_key"))
    job = store.cancel_backend(job_id, body["lease"], body["request_key"])
    return {"id": job["id"], "state": job["state"]}


def create_task(config, store, body):
    fields(body, ("project_id", "prompt", "scope", "request_key"), ("model_version",))
    text(body["prompt"], "prompt")
    text(body["project_id"], "project_id", 200)
    project = project_config(config, body["project_id"], body["scope"])
    payload = {key: body[key] for key in ("project_id", "prompt", "scope")}
    if "model_version" in body:
        payload["model_version"] = text(body["model_version"], "model_version", 200)
    payload["resource_id"], payload["resource_project_ids"] = _resource_identity(
        config, body["project_id"], project)
    return _accept_task(config, store, payload, body["request_key"])


def read_task(store, task_id):
    return public_job(store.get(task_id, "task"))


def continue_task(config, store, task_id, body):
    fields(body, ("prompt", "request_key"), ("model_version",))
    text(body["prompt"], "prompt")
    parent = store.get(task_id, "task")
    payload = {key: value for key, value in parent["payload"].items() if key not in ("selected_model", "siwc_registration", "execution_backend")}
    # Preserve the actual selected model for a follow-up, including a configured
    # default used by a parent whose original request omitted model_version.
    if parent["payload"].get("selected_model"):
        payload["model_version"] = parent["payload"]["selected_model"]["id"]
    elif isinstance(parent.get("result"), dict):
        selection = parent["result"].get("model_selection")
        if isinstance(selection, dict) and selection.get("model_identity_verified") is True:
            payload["model_version"] = selection["id"]
    payload.update(prompt=body["prompt"], parent_task_id=parent["id"])
    if "model_version" in body:
        payload["model_version"] = text(body["model_version"], "model_version", 200)
    project = project_config(config, payload["project_id"], payload["scope"])
    payload["resource_id"], payload["resource_project_ids"] = _resource_identity(
        config, payload["project_id"], project)
    parent_backend = parent["payload"].get("execution_backend", "codex_app_server")
    if parent_backend != _backend(config):
        raise Fault(409, "Follow-up cannot change the parent execution backend")
    return _accept_task(config, store, payload, body["request_key"])


def cancel_task(store, task_id, body):
    fields(body, ("request_key",))
    job = store.cancel_task(task_id, body["request_key"])
    return public_job(job)
