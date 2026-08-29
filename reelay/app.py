import logging

from telegram.ext import Application

from .bot import post_init, post_stop, register_handlers
from .config import Settings
from .db import QueueDB
from .downloader import InstagramDownloader
from .media import ReelayWatermarker
from .publishers import PublisherRegistry
from .scheduler import register_schedule
from .services import PublishingService
from .tagger import AutoTagger

LOGGER = logging.getLogger(__name__)


def build_application(settings=None):
    settings = settings or Settings()
    db = QueueDB(settings.db_path)
    db.init()
    rebased = db.rebase_existing_video_paths(settings.video_dir)
    if rebased:
        LOGGER.warning("Rebased moved video paths: %s", rebased)
    tagger = AutoTagger(settings)
    ignored = db.sync_platform_enabled("tiktok", settings.publish_tiktok)
    if ignored:
        LOGGER.info("TikTok was not required for previously completed jobs: %s", ignored)
    publishers = PublisherRegistry.from_settings(settings, token_store=db)
    watermarker = ReelayWatermarker(settings)
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
        watermarker=watermarker,
    )

    application = (
        Application.builder()
        .token(settings.telegram_token)
        .post_init(post_init)
        .post_stop(post_stop)
        .build()
    )
    application.bot_data.update(
        {
            "settings": settings,
            "db": db,
            "download_tasks": set(),
            "downloader": InstagramDownloader(settings),
            "publishers": publishers,
            "publishing_service": publishing_service,
            "tagger": tagger,
            "watermarker": watermarker,
        }
    )
    register_handlers(application)
    post_times = register_schedule(application, startup=True)
    return application, post_times
