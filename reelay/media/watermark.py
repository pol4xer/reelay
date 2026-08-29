import asyncio
import json
import os
import shutil
from pathlib import Path

WATERMARKED_VIDEO_NAME = "reelay-watermarked-v2.mp4"
WATERMARK_ASSET_NAME = "reelay-logo-watermark-v2.png"
WATERMARK_SOURCE_NAME = "reelay-telegram-avatar.png"
WATERMARK_OPACITY = 0.23
FFMPEG_TIMEOUT_SECONDS = 30 * 60


class ReelayWatermarker:
    """Creates and caches one subtly branded derivative beside a queue video's source."""

    def __init__(self, settings):
        self.enabled = settings.video_watermark_enabled
        self.source_asset_path = Path(settings.root) / "assets" / WATERMARK_SOURCE_NAME
        self.asset_path = Path(settings.data_dir) / "media-cache" / WATERMARK_ASSET_NAME

    async def prepare(self, source):
        source = Path(source).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Video is missing: {source}")
        if not self.enabled:
            return source

        destination = source.parent / WATERMARKED_VIDEO_NAME
        if await self._is_valid_video(destination):
            return destination

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg is required to add the Reelay watermark")

        asset = await self._ensure_watermark_asset(ffmpeg)
        probe = await self._probe(source)
        video = next(
            (
                stream
                for stream in probe.get("streams") or []
                if stream.get("codec_type") == "video"
            ),
            None,
        )
        if not video:
            raise RuntimeError("Watermark source has no video stream")

        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        if width <= 0 or height <= 0:
            raise RuntimeError("Watermark source dimensions are unavailable")

        watermark_width = _even(max(48, min(120, round(width * 0.105))))
        margin_x = max(16, round(width * 0.03))
        margin_y = max(16, round(height * 0.03))
        filter_graph = (
            f"[1:v:0]scale={watermark_width}:-2:flags=lanczos,format=rgba,"
            f"colorchannelmixer=aa={WATERMARK_OPACITY}[watermark];"
            f"[0:v:0][watermark]overlay=x={margin_x}:"
            f"y={margin_y}:format=auto:shortest=1,"
            "format=yuv420p[video]"
        )

        temporary = destination.with_name(f".{destination.name}.tmp.mp4")
        temporary.unlink(missing_ok=True)
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            os.fspath(source),
            "-loop",
            "1",
            "-i",
            os.fspath(asset),
            "-filter_complex",
            filter_graph,
            "-map",
            "[video]",
            "-map",
            "0:a:0?",
            "-map_metadata",
            "0",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "19",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            "-shortest",
            os.fspath(temporary),
        ]
        try:
            await self._run(command, "Reelay watermark ffmpeg")
            if not await self._is_valid_video(temporary):
                raise RuntimeError("Reelay watermark output is invalid")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    async def _ensure_watermark_asset(self, ffmpeg):
        source = self.source_asset_path
        if not source.is_file():
            raise FileNotFoundError(f"Reelay watermark logo is missing: {source}")
        if await self._is_current_asset(source):
            return self.asset_path

        self.asset_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.asset_path.with_name(f".{self.asset_path.name}.tmp.png")
        temporary.unlink(missing_ok=True)
        circle_alpha = (
            "if(lte((X-W/2)*(X-W/2)+(Y-H/2)*(Y-H/2),(min(W,H)/2-2)*(min(W,H)/2-2)),255,0)"
        )
        filter_graph = (
            "scale=512:512:force_original_aspect_ratio=increase,"
            "crop=512:512,format=rgba,"
            "geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
            f"a='{circle_alpha}'"
        )
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            os.fspath(source),
            "-vf",
            filter_graph,
            "-frames:v",
            "1",
            os.fspath(temporary),
        ]
        try:
            await self._run(command, "Reelay watermark asset ffmpeg")
            if not await self._is_valid_asset(temporary):
                raise RuntimeError("Generated Reelay watermark asset is invalid")
            os.replace(temporary, self.asset_path)
        finally:
            temporary.unlink(missing_ok=True)
        return self.asset_path

    async def _is_current_asset(self, source):
        if not self.asset_path.is_file():
            return False
        if self.asset_path.stat().st_mtime_ns < source.stat().st_mtime_ns:
            return False
        return await self._is_valid_asset(self.asset_path)

    async def _is_valid_asset(self, path):
        path = Path(path)
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        try:
            probe = await self._probe(path)
        except RuntimeError:
            return False
        image = next(
            (
                stream
                for stream in probe.get("streams") or []
                if stream.get("codec_type") == "video"
            ),
            {},
        )
        return (
            image.get("codec_name") == "png"
            and image.get("pix_fmt") == "rgba"
            and int(image.get("width") or 0) == 512
            and int(image.get("height") or 0) == 512
        )

    async def _is_valid_video(self, path):
        path = Path(path)
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        try:
            probe = await self._probe(path)
        except RuntimeError:
            return False
        video = next(
            (
                stream
                for stream in probe.get("streams") or []
                if stream.get("codec_type") == "video"
            ),
            {},
        )
        return video.get("codec_name") == "h264" and video.get("pix_fmt") == "yuv420p"

    async def _probe(self, path):
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            raise RuntimeError("ffprobe is required to add the Reelay watermark")
        process = await asyncio.create_subprocess_exec(
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,pix_fmt,width,height",
            "-of",
            "json",
            os.fspath(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode:
            detail = stderr.decode("utf-8", "replace").strip()[-700:]
            raise RuntimeError(f"ffprobe failed: {detail}")
        try:
            return json.loads(stdout)
        except (TypeError, ValueError) as error:
            raise RuntimeError("ffprobe returned invalid JSON") from error

    @staticmethod
    async def _run(command, label):
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        communication = asyncio.create_task(process.communicate())
        try:
            _stdout, stderr = await asyncio.wait_for(
                asyncio.shield(communication),
                timeout=FFMPEG_TIMEOUT_SECONDS,
            )
        except (TimeoutError, asyncio.CancelledError):
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=10)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            await asyncio.gather(communication, return_exceptions=True)
            raise
        if process.returncode:
            detail = stderr.decode("utf-8", "replace").strip()[-700:]
            raise RuntimeError(f"{label} failed: {detail}")


def _even(value):
    return value if value % 2 == 0 else value + 1
