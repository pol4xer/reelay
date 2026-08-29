from collections.abc import Iterable, Iterator, Mapping
from typing import TYPE_CHECKING, Self

from .contract import Platform, Publisher
from .facebook import FacebookPublisher
from .instagram import InstagramPublisher
from .threads import ThreadsPublisher
from .tiktok import TikTokPublisher
from .youtube import YouTubePublisher

if TYPE_CHECKING:
    from ..config import Settings


class PublisherRegistry(Mapping[Platform, Publisher]):
    def __init__(self, publishers: Iterable[Publisher]):
        registered: dict[Platform, Publisher] = {}
        for publisher in publishers:
            if not isinstance(publisher, Publisher):
                raise TypeError("registry values must implement Publisher")
            platform = publisher.platform
            if not isinstance(platform, Platform):
                raise TypeError("publisher.platform must be a Platform")
            if platform in registered:
                raise ValueError(f"Duplicate publisher: {platform.value}")
            registered[platform] = publisher
        self._publishers = registered

    @classmethod
    def from_settings(cls, settings: "Settings", token_store=None) -> Self:
        publishers: list[Publisher] = [InstagramPublisher(settings)]
        if settings.publish_facebook:
            publishers.append(FacebookPublisher(settings))
        if settings.publish_threads:
            publishers.append(ThreadsPublisher(settings))
        if settings.publish_youtube:
            publishers.append(YouTubePublisher(settings))
        if settings.publish_tiktok:
            publishers.append(TikTokPublisher(settings, token_store=token_store))
        return cls(publishers)

    def __getitem__(self, platform: Platform) -> Publisher:
        if not isinstance(platform, Platform):
            raise TypeError("registry keys must be Platform values")
        return self._publishers[platform]

    def __iter__(self) -> Iterator[Platform]:
        return iter(self._publishers)

    def __len__(self) -> int:
        return len(self._publishers)

    def require(self, platform: Platform) -> Publisher:
        try:
            return self[platform]
        except KeyError as error:
            raise RuntimeError(f"Publisher is disabled: {platform.value}") from error
