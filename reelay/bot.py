import asyncio
import logging
import re
import shutil
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from telegram import BotCommand, MenuButtonCommands
from telegram.error import TelegramError
from telegram.ext import CommandHandler, MessageHandler, filters

from .publishers import PublishRequest, YouTubePublisher
from .scheduler import (
    current_schedule,
    exact_schedule,
    next_scheduled_at,
    publish_next,
    reschedule_posts,
    reschedule_times,
)
from .services import compose_caption

SHORTCODE = re.compile(r"^[A-Za-z0-9_-]+$")
LOGGER = logging.getLogger(__name__)
PATH_KINDS = {"p", "reel", "reels", "tv"}
MAX_TELEGRAM_FILE_SIZE = 50 * 1024 * 1024
DOWNLOAD_TASKS_KEY = "download_tasks"
PUBLISH_CHECKPOINT_COLUMNS = (
    "instagram_media_id",
    "facebook_media_id",
    "threads_media_id",
    "youtube_video_id",
    "tiktok_publish_id",
)
BOT_COMMANDS = [
    BotCommand("start", "Подключить или проверить бота"),
    BotCommand("help", "Показать инструкцию и команды"),
    BotCommand("queue", "Показать очередь"),
    BotCommand("status", "Показать состояние сервиса"),
    BotCommand("posts", "Показать или изменить постов в день"),
    BotCommand("times", "Показать или задать точные времена"),
    BotCommand("now", "Опубликовать следующее видео сейчас"),
    BotCommand("youtube", "Загрузить один приватный YouTube Short"),
    BotCommand("file", "Скачать MP4 по ID или shortcode"),
    BotCommand("drop", "Удалить по ID или shortcode"),
    BotCommand("retry", "Повторить failed по ID или shortcode"),
    BotCommand("pause", "Приостановить публикации"),
    BotCommand("resume", "Возобновить публикации"),
]


async def post_init(application):
    try:
        await application.bot.set_my_commands(BOT_COMMANDS)
        await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except TelegramError as error:
        LOGGER.warning("Telegram menu setup failed: %s", error)


async def post_stop(application):
    tasks = application.bot_data.get(DOWNLOAD_TASKS_KEY, set())
    if not tasks:
        return

    pending = tuple(tasks)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    tasks.clear()


def _schedule_download(application, coroutine, name):
    tasks = application.bot_data.setdefault(DOWNLOAD_TASKS_KEY, set())
    task = asyncio.create_task(coroutine, name=name)
    tasks.add(task)
    task.add_done_callback(lambda completed: _download_finished(tasks, completed))


def _download_finished(tasks, task):
    tasks.discard(task)
    if task.cancelled():
        return
    try:
        task.result()
    except Exception:
        LOGGER.exception("Background download task failed")


def _owner_id(context):
    db = context.application.bot_data["db"]
    stored = db.get_setting("telegram_owner_id")
    if stored:
        return int(stored)

    configured = context.application.bot_data["settings"].telegram_owner_id
    if configured is not None:
        db.set_setting("telegram_owner_id", configured)
        return configured
    return None


def _authorized(update, context):
    owner_id = _owner_id(context)
    return (
        owner_id is not None
        and update.effective_user is not None
        and update.effective_user.id == owner_id
    )


def _job_from_args(context):
    if len(context.args) != 1:
        return None

    selector = context.args[0]
    numeric = selector[1:] if selector.startswith("#") else selector
    db = context.application.bot_data["db"]
    if numeric.isdigit() and int(numeric) > 0:
        job = db.get_job(int(numeric))
        if job:
            return job
    if SHORTCODE.fullmatch(selector):
        return db.get_job_by_shortcode(selector)
    return None


def _instagram_url(line):
    if line != line.strip() or any(character.isspace() for character in line):
        return None

    try:
        parsed = urlsplit(line)
    except ValueError:
        return None

    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {
        "instagram.com",
        "www.instagram.com",
    }:
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2 or parts[0] not in PATH_KINDS or not SHORTCODE.fullmatch(parts[1]):
        return None

    kind, shortcode = parts
    return f"https://www.instagram.com/{kind}/{shortcode}/", shortcode


def _instagram_blocks(text):
    lines = [line for line in text.splitlines() if line.strip()]
    parsed_lines = [_instagram_url(line) for line in lines]
    if not any(parsed_lines):
        return []
    if not parsed_lines[0]:
        return None

    blocks = []
    source_url = shortcode = None
    caption_lines = []
    for line, parsed in zip(lines, parsed_lines, strict=True):
        if parsed:
            if source_url is not None:
                blocks.append((source_url, shortcode, "\n".join(caption_lines)))
            source_url, shortcode = parsed
            caption_lines = []
            continue
        caption_lines.append(line)

    blocks.append((source_url, shortcode, "\n".join(caption_lines)))
    return blocks


