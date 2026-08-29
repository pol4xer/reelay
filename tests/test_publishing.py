import unittest

from reelay.publishers import Platform
from reelay.services.publishing import compose_caption, success_message


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

    def test_tiktok_success_message_contains_limited_manual_caption(self):
        job = {
            "id": 7,
            "caption": "Текст #one #two #three #four #five",
            "tags": "#six",
            "tiktok_publish_id": "publish-id",
        }

        message = success_message(job, [Platform.TIKTOK])

        self.assertIn("Caption для копирования:\n", message)
        self.assertIn("Текст\n\n#Reelay #one #two #three #four", message)
        self.assertNotIn("#five", message)
        self.assertNotIn("#six", message)

    def test_long_tiktok_copy_caption_keeps_hashtag_line(self):
        job = {
            "id": 8,
            "caption": f"{'Очень длинный текст 🎬 ' * 200}#one #two",
            "tags": "#three #four #five",
            "tiktok_publish_id": "publish-id",
        }

        message = success_message(job, [Platform.TIKTOK])
        manual_caption = message.split("Caption для копирования:\n", 1)[1]

        self.assertLessEqual(len(manual_caption), 2500)
        self.assertTrue(manual_caption.endswith("#Reelay #one #two #three #four"))

    def test_not_required_tiktok_checkpoint_has_no_inbox_instructions(self):
        job = {
            "id": 9,
            "caption": "Текст #one",
            "tags": "#two",
            "tiktok_publish_id": "not-required-before-enabled",
        }

        message = success_message(job, [Platform.TIKTOK])

        self.assertEqual(message, "Готово #9:")
        self.assertNotIn("Caption для копирования", message)


if __name__ == "__main__":
    unittest.main()
