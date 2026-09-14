import argparse
import hashlib
import hmac
import os
import secrets
import sqlite3
import string
import tempfile
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlsplit

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
SCOPES = {"user.info.basic", "video.upload"}
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:*/callback/"
_PKCE_ALPHABET = string.ascii_letters + string.digits + "-._~"


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != self.server.callback_path:
            self.send_error(404)
            return

        parameters = parse_qs(parsed.query, keep_blank_values=True)
        state = parameters.get("state", [""])[0]
        if not hmac.compare_digest(state, self.server.expected_state):
            self.send_error(400, "Invalid OAuth state")
            return

        self.server.oauth_result = {
            "code": parameters.get("code", [""])[0],
            "error": parameters.get("error", [""])[0],
            "error_description": parameters.get("error_description", [""])[0],
        }
        body = (
            b"<!doctype html><html lang='en'><meta charset='utf-8'>"
            b"<title>Reelay TikTok OAuth</title>"
            b"<h2>TikTok response received</h2>"
            b"<p>You can close this tab and return to the terminal.</p>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def _required_env(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Add {name} to your local .env file")
    return value


def _pkce():
    verifier = "".join(secrets.choice(_PKCE_ALPHABET) for _ in range(64))
    challenge = hashlib.sha256(verifier.encode("ascii")).hexdigest()
    return verifier, challenge


def _wait_for_callback(server, timeout=300):
    server.timeout = 1
    deadline = time.monotonic() + timeout
    while server.oauth_result is None and time.monotonic() < deadline:
        server.handle_request()
    if server.oauth_result is None:
        raise RuntimeError("TikTok OAuth timed out")
    return server.oauth_result


def _redirect_configuration(value, requested_port):
    template = str(value or DEFAULT_REDIRECT_URI).strip()
    wildcard = ":*" in template
    if "*" in template and (not wildcard or template.count("*") != 1):
        raise RuntimeError("TIKTOK_REDIRECT_URI contains an invalid wildcard")

    parseable = template.replace(":*", ":0", 1) if wildcard else template
    parsed = urlsplit(parseable)
    try:
        configured_port = parsed.port
    except ValueError as error:
        raise RuntimeError("TIKTOK_REDIRECT_URI contains an invalid port") from error
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username is not None
        or parsed.password is not None
        or configured_port is None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
    ):
        raise RuntimeError(
            "TIKTOK_REDIRECT_URI must be a static HTTP loopback URL "
            "with a port and without a query or fragment"
        )

    if wildcard:
        listen_port = requested_port
    else:
        if requested_port and requested_port != configured_port:
            raise RuntimeError("--port does not match the port in TIKTOK_REDIRECT_URI")
        listen_port = configured_port
    return template, listen_port, parsed.path


def _safe_message(value, *secrets_to_hide):
    message = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    for secret in secrets_to_hide:
        if secret:
            message = message.replace(str(secret), "<redacted>")
    return message[:500]


def _runtime_db_path():
    configured = os.getenv("REELAY_DATA_DIR", "").strip()
    data_dir = Path(configured).expanduser() if configured else ROOT / "data"
    if not data_dir.is_absolute():
        raise RuntimeError("REELAY_DATA_DIR must be an absolute path")
    return data_dir / "reelay.db"


