import os
import sys
from time import perf_counter
from typing import Any

import googleapiclient.discovery
import googleapiclient.errors
import mwparserfromhell
from dateutil.parser import isoparse
from pywikibot import Claim, ItemPage, info, Timestamp, WbTime, warning
from pywikibot.pagegenerators import SearchPageGenerator

try:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)) + '/lib')
    from lib.bot import BaseBot
    from lib.wikidata import WikidataEntity, WikidataProperty
except:
    from .lib.bot import BaseBot
    from .lib.wikidata import WikidataEntity, WikidataProperty


class YouTubeChannel:
    Handle: str | None
    Id: str
    Title: str

    def __init__(self, channel_id: str, title: str, handle: str | None):
        self.Handle = handle
        self.Id = channel_id
        self.Title = title


class YouTubeVideo:
    Channel: YouTubeChannel
    Id: str
    PublishedAt: str
    Title: str

    def __init__(self, video_id: str, channel: YouTubeChannel, published_at: str, title: str):
        self.Channel = channel
        self.Id = video_id
        self.PublishedAt = published_at
        self.Title = title


class YouTubeBot(BaseBot):
    redis_prefix = 'Rb7S5jwVOrdIQ6OI9Uu0clfTqAAwH3ayhEKbTtd3ESA='
    summary = 'add [[Commons:Structured data|SDC]] based on metadata from YouTube'

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

        self.generator = SearchPageGenerator(f'file: incategory:"License review needed (video)" filemime:video hastemplate:"YouTube CC-BY" -haswbstatement:{WikidataProperty.YouTubeVideoId}', site=self.commons)

        self.youtube = googleapiclient.discovery.build('youtube', 'v3', developerKey=os.getenv('YOUTUBE_API_KEY'))

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
        super().treat_page()

        youtube_id = self.retrieve_template_data(['From YouTube'], ['1'])
        if not youtube_id:
            return

        self.fetch_claims()
        self.create_id_claim(WikidataProperty.YouTubeVideoId, youtube_id)

        video_data = self._fetch_youtube_data(youtube_id)
        if video_data is None:
            self.save()
            return

        self.create_published_in_claim(video_data.PublishedAt)
        self.create_creator_claim(video_data.Channel)
        self.create_source_claim(f'https://www.youtube.com/watch?v={youtube_id}', WikidataEntity.YouTube)

        self.save()

    def _fetch_youtube_data(self, youtube_id: str) -> YouTubeVideo | None:
        """
        Fetches video details from the YouTube API

        :param str youtube_id: The ID of the YouTube video to fetch
        :return: A dictionary containing video details, or None if the video is not found
        :rtype: YouTubeVideo | None
        """
        try:
            start = perf_counter()
            video = self.youtube.videos().list(part="snippet", id=youtube_id).execute()
            info(f"Retrieved video data in {(perf_counter() - start) * 1000:.0f} ms")
        except googleapiclient.errors.HttpError as e:
            warning(f"Error fetching video data: {str(e)}")
            return None

        if video['pageInfo']['totalResults'] == 0:
            warning(f"No video found with ID {youtube_id}")
            return None

        video_title = video['items'][0]['snippet']['localized']['title'].strip()
        info(f'Video title: {video_title}')

        published_at = video['items'][0]['snippet']['publishedAt'].strip()
        info(published_at)

        channel_id = video['items'][0]['snippet']['channelId'].strip()
        info(f'Channel ID: {channel_id}')

        channel_title = video['items'][0]['snippet']['channelTitle'].strip()
        info(f'Channel title: {channel_title}')

        channel_handle = None
        try:
            start = perf_counter()
            channel = self.youtube.channels().list(part="snippet", id=channel_id).execute()
            info(f"Retrieved channel data in {(perf_counter() - start) * 1000:.0f} ms")

            if channel['pageInfo']['totalResults'] == 1 and 'customUrl' in channel['items'][0]['snippet']:
                channel_handle = channel['items'][0]['snippet']['customUrl'].strip().lstrip('@')
                info(f'Channel handle: {channel_handle}')
        except googleapiclient.errors.HttpError as e:
            warning(f"Error fetching channel data: {str(e)}")

        ytc = YouTubeChannel(channel_id=channel_id, title=channel_title, handle=channel_handle)
        yt = YouTubeVideo(video_id=youtube_id, channel=ytc, published_at=published_at, title=video_title)

        return yt

    def create_published_in_claim(self, date: str) -> None:
        """
        Creates a published-in claim with a publication date qualifier.
        
        If a PublishedIn claim already exists, no claim is created. Otherwise, this
        method constructs a new claim linking the video to YouTube and adds a qualifier
        for the publication date (normalized to day precision from the provided ISO date
        string). The new claim is then added in JSON format to the list of pending claims.

        :param str date: The video's publication date in ISO format
        :rtype: None
        """
        if WikidataProperty.PublishedIn in self.existing_claims:
            return

        claim = Claim(self.commons, WikidataProperty.PublishedIn)
        claim.setTarget(ItemPage(self.wikidata, WikidataEntity.YouTube))

        wb_ts = WbTime.fromTimestamp(
            Timestamp.fromISOformat(isoparse(date).replace(hour=0, minute=0, second=0).isoformat()),
            WbTime.PRECISION['day'],
        )

        published_date_qualifier = Claim(self.commons, WikidataProperty.PublicationDate)
        published_date_qualifier.setTarget(wb_ts)
        claim.addQualifier(published_date_qualifier)

        self.new_claims.append(claim.toJSON())

    def create_creator_claim(self, channel: YouTubeChannel) -> None:
        """
        Creates a creator claim with channel information.
        
        If no creator claim exists, constructs a new claim that includes qualifiers for the 
        channel title, handle (if provided), and channel ID, then appends the serialized claim 
        to the list of new claims.

        :param channel: The YouTubeChannel object containing channel details
        :type channel: YouTubeChannel
        :rtype: None
        """
        if WikidataProperty.Creator in self.existing_claims:
            return

        claim = Claim(self.commons, WikidataProperty.Creator)
        claim.setSnakType('somevalue')

        author_name_string_qualifier = Claim(self.commons, WikidataProperty.AuthorNameString)
        author_name_string_qualifier.setTarget(channel.Title)
        claim.addQualifier(author_name_string_qualifier)

        if channel.Handle is not None:
            youtube_handle_qualifier = Claim(self.commons, WikidataProperty.YouTubeHandle)
            youtube_handle_qualifier.setTarget(channel.Handle)
            claim.addQualifier(youtube_handle_qualifier)

        youtube_channel_id_qualifier = Claim(self.commons, WikidataProperty.YouTubeChannelId)
        youtube_channel_id_qualifier.setTarget(channel.Id)
        claim.addQualifier(youtube_channel_id_qualifier)

        self.new_claims.append(claim.toJSON())


def main():
    """
    Entrypoint for running the YouTube bot.
    
    Instantiates the YouTubeBot and initiates its execution, starting the process of
    retrieving video metadata and updating corresponding Wikidata claims.
    """
    YouTubeBot().run()


if __name__ == "__main__":
    main()