async def start(update, context):
    if update.effective_chat.type != "private":
        return

    db = context.application.bot_data["db"]
    owner_id = _owner_id(context)
    user = update.effective_user

    if owner_id is not None:
        if user.id == owner_id:
            await update.effective_message.reply_text(
                "Reelay готов. Откройте Menu или отправьте /help."
            )
        return

    settings = context.application.bot_data["settings"]
    username = (user.username or "").lower()
    if username != settings.telegram_owner_username:
        return

    db.set_setting("telegram_owner_id", user.id)
    await update.effective_message.reply_text(
        f"Подключено. Telegram ID: {user.id}\nОткройте Menu или отправьте /help."
    )


async def help_command(update, context):
    if not _authorized(update, context):
        return
    times = current_schedule(context.application)
    await update.effective_message.reply_text(_help_message(times))


async def add_link(update, context):
    if not _authorized(update, context):
        return

    text = update.effective_message.text
    blocks = _instagram_blocks(text)
    if blocks is None:
        await update.effective_message.reply_text(
            "Первая непустая строка должна быть одной ссылкой Instagram."
        )
        return
    if not blocks:
        attached = await _attach_pending_caption(context, text)
        if attached:
            job_id, tags = attached
            await update.effective_message.reply_text(_caption_updated_message(job_id, tags))
            return
        await update.effective_message.reply_text(
            "Первая строка должна быть одной ссылкой Instagram."
        )
        return

    for source_url, shortcode, caption in blocks:
        await _add_instagram_job(update, context, source_url, shortcode, caption)


async def _add_instagram_job(update, context, source_url, shortcode, caption):
    db = context.application.bot_data["db"]

    existing = db.get_job_by_shortcode(shortcode)
    if existing:
        if caption and db.set_caption(existing["id"], caption):
            db.delete_setting("pending_caption_job_id")
            tags = ""
            video_path = Path(existing["video_path"]) if existing.get("video_path") else None
            if video_path and video_path.is_file():
                tags = await _regenerate_tags(context, existing["id"], video_path, caption)
            await update.effective_message.reply_text(
                _caption_updated_message(existing["id"], tags)
            )
            return
        if not caption and existing["status"] in {
            "downloading",
            "queued",
            "failed",
        }:
            db.set_setting("pending_caption_job_id", existing["id"])
        await update.effective_message.reply_text(
            f"Уже есть: #{existing['id']} · {existing['status']}"
        )
        return

    try:
        job_id = db.create_job(source_url, shortcode, caption)
    except sqlite3.IntegrityError:
        existing = db.get_job_by_shortcode(shortcode)
        await update.effective_message.reply_text(
            f"Уже есть: #{existing['id']} · {existing['status']}"
        )
        return

    if caption:
        db.delete_setting("pending_caption_job_id")
    else:
        db.set_setting("pending_caption_job_id", job_id)

    await update.effective_message.reply_text(f"Скачиваю #{job_id}…")
    _schedule_download(
        context.application,
        _download_and_tag(update, context, job_id, source_url),
        f"download-instagram-{job_id}",
    )


async def _download_and_tag(
    update,
    context,
    job_id,
    source_url,
    *,
    preserve_checkpoints=False,
):
    db = context.application.bot_data["db"]
    downloader = context.application.bot_data["downloader"]
    tagger = context.application.bot_data["tagger"]
    try:
        path = await downloader.download(job_id, source_url)
        job = db.get_job(job_id)
        if not job:
            return
        caption = job.get("caption") or ""
        tags = await tagger.generate(path, caption)
        latest = db.get_job(job_id)
        if not latest:
            return
        latest_caption = latest.get("caption") or ""
        if latest_caption != caption:
            tags = await tagger.generate(path, latest_caption)
        if not db.set_downloaded(job_id, path, tags):
            return
    except Exception:
        settings = context.application.bot_data["settings"]
        shutil.rmtree(settings.video_dir / str(job_id), ignore_errors=True)
        if preserve_checkpoints:
            db.set_failed(job_id, "Source download failed; checkpoints preserved")
        else:
            db.delete_job(job_id)
        try:
            await update.effective_message.reply_text(_skipped_message(job_id, source_url))
        except TelegramError as error:
            LOGGER.warning("Could not notify about skipped job #%s: %s", job_id, error)
        return

    try:
        await update.effective_message.reply_text(_queued_message(job_id, tags))
    except TelegramError as error:
        LOGGER.warning("Could not notify about queued job #%s: %s", job_id, error)


