import re
import shutil
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from telegram import BotCommand, MenuButtonCommands
from telegram.ext import CommandHandler, MessageHandler, filters

from .scheduler import current_schedule, publish_next, reschedule_posts


SHORTCODE = re.compile(r"^[A-Za-z0-9_-]+$")
PATH_KINDS = {"p", "reel", "reels", "tv"}
MAX_TELEGRAM_FILE_SIZE = 50 * 1024 * 1024
BOT_COMMANDS = [
    BotCommand("start", "Подключить или проверить бота"),
    BotCommand("help", "Показать инструкцию и команды"),
    BotCommand("queue", "Показать очередь"),
    BotCommand("posts", "Показать или изменить постов в день"),
    BotCommand("now", "Опубликовать следующее видео сейчас"),
    BotCommand("file", "Скачать MP4: /file ID"),
    BotCommand("drop", "Удалить задание: /drop ID"),
    BotCommand("retry", "Повторить failed: /retry ID"),
    BotCommand("pause", "Приостановить публикации"),
    BotCommand("resume", "Возобновить публикации"),
]


async def post_init(application):
    await application.bot.set_my_commands(BOT_COMMANDS)
    await application.bot.set_chat_menu_button(
        menu_button=MenuButtonCommands()
    )


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


def _job_id(context):
    if len(context.args) != 1 or not context.args[0].isdigit():
        return None
    value = int(context.args[0])
    return value if value > 0 else None


def _instagram_url(line):
    if line != line.strip() or any(character.isspace() for character in line):
        return None

    try:
        parsed = urlsplit(line)
    except ValueError:
        return None

    if (
        parsed.scheme not in {"http", "https"}
        or parsed.netloc.lower() not in {"instagram.com", "www.instagram.com"}
    ):
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if (
        len(parts) != 2
        or parts[0] not in PATH_KINDS
        or not SHORTCODE.fullmatch(parts[1])
    ):
        return None

    kind, shortcode = parts
    return f"https://www.instagram.com/{kind}/{shortcode}/", shortcode


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
        f"Подключено. Telegram ID: {user.id}\n"
        "Откройте Menu или отправьте /help."
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
    first_line, separator, caption = text.partition("\n")
    parsed = _instagram_url(first_line)
    if not parsed:
        attached = await _attach_pending_caption(context, text)
        if attached:
            job_id, tags = attached
            await update.effective_message.reply_text(
                _caption_updated_message(job_id, tags)
            )
            return
        await update.effective_message.reply_text(
            "Первая строка должна быть одной ссылкой Instagram."
        )
        return

    source_url, shortcode = parsed
    if not separator:
        caption = ""
    db = context.application.bot_data["db"]

    existing = db.get_job_by_shortcode(shortcode)
    if existing:
        if caption and db.set_caption(existing["id"], caption):
            db.delete_setting("pending_caption_job_id")
            tags = ""
            video_path = (
                Path(existing["video_path"])
                if existing.get("video_path")
                else None
            )
            if video_path and video_path.is_file():
                try:
                    tagger = context.application.bot_data["tagger"]
                    tags = await tagger.generate(video_path, caption)
                    db.set_tags(existing["id"], tags)
                except Exception:
                    tags = ""
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
    downloader = context.application.bot_data["downloader"]
    tagger = context.application.bot_data["tagger"]
    try:
        path = await downloader.download(job_id, source_url)
        tags = await tagger.generate(path, caption)
        db.set_downloaded(job_id, path, tags)
        await update.effective_message.reply_text(
            _queued_message(job_id, tags)
        )
    except Exception:
        db.delete_job(job_id)
        settings = context.application.bot_data["settings"]
        shutil.rmtree(settings.video_dir / str(job_id), ignore_errors=True)
        await update.effective_message.reply_text(
            _skipped_message(job_id, source_url)
        )


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

    job_id = _job_id(context)
    if job_id is None:
        await update.effective_message.reply_text("Использование: /file ID")
        return

    job = context.application.bot_data["db"].get_job(job_id)
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

    job_id = _job_id(context)
    if job_id is None:
        await update.effective_message.reply_text("Использование: /drop ID")
        return

    db = context.application.bot_data["db"]
    job = db.get_job(job_id)
    if not job:
        await update.effective_message.reply_text("Задание не найдено.")
        return
    if job["status"] == "publishing":
        await update.effective_message.reply_text("Сейчас публикуется.")
        return

    db.delete_job(job_id)
    settings = context.application.bot_data["settings"]
    shutil.rmtree(settings.video_dir / str(job_id), ignore_errors=True)
    await update.effective_message.reply_text(f"Удалено: #{job_id}")


