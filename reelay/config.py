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
        self.meta_page_id = os.getenv("META_PAGE_ID", "").strip()
        self.meta_page_access_token = _required("META_PAGE_ACCESS_TOKEN")
        self.instagram_username = _required("INSTAGRAM_USERNAME")

        self.publish_facebook = _bool("PUBLISH_FACEBOOK")
        self.publish_threads = _bool("PUBLISH_THREADS")
        self.publish_youtube = _bool("PUBLISH_YOUTUBE")

        self.threads_api_version = os.getenv(
            "THREADS_API_VERSION", "v1.0"
        ).strip()
        self.threads_app_id = os.getenv("THREADS_APP_ID", "").strip()
        self.threads_app_secret = os.getenv(
            "THREADS_APP_SECRET", ""
        ).strip()
        self.threads_user_id = os.getenv("THREADS_USER_ID", "").strip()
        self.threads_access_token = os.getenv(
            "THREADS_ACCESS_TOKEN", ""
        ).strip()

        self.youtube_client_id = os.getenv("YOUTUBE_CLIENT_ID", "").strip()
        self.youtube_client_secret = os.getenv(
            "YOUTUBE_CLIENT_SECRET", ""
        ).strip()
        self.youtube_refresh_token = os.getenv(
            "YOUTUBE_REFRESH_TOKEN", ""
        ).strip()
        self.youtube_channel_id = os.getenv(
            "YOUTUBE_CHANNEL_ID", ""
        ).strip()
        self.youtube_privacy_status = os.getenv(
            "YOUTUBE_PRIVACY_STATUS", "private"
        ).strip()

        required_by_flag = {
            "PUBLISH_FACEBOOK": (
                self.publish_facebook,
                {"META_PAGE_ID": self.meta_page_id},
            ),
            "PUBLISH_THREADS": (
                self.publish_threads,
                {
                    "THREADS_USER_ID": self.threads_user_id,
                    "THREADS_ACCESS_TOKEN": self.threads_access_token,
                },
            ),
            "PUBLISH_YOUTUBE": (
                self.publish_youtube,
                {
                    "YOUTUBE_CLIENT_ID": self.youtube_client_id,
                    "YOUTUBE_CLIENT_SECRET": self.youtube_client_secret,
                    "YOUTUBE_REFRESH_TOKEN": self.youtube_refresh_token,
                    "YOUTUBE_CHANNEL_ID": self.youtube_channel_id,
                },
            ),
        }
        for flag, (enabled, values) in required_by_flag.items():
            if enabled:
                missing = [name for name, value in values.items() if not value]
                if missing:
                    raise RuntimeError(
                        f"{flag}=true requires {', '.join(missing)}"
                    )

        self.chrome_profile = os.getenv("CHROME_PROFILE", "Default").strip()
        self.timezone = os.getenv("TIMEZONE", "Europe/Istanbul").strip()
        self.posts_per_day = int(os.getenv("POSTS_PER_DAY", "5"))
        self.post_window_start = os.getenv(
            "POST_WINDOW_START", "09:00"
        ).strip()
        self.post_window_end = os.getenv(
            "POST_WINDOW_END", "21:00"
        ).strip()
        self.post_on_weekends = _bool("POST_ON_WEEKENDS", "true")
        self.send_mp4_automatically = _bool("SEND_MP4_AUTOMATICALLY")
        self.allow_private_sources = _bool("ALLOW_PRIVATE_SOURCES")
        self.delete_after_publish = _bool("DELETE_AFTER_PUBLISH", "true")
        self.auto_tags = _bool("AUTO_TAGS", "true")
        self.auto_tag_min = int(os.getenv("AUTO_TAG_MIN", "15"))
        self.auto_tag_max = int(os.getenv("AUTO_TAG_MAX", "20"))

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.video_dir.mkdir(parents=True, exist_ok=True)
