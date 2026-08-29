import asyncio
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from reelay.media import ReelayWatermarker
from reelay.publishers import Platform, PublisherRegistry, PublishResult
from reelay.services import PublishingService


class _RecordingPublisher:
    def __init__(self, platform):
        self.platform = platform
        self.requests = []

    async def publish(self, request):
        self.requests.append(request)
        return PublishResult(platform=self.platform, media_id=f"{self.platform.value}-id")


class _RecordingWatermarker:
    def __init__(self, derivative):
        self.derivative = derivative
        self.sources = []

    async def prepare(self, source):
        self.sources.append(source)
        return self.derivative


class _Tagger:
    async def generate_title(self, _video_path, _caption):
        return "Test title"


class _DB:
    def __init__(self, job):
        self.job = job

    def record_publish_attempt(self, *_args, **_kwargs):
        return None

    def set_platform_media_id(self, _job_id, platform, media_id):
        column = {
            "instagram": "instagram_media_id",
            "tiktok": "tiktok_publish_id",
        }[platform]
        self.job[column] = media_id
        return True

    def mark_completed(self, _job_id):
        self.job["status"] = "published"
        return True

    def get_job(self, _job_id):
        return self.job

    def set_failed(self, _job_id, error):
        self.job["error"] = error


class PublishingWatermarkRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_tiktok_gets_clean_original_and_other_platforms_share_derivative(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "video.mp4"
            derivative = root / "reelay-watermarked-v2.mp4"
            original.touch()
            derivative.touch()
            job = {
                "id": 7,
                "source_url": "https://example.test/video",
                "video_path": str(original),
                "caption": "caption",
                "tags": "#tag",
                "instagram_media_id": None,
                "tiktok_publish_id": None,
            }
            instagram = _RecordingPublisher(Platform.INSTAGRAM)
            tiktok = _RecordingPublisher(Platform.TIKTOK)
            watermarker = _RecordingWatermarker(derivative)
            service = PublishingService(
                settings=SimpleNamespace(delete_after_publish=False, video_dir=root),
                db=_DB(job),
                publishers=PublisherRegistry([instagram, tiktok]),
                tagger=_Tagger(),
                watermarker=watermarker,
            )

            report = await service._publish_claimed_job(job)

            self.assertEqual(report.outcome, "published")
            self.assertEqual(watermarker.sources, [original])
            self.assertEqual(instagram.requests[0].video_path, derivative.resolve())
            self.assertEqual(tiktok.requests[0].video_path, original.resolve())

    async def test_tiktok_only_retry_does_not_create_watermarked_derivative(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "video.mp4"
            derivative = root / "reelay-watermarked-v2.mp4"
            original.touch()
            derivative.touch()
            job = {
                "id": 8,
                "source_url": "https://example.test/video",
                "video_path": str(original),
                "caption": "",
                "tags": "",
                "instagram_media_id": "already-published",
                "tiktok_publish_id": None,
            }
            instagram = _RecordingPublisher(Platform.INSTAGRAM)
            tiktok = _RecordingPublisher(Platform.TIKTOK)
            watermarker = _RecordingWatermarker(derivative)
            service = PublishingService(
                settings=SimpleNamespace(delete_after_publish=False, video_dir=root),
                db=_DB(job),
                publishers=PublisherRegistry([instagram, tiktok]),
                tagger=_Tagger(),
                watermarker=watermarker,
            )

            report = await service._publish_claimed_job(job)

            self.assertEqual(report.outcome, "published")
            self.assertEqual(watermarker.sources, [])
            self.assertEqual(instagram.requests, [])
            self.assertEqual(tiktok.requests[0].video_path, original.resolve())


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class WatermarkMediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_derivative_and_generated_png_are_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_dir = root / "videos" / "1"
            job_dir.mkdir(parents=True)
            source = job_dir / "video.mp4"
            process = await asyncio.create_subprocess_exec(
                shutil.which("ffmpeg"),
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x345678:s=360x640:r=30:d=0.35",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(source),
            )
            self.assertEqual(await process.wait(), 0)
            settings = SimpleNamespace(
                video_watermark_enabled=True,
                data_dir=root,
                root=Path(__file__).resolve().parents[1],
            )
            watermarker = ReelayWatermarker(settings)

            derivative = await watermarker.prepare(source)
            first_mtime = derivative.stat().st_mtime_ns
            cached = await watermarker.prepare(source)

            self.assertEqual(cached, derivative)
            self.assertEqual(derivative.name, "reelay-watermarked-v2.mp4")
            self.assertEqual(derivative.stat().st_mtime_ns, first_mtime)
            self.assertTrue(watermarker.asset_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
            probe = subprocess.run(
                [
                    shutil.which("ffprobe"),
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=codec_name,pix_fmt,width,height",
                    "-of",
                    "csv=p=0",
                    str(derivative),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(probe.stdout.strip(), "h264,360,640,yuv420p")

    async def test_disabled_watermark_returns_original_without_generating_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "video.mp4"
            source.touch()
            watermarker = ReelayWatermarker(
                SimpleNamespace(
                    video_watermark_enabled=False,
                    data_dir=root,
                    root=Path(__file__).resolve().parents[1],
                )
            )

            self.assertEqual(await watermarker.prepare(source), source.resolve())
            self.assertFalse(watermarker.asset_path.exists())


if __name__ == "__main__":
    unittest.main()
