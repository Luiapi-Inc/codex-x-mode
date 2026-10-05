"""Offline SIWC security tests; all tokens and RSA keys are disposable fixtures."""
import base64
from concurrent.futures import ThreadPoolExecutor
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request
import uuid

from bridge import siwc


def credentials(host_id, **changes):
    value = {"issuer": "https://auth.openai.com", "subject": "fixture-subject", "client_id": "fixture-client",
             "ext_agent_host_id": host_id, "token_type": "Bearer", "access_token": "fixture-access",
             "refresh_token": "fixture-refresh", "id_token": "fixture-id-token",
             "scopes": sorted(siwc.REQUIRED_SCOPES), "expires_at": time.time() + 3600}
    value.update(changes)
    return value


class SiwcStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.path = self.root / "credentials.json"
        self.host_id = "urn:uuid:" + str(uuid.uuid4())
        self.config = {"siwc_credentials_file": str(self.path), "ext_agent_host_id": self.host_id}
        siwc._save_credentials(self.config, credentials(self.host_id))

    def tearDown(self):
        self.tmp.cleanup()

    def test_atomic_owner_only_write_preserves_parent_permissions(self):
        self.root.chmod(0o755)
        siwc._save_credentials(self.config, credentials(self.host_id, access_token="fixture-next"))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o755)
        self.assertEqual(siwc.get_credentials(self.config)["access_token"], "fixture-next")
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_unsafe_modes_symlink_and_malformed_file_fail_closed(self):
        for mode in (0o644, 0o400, 0o700):
            with self.subTest(mode=mode):
                self.path.chmod(mode)
                with self.assertRaises(siwc.SiwcError):
                    siwc.get_credentials(self.config)
        self.path.chmod(0o600)
        original = self.root / "original.json"
        self.path.rename(original)
        self.path.symlink_to(original)
        with self.assertRaises(siwc.SiwcError):
            siwc.get_credentials(self.config)
        with self.assertRaises(siwc.SiwcError):
            siwc._save_credentials(self.config, {})
        self.path.unlink()
        original.rename(self.path)
        for raw in ("[]", "{invalid", "{}", "x" * 65537):
            with self.subTest(raw=raw[:20]):
                self.path.write_text(raw)
                with self.assertRaises(siwc.SiwcError):
                    siwc.get_credentials(self.config)

    def test_parent_symlink_is_refused(self):
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(siwc.SiwcError):
            siwc.get_credentials(dict(self.config, siwc_credentials_file=str(alias / self.path.name)))

    def test_host_id_migration_reuses_durable_identity_for_stale_config_reader(self):
        config_path = self.root / "private.json"
        siwc._atomic_write(config_path, {"preserved_setting": "fixture"})
        first = {"_config_path": str(config_path), "siwc_credentials_file": str(self.path)}
        stale = dict(first)
        value = siwc._write_host_id(first)
        self.assertEqual(siwc._write_host_id(stale), value)
        self.assertEqual(json.loads(config_path.read_text())["preserved_setting"], "fixture")

    def test_malformed_scopes_and_account_host_metadata_fail_closed(self):
        changes = [{"scopes": None}, {"scopes": {}}, {"scopes": [None]}, {"scopes": "openid"},
                   {"issuer": "https://attacker.invalid"}, {"subject": ""}, {"client_id": "dynamic_agent_client"},
                   {"token_type": "Basic"}, {"expires_at": True}, {"expires_at": float("nan")},
                   {"ext_agent_host_id": None}, {"ext_agent_host_id": "urn:uuid:" + str(uuid.uuid4())}]
        for change in changes:
            with self.subTest(change=change):
                value = credentials(self.host_id, **change)
                # Invalid JSON NaN is intentionally supplied as hostile local input.
                self.path.write_text(json.dumps(value))
                with self.assertRaises(siwc.SiwcError):
                    siwc.get_credentials(self.config)

    def test_status_is_truthful_and_never_returns_tokens(self):
        status = siwc.authorization_status(self.config)
        self.assertEqual(status["state"], "authorized")
        self.assertNotIn("fixture-access", json.dumps(status))
        siwc._save_credentials(self.config, credentials(self.host_id, expires_at=time.time() - 1))
        self.assertEqual(siwc.authorization_status(self.config)["state"], "refresh_required")
        self.path.unlink()
        self.assertEqual(siwc.authorization_status(self.config)["state"], "authorization_required")

    def refresh_response(self, **changes):
        result = {"access_token": "fixture-rotated-access", "refresh_token": "fixture-rotated-refresh",
                  "id_token": "fixture-rotated-id", "token_type": "Bearer", "expires_in": 3600,
                  "scope": " ".join(siwc.REQUIRED_SCOPES)}
        result.update(changes)
        return result

    def test_concurrent_refresh_rotates_once_and_keeps_account_binding(self):
        siwc._save_credentials(self.config, credentials(self.host_id, expires_at=time.time() - 1))
        with patch.object(siwc, "_request_json", return_value=self.refresh_response()) as request, \
             patch.object(siwc, "verify_id_token", return_value={"sub": "fixture-subject"}):
            with ThreadPoolExecutor(max_workers=8) as pool:
                values = list(pool.map(lambda _: siwc.get_credentials(self.config), range(8)))
        self.assertEqual(request.call_count, 1)
        self.assertTrue(all(value["refresh_token"] == "fixture-rotated-refresh" for value in values))
        self.assertNotIn("scope", request.call_args.kwargs["form"])
        self.assertEqual(request.call_args.kwargs["form"]["client_id"], "fixture-client")

    def test_refresh_omitted_scope_retains_existing_grant(self):
        response = self.refresh_response()
        response.pop("scope")
        siwc._save_credentials(self.config, credentials(self.host_id, expires_at=time.time() - 1))
        with patch.object(siwc, "_request_json", return_value=response), \
             patch.object(siwc, "verify_id_token", return_value={"sub": "fixture-subject"}):
            value = siwc.get_credentials(self.config)
        self.assertEqual(set(value["scopes"]), siwc.REQUIRED_SCOPES)

    def test_refresh_failure_does_not_replace_selected_account(self):
        for response, subject in [(self.refresh_response(), "other-account"),
                                  (self.refresh_response(scope="openid"), "fixture-subject"),
                                  (self.refresh_response(expires_in=True), "fixture-subject"),
                                  (self.refresh_response(token_type="Basic"), "fixture-subject")]:
            with self.subTest(response=response, subject=subject):
                siwc._save_credentials(self.config, credentials(self.host_id, expires_at=time.time() - 1))
                previous = self.path.read_bytes()
                with patch.object(siwc, "_request_json", return_value=response), \
                     patch.object(siwc, "verify_id_token", return_value={"sub": subject}):
                    with self.assertRaises(siwc.SiwcError):
                        siwc.get_credentials(self.config)
                self.assertEqual(self.path.read_bytes(), previous)

    def test_revoked_refresh_clears_tokens_and_requires_new_authorization(self):
        siwc._save_credentials(self.config, credentials(self.host_id, expires_at=time.time() - 1))
        with patch.object(siwc, "_request_json", side_effect=siwc.SiwcError("request failed (400, invalid_grant)")):
            with self.assertRaisesRegex(siwc.SiwcError, "siwc-login"):
                siwc.get_credentials(self.config)
        value = siwc._read_credentials(self.config)
        self.assertTrue(all(value[name] is None for name in ("access_token", "refresh_token", "id_token")))
        self.assertEqual(value["client_id"], "fixture-client")

    def test_earliest_refresh_time_prevents_early_renewal(self):
        siwc._save_credentials(self.config, credentials(self.host_id, expires_at=time.time() + 60,
                                                       earliest_refresh_at=time.time() + 10))
        with patch.object(siwc, "_request_json") as request:
            self.assertEqual(siwc.get_credentials(self.config)["access_token"], "fixture-access")
            request.assert_not_called()

    def test_process_lock_serializes_other_refresh_owner(self):
        script = "import fcntl,sys; f=open(sys.argv[1],'r+'); print('ready',flush=True); fcntl.flock(f,fcntl.LOCK_EX); print('acquired',flush=True)"
        with siwc._credential_guard(self.config):
            process = subprocess.Popen(["python3", "-B", "-c", script, str(self.path) + ".lock"], stdout=subprocess.PIPE, text=True)
            self.assertEqual(process.stdout.readline().strip(), "ready")
            self.assertIsNone(process.poll())
        try:
            self.assertEqual(process.communicate(timeout=5)[0].strip(), "acquired")
            self.assertEqual(process.returncode, 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    def test_import_retains_vm_host_and_rejects_other_registration(self):
        imported = self.root / "imported.json"
        siwc._atomic_write(imported, credentials("urn:uuid:" + str(uuid.uuid4())))
        with patch.object(siwc, "verify_id_token", return_value={"sub": "fixture-subject"}):
            siwc.import_credentials(self.config, str(imported))
        self.assertEqual(siwc._read_credentials(self.config)["ext_agent_host_id"], self.host_id)
        previous = self.path.read_bytes()
        siwc._atomic_write(imported, credentials(self.host_id, client_id="other-client", subject="other-subject"))
        with patch.object(siwc, "verify_id_token", return_value={"sub": "other-subject"}):
            with self.assertRaises(siwc.SiwcError):
                siwc.import_credentials(self.config, str(imported))
        self.assertEqual(self.path.read_bytes(), previous)


class SiwcTokenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.keyfile = Path(cls.tmp.name) / "disposable-test-key.pem"
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(cls.keyfile)], check=True, capture_output=True)
        output = subprocess.run(["openssl", "rsa", "-in", str(cls.keyfile), "-noout", "-modulus"], check=True, capture_output=True, text=True).stdout
        modulus = bytes.fromhex(output.strip().split("=", 1)[1])
        cls.jwk = {"kid": "fixture-key", "kty": "RSA", "use": "sig", "alg": "RS256",
                   "n": cls.b64(modulus), "e": cls.b64((65537).to_bytes(3, "big"))}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @staticmethod
    def b64(value):
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    def token(self, *, header=None, **changes):
        claims = {"iss": "https://auth.openai.com", "aud": "fixture-client", "sub": "fixture-subject",
                  "exp": 2000, "iat": 900, "nonce": "fixture-nonce"}
        claims.update(changes)
        raw = ".".join(self.b64(json.dumps(value).encode()) for value in (header or {"alg": "RS256", "kid": "fixture-key"}, claims))
        signature = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(self.keyfile)], input=raw.encode(), check=True, capture_output=True).stdout
        return raw + "." + self.b64(signature)

    def test_signature_and_identity_claims_verified_with_independent_openssl_signer(self):
        with patch.object(siwc, "_fetch_jwks", return_value=[self.jwk]):
            self.assertEqual(siwc.verify_id_token(self.token(), "fixture-client", "fixture-nonce", now=1000)["sub"], "fixture-subject")
            for changes in ({"iss": "https://attacker.invalid"}, {"aud": "other-client"}, {"exp": 1000},
                            {"exp": True}, {"exp": float("nan")}, {"iat": 2001}, {"nbf": 1200},
                            {"nbf": "invalid"}, {"nonce": "wrong"}, {"sub": ""},
                            {"aud": ["fixture-client", "other"], "azp": "other"}, {"azp": "other"}):
                with self.subTest(changes=changes), self.assertRaises(siwc.SiwcError):
                    siwc.verify_id_token(self.token(**changes), "fixture-client", "fixture-nonce", now=1000)

    def test_forged_signature_missing_kid_and_unsupported_algorithm_rejected(self):
        valid = self.token()
        parts = valid.split(".")
        signature = bytearray(siwc._b64url_decode(parts[2])); signature[0] ^= 1
        forged = ".".join(parts[:2]) + "." + self.b64(signature)
        with patch.object(siwc, "_fetch_jwks", return_value=[self.jwk]):
            for value in (forged, self.token(header={"alg": "none", "kid": "fixture-key"}),
                          self.token(header={"alg": "RS256"}), "invalid"):
                with self.subTest(value=value[:25]), self.assertRaises(siwc.SiwcError):
                    siwc.verify_id_token(value, "fixture-client", now=1000)

    def test_unknown_kid_refetches_once_and_duplicate_keys_fail_closed(self):
        token = self.token(header={"alg": "RS256", "kid": "rotated"})
        key = dict(self.jwk, kid="rotated")
        with patch.object(siwc, "_fetch_jwks", side_effect=[[self.jwk], [key]]) as fetch:
            siwc.verify_id_token(token, "fixture-client", now=1000)
        self.assertEqual(fetch.call_args_list[1].kwargs, {"force": True})
        with patch.object(siwc, "_fetch_jwks", return_value=[self.jwk, self.jwk]):
            with self.assertRaises(siwc.SiwcError):
                siwc.verify_id_token(self.token(), "fixture-client", now=1000)