async def _regenerate_tags(context, job_id, video_path, caption):
    try:
        tagger = context.application.bot_data["tagger"]
        tags = await tagger.generate(video_path, caption)
        db = context.application.bot_data["db"]
        job = db.get_job(job_id)
        if job and (job.get("caption") or "") == caption:
            db.set_tags(job_id, tags)
            return tags
    except Exception as error:
        LOGGER.warning("Could not regenerate tags for job #%s: %s", job_id, error)
    return ""


async def queue(update, context):
    if not _authorized(update, context):
        return

    db = context.application.bot_data["db"]
    jobs = db.list_jobs()
    if not jobs:
        await update.effective_message.reply_text("Очередь пуста.")
        return

    paused = " · пауза" if db.is_paused() else ""
    rows = [f"Последние задания{paused}:"]
    for job in jobs:
        row = f"#{job['id']} · {job['status']} · {job['shortcode']}"
        destination_ids = _destination_ids(job)
        if destination_ids:
            row += f"\n{destination_ids}"
        if job.get("tags"):
            row += f"\n{_short_tags(job['tags'])}"
        if job["status"] == "failed":
            row += f"\nИсточник: {job['source_url']}"
            if job["error"]:
                row += f"\n{_short_error(job['error'], 160)}"
        rows.append(row)
    await update.effective_message.reply_text("\n".join(rows))


async def send_file(update, context):
    if not _authorized(update, context):
        return

    job = _job_from_args(context)
    if not job:
        await update.effective_message.reply_text("Использование: /file 33 или /file SHORTCODE")
        return

    path = Path(job["video_path"]) if job and job["video_path"] else None
    if not path or not path.is_file():
        await update.effective_message.reply_text("Файл не найден.")
        return

    if path.stat().st_size > MAX_TELEGRAM_FILE_SIZE:
        await update.effective_message.reply_text(f"Больше 50 МБ: {path}")
        return

    with path.open("rb") as document:
        await update.effective_message.reply_document(
            document=document,
            filename=path.name,
        )


async def drop(update, context):
    if not _authorized(update, context):
        return

    job = _job_from_args(context)
    if not job:
        await update.effective_message.reply_text("Использование: /drop 33 или /drop SHORTCODE")
        return

    db = context.application.bot_data["db"]
    job_id = job["id"]
    if job["status"] in {"downloading", "publishing"}:
        action = "скачивается" if job["status"] == "downloading" else "публикуется"
        await update.effective_message.reply_text(f"Сейчас {action}.")
        return

    db.delete_job(job_id)
    settings = context.application.bot_data["settings"]
    shutil.rmtree(settings.video_dir / str(job_id), ignore_errors=True)
    await update.effective_message.reply_text(f"Удалено: #{job_id}")


async def retry(update, context):
    if not _authorized(update, context):
        return

    job = _job_from_args(context)
    if not job:
        await update.effective_message.reply_text("Использование: /retry 33 или /retry SHORTCODE")
        return

    db = context.application.bot_data["db"]
    job_id = job["id"]
    if job["status"] != "failed":
        await update.effective_message.reply_text("Повтор доступен только для failed.")
        return

    if db.retry(job_id):
        await update.effective_message.reply_text(f"Снова в очереди: #{job_id}")
        return

    db.mark_downloading(job_id)
    await update.effective_message.reply_text(f"Скачиваю #{job_id} заново…")
    _schedule_download(
        context.application,
        _download_and_tag(
            update,
            context,
            job_id,
            job["source_url"],
            preserve_checkpoints=any(job.get(column) for column in PUBLISH_CHECKPOINT_COLUMNS),
        ),
        f"retry-instagram-{job_id}",
    )


async def pause(update, context):
    if not _authorized(update, context):
        return
    context.application.bot_data["db"].set_paused(True)
    await update.effective_message.reply_text("Публикация приостановлена.")


async def resume(update, context):
    if not _authorized(update, context):
        return
    context.application.bot_data["db"].set_paused(False)
    next_run = next_scheduled_at(context.application)
    await update.effective_message.reply_text(
        f"Публикация возобновлена.\nСледующий слот: {next_run:%d.%m %H:%M}."
    )


