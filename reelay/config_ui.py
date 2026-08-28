import argparse
import errno
import hmac
import html
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from dotenv import dotenv_values

from .config import DATA_DIR
from .config_schema import (
    ALLOWED_KEYS,
    SECRET_KEYS,
    effective_values,
    normalize_updates,
    public_schema,
    validate_values,
)

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
EXAMPLE_ENV_PATH = ROOT / ".env.example"
STATIC_DIR = Path(__file__).resolve().with_name("config_ui_static")
SERVICE_SCRIPT = ROOT / "scripts" / "service.sh"
DB_PATH = DATA_DIR / "reelay.db"
HOST = "127.0.0.1"
PORT = 8765
MAX_REQUEST_BYTES = 256 * 1024
SERVICE_TIMEOUT_SECONDS = 20
TOKEN_HEADER = "X-Reelay-Token"
ASSIGNMENT = re.compile(r"^(?P<prefix>\s*(?:export\s+)?)(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=.*$")
SAFE_DOTENV_VALUE = re.compile(r"^[A-Za-z0-9_./:@%+,=\-]*$")
TOKEN_META = re.compile(
    r"<meta\b[^>]*\bname=[\"']reelay-token[\"'][^>]*>",
    re.IGNORECASE,
)
HEAD_END = re.compile(r"</head\s*>", re.IGNORECASE)


def read_env(path=ENV_PATH):
    if not path.is_file():
        return {}
    parsed = dotenv_values(path, interpolate=False, encoding="utf-8")
    return {str(key): str(value or "") for key, value in parsed.items() if key}


