import asyncio
import hmac
import re

import httpx

from ..db import PENDING_TIKTOK_PREFIX
from .contract import Platform, PublishRequest, PublishResult

API_BASE = "https://open.tiktokapis.com"
TOKEN_URL = f"{API_BASE}/v2/oauth/token/"
INBOX_INIT_URL = f"{API_BASE}/v2/post/publish/inbox/video/init/"
STATUS_URL = f"{API_BASE}/v2/post/publish/status/fetch/"

MAX_VIDEO_BYTES = 4_000_000_000
MAX_CHUNK_BYTES = 64_000_000
STATUS_POLL_SECONDS = 5
STATUS_TIMEOUT_SECONDS = 300
UPLOAD_ATTEMPTS = 3

_READY_STATUSES = {"SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"}
_PROCESSING_STATUSES = {"PROCESSING_UPLOAD", "PROCESSING_DOWNLOAD"}
_SUPPORTED_MIME_TYPES = {
    ".mov": "video/quicktime",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}
_SENSITIVE_PARAMETER = re.compile(
    r"(?i)(access_token|refresh_token|client_secret|upload_token|code)=([^&\s]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)bearer\s+[^\s,;]+")


class TikTokAPIError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = str(code)


class TikTokPublisher:
    """Upload a local video to the creator's TikTok inbox as an editable draft."""

    platform = Platform.TIKTOK

    def __init__(self, settings, token_store=None):
        self.client_key = settings.tiktok_client_key
        self.client_secret = settings.tiktok_client_secret
        self.open_id = settings.tiktok_open_id
        self.token_store = token_store
        refresh_token = settings.tiktok_refresh_token
        if token_store is not None:
            try:
                refresh_token = token_store.get_setting(
                    "tiktok_refresh_token",
                    refresh_token,
                )
            except Exception as error:
                raise RuntimeError(
                    "Reelay could not load the saved TikTok refresh token"
                ) from error
        self.refresh_token = str(refresh_token or "").strip()

    async def publish(self, request: PublishRequest) -> PublishResult:
        video_path = request.video_path
        video_size = video_path.stat().st_size
        mime_type = self._mime_type(video_path)
        chunk_size, total_chunk_count = self._chunk_plan(video_size)
        timeout = httpx.Timeout(300.0, connect=30.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            access_token = await self._access_token(client)
            pending = await self._pending_upload_state(request.job_id)
            if pending:
                publish_id, upload_url, upload_offset = pending
                await self._resume_pending(
                    client,
                    access_token,
                    request,
                    publish_id,
                    upload_url,
                    upload_offset,
                    video_size,
                    mime_type,
                    chunk_size,
                    total_chunk_count,
                )
                return PublishResult(
                    platform=self.platform,
                    media_id=publish_id,
                )

            initialized = await self._request_json(
                client,
                "POST",
                INBOX_INIT_URL,
                operation="inbox upload initialization",
                access_token=access_token,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
                json={
                    "source_info": {
                        "source": "FILE_UPLOAD",
                        "video_size": video_size,
                        "chunk_size": chunk_size,
                        "total_chunk_count": total_chunk_count,
                    }
                },
            )
            publish_id = str(initialized.get("publish_id") or "").strip()
            upload_url = str(initialized.get("upload_url") or "").strip()
            if not publish_id or not upload_url:
                raise RuntimeError("TikTok did not return a publish id and upload URL")

            await self._start_pending(request.job_id, publish_id, upload_url)
            await self._upload(
                client,
                upload_url,
                video_path,
                video_size,
                mime_type,
                chunk_size,
                total_chunk_count,
                access_token,
                publish_id,
                job_id=request.job_id,
            )
            await self._wait_until_inbox(
                client,
                access_token,
                publish_id,
                job_id=request.job_id,
            )

        return PublishResult(
            platform=self.platform,
            media_id=publish_id,
        )

    async def _resume_pending(
        self,
        client,
        access_token,
        request,
        publish_id,
        upload_url,
        stored_offset,
        video_size,
        mime_type,
        chunk_size,
        total_chunk_count,
    ):
        status_payload = await self._status_or_reset_invalid(
            client,
            access_token,
            publish_id,
            request.job_id,
        )
        status = str(status_payload.get("status") or "").strip().upper()
        if status in _READY_STATUSES:
            await self._clear_transport(request.job_id, publish_id)
            return
        if status == "FAILED":
            reason = self._sanitize(status_payload.get("fail_reason") or "unknown reason")
            await self._reset_pending(request.job_id, publish_id)
            raise RuntimeError(f"TikTok rejected the uploaded video: {reason}")
        if status not in _PROCESSING_STATUSES:
            raise RuntimeError(
                f"TikTok returned an unknown upload status: {self._sanitize(status or 'empty')}"
            )

        try:
            remote_offset = int(status_payload.get("uploaded_bytes") or 0)
        except (TypeError, ValueError) as error:
            raise RuntimeError("TikTok returned invalid upload progress") from error
        offset = max(int(stored_offset or 0), remote_offset)
        if offset < video_size:
            if not upload_url:
                raise RuntimeError("TikTok pending upload has no resumable upload URL")
            await self._upload(
                client,
                upload_url,
                request.video_path,
                video_size,
                mime_type,
                chunk_size,
                total_chunk_count,
                access_token,
                publish_id,
                job_id=request.job_id,
                initial_offset=offset,
            )
        await self._wait_until_inbox(
            client,
            access_token,
            publish_id,
            job_id=request.job_id,
        )

    async def _access_token(self, client):
        payload = await self._request_oauth_json(
            client,
            data={
                "client_key": self.client_key,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            },
        )
        access_token = str(payload.get("access_token") or "").strip()
        refreshed_token = str(payload.get("refresh_token") or "").strip()
        refreshed_open_id = str(payload.get("open_id") or "").strip()
        granted_scopes = self._scopes(payload.get("scope"))
        if not access_token:
            raise RuntimeError("TikTok OAuth did not return an access token")
        if not refreshed_token:
            raise RuntimeError("TikTok OAuth did not return a refresh token")
        if not refreshed_open_id:
            raise RuntimeError("TikTok OAuth did not return an open_id")
        if not hmac.compare_digest(refreshed_open_id, self.open_id):
            raise RuntimeError("TikTok OAuth returned a token for a different account")
        if "video.upload" not in granted_scopes:
            raise RuntimeError("TikTok OAuth token is missing the video.upload scope")

        if self.token_store is not None:
            try:
                await asyncio.to_thread(
                    self.token_store.set_setting,
                    "tiktok_refresh_token",
                    refreshed_token,
                )
            except Exception as error:
                raise RuntimeError(
                    "TikTok OAuth refreshed the token, but Reelay could not save it"
                ) from error
        self.refresh_token = refreshed_token
        return access_token

    async def _pending_upload_state(self, job_id):
        if self.token_store is None or job_id is None:
            return None
        try:
            job = await asyncio.to_thread(self.token_store.get_job, job_id)
        except Exception as error:
            raise RuntimeError("Reelay could not load the TikTok checkpoint") from error
        checkpoint = str((job or {}).get("tiktok_publish_id") or "")
        if checkpoint.startswith(PENDING_TIKTOK_PREFIX):
            return (
                checkpoint.removeprefix(PENDING_TIKTOK_PREFIX),
                str((job or {}).get("tiktok_upload_url") or ""),
                int((job or {}).get("tiktok_upload_offset") or 0),
            )
        return None

    async def _start_pending(self, job_id, publish_id, upload_url):
        if self.token_store is None or job_id is None:
            return
        try:
            saved = await asyncio.to_thread(
                self.token_store.start_tiktok_upload,
                job_id,
                publish_id,
                upload_url,
            )
        except Exception as error:
            raise RuntimeError("Reelay could not save the pending TikTok upload") from error
        if not saved:
            raise RuntimeError("Reelay could not save the pending TikTok upload")

    async def _save_offset(self, job_id, publish_id, offset):
        if self.token_store is None or job_id is None:
            return
        try:
            saved = await asyncio.to_thread(
                self.token_store.set_tiktok_upload_offset,
                job_id,
                publish_id,
                offset,
            )
        except Exception as error:
            raise RuntimeError("Reelay could not save TikTok upload progress") from error
        if not saved:
            raise RuntimeError("Reelay could not save TikTok upload progress")

    async def _clear_transport(self, job_id, publish_id):
        if self.token_store is None or job_id is None:
            return
        try:
            await asyncio.to_thread(
                self.token_store.clear_tiktok_transport,
                job_id,
                publish_id,
            )
        except Exception as error:
            raise RuntimeError("Reelay could not clear TikTok transport state") from error

    async def _reset_pending(self, job_id, publish_id):
        if self.token_store is None or job_id is None:
            return
        try:
            await asyncio.to_thread(
                self.token_store.reset_tiktok_upload,
                job_id,
                publish_id,
            )
        except Exception as error:
            raise RuntimeError("Reelay could not reset the rejected TikTok upload") from error

    async def _upload(
        self,
        client,
        upload_url,
        video_path,
        video_size,
        mime_type,
        chunk_size,
        total_chunk_count,
        access_token,
        publish_id,
        *,
        job_id=None,
        initial_offset=0,
    ):
        offset = int(initial_offset or 0)
        if not 0 <= offset <= video_size:
            raise RuntimeError("TikTok returned an invalid resumable upload offset")
        for chunk_index in range(total_chunk_count):
            is_final = chunk_index == total_chunk_count - 1
            chunk_start = chunk_index * chunk_size
            chunk_end = video_size if is_final else chunk_start + chunk_size
            if chunk_end <= offset:
                continue
            if chunk_start != offset:
                raise RuntimeError("TikTok returned a partial chunk upload offset")
            length = chunk_end - chunk_start
            expected_status = 201 if is_final else 206
            response = None
            accepted_out_of_band = False

            for attempt in range(UPLOAD_ATTEMPTS):
                try:
                    response = await client.put(
                        upload_url,
                        headers={
                            "Content-Type": mime_type,
                            "Content-Length": str(length),
                            "Content-Range": (f"bytes {offset}-{offset + length - 1}/{video_size}"),
                        },
                        content=self._read_range(video_path, offset, length),
                    )
                except httpx.HTTPError as error:
                    if attempt + 1 == UPLOAD_ATTEMPTS:
                        if (
                            await self._uploaded_bytes(
                                client,
                                access_token,
                                publish_id,
                                job_id,
                            )
                            >= chunk_end
                        ):
                            accepted_out_of_band = True
                            break
                        raise RuntimeError("TikTok network error while uploading video") from error
                    await asyncio.sleep(2**attempt)
                    continue

                if response.status_code < 500 or attempt + 1 == UPLOAD_ATTEMPTS:
                    break
                await asyncio.sleep(2**attempt)

            if response is None:
                if not accepted_out_of_band:
                    raise RuntimeError("TikTok video upload did not return a response")
            elif response.status_code == 416:
                uploaded_bytes = await self._uploaded_bytes(
                    client,
                    access_token,
                    publish_id,
                    job_id,
                )
                if uploaded_bytes >= chunk_end:
                    accepted_out_of_band = True
            if response is not None and response.status_code == 404:
                uploaded_bytes = await self._uploaded_bytes(
                    client,
                    access_token,
                    publish_id,
                    job_id,
                )
                if uploaded_bytes >= chunk_end:
                    accepted_out_of_band = True
                else:
                    await self._reset_pending(job_id, publish_id)
                    raise RuntimeError("TikTok upload task no longer exists; run /retry again")
            if response is not None and response.status_code == 403:
                await self._reset_pending(job_id, publish_id)
                raise RuntimeError("TikTok upload URL expired; run /retry again")
            if (
                response is not None
                and not accepted_out_of_band
                and response.status_code != expected_status
            ):
                raise RuntimeError(f"TikTok video upload failed (HTTP {response.status_code})")
            offset = chunk_end
            await self._save_offset(job_id, publish_id, offset)

        if offset != video_size:
            raise RuntimeError("TikTok video upload did not consume the complete file")

    async def _uploaded_bytes(self, client, access_token, publish_id, job_id=None):
        payload = await self._status_or_reset_invalid(
            client,
            access_token,
            publish_id,
            job_id,
        )
        status = str(payload.get("status") or "").strip().upper()
        if status in _READY_STATUSES:
            return MAX_VIDEO_BYTES
        if status == "FAILED":
            reason = self._sanitize(payload.get("fail_reason") or "unknown reason")
            raise RuntimeError(f"TikTok rejected the uploaded video: {reason}")
        try:
            uploaded_bytes = int(payload.get("uploaded_bytes") or 0)
        except (TypeError, ValueError) as error:
            raise RuntimeError("TikTok returned invalid upload progress") from error
        if uploaded_bytes < 0:
            raise RuntimeError("TikTok returned invalid upload progress")
        return uploaded_bytes

    async def _status(self, client, access_token, publish_id):
        return await self._request_json(
            client,
            "POST",
            STATUS_URL,
            operation="upload status check",
            access_token=access_token,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={"publish_id": publish_id},
        )

    async def _status_or_reset_invalid(self, client, access_token, publish_id, job_id):
        try:
            return await self._status(client, access_token, publish_id)
        except TikTokAPIError as error:
            if error.code != "invalid_publish_id":
                raise
            await self._reset_pending(job_id, publish_id)
            raise RuntimeError(
                "TikTok no longer recognizes the pending upload; "
                "its checkpoint was cleared, run /retry again"
            ) from error

    async def _wait_until_inbox(self, client, access_token, publish_id, job_id=None):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + STATUS_TIMEOUT_SECONDS

        while True:
            status_payload = await self._status_or_reset_invalid(
                client,
                access_token,
                publish_id,
                job_id,
            )
            status = str(status_payload.get("status") or "").strip().upper()
            if status in _READY_STATUSES:
                await self._clear_transport(job_id, publish_id)
                return
            if status == "FAILED":
                reason = self._sanitize(status_payload.get("fail_reason") or "unknown reason")
                await self._reset_pending(job_id, publish_id)
                raise RuntimeError(f"TikTok rejected the uploaded video: {reason}")
            if status not in _PROCESSING_STATUSES:
                raise RuntimeError(
                    f"TikTok returned an unknown upload status: {self._sanitize(status or 'empty')}"
                )

            remaining = deadline - loop.time()
            if remaining <= 0:
                raise RuntimeError("TikTok did not deliver the video to the inbox within 5 minutes")
            await asyncio.sleep(min(STATUS_POLL_SECONDS, remaining))

    async def _request_oauth_json(self, client, **kwargs):
        try:
            response = await client.post(TOKEN_URL, **kwargs)
        except httpx.HTTPError as error:
            raise RuntimeError("TikTok OAuth network error") from error

        try:
            payload = response.json()
        except ValueError as error:
            raise RuntimeError(
                f"TikTok OAuth returned an invalid response (HTTP {response.status_code})"
            ) from error
        if not isinstance(payload, dict):
            raise RuntimeError("TikTok OAuth returned an invalid response")
        if response.is_error or payload.get("error"):
            code = self._sanitize(payload.get("error") or f"HTTP {response.status_code}")
            message = self._sanitize(payload.get("error_description") or "")
            detail = f": {message}" if message else ""
            raise RuntimeError(f"TikTok OAuth failed ({code}){detail}")
        return payload

    async def _request_json(
        self,
        client,
        method,
        url,
        *,
        operation,
        access_token,
        **kwargs,
    ):
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.HTTPError as error:
            raise RuntimeError(f"TikTok network error during {operation}") from error

        try:
            payload = response.json()
        except ValueError as error:
            raise RuntimeError(
                f"TikTok {operation} returned an invalid response (HTTP {response.status_code})"
            ) from error
        if not isinstance(payload, dict):
            raise RuntimeError(f"TikTok {operation} returned an invalid response")

        api_error = payload.get("error") or {}
        error_code = str(api_error.get("code") or "") if isinstance(api_error, dict) else ""
        if response.is_error or error_code != "ok":
            code = self._sanitize(error_code or f"HTTP {response.status_code}", access_token)
            raw_message = api_error.get("message") if isinstance(api_error, dict) else api_error
            message = self._sanitize(raw_message or "", access_token)
            detail = f": {message}" if message else ""
            raise TikTokAPIError(
                error_code or f"HTTP {response.status_code}",
                f"TikTok {operation} failed ({code}){detail}",
            )

        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError(f"TikTok {operation} returned no data")
        return data

    def _sanitize(self, value, *additional_secrets):
        message = str(value).replace("\r", " ").replace("\n", " ").strip()
        message = _SENSITIVE_PARAMETER.sub(r"\1=<redacted>", message)
        message = _BEARER_TOKEN.sub("Bearer <redacted>", message)
        for secret in (
            self.client_key,
            self.client_secret,
            self.refresh_token,
            *additional_secrets,
        ):
            if secret:
                message = message.replace(str(secret), "<redacted>")
        return message[:500] or "unknown error"

    @staticmethod
    def _scopes(value):
        return {scope for scope in re.split(r"[,\s]+", str(value or "")) if scope}

    @staticmethod
    def _mime_type(video_path):
        mime_type = _SUPPORTED_MIME_TYPES.get(video_path.suffix.casefold())
        if not mime_type:
            raise RuntimeError("TikTok upload supports only MP4, MOV, and WebM video files")
        return mime_type

    @staticmethod
    def _chunk_plan(video_size):
        if video_size <= 0:
            raise RuntimeError("TikTok cannot upload an empty video file")
        if video_size > MAX_VIDEO_BYTES:
            raise RuntimeError("TikTok cannot upload a video larger than 4 GB")
        if video_size <= MAX_CHUNK_BYTES:
            return video_size, 1

        chunk_size = MAX_CHUNK_BYTES
        if video_size < 2 * MAX_CHUNK_BYTES:
            chunk_size = video_size // 2
        total_chunk_count = video_size // chunk_size
        if not 1 <= total_chunk_count <= 1000:
            raise RuntimeError("TikTok upload requires between 1 and 1000 chunks")
        return chunk_size, total_chunk_count

    @staticmethod
    async def _read_range(video_path, offset, length):
        remaining = length
        with video_path.open("rb") as video:
            video.seek(offset)
            while remaining:
                chunk = await asyncio.to_thread(video.read, min(1024 * 1024, remaining))
                if not chunk:
                    raise RuntimeError("Video file changed while TikTok upload was in progress")
                remaining -= len(chunk)
                yield chunk