async def publish_now(update, context):
    if not _authorized(update, context):
        return
    if not context.application.bot_data["db"].next_queued():
        await update.effective_message.reply_text("Очередь пуста.")
        return
    await update.effective_message.reply_text("Публикую следующее видео…")
    context.application.create_task(
        _publish_now(update, context),
        update=update,
        name="publish-now",
    )


async def _publish_now(update, context):
    report = await publish_next(context)
    if report.outcome == "skipped":
        await update.effective_message.reply_text(report.message)


async def status_command(update, context):
    if not _authorized(update, context):
        return

    db = context.application.bot_data["db"]
    publishers = context.application.bot_data["publishers"]
    next_run = next_scheduled_at(context.application)
    state = "пауза" if db.is_paused() else "активен"
    platforms = ", ".join(platform.value for platform in publishers)
    last_attempt = db.latest_publish_attempt()
    last_line = "нет запусков"
    if last_attempt:
        last_line = f"{last_attempt['created_at']} · {last_attempt['outcome']}"
        if last_attempt.get("job_id"):
            last_line += f" · #{last_attempt['job_id']}"

    await update.effective_message.reply_text(
        f"Reelay: {state}\n"
        f"В очереди: {db.count_jobs('queued')}\n"
        f"Платформы: {platforms}\n"
        f"Следующий слот: {next_run:%d.%m %H:%M}\n"
        f"Последний запуск: {last_line}"
    )


async def youtube_test(update, context):
    if not _authorized(update, context):
        return

    job = _job_from_args(context)
    if not job:
        await update.effective_message.reply_text(
            "Использование: /youtube 33 или /youtube SHORTCODE"
        )
        return

    if job.get("youtube_video_id") or job.get("youtube_test_video_id"):
        video_id = job.get("youtube_video_id") or job["youtube_test_video_id"]
        await update.effective_message.reply_text(
            f"Уже загружено в YouTube: {video_id}\nhttps://youtu.be/{video_id}"
        )
        return

    video_path = Path(job["video_path"]) if job.get("video_path") else None
    if not video_path or not video_path.is_file():
        await update.effective_message.reply_text("Локальный MP4 не найден.")
        return

    settings = context.application.bot_data["settings"]
    missing = [
        name
        for name, value in (
            ("YOUTUBE_CLIENT_ID", settings.youtube_client_id),
            ("YOUTUBE_CLIENT_SECRET", settings.youtube_client_secret),
            ("YOUTUBE_REFRESH_TOKEN", settings.youtube_refresh_token),
        )
        if not value
    ]
    if missing:
        await update.effective_message.reply_text("YouTube OAuth не готов: " + ", ".join(missing))
        return

    await update.effective_message.reply_text(f"Загружаю #{job['id']} в YouTube как private…")
    publisher = YouTubePublisher(settings)
    publisher.privacy_status = "private"
    try:
        tagger = context.application.bot_data["tagger"]
        title = await tagger.generate_title(video_path, job.get("caption") or "")
        result = await publisher.publish(
            PublishRequest(
                video_path=video_path,
                caption=compose_caption(job),
                title=title,
            )
        )
        video_id = result.media_id
        db = context.application.bot_data["db"]
        if not db.set_youtube_test_video_id(job["id"], video_id):
            raise RuntimeError("YouTube test ID не сохранился в очереди")
    except Exception as error:
        await update.effective_message.reply_text(
            f"YouTube не загрузил #{job['id']}: {_short_error(error)}\n"
            f"Источник: {job['source_url']}"
        )
        return

    await update.effective_message.reply_text(
        f"YouTube private готов: #{job['id']} · {video_id}\n"
        f"Title: {title}\n"
        f"https://youtu.be/{video_id}"
    )


async def posts(update, context):
    if not _authorized(update, context):
        return

    application = context.application
    if not context.args:
        times = current_schedule(application)
        await update.effective_message.reply_text(_posts_message(len(times), times))
        return

    if (
        len(context.args) != 1
        or not context.args[0].isdigit()
        or not 1 <= int(context.args[0]) <= 12
    ):
        await update.effective_message.reply_text("Использование: /posts N, где N от 1 до 12.")
        return

    count = int(context.args[0])
    times = reschedule_posts(application, count)
    await update.effective_message.reply_text(
        _posts_message(count, times) + "\nРежим: равномерно в окне публикации."
    )


