import logging

from telegram import Update
from telegram.ext import Application

from .bot import register_handlers
from .config import Settings
from .db import QueueDB
from .downloader import InstagramDownloader
from .instagram import InstagramPublisher
from .scheduler import register_schedule
from .tagger import AutoTagger


def main():
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    settings = Settings()
    db = QueueDB(settings.db_path)
    db.init()

    application = Application.builder().token(settings.telegram_token).build()
    application.bot_data.update(
        {
            "settings": settings,
            "db": db,
            "downloader": InstagramDownloader(settings),
            "publisher": InstagramPublisher(settings),
            "tagger": AutoTagger(settings),
        }
    )

    register_handlers(application)
    register_schedule(application)

    print(
        "Reelay started: "
        + ", ".join(settings.post_times)
        + f" ({settings.timezone})"
    )
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