async def retry(update, context):
    if not _authorized(update, context):
        return

    job_id = _job_id(context)
    if job_id is None:
        await update.effective_message.reply_text("Использование: /retry ID")
        return

    db = context.application.bot_data["db"]
    job = db.get_job(job_id)
    if not job:
        await update.effective_message.reply_text("Задание не найдено.")
        return
    if job["status"] != "failed":
        await update.effective_message.reply_text("Повтор доступен только для failed.")
        return

    if db.retry(job_id):
        await update.effective_message.reply_text(f"Снова в очереди: #{job_id}")
        return

    db.mark_downloading(job_id)
    downloader = context.application.bot_data["downloader"]
    tagger = context.application.bot_data["tagger"]
    await update.effective_message.reply_text(f"Скачиваю #{job_id} заново…")
    try:
        path = await downloader.download(job_id, job["source_url"])
        tags = await tagger.generate(path, job.get("caption") or "")
        db.set_downloaded(job_id, path, tags)
        await update.effective_message.reply_text(
            _queued_message(job_id, tags)
        )
    except Exception:
        db.delete_job(job_id)
        settings = context.application.bot_data["settings"]
        shutil.rmtree(settings.video_dir / str(job_id), ignore_errors=True)
        await update.effective_message.reply_text(
            _skipped_message(job_id, job["source_url"])
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
    await update.effective_message.reply_text("Публикация возобновлена.")


async def publish_now(update, context):
    if not _authorized(update, context):
        return
    if not context.application.bot_data["db"].next_queued():
        await update.effective_message.reply_text("Очередь пуста.")
        return
    await update.effective_message.reply_text("Публикую следующее видео…")
    await publish_next(context)


async def posts(update, context):
    if not _authorized(update, context):
        return

    application = context.application
    if not context.args:
        times = current_schedule(application)
        await update.effective_message.reply_text(
            _posts_message(len(times), times)
        )
        return

    if (
        len(context.args) != 1
        or not context.args[0].isdigit()
        or not 1 <= int(context.args[0]) <= 12
    ):
        await update.effective_message.reply_text(
            "Использование: /posts N, где N от 1 до 12."
        )
        return

    count = int(context.args[0])
    times = reschedule_posts(application, count)
    await update.effective_message.reply_text(_posts_message(count, times))


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
        try:
            tagger = context.application.bot_data["tagger"]
            tags = await tagger.generate(video_path, caption)
            db.set_tags(job["id"], tags)
        except Exception:
            tags = ""
    return job["id"], tags


def _short_error(error, limit=700):
    text = str(error).strip() or error.__class__.__name__
    return text[-limit:]


def _short_tags(tags, limit=180):
    tags = str(tags).strip()
    if len(tags) <= limit:
        return tags
    return tags[: limit - 1].rstrip() + "…"


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
        "/posts [N] — показать расписание или задать 1–12 постов в день\n"
        "/now — опубликовать следующее видео сейчас\n"
        "/file ID — отправить MP4 в Telegram\n"
        "/drop ID — удалить задание и локальный файл\n"
        "/retry ID — повторить задание со статусом failed\n"
        "/pause — приостановить публикации\n"
        "/resume — возобновить публикации\n\n"
        f"Текущее расписание: {len(times)} в день — {', '.join(times)}"
    )


def register_handlers(application):
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("queue", queue))
    application.add_handler(CommandHandler("file", send_file))
    application.add_handler(CommandHandler("drop", drop))
    application.add_handler(CommandHandler("retry", retry))
    application.add_handler(CommandHandler("pause", pause))
    application.add_handler(CommandHandler("resume", resume))
    application.add_handler(CommandHandler("now", publish_now))
    application.add_handler(CommandHandler("posts", posts))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, add_link)
    )
