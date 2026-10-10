"""Isolated Serena chatgpt-context MCP fixture used by G4 tests only."""
import json
import os
from pathlib import Path
import sys

argv = sys.argv[1:]
project = Path(argv[argv.index("--project") + 1]) if "--project" in argv else None
context = argv[argv.index("--context") + 1] if "--context" in argv else None

for line in sys.stdin:
    try:
        request = json.loads(line)
    except ValueError:
        continue
    method = request.get("method")
    rid = request.get("id")
    if rid is None:
        continue
    if method == "initialize":
        result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake-serena", "version": "test"}}
    elif method == "tools/list":
        result = {"tools": [
            {"name": "find_symbol", "inputSchema": {"type": "object"},
             "annotations": {"readOnlyHint": True}},
            {"name": "replace_content", "inputSchema": {"type": "object"},
             "annotations": {"readOnlyHint": False}},
            {"name": "replace_in_files", "inputSchema": {"type": "object"},
             "annotations": {"readOnlyHint": False}},
        ]}
    elif method == "tools/call":
        args = request["params"].get("arguments", {})
        name = request["params"].get("name")
        if name == "find_symbol":
            # Mimic Serena's normal project initialization/cache side effects;
            # read-only gateway must confine these to its ephemeral mirror.
            if project is not None:
                cache = project / ".serena" / "cache"
                cache.mkdir(parents=True, exist_ok=True)
                (cache / "index.txt").write_text("ephemeral")
            value = {"name_path_pattern": args.get("name_path_pattern"),
                     "relative_path": args.get("relative_path"),
                     "include_body": args.get("include_body"), "context": context,
                     "exposed_secret": "OPENAI_API_KEY" in os.environ or "GITHUB_TOKEN" in os.environ,
                     "operator_auth_visible": (Path.home() / ".codex" / "auth.json").is_file(),
                     "child_cwd": os.getcwd()}
            result = {"content": [{"type": "text", "text": json.dumps(value)}]}
        elif name == "replace_in_files" and project is not None:
            # Simulate the native dry-run contract: the provider is never
            # authorized to alter the original project.
            if args.get("dry_run") is not True:
                result = {"isError": True, "content": [
                    {"type": "text", "text": "dry_run required"}]}
            else:
                count = sum(args.get("needle", "") in p.read_text()
                            for p in project.glob("*.py"))
                result = {"content": [{"type": "text",
                    "text": f"DRY RUN - {count} matching files"}]}
        elif name == "replace_content" and project is not None:
            path = project / args["relative_path"]
            previous = path.read_text()
            if args.get("mode") != "literal" or args["needle"] not in previous:
                result = {"isError": True, "content": [{"type": "text", "text": "no match"}]}
            else:
                path.write_text(previous.replace(args["needle"], args["repl"]))
                result = {"content": [{"type": "text", "text": "modified"}]}
        else:
            result = {"isError": True, "content": [{"type": "text", "text": "unsafe call"}]}
    else:
        result = {"error": {"code": -32601, "message": "unsupported"}}
    print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}), flush=True)
