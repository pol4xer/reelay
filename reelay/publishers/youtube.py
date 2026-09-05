import asyncio
import re
import subprocess

import httpx

from .contract import Platform, PublishRequest, PublishResult


class YouTubePublisher:
    platform = Platform.YOUTUBE

    def __init__(self, settings):
        self.client_id = settings.youtube_client_id
        self.client_secret = settings.youtube_client_secret
        self.refresh_token = settings.youtube_refresh_token
        self.privacy_status = settings.youtube_privacy_status

    async def publish(self, request: PublishRequest) -> PublishResult:
        video_path = request.video_path
        duration = await asyncio.to_thread(self._duration, video_path)
        if duration > 180.0:
            raise RuntimeError(f"YouTube Short длиннее 3 минут: {duration:.1f} сек")
        file_size = video_path.stat().st_size
        timeout = httpx.Timeout(300.0, connect=30.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            access_token = await self._access_token(client)
            metadata = {
                "snippet": {
                    "title": self._title(request.title, request.caption),
                    "description": request.caption[:5000],
                    "categoryId": "22",
                },
                "status": {
                    "privacyStatus": self.privacy_status,
                    "selfDeclaredMadeForKids": False,
                },
            }
            started = await self._request(
                client,
                "POST",
                "https://www.googleapis.com/upload/youtube/v3/videos",
                params={
                    "uploadType": "resumable",
                    "part": "snippet,status",
                },
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Length": str(file_size),
                    "X-Upload-Content-Type": "video/mp4",
                },
                json=metadata,
            )
            if started.is_error:
                raise RuntimeError(self._response_error(started))
            upload_url = started.headers.get("location")
            if not upload_url:
                raise RuntimeError("YouTube did not return a resumable upload URL")

            offset = 0
            stalled = 0
            while offset < file_size:
                uploaded = await self._request(
                    client,
                    "PUT",
                    upload_url,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "video/mp4",
                        "Content-Length": str(file_size - offset),
                        "Content-Range": (f"bytes {offset}-{file_size - 1}/{file_size}"),
                    },
                    content=self._read_file(video_path, offset),
                )
                if uploaded.status_code in {200, 201}:
                    try:
                        video_id = uploaded.json().get("id")
                    except ValueError as error:
                        raise RuntimeError("YouTube returned invalid JSON") from error
                    if not video_id:
                        raise RuntimeError("YouTube did not return a video id")
                    return PublishResult(
                        platform=self.platform,
                        media_id=video_id,
                        permalink=f"https://youtu.be/{video_id}",
                    )
                if uploaded.status_code != 308:
                    raise RuntimeError(self._response_error(uploaded))
                received = uploaded.headers.get("range", "")
                if not received:
                    uploaded = await self._request(
                        client,
                        "PUT",
                        upload_url,
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "Content-Length": "0",
                            "Content-Range": f"bytes */{file_size}",
                        },
                        content=b"",
                    )
                    if uploaded.status_code in {200, 201}:
                        try:
                            video_id = uploaded.json().get("id")
                        except ValueError as error:
                            raise RuntimeError("YouTube returned invalid JSON") from error
                        if not video_id:
                            raise RuntimeError("YouTube did not return a video id")
                        return PublishResult(
                            platform=self.platform,
                            media_id=video_id,
                            permalink=f"https://youtu.be/{video_id}",
                        )
                    if uploaded.status_code != 308:
                        raise RuntimeError(self._response_error(uploaded))
                    received = uploaded.headers.get("range", "")
                try:
                    next_offset = int(received.rsplit("-", 1)[1]) + 1 if received else 0
                except (IndexError, ValueError) as error:
                    raise RuntimeError(
                        "YouTube resumable upload returned invalid byte range"
                    ) from error
                if next_offset <= offset:
                    stalled += 1
                    if stalled > 2:
                        raise RuntimeError("YouTube resumable upload made no progress")
                else:
                    stalled = 0
                offset = next_offset

        raise RuntimeError("YouTube upload did not complete")

    async def _access_token(self, client):
        response = await self._request(
            client,
            "POST",
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if response.is_error:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if isinstance(payload, dict) and payload.get("error") == "invalid_grant":
                raise RuntimeError(
                    "invalid_grant: Google отклонил сохранённое разрешение YouTube "
                    "(срок истёк или доступ отозван). Повторно подключите YouTube через OAuth. "
                    "Если приложение в режиме Testing, сначала переведите его в In production: "
                    "https://console.cloud.google.com/auth/audience"
                )
            raise RuntimeError(self._response_error(response))
        try:
            access_token = response.json().get("access_token")
        except ValueError as error:
            raise RuntimeError("Google OAuth returned invalid JSON") from error
        if not access_token:
            raise RuntimeError("Google OAuth did not return an access token")
        return access_token

    @staticmethod
    async def _request(client, method, url, **kwargs):
        try:
            return await client.request(method, url, **kwargs)
        except httpx.HTTPError as error:
            raise RuntimeError(f"YouTube network error: {error}") from error

    @staticmethod
    def _duration(video_path):
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            duration = float(result.stdout.strip())
        except ValueError as error:
            reason = result.stderr.strip() or "ffprobe did not return duration"
            raise RuntimeError(reason[-700:]) from error
        if duration <= 0:
            raise RuntimeError("ffprobe returned invalid duration")
        return duration

    @staticmethod
    def _title(source_title, caption):
        title = str(source_title or "").strip()
        if not title:
            title = next(
                (
                    line.strip()
                    for line in str(caption or "").splitlines()
                    if line.strip() and not line.lstrip().startswith("#")
                ),
                "",
            )
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            title = "A Moment Worth Watching"
        if len(title) <= 100:
            return title
        shortened = title[:101].rsplit(" ", 1)[0].rstrip()
        return shortened or title[:100]

    @staticmethod
    async def _read_file(video_path, offset):
        with video_path.open("rb") as video:
            video.seek(offset)
            while True:
                chunk = await asyncio.to_thread(video.read, 1024 * 1024)
                if not chunk:
                    break
                yield chunk

    @staticmethod
    def _response_error(response):
        try:
            payload = response.json()
        except ValueError:
            return (response.text.strip() or f"YouTube HTTP {response.status_code}")[-700:]
        error = payload.get("error", payload)
        if isinstance(error, dict):
            details = error.get("errors") or []
            reason = details[0].get("reason") if details else ""
            message = error.get("message") or str(error)
            return f"{reason}: {message}".strip(": ")[-700:]
        return str(error)[-700:]
