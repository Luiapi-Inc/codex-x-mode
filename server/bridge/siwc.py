"""Sign in with ChatGPT plan credentials for the bridge-host Responses provider.

Secrets remain in a mode-600 credential file and are only placed in HTTPS
Authorization headers or the Codex app-server child's ACCESS_TOKEN env.
"""
import base64
import fcntl
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from contextlib import contextmanager


AUTHORIZATION_ENDPOINT = "https://auth.openai.com/api/accounts/authorize"
TOKEN_ENDPOINT = "https://auth.openai.com/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
MODELS_ENDPOINT = RESOURCE + "/models"
DISCOVERY_ENDPOINT = "https://auth.openai.com/.well-known/openid-configuration"
REQUIRED_SCOPES = {"openid", "offline_access", "resource.invoke", "chatgpt.tokens.use.direct"}
APP_NAME = "codex_x_mode"
_CREDENTIAL_LOCK = threading.RLock()
_JWKS_CACHE = None
_JWKS_CACHE_UNTIL = 0
_JWKS_REFRESHED_AT = 0


class SiwcError(RuntimeError):
    pass


def credential_path(config):
    value = config.get("siwc_credentials_file")
    if not isinstance(value, str) or not value:
        config_path = config.get("_config_path")
        if isinstance(config_path, str):
            value = str(Path(config_path).with_suffix(".siwc.json"))
    if not isinstance(value, str) or not value:
        raise SiwcError("Sign in with ChatGPT credential path is not configured")
    path = Path(value).expanduser()
    if not path.is_absolute():
        config_path = config.get("_config_path")
        if not isinstance(config_path, str):
            raise SiwcError("Relative credential paths require a bridge config path")
        path = Path(config_path).parent / path
    return path.absolute()


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _scopes(value):
    if isinstance(value, str):
        value = value.split()
    if not isinstance(value, list) or not all(isinstance(item, str) and item and not any(c.isspace() for c in item) for item in value):
        raise SiwcError("ChatGPT plan granted scopes are invalid")
    return set(value)


def _host_id(value):
    if not isinstance(value, str) or len(value) > 1024:
        return False
    if value.startswith("urn:uuid:"):
        try:
            return uuid.UUID(value[9:]).version == 4
        except ValueError:
            return False
    return bool(re.fullmatch(r"(?:urn:ietf:params:oauth:jwk-thumbprint:|did:key:)[A-Za-z0-9:_-]+", value))


@contextmanager
def _parent(path, create=False):
    """Traverse directories by descriptor, refusing symlink components."""
    path = Path(path).absolute()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:-1]:
            if part == "..":
                raise SiwcError("Credential path cannot contain parent traversal")
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd, path.name
    except OSError:
        raise SiwcError("Protected credential path is unavailable") from None
    finally:
        os.close(fd)


def _check_file(metadata):
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_uid != os.getuid():
        raise SiwcError("Credential file must be an owner-held regular mode-600 file")


def _read_file(path):
    with _parent(path) as (parent_fd, name):
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as source:
            metadata = os.fstat(source.fileno())
            _check_file(metadata)
            raw = source.read(65537)
    if len(raw) > 65536:
        raise SiwcError("Credential file is too large")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise SiwcError("Credentials are unreadable") from None
    if not isinstance(value, dict):
        raise SiwcError("Credentials are invalid")
    return value


@contextmanager
def _credential_guard(config):
    """Serialize rotating refresh tokens across threads and processes."""
    with _CREDENTIAL_LOCK, _parent(credential_path(config), create=True) as (parent_fd, name):
        fd = os.open(name + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=parent_fd)
        try:
            _check_file(os.fstat(fd))
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def _atomic_write(path, value):
    with _parent(path, create=True) as (directory_fd, name):
        try:
            _check_file(os.stat(name, dir_fd=directory_fd, follow_symlinks=False))
        except FileNotFoundError:
            pass
        temporary = name + "." + secrets.token_hex(8) + ".tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            os.fsync(directory_fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass


def _read_credentials(config):
    return _read_file(credential_path(config))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise SiwcError("Sign in with ChatGPT redirects are refused")


