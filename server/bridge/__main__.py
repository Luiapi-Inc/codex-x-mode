import argparse
import json
import os
import secrets
import subprocess
import threading
import fcntl
import uuid
from pathlib import Path

from .core import Store
from .http import Server
from .mcp import serve_stdio
from .codex import worker
from .schema import dump
from . import siwc


def default_config_path():
    override = os.environ.get("CODEX_X_MODE_CONFIG")
    if override:
        return override
    local = Path("bridge-private.json")
    if local.exists():
        return str(local)
    return str(Path.home() / ".config" / "codex-x-mode" / "bridge-private.json")


def main():
    parser = argparse.ArgumentParser(description="Single-operator Codex / Custom GPT bridge")
    parser.add_argument("--config", default=default_config_path())
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup")
    setup.add_argument("--project", required=True)
    setup.add_argument("--cwd", required=True)
    setup.add_argument("--allow-write", action="store_true")
    setup.add_argument("--model-version", help="Exact ChatGPT plan model slug to use when a web request omits model_version")
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8240)
    commands.add_parser("mcp-stdio")
    export = commands.add_parser("schema")
    export.add_argument("--url", required=True)
    export.add_argument("--output", default="openapi.json")
    key = commands.add_parser("show-key")
    key.add_argument("role", choices=["gpt", "provider", "mcp"])
    codex = commands.add_parser("codex")
    codex.add_argument("--port", type=int, default=8240)
    codex.add_argument("args", nargs=argparse.REMAINDER)
    commands.add_parser("siwc-login")
    commands.add_parser("siwc-status")
    commands.add_parser("siwc-host-id")
    siwc_import = commands.add_parser("siwc-import")
    siwc_import.add_argument("--file", required=True, help="Protected mode-600 credential file transferred securely from the same tool/account")
    args = parser.parse_args()
    os.umask(0o077)
    config_path = Path(args.config).resolve()
    if args.command == "schema":
        Path(args.output).write_text(dump(args.url) + "\n")
        print("OpenAPI written to", args.output)
        return
    if args.command == "setup":
        cwd = Path(args.cwd).resolve(strict=True)
        if not cwd.is_dir():
            parser.error("cwd must be an existing project directory")
        config = {"gpt_key": secrets.token_urlsafe(32), "provider_key": secrets.token_urlsafe(32),
                  "mcp_key": secrets.token_urlsafe(32),
                  "projects": {args.project: {"cwd": str(cwd), "allow_write": args.allow_write}},
                  "codex_command": ["codex"], "backend_timeout_seconds": 600, "task_timeout_seconds": 600,
                  "ext_agent_host_id": "urn:uuid:" + str(uuid.uuid4()),
                  "siwc_credentials_file": str(config_path.with_suffix(".siwc.json"))}
        if args.model_version is not None:
            config["chatgpt_plan_default_model"] = args.model_version
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with config_path.open("x") as output:
            json.dump(config, output, indent=2)
        print("Private configuration created. Do not upload or share it:", config_path)
        return
    if not config_path.exists():
        parser.error(f"Configuration not found: {config_path}. Run setup first.")
    if config_path.stat().st_mode & 0o077:
        parser.error("Private config must have mode 600; run chmod 600 on it")
    config = json.loads(config_path.read_text())
    config = {key: value for key, value in config.items() if not key.startswith("_")}
    config["_config_path"] = str(config_path)
    if any(not isinstance(config.get(name), str) or len(config[name]) < 32 for name in ("gpt_key", "provider_key")):
        parser.error("Invalid authentication keys")
    if config["gpt_key"] == config["provider_key"]:
        parser.error("Use distinct GPT and provider keys")
    if isinstance(config.get("mcp_key"), str) and config["mcp_key"] in (config["gpt_key"], config["provider_key"]):
        parser.error("Use a distinct MCP key")
    if args.command.startswith("siwc-"):
        try:
            if args.command == "siwc-login":
                siwc.login(config)
            elif args.command == "siwc-import":
                print(json.dumps(siwc.import_credentials(config, args.file)))
            elif args.command == "siwc-host-id":
                print(siwc._write_host_id(config))
            else:
                print(json.dumps(siwc.authorization_status(config)))
        except (siwc.SiwcError, OSError) as exc:
            parser.error(str(exc) if isinstance(exc, siwc.SiwcError) else "Protected credential operation failed")
        return
    if args.command == "show-key":
        value = config.get(args.role + "_key")
        if not isinstance(value, str) or len(value) < 32:
            parser.error(f"{args.role} key is not configured")
        print(value)
        return
    if args.command == "codex":
        env = dict(os.environ, CODEX_BRIDGE_PROVIDER_KEY=config["provider_key"])
        extra = args.args[1:] if args.args[:1] == ["--"] else args.args
        command = config["codex_command"] + [
            "-c", 'model_provider="custom_gpt_bridge"',
            "-c", 'model_providers.custom_gpt_bridge.name="Custom GPT Bridge"',
            "-c", 'model_providers.custom_gpt_bridge.wire_api="responses"',
            "-c", f'model_providers.custom_gpt_bridge.base_url="http://127.0.0.1:{args.port}/v1"',
            "-c", 'model_providers.custom_gpt_bridge.env_key="CODEX_BRIDGE_PROVIDER_KEY"',
            "-c", 'model_providers.custom_gpt_bridge.supports_websockets=false',
            "-c", 'model_providers.custom_gpt_bridge.request_max_retries=0',
            "-c", 'model_providers.custom_gpt_bridge.stream_max_retries=0',
            *extra]
        raise SystemExit(subprocess.call(command, env=env))
    lock_file = config_path.with_suffix(".lock").open("a")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error("This configuration already has a running bridge")
    store = Store(config_path.with_suffix(".sqlite3"))
    if args.command == "mcp-stdio":
        stop = threading.Event()
        thread = threading.Thread(target=worker, args=(config, store, stop), daemon=True)
        thread.start()
        try:
            serve_stdio(config, store)
        finally:
            stop.set()
            thread.join(timeout=15)
            if thread.is_alive():
                raise RuntimeError("Worker did not stop; preserve database until recovery")
            store.close()
            lock_file.close()
        return
    server = Server(("127.0.0.1", args.port), config, store)
    print(f"Bridge listening on loopback port {server.server_port}. HTTPS is required for GPT Actions or remote MCP.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()
        lock_file.close()


if __name__ == "__main__":
    main()
