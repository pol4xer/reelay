import asyncio
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..db import PLATFORM_MEDIA_COLUMNS
from ..publishers import Platform, PublisherRegistry, PublishRequest

LOGGER = logging.getLogger(__name__)
PLATFORM_LABELS = {
    Platform.INSTAGRAM: "Instagram",
    Platform.FACEBOOK: "Facebook",
    Platform.THREADS: "Threads",
    Platform.YOUTUBE: "YouTube",
}


@dataclass(frozen=True, slots=True)
class PublicationReport:
    outcome: str
    message: str
    job_id: int | None = None
    platform: Platform | None = None
    notify_owner: bool = False


class PublishingService:
    """Owns the queue-to-platform workflow; publishers only upload media."""

    def __init__(self, settings, db, publishers: PublisherRegistry, tagger):
        self.settings = settings
        self.db = db
        self.publishers = publishers
        self.tagger = tagger
        self._lock = asyncio.Lock()

    async def publish_next(self) -> PublicationReport:
        if self._lock.locked():
            return self._skip("locked", "Публикация уже выполняется.")

        async with self._lock:
            if self.db.is_paused():
                return self._skip("paused", "Очередь стоит на паузе.")

            now = datetime.now(ZoneInfo(self.settings.timezone))
            if not self.settings.post_on_weekends and now.weekday() >= 5:
                return self._skip("weekend", "Публикации по выходным отключены.")

            job = self.db.next_queued()
            if not job:
                return self._skip("empty_queue", "Очередь пуста.")
            if not self.db.mark_publishing(job["id"]):
                return self._skip(
                    "claim_failed",
                    f"Не удалось захватить задание #{job['id']}.",
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
                f"Локальный MP4 не найден: {video_path}",
            )

        caption = compose_caption(job)
        for platform, publisher in self.publishers.items():
            column = PLATFORM_MEDIA_COLUMNS[platform.value]
            if job.get(column):
                self.db.record_publish_attempt(
                    job_id,
                    "checkpoint_skipped",
                    platform=platform.value,
                    detail=str(job[column]),
                )
                continue

            try:
                title = ""
                if platform is Platform.YOUTUBE:
                    title = await self.tagger.generate_title(
                        video_path,
                        job.get("caption") or "",
                    )
                result = await publisher.publish(
                    PublishRequest(
                        video_path=video_path,
                        caption=caption,
                        title=title,
                    )
                )
                if result.platform is not platform:
                    raise RuntimeError(
                        f"Publisher contract mismatch: expected {platform.value}, "
                        f"got {result.platform.value}"
                    )
                if not self.db.set_platform_media_id(
                    job_id,
                    platform.value,
                    result.media_id,
                ):
                    raise RuntimeError("Platform checkpoint was not saved")
                job[column] = result.media_id
                self.db.record_publish_attempt(
                    job_id,
                    "published",
                    platform=platform.value,
                    detail=result.media_id,
                )
            except Exception as error:
                return self._fail(job, platform, error)

        missing = self.missing_platforms(job)
        if missing:
            return self._fail(
                job,
                missing[0],
                "Не сохранены ID платформ: " + ", ".join(platform.value for platform in missing),
            )

        if not self.db.mark_completed(job_id):
            return self._fail(job, None, "Не удалось завершить задание в SQLite")

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
        message = success_message(completed, self.publishers)
        self.db.record_publish_attempt(job_id, "completed")
        LOGGER.info("Publication completed for job #%s", job_id)
        return PublicationReport(
            outcome="published",
            job_id=job_id,
            message=message,
            notify_owner=True,
        )

    def missing_platforms(self, job) -> list[Platform]:
        return [
            platform
            for platform in self.publishers
            if not job.get(PLATFORM_MEDIA_COLUMNS[platform.value])
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
        label = PLATFORM_LABELS.get(platform, "Reelay")
        reason = str(error).strip() or error.__class__.__name__
        stored_error = f"{label}: {reason}"
        self.db.set_failed(job["id"], stored_error)
        self.db.record_publish_attempt(
            job["id"],
            "failed",
            platform=platform.value if platform else None,
            detail=reason,
        )
        LOGGER.error("Publication failed for job #%s: %s", job["id"], stored_error)
        return PublicationReport(
            outcome="failed",
            job_id=job["id"],
            platform=platform,
            message=(
                f"Ошибка публикации #{job['id']} · {label}: {reason}\nИсточник: {job['source_url']}"
            ),
            notify_owner=True,
        )


def compose_caption(job):
    caption = (job.get("caption") or "").strip()
    tags = (job.get("tags") or "").split()
    existing = {hashtag.casefold() for hashtag in re.findall(r"(?<!\w)#[\w]+", caption, re.UNICODE)}
    tags = [tag for tag in tags if tag.casefold() not in existing]
    tag_line = " ".join(tags)
    return "\n\n".join(value for value in (caption, tag_line) if value)


def success_message(job, publishers):
    lines = [f"Опубликовано #{job['id']}:"]
    for platform in publishers:
        media_id = job.get(PLATFORM_MEDIA_COLUMNS[platform.value])
        if media_id:
            lines.append(f"{PLATFORM_LABELS[platform]}: {media_id}")
    return "\n".join(lines)


def _delete_job_video_directory(video_root, video_path):
    video_root = Path(video_root).resolve()
    job_directory = Path(video_path).resolve().parent
    if job_directory.parent == video_root and job_directory.exists():
        shutil.rmtree(job_directory)
