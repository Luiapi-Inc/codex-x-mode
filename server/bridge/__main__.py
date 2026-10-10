import argparse
import json
import os
import secrets
import subprocess
import threading
import fcntl
from pathlib import Path

from .core import Store
from .http import Server
from .mcp import serve_stdio
from .codex import worker
from .schema import dump


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
    setup.add_argument("--model-version", help="Preferred exact chatgpt-web/<version> ID from Native Codex model/list")
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8240)
    serve.add_argument("--admin-port", type=int, help="Opt-in local-only Admin API; requires admin_key")
    serve.add_argument("--shell-port", type=int, help="Opt-in operator-only PTY MCP on loopback; requires shell_key and shell.enabled")
    commands.add_parser("mcp-stdio")
    commands.add_parser("native-protocol-check", help="Inspect installed Native schema; never runs inference")
    commands.add_parser("codex-x-app-mcp-stdio")
    export = commands.add_parser("schema")
    export.add_argument("--url", required=True)
    export.add_argument("--output", default="openapi.json")
    commands.add_parser("config-show", help="Inspect redacted configuration and revision")
    preview = commands.add_parser("config-preview", help="Validate a local JSON patch without saving")
    preview.add_argument("--changes", required=True, help="Path to JSON object with changed top-level keys")
    preview.add_argument("--expected-revision", required=True)
    apply = commands.add_parser("config-apply", help="Apply a validated offline configuration revision")
    apply.add_argument("--changes", required=True)
    apply.add_argument("--expected-revision", required=True)
    apply.add_argument("--confirm", action="store_true", help="Explicitly authorize offline config mutation")
    key = commands.add_parser("show-key")
    key.add_argument("role", choices=["gpt", "provider", "mcp", "shell"])
    codex = commands.add_parser("codex")
    codex.add_argument("--port", type=int, default=8240)
    codex.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    os.umask(0o077)
    config_path = Path(args.config).resolve()
    if args.command == "schema":
        Path(args.output).write_text(dump(args.url) + "\n")
        print("OpenAPI written to", args.output)
        return
    if args.command == "native-protocol-check":
        from .native_protocol import probe_native_protocol_cli, NativeProtocolError
        try:
            result = probe_native_protocol_cli()
        except NativeProtocolError as exc:
            parser.error(str(exc))
        print(json.dumps(result, sort_keys=True))
        return
    if args.command == "setup":
        cwd = Path(args.cwd).resolve(strict=True)
        if not cwd.is_dir():
            parser.error("cwd must be an existing project directory")
        config = {"gpt_key": secrets.token_urlsafe(32), "provider_key": secrets.token_urlsafe(32),
                  "mcp_key": secrets.token_urlsafe(32), "admin_key": secrets.token_urlsafe(32),
                  "shell_key": secrets.token_urlsafe(32),
                  "projects": {args.project: {"cwd": str(cwd), "allow_write": args.allow_write}},
                  "codex_command": ["codex"], "backend_timeout_seconds": 600, "task_timeout_seconds": 600,
                  "config_schema_version": 1,
                  "mcp_policy": {"mode": "read-only"}, "allowed_origins": [],
                  "web_model_policy": "native",
                  "serena": {"enabled": False, "context": "chatgpt",
                             "allow_mutations": False, "timeout_seconds": 45},
                  "shell": {"enabled": False, "executable": "/bin/sh"},
                  }
        if args.model_version is not None:
            config["chatgpt_web_default_model"] = args.model_version
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
    if args.command in ("config-show", "config-preview", "config-apply"):
        from .config_kernel import ConfigKernel, ConfigFault

        kernel = ConfigKernel(config_path)
        try:
            if args.command == "config-show":
                result = kernel.snapshot()
            else:
                patch = Path(args.changes)
                if not patch.is_file() or patch.stat().st_size > 65536:
                    parser.error("Changes must be a local JSON file up to 64 KiB")
                changes = json.loads(patch.read_text())
                if args.command == "config-preview":
                    result = kernel.preview(changes, expected_revision=args.expected_revision)
                else:
                    if not args.confirm:
                        parser.error("Config apply requires explicit --confirm")
                    # Never change disk configuration while the resident
                    # runtime owns its private config lock.
                    runtime_lock = config_path.with_suffix(".lock").open("a")
                    try:
                        try:
                            fcntl.flock(runtime_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            parser.error("Running bridge holds config; stop it before offline apply")
                        result = kernel.apply(changes, expected_revision=args.expected_revision)
                    finally:
                        runtime_lock.close()
        except (ConfigFault, OSError, ValueError) as exc:
            parser.error("Configuration operation rejected: " + str(exc))
        print(json.dumps(result, sort_keys=True))
        return
    if args.command == "show-key":
        value = config.get(args.role + "_key")
        if not isinstance(value, str) or len(value) < 32:
            parser.error(f"{args.role} key is not configured")
        print(value)
        return
    if args.command == "codex":
        # Native Codex owns authentication, catalog selection, and inference.
        # This wrapper must never turn Codex X Mode into a custom Responses provider.
        extra = args.args[1:] if args.args[:1] == ["--"] else args.args
        command = config["codex_command"] + ["-c", 'model_provider="openai"', *extra]
        env = dict(os.environ)
        for key in ("CODEX_BRIDGE_PROVIDER_KEY", "ACCESS_TOKEN", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
            env.pop(key, None)
        raise SystemExit(subprocess.call(command, env=env))
    lock_file = config_path.with_suffix(".lock").open("a")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error("This configuration already has a running bridge")
    store = Store(config_path.with_suffix(".sqlite3"))
    if args.command in ("mcp-stdio", "codex-x-app-mcp-stdio"):
        if args.command == "codex-x-app-mcp-stdio":
            try:
                serve_stdio(config, store, surface="codex_x_app")
            finally:
                store.close()
                lock_file.close()
            return
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
    admin = None
    admin_thread = None
    shell_server = None
    shell_thread = None
    try:
        if args.admin_port is not None:
            if not 1 <= args.admin_port <= 65535 or args.admin_port == server.server_port:
                parser.error("Admin port must be distinct and in range 1..65535")
            from .admin import AdminServer
            admin = AdminServer(("127.0.0.1", args.admin_port), config, store, server,
                                config_path=config_path)
            admin_thread = threading.Thread(target=admin.serve_forever, daemon=True)
            admin_thread.start()
            print(f"Local-only Admin API on 127.0.0.1:{admin.server_port}; remote ingress disabled.")
        if args.shell_port is not None:
            reserved_ports = {server.server_port}
            if admin is not None:
                reserved_ports.add(admin.server_port)
            if not 1 <= args.shell_port <= 65535 or args.shell_port in reserved_ports:
                parser.error("Operator shell port must be distinct and within 1..65535")
            if config.get("shell", {}).get("enabled") is not True:
                parser.error("Operator shell must be enabled in private config")
            from .shell_operator import OperatorShellServer
            shell_server = OperatorShellServer(
                ("127.0.0.1", args.shell_port), config, store,
            )
            shell_thread = threading.Thread(target=shell_server.serve_forever, daemon=True)
            shell_thread.start()
            print(f"Operator-only full PTY MCP listening on 127.0.0.1:{shell_server.server_port}; no tunnel route.")
        print(f"Bridge listening on loopback port {server.server_port}. HTTPS is required for remote MCP.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    finally:
        if shell_server is not None:
            if shell_thread is not None:
                shell_server.shutdown()
                shell_thread.join(timeout=10)
            shell_server.server_close()
        if admin is not None:
            if admin_thread is not None:
                admin.shutdown()
                admin_thread.join(timeout=10)
            admin.server_close()
        server.server_close()
        store.close()
        lock_file.close()


if __name__ == "__main__":
    main()
