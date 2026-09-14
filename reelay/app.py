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


def _validate_owner_configuration(settings, db):
    stored = db.get_setting("telegram_owner_id")
    if stored not in (None, ""):
        try:
            owner_id = int(stored)
        except (TypeError, ValueError):
            raise RuntimeError("The stored Telegram owner ID is invalid") from None
        if owner_id <= 0:
            raise RuntimeError("The stored Telegram owner ID must be positive")
        return
    if settings.telegram_owner_id is not None or settings.telegram_owner_username:
        return
    raise RuntimeError(
        "Configure TELEGRAM_OWNER_ID or TELEGRAM_OWNER_USERNAME before the first start"
    )


def build_application(settings=None):
    settings = settings or Settings()
    db = QueueDB(settings.db_path)
    db.init()
    _validate_owner_configuration(settings, db)
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
