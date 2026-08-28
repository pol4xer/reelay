import asyncio
from pathlib import Path

import httpx


class FacebookPublisher:
    def __init__(self, settings):
        self.api_base = (
            f"https://graph.facebook.com/{settings.meta_api_version}"
        )
        self.page_id = settings.meta_page_id
        self.access_token = settings.meta_page_access_token

    async def publish(self, video_path, caption=""):
        video_path = Path(video_path)
        file_size = video_path.stat().st_size
        timeout = httpx.Timeout(300.0, connect=30.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            started = await self._request_json(
                client,
                "POST",
                f"{self.api_base}/{self.page_id}/video_reels",
                data={
                    "access_token": self.access_token,
                    "upload_phase": "start",
                },
            )
            video_id = started.get("video_id")
            upload_url = started.get("upload_url")
            if not video_id or not upload_url:
                raise RuntimeError(
                    "Facebook did not return video_id and upload_url"
                )

            uploaded = await self._request_json(
                client,
                "POST",
                upload_url,
                headers={
                    "Authorization": f"OAuth {self.access_token}",
                    "offset": "0",
                    "file_size": str(file_size),
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(file_size),
                },
                content=self._read_file(video_path),
            )
            if uploaded.get("success") is False:
                raise RuntimeError(self._error_message(uploaded))

            finished = await self._request_json(
                client,
                "POST",
                f"{self.api_base}/{self.page_id}/video_reels",
                data={
                    "access_token": self.access_token,
                    "upload_phase": "finish",
                    "video_id": video_id,
                    "video_state": "PUBLISHED",
                    "description": caption or "",
                },
            )
            if finished.get("success") is False:
                raise RuntimeError(self._error_message(finished))
            return str(video_id)

    @staticmethod
    async def _read_file(video_path):
        with video_path.open("rb") as video:
            while True:
                chunk = await asyncio.to_thread(video.read, 1024 * 1024)
                if not chunk:
                    break
                yield chunk

    async def _request_json(self, client, method, url, **kwargs):
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.HTTPError as error:
            raise RuntimeError(str(error)) from error
        try:
            payload = response.json()
        except ValueError as error:
            message = response.text.strip() or f"Facebook HTTP {response.status_code}"
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
