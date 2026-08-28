import asyncio
import tempfile
from pathlib import Path

import httpx

from ..media import ThreadsVideoPreparer
from ..transports import QuickTunnelVideoTransport
from .contract import Platform, PublishRequest, PublishResult


class ThreadsPublisher:
    platform = Platform.THREADS

    def __init__(self, settings):
        self.api_base = f"https://graph.threads.net/{settings.threads_api_version}"
        self.access_token = settings.threads_access_token
        self.video_preparer = ThreadsVideoPreparer()
        self.transport = QuickTunnelVideoTransport(Path(settings.data_dir) / "bin" / "cloudflared")

    async def publish(self, request: PublishRequest) -> PublishResult:
        with tempfile.TemporaryDirectory(prefix="reelay-threads-") as workdir:
            prepared = Path(workdir) / "video.mp4"
            await self.video_preparer.prepare(request.video_path, prepared)

            async with self.transport.public_url(prepared) as video_url:
                timeout = httpx.Timeout(300.0, connect=30.0)
                async with httpx.AsyncClient(timeout=timeout) as client:
                    container = await self._request_json(
                        client,
                        "POST",
                        f"{self.api_base}/me/threads",
                        params={
                            "media_type": "VIDEO",
                            "video_url": video_url,
                            "text": request.caption[:500],
                        },
                    )
                    container_id = container.get("id")
                    if not container_id:
                        raise RuntimeError("Threads did not return a container id")

                    await self._wait_until_ready(client, str(container_id))
                    published = await self._request_json(
                        client,
                        "POST",
                        f"{self.api_base}/me/threads_publish",
                        params={"creation_id": container_id},
                    )
                    thread_id = published.get("id")
                    if not thread_id:
                        raise RuntimeError("Threads did not return a published post id")
                    return PublishResult(
                        platform=self.platform,
                        media_id=thread_id,
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
                raise RuntimeError(f"Threads container {container_id}: {status}: {detail}")
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise RuntimeError(
                    f"Threads container {container_id} stayed {last_status} for 5 minutes"
                )
            await asyncio.sleep(min(5, remaining))

    async def _request_json(self, client, method, url, **kwargs):
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self.access_token}"
        try:
            response = await client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as error:
            raise RuntimeError(f"Threads request failed: {error.__class__.__name__}") from error
        try:
            payload = response.json()
        except ValueError as error:
            message = response.text.strip() or f"Threads HTTP {response.status_code}"
            raise RuntimeError(message[-700:]) from error
        if response.is_error or payload.get("error"):
            raise RuntimeError(self._error_message(payload))
        return payload

    @staticmethod
    def _error_message(payload):
        error = payload.get("error", payload)
        if isinstance(error, dict):
            return str(error.get("error_user_msg") or error.get("message") or error)[-700:]
        return str(error)[-700:]
