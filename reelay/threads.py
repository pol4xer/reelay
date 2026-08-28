import asyncio
import json
import os
import re
import shutil
import tempfile
import threading
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import httpx


QUICK_TUNNEL_URL = re.compile(
    r"https://[a-z0-9-]+\.trycloudflare\.com",
    re.IGNORECASE,
)


class _VideoHandler(BaseHTTPRequestHandler):
    server_version = "ReelayVideo/1"

    def do_HEAD(self):
        self._serve(send_body=False)

    def do_GET(self):
        self._serve(send_body=True)

    def _serve(self, send_body):
        if urlsplit(self.path).path != "/video.mp4":
            self.send_error(404)
            return

        video_path = self.server.video_path
        size = video_path.stat().st_size
        start, end = 0, size - 1
        range_header = self.headers.get("Range", "").strip()
        partial_response = False

        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
            if not match or not any(match.groups()):
                self._range_not_satisfiable(size)
                return
            first, last = match.groups()
            if first:
                start = int(first)
                end = int(last) if last else size - 1
            else:
                length = int(last)
                if length <= 0:
                    self._range_not_satisfiable(size)
                    return
                start = max(0, size - length)
                end = size - 1
            if start >= size or start > end:
                self._range_not_satisfiable(size)
                return
            end = min(end, size - 1)
            partial_response = True

        content_length = end - start + 1
        self.send_response(206 if partial_response else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(content_length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if partial_response:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()

        if not send_body:
            return

        try:
            with video_path.open("rb") as video:
                video.seek(start)
                remaining = content_length
                while remaining:
                    chunk = video.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _range_not_satisfiable(self, size):
        self.send_response(416)
        self.send_header("Content-Range", f"bytes */{size}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, _format, *_args):
        pass


class _VideoServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, video_path):
        self.video_path = Path(video_path)
        super().__init__(("127.0.0.1", 0), _VideoHandler)


class ThreadsPublisher:
    def __init__(self, settings):
        self.api_base = (
            f"https://graph.threads.net/{settings.threads_api_version}"
        )
        self.access_token = settings.threads_access_token
        self.cloudflared_path = (
            Path(settings.data_dir) / "bin" / "cloudflared"
        )

    async def publish(self, video_path, caption=""):
        source = Path(video_path).expanduser().resolve()
        if not source.is_file():
            raise RuntimeError(f"Threads video is missing: {source}")

        with tempfile.TemporaryDirectory(prefix="reelay-threads-") as workdir:
            prepared = Path(workdir) / "video.mp4"
            await self._prepare_video(source, prepared)

            async with self._public_video_url(prepared) as video_url:
                timeout = httpx.Timeout(300.0, connect=30.0)
                async with httpx.AsyncClient(timeout=timeout) as client:
                    container = await self._request_json(
                        client,
                        "POST",
                        f"{self.api_base}/me/threads",
                        params={
                            "media_type": "VIDEO",
                            "video_url": video_url,
                            "text": (caption or "").strip()[:500],
                        },
                    )
                    container_id = container.get("id")
                    if not container_id:
                        raise RuntimeError(
                            "Threads did not return a container id"
                        )

                    await self._wait_until_ready(client, str(container_id))
                    published = await self._request_json(
                        client,
                        "POST",
                        f"{self.api_base}/me/threads_publish",
                        params={"creation_id": container_id},
                    )
                    thread_id = published.get("id")
                    if not thread_id:
                        raise RuntimeError(
                            "Threads did not return a published post id"
                        )
                    return str(thread_id)

    async def _prepare_video(self, source, destination):
        probe = await self._probe_video(source)
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
        if (
            video.get("codec_name") == "h264"
            and video.get("pix_fmt") in {"yuv420p", "yuvj420p"}
        ):
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
        await self._run_media_command(command, "ffmpeg")

        prepared_probe = await self._probe_video(destination)
        prepared_streams = prepared_probe.get("streams") or []
        prepared_video = next(
            (
                stream
                for stream in prepared_streams
                if stream.get("codec_type") == "video"
            ),
            {},
        )
        prepared_audio = next(
            (
                stream
                for stream in prepared_streams
                if stream.get("codec_type") == "audio"
            ),
            None,
        )
        if prepared_video.get("codec_name") != "h264":
            raise RuntimeError("Threads prepared video is not H.264")
        if prepared_audio and (
            prepared_audio.get("codec_name") != "aac"
            or prepared_audio.get("profile") != "LC"
        ):
            raise RuntimeError("Threads prepared audio is not AAC-LC")
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError("Threads prepared video is empty")

    async def _probe_video(self, path):
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

    async def _run_media_command(self, command, label):
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await process.communicate()
        if process.returncode:
            detail = stderr.decode("utf-8", "replace").strip()[-700:]
            raise RuntimeError(f"{label} failed: {detail}")

    @asynccontextmanager
    async def _public_video_url(self, video_path):
        server = _VideoServer(video_path)
        server_thread = threading.Thread(
            target=server.serve_forever,
            name="reelay-threads-video",
            daemon=True,
        )
        server_thread.start()

        process = None
        drain_task = None
        try:
            executable = self._resolve_cloudflared()
            local_url = f"http://127.0.0.1:{server.server_port}"
            process = await asyncio.create_subprocess_exec(
                os.fspath(executable),
                "tunnel",
                "--no-autoupdate",
                "--url",
                local_url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            public_base = await self._read_tunnel_url(process)
            drain_task = asyncio.create_task(
                self._drain_process_output(process.stdout)
            )
            public_video_url = f"{public_base}/video.mp4"
            await self._validate_public_video(public_video_url, process)
            yield public_video_url
        finally:
            if process is not None and process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=10)
                except asyncio.TimeoutError:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await process.wait()
            if drain_task is not None:
                drain_task.cancel()
                await asyncio.gather(drain_task, return_exceptions=True)
            server.shutdown()
            server.server_close()
            await asyncio.to_thread(server_thread.join, 5)

    def _resolve_cloudflared(self):
        candidates = [self.cloudflared_path]
        from_path = shutil.which("cloudflared")
        if from_path:
            candidates.append(Path(from_path))
        for candidate in candidates:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
        raise RuntimeError(
            "cloudflared is missing; expected executable at "
            f"{self.cloudflared_path}"
        )

    async def _read_tunnel_url(self, process):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 45
        recent_lines = []
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                detail = " | ".join(recent_lines[-4:])[-700:]
                raise RuntimeError(
                    "Cloudflare Quick Tunnel did not provide a URL"
                    + (f": {detail}" if detail else "")
                )
            try:
                line = await asyncio.wait_for(
                    process.stdout.readline(), timeout=remaining
                )
            except asyncio.TimeoutError as error:
                raise RuntimeError(
                    "Cloudflare Quick Tunnel did not start within 45 seconds"
                ) from error
            if not line:
                returncode = await process.wait()
                detail = " | ".join(recent_lines[-4:])[-700:]
                raise RuntimeError(
                    f"cloudflared exited with code {returncode}"
                    + (f": {detail}" if detail else "")
                )
            decoded = line.decode("utf-8", "replace").strip()
            if decoded:
                recent_lines.append(decoded)
            match = QUICK_TUNNEL_URL.search(decoded)
            if match:
                return match.group(0).rstrip("/")

    @staticmethod
    async def _drain_process_output(stream):
        while await stream.readline():
            pass

    async def _validate_public_video(self, video_url, process):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 45
        last_problem = "not reachable"
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=10.0),
            follow_redirects=True,
            trust_env=False,
        ) as client:
            while loop.time() < deadline:
                if process.returncode is not None:
                    raise RuntimeError(
                        f"cloudflared exited with code {process.returncode}"
                    )
                try:
                    async with client.stream(
                        "GET",
                        video_url,
                        headers={"Range": "bytes=0-0"},
                    ) as response:
                        content_type = response.headers.get(
                            "content-type", ""
                        ).split(";", 1)[0].strip().lower()
                        if (
                            response.status_code in {200, 206}
                            and content_type == "video/mp4"
                        ):
                            async for chunk in response.aiter_bytes():
                                if chunk:
                                    return
                            last_problem = "empty response body"
                        else:
                            last_problem = (
                                f"HTTP {response.status_code} {content_type}"
                            )
                except httpx.HTTPError as error:
                    last_problem = error.__class__.__name__
                await asyncio.sleep(2)
        raise RuntimeError(
            "Cloudflare video URL is not publicly readable: " + last_problem
        )

    async def _wait_until_ready(self, client, container_id):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 300
        last_status = "unknown"
        while True:
            payload = await self._request_json(
                client,
                "GET",
                f"{self.api_base}/{container_id}",
                params={"fields": "id,status,error_message"},
            )
            status = str(payload.get("status") or "unknown")
            last_status = status
            if status == "FINISHED":
                return
            if status in {"ERROR", "EXPIRED"}:
                detail = payload.get("error_message") or "no error detail"
                raise RuntimeError(
                    f"Threads container {container_id}: {status}: {detail}"
                )
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise RuntimeError(
                    f"Threads container {container_id} stayed {last_status} "
                    "for 5 minutes"
                )
            await asyncio.sleep(min(5, remaining))

    async def _request_json(self, client, method, url, **kwargs):
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self.access_token}"
        try:
            response = await client.request(
                method, url, headers=headers, **kwargs
            )
        except httpx.HTTPError as error:
            raise RuntimeError(
                f"Threads request failed: {error.__class__.__name__}"
            ) from error
        try:
            payload = response.json()
        except ValueError as error:
            message = response.text.strip() or (
                f"Threads HTTP {response.status_code}"
            )
            raise RuntimeError(message[-700:]) from error
        if response.is_error or payload.get("error"):
            raise RuntimeError(self._error_message(payload))
        return payload

    @staticmethod
    def _error_message(payload):
        error = payload.get("error", payload)
        if isinstance(error, dict):
            return str(
                error.get("error_user_msg")
                or error.get("message")
                or error
            )[-700:]
        return str(error)[-700:]