class SiwcProtocolTests(unittest.TestCase):
    def test_callback_rejects_bad_state_duplicate_fields_path_and_ambiguous_result(self):
        self.assertEqual(siwc._callback_values("/auth/callback?state=expected&code=fixture", "expected")["code"], "fixture")
        for path in ("/auth/callback?state=wrong&code=fixture", "/auth/callback?state=expected&state=expected",
                     "/callback?state=expected", "/auth/callback?state=expected&code=a&code=b",
                     "/auth/callback?state=expected&code=a&error=access_denied"):
            with self.subTest(path=path), self.assertRaises(siwc.SiwcError):
                siwc._callback_values(path, "expected")

    def test_login_loopback_pkce_and_nonce_state_are_bound_to_code_exchange(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config = {"_config_path": str(root / "private.json"), "siwc_credentials_file": str(root / "credentials.json")}
            siwc._atomic_write(root / "private.json", {})
            captured = {}
            callbacks = []

            def browser(url):
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
                captured.update({key: values[0] for key, values in query.items()})
                target = captured["redirect_uri"] + "?" + urllib.parse.urlencode({"state": captured["state"], "code": "fixture-code", "client_id": "fixture-client"})
                def callback():
                    with urllib.request.urlopen(target, timeout=5) as response:
                        callbacks.append(response.status)
                thread = threading.Thread(target=callback); thread.start(); captured["thread"] = thread
                return True

            def exchange(url, *, form):
                self.assertEqual(url, siwc.TOKEN_ENDPOINT)
                self.assertEqual(form["client_id"], "fixture-client")
                self.assertEqual(form["redirect_uri"], captured["redirect_uri"])
                expected = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).decode().rstrip("=")
                self.assertEqual(expected, captured["code_challenge"])
                return {"access_token": "fixture-access", "refresh_token": "fixture-refresh", "id_token": "fixture-id",
                        "scope": " ".join(siwc.REQUIRED_SCOPES), "expires_in": 3600, "token_type": "Bearer"}

            output = io.StringIO()
            with patch.object(siwc, "_request_json", side_effect=exchange), \
                 patch.object(siwc, "verify_id_token", return_value={"sub": "fixture-subject"}) as verify, \
                 contextlib.redirect_stdout(output):
                siwc.login(config, open_browser=browser, timeout=5)
            captured["thread"].join(5)
            self.assertEqual(callbacks, [200])
            self.assertEqual(captured["client_id"], "dynamic_agent_client")
            self.assertEqual(captured["code_challenge_method"], "S256")
            self.assertEqual(verify.call_args.args[2], captured["nonce"])
            self.assertNotIn("fixture-access", output.getvalue())
            self.assertNotIn("fixture-refresh", output.getvalue())
            self.assertEqual(siwc.get_credentials(config)["client_id"], "fixture-client")

    def test_token_requests_refuse_redirect_and_sanitize_http_errors(self):
        with self.assertRaises(siwc.SiwcError):
            siwc._NoRedirect().redirect_request(None, None, 302, "", {}, "https://attacker.invalid")
        response = urllib.error.HTTPError(siwc.TOKEN_ENDPOINT, 400, "fixture", {}, io.BytesIO(b'{"error":"fixture-secret-token","error_description":"secret"}'))
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = response
            with self.assertRaises(siwc.SiwcError) as caught:
                siwc._request_json(siwc.TOKEN_ENDPOINT)
        self.assertNotIn("secret", str(caught.exception))
        for url in ("http://auth.openai.com/token", "https://auth.openai.com@attacker.invalid/token"):
            with self.assertRaises(siwc.SiwcError):
                siwc._request_json(url)

    def test_token_scoped_catalog_filters_hidden_models_preserves_order_and_exact_selection(self):
        creds = credentials("urn:uuid:" + str(uuid.uuid4()))
        response = {"models": [{"slug": "second", "display_name": "Second", "visibility": "list"},
                               {"slug": "hidden", "visibility": "hidden"},
                               {"slug": "first", "display_name": "First", "visibility": "list"}]}
        with patch.object(siwc, "_request_json", return_value=response) as request:
            catalog = siwc.list_models({}, credentials=creds)
        self.assertEqual(request.call_args.kwargs["headers"], {"Authorization": "Bearer fixture-access"})
        self.assertEqual([model["id"] for model in catalog["models"]], ["second", "first"])
        self.assertFalse(catalog["model_entitlement_verified"])
        self.assertEqual(siwc.select_model(catalog, "first")["model"], "first")
        self.assertEqual(siwc.select_model(catalog, None, "second")["source"], "configured_default")
        for requested in (None, "FIRST", "hidden", "missing"):
            with self.assertRaises(siwc.SiwcError):
                siwc.select_model(catalog, requested)
