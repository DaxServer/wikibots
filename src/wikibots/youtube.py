import logging
import os
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter

import googleapiclient.discovery
from dateutil.parser import isoparse
from googleapiclient.errors import HttpError

from wikibots.lib.bot import BaseBot
from wikibots.lib.claim import Claim
from wikibots.lib.wikidata import WikidataEntity, WikidataProperty

logger = logging.getLogger(__name__)


@dataclass
class YouTubeChannel:
    id: str
    title: str
    handle: str | None = None


@dataclass
class YouTubeVideo:
    channel: YouTubeChannel
    id: str
    published_at: datetime
    title: str


class YouTubeBot(BaseBot):
    redis_prefix = "Rb7S5jwVOrdIQ6OI9Uu0clfTqAAwH3ayhEKbTtd3ESA="
    summary = "add [[Commons:Structured data|SDC]] based on metadata from YouTube"

    # Throttle to 30 seconds to be under YouTube API quota 10k/day
    throttle = 30
    search_query = (
        f'file: filemime:video hastemplate:"YouTube CC-BY"'
        f" -haswbstatement:{WikidataProperty.YouTubeVideoId}"
    )

    def __init__(self) -> None:
        super().__init__()

        self.youtube = googleapiclient.discovery.build(
            "youtube", "v3", developerKey=os.getenv("YOUTUBE_API_KEY")
        )
        self.video: YouTubeVideo | None = None

    def treat_page(self) -> None:
        # Reset
        self.video = None

        youtube_id = self.retrieve_template_data(["From YouTube"], ["1"])
        if not youtube_id:
            return

        self.fetch_claims()
        self.create_id_claim(WikidataProperty.YouTubeVideoId, youtube_id)

        self._fetch_youtube_data(youtube_id)
        if self.video is None:
            self.save()
            return

        self.create_creator_claim(self.video.channel.title)
        self.create_source_claim(f"https://www.youtube.com/watch?v={youtube_id}")

        self.save()

    def _fetch_youtube_data(self, youtube_id: str) -> None:
        try:
            start = perf_counter()
            video = self.youtube.videos().list(part="snippet", id=youtube_id).execute()
            logger.info(
                f"Retrieved video data in {(perf_counter() - start) * 1000:.0f} ms"
            )
        except HttpError as e:
            logger.warning(f"Error fetching video data: {str(e)}")
            return

        if video["pageInfo"]["totalResults"] == 0:
            logger.warning(f"No video found with ID {youtube_id}")
            return

        video_title = video["items"][0]["snippet"]["localized"]["title"].strip()
        logger.info(f"Video title: {video_title}")

        published_at = isoparse(video["items"][0]["snippet"]["publishedAt"].strip())
        logger.info(published_at)

        channel_id = video["items"][0]["snippet"]["channelId"].strip()
        logger.info(f"Channel ID: {channel_id}")

        channel_title = video["items"][0]["snippet"]["channelTitle"].strip()
        logger.info(f"Channel title: {channel_title}")

        channel_handle = None
        try:
            start = perf_counter()
            channel = (
                self.youtube.channels().list(part="snippet", id=channel_id).execute()
            )
            logger.info(
                f"Retrieved channel data in {(perf_counter() - start) * 1000:.0f} ms"
            )

            if (
                channel["pageInfo"]["totalResults"] == 1
                and "customUrl" in channel["items"][0]["snippet"]
            ):
                channel_handle = (
                    channel["items"][0]["snippet"]["customUrl"].strip().lstrip("@")
                )
                logger.info(f"Channel handle: {channel_handle}")
        except HttpError as e:
            logger.warning(f"Error fetching channel data: {str(e)}")

        ytc = YouTubeChannel(id=channel_id, title=channel_title, handle=channel_handle)
        self.video = YouTubeVideo(
            id=youtube_id, channel=ytc, published_at=published_at, title=video_title
        )

    def hook_creator_claim(self, claim: Claim) -> None:
        if not self.video:
            return
        if self.video.channel and self.video.channel.handle:
            claim.add_qualifier_string(
                WikidataProperty.YouTubeHandle, self.video.channel.handle
            )
        claim.add_qualifier_string(
            WikidataProperty.YouTubeChannelId, self.video.channel.id
        )

    def hook_source_claim(self, claim: Claim) -> None:
        if not self.video:
            return
        claim.add_qualifier_entity(
            WikidataProperty.ContentDeliverer, WikidataEntity.YouTube
        )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    YouTubeBot().run()


if __name__ == "__main__":
    main()
