import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from reelay.app import _validate_owner_configuration
from reelay.bot import start


class FakeOwnerDB:
    def __init__(self, owner=None):
        self.owner = owner
        self.writes = []

    def get_setting(self, _key):
        return self.owner

    def set_setting(self, key, value):
        self.writes.append((key, value))
        self.owner = value


class OwnerConfigurationTests(unittest.TestCase):
    def test_new_install_requires_an_explicit_owner(self):
        settings = SimpleNamespace(telegram_owner_id=None, telegram_owner_username="")
        with self.assertRaisesRegex(RuntimeError, "Configure TELEGRAM_OWNER_ID"):
            _validate_owner_configuration(settings, FakeOwnerDB())

    def test_persisted_owner_is_enough_after_a_private_install_is_migrated(self):
        settings = SimpleNamespace(telegram_owner_id=None, telegram_owner_username="")
        _validate_owner_configuration(settings, FakeOwnerDB("12345"))

    def test_corrupt_persisted_owner_fails_closed(self):
        settings = SimpleNamespace(telegram_owner_id=12345, telegram_owner_username="")
        for value in ("broken", "0", "-1"):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                _validate_owner_configuration(settings, FakeOwnerDB(value))


class OwnerBootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def call_start(
        self,
        *,
        configured_username="",
        configured_id=None,
        username=None,
        user_id=12345,
        stored=None,
    ):
        db = FakeOwnerDB(stored)
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(type="private"),
            effective_user=SimpleNamespace(id=user_id, username=username),
            effective_message=message,
        )
        context = SimpleNamespace(
            application=SimpleNamespace(
                bot_data={
                    "db": db,
                    "settings": SimpleNamespace(
                        telegram_owner_id=configured_id,
                        telegram_owner_username=configured_username,
                    ),
                }
            )
        )
        await start(update, context)
        return db, message

    async def test_missing_username_cannot_claim_unconfigured_bot(self):
        db, message = await self.call_start()
        self.assertEqual(db.writes, [])
        message.reply_text.assert_not_awaited()

    async def test_only_the_explicit_username_can_claim_a_new_bot(self):
        db, message = await self.call_start(
            configured_username="example_owner", username="stranger"
        )
        self.assertEqual(db.writes, [])
        message.reply_text.assert_not_awaited()
        db, message = await self.call_start(
            configured_username="example_owner", username="Example_Owner"
        )
        self.assertEqual(db.writes, [("telegram_owner_id", 12345)])
        message.reply_text.assert_awaited_once()

    async def test_explicit_id_can_authorize_an_account_without_username(self):
        db, message = await self.call_start(configured_id=12345)
        self.assertEqual(db.owner, 12345)
        message.reply_text.assert_awaited_once()

    async def test_stored_id_cannot_be_replaced_by_username_claim(self):
        db, message = await self.call_start(
            configured_username="example_owner", username="example_owner", stored="98765"
        )
        self.assertEqual(db.owner, "98765")
        self.assertEqual(db.writes, [])
        message.reply_text.assert_not_awaited()
