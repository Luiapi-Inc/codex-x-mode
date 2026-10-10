"""G5.1: opt-in SSH profile pinning and bounded operator remote PTY."""
import base64
import os
from pathlib import Path
import stat
import tempfile
import time
import unittest

from bridge.core import Fault, Store
from bridge.ssh_remote import SSHManager, validate_ssh_profile


class ManagedSSHTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.hosts = self.root / "known_hosts"
        wire = (len(b"ssh-ed25519").to_bytes(4, "big") + b"ssh-ed25519"
                + (32).to_bytes(4, "big") + b"z" * 32)
        self.hosts.write_text("test.invalid ssh-ed25519 " + base64.b64encode(wire).decode() + "\n")
        self.hosts.chmod(0o600)
        self.key = self.root / "identity"
        self.key.write_text("dummy-key-for-fixture")
        self.key.chmod(0o600)
        self.fixture = self.root / "mock-ssh"
        self.fixture.write_text('#!/usr/bin/env python3\n'
                                'import os,sys\n'
                                'print("SSH_TEST_ARGS_BEGIN " + "|".join(sys.argv[1:]),flush=True)\n'
                                'os.execv("/bin/sh", ["/bin/sh"])\n')
        self.fixture.chmod(0o700)
        self.store = Store(self.root / "state.sqlite3")
        self.config = {
            "projects": {"demo": {"cwd": str(self.project), "allow_write": True}},
            "shell": {"enabled": True, "executable": "/bin/sh"},
            "ssh": {"enabled": True, "executable": str(self.fixture),
                    "profiles": {"staging": {
                        "project_id": "demo", "host": "test.invalid",
                        "user": "remoteuser", "port": 22,
                        "known_hosts_file": str(self.hosts),
                        "identity_file": str(self.key),
                    }}},
        }
        self.manager = SSHManager(self.config, self.store)

    def tearDown(self):
        self.manager.shutdown()
        self.store.close()
        self.temp.cleanup()

    def test_pinned_host_profile_tty_lifecycle_and_ssh_receipt(self):
        opened = self.manager.open("staging", request_key="remote-open")
        sid = opened["session_id"]
        self.assertEqual(opened["profile_id"], "staging")
        self.assertEqual(opened["connection_state"], "unverified")
        self.manager.write(sid, "printf 'REMOTE_PTY_OK\\n'\n", request_key="remote-input")
        data = ""
        for _ in range(50):
            value = self.manager.read(sid, cursor=0)
            data = value["output"]
            if "SSH_TEST_ARGS_BEGIN" in data and "REMOTE_PTY_OK" in data:
                break
            time.sleep(.04)
        self.assertIn("StrictHostKeyChecking=yes", data)
        self.assertIn("UserKnownHostsFile=", data)
        self.assertIn("BatchMode=yes", data)
        self.assertIn("ForwardAgent=no", data)
        self.assertIn("RequestTTY=force", data)
        self.assertIn("REMOTE_PTY_OK", data)
        snapshot = self.manager.ssh_snapshots[sid]
        self.assertTrue((snapshot / "identity").exists())
        result = self.manager.close(sid)
        self.assertFalse(result["active"])
        self.assertFalse(snapshot.exists(), "SSH private key snapshot must be erased after exit")
        self.assertEqual(self.store.unknown_app_writers(), [])
        receipt = self.store.db.execute(
            "SELECT receipt FROM operator_shell_sessions WHERE id=?", (sid,)
        ).fetchone()["receipt"]
        self.assertIn('"transport": "ssh"', receipt)
        self.assertIn('"profile_id": "staging"', receipt)

    def test_open_replay_cannot_replace_credentials_or_leak_snapshots(self):
        first = self.manager.open("staging", request_key="same-key")
        sid = first["session_id"]
        original = self.manager.ssh_snapshots[sid]
        self.assertTrue((original / "identity").exists())
        repeated = self.manager.open("staging", request_key="same-key")
        self.assertEqual(repeated["session_id"], sid)
        self.assertEqual(self.manager.ssh_snapshots[sid], original)
        self.assertEqual(
            list(Path(self.manager.home.name).glob("ssh-keys-*")), [original]
        )
        self.manager.close(sid)
        self.assertFalse(original.exists())

    def test_idempotency_key_rejects_changed_remote_destination(self):
        sid = self.manager.open("staging", request_key="same-remote-contract")["session_id"]
        self.config["ssh"]["profiles"]["staging"]["user"] = "differentuser"
        with self.assertRaises(Fault) as conflict:
            self.manager.open("staging", request_key="same-remote-contract")
        self.assertEqual(conflict.exception.status, 409)
        self.assertEqual(self.manager.status(sid)["remote_user"]
                         if "remote_user" in self.manager.status(sid)
                         else self.manager.sessions[sid]["launch_metadata"]["remote_user"],
                         "remoteuser")
        self.manager.close(sid)

    def test_disabled_config_and_unknown_profile(self):
        self.config["ssh"]["enabled"] = False
        with self.assertRaises(Fault):
            self.manager.open("staging", request_key="disabled")
        self.config["ssh"]["enabled"] = True
        with self.assertRaises(Fault):
            self.manager.open("other", request_key="missing")

    def test_requires_exact_host_key_and_prevents_symlink_or_weak_permissions(self):
        for change in (
            lambda: self.hosts.write_text("wrong.invalid ssh-ed25519 "+("a"*48)+"\n"),
            lambda: self.hosts.chmod(0o644),
        ):
            self.hosts.chmod(0o600)
            self.hosts.write_text("test.invalid ssh-ed25519 " + base64.b64encode(
                (len(b"ssh-ed25519")).to_bytes(4, "big") + b"ssh-ed25519"
                + (32).to_bytes(4, "big") + b"z"*32).decode() + "\n")
            change()
            with self.assertRaises(Fault):
                validate_ssh_profile(self.config, "staging")
        self.hosts.chmod(0o600)
        self.hosts.write_text("test.invalid ssh-ed25519 " + base64.b64encode(
            (len(b"ssh-ed25519")).to_bytes(4, "big") + b"ssh-ed25519"
            + (32).to_bytes(4, "big") + b"z"*32).decode() + "\n")
        alternative = self.root / "hosts-link"
        alternative.symlink_to(self.hosts)
        self.config["ssh"]["profiles"]["staging"]["known_hosts_file"] = str(alternative)
        with self.assertRaises(Fault):
            validate_ssh_profile(self.config, "staging")

    def test_no_dynamic_host_or_shell_arguments_from_request(self):
        info = self.config["ssh"]["profiles"]["staging"]
        for invalid in ("-oProxyCommand=bad", "host;rm -rf ?", "host\nunsafe"):
            info["host"] = invalid
            with self.assertRaises(Fault):
                validate_ssh_profile(self.config, "staging")
        info["host"] = "test.invalid"
        info["port"] = True
        with self.assertRaises(Fault):
            validate_ssh_profile(self.config, "staging")

    def test_operator_mcp_advertises_ssh_only_when_enabled(self):
        import secrets
        import http.client
        import json
        import threading
        from bridge.shell_operator import OperatorShellServer
        self.config.update({
            "shell_key": secrets.token_urlsafe(36),
            "mcp_key": secrets.token_urlsafe(36),
            "gpt_key": secrets.token_urlsafe(36),
            "admin_key": secrets.token_urlsafe(36),
            "provider_key": secrets.token_urlsafe(36),
        })
        server = OperatorShellServer(("127.0.0.1", 0), self.config, self.store)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def rpc(name, arguments=None):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=7)
            payload = {"jsonrpc":"2.0","id":3,"method": "tools/call" if name else "tools/list",
                       "params": {"name": name, "arguments": arguments or {}} if name else {}}
            conn.request("POST", "/operator/mcp", json.dumps(payload), {
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.config["shell_key"],
            })
            response=conn.getresponse()
            data=response.read()
            conn.close()
            return response.status,json.loads(data)
        try:
            code,listing = rpc(None)
            self.assertEqual(code,200)
            self.assertIn("codex_x_ssh_open", [t["name"] for t in listing["result"]["tools"]])
            code,opened=rpc("codex_x_ssh_open",{"profile_id":"staging","request_key":"ssh-mcp-open"})
            self.assertEqual(code,200)
            self.assertFalse(opened["result"].get("isError"),opened)
            sid=opened["result"]["structuredContent"]["session_id"]
            code,ended=rpc("codex_x_ssh_close",{"session_id":sid})
            self.assertEqual(code,200)
            self.assertFalse(ended["result"]["structuredContent"]["active"])
        finally:
            server.shutdown()
            thread.join(timeout=4)
            server.server_close()

    def test_invalid_remote_profile_never_gets_operator_spawn(self):
        entry = self.config["ssh"]["profiles"]["staging"]
        entry["user"] = "-oProxyCommand=evil"
        with self.assertRaises(Fault):
            self.manager.open("staging", request_key="bad-user")
        entry["user"] = "remoteuser"
        entry["identity_file"] = str(self.root / "missing-key")
        with self.assertRaises(Fault):
            self.manager.open("staging", request_key="missing-identity")
        self.assertFalse(self.manager.list()["sessions"])
        self.assertFalse(self.store.unknown_app_writers())

    def test_external_ssh_disconnect_reports_failure_not_false_connection_success(self):
        fixture = self.root / "fail-ssh"
        fixture.write_text("#!/bin/sh\nexit 255\n")
        fixture.chmod(0o700)
        self.config["ssh"]["executable"] = str(fixture)
        result = self.manager.open("staging", request_key="failing-ssh")
        sid = result["session_id"]
        self.assertEqual(result["connection_state"], "unverified")
        for _ in range(60):
            observed = self.manager.status(sid)
            if observed["state"] == "exited":
                break
            time.sleep(.05)
        self.assertFalse(observed["active"])
        self.assertEqual(observed["exit_code"], 255)
        self.assertEqual(self.store.unknown_app_writers(), [])
        claim = self.store.db.execute(
            "SELECT state,receipt FROM operator_shell_sessions WHERE id=?", (sid,)
        ).fetchone()
        self.assertEqual(claim["state"], "failed")

    def test_ssh_is_not_visible_or_callable_from_public_mcp(self):
        from bridge.mcp import rpc_response
        self.config["mcp_policy"] = {"mode": "read-only"}
        response = rpc_response(
            {"jsonrpc": "2.0", "id": 11, "method": "tools/list", "params": {}},
            self.config, self.store, {"version": "2025-11-25"}, "unified",
        )
        self.assertFalse(any(x["name"].startswith("codex_x_ssh_")
                             for x in response["result"]["tools"]))
        call = rpc_response(
            {"jsonrpc": "2.0", "id": 12, "method": "tools/call",
             "params": {"name": "codex_x_ssh_open",
                        "arguments": {"profile_id": "staging", "request_key": "public-test"}}},
            self.config, self.store, {"version": "2025-11-25"}, "unified",
        )
        self.assertTrue(call["result"]["isError"])
        self.assertEqual(call["result"]["structuredContent"]["error"]["status"], 404)

    def test_ssh_profile_is_offline_configuration_validated(self):
        from bridge.config_kernel import ConfigKernel, ConfigFault
        validated = dict(self.config, config_schema_version=1,
                         mcp_policy={"mode":"read-only"})
        ConfigKernel._validate(validated)
        validated["ssh"]["profiles"]["staging"]["port"] = 0
        with self.assertRaises(ConfigFault):
            ConfigKernel._validate(validated)

    def test_fast_exiting_ssh_cleans_credential_snapshot_without_manager_shutdown(self):
        quick = self.root / "fast-exit-ssh"
        quick.write_text("#!/bin/sh\nexit 255\n")
        quick.chmod(0o700)
        self.config["ssh"]["executable"] = str(quick)
        for number in range(8):
            sid = self.manager.open("staging", request_key=f"fast-{number}")["session_id"]
            for _ in range(100):
                if not self.manager.status(sid)["active"]:
                    break
                time.sleep(.015)
            self.assertFalse(self.manager.status(sid)["active"])
            self.assertNotIn(sid, self.manager.ssh_snapshots)
            self.assertFalse(
                list(Path(self.manager.home.name).glob("ssh-keys-*")),
                "Fast-exiting SSH must not leak copied private key snapshots",
            )

    def test_restart_observer_does_not_fabricate_ssh_handshake_attestation(self):
        sid = self.manager.open("staging", request_key="ssh-recovery-test")["session_id"]
        observer = SSHManager(self.config, self.store)
        try:
            detached = observer.status(sid)
            self.assertEqual(detached["state"], "detached_unverified")
            self.assertEqual(detached["transport"], "ssh")
            self.assertEqual(detached["profile_id"], "staging")
            self.assertEqual(detached["connection_state"], "detached_unverified")
            self.assertTrue(detached["reconciliation_required"])
            with self.assertRaises(Fault):
                observer.open("staging", request_key="ssh-recovery-test")
        finally:
            observer.shutdown()
        self.assertTrue(self.manager.status(sid)["active"])
        self.manager.close(sid)

    def test_writer_claim_blocks_ssh_session_before_launch(self):
        claim = self.store.acquire_app_writer(resource_id=str(self.project),
            project_id="demo", resource_project_ids=["demo"], thread_id="writer")
        self.store.mark_app_writer_unknown(claim["id"], {"reason": "unknown writer"})
        with self.assertRaises(Fault) as blocked:
            self.manager.open("staging", request_key="writer-block")
        self.assertEqual(blocked.exception.status, 409)
