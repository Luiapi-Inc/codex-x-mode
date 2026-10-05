"""Build a Codex-native catalog for the packaged chatgpt-web aliases.

The native model schema changes with Codex releases. Clone metadata from the
installed Codex bundled catalog instead of vendoring a second schema snapshot.
"""
import copy
import json
import os
import subprocess
from pathlib import Path

from .service import _web_model_routes


class CodexCatalogError(RuntimeError):
    pass


def default_catalog_path():
    return Path.home() / ".local" / "share" / "codex-x-mode" / "codex-models.json"


def build_codex_catalog(config):
    command = config.get("codex_command", ["codex"])
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        raise CodexCatalogError("codex_command is invalid")
    try:
        completed = subprocess.run(
            command + ["debug", "models", "--bundled"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CodexCatalogError("Unable to read the installed Codex bundled model catalog") from exc
    if completed.returncode != 0:
        raise CodexCatalogError("Installed Codex could not return its bundled model catalog")
    try:
        bundled = json.loads(completed.stdout)
        native_models = bundled["models"]
    except (ValueError, TypeError, KeyError) as exc:
        raise CodexCatalogError("Installed Codex returned an invalid bundled model catalog") from exc
    if not isinstance(native_models, list):
        raise CodexCatalogError("Installed Codex returned an invalid bundled model catalog")

    by_slug = {
        item.get("slug"): item
        for item in native_models
        if isinstance(item, dict) and isinstance(item.get("slug"), str)
    }
    routes = _web_model_routes()
    aliases = []
    ordered = sorted(routes.items(), key=lambda item: (-item[1]["priority"], item[0]))
    for picker_priority, (alias, route) in enumerate(ordered, start=1):
        native = by_slug.get(route["slug"])
        if native is None:
            raise CodexCatalogError(
                "Installed Codex is missing packaged model metadata for " + route["slug"]
            )
        model = copy.deepcopy(native)
        model["slug"] = alias
        model["display_name"] = "ChatGPT Web — " + route["display_name"]
        model["description"] = "Codex X Mode route through the ChatGPT Web backend."
        model["priority"] = picker_priority
        model["visibility"] = "list"
        aliases.append(model)
    return {"models": aliases}


def write_codex_catalog(config, output=None):
    path = Path(output) if output is not None else default_catalog_path()
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(build_codex_catalog(config), indent=2, ensure_ascii=False) + "\n").encode()
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    return path
