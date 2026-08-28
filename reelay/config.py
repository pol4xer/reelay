import os
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _required(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing {name} in .env")
    return value


def _bool(name, default="false"):
    return os.getenv(name, default).strip().lower() == "true"


class Settings:
    def __init__(self):
        self.root = ROOT
        self.data_dir = ROOT / "data"
        self.video_dir = self.data_dir / "videos"
        self.db_path = self.data_dir / "reelay.db"

        self.telegram_token = _required("TELEGRAM_BOT_TOKEN")
        owner_id = os.getenv("TELEGRAM_OWNER_ID", "").strip()
        self.telegram_owner_id = int(owner_id) if owner_id else None
        self.telegram_owner_username = os.getenv(
            "TELEGRAM_OWNER_USERNAME", "pol4xer"
        ).strip().lstrip("@").lower()

        self.meta_api_version = os.getenv("META_API_VERSION", "v26.0").strip()
        self.meta_ig_user_id = _required("META_IG_USER_ID")
        self.meta_page_access_token = _required("META_PAGE_ACCESS_TOKEN")
        self.instagram_username = _required("INSTAGRAM_USERNAME")

        self.chrome_profile = os.getenv("CHROME_PROFILE", "Default").strip()
        self.timezone = os.getenv("TIMEZONE", "Europe/Istanbul").strip()
        self.post_times = [
            value.strip()
            for value in os.getenv("POST_TIMES", "12:30,17:30,21:00").split(",")
            if value.strip()
        ]
        self.post_on_weekends = _bool("POST_ON_WEEKENDS", "true")
        self.send_mp4_automatically = _bool("SEND_MP4_AUTOMATICALLY")
        self.allow_private_sources = _bool("ALLOW_PRIVATE_SOURCES")
        self.delete_after_publish = _bool("DELETE_AFTER_PUBLISH", "true")
        self.auto_tags = _bool("AUTO_TAGS", "true")
        self.auto_tag_count = int(os.getenv("AUTO_TAG_COUNT", "6"))

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.video_dir.mkdir(parents=True, exist_ok=True)
