import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

from reelay.scheduler import publish_next
from reelay.services import PublicationReport


class _DB:
    def get_setting(self, _key, default=None):
        return default


class PublicationNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_primary_report_is_followed_by_hashtag_only_message_in_order(self):
        report = PublicationReport(
            outcome="published",
            message="Done #7:\nTikTok Inbox: publish-id",
            job_id=7,
            notify_owner=True,
            followup_messages=("#Reelay #one #two #three #four",),
        )
        service = SimpleNamespace(publish_next=AsyncMock(return_value=report))
        bot = SimpleNamespace(send_message=AsyncMock())
        context = SimpleNamespace(
            application=SimpleNamespace(
                bot_data={
                    "publishing_service": service,
                    "settings": SimpleNamespace(telegram_owner_id="123"),
                    "db": _DB(),
                }
            ),
            bot=bot,
        )

        returned = await publish_next(context)

        self.assertIs(returned, report)
        self.assertEqual(
            bot.send_message.await_args_list,
            [
                call(chat_id=123, text=report.message),
                call(chat_id=123, text="#Reelay #one #two #three #four"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
