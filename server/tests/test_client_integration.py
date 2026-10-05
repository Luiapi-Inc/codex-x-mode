"""Real sockets and stdio processes; Codex execution remains an explicit fixture."""
import json
import os
from pathlib import Path
import subprocess
import ssl
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bridge.client import ClientError, MCPClient
from bridge.core import Store, prepare_response
from bridge.http import Server
from bridge.mcp import LEGACY_VERSIONS, MODERN_VERSION


class ClientIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        project = self.root / "project"
        project.mkdir()
        (project / "hello.txt").write_text("hello client\n")
        self.config = {"gpt_key": "g" * 40, "provider_key": "p" * 40, "mcp_key": "m" * 40,
            "projects": {"demo": {"cwd": str(project.resolve()), "allow_write": False}},
            "codex_command": [sys.executable, str(Path(__file__).with_name("fake_app_server.py"))],
            "chatgpt_web_default_model": "chatgpt-web/fixture-model",
            "task_timeout_seconds": 5}
        self.config_path = self.root / "private.json"
        self.config_path.write_text(json.dumps(self.config))
        self.config_path.chmod(0o600)
        self.store = Store(self.root / "http.sqlite3")
        self.server = Server(("127.0.0.1", 0), self.config, self.store, start_worker=False)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.url = "http://127.0.0.1:%s/mcp" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(3)
        self.server.server_close()
        self.store.close()
        self.tmp.cleanup()

    def http(self, version="2025-11-25", token=None):
        return MCPClient(url=self.url, token=token or self.config["mcp_key"], version=version)

    def value(self, client, name, arguments=None):
        result = client.call_tool(name, arguments)
        self.assertFalse(result.get("isError"), result)
        return result["structuredContent"]

    def test_http_discovery_all_revisions_and_read_only_doctor(self):
        for version in (*LEGACY_VERSIONS, MODERN_VERSION):
            with self.subTest(version=version), self.http(version) as client:
                report = client.doctor()
                self.assertEqual(len(report["tools"]), 13)
                self.assertEqual(len(report["resources"]), 2)
                self.assertEqual(len(report["prompts"]), 2)
                self.assertEqual(report["projects"]["projects"][0]["id"], "demo")
                self.assertFalse(report["status"]["live_codex_verified"])
                resource = client.request("resources/read", {"uri": "codex-x://status"})
                self.assertEqual(json.loads(resource["contents"][0]["text"])["status"], "up")
                prompt = client.request("prompts/get", {"name": "codex-x-dispatch-read-only",
                    "arguments": {"project_id": "demo", "goal": "Review"}})
                self.assertEqual(prompt["messages"][0]["role"], "user")
        self.assertEqual(self.store.unknown_projects(), [])

    def test_stdio_subprocess_and_cleanup(self):
        for version in ("2025-11-25", MODERN_VERSION):
            with self.subTest(version=version):
                client = MCPClient(command=[sys.executable, "-B", "-m", "bridge", "--config",
                    str(self.config_path), "mcp-stdio"], version=version)
                with client:
                    report = client.doctor()
                    self.assertEqual(len(report["tools"]), 13)
                    value = self.value(client, "codex_x_read_project_file",
                                       {"project_id": "demo", "path": "hello.txt"})
                    self.assertEqual(value["content"], "hello client\n")
                self.assertIsNotNone(client.process.returncode)
                self.assertFalse(client.reader.is_alive())

    def test_http_auth_role_separation(self):
        for key in ("gpt_key", "provider_key"):
            with self.http(token=self.config[key]) as client:
                with self.assertRaisesRegex(ClientError, "401"):
                    client.connect()

    def test_read_only_cli_http_and_stdio(self):
        base = [sys.executable, "-B", "-m", "bridge.client"]
        environment = dict(os.environ, CODEX_X_MCP_TOKEN=self.config["mcp_key"])
        for args in (["--url", self.url], ["--config", str(self.config_path)]):
            result = subprocess.run(base + args, env=environment, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(self.config["mcp_key"], result.stdout + result.stderr)
            self.assertEqual(len(json.loads(result.stdout)["tools"]), 13)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 0)

    def test_invalid_http_protocol_rejected(self):
        request = urllib.request.Request(self.url,
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
            {"Authorization": "Bearer " + self.config["mcp_key"], "MCP-Protocol-Version": "invalid",
             "Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 400)

    def test_https_cli_with_trusted_local_certificate(self):
        cert, key = self.root / "cert.pem", self.root / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=localhost",
            "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
            check=True, capture_output=True, timeout=10)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server = Server(("127.0.0.1", 0), self.config, self.store, start_worker=False)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        url = "https://localhost:%s/mcp" % server.server_port
        try:
            environment = dict(os.environ, SSL_CERT_FILE=str(cert), CODEX_X_MCP_TOKEN=self.config["mcp_key"])
            result = subprocess.run([sys.executable, "-B", "-m", "bridge.client", "--url", url],
                env=environment, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(json.loads(result.stdout)["tools"]), 13)
            with patch.dict(os.environ, {"SSL_CERT_FILE": str(self.root / "missing-ca.pem")}):
                with MCPClient(url=url, token=self.config["mcp_key"]) as client:
                    with self.assertRaises(ClientError):
                        client.connect()
        finally:
            server.shutdown()
            thread.join(3)
            server.server_close()

    def test_scoped_reads_and_write_denial(self):
        with self.http() as client:
            client.connect()
            listing = self.value(client, "codex_x_list_project_directory", {"project_id": "demo"})
            self.assertEqual(listing["entries"][0]["name"], "hello.txt")
            for name, args in (
                ("codex_x_read_project_file", {"project_id": "demo", "path": "../private.json"}),
                ("codex_x_create_task", {"project_id": "demo", "prompt": "edit", "scope": "workspace-write", "request_key": "denied"}),
            ):
                result = client.call_tool(name, args)
                self.assertTrue(result["isError"])
                self.assertEqual(result["structuredContent"]["error"]["status"], 403)

    def test_task_dedup_conflict_read_and_queued_cancel(self):
        with self.http() as client:
            client.connect()
            args = {"project_id": "demo", "prompt": "Review", "scope": "read-only", "request_key": "create"}
            job = self.value(client, "codex_x_create_task", args)
            self.assertEqual(job["id"], self.value(client, "codex_x_create_task", args)["id"])
            conflict = client.call_tool("codex_x_create_task", dict(args, prompt="Different"))
            self.assertEqual(conflict["structuredContent"]["error"]["status"], 409)
            self.assertEqual(self.value(client, "codex_x_read_task", {"task_id": job["id"]})["state"], "queued")
            result = self.value(client, "codex_x_cancel_task", {"task_id": job["id"], "request_key": "cancel"})
            self.assertEqual(result["state"], "cancelled")

    def test_fixture_dispatch_complete_and_followup(self):
        # Real bridge worker + app-server subprocess fixture, not real Codex.
        from bridge.codex import worker
        stop = threading.Event()
        thread = threading.Thread(target=worker, args=(self.config, self.store, stop))
        thread.start()
        try:
            with self.http() as client:
                client.connect()
                job = self.value(client, "codex_x_create_task", {"project_id": "demo",
                    "prompt": "Review", "scope": "read-only", "request_key": "fixture"})
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    job = self.value(client, "codex_x_read_task", {"task_id": job["id"]})
                    if job["state"] == "completed":
                        break
                    time.sleep(0.02)
                self.assertEqual(job["state"], "completed")
                self.assertIn("Fixture answer", json.dumps(job["result"]))
                self.assertEqual(job["result"]["observations"][0]["exit_code"], 1)
                next_job = self.value(client, "codex_x_continue_task", {"task_id": job["id"],
                    "prompt": "Review more", "request_key": "followup"})
                self.assertNotEqual(next_job["id"], job["id"])
        finally:
            stop.set()
            thread.join(10)
            self.assertFalse(thread.is_alive())

    def test_backend_claim_context_completion_and_replay(self):
        payload = prepare_response(self.store, {"model": "fixture", "input": "x" * 25000})
        self.store.create("backend", payload, "provider")
        with self.http(MODERN_VERSION) as client:
            client.connect()
            claim = self.value(client, "codex_x_claim_backend_turn", {"request_key": "claim"})["turn"]
            retry = self.value(client, "codex_x_claim_backend_turn", {"request_key": "claim"})["turn"]
            self.assertEqual(claim, retry)
            args = {"turn_id": claim["id"], "lease": claim["lease"]}
            first = self.value(client, "codex_x_read_backend_context", args)
            second = self.value(client, "codex_x_read_backend_context", dict(args, offset=first["next_offset"]))
            self.assertEqual(len(first["chunk"] + second["chunk"]), first["total"])
            self.assertIsNone(second["next_offset"])
            denied = client.call_tool("codex_x_complete_backend_turn", dict(args, lease="wrong", request_key="bad", answer="bad"))
            self.assertTrue(denied["isError"])
            body = dict(args, request_key="complete", answer="Reviewed")
            result = self.value(client, "codex_x_complete_backend_turn", body)
            self.assertEqual(result, self.value(client, "codex_x_complete_backend_turn", body))

    def test_backend_cancel_and_stable_null_claim(self):
        with self.http() as client:
            client.connect()
            self.assertIsNone(self.value(client, "codex_x_claim_backend_turn", {"request_key": "empty"})["turn"])
            self.store.create("backend", prepare_response(self.store, {"model": "fixture", "input": "x"}), "later")
            self.assertIsNone(self.value(client, "codex_x_claim_backend_turn", {"request_key": "empty"})["turn"])
            claim = self.value(client, "codex_x_claim_backend_turn", {"request_key": "new"})["turn"]
            result = self.value(client, "codex_x_cancel_backend_turn",
                {"turn_id": claim["id"], "lease": claim["lease"], "request_key": "cancel"})
            self.assertEqual(result["state"], "cancelled")

    def test_unknown_writer_blocks_only_conflicting_writes(self):
        self.config["projects"]["demo"]["allow_write"] = True
        self.config["projects"]["alias"] = dict(self.config["projects"]["demo"])
        args = {"project_id": "demo", "prompt": "Review", "scope": "workspace-write", "request_key": "unknown"}
        with self.http() as client:
            client.connect()
            job = self.value(client, "codex_x_create_task", args)
            with self.store.lock, self.store.db:
                self.store.db.execute("UPDATE jobs SET state='running' WHERE id=?", (job["id"],))
            self.store.close()
            self.store = Store(self.root / "http.sqlite3")
            self.server.store = self.store
            read_result = client.call_tool("codex_x_create_task", {
                "project_id": "alias", "prompt": "Read safely", "scope": "read-only", "request_key": "read"})
            self.assertFalse(read_result.get("isError", False), read_result)
            write_result = client.call_tool("codex_x_create_task", {
                "project_id": "alias", "prompt": "Write", "scope": "workspace-write", "request_key": "write"})
            self.assertTrue(write_result["isError"])
            status = self.value(client, "codex_x_status")
            self.assertEqual(status["unknown_projects"], ["demo"])
            self.assertEqual(status["unknown_tasks"][0]["id"], job["id"])

    def test_origin_is_rejected(self):
        data = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": "2025-11-25"}}).encode()
        request = urllib.request.Request(self.url, data, {"Authorization": "Bearer " + self.config["mcp_key"],
            "Content-Type": "application/json", "Origin": "https://untrusted.invalid"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 403)


class ClientFailureTests(unittest.TestCase):
    def test_remote_cleartext_and_credentials_in_url_refused(self):
        for url in ("http://remote.invalid/mcp", "https://user:secret@remote.invalid/mcp",
                    "https://remote.invalid/mcp?token=secret"):
            with self.assertRaises(ValueError):
                MCPClient(url=url, token="secret")

    def test_redirect_is_not_followed(self):
        hits = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def do_POST(self):
                hits.append(self.path)
                self.send_response(307)
                self.send_header("Location", "/leak")
                self.send_header("Content-Length", "0")
                self.end_headers()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with MCPClient(url="http://127.0.0.1:%s/mcp" % server.server_port, token="secret") as client:
                with self.assertRaisesRegex(ClientError, "Redirect"):
                    client.connect()
            self.assertEqual(hits, ["/mcp"])
        finally:
            server.shutdown()
            thread.join(3)
            server.server_close()

    def test_stdio_timeout_cleanup_and_no_retry(self):
        client = MCPClient(command=[sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.05)
        with client:
            with self.assertRaisesRegex(ClientError, "timed out"):
                client.connect()
        self.assertIsNotNone(client.process.returncode)

    def test_stdio_response_identity_mismatch(self):
        script = "import sys,json; json.loads(sys.stdin.readline()); print(json.dumps({'jsonrpc':'2.0','id':999,'result':{}}),flush=True)"
        with MCPClient(command=[sys.executable, "-c", script]) as client:
            with self.assertRaisesRegex(ClientError, "identity"):
                client.connect()
