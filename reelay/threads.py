import asyncio

import httpx


class ThreadsPublisher:
    def __init__(self, settings):
        self.meta_base = (
            f"https://graph.facebook.com/{settings.meta_api_version}"
        )
        self.meta_access_token = settings.meta_page_access_token
        self.api_base = (
            f"https://graph.threads.net/{settings.threads_api_version}"
        )
        self.user_id = settings.threads_user_id
        self.access_token = settings.threads_access_token

    async def publish(self, instagram_media_id, caption=""):
        timeout = httpx.Timeout(300.0, connect=30.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            media = await self._request_json(
                client,
                "GET",
                f"{self.meta_base}/{instagram_media_id}",
                params={
                    "access_token": self.meta_access_token,
                    "fields": "media_url,media_type",
                },
            )
            video_url = media.get("media_url")
            if not video_url:
                raise RuntimeError("Instagram did not return a public media_url")

            container = await self._request_json(
                client,
                "POST",
                f"{self.api_base}/{self.user_id}/threads",
                data={
                    "access_token": self.access_token,
                    "media_type": "VIDEO",
                    "video_url": video_url,
                    "text": (caption or "")[:500],
                },
            )
            container_id = container.get("id")
            if not container_id:
                raise RuntimeError("Threads did not return a container id")

            await self._wait_until_ready(client, container_id)
            published = await self._request_json(
                client,
                "POST",
                f"{self.api_base}/{self.user_id}/threads_publish",
                data={
                    "access_token": self.access_token,
                    "creation_id": container_id,
                },
            )
            thread_id = published.get("id")
            if not thread_id:
                raise RuntimeError("Threads did not return a published post id")
            return str(thread_id)

    async def _wait_until_ready(self, client, container_id):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 300
        while True:
            payload = await self._request_json(
                client,
                "GET",
                f"{self.api_base}/{container_id}",
                params={
                    "access_token": self.access_token,
                    "fields": "id,status,error_message",
                },
            )
            status = payload.get("status")
            if status == "FINISHED":
                return
            if status in {"ERROR", "EXPIRED"}:
                raise RuntimeError(
                    payload.get("error_message")
                    or f"Threads container status is {status}"
                )
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise RuntimeError(
                    "Threads did not process the video within 5 minutes"
                )
            await asyncio.sleep(min(5, remaining))

    async def _request_json(self, client, method, url, **kwargs):
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.HTTPError as error:
            raise RuntimeError(str(error)) from error
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
            return str(
                error.get("error_user_msg")
                or error.get("message")
                or error
            )[-700:]
        return str(error)[-700:]
