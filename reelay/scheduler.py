import asyncio
import shutil
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo


_PUBLISH_LOCK = asyncio.Lock()


def register_schedule(application):
    settings = application.bot_data["settings"]
    timezone = ZoneInfo(settings.timezone)

    for post_time in settings.post_times:
        hour, minute = (int(part) for part in post_time.split(":"))
        application.job_queue.run_daily(
            publish_next,
            time=time(hour=hour, minute=minute, tzinfo=timezone),
            name=f"publish-{post_time}",
        )


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
                job["video_path"], job.get("caption") or ""
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


def _delete_job_video_directory(video_root, video_path):
    video_root = Path(video_root).resolve()
    job_directory = Path(video_path).resolve().parent

    if job_directory.parent == video_root and job_directory.exists():
        shutil.rmtree(job_directory)
