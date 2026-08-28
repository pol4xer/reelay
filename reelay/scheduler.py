import asyncio
import re
import shutil
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo


_PUBLISH_LOCK = asyncio.Lock()


def register_schedule(application):
    settings = application.bot_data["settings"]
    timezone = ZoneInfo(settings.timezone)
    post_times = current_schedule(application)

    for post_time in post_times:
        hour, minute = (int(part) for part in post_time.split(":"))
        application.job_queue.run_daily(
            publish_next,
            time=time(hour=hour, minute=minute, tzinfo=timezone),
            name=f"publish-{post_time}",
        )
    return post_times


def reschedule_posts(application, count):
    count = int(count)
    if not 1 <= count <= 12:
        raise ValueError("posts_per_day must be between 1 and 12")

    settings = application.bot_data["settings"]
    build_post_times(
        count,
        settings.post_window_start,
        settings.post_window_end,
    )

    db = application.bot_data["db"]
    db.set_setting("posts_per_day", count)

    for job in application.job_queue.jobs():
        if job.name and job.name.startswith("publish-"):
            job.schedule_removal()

    return register_schedule(application)


def current_schedule(application):
    settings = application.bot_data["settings"]
    db = application.bot_data["db"]
    count = int(
        db.get_setting("posts_per_day", settings.posts_per_day)
    )
    if not 1 <= count <= 12:
        raise ValueError("posts_per_day must be between 1 and 12")
    return build_post_times(
        count,
        settings.post_window_start,
        settings.post_window_end,
    )


def build_post_times(count, window_start, window_end):
    count = int(count)
    if not 1 <= count <= 12:
        raise ValueError("posts_per_day must be between 1 and 12")

    start = _clock_minutes(window_start)
    end = _clock_minutes(window_end)
    if count == 1:
        return [_format_minutes(start)]

    span = (end - start) % (24 * 60)
    if span == 0:
        raise ValueError("post window must contain more than one minute")

    intervals = count - 1
    return [
        _format_minutes(
            start + (span * index + intervals // 2) // intervals
        )
        for index in range(count)
    ]


def _clock_minutes(value):
    parts = str(value).split(":")
    if len(parts) != 2 or any(not part.isdigit() for part in parts):
        raise ValueError("post window time must use HH:MM")
    hour, minute = (int(part) for part in parts)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("post window time must use HH:MM")
    return hour * 60 + minute


def _format_minutes(value):
    value %= 24 * 60
    hour, minute = divmod(value, 60)
    return f"{hour:02d}:{minute:02d}"


async def publish_next(context):
    if _PUBLISH_LOCK.locked():
        return

    async with _PUBLISH_LOCK:
        settings = context.application.bot_data["settings"]
        db = context.application.bot_data["db"]
        publisher = context.application.bot_data["publisher"]

        if db.is_paused():
            return

        now = datetime.now(ZoneInfo(settings.timezone))
        if not settings.post_on_weekends and now.weekday() >= 5:
            return

        job = db.next_queued()
        if not job or not db.mark_publishing(job["id"]):
            return

        try:
            media_id = await publisher.publish(
                job["video_path"], _publish_caption(job)
            )
        except Exception as exc:
            db.set_failed(job["id"], str(exc))
            await _notify_owner(
                context,
                settings,
                db,
                f"Ошибка публикации #{job['id']}: {exc}",
            )
            return

        db.mark_published(job["id"], media_id)

        if settings.delete_after_publish:
            await asyncio.to_thread(
                _delete_job_video_directory,
                settings.video_dir,
                job["video_path"],
            )

        await _notify_owner(
            context,
            settings,
            db,
            f"Опубликовано #{job['id']} в Instagram. Media ID: {media_id}",
        )


async def _notify_owner(context, settings, db, message):
    owner_id = db.get_setting(
        "telegram_owner_id", settings.telegram_owner_id
    )
    if owner_id:
        await context.bot.send_message(chat_id=int(owner_id), text=message)


def _publish_caption(job):
    caption = (job.get("caption") or "").strip()
    tags = (job.get("tags") or "").split()
    existing = {
        hashtag.casefold()
        for hashtag in re.findall(r"(?<!\w)#[\w]+", caption, re.UNICODE)
    }
    tags = [tag for tag in tags if tag.casefold() not in existing]
    tag_line = " ".join(tags)
    return "\n\n".join(value for value in (caption, tag_line) if value)


def _delete_job_video_directory(video_root, video_path):
    video_root = Path(video_root).resolve()
    job_directory = Path(video_path).resolve().parent

    if job_directory.parent == video_root and job_directory.exists():
        shutil.rmtree(job_directory)