def _request_json(url, *, form=None, headers=None, timeout=20):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {"auth.openai.com", "api.openai.com"} or parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.fragment:
        raise SiwcError("Sign in with ChatGPT endpoint is invalid")
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    request_headers = {"Accept": "application/json"}
    if form is not None:
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"
    request_headers.update(headers or {})
    request = urllib.request.Request(url, data=data, headers=request_headers)
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read(65536))
            code = body.get("error") if isinstance(body, dict) else None
        except Exception:
            code = None
        safe_code = code if isinstance(code, str) and code in {"invalid_grant", "invalid_refresh_token", "access_denied", "invalid_scope", "invalid_client"} else "request_failed"
        raise SiwcError(f"Sign in with ChatGPT request failed ({exc.code}, {safe_code})") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SiwcError("Sign in with ChatGPT service is unreachable") from None
    if len(raw) > 2 * 1024 * 1024:
        raise SiwcError("Sign in with ChatGPT response is too large")
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise SiwcError("Sign in with ChatGPT returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise SiwcError("Sign in with ChatGPT returned an invalid response")
    return value


def _b64url_decode(value):
    if not isinstance(value, str) or not value or len(value) > 32768 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise SiwcError("OpenID token encoding is invalid")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise SiwcError("OpenID token encoding is invalid") from exc


def _fetch_jwks(force=False):
    global _JWKS_CACHE, _JWKS_CACHE_UNTIL, _JWKS_REFRESHED_AT
    if _JWKS_CACHE is not None and time.time() < _JWKS_CACHE_UNTIL and (not force or time.time() - _JWKS_REFRESHED_AT < 30):
        return _JWKS_CACHE
    metadata = _request_json(DISCOVERY_ENDPOINT)
    if metadata.get("issuer") != "https://auth.openai.com":
        raise SiwcError("OpenID issuer metadata is invalid")
    jwks_url = metadata.get("jwks_uri")
    parsed = urllib.parse.urlsplit(jwks_url) if isinstance(jwks_url, str) else None
    if not parsed or parsed.scheme != "https" or parsed.hostname != "auth.openai.com" or parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SiwcError("OpenID signing-key endpoint is invalid")
    keys = _request_json(jwks_url).get("keys")
    if not isinstance(keys, list) or len(keys) > 100:
        raise SiwcError("OpenID signing keys are invalid")
    _JWKS_CACHE, _JWKS_CACHE_UNTIL = keys, time.time() + 3600
    _JWKS_REFRESHED_AT = time.time()
    return keys


def _verify_rs256(input_bytes, signature, key):
    try:
        modulus = int.from_bytes(_b64url_decode(key["n"]), "big")
        exponent = int.from_bytes(_b64url_decode(key["e"]), "big")
    except (KeyError, TypeError, ValueError) as exc:
        raise SiwcError("OpenID signing key is invalid") from exc
    width = (modulus.bit_length() + 7) // 8
    if not 2048 <= modulus.bit_length() <= 8192 or exponent != 65537:
        raise SiwcError("OpenID signing key parameters are unsupported")
    signature_value = int.from_bytes(signature, "big")
    if len(signature) != width or signature_value >= modulus:
        return False
    encoded_message = pow(signature_value, exponent, modulus).to_bytes(width, "big")
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(input_bytes).digest()
    padding_length = width - len(digest_info) - 3
    if padding_length < 8:
        return False
    expected = b"\x00\x01" + b"\xff" * padding_length + b"\x00" + digest_info
    return hmac.compare_digest(encoded_message, expected)


def verify_id_token(token, client_id, nonce=None, *, now=None):
    if not isinstance(token, str) or len(token) > 32768:
        raise SiwcError("OpenID ID token is missing or invalid")
    parts = token.split(".")
    if len(parts) != 3:
        raise SiwcError("OpenID ID token is invalid")
    try:
        header = json.loads(_b64url_decode(parts[0]))
        claims = json.loads(_b64url_decode(parts[1]))
        signature = _b64url_decode(parts[2])
    except (ValueError, TypeError) as exc:
        raise SiwcError("OpenID ID token is invalid") from exc
    if not isinstance(header, dict) or not isinstance(claims, dict) or header.get("alg") != "RS256" or header.get("crit"):
        raise SiwcError("OpenID ID token uses an unsupported signing algorithm")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid or len(kid) > 200:
        raise SiwcError("OpenID signing key ID is invalid")
    def matching(keys):
        matches = [item for item in keys if isinstance(item, dict) and item.get("kid") == kid and item.get("kty") == "RSA"
                   and item.get("use", "sig") == "sig" and item.get("alg", "RS256") == "RS256"
                   and ("key_ops" not in item or item["key_ops"] == ["verify"])]
        return matches[0] if len(matches) == 1 else None
    key = matching(_fetch_jwks())
    if not key:
        key = matching(_fetch_jwks(force=True))
    if not key or not _verify_rs256((parts[0] + "." + parts[1]).encode("ascii"), signature, key):
        raise SiwcError("OpenID ID token signature is invalid")
    current = time.time() if now is None else now
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if not audiences or not all(isinstance(item, str) and item for item in audiences):
        raise SiwcError("OpenID ID token audience is invalid")
    if claims.get("iss") != "https://auth.openai.com" or client_id not in audiences:
        raise SiwcError("OpenID ID token issuer or audience is invalid")
    if (len(audiences) > 1 or "azp" in claims) and claims.get("azp") != client_id:
        raise SiwcError("OpenID ID token authorized party is invalid")
    if not _number(claims.get("exp")) or claims["exp"] <= current:
        raise SiwcError("OpenID ID token is expired")
    if not _number(claims.get("iat")) or claims["iat"] > current + 60 or claims["iat"] >= claims["exp"]:
        raise SiwcError("OpenID ID token issued-at time is invalid")
    if "nbf" in claims and (not _number(claims["nbf"]) or claims["nbf"] > current + 60):
        raise SiwcError("OpenID ID token is not yet valid")
    if (nonce is not None and (not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(claims["nonce"].encode(), nonce.encode()))) or not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise SiwcError("OpenID ID token nonce or subject is invalid")
    return claims


def _save_credentials(config, value):
    _atomic_write(credential_path(config), value)


def registration(credentials):
    return {key: credentials[key] for key in ("issuer", "subject", "client_id")}


def _validate_credentials(config, credentials):
    if not REQUIRED_SCOPES <= _scopes(credentials.get("scopes")):
        raise SiwcError("ChatGPT plan permission is missing; run siwc-login")
    if credentials.get("issuer") != "https://auth.openai.com" or not isinstance(credentials.get("subject"), str) or not credentials["subject"]:
        raise SiwcError("ChatGPT plan account identity is invalid")
    if not isinstance(credentials.get("client_id"), str) or not credentials["client_id"] or credentials["client_id"] == "dynamic_agent_client":
        raise SiwcError("ChatGPT plan registration is invalid; run siwc-login")
    if credentials.get("token_type") != "Bearer" or not all(isinstance(credentials.get(key), str) and credentials[key] for key in ("access_token", "refresh_token", "id_token")):
        raise SiwcError("ChatGPT plan token set is invalid; run siwc-login")
    if not _number(credentials.get("expires_at")) or ("earliest_refresh_at" in credentials and not _number(credentials["earliest_refresh_at"])):
        raise SiwcError("ChatGPT plan token expiry is invalid")
    host_id = config.get("ext_agent_host_id")
    if not _host_id(host_id) or credentials.get("ext_agent_host_id") != host_id:
        raise SiwcError("ChatGPT plan credentials belong to a different or unconfigured host")


def _refresh_locked(config, credentials):
    refresh_token = credentials.get("refresh_token")
    client_id = credentials.get("client_id")
    if not isinstance(refresh_token, str) or not isinstance(client_id, str) or client_id == "dynamic_agent_client":
        raise SiwcError("ChatGPT plan session needs Sign in with ChatGPT authorization")
    form = {"grant_type": "refresh_token", "client_id": client_id,
            "refresh_token": refresh_token, "resource": RESOURCE}
    try:
        response = _request_json(TOKEN_ENDPOINT, form=form)
    except SiwcError as exc:
        if "invalid_grant" in str(exc) or "invalid_refresh_token" in str(exc):
            credentials.update({"access_token": None, "refresh_token": None, "id_token": None, "expires_at": 0})
            _save_credentials(config, credentials)
            raise SiwcError("ChatGPT plan session expired; run siwc-login again") from None
        raise
    scope_value = response.get("scope")
    scopes = _scopes(scope_value) if scope_value is not None else _scopes(credentials["scopes"])
    if not REQUIRED_SCOPES <= scopes or not all(isinstance(response.get(key), str) and response[key] for key in ("access_token", "refresh_token", "id_token")):
        raise SiwcError("ChatGPT plan refresh did not return the required permission and credentials")
    claims = verify_id_token(response["id_token"], client_id)
    if claims.get("sub") != credentials.get("subject"):
        raise SiwcError("Refreshed ChatGPT identity does not match the saved account")
    if response.get("token_type") != "Bearer":
        raise SiwcError("ChatGPT plan refresh returned an invalid token type")
    expires_in = response.get("expires_in")
    if not isinstance(expires_in, int) or isinstance(expires_in, bool) or expires_in < 1 or expires_in > 86400:
        raise SiwcError("ChatGPT plan refresh returned an invalid expiry")
    credentials.update({"access_token": response["access_token"], "refresh_token": response["refresh_token"],
                        "id_token": response["id_token"], "scopes": sorted(scopes),
                        "expires_at": time.time() + expires_in})
    credentials.pop("earliest_refresh_at", None)
    if "earliest_refresh_at" in response:
        if not _number(response["earliest_refresh_at"]):
            raise SiwcError("ChatGPT plan refresh returned an invalid renewal time")
        credentials["earliest_refresh_at"] = response["earliest_refresh_at"]
    _save_credentials(config, credentials)
    return credentials


def get_credentials(config, *, refresh=True):
    with _credential_guard(config):
        credentials = _read_credentials(config)
        if credentials is None:
            raise SiwcError("ChatGPT plan authorization is required; run siwc-login")
        _validate_credentials(config, credentials)
        if refresh and credentials["expires_at"] <= time.time() + 90:
            earliest = credentials.get("earliest_refresh_at", 0)
            if earliest > time.time():
                if credentials["expires_at"] > time.time() + 30:
                    return credentials
                raise SiwcError("ChatGPT plan renewal is not yet allowed")
            credentials = _refresh_locked(config, credentials)
        if not isinstance(credentials.get("access_token"), str) or not credentials["access_token"]:
            raise SiwcError("ChatGPT plan authorization is required; run siwc-login")
        return credentials


def authorization_status(config):
    try:
        credentials = get_credentials(config, refresh=False)
    except (SiwcError, OSError) as exc:
        message = str(exc)
        needs_login = any(marker in message for marker in (
            "authorization is required", "run siwc-login", "credential path is not configured"
        ))
        return {"state": "authorization_required" if needs_login else "invalid",
                "message": message if isinstance(exc, SiwcError) else "Protected credentials are unavailable"}
    return {"state": "authorized" if credentials["expires_at"] > time.time() else "refresh_required", "expires_at": credentials.get("expires_at"),
            "client_id_present": bool(credentials.get("client_id")), "scopes_verified": True}


def access_token(config):
    return get_credentials(config, refresh=True)["access_token"]


def list_models(config, *, credentials=None):
    credentials = credentials if credentials is not None else get_credentials(config, refresh=True)
    response = _request_json(MODELS_ENDPOINT, headers={"Authorization": "Bearer " + credentials["access_token"]})
    items = response.get("models")
    if not isinstance(items, list):
        raise SiwcError("ChatGPT plan model catalog is invalid")
    models, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or item.get("visibility") != "list":
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or not slug or slug in seen:
            raise SiwcError("ChatGPT plan model catalog contains an invalid model")
        display_name = item.get("display_name")
        if not isinstance(display_name, str) or not display_name:
            raise SiwcError("ChatGPT plan model catalog is missing a display name")
        seen.add(slug)
        models.append({"id": slug, "model": slug, "display_name": display_name,
                       "is_default": False, "supported_reasoning_efforts": []})
    if not models:
        raise SiwcError("ChatGPT plan account has no listed models")
    return {"backend": "chatgpt_plan", "catalog_source": "responses_api_models",
            "catalog_integrity_verified": True, "model_entitlement_verified": False,
            "models": models}


def select_model(catalog, requested, default_model=None):
    model_id = requested if requested is not None else default_model
    if not isinstance(model_id, str) or not model_id:
        raise SiwcError("model_version is required for ChatGPT plan dispatch")
    models = catalog.get("models", [])
    matches = [item for item in models if item.get("id") == model_id]
    if len(matches) != 1:
        raise SiwcError("Requested model is unavailable to the authorized ChatGPT account")
    return {"id": matches[0]["id"], "model": matches[0]["model"],
            "display_name": matches[0]["display_name"],
            "source": "requested" if requested is not None else "configured_default"}


def _write_host_id(config):
    value = config.get("ext_agent_host_id")
    if _host_id(value):
        return value
    if value is not None:
        raise SiwcError("Configured ChatGPT host ID is invalid")
    config_path = config.get("_config_path")
    if not isinstance(config_path, str):
        raise SiwcError("Bridge config path is required to create a stable ChatGPT host ID")
    with _credential_guard(config):
        persisted = _read_file(Path(config_path))
        if persisted is None:
            raise SiwcError("Bridge configuration must exist before assigning a host ID")
        value = persisted.get("ext_agent_host_id")
        if value is not None and not _host_id(value):
            raise SiwcError("Saved ChatGPT host ID is invalid")
        if value is None:
            value = "urn:uuid:" + str(uuid.uuid4())
            persisted["ext_agent_host_id"] = value
            _atomic_write(Path(config_path), persisted)
        config["ext_agent_host_id"] = value
        return value


def _callback_values(path, expected_state):
    if not isinstance(path, str) or len(path) > 16384:
        raise SiwcError("Sign in with ChatGPT callback is invalid")
    parsed = urllib.parse.urlsplit(path)
    if parsed.path != "/auth/callback" or parsed.scheme or parsed.netloc or parsed.fragment:
        raise SiwcError("Sign in with ChatGPT callback path is invalid")
    try:
        values = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, max_num_fields=20)
    except ValueError:
        raise SiwcError("Sign in with ChatGPT callback is invalid") from None
    if any(len(items) != 1 for items in values.values()):
        raise SiwcError("Sign in with ChatGPT callback contains duplicate fields")
    result = {key: items[0] for key, items in values.items()}
    if not hmac.compare_digest(result.get("state", "").encode(), expected_state.encode()):
        raise SiwcError("Sign in with ChatGPT state validation failed")
    if result.get("error") and result.get("code"):
        raise SiwcError("Sign in with ChatGPT callback is ambiguous")
    return result


