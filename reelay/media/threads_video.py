import asyncio
import json
import os
import shutil


class ThreadsVideoPreparer:
    """Creates the strict H.264/AAC-LC MP4 required by Threads."""

    async def prepare(self, source, destination):
        probe = await self._probe(source)
        streams = probe.get("streams") or []
        video = next(
            (stream for stream in streams if stream.get("codec_type") == "video"),
            None,
        )
        if not video:
            raise RuntimeError("Threads source has no video stream")

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg is required for Threads publishing")

        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            os.fspath(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
        ]
        if video.get("codec_name") == "h264" and video.get("pix_fmt") in {
            "yuv420p",
            "yuvj420p",
        }:
            command.extend(["-c:v", "copy"])
        else:
            command.extend(
                [
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-crf",
                    "20",
                    "-pix_fmt",
                    "yuv420p",
                ]
            )
        command.extend(
            [
                "-c:a",
                "aac",
                "-profile:a",
                "aac_low",
                "-b:a",
                "128k",
                "-ar",
                "44100",
                "-movflags",
                "+faststart",
                "-use_editlist",
                "0",
                os.fspath(destination),
            ]
        )
        await self._run(command, "ffmpeg")
        await self._validate(destination)

    async def _validate(self, destination):
        probe = await self._probe(destination)
        streams = probe.get("streams") or []
        video = next(
            (stream for stream in streams if stream.get("codec_type") == "video"),
            {},
        )
        audio = next(
            (stream for stream in streams if stream.get("codec_type") == "audio"),
            None,
        )
        if video.get("codec_name") != "h264":
            raise RuntimeError("Threads prepared video is not H.264")
        if audio and (audio.get("codec_name") != "aac" or audio.get("profile") != "LC"):
            raise RuntimeError("Threads prepared audio is not AAC-LC")
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError("Threads prepared video is empty")

    async def _probe(self, path):
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            raise RuntimeError("ffprobe is required for Threads publishing")
        process = await asyncio.create_subprocess_exec(
            ffprobe,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
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
        _stdout, stderr = await process.communicate()
        if process.returncode:
            detail = stderr.decode("utf-8", "replace").strip()[-700:]
            raise RuntimeError(f"{label} failed: {detail}")
