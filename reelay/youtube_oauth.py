import argparse
import base64
import hashlib
import hmac
import os
import secrets
import tempfile
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
SCOPES = {
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
}
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
CALLBACK_PATH = "/oauth2/callback"


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_error(404)
            return

        parameters = parse_qs(parsed.query)
        state = parameters.get("state", [""])[0]
        if not hmac.compare_digest(state, self.server.expected_state):
            self.send_error(400, "Invalid OAuth state")
            return

        self.server.oauth_result = {
            "code": parameters.get("code", [""])[0],
            "error": parameters.get("error", [""])[0],
        }
        body = (
            "<!doctype html><meta charset='utf-8'>"
            "<title>Reelay YouTube OAuth</title>"
            "<h2>Авторизация получена</h2>"
            "<p>Эту вкладку можно закрыть и вернуться в терминал.</p>"
        ).encode("utf-8")
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
        raise RuntimeError(f"Добавьте {name} в локальный .env")
    return value


def _pkce():
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _wait_for_callback(server, timeout=300):
    server.timeout = 1
    deadline = time.monotonic() + timeout
    while server.oauth_result is None and time.monotonic() < deadline:
        server.handle_request()
    if server.oauth_result is None:
        raise RuntimeError("Время ожидания Google OAuth истекло")
    return server.oauth_result


def _json_response(response, service):
    try:
        payload = response.json()
    except ValueError as error:
        raise RuntimeError(f"{service} вернул некорректный ответ") from error
    if response.is_error:
        message = ""
        if isinstance(payload, dict):
            api_error = payload.get("error", payload)
            if isinstance(api_error, dict):
                message = api_error.get("message") or ""
            elif isinstance(api_error, str):
                message = api_error
            message = message or payload.get("error_description", "")
        raise RuntimeError(message or f"{service}: HTTP {response.status_code}")
    return payload


def _exchange_code(client, client_id, client_secret, code, verifier, redirect_uri):
    response = client.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    payload = _json_response(response, "Google OAuth")
    access_token = str(payload.get("access_token") or "")
    refresh_token = str(payload.get("refresh_token") or "")
    granted_scopes = set(str(payload.get("scope") or "").split())
    if not access_token:
        raise RuntimeError("Google OAuth не вернул access token")
    if not refresh_token:
        raise RuntimeError(
            "Google OAuth не вернул refresh token; отзовите старый доступ "
            "Reelay и повторите авторизацию"
        )
    missing_scopes = SCOPES - granted_scopes
    if missing_scopes:
        raise RuntimeError(
            "Google не выдал scopes: " + ", ".join(sorted(missing_scopes))
        )
    return access_token, refresh_token


def _authorized_channel(client, access_token):
    response = client.get(
        CHANNELS_URL,
        params={"part": "id,snippet", "mine": "true", "maxResults": "50"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    payload = _json_response(response, "YouTube channels.list")
    channels = payload.get("items") or []
    if not channels:
        raise RuntimeError("У выбранного Google-аккаунта нет YouTube-канала")
    if len(channels) != 1:
        found = ", ".join(
            str(channel.get("id") or "unknown") for channel in channels
        )
        raise RuntimeError(
            "OAuth вернул несколько каналов: "
            f"{found}. Сделайте нужный канал каналом по умолчанию и повторите."
        )
    channel = channels[0]
    channel_id = str(channel.get("id") or "").strip()
    title = str((channel.get("snippet") or {}).get("title") or "").strip()
    if not channel_id or not title:
        raise RuntimeError("YouTube не вернул имя и ID выбранного канала")
    return channel_id, title


def _write_env(refresh_token, channel_id):
    for value in (refresh_token, channel_id):
        if "\n" in value or "\r" in value:
            raise RuntimeError("Некорректное значение для .env")

    original = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    updates = {
        "YOUTUBE_REFRESH_TOKEN": refresh_token,
        "YOUTUBE_CHANNEL_ID": channel_id,
    }
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
            dir=ROOT,
            prefix=".env.youtube.",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            os.chmod(temporary, 0o600)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, ENV_PATH)
        os.chmod(ENV_PATH, 0o600)
        temporary = None
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def main():
    parser = argparse.ArgumentParser(
        description="Получить локальный YouTube OAuth refresh token для Reelay"
    )
    parser.add_argument("--expected-channel-id")
    args = parser.parse_args()

    load_dotenv(ENV_PATH, override=True)
    client_id = _required_env("YOUTUBE_CLIENT_ID")
    client_secret = _required_env("YOUTUBE_CLIENT_SECRET")
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(32)

    server = HTTPServer(("127.0.0.1", 0), CallbackHandler)
    server.expected_state = state
    server.oauth_result = None
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}{CALLBACK_PATH}"
    authorization_url = AUTH_URL + "?" + urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(sorted(SCOPES)),
            "access_type": "offline",
            "prompt": "consent select_account",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )

    print("Открываю Google OAuth в системном браузере…")
    if not webbrowser.open(authorization_url, new=1, autoraise=True):
        server.server_close()
        raise RuntimeError("Не удалось открыть системный браузер")

    try:
        result = _wait_for_callback(server)
    finally:
        server.server_close()
    if result["error"]:
        raise RuntimeError(f"Google OAuth отклонён: {result['error']}")
    if not result["code"]:
        raise RuntimeError("Google OAuth не вернул authorization code")

    with httpx.Client(timeout=30) as client:
        access_token, refresh_token = _exchange_code(
            client,
            client_id,
            client_secret,
            result["code"],
            verifier,
            redirect_uri,
        )
        channel_id, title = _authorized_channel(client, access_token)

    print(f"YouTube channel: {title}")
    print(f"YouTube channel ID: {channel_id}")
    expected = str(args.expected_channel_id or "").strip()
    if expected and not hmac.compare_digest(expected, channel_id):
        raise RuntimeError(
            f"Ожидался канал {expected}, но выбран {channel_id}; .env не изменён"
        )
    if not expected:
        confirmation = input("Сохранить OAuth для этого канала? [y/N]: ").strip()
        if confirmation.casefold() not in {"y", "yes", "д", "да"}:
            raise RuntimeError("Отменено; .env не изменён")

    _write_env(refresh_token, channel_id)
    print("YouTube OAuth сохранён в локальный .env. Секреты не выведены.")


if __name__ == "__main__":
    main()
