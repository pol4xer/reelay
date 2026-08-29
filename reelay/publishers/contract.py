from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable


class Platform(StrEnum):
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    THREADS = "threads"
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"


@dataclass(frozen=True, slots=True)
class PublishRequest:
    video_path: Path
    caption: str = ""
    title: str = ""
    job_id: int | None = None

    def __post_init__(self):
        if not isinstance(self.video_path, Path):
            raise TypeError("video_path must be a pathlib.Path")
        if not isinstance(self.caption, str):
            raise TypeError("caption must be a string")
        if not isinstance(self.title, str):
            raise TypeError("title must be a string")
        if self.job_id is not None and (
            isinstance(self.job_id, bool) or not isinstance(self.job_id, int) or self.job_id <= 0
        ):
            raise TypeError("job_id must be a positive integer or None")

        video_path = Path(self.video_path).expanduser().resolve()
        if not video_path.is_file():
            raise FileNotFoundError(f"Video is missing: {video_path}")

        object.__setattr__(self, "video_path", video_path)


@dataclass(frozen=True, slots=True)
class PublishResult:
    platform: Platform
    media_id: str
    permalink: str | None = None

    def __post_init__(self):
        if not isinstance(self.platform, Platform):
            raise TypeError("platform must be a Platform")
        media_id = str(self.media_id).strip()
        if not media_id:
            raise ValueError("media_id must not be empty")
        if self.permalink is not None and not isinstance(self.permalink, str):
            raise TypeError("permalink must be a string or None")

        object.__setattr__(self, "media_id", media_id)
        if self.permalink is not None:
            object.__setattr__(self, "permalink", self.permalink.strip() or None)


@runtime_checkable
class Publisher(Protocol):
    platform: Platform

    async def publish(self, request: PublishRequest) -> PublishResult: ...