def _dotenv_value(value):
    if SAFE_DOTENV_VALUE.fullmatch(value):
        return value
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def write_env_updates(path, updates, *, example_path=EXAMPLE_ENV_PATH):
    if not updates:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        original = path.read_text(encoding="utf-8")
    elif example_path and Path(example_path).is_file():
        original = Path(example_path).read_text(encoding="utf-8")
    else:
        original = ""

    found = set()
    lines = []
    for line in original.splitlines():
        match = ASSIGNMENT.match(line)
        key = match.group("key") if match else None
        if key in updates and not line.lstrip().startswith("#"):
            lines.append(f"{match.group('prefix')}{key}={_dotenv_value(updates[key])}")
            found.add(key)
        else:
            lines.append(line)

    missing = [key for key in updates if key not in found]
    if missing and lines and lines[-1]:
        lines.append("")
    lines.extend(f"{key}={_dotenv_value(updates[key])}" for key in missing)
    rendered = "\n".join(lines).rstrip("\n") + "\n"

    temporary_path = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".env.", dir=path.parent)
        temporary_path = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        os.chmod(path, 0o600)
        try:
            directory_descriptor = os.open(path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def sync_runtime_settings(path, updates):
    mirrored = {
        "POSTS_PER_DAY": "posts_per_day",
        "TELEGRAM_OWNER_ID": "telegram_owner_id",
    }
    changes = {mirrored[key]: value for key, value in updates.items() if key in mirrored}
    if not changes or not Path(path).is_file():
        return

    connection = sqlite3.connect(path, timeout=5)
    try:
        for key, value in changes.items():
            if value:
                connection.execute(
                    """
                    INSERT INTO settings(key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (key, value),
                )
            else:
                connection.execute("DELETE FROM settings WHERE key = ?", (key,))
        connection.commit()
    finally:
        connection.close()


def runtime_setting_overrides(path):
    mirrored = {
        "posts_per_day": "POSTS_PER_DAY",
        "telegram_owner_id": "TELEGRAM_OWNER_ID",
    }
    if not Path(path).is_file():
        return {}
    connection = None
    try:
        connection = sqlite3.connect(
            Path(path).resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=2,
        )
        placeholders = ",".join("?" for _ in mirrored)
        rows = connection.execute(
            f"SELECT key, value FROM settings WHERE key IN ({placeholders})",
            tuple(mirrored),
        ).fetchall()
        return {mirrored[key]: str(value) for key, value in rows}
    except sqlite3.Error:
        return {}
    finally:
        if connection is not None:
            connection.close()


def _configured(stored):
    return {key: bool(str(stored.get(key, "")).strip()) for key in ALLOWED_KEYS}


def _public_values(stored):
    values = effective_values(stored)
    for key in SECRET_KEYS:
        values[key] = ""
    return values


def _service_status():
    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.pol4xer.reelay.plist"
    status = {
        "state": "not_installed" if not plist_path.is_file() else "stopped",
        "running": False,
        "installed": plist_path.is_file(),
    }
    if sys.platform != "darwin":
        status["state"] = "unsupported"
        return status

    service = f"gui/{os.getuid()}/com.pol4xer.reelay"
    try:
        result = subprocess.run(
            ["launchctl", "print", service],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        status["state"] = "unknown"
        return status
    if result.returncode != 0:
        return status

    state_match = re.search(r"^\s*state\s*=\s*(\S+)", result.stdout, re.MULTILINE)
    pid_match = re.search(r"^\s*pid\s*=\s*(\d+)", result.stdout, re.MULTILINE)
    launchd_state = state_match.group(1).lower() if state_match else "loaded"
    status["state"] = launchd_state
    status["running"] = launchd_state == "running"
    status["installed"] = True
    if pid_match:
        status["pid"] = int(pid_match.group(1))
    return status


def _queue_status(path=DB_PATH):
    statuses = ("downloading", "queued", "publishing", "published", "failed")
    result = {
        "available": False,
        "paused": False,
        "total": 0,
        "byStatus": {status: 0 for status in statuses},
    }
    if not path.is_file():
        return result

    connection = None
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT status, COUNT(*) AS count FROM jobs GROUP BY status"
        ).fetchall()
        for row in rows:
            if row["status"] in result["byStatus"]:
                result["byStatus"][row["status"]] = int(row["count"])
        result["total"] = sum(result["byStatus"].values())
        paused = connection.execute("SELECT value FROM settings WHERE key = 'paused'").fetchone()
        result["paused"] = bool(paused and paused["value"] == "1")
        attempt = connection.execute(
            """
            SELECT job_id, outcome, platform, created_at
            FROM publish_attempts ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        if attempt:
            result["lastAttempt"] = {
                "jobId": attempt["job_id"],
                "outcome": attempt["outcome"],
                "platform": attempt["platform"],
                "createdAt": attempt["created_at"],
            }
        result["available"] = True
    except sqlite3.Error:
        result["error"] = "queue_unavailable"
    finally:
        if connection is not None:
            connection.close()
    return result


def build_status(env_path=ENV_PATH, db_path=DB_PATH):
    stored = read_env(env_path)
    errors = validate_values(stored)
    enabled = {
        "instagram": True,
        "facebook": stored.get("PUBLISH_FACEBOOK", "false").lower() == "true",
        "threads": stored.get("PUBLISH_THREADS", "false").lower() == "true",
        "youtube": stored.get("PUBLISH_YOUTUBE", "false").lower() == "true",
    }
    config_status = {
        "envExists": env_path.is_file(),
        "valid": not errors,
        "errors": errors,
        "enabledPlatforms": enabled,
    }
    if env_path.is_file():
        config_status["updatedAt"] = int(env_path.stat().st_mtime)
    return {
        "service": _service_status(),
        "queue": _queue_status(db_path),
        "config": config_status,
    }


def _restart_service(script=SERVICE_SCRIPT):
    if not script.is_file():
        return {
            "requested": True,
            "ok": False,
            "error": "service_script_missing",
            "message": "Скрипт управления сервисом не найден.",
        }
    try:
        result = subprocess.run(
            ["bash", str(script), "start"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=SERVICE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "requested": True,
            "ok": False,
            "error": "restart_timeout",
            "message": "Перезапуск не завершился за отведённое время.",
        }
    except OSError:
        return {
            "requested": True,
            "ok": False,
            "error": "restart_unavailable",
            "message": "Не удалось запустить скрипт управления сервисом.",
        }
    if result.returncode == 0:
        return {
            "requested": True,
            "ok": True,
            "message": "Настройки сохранены, сервис перезапущен.",
        }

    combined = (result.stdout + "\n" + result.stderr).lower()
    if "in progress" in combined:
        error = "publish_in_progress"
        message = "Настройки сохранены, но публикация сейчас выполняется. Перезапустите позже."
    elif "not installed" in combined:
        error = "service_not_installed"
        message = "Настройки сохранены, но LaunchAgent ещё не установлен."
    else:
        error = "restart_failed"
        message = "Настройки сохранены, но сервис не удалось перезапустить."
    return {"requested": True, "ok": False, "error": error, "message": message}


class ConfigHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address,
        handler_class,
        *,
        api_token,
        env_path=ENV_PATH,
        static_dir=STATIC_DIR,
        service_script=SERVICE_SCRIPT,
        db_path=DB_PATH,
    ):
        self.api_token = api_token
        self.env_path = Path(env_path)
        self.static_dir = Path(static_dir).resolve()
        self.service_script = Path(service_script)
        self.db_path = Path(db_path)
        self.config_lock = threading.Lock()
        super().__init__(server_address, handler_class)


class ConfigRequestHandler(BaseHTTPRequestHandler):
    server_version = "ReelayConfig/1"
    sys_version = ""

    def log_message(self, format, *args):
        return

    def do_GET(self):
        if not self._is_local_request() or not self._valid_host():
            self._json(HTTPStatus.FORBIDDEN, {"error": "local_requests_only"})
            return
        path = urlsplit(self.path).path
        if path == "/api/config":
            if not self._authorized():
                return
            self._get_config()
            return
        if path in {"/api/status", "/api/config/status"}:
            if not self._authorized():
                return
            self._json(HTTPStatus.OK, build_status(self.server.env_path, self.server.db_path))
            return
        if path.startswith("/api/"):
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        self._serve_static(path, head_only=False)

    def do_HEAD(self):
        if not self._is_local_request() or not self._valid_host():
            self._empty(HTTPStatus.FORBIDDEN)
            return
        path = urlsplit(self.path).path
        if path.startswith("/api/"):
            self._empty(HTTPStatus.METHOD_NOT_ALLOWED, {"Allow": "GET, POST"})
            return
        self._serve_static(path, head_only=True)

    def do_POST(self):
        if not self._is_local_request() or not self._valid_host():
            self._json(HTTPStatus.FORBIDDEN, {"error": "local_requests_only"})
            return
        path = urlsplit(self.path).path
        if path != "/api/config":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        if not self._authorized():
            return
        self._post_config()

    def do_OPTIONS(self):
        self._empty(HTTPStatus.METHOD_NOT_ALLOWED, {"Allow": "GET, HEAD, POST"})

    def _is_local_request(self):
        return self.client_address[0] == "127.0.0.1"

    def _valid_host(self):
        host = (self.headers.get("Host") or "").lower().strip()
        return host in {
            "127.0.0.1",
            f"127.0.0.1:{self.server.server_port}",
            "localhost",
            f"localhost:{self.server.server_port}",
        }

    def _authorized(self):
        supplied = self.headers.get(TOKEN_HEADER, "")
        if supplied and hmac.compare_digest(supplied, self.server.api_token):
            return True
        self._json(HTTPStatus.UNAUTHORIZED, {"error": "invalid_api_token"})
        return False

    def _get_config(self):
        try:
            stored = read_env(self.server.env_path)
            stored.update(runtime_setting_overrides(self.server.db_path))
        except (OSError, UnicodeError):
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "config_unavailable"})
            return
        self._json(
            HTTPStatus.OK,
            {
                "schema": public_schema(),
                "values": _public_values(stored),
                "configured": _configured(stored),
            },
        )

    def _post_config(self):
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip()
        if content_type != "application/json":
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "json_required"})
            return
        if self.headers.get("Transfer-Encoding"):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "chunked_body_not_supported"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._json(HTTPStatus.LENGTH_REQUIRED, {"error": "content_length_required"})
            return
        if not 0 <= content_length <= MAX_REQUEST_BYTES:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request_too_large"})
            return
        try:
            payload = json.loads(self.rfile.read(content_length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return
        if not isinstance(payload, dict):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "object_required"})
            return
        unknown_payload_keys = set(payload) - {"values", "restart"}
        if unknown_payload_keys:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": "unknown_payload_fields", "fields": sorted(unknown_payload_keys)},
            )
            return
        restart = payload.get("restart", False)
        if not isinstance(restart, bool):
            self._json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"error": "validation_failed", "errors": {"restart": "Укажите true или false."}},
            )
            return
        normalized, errors = normalize_updates(payload.get("values"))
        if errors:
            self._json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"error": "validation_failed", "errors": errors},
            )
            return

        try:
            with self.server.config_lock:
                stored = read_env(self.server.env_path)
                merged = effective_values(stored)
                merged.update(normalized)
                errors = validate_values(merged)
                if errors:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": "validation_failed", "errors": errors},
                    )
                    return
                write_env_updates(self.server.env_path, normalized)
                sync_runtime_settings(self.server.db_path, normalized)
                stored = read_env(self.server.env_path)
                stored.update(runtime_setting_overrides(self.server.db_path))
        except (OSError, UnicodeError, sqlite3.Error):
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "config_write_failed"})
            return

        restart_result = (
            _restart_service(self.server.service_script)
            if restart
            else {"requested": False, "ok": True}
        )
        self._json(
            HTTPStatus.OK,
            {
                "ok": True,
                "saved": sorted(normalized),
                "configured": _configured(stored),
                "restart": restart_result,
            },
        )

    def _serve_static(self, request_path, *, head_only):
        try:
            decoded = unquote(request_path, errors="strict")
        except UnicodeError:
            self._empty(HTTPStatus.BAD_REQUEST)
            return
        relative = decoded.lstrip("/") or "index.html"
        candidate = (self.server.static_dir / relative).resolve()
        try:
            candidate.relative_to(self.server.static_dir)
        except ValueError:
            self._empty(HTTPStatus.FORBIDDEN)
            return
        if candidate.is_dir():
            candidate = candidate / "index.html"
        if not candidate.is_file() or any(part.startswith(".") for part in Path(relative).parts):
            self._empty(HTTPStatus.NOT_FOUND)
            return
        try:
            data = candidate.read_bytes()
        except OSError:
            self._empty(HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if candidate.name == "index.html":
            data = self._inject_token(data)
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
        }:
            content_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self._security_headers(no_store=True)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def _inject_token(self, data):
        try:
            document = data.decode("utf-8")
        except UnicodeDecodeError:
            return data
        token = html.escape(self.server.api_token, quote=True)
        meta = f'<meta name="reelay-token" content="{token}">'
        if TOKEN_META.search(document):
            document = TOKEN_META.sub(meta, document, count=1)
        elif HEAD_END.search(document):
            document = HEAD_END.sub(meta + "</head>", document, count=1)
        else:
            document = meta + document
        return document.encode("utf-8")

    def _json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self._security_headers(no_store=True)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _empty(self, status, extra_headers=None):
        self.send_response(status)
        self._security_headers(no_store=True)
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _security_headers(self, *, no_store):
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; "
            "frame-ancestors 'none'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        if no_store:
            self.send_header("Cache-Control", "no-store")


def run(*, open_browser=True, port=PORT):
    api_token = secrets.token_urlsafe(32)
    url = f"http://{HOST}:{port}/"
    try:
        server = ConfigHTTPServer(
            (HOST, port),
            ConfigRequestHandler,
            api_token=api_token,
        )
    except OSError as error:
        if error.errno != errno.EADDRINUSE:
            raise
        print(f"Reelay Settings уже запущен: {url}")
        if open_browser:
            webbrowser.open(url)
        return
    print(f"Reelay settings: {url}")
    print("Press Ctrl+C to stop the local settings server.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Open the local Reelay settings UI")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Serve the settings UI without opening the system browser",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=PORT,
        help=f"Local port (default: {PORT})",
    )
    arguments = parser.parse_args(argv)
    if not 1 <= arguments.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    run(open_browser=not arguments.no_browser, port=arguments.port)


if __name__ == "__main__":
    main()