def import_credentials(config, source):
    """Import an owner-protected record, retaining this runtime's host ID."""
    imported = _read_file(Path(source).expanduser())
    if imported is None:
        raise SiwcError("Credential import file was not found")
    host_id = _write_host_id(config)
    imported = dict(imported, ext_agent_host_id=host_id)
    _validate_credentials(config, imported)
    claims = verify_id_token(imported["id_token"], imported["client_id"])
    if claims["sub"] != imported["subject"]:
        raise SiwcError("Imported credentials do not match their verified identity")
    with _credential_guard(config):
        existing = _read_credentials(config)
        if existing and registration(existing) != registration(imported):
            raise SiwcError("Import cannot replace another account registration; use a separate bridge config")
        _save_credentials(config, imported)
    return authorization_status(config)


def login(config, *, open_browser=None, timeout=300):
    """Run the documented loopback PKCE flow. Never print or return tokens."""
    with _credential_guard(config):
        existing = _read_credentials(config)
    if existing and (not isinstance(existing.get("client_id"), str) or not existing["client_id"] or existing["client_id"] == "dynamic_agent_client"):
        raise SiwcError("Saved account registration is invalid")
    host_id = _write_host_id(config)
    callback = {}
    received = threading.Event()
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                    raise SiwcError("Invalid callback host")
                values = _callback_values(self.path, state)
            except SiwcError:
                self.send_error(400, "Invalid authorization callback")
                return
            callback.update(values)
            received.set()
            body = b"Authorization received. You can close this window and return to the terminal."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    try:
        server = HTTPServer(("127.0.0.1", 0), CallbackHandler)
    except OSError as exc:
        raise SiwcError("Could not start the local Sign in with ChatGPT callback") from exc
    server.timeout = 0.5
    redirect_uri = f"http://127.0.0.1:{server.server_port}/auth/callback"
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    client_id = existing.get("client_id") if existing else "dynamic_agent_client"
    params = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
              "scope": "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct",
              "resource": RESOURCE, "state": state, "nonce": nonce,
              "code_challenge_method": "S256", "code_challenge": challenge,
              "ext_agent_host_id": host_id}
    if not existing:
        params["agent_name_hint"] = APP_NAME
    auth_url = AUTHORIZATION_ENDPOINT + "?" + urllib.parse.urlencode(params)
    opener = open_browser or webbrowser.open
    try:
        if not opener(auth_url):
            print("Open this Sign in with ChatGPT URL in a browser:\n" + auth_url)
        deadline = time.monotonic() + timeout
        while not received.is_set() and time.monotonic() < deadline:
            server.handle_request()
        if not received.is_set():
            raise SiwcError("Sign in with ChatGPT callback timed out")
    finally:
        server.server_close()
    if callback.get("error"):
        raise SiwcError("Sign in with ChatGPT authorization was declined or failed")
    code = callback.get("code")
    callback_client = callback.get("client_id")
    if not isinstance(code, str) or not code:
        raise SiwcError("Sign in with ChatGPT callback did not include an authorization code")
    if existing:
        if callback_client and callback_client != existing.get("client_id"):
            raise SiwcError("Sign in with ChatGPT callback client did not match the selected account")
        issued_client = existing.get("client_id")
    else:
        issued_client = callback_client
        if not isinstance(issued_client, str) or not issued_client or issued_client == "dynamic_agent_client":
            raise SiwcError("New Sign in with ChatGPT registration did not return an issued client ID")
    token_response = _request_json(TOKEN_ENDPOINT, form={
        "grant_type": "authorization_code", "client_id": issued_client, "code": code,
        "code_verifier": verifier, "redirect_uri": redirect_uri, "resource": RESOURCE,
    })
    scope_value = token_response.get("scope")
    scopes = _scopes(scope_value)
    if not REQUIRED_SCOPES <= scopes or not all(isinstance(token_response.get(key), str) and token_response[key]
                                                for key in ("access_token", "refresh_token", "id_token")):
        raise SiwcError("Sign in with ChatGPT did not grant the required plan permission")
    claims = verify_id_token(token_response["id_token"], issued_client, nonce)
    if existing and claims.get("sub") != existing.get("subject"):
        raise SiwcError("Sign in returned a different ChatGPT account; saved credentials were not replaced")
    expires_in = token_response.get("expires_in")
    if not isinstance(expires_in, int) or isinstance(expires_in, bool) or expires_in < 1 or expires_in > 86400:
        raise SiwcError("Sign in with ChatGPT returned an invalid expiry")
    credentials = {
        "issuer": "https://auth.openai.com", "subject": claims["sub"],
        "email": claims.get("email") if isinstance(claims.get("email"), str) else None,
        "client_id": issued_client, "ext_agent_host_id": host_id,
        "id_token": token_response["id_token"], "access_token": token_response["access_token"],
        "refresh_token": token_response["refresh_token"], "token_type": token_response.get("token_type"),
        "scopes": sorted(scopes), "expires_at": time.time() + expires_in,
    }
    if "earliest_refresh_at" in token_response:
        credentials["earliest_refresh_at"] = token_response["earliest_refresh_at"]
    _validate_credentials(config, credentials)
    with _credential_guard(config):
        current = _read_credentials(config)
        if (current is None) != (existing is None) or current and registration(current) != registration(credentials):
            raise SiwcError("Selected account changed during authorization; credentials were not replaced")
        _save_credentials(config, credentials)
    print("Sign in with ChatGPT plan usage is connected. Tokens were saved with owner-only permissions.")
