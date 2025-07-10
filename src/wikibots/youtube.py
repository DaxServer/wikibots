import os
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import Any

import googleapiclient.discovery
from dateutil.parser import isoparse
from googleapiclient.errors import HttpError
from pywikibot import Claim, ItemPage, info, warning
from pywikibot.pagegenerators import SearchPageGenerator

from wikibots.lib.bot import BaseBot
from wikibots.lib.wikidata import WikidataEntity, WikidataProperty


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

    def __init__(self, **kwargs: Any):
        """
        Initializes the YouTubeBot instance.

        Passes any keyword arguments to the base class initializer and configures key components:
          - A search generator to locate Commons files that lack a YouTube video ID.
          - A YouTube API client built with the API key from the environment.
          - A language detector constructed from all available languages.
        """
        super().__init__(**kwargs)

        self.generator = SearchPageGenerator(
            f'file: filemime:video hastemplate:"YouTube CC-BY" -haswbstatement:{WikidataProperty.YouTubeVideoId}',
            site=self.commons,
        )

        self.youtube = googleapiclient.discovery.build(
            "youtube", "v3", developerKey=os.getenv("YOUTUBE_API_KEY")
        )
        self.video: YouTubeVideo | None = None

        self.items = {
            "youtube": ItemPage(self.wikidata, WikidataEntity.YouTube),
        }

    def treat_page(self) -> None:
        """
        Processes the page to extract YouTube metadata and update Wikidata claims.

        Parses the current page's wikitext to retrieve the YouTube video ID from a
        "YouTube CC-BY" template, then uses the YouTube API to fetch video details such as
        the title, publication date, and channel information. If valid video data is found,
        it creates or updates claims for the video ID, publication date, creator details,
        source URL, and copyright license. If the video is not found, the method exits
        without making changes. Finally, it saves the updates with a descriptive edit summary.
        """
        # Reset
        self.video = None

        super().treat_page()

        youtube_id = self.retrieve_template_data(["From YouTube"], ["1"])
        if not youtube_id:
            return

        self.fetch_claims()
        self.create_id_claim(WikidataProperty.YouTubeVideoId, youtube_id)

        self._fetch_youtube_data(youtube_id)
        if self.video is None:
            self.save()
            return

        assert self.video

        self.create_published_in_claim(WikidataEntity.YouTube, self.video.published_at)
        self.create_creator_claim(self.video.channel.title)
        self.create_source_claim(f"https://www.youtube.com/watch?v={youtube_id}")

        self.save()

    def _fetch_youtube_data(self, youtube_id: str) -> None:
        """
        Fetches video details from the YouTube API

        :param str youtube_id: The ID of the YouTube video to fetch
        :return: None
        """
        try:
            start = perf_counter()
            video = self.youtube.videos().list(part="snippet", id=youtube_id).execute()
            info(f"Retrieved video data in {(perf_counter() - start) * 1000:.0f} ms")
        except HttpError as e:
            warning(f"Error fetching video data: {str(e)}")
            return

        if video["pageInfo"]["totalResults"] == 0:
            warning(f"No video found with ID {youtube_id}")
            return

        video_title = video["items"][0]["snippet"]["localized"]["title"].strip()
        info(f"Video title: {video_title}")

        published_at = isoparse(video["items"][0]["snippet"]["publishedAt"].strip())
        info(published_at)

        channel_id = video["items"][0]["snippet"]["channelId"].strip()
        info(f"Channel ID: {channel_id}")

        channel_title = video["items"][0]["snippet"]["channelTitle"].strip()
        info(f"Channel title: {channel_title}")

        channel_handle = None
        try:
            start = perf_counter()
            channel = (
                self.youtube.channels().list(part="snippet", id=channel_id).execute()
            )
            info(f"Retrieved channel data in {(perf_counter() - start) * 1000:.0f} ms")

            if (
                channel["pageInfo"]["totalResults"] == 1
                and "customUrl" in channel["items"][0]["snippet"]
            ):
                channel_handle = (
                    channel["items"][0]["snippet"]["customUrl"].strip().lstrip("@")
                )
                info(f"Channel handle: {channel_handle}")
        except HttpError as e:
            warning(f"Error fetching channel data: {str(e)}")

        ytc = YouTubeChannel(id=channel_id, title=channel_title, handle=channel_handle)
        self.video = YouTubeVideo(
            id=youtube_id, channel=ytc, published_at=published_at, title=video_title
        )

    def hook_creator_claim(self, claim: Claim) -> None:
        assert self.video

        if self.video.channel and self.video.channel.handle:
            youtube_handle_qualifier = Claim(
                self.commons, WikidataProperty.YouTubeHandle
            )
            youtube_handle_qualifier.setTarget(self.video.channel.handle)
            claim.addQualifier(youtube_handle_qualifier)

        youtube_channel_id_qualifier = Claim(
            self.commons, WikidataProperty.YouTubeChannelId
        )
        youtube_channel_id_qualifier.setTarget(self.video.channel.id)
        claim.addQualifier(youtube_channel_id_qualifier)

    def hook_source_claim(self, claim: Claim) -> None:
        assert self.video

        content_deliverer_qualifier = Claim(
            self.commons, WikidataProperty.ContentDeliverer
        )
        content_deliverer_qualifier.setTarget(self.items["youtube"])
        claim.addQualifier(content_deliverer_qualifier)


def main():
    """
    Entrypoint for running the YouTube bot.

    Instantiates the YouTubeBot and initiates its execution, starting the process of
    retrieving video metadata and updating corresponding Wikidata claims.

    If the --dry-run flag is provided, the bot will run in dry-run mode, which means it will
    not save any changes to Wikimedia Commons and will exit after processing the first page.
    """
    YouTubeBot().run()


if __name__ == "__main__":
    main()
