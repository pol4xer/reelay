import unittest

from reelay.publishers import Platform
from reelay.services.publishing import (
    compose_caption,
    compose_hashtags,
    success_followup_messages,
    success_message,
)


class CaptionCompositionTests(unittest.TestCase):
    def setUp(self):
        self.job = {
            "id": 42,
            "caption": "Смотри #Cats 😻\n#REELAY уже здесь #fun",
            "tags": "#cats #Travel #FUN #новости",
            "tiktok_publish_id": "publish-id",
        }

    def test_every_non_tiktok_platform_keeps_all_deduplicated_hashtags(self):
        expected = "Смотри 😻\nуже здесь\n\n#Reelay #Cats #fun #Travel #новости"

        for platform in (
            Platform.INSTAGRAM,
            Platform.FACEBOOK,
            Platform.THREADS,
            Platform.YOUTUBE,
        ):
            with self.subTest(platform=platform):
                self.assertEqual(compose_caption(self.job, platform), expected)

    def test_default_caption_is_full_and_starts_with_brand_hashtag(self):
        caption = compose_caption(self.job)

        self.assertEqual(
            caption,
            "Смотри 😻\nуже здесь\n\n#Reelay #Cats #fun #Travel #новости",
        )
        self.assertEqual(caption.split("\n\n")[-1].split()[0], "#Reelay")

    def test_tiktok_counts_user_hashtags_toward_five_hashtag_limit(self):
        job = {
            "caption": "Текст 🎬 #one #two #three #four #five",
            "tags": "#six #seven",
        }

        self.assertEqual(
            compose_caption(job, Platform.TIKTOK),
            "Текст 🎬\n\n#Reelay #one #two #three #four",
        )

    def test_caption_without_any_existing_hashtags_still_gets_brand(self):
        self.assertEqual(
            compose_caption({"caption": "Просто текст ✨", "tags": ""}, Platform.TIKTOK),
            "Просто текст ✨\n\n#Reelay",
        )

    def test_tiktok_success_separates_limited_hashtag_only_followup(self):
        job = {
            "id": 7,
            "caption": "Текст #one #two #three #four #five",
            "tags": "#six",
            "tiktok_publish_id": "publish-id",
        }

        message = success_message(job, [Platform.TIKTOK])
        followups = success_followup_messages(job, [Platform.TIKTOK])

        self.assertNotIn("Текст", message)
        self.assertNotIn("#Reelay", message)
        self.assertNotIn("#five", message)
        self.assertNotIn("#six", message)
        self.assertEqual(followups, ("#Reelay #one #two #three #four",))

    def test_tiktok_hashtag_line_contains_no_caption_or_label(self):
        job = {"caption": "Текст 🎬 #one", "tags": "#two"}

        self.assertEqual(
            compose_hashtags(job, Platform.TIKTOK),
            "#Reelay #one #two",
        )

    def test_not_required_tiktok_checkpoint_has_no_inbox_instructions(self):
        job = {
            "id": 9,
            "caption": "Текст #one",
            "tags": "#two",
            "tiktok_publish_id": "not-required-before-enabled",
        }

        message = success_message(job, [Platform.TIKTOK])
        followups = success_followup_messages(job, [Platform.TIKTOK])

        self.assertEqual(message, "Done #9:")
        self.assertEqual(followups, ())

    def test_disabled_tiktok_has_no_followup_even_with_checkpoint(self):
        job = {
            "id": 10,
            "caption": "Текст #one",
            "tags": "#two",
            "tiktok_publish_id": "publish-id",
        }

        self.assertEqual(success_followup_messages(job, [Platform.INSTAGRAM]), ())

    def test_missing_tiktok_checkpoint_has_no_followup(self):
        job = {
            "id": 11,
            "caption": "Текст #one",
            "tags": "#two",
            "tiktok_publish_id": None,
        }

        self.assertEqual(success_followup_messages(job, [Platform.TIKTOK]), ())

    def test_pending_tiktok_checkpoint_has_no_followup(self):
        job = {
            "id": 12,
            "caption": "Текст #one",
            "tags": "#two",
            "tiktok_publish_id": "pending:upload-id",
        }

        self.assertEqual(success_followup_messages(job, [Platform.TIKTOK]), ())


if __name__ == "__main__":
    unittest.main()