async def times(update, context):
    if not _authorized(update, context):
        return

    application = context.application
    if not context.args:
        configured = exact_schedule(application)
        if configured:
            await update.effective_message.reply_text(_times_message(configured))
            return
        generated = current_schedule(application)
        await update.effective_message.reply_text(
            "Точные времена не заданы.\n"
            f"Равномерное расписание: {', '.join(generated)}\n"
            "Чтобы задать точные слоты: /times 13:00 18:30 21:30"
        )
        return

    raw_times = " ".join(context.args)
    requested = [part for part in re.split(r"[\s,]+", raw_times.strip()) if part]
    try:
        configured = reschedule_times(application, requested)
    except ValueError:
        await update.effective_message.reply_text(
            "Использование: /times HH:MM HH:MM ...\n"
            "От 1 до 12 уникальных времён строго по возрастанию."
        )
        return
    await update.effective_message.reply_text(_times_message(configured))


async def _attach_pending_caption(context, caption):
    db = context.application.bot_data["db"]
    pending = db.get_setting("pending_caption_job_id")
    if not pending or not str(pending).isdigit():
        return False

    job = db.get_job(int(pending))
    if not job or job["status"] not in {"downloading", "queued", "failed"}:
        db.delete_setting("pending_caption_job_id")
        return False

    if not db.set_caption(job["id"], caption):
        return False
    db.delete_setting("pending_caption_job_id")

    tags = ""
    video_path = Path(job["video_path"]) if job.get("video_path") else None
    if video_path and video_path.is_file():
        tags = await _regenerate_tags(context, job["id"], video_path, caption)
    return job["id"], tags


def _short_error(error, limit=700):
    text = str(error).strip() or error.__class__.__name__
    return text[-limit:]


def _short_tags(tags, limit=180):
    tags = str(tags).strip()
    if len(tags) <= limit:
        return tags
    return tags[: limit - 1].rstrip() + "…"


def _destination_ids(job):
    fields = (
        ("IG", "instagram_media_id"),
        ("FB", "facebook_media_id"),
        ("TH", "threads_media_id"),
        ("YT", "youtube_video_id"),
        ("TT-Inbox", "tiktok_publish_id"),
        ("YT-test", "youtube_test_video_id"),
    )
    return " · ".join(
        f"{label}:{job[column]}"
        for label, column in fields
        if job.get(column) and not str(job[column]).startswith("not-required-")
    )


def _queued_message(job_id, tags):
    message = f"В очереди: #{job_id}"
    if tags:
        message += f"\n{tags}"
    return message


def _skipped_message(job_id, source_url):
    return f"Пропущено: #{job_id}\nИсточник: {source_url}"


def _caption_updated_message(job_id, tags):
    message = f"Caption обновлён: #{job_id}"
    if tags:
        message += f"\n{tags}"
    return message


def _posts_message(count, times):
    return f"Постов в день: {count}\nВремена: {', '.join(times)}"


def _times_message(times):
    return f"Точное расписание: {len(times)} в день\nВремена: {', '.join(times)}"


def _help_message(times):
    return (
        "Reelay — очередь для Instagram Reels.\n\n"
        "Как добавить видео:\n"
        "Отправьте ссылку Instagram первой строкой. Caption можно "
        "добавить со второй строки или следующим сообщением.\n\n"
        "Команды:\n"
        "/start — подключить или проверить бота\n"
        "/help — показать эту инструкцию\n"
        "/queue — показать последние задания\n"
        "/status — состояние процесса, очереди и следующий слот\n"
        "/times [HH:MM ...] — показать или задать точные времена\n"
        "/posts [N] — показать расписание или вернуться к 1–12 равномерным слотам\n"
        "/now — опубликовать следующее видео сейчас\n"
        "/youtube ID|SHORTCODE — приватно протестировать YouTube Short\n"
        "/file ID|SHORTCODE — отправить MP4 в Telegram\n"
        "/drop ID|SHORTCODE — удалить задание и локальный файл\n"
        "/retry ID|SHORTCODE — повторить failed\n"
        "/pause — приостановить публикации\n"
        "/resume — возобновить публикации\n\n"
        "TikTok Inbox требует открыть уведомление, добавить caption и вручную нажать Publish.\n\n"
        f"Текущее расписание: {len(times)} в день — {', '.join(times)}"
    )


def register_handlers(application):
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("queue", queue))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("file", send_file))
    application.add_handler(CommandHandler("drop", drop))
    application.add_handler(CommandHandler("retry", retry))
    application.add_handler(CommandHandler("pause", pause))
    application.add_handler(CommandHandler("resume", resume))
    application.add_handler(CommandHandler("now", publish_now))
    application.add_handler(CommandHandler("youtube", youtube_test))
    application.add_handler(CommandHandler("times", times))
    application.add_handler(CommandHandler("posts", posts))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, add_link))
