import logging

from telegram import Update
from telegram.ext import Application

from .bot import post_init, register_handlers
from .config import Settings
from .db import QueueDB
from .downloader import InstagramDownloader
from .facebook import FacebookPublisher
from .instagram import InstagramPublisher
from .scheduler import register_schedule
from .tagger import AutoTagger
from .threads import ThreadsPublisher
from .youtube import YouTubePublisher


def main():
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    settings = Settings()
    db = QueueDB(settings.db_path)
    db.init()

    destinations = {}
    if settings.publish_facebook:
        destinations["facebook"] = FacebookPublisher(settings)
    if settings.publish_threads:
        destinations["threads"] = ThreadsPublisher(settings)
    if settings.publish_youtube:
        destinations["youtube"] = YouTubePublisher(settings)

    application = (
        Application.builder()
        .token(settings.telegram_token)
        .post_init(post_init)
        .build()
    )
    application.bot_data.update(
        {
            "settings": settings,
            "db": db,
            "downloader": InstagramDownloader(settings),
            "publisher": InstagramPublisher(settings),
            "destinations": destinations,
            "tagger": AutoTagger(settings),
        }
    )

    register_handlers(application)
    post_times = register_schedule(application)

    print(
        "Reelay started: "
        + ", ".join(post_times)
        + f" ({settings.timezone})"
    )
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
