import asyncio
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from reelay.db import PLATFORM_MEDIA_COLUMNS, QueueDB
from reelay.publishers import Platform, PublisherRegistry, PublishResult
from reelay.services.publishing import PLATFORM_LABELS, PublishingService


class FakePublisher:
    def __init__(self, platform, db, calls):
        self.platform = platform
        self.db = db
        self.calls = calls
        self.error = None
        self.media_id = f"{platform.value}-id"
        self.requests = []
        self.snapshots = []

    async def publish(self, request):
        self.requests.append(request)
        self.calls.append(self.platform)
        self.snapshots.append(self.db.get_job(request.job_id))
        if self.error is not None:
            raise self.error
        return PublishResult(self.platform, self.media_id)


class FakeWatermarker:
    def __init__(self, derivative):
        self.derivative = derivative
        self.calls = []
        self.error = None

    async def prepare(self, video):
        self.calls.append(video)
        if self.error is not None:
            raise self.error
        return self.derivative


class FakeTagger:
    def __init__(self):
        self.error = None

    async def generate_title(self, _video, _caption):
        if self.error is not None:
            raise self.error
        return "Test title"


class IndependentPublishingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._create_fixture()

    def _create_fixture(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.db = QueueDB(self.root / "queue.sqlite3")
        self.db.init()
        self.job_id = self.db.create_job(
            "https://www.instagram.com/reel/test-source/", "test-source", "Caption #one #two"
        )
        self.video_dir = self.root / "videos"
        job_dir = self.video_dir / str(self.job_id)
        job_dir.mkdir(parents=True)
        self.video = job_dir / "video.mp4"
        self.derivative = job_dir / "reelay-watermarked.mp4"
        self.video.touch()
        self.derivative.touch()
        self.db.set_downloaded(self.job_id, str(self.video), "#three #four #five")
        self.calls = []
        self.publishers = {
            platform: FakePublisher(platform, self.db, self.calls) for platform in Platform
        }
        self.watermarker = FakeWatermarker(self.derivative)
        self.tagger = FakeTagger()
        self.service = PublishingService(
            SimpleNamespace(
                timezone="UTC",
                post_on_weekends=True,
                delete_after_publish=True,
                video_dir=self.video_dir,
            ),
            self.db,
            PublisherRegistry(self.publishers.values()),
            self.tagger,
            self.watermarker,
        )

    async def test_failure_at_every_position_does_not_block_other_destinations(self):
        for failed_platform in Platform:
            with self.subTest(platform=failed_platform):
                if failed_platform is not Platform.INSTAGRAM:
                    self._create_fixture()
                self.publishers[failed_platform].error = RuntimeError("platform unavailable")

                with patch.object(self.db, "set_failed", wraps=self.db.set_failed) as set_failed:
                    report = await self.service.publish_next()

                self.assertEqual(self.calls, list(Platform))
                self.assertEqual(report.outcome, "failed")
                self.assertEqual(report.platform, failed_platform)
                self.assertTrue(report.notify_owner)
                set_failed.assert_called_once()
                saved = self.db.get_job(self.job_id)
                self.assertEqual(saved["status"], "failed")
                self.assertIsNone(saved["published_at"])
                for platform, publisher in self.publishers.items():
                    self.assertEqual(publisher.snapshots[0]["status"], "publishing")
                    checkpoint = saved[PLATFORM_MEDIA_COLUMNS[platform.value]]
                    if platform is failed_platform:
                        self.assertIsNone(checkpoint)
                    else:
                        self.assertEqual(checkpoint, publisher.media_id)
                        self.assertIn(f"отправлено · {checkpoint}", report.message)
                self.assertTrue(self.video.is_file())
                self.assertTrue(self.derivative.is_file())
                self.assertIn(saved["source_url"], report.message)
                self.assertIn(f"/retry {self.job_id}", report.message)
                self.assertEqual(
                    bool(report.followup_messages), failed_platform is not Platform.TIKTOK
                )

    async def test_all_platform_errors_are_reported_and_persisted_together(self):
        failed = (Platform.INSTAGRAM, Platform.THREADS, Platform.YOUTUBE)
        for platform in failed:
            self.publishers[platform].error = RuntimeError(f"unavailable-{platform.value}")

        report = await self.service.publish_next()

        saved = self.db.get_job(self.job_id)
        self.assertEqual(self.calls, list(Platform))
        for platform in failed:
            self.assertIn(f"{PLATFORM_LABELS[platform]}: ошибка", report.message)
            self.assertIn(f"unavailable-{platform.value}", report.message)
            self.assertIn(f"unavailable-{platform.value}", saved["error"])
        with closing(self.db._connect()) as connection:
            logged_platforms = {
                row["platform"]
                for row in connection.execute(
                    "SELECT platform FROM publish_attempts WHERE outcome = 'failed'"
                )
            }
        self.assertEqual(logged_platforms, {platform.value for platform in failed})

    async def test_youtube_invalid_grant_keeps_tiktok_delivery_and_hashtag_followup(self):
        self.publishers[Platform.YOUTUBE].error = RuntimeError("invalid_grant")

        report = await self.service.publish_next()

        self.assertEqual(report.outcome, "failed")
        tiktok = self.publishers[Platform.TIKTOK]
        self.assertEqual(tiktok.requests[0].video_path, self.video.resolve())
        self.assertEqual(tiktok.snapshots[0]["instagram_media_id"], "instagram-id")
        self.assertEqual(tiktok.snapshots[0]["facebook_media_id"], "facebook-id")
        self.assertEqual(tiktok.snapshots[0]["threads_media_id"], "threads-id")
        self.assertIn("TikTok Inbox: отправлено · tiktok-id", report.message)
        self.assertIn("из следующего сообщения", report.message)
        self.assertEqual(report.followup_messages, ("#Reelay #one #two #three #four",))

    async def test_long_errors_fit_telegram_and_preserve_tiktok_followup_and_database_details(self):
        errors = {}
        for platform, publisher in self.publishers.items():
            if platform is not Platform.TIKTOK:
                errors[platform.value] = f"unavailable-{platform.value} " + "💥" * 5000
                publisher.error = RuntimeError(errors[platform.value])
        tiktok_id = "tiktok-id-" + "x" * 5000
        self.publishers[Platform.TIKTOK].media_id = tiktok_id
        self.db._update(
            "UPDATE jobs SET source_url = ? WHERE id = ?",
            ("https://www.instagram.com/reel/test-source/?query=" + "x" * 5000, self.job_id),
        )

        with self.assertLogs("reelay.services.publishing", level="ERROR"):
            report = await self.service.publish_next()

        self.assertLessEqual(len(report.message.encode("utf-16-le")) // 2, 4096)
        self.assertIn("TikTok Inbox: отправлено · tiktok-id-", report.message)
        self.assertIn("из следующего сообщения", report.message)
        self.assertIn(f"/retry {self.job_id}", report.message)
        self.assertEqual(report.followup_messages, ("#Reelay #one #two #three #four",))
        saved = self.db.get_job(self.job_id)
        self.assertEqual(saved["tiktok_publish_id"], tiktok_id)
        for platform, error in errors.items():
            self.assertIn(f"unavailable-{platform}", report.message)
            self.assertIn(error, saved["error"])
        with closing(self.db._connect()) as connection:
            details = {
                row["platform"]: row["detail"]
                for row in connection.execute(
                    "SELECT platform, detail FROM publish_attempts WHERE outcome = 'failed'"
                )
            }
        # The audit table retains its existing 2,000-character tail limit;
        # jobs.error above keeps every full provider error.
        self.assertEqual(details, {platform: error[-2000:] for platform, error in errors.items()})

    async def test_long_success_ids_fit_telegram_without_truncating_saved_checkpoints(self):
        for publisher in self.publishers.values():
            publisher.media_id += "x" * 5000

        report = await self.service.publish_next()

        self.assertEqual(report.outcome, "published")
        self.assertLessEqual(len(report.message.encode("utf-16-le")) // 2, 4096)
        self.assertTrue(report.followup_messages)
        saved = self.db.get_job(self.job_id)
        for platform, publisher in self.publishers.items():
            self.assertIn(f"{PLATFORM_LABELS[platform]}: {platform.value}-id", report.message)
            self.assertEqual(saved[PLATFORM_MEDIA_COLUMNS[platform.value]], publisher.media_id)

    async def test_retry_only_publishes_missing_platform_and_cleans_up_when_complete(self):
        youtube = self.publishers[Platform.YOUTUBE]
        youtube.error = RuntimeError("invalid_grant")
        await self.service.publish_next()
        youtube.error = None
        self.assertTrue(self.db.retry(self.job_id))
        self.calls.clear()

        report = await self.service.publish_next()

        self.assertEqual(self.calls, [Platform.YOUTUBE])
        self.assertEqual(report.outcome, "published")
        saved = self.db.get_job(self.job_id)
        self.assertEqual(saved["status"], "published")
        self.assertIsNotNone(saved["published_at"])
        self.assertIsNone(saved["error"])
        for platform, publisher in self.publishers.items():
            self.assertEqual(saved[PLATFORM_MEDIA_COLUMNS[platform.value]], publisher.media_id)
            self.assertEqual(len(publisher.requests), 2 if platform is Platform.YOUTUBE else 1)
        self.assertEqual(report.followup_messages, ())
        self.assertNotIn("нажмите Publish", report.message)
        self.assertFalse(self.video.parent.exists())

    async def test_failed_retry_reports_previously_saved_ids_without_new_tiktok_instructions(self):
        self.publishers[Platform.YOUTUBE].error = RuntimeError("invalid_grant")
        await self.service.publish_next()
        self.assertTrue(self.db.retry(self.job_id))
        self.calls.clear()

        report = await self.service.publish_next()

        self.assertEqual(self.calls, [Platform.YOUTUBE])
        self.assertEqual(report.outcome, "failed")
        self.assertIn("TikTok Inbox: отправлено ранее · tiktok-id", report.message)
        self.assertEqual(report.followup_messages, ())
        self.assertNotIn("нажмите Publish", report.message)
        self.assertTrue(self.video.is_file())

    async def test_pending_tiktok_upload_is_retried_and_not_reported_as_delivered_on_error(self):
        self.db.start_tiktok_upload(self.job_id, "upload-id", "https://upload.example.test/video")
        self.publishers[Platform.TIKTOK].error = RuntimeError("still processing")

        report = await self.service.publish_next()

        saved = self.db.get_job(self.job_id)
        self.assertEqual(len(self.publishers[Platform.TIKTOK].requests), 1)
        self.assertEqual(saved["tiktok_publish_id"], "pending:upload-id")
        self.assertEqual(saved["tiktok_upload_url"], "https://upload.example.test/video")
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(report.followup_messages, ())
        self.assertNotIn("TikTok Inbox: отправлено", report.message)
        self.assertTrue(self.video.is_file())

    async def test_pending_result_is_not_a_completed_checkpoint_and_keeps_resume_state(self):
        self.db.start_tiktok_upload(self.job_id, "upload-id", "https://upload.example.test/video")
        self.publishers[Platform.TIKTOK].media_id = "pending:upload-id"

        report = await self.service.publish_next()

        saved = self.db.get_job(self.job_id)
        self.assertEqual(report.outcome, "failed")
        self.assertEqual(saved["tiktok_upload_url"], "https://upload.example.test/video")
        self.assertEqual(report.followup_messages, ())
        self.assertTrue(self.video.is_file())

    async def test_pending_tiktok_can_complete_and_get_its_followup(self):
        self.db.start_tiktok_upload(self.job_id, "upload-id", "https://upload.example.test/video")
        self.publishers[Platform.TIKTOK].media_id = "upload-id"

        report = await self.service.publish_next()

        saved = self.db.get_job(self.job_id)
        self.assertEqual(report.outcome, "published")
        self.assertEqual(saved["tiktok_publish_id"], "upload-id")
        self.assertIsNone(saved["tiktok_upload_url"])
        self.assertEqual(report.followup_messages, ("#Reelay #one #two #three #four",))

    async def test_watermark_failure_is_cached_but_tiktok_still_uses_original(self):
        self.watermarker.error = RuntimeError("ffmpeg failed")

        report = await self.service.publish_next()

        self.assertEqual(self.watermarker.calls, [self.video])
        self.assertEqual(self.calls, [Platform.TIKTOK])
        self.assertEqual(
            self.publishers[Platform.TIKTOK].requests[0].video_path, self.video.resolve()
        )
        self.assertEqual(report.outcome, "failed")
        for platform in Platform:
            if platform is not Platform.TIKTOK:
                self.assertIn(
                    f"{PLATFORM_LABELS[platform]}: ошибка · ffmpeg failed", report.message
                )
        self.assertEqual(report.followup_messages, ("#Reelay #one #two #three #four",))
        self.assertTrue(self.video.is_file())

        self.watermarker.error = None
        self.assertTrue(self.db.retry(self.job_id))
        self.calls.clear()
        report = await self.service.publish_next()

        self.assertEqual(
            self.calls, [platform for platform in Platform if platform is not Platform.TIKTOK]
        )
        self.assertEqual(self.watermarker.calls, [self.video, self.video])
        self.assertEqual(report.outcome, "published")

    async def test_title_generation_failure_is_local_to_youtube(self):
        self.tagger.error = RuntimeError("title generation failed")

        report = await self.service.publish_next()

        self.assertEqual(
            self.calls, [platform for platform in Platform if platform is not Platform.YOUTUBE]
        )
        self.assertEqual(report.outcome, "failed")
        self.assertIn("YouTube: ошибка · title generation failed", report.message)
        self.assertTrue(report.followup_messages)

    async def test_cancellation_propagates_and_keeps_checkpoints_and_media(self):
        self.publishers[Platform.FACEBOOK].error = asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await self.service.publish_next()

        saved = self.db.get_job(self.job_id)
        self.assertEqual(self.calls, [Platform.INSTAGRAM, Platform.FACEBOOK])
        self.assertEqual(saved["status"], "publishing")
        self.assertEqual(saved["instagram_media_id"], "instagram-id")
        self.assertTrue(self.video.is_file())
        self.assertFalse(self.service._lock.locked())

    async def test_checkpoint_save_failure_stops_before_more_external_side_effects(self):
        with patch.object(self.db, "set_platform_media_id", return_value=False):
            report = await self.service.publish_next()

        saved = self.db.get_job(self.job_id)
        self.assertEqual(self.calls, [Platform.INSTAGRAM])
        self.assertEqual(saved["status"], "failed")
        self.assertIsNone(saved["instagram_media_id"])
        self.assertIn("Отправка вернула ID instagram-id", report.message)
        self.assertIn("восстановите ID", report.message)
        self.assertNotIn("/retry", report.message)
        self.assertTrue(self.video.is_file())


if __name__ == "__main__":
    unittest.main()
