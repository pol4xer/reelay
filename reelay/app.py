import logging

from telegram.ext import Application

from .bot import post_init, register_handlers
from .config import Settings
from .db import QueueDB
from .downloader import InstagramDownloader
from .publishers import PublisherRegistry
from .scheduler import register_schedule
from .services import PublishingService
from .tagger import AutoTagger

LOGGER = logging.getLogger(__name__)


def build_application(settings=None):
    settings = settings or Settings()
    db = QueueDB(settings.db_path)
    db.init()
    tagger = AutoTagger(settings)
    publishers = PublisherRegistry.from_settings(settings)
    recovery = db.recover_interrupted_jobs()
    if any(recovery.values()):
        LOGGER.warning("Recovered interrupted jobs: %s", recovery)
    incomplete = db.reconcile_incomplete_published(platform.value for platform in publishers)
    if incomplete:
        LOGGER.warning("Incomplete published jobs moved to failed: %s", incomplete)
    publishing_service = PublishingService(
        settings=settings,
        db=db,
        publishers=publishers,
        tagger=tagger,
    )

    application = Application.builder().token(settings.telegram_token).post_init(post_init).build()
    application.bot_data.update(
        {
            "settings": settings,
            "db": db,
            "downloader": InstagramDownloader(settings),
            "publishers": publishers,
            "publishing_service": publishing_service,
            "tagger": tagger,
        }
    )
    register_handlers(application)
    post_times = register_schedule(application, startup=True)
    return application, post_times
