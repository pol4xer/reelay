import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

LOGGER = logging.getLogger(__name__)


def register_schedule(application, *, startup=False):
    settings = application.bot_data["settings"]
    timezone = ZoneInfo(settings.timezone)
    post_times = current_schedule(application)

    for post_time in post_times:
        hour, minute = (int(part) for part in post_time.split(":"))
        application.job_queue.run_daily(
            publish_next,
            time=time(hour=hour, minute=minute, tzinfo=timezone),
            name=f"publish-{post_time}",
            job_kwargs={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": settings.schedule_grace_minutes * 60,
            },
        )
    if startup:
        _register_startup_catchup(application)
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
    count = int(db.get_setting("posts_per_day", settings.posts_per_day))
    if not 1 <= count <= 12:
        raise ValueError("posts_per_day must be between 1 and 12")
    return build_post_times(
        count,
        settings.post_window_start,
        settings.post_window_end,
    )


def next_scheduled_at(application, now=None):
    settings = application.bot_data["settings"]
    timezone = ZoneInfo(settings.timezone)
    now = now.astimezone(timezone) if now else datetime.now(timezone)
    schedule = current_schedule(application)

    for day_offset in (0, 1):
        target_date = (now + timedelta(days=day_offset)).date()
        for post_time in schedule:
            hour, minute = (int(part) for part in post_time.split(":"))
            candidate = datetime.combine(
                target_date,
                time(hour=hour, minute=minute),
                tzinfo=timezone,
            )
            if candidate > now:
                return candidate
    raise RuntimeError("Could not calculate the next publishing slot")


def most_recent_scheduled_at(application, now=None):
    settings = application.bot_data["settings"]
    timezone = ZoneInfo(settings.timezone)
    now = now.astimezone(timezone) if now else datetime.now(timezone)
    schedule = current_schedule(application)

    candidates = []
    for day_offset in (-1, 0):
        target_date = (now + timedelta(days=day_offset)).date()
        for post_time in schedule:
            hour, minute = (int(part) for part in post_time.split(":"))
            candidate = datetime.combine(
                target_date,
                time(hour=hour, minute=minute),
                tzinfo=timezone,
            )
            if candidate <= now:
                candidates.append(candidate)
    if not candidates:
        raise RuntimeError("Could not calculate the previous publishing slot")
    return max(candidates)


def _register_startup_catchup(application):
    settings = application.bot_data["settings"]
    db = application.bot_data["db"]
    if db.is_paused() or not db.next_queued():
        return

    timezone = ZoneInfo(settings.timezone)
    now = datetime.now(timezone)
    previous = most_recent_scheduled_at(application, now)
    grace = timedelta(minutes=settings.schedule_grace_minutes)
    if now - previous > grace:
        return

    previous_utc = previous.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%d %H:%M:%S")
    if db.has_publish_attempt_since(previous_utc):
        return

    application.job_queue.run_once(
        publish_next,
        when=1,
        name=f"publish-catchup-{previous:%Y%m%d-%H%M}",
        job_kwargs={"max_instances": 1},
    )
    LOGGER.info("Scheduled startup catch-up for missed slot %s", previous)


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
        _format_minutes(start + (span * index + intervals // 2) // intervals)
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
    service = context.application.bot_data["publishing_service"]
    report = await service.publish_next()
    if report.notify_owner:
        settings = context.application.bot_data["settings"]
        db = context.application.bot_data["db"]
        await _notify_owner(context, settings, db, report.message)
    return report


async def _notify_owner(context, settings, db, message):
    owner_id = db.get_setting("telegram_owner_id", settings.telegram_owner_id)
    if owner_id:
        await context.bot.send_message(chat_id=int(owner_id), text=message)
