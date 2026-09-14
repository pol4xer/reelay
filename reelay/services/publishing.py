import asyncio
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..db import PENDING_TIKTOK_PREFIX, PLATFORM_MEDIA_COLUMNS
from ..publishers import Platform, PublisherRegistry, PublishRequest

LOGGER = logging.getLogger(__name__)
PLATFORM_LABELS = {
    Platform.INSTAGRAM: "Instagram",
    Platform.FACEBOOK: "Facebook",
    Platform.THREADS: "Threads",
    Platform.YOUTUBE: "YouTube",
    Platform.TIKTOK: "TikTok Inbox",
}
BRAND_HASHTAG = "#Reelay"
TIKTOK_HASHTAG_LIMIT = 5
REPORT_ERROR_LIMIT = 400
REPORT_MEDIA_ID_LIMIT = 160
REPORT_SOURCE_LIMIT = 512
HASHTAG_PATTERN = re.compile(r"(?<!\w)#[\w]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class PublicationReport:
    outcome: str
    message: str
    job_id: int | None = None
    platform: Platform | None = None
    notify_owner: bool = False
    followup_messages: tuple[str, ...] = ()


class PublishingService:
    """Owns the queue-to-platform workflow; publishers only upload media."""

    def __init__(self, settings, db, publishers: PublisherRegistry, tagger, watermarker):
        self.settings = settings
        self.db = db
        self.publishers = publishers
        self.tagger = tagger
        self.watermarker = watermarker
        self._lock = asyncio.Lock()

    async def publish_next(self) -> PublicationReport:
        if self._lock.locked():
            return self._skip("locked", "Publishing is already in progress.")

        async with self._lock:
            if self.db.is_paused():
                return self._skip("paused", "The queue is paused.")

            now = datetime.now(ZoneInfo(self.settings.timezone))
            if not self.settings.post_on_weekends and now.weekday() >= 5:
                return self._skip("weekend", "Weekend publishing is disabled.")

            job = self.db.next_queued()
            if not job:
                return self._skip("empty_queue", "The queue is empty.")
            if not self.db.mark_publishing(job["id"]):
                return self._skip(
                    "claim_failed",
                    f"Could not claim job #{job['id']}.",
                    job_id=job["id"],
                )

            return await self._publish_claimed_job(job)

    async def _publish_claimed_job(self, job) -> PublicationReport:
        job_id = job["id"]
        self.db.record_publish_attempt(job_id, "started")
        video_path = Path(job.get("video_path") or "")
        if not video_path.is_file():
            return self._fail(
                job,
                None,
                f"Local MP4 not found: {video_path}",
            )

        watermarked_video_path = None
        watermark_error = None
        failures = []
        delivered = set()
        for platform, publisher in self.publishers.items():
            column = PLATFORM_MEDIA_COLUMNS[platform.value]
            if _checkpoint_complete(platform, job.get(column)):
                self.db.record_publish_attempt(
                    job_id,
                    "checkpoint_skipped",
                    platform=platform.value,
                    detail=str(job[column]),
                )
                continue

            try:
                publish_video_path = video_path
                if platform is not Platform.TIKTOK:
                    if watermark_error is not None:
                        raise watermark_error
                    if watermarked_video_path is None:
                        try:
                            watermarked_video_path = await self.watermarker.prepare(video_path)
                        except Exception as error:
                            watermark_error = error
                            raise
                    publish_video_path = watermarked_video_path
                title = ""
                if platform is Platform.YOUTUBE:
                    title = await self.tagger.generate_title(
                        video_path,
                        job.get("caption") or "",
                    )
                result = await publisher.publish(
                    PublishRequest(
                        video_path=publish_video_path,
                        caption=compose_caption(job, platform),
                        title=title,
                        job_id=job_id,
                    )
                )
                if result.platform is not platform:
                    raise RuntimeError(
                        f"Publisher contract mismatch: expected {platform.value}, "
                        f"got {result.platform.value}"
                    )
                if not _checkpoint_complete(platform, result.media_id):
                    raise RuntimeError("The platform did not confirm upload completion")
            except Exception as error:
                failures.append((platform, error))
                continue

            # An upload has succeeded; a failed checkpoint is a shared storage
            # problem with an ambiguous external side effect, not a platform failure.
            try:
                if not self.db.set_platform_media_id(
                    job_id,
                    platform.value,
                    result.media_id,
                ):
                    raise RuntimeError("Platform checkpoint was not saved")
            except Exception as error:
                failures.append(
                    (
                        platform,
                        f"Upload returned ID {result.media_id}, but saving the ID in SQLite "
                        f"failed: {error}. Further uploads have been stopped. "
                        "Before retrying, check the post on the platform and restore its ID.",
                    )
                )
                return self._failures(job, failures, delivered, retry_safe=False)
            job[column] = result.media_id
            delivered.add(platform)
            self.db.record_publish_attempt(
                job_id,
                "inbox_delivered" if platform is Platform.TIKTOK else "published",
                platform=platform.value,
                detail=result.media_id,
            )

        failed_platforms = {platform for platform, _error in failures}
        for platform in self.missing_platforms(job):
            if platform not in failed_platforms:
                failures.append((platform, "The completed upload ID was not saved"))
        if failures:
            return self._failures(job, failures, delivered)

        if not self.db.mark_completed(job_id):
            return self._failures(
                job, [(None, "Could not mark the job as completed in SQLite")], delivered
            )

        if self.settings.delete_after_publish:
            try:
                await asyncio.to_thread(
                    _delete_job_video_directory,
                    self.settings.video_dir,
                    video_path,
                )
            except OSError as error:
                LOGGER.warning(
                    "Published job #%s but could not remove local media: %s",
                    job_id,
                    error,
                )
                self.db.record_publish_attempt(
                    job_id,
                    "cleanup_failed",
                    detail=str(error),
                )

        completed = self.db.get_job(job_id) or job
        message = success_message(
            completed, self.publishers, include_tiktok_instructions=Platform.TIKTOK in delivered
        )
        followup_messages = success_followup_messages(completed, delivered)
        self.db.record_publish_attempt(job_id, "completed")
        LOGGER.info("Publication completed for job #%s", job_id)
        return PublicationReport(
            outcome="published",
            job_id=job_id,
            message=message,
            notify_owner=True,
            followup_messages=followup_messages,
        )

    def missing_platforms(self, job) -> list[Platform]:
        return [
            platform
            for platform in self.publishers
            if not _checkpoint_complete(
                platform,
                job.get(PLATFORM_MEDIA_COLUMNS[platform.value]),
            )
        ]

    def _skip(self, reason, message, job_id=None):
        self.db.record_publish_attempt(job_id, reason)
        LOGGER.info("Publication skipped: %s", reason)
        return PublicationReport(
            outcome="skipped",
            job_id=job_id,
            message=message,
        )

    def _fail(self, job, platform, error):
        return self._failures(job, [(platform, error)])

    def _failures(self, job, failures, delivered=(), *, retry_safe=True):
        reasons = [
            (platform, str(error).strip() or error.__class__.__name__)
            for platform, error in failures
        ]
        stored_error = "\n".join(
            f"{PLATFORM_LABELS.get(platform, 'Reelay')}: {reason}" for platform, reason in reasons
        )
        self.db.set_failed(job["id"], stored_error)
        for platform, reason in reasons:
            self.db.record_publish_attempt(
                job["id"],
                "failed",
                platform=platform.value if platform else None,
                detail=reason,
            )
        LOGGER.error("Publication failed for job #%s: %s", job["id"], stored_error)
        lines = [f"Publishing failed for #{job['id']}:"]
        for platform in self.publishers:
            media_id = job.get(PLATFORM_MEDIA_COLUMNS[platform.value])
            if _checkpoint_complete(platform, media_id):
                if str(media_id).startswith("not-required-"):
                    lines.append(f"{PLATFORM_LABELS[platform]}: upload not required")
                else:
                    status = "sent" if platform in delivered else "sent earlier"
                    displayed_id = _report_excerpt(media_id, REPORT_MEDIA_ID_LIMIT)
                    lines.append(f"{PLATFORM_LABELS[platform]}: {status} · {displayed_id}")
        lines.extend(
            f"{PLATFORM_LABELS.get(platform, 'Reelay')}: error · "
            f"{_report_excerpt(reason, REPORT_ERROR_LIMIT)}"
            for platform, reason in reasons
        )
        lines.append(f"Source: {_report_excerpt(job['source_url'], REPORT_SOURCE_LIMIT)}")
        if retry_safe:
            lines.append(f"Retry unfinished uploads only: /retry {job['id']}")
        followup_messages = success_followup_messages(job, delivered)
        if followup_messages:
            lines.extend(["", _tiktok_instructions()])
        return PublicationReport(
            outcome="failed",
            job_id=job["id"],
            platform=reasons[0][0],
            message="\n".join(lines),
            notify_owner=True,
            followup_messages=followup_messages,
        )


def compose_caption(job, platform=None):
    caption = (job.get("caption") or "").strip()
    hashtag_line = compose_hashtags(job, platform)

    prose = HASHTAG_PATTERN.sub("", caption)
    prose = re.sub(r"[ \t]{2,}", " ", prose)
    prose = re.sub(r"(?m)^[ \t]+|[ \t]+$", "", prose)
    prose = re.sub(r"[ \t]+([,.;:!?])", r"\1", prose)
    prose = re.sub(r"\n{3,}", "\n\n", prose).strip()
    return "\n\n".join(value for value in (prose, hashtag_line) if value)


def compose_hashtags(job, platform=None):
    hashtags = [BRAND_HASHTAG]
    seen = {BRAND_HASHTAG.casefold()}
    caption = job.get("caption") or ""
    for hashtag in HASHTAG_PATTERN.findall(caption) + HASHTAG_PATTERN.findall(
        job.get("tags") or ""
    ):
        key = hashtag.casefold()
        if key not in seen:
            seen.add(key)
            hashtags.append(hashtag)

    if platform == Platform.TIKTOK:
        hashtags = hashtags[:TIKTOK_HASHTAG_LIMIT]
    return " ".join(hashtags)


def _checkpoint_complete(platform, value):
    if not value:
        return False
    return not (platform is Platform.TIKTOK and str(value).startswith(PENDING_TIKTOK_PREFIX))


def _report_excerpt(value, limit):
    # Telegram counts UTF-16 units; supplementary characters take two units.
    # Keep full provider responses in SQLite, only shorten the user-facing report.
    text = str(value)
    encoded = text.encode("utf-16-le")
    if len(encoded) <= limit * 2:
        return text
    return encoded[: (limit - 1) * 2].decode("utf-16-le", errors="ignore") + "…"


def success_message(job, publishers, *, include_tiktok_instructions=True):
    lines = [f"Done #{job['id']}:"]
    for platform in publishers:
        media_id = job.get(PLATFORM_MEDIA_COLUMNS[platform.value])
        if media_id and not str(media_id).startswith("not-required-"):
            lines.append(
                f"{PLATFORM_LABELS[platform]}: {_report_excerpt(media_id, REPORT_MEDIA_ID_LIMIT)}"
            )
    if include_tiktok_instructions and _has_completed_tiktok_delivery(job, publishers):
        lines.extend(["", _tiktok_instructions()])
    return "\n".join(lines)


def _tiktok_instructions():
    return (
        "TikTok: open the Inbox notification, paste the hashtags "
        "from the next message and tap Publish."
    )


def success_followup_messages(job, publishers):
    if not _has_completed_tiktok_delivery(job, publishers):
        return ()
    return (compose_hashtags(job, Platform.TIKTOK),)


def _has_completed_tiktok_delivery(job, publishers):
    tiktok_id = job.get(PLATFORM_MEDIA_COLUMNS[Platform.TIKTOK.value])
    return (
        Platform.TIKTOK in publishers
        and _checkpoint_complete(Platform.TIKTOK, tiktok_id)
        and not str(tiktok_id).startswith("not-required-")
    )


def _delete_job_video_directory(video_root, video_path):
    video_root = Path(video_root).resolve()
    job_directory = Path(video_path).resolve().parent
    if job_directory.parent == video_root and job_directory.exists():
        shutil.rmtree(job_directory)
