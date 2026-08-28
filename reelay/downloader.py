import asyncio
import json
import shutil
import sys
from fractions import Fraction
from pathlib import Path


class InstagramDownloader:
    def __init__(self, settings):
        self.settings = settings
        self._lock = asyncio.Lock()

    async def download(self, job_id, url):
        async with self._lock:
            job_dir = self._job_dir(job_id)
            self._reset_job_dir(job_dir)

            try:
                return await self._download_once(job_dir, url)
            except Exception as error:
                shutil.rmtree(job_dir, ignore_errors=True)
                if not self.settings.allow_private_sources:
                    raise RuntimeError(self._short_failure(error)) from error
                job_dir.mkdir(parents=True, exist_ok=True)

            profile = self.settings.chrome_profile.strip()
            browser = f"chrome:{profile}" if profile else "chrome"
            try:
                return await self._download_once(job_dir, url, browser)
            except Exception as error:
                shutil.rmtree(job_dir, ignore_errors=True)
                raise RuntimeError(self._short_failure(error)) from error

    @staticmethod
    def _short_failure(error):
        lines = [line for line in str(error).splitlines() if line.strip()]
        message = "\n".join(lines[-3:]) or error.__class__.__name__
        return message[-700:]

    def _job_dir(self, job_id):
        name = str(job_id)
        if not name or Path(name).name != name or name in {".", ".."}:
            raise RuntimeError("Invalid job id")
        return self.settings.video_dir / name

    def _reset_job_dir(self, job_dir):
        shutil.rmtree(job_dir, ignore_errors=True)
        job_dir.mkdir(parents=True, exist_ok=True)

    async def _download_once(self, job_dir, url, browser=None):
        output = job_dir / "video.%(ext)s"
        command = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--no-playlist",
            "--format",
            "bestvideo*+bestaudio/best",
            "--merge-output-format",
            "mp4",
            "--remux-video",
            "mp4",
            "--write-info-json",
            "--output",
            str(output),
            "--print",
            "after_move:filepath",
            "--quiet",
            "--no-progress",
        ]
        if browser:
            command.extend(["--cookies-from-browser", browser])
        command.append(url)

        stdout = await self._run(command)
        source = self._output_path(job_dir, stdout)
        probe = await self._probe(source)
        video = next(
            (stream for stream in probe if stream.get("codec_type") == "video"),
            None,
        )
        audio = next(
            (stream for stream in probe if stream.get("codec_type") == "audio"),
            None,
        )
        if not video:
            raise RuntimeError("Downloaded file has no video stream")

        needs_vertical_canvas = not self._is_9_16(video)
        video_copy = self._video_is_compatible(video)
        audio_copy = not audio or (
            audio.get("codec_name") == "aac"
            and str(audio.get("sample_rate")) == "48000"
        )
        if (
            source.suffix.lower() == ".mp4"
            and not needs_vertical_canvas
            and video_copy
            and audio_copy
        ):
            return source

        target = job_dir / "normalized.mp4"
        ffmpeg = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
        ]
        if needs_vertical_canvas:
            ffmpeg.extend(
                [
                    "-filter_complex",
                    (
                        "[0:v:0]split=2[bgsrc][fgsrc];"
                        "[bgsrc]scale=1080:1920:"
                        "force_original_aspect_ratio=increase:"
                        "force_divisible_by=2,crop=1080:1920,"
                        "gblur=sigma=30,setsar=1[bg];"
                        "[fgsrc]scale=1080:1920:"
                        "force_original_aspect_ratio=decrease:"
                        "force_divisible_by=2,setsar=1[fg];"
                        "[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1,"
                        "format=yuv420p[v]"
                    ),
                    "-map",
                    "[v]",
                    "-map",
                    "0:a:0?",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-crf",
                    "18",
                    "-pix_fmt",
                    "yuv420p",
                    "-r",
                    "30",
                ]
            )
        elif video_copy:
            ffmpeg.extend(["-map", "0:v:0", "-map", "0:a:0?"])
            ffmpeg.extend(["-c:v", "copy"])
        else:
            ffmpeg.extend(
                [
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0?",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-crf",
                    "18",
                    "-pix_fmt",
                    "yuv420p",
                    "-r",
                    "30",
                ]
            )
            if int(video.get("width") or 0) > 1920:
                ffmpeg.extend(["-vf", "scale=1920:-2"])

        if not audio:
            ffmpeg.append("-an")
        elif audio_copy:
            ffmpeg.extend(["-c:a", "copy"])
        else:
            ffmpeg.extend(
                ["-c:a", "aac", "-ar", "48000", "-b:a", "128k"]
            )

        ffmpeg.extend(["-movflags", "+faststart", str(target)])
        await self._run(ffmpeg)
        if not target.is_file():
            raise RuntimeError("ffmpeg finished without creating normalized.mp4")
        source.unlink()
        final_path = job_dir / "video.mp4"
        target.replace(final_path)
        return final_path

    async def _probe(self, path):
        output = await self._run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                (
                    "stream=codec_type,codec_name,width,height,"
                    "r_frame_rate,sample_rate"
                ),
                "-of",
                "json",
                str(path),
            ]
        )
        try:
            return json.loads(output).get("streams", [])
        except json.JSONDecodeError as error:
            raise RuntimeError("ffprobe returned invalid JSON") from error

    @staticmethod
    def _is_9_16(stream):
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        if not width or not height:
            return False
        return abs(width / height - 9 / 16) <= 0.02

    @staticmethod
    def _video_is_compatible(stream):
        if stream.get("codec_name") not in {"h264", "hevc"}:
            return False
        if int(stream.get("width") or 0) > 1920:
            return False
        try:
            frame_rate = float(Fraction(stream.get("r_frame_rate") or "0/1"))
        except (ValueError, ZeroDivisionError):
            return False
        return 23 <= frame_rate <= 60

    def _output_path(self, job_dir, stdout):
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if lines:
            path = Path(lines[-1])
            if path.is_file():
                return path

        candidates = [
            path
            for path in job_dir.glob("video.*")
            if path.is_file()
            and path.suffix not in {".part", ".ytdl", ".json"}
        ]
        if not candidates:
            raise RuntimeError("yt-dlp finished without creating a video file")
        return max(candidates, key=lambda path: path.stat().st_mtime)

    async def _run(self, command):
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise RuntimeError(str(error)) from error

        stdout, stderr = await process.communicate()
        stdout = stdout.decode(errors="replace")
        stderr = stderr.decode(errors="replace")
        if process.returncode:
            lines = [line for line in stderr.splitlines() if line.strip()]
            message = "\n".join(lines[-12:]) or f"Command failed: {process.returncode}"
            raise RuntimeError(message)
        return stdout
