import re
import shutil
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from telegram.ext import CommandHandler, MessageHandler, filters

from .scheduler import publish_next


SHORTCODE = re.compile(r"^[A-Za-z0-9_-]+$")
PATH_KINDS = {"p", "reel", "reels", "tv"}
MAX_TELEGRAM_FILE_SIZE = 50 * 1024 * 1024


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
            await update.effective_message.reply_text("Reelay готов.")
        return

    settings = context.application.bot_data["settings"]
    username = (user.username or "").lower()
    if username != settings.telegram_owner_username:
        return

    db.set_setting("telegram_owner_id", user.id)
    await update.effective_message.reply_text(
        f"Подключено. Telegram ID: {user.id}"
    )


async def add_link(update, context):
    if not _authorized(update, context):
        return

    text = update.effective_message.text
    first_line, separator, caption = text.partition("\n")
    parsed = _instagram_url(first_line)
    if not parsed:
        if _attach_pending_caption(context, text):
            await update.effective_message.reply_text(
                "Caption добавлен к последнему видео."
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
            await update.effective_message.reply_text(
                f"Caption обновлён: #{existing['id']}"
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
    try:
        path = await downloader.download(job_id, source_url)
        db.set_downloaded(job_id, path)
        await update.effective_message.reply_text(f"В очереди: #{job_id}")
    except Exception as error:
        db.set_failed(job_id, error)
        await update.effective_message.reply_text(
            f"Ошибка #{job_id}: {_short_error(error)}"
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
        if job["status"] == "failed" and job["error"]:
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
    await update.effective_message.reply_text(f"Скачиваю #{job_id} заново…")
    try:
        path = await downloader.download(job_id, job["source_url"])
        db.set_downloaded(job_id, path)
        await update.effective_message.reply_text(f"В очереди: #{job_id}")
    except Exception as error:
        db.set_failed(job_id, error)
        await update.effective_message.reply_text(
            f"Ошибка #{job_id}: {_short_error(error)}"
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


def _attach_pending_caption(context, caption):
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
    return True


def _short_error(error, limit=700):
    text = str(error).strip() or error.__class__.__name__
    return text[-limit:]


def register_handlers(application):
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("queue", queue))
    application.add_handler(CommandHandler("file", send_file))
    application.add_handler(CommandHandler("drop", drop))
    application.add_handler(CommandHandler("retry", retry))
    application.add_handler(CommandHandler("pause", pause))
    application.add_handler(CommandHandler("resume", resume))
    application.add_handler(CommandHandler("now", publish_now))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, add_link)
    )