def _exchange_code(client, client_key, client_secret, code, verifier, redirect_uri):
    try:
        response = client.post(
            TOKEN_URL,
            data={
                "client_key": client_key,
                "client_secret": client_secret,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    except httpx.HTTPError as error:
        raise RuntimeError("Network error during TikTok OAuth") from error

    try:
        payload = response.json()
    except ValueError as error:
        raise RuntimeError(
            f"TikTok OAuth returned an invalid response (HTTP {response.status_code})"
        ) from error
    if not isinstance(payload, dict):
        raise RuntimeError("TikTok OAuth returned an invalid response")
    if response.is_error or payload.get("error"):
        error_code = _safe_message(
            payload.get("error") or f"HTTP {response.status_code}",
            client_key,
            client_secret,
            code,
            verifier,
        )
        description = _safe_message(
            payload.get("error_description"),
            client_key,
            client_secret,
            code,
            verifier,
        )
        detail = f": {description}" if description else ""
        raise RuntimeError(f"TikTok OAuth was denied ({error_code}){detail}")

    access_token = str(payload.get("access_token") or "").strip()
    refresh_token = str(payload.get("refresh_token") or "").strip()
    open_id = str(payload.get("open_id") or "").strip()
    granted_scopes = {
        scope for scope in str(payload.get("scope") or "").replace(",", " ").split() if scope
    }
    if not access_token:
        raise RuntimeError("TikTok OAuth did not return an access token")
    if not refresh_token:
        raise RuntimeError("TikTok OAuth did not return a refresh token")
    if not open_id:
        raise RuntimeError("TikTok OAuth did not return an open_id")
    missing_scopes = SCOPES - granted_scopes
    if missing_scopes:
        raise RuntimeError(
            "TikTok did not grant these scopes: " + ", ".join(sorted(missing_scopes))
        )
    return access_token, refresh_token, open_id


def _authorized_user(client, access_token, expected_open_id):
    try:
        response = client.get(
            USER_INFO_URL,
            params={"fields": "open_id,display_name"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    except httpx.HTTPError as error:
        raise RuntimeError("Network error while verifying the TikTok account") from error

    try:
        payload = response.json()
    except ValueError as error:
        raise RuntimeError(
            f"TikTok user.info returned an invalid response (HTTP {response.status_code})"
        ) from error
    if not isinstance(payload, dict):
        raise RuntimeError("TikTok user.info returned an invalid response")

    api_error = payload.get("error") or {}
    error_code = str(api_error.get("code") or "") if isinstance(api_error, dict) else ""
    if response.is_error or error_code != "ok":
        raw_message = api_error.get("message") if isinstance(api_error, dict) else api_error
        message = _safe_message(raw_message, access_token)
        detail = f": {message}" if message else ""
        raise RuntimeError(
            f"TikTok user.info was denied ({error_code or f'HTTP {response.status_code}'}){detail}"
        )

    user = (payload.get("data") or {}).get("user")
    if not isinstance(user, dict):
        raise RuntimeError("TikTok user.info did not return a profile")
    open_id = str(user.get("open_id") or "").strip()
    display_name = str(user.get("display_name") or "").strip()
    if not open_id or not hmac.compare_digest(open_id, expected_open_id):
        raise RuntimeError("TikTok OAuth and user.info returned different accounts")
    if not display_name:
        raise RuntimeError("TikTok user.info did not return the selected account name")
    return display_name


def persist_tiktok_credentials(
    *,
    refresh_token,
    open_id=None,
    env_path=ENV_PATH,
    db_path=None,
):
    updates = {"TIKTOK_REFRESH_TOKEN": str(refresh_token).strip()}
    if open_id is not None:
        updates["TIKTOK_OPEN_ID"] = str(open_id).strip()
    if not updates["TIKTOK_REFRESH_TOKEN"]:
        raise ValueError("TikTok refresh token must not be empty")
    for value in updates.values():
        if "\n" in value or "\r" in value:
            raise ValueError("Invalid value for .env")

    env_path = Path(env_path)
    original = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    found = set()
    lines = []
    for line in original.splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in updates and not line.lstrip().startswith("#"):
            lines.append(f"{key}={updates[key]}")
            found.add(key)
        else:
            lines.append(line)
    if lines and lines[-1]:
        lines.append("")
    for key, value in updates.items():
        if key not in found:
            lines.append(f"{key}={value}")
    content = "\n".join(lines).rstrip("\n") + "\n"

    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=env_path.parent,
            prefix=".env.tiktok.",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            os.chmod(temporary, 0o600)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, env_path)
        os.chmod(env_path, 0o600)
        temporary = None
    finally:
        if temporary and temporary.exists():
            temporary.unlink()

    if db_path is not None and Path(db_path).is_file():
        connection = None
        try:
            connection = sqlite3.connect(db_path, timeout=5)
            connection.execute(
                """
                INSERT INTO settings(key, value) VALUES ('tiktok_refresh_token', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (updates["TIKTOK_REFRESH_TOKEN"],),
            )
            connection.commit()
        except sqlite3.Error as error:
            if connection is not None:
                connection.rollback()
            raise RuntimeError(
                "TikTok OAuth credentials were saved to .env, but the runtime token in SQLite was not updated"
            ) from error
        finally:
            if connection is not None:
                connection.close()


def main():
    parser = argparse.ArgumentParser(
        description="Get a local TikTok OAuth refresh token for Reelay"
    )
    parser.add_argument("--expected-open-id")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    load_dotenv(ENV_PATH, override=True)
    client_key = _required_env("TIKTOK_CLIENT_KEY")
    client_secret = _required_env("TIKTOK_CLIENT_SECRET")
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(32)

    redirect_template, listen_port, callback_path = _redirect_configuration(
        os.getenv("TIKTOK_REDIRECT_URI", DEFAULT_REDIRECT_URI),
        args.port,
    )

    server = HTTPServer(("127.0.0.1", listen_port), CallbackHandler)
    server.expected_state = state
    server.oauth_result = None
    server.callback_path = callback_path
    port = server.server_address[1]
    redirect_uri = redirect_template.replace(":*", f":{port}", 1)
    authorization_url = (
        AUTH_URL
        + "?"
        + urlencode(
            {
                "client_key": client_key,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": ",".join(sorted(SCOPES)),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )

    print("Opening TikTok OAuth in your default browser…")
    if not webbrowser.open(authorization_url, new=1, autoraise=True):
        server.server_close()
        raise RuntimeError("Could not open your default browser")

    try:
        result = _wait_for_callback(server, timeout=args.timeout)
    finally:
        server.server_close()
    if result["error"]:
        description = _safe_message(result["error_description"])
        detail = f": {description}" if description else ""
        raise RuntimeError(f"TikTok OAuth was denied ({result['error']}){detail}")
    if not result["code"]:
        raise RuntimeError("TikTok OAuth did not return an authorization code")

    with httpx.Client(timeout=30) as client:
        access_token, refresh_token, open_id = _exchange_code(
            client,
            client_key,
            client_secret,
            result["code"],
            verifier,
            redirect_uri,
        )
        display_name = _authorized_user(client, access_token, open_id)

    print(f"TikTok account: {display_name}")
    print(f"TikTok account open_id: {open_id}")
    expected = str(args.expected_open_id or "").strip()
    if expected and not hmac.compare_digest(expected, open_id):
        raise RuntimeError(
            f"Expected TikTok open_id {expected}, but selected {open_id}; .env was not changed"
        )
    if not expected:
        confirmation = input("Save OAuth credentials for this TikTok account? [y/N]: ").strip()
        if confirmation.casefold() not in {"y", "yes", "д", "да"}:
            raise RuntimeError("Cancelled; .env was not changed")

    persist_tiktok_credentials(
        refresh_token=refresh_token,
        open_id=open_id,
        env_path=ENV_PATH,
        db_path=_runtime_db_path(),
    )
    print("TikTok OAuth credentials saved to local .env. Secrets were not printed.")


if __name__ == "__main__":
    main()
