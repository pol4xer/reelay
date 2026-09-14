import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Test discovery must not load a developer's local .env file.
with patch("dotenv.load_dotenv"), patch.dict(os.environ, {}, clear=True):
    from reelay import config

from reelay.config_schema import DEFAULTS, validate_values


class OwnerConfigurationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.enterContext(patch.object(config, "DATA_DIR", Path(temporary.name)))
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    "TELEGRAM_BOT_TOKEN": "test-bot-token",
                    "META_IG_USER_ID": "123",
                    "META_PAGE_ACCESS_TOKEN": "test-page-token",
                    "INSTAGRAM_USERNAME": "test_account",
                },
                clear=True,
            )
        )

    def test_missing_owner_remains_unbound_for_persisted_id_check(self):
        settings = config.Settings()
        self.assertIsNone(settings.telegram_owner_id)
        self.assertEqual(settings.telegram_owner_username, "")
        self.assertEqual(settings.timezone, "UTC")
        self.assertEqual(DEFAULTS["TELEGRAM_OWNER_USERNAME"], "")
        self.assertEqual(DEFAULTS["TIMEZONE"], "UTC")

    def test_explicit_numeric_owner_is_preserved(self):
        os.environ["TELEGRAM_OWNER_ID"] = "123456789"
        self.assertEqual(config.Settings().telegram_owner_id, 123456789)

    def test_explicit_username_is_normalized(self):
        os.environ["TELEGRAM_OWNER_USERNAME"] = " @Example_User "
        self.assertEqual(config.Settings().telegram_owner_username, "example_user")

    def test_invalid_owner_ids_are_rejected(self):
        for owner_id in ("0", "-123", "unknown", "1.5", str(2**63)):
            with self.subTest(owner_id=owner_id):
                os.environ["TELEGRAM_OWNER_ID"] = owner_id
                with self.assertRaisesRegex(RuntimeError, "TELEGRAM_OWNER_ID"):
                    config.Settings()

    def test_invalid_usernames_are_rejected_by_runtime_and_settings_form(self):
        for username in ("some.body", "two users", "https://t.me/example", "x" * 65):
            with self.subTest(username=username):
                os.environ["TELEGRAM_OWNER_USERNAME"] = username
                with self.assertRaisesRegex(RuntimeError, "TELEGRAM_OWNER_USERNAME"):
                    config.Settings()
                self.assertIn(
                    "TELEGRAM_OWNER_USERNAME",
                    validate_values({"TELEGRAM_OWNER_USERNAME": username}),
                )

    def test_explicit_timezone_is_preserved(self):
        os.environ["TIMEZONE"] = "Europe/London"
        self.assertEqual(config.Settings().timezone, "Europe/London")
