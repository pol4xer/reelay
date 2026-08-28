import asyncio

import httpx

from .contract import Platform, PublishRequest, PublishResult


class InstagramPublisher:
    platform = Platform.INSTAGRAM

    def __init__(self, settings):
        self.api_base = f"https://graph.facebook.com/{settings.meta_api_version}"
        self.ig_user_id = settings.meta_ig_user_id
        self.access_token = settings.meta_page_access_token

    async def publish(self, request: PublishRequest) -> PublishResult:
        video_path = request.video_path
        file_size = video_path.stat().st_size

        timeout = httpx.Timeout(300.0, connect=30.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            container = await self._request_json(
                client,
                "POST",
                f"{self.api_base}/{self.ig_user_id}/media",
                data={
                    "access_token": self.access_token,
                    "media_type": "REELS",
                    "upload_type": "resumable",
                    "caption": request.caption,
                    "share_to_feed": "true",
                },
            )

            container_id = container.get("id")
            upload_uri = container.get("uri")
            if not container_id or not upload_uri:
                raise RuntimeError("Meta did not return a container id and upload URI")

            upload = await self._request_json(
                client,
                "POST",
                upload_uri,
                headers={
                    "Authorization": f"OAuth {self.access_token}",
                    "offset": "0",
                    "file_size": str(file_size),
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(file_size),
                },
                content=self._read_file(video_path),
            )
            if upload.get("success") is False:
                raise RuntimeError(self._error_message(upload))

            await self._wait_until_ready(client, container_id)

            published = await self._request_json(
                client,
                "POST",
                f"{self.api_base}/{self.ig_user_id}/media_publish",
                data={
                    "access_token": self.access_token,
                    "creation_id": container_id,
                },
            )

            media_id = published.get("id")
            if not media_id:
                raise RuntimeError("Meta did not return a published media id")
            return PublishResult(
                platform=self.platform,
                media_id=media_id,
            )

    async def _wait_until_ready(self, client, container_id):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 300

        while True:
            status = await self._request_json(
                client,
                "GET",
                f"{self.api_base}/{container_id}",
                params={
                    "access_token": self.access_token,
                    "fields": "status_code,status",
                },
            )
            status_code = status.get("status_code")
            if status_code == "FINISHED":
                return
            if status_code in {"ERROR", "EXPIRED"}:
                raise RuntimeError(
                    status.get("status") or f"Meta container status is {status_code}"
                )

            remaining = deadline - loop.time()
            if remaining <= 0:
                raise RuntimeError("Meta did not finish processing the video within 5 minutes")
            await asyncio.sleep(min(10, remaining))

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
        except httpx.HTTPError as exc:
            raise RuntimeError(str(exc)) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            message = response.text.strip() or f"Meta HTTP {response.status_code}"
            raise RuntimeError(message) from exc

        if response.is_error or payload.get("error"):
            raise RuntimeError(self._error_message(payload))
        return payload

    @staticmethod
    def _error_message(payload):
        error = payload.get("error", payload)
        if isinstance(error, dict):
            return error.get("error_user_msg") or error.get("message") or str(error)
        return str(error)
