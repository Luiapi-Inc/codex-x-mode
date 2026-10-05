"""Bounded, single-request MCP client for this bridge; never retries mutations."""
import json
import os
import queue
import subprocess
import threading
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .mcp import LEGACY_VERSIONS, MODERN_VERSION, SUPPORTED_VERSIONS


MAX_RESPONSE = 4 * 1024 * 1024


class ClientError(Exception):
    """Sanitized transport/protocol error; excludes tokens and request bodies."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ClientError("Redirect refused; no credentials forwarded")


class MCPClient:
    def __init__(self, *, url=None, token=None, command=None, cwd=None, env=None,
                 version="2025-11-25", timeout=10):
        if (url is None) == (command is None):
            raise ValueError("Choose exactly one transport")
        if version not in SUPPORTED_VERSIONS or timeout <= 0:
            raise ValueError("Unsupported protocol or timeout")
        self.version, self.timeout = version, timeout
        self.url, self.token, self.process = url, token, None
        self.counter, self.connected, self.closed = 0, False, False
        self.lock = threading.Lock()
        self.lines = queue.Queue(maxsize=32)
        if url:
            parsed = urlsplit(url)
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("Endpoint must not contain credentials, query or fragment")
            if not (parsed.scheme == "https" or
                    parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "::1", "localhost")):
                raise ValueError("Remote endpoints require HTTPS")
            if not parsed.hostname or not isinstance(token, str) or not token:
                raise ValueError("Endpoint and Bearer token are required")
            self.opener = urllib.request.build_opener(NoRedirect())
        else:
            self.process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self.reader = threading.Thread(target=self._read_lines, daemon=True)
            self.reader.start()

    def _read_lines(self):
        try:
            while not self.closed:
                line = self.process.stdout.readline(MAX_RESPONSE + 1)
                if not line:
                    self.lines.put(None, timeout=self.timeout)
                    return
                if len(line) > MAX_RESPONSE or not line.endswith(b"\n"):
                    self.lines.put(ClientError("Oversized stdio response"), timeout=self.timeout)
                    return
                self.lines.put(line, timeout=self.timeout)
        except (OSError, queue.Full):
            return

    def _exchange(self, request, notification=False):
        payload = json.dumps(request, ensure_ascii=False).encode()
        if self.url:
            headers = {"Authorization": "Bearer " + self.token,
                       "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                       "MCP-Protocol-Version": self.version}
            if self.version == MODERN_VERSION:
                headers["Mcp-Method"] = request["method"]
                params = request["params"]
                name = params.get("uri") if request["method"] == "resources/read" else params.get("name")
                if name is not None:
                    headers["Mcp-Name"] = name
            try:
                with self.opener.open(urllib.request.Request(self.url, payload, headers), timeout=self.timeout) as response:
                    if notification:
                        if response.status != 202:
                            raise ClientError("Notification was not accepted")
                        return None
                    if response.headers.get_content_type() != "application/json":
                        raise ClientError("Bridge client requires a JSON HTTP response")
                    raw = response.read(MAX_RESPONSE + 1)
            except urllib.error.HTTPError as exc:
                raise ClientError("HTTP request failed (status %d); no automatic replay" % exc.code) from None
            except (urllib.error.URLError, TimeoutError, OSError):
                raise ClientError("Transport failed; execution outcome may be unknown; no automatic replay") from None
        else:
            try:
                self.process.stdin.write(payload + b"\n")
                self.process.stdin.flush()
                if notification:
                    return None
                raw = self.lines.get(timeout=self.timeout)
            except (OSError, queue.Empty):
                raise ClientError("Stdio failed or timed out; no automatic replay") from None
            if raw is None:
                raise ClientError("Stdio server closed")
            if isinstance(raw, ClientError):
                raise raw
        if len(raw) > MAX_RESPONSE:
            raise ClientError("Oversized response")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ClientError("Invalid JSON response") from None
        if not isinstance(result, dict) or result.get("jsonrpc") != "2.0" or result.get("id") != request["id"]:
            raise ClientError("Response identity mismatch")
        if "error" in result:
            error = result["error"]
            code = error.get("code") if isinstance(error, dict) else None
            raise ClientError("RPC request failed (code %s)" % code)
        if not isinstance(result.get("result"), dict):
            raise ClientError("Invalid RPC result")
        return result["result"]

    def _request(self, method, params=None, notification=False):
        if self.closed:
            raise ClientError("Client is closed")
        params = dict(params or {})
        if self.version == MODERN_VERSION:
            params["_meta"] = {"io.modelcontextprotocol/protocolVersion": self.version,
                               "io.modelcontextprotocol/clientCapabilities": {},
                               "io.modelcontextprotocol/clientInfo": {"name": "codex-x-client", "version": "0.2.21"}}
        self.counter += 1
        request = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            request["id"] = self.counter
        return self._exchange(request, notification)

    def connect(self):
        with self.lock:
            if self.connected:
                return self.server
            if self.version == MODERN_VERSION:
                result = self._request("server/discover")
                if self.version not in result.get("supportedVersions", []):
                    raise ClientError("Server does not support selected protocol")
            else:
                result = self._request("initialize", {"protocolVersion": self.version,
                    "capabilities": {}, "clientInfo": {"name": "codex-x-client", "version": "0.2.21"}})
                negotiated = result.get("protocolVersion")
                if negotiated not in LEGACY_VERSIONS:
                    raise ClientError("Unsupported negotiated protocol")
                self.version = negotiated
                self._request("notifications/initialized", notification=True)
            self.server, self.connected = result, True
            return result

    def request(self, method, params=None):
        if not self.connected:
            raise ClientError("Connect before requesting operations")
        with self.lock:
            return self._request(method, params)

    def call_tool(self, name, arguments=None):
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})

    def doctor(self):
        self.connect()
        tools = self.request("tools/list")["tools"]
        status = self.call_tool("codex_x_status")
        projects = self.call_tool("codex_x_list_projects")
        if status.get("isError") or projects.get("isError"):
            raise ClientError("Read-only readiness check failed")
        return {"transport": "http" if self.url else "stdio", "protocolVersion": self.version,
                "tools": [tool["name"] for tool in tools],
                "resources": self.request("resources/list")["resources"],
                "prompts": self.request("prompts/list")["prompts"],
                "status": status["structuredContent"], "projects": projects["structuredContent"],
                "live_host_verified": False}

    def close(self):
        self.closed = True
        if self.process:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
            self.process.stdout.close()
            self.reader.join(timeout=1)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def main(argv=None):
    import argparse
    import sys
    parser = argparse.ArgumentParser(description="Read-only MCP transport readiness check")
    parser.add_argument("--url", help="Exact HTTPS /mcp endpoint, or loopback HTTP")
    parser.add_argument("--config", help="Private local configuration for bundled stdio server")
    parser.add_argument("--token-env", default="CODEX_X_MCP_TOKEN")
    parser.add_argument("--protocol", choices=SUPPORTED_VERSIONS, default="2025-11-25")
    parser.add_argument("--timeout", type=float, default=10)
    args = parser.parse_args(argv)
    try:
        if args.url:
            client = MCPClient(url=args.url, token=os.environ.get(args.token_env),
                               version=args.protocol, timeout=args.timeout)
        else:
            command = [sys.executable, "-B", "-m", "bridge"]
            if args.config:
                command += ["--config", args.config]
            command += ["mcp-stdio"]
            client = MCPClient(command=command, version=args.protocol, timeout=args.timeout)
        with client:
            print(json.dumps(client.doctor(), ensure_ascii=False, indent=2))
    except (ClientError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
