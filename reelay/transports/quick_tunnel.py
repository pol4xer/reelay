import asyncio
import os
import re
import shutil
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


class QuickTunnelVideoTransport:
    """Exposes exactly one local MP4 through a disposable Quick Tunnel."""

    def __init__(self, cloudflared_path):
        self.cloudflared_path = Path(cloudflared_path)

    @asynccontextmanager
    async def public_url(self, video_path):
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
            drain_task = asyncio.create_task(self._drain_output(process.stdout))
            public_video_url = f"{public_base}/video.mp4"
            await self._validate(public_video_url, process)
            yield public_video_url
        finally:
            await self._stop_process(process)
            if drain_task is not None:
                drain_task.cancel()
                await asyncio.gather(drain_task, return_exceptions=True)
            server.shutdown()
            server.server_close()
            await asyncio.to_thread(server_thread.join, 5)

    async def _stop_process(self, process):
        if process is None or process.returncode is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                return
            await process.wait()

    def _resolve_cloudflared(self):
        candidates = []
        from_path = shutil.which("cloudflared")
        if from_path:
            candidates.append(Path(from_path))
        candidates.append(self.cloudflared_path)
        for candidate in candidates:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
        raise RuntimeError(
            f"cloudflared is missing; expected executable at {self.cloudflared_path}"
        )

    @staticmethod
    async def _read_tunnel_url(process):
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
                    process.stdout.readline(),
                    timeout=remaining,
                )
            except TimeoutError as error:
                raise RuntimeError(
                    "Cloudflare Quick Tunnel did not start within 45 seconds"
                ) from error
            if not line:
                returncode = await process.wait()
                detail = " | ".join(recent_lines[-4:])[-700:]
                raise RuntimeError(
                    f"cloudflared exited with code {returncode}" + (f": {detail}" if detail else "")
                )
            decoded = line.decode("utf-8", "replace").strip()
            if decoded:
                recent_lines.append(decoded)
            match = QUICK_TUNNEL_URL.search(decoded)
            if match:
                return match.group(0).rstrip("/")

    @staticmethod
    async def _drain_output(stream):
        while await stream.readline():
            pass

    @staticmethod
    async def _validate(video_url, process):
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
                    raise RuntimeError(f"cloudflared exited with code {process.returncode}")
                try:
                    async with client.stream(
                        "GET",
                        video_url,
                        headers={"Range": "bytes=0-0"},
                    ) as response:
                        content_type = (
                            response.headers.get("content-type", "")
                            .split(";", 1)[0]
                            .strip()
                            .lower()
                        )
                        if response.status_code in {200, 206} and content_type == "video/mp4":
                            async for chunk in response.aiter_bytes():
                                if chunk:
                                    return
                            last_problem = "empty response body"
                        else:
                            last_problem = f"HTTP {response.status_code} {content_type}"
                except httpx.HTTPError as error:
                    last_problem = error.__class__.__name__
                await asyncio.sleep(2)
        raise RuntimeError("Cloudflare video URL is not publicly readable: " + last_problem)
