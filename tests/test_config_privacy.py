import json
import os
import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

with patch("dotenv.load_dotenv"):
    from reelay.config_ui import (
        TOKEN_HEADER,
        ConfigRequestHandler,
        read_env,
        write_env_updates,
    )

from reelay.config_schema import SECRET_KEYS


class ConfigPrivacyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = self.root / ".env"
        self.static = self.root / "static"
        self.static.mkdir()
        self.handler = ConfigRequestHandler.__new__(ConfigRequestHandler)
        self.handler.server = SimpleNamespace(
            env_path=self.env,
            db_path=self.root / "missing.db",
            api_token="local-test-session",
            server_port=8765,
            static_dir=self.static,
        )
        self.handler.headers = {}
        self.handler._json = Mock()
        self.handler._empty = Mock()

    def test_api_returns_configured_flags_but_never_stored_secret_values(self):
        secrets = {key: f"private-fixture-{key.lower()}" for key in SECRET_KEYS}
        write_env_updates(self.env, secrets, example_path=None)
        self.handler._get_config()
        status, body = self.handler._json.call_args.args
        self.assertEqual(status, HTTPStatus.OK)
        serialized = json.dumps(body)
        for key, secret in secrets.items():
            self.assertEqual(body["values"][key], "")
            self.assertTrue(body["configured"][key])
            self.assertNotIn(secret, serialized)

    def test_partial_save_preserves_existing_secrets_and_private_file_permissions(self):
        write_env_updates(self.env, {"TELEGRAM_BOT_TOKEN": "private-fixture"}, example_path=None)
        write_env_updates(self.env, {"TIMEZONE": "UTC"}, example_path=None)
        stored = read_env(self.env)
        self.assertEqual(stored["TELEGRAM_BOT_TOKEN"], "private-fixture")
        self.assertEqual(stored["TIMEZONE"], "UTC")
        if os.name == "posix":
            self.assertEqual(self.env.stat().st_mode & 0o777, 0o600)

    def test_api_requires_the_local_session_header(self):
        self.assertFalse(self.handler._authorized())
        self.assertEqual(self.handler._json.call_args.args[0], HTTPStatus.UNAUTHORIZED)
        self.handler.headers[TOKEN_HEADER] = "local-test-session"
        self.assertTrue(self.handler._authorized())

    def test_unexpected_host_and_paths_cannot_expose_local_env(self):
        self.handler.headers = {"Host": "attacker.example"}
        self.assertFalse(self.handler._valid_host())
        self.handler.headers = {"Host": "127.0.0.1:8765"}
        self.assertTrue(self.handler._valid_host())
        self.env.write_text("TELEGRAM_BOT_TOKEN=private-fixture\n")
        self.handler._serve_static("/%2e%2e/.env", head_only=False)
        self.handler._empty.assert_called_with(HTTPStatus.FORBIDDEN)
