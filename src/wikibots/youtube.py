import json
import os
import sys
from pprint import pprint
from typing import Any

import googleapiclient.discovery
import googleapiclient.errors
import mwparserfromhell
from dateutil.parser import isoparse
from lingua.lingua import LanguageDetectorBuilder
from pywikibot import Claim, ItemPage, info, Timestamp, WbTime, WbMonolingualText
from pywikibot.page._collections import ClaimCollection
from pywikibot.pagegenerators import SearchPageGenerator
try:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)) + '/lib')
    from lib.bot import BaseBot
    from lib.wikidata import WikidataEntity, WikidataProperty
except:
    from .lib.bot import BaseBot
    from .lib.wikidata import WikidataEntity, WikidataProperty


class YouTubeBot(BaseBot):
    def __init__(self, **kwargs: Any):
        """
        Initializes the YouTubeBot instance.
        
        Passes any keyword arguments to the base class initializer and configures key components:
          - A search generator to locate Commons files using the "YouTubeReview" template that lack a YouTube video ID.
          - A YouTube API client built with the API key from the environment.
          - A language detector constructed from all available languages.
        """
        super().__init__(**kwargs)

        self.generator = SearchPageGenerator(f'file: deepcat:"License reviewed by YouTubeReviewBot" filemime:video hastemplate:"YouTubeReview" -haswbstatement:{WikidataProperty.YouTubeVideoId}', site=self.commons)

        self.youtube = googleapiclient.discovery.build('youtube', 'v3', developerKey=os.getenv('YOUTUBE_API_KEY'))
        self.language_detector = LanguageDetectorBuilder.from_all_languages().build()

    def treat_page(self) -> None:
        """
        Processes the page to extract YouTube metadata and update Wikidata claims.
        
        Parses the current page's wikitext to retrieve the YouTube video ID from a 
        "YouTubeReview" template, then uses the YouTube API to fetch video details such as 
        the title, publication date, and channel information. If valid video data is found, 
        it creates or updates claims for the video ID, publication date, creator details, 
        source URL, and copyright license. If the video is not found, the method exits 
        without making changes. Finally, it saves the updates with a descriptive edit summary.
        """
        mid = f'M{self.current_page.pageid}'
        info(self.current_page.full_url())
        info(mid)

        wikitext = mwparserfromhell.parse(self.current_page.text)

        youtube_templates = [w for w in wikitext.filter_templates() if w.name == 'YouTubeReview']
        if not youtube_templates:
            info(f"No YouTubeReview template found on {self.current_page.title()}")
            return
        
        template = youtube_templates[0]
        if not template.has('id'):
            info(f"YouTubeReview template on {self.current_page.title()} is missing the id parameter")
            return
        
        youtube_id = template.get('id').value
        pprint(f'Video ID: {youtube_id}')

        try:
            video = self.youtube.videos().list(part="snippet", id=youtube_id).execute()
            
            if video['pageInfo']['totalResults'] == 0:
                info(f"No video found with ID {youtube_id}")
                return
        except googleapiclient.errors.HttpError as e:
            info(f"Error fetching video data: {str(e)}")
            return
        video_title = video['items'][0]['snippet']['localized']['title']
        pprint(f'Video title: {video_title}')

        published_at = video['items'][0]['snippet']['publishedAt']
        pprint(published_at)

        channel_id = video['items'][0]['snippet']['channelId']
        pprint(f'Channel ID: {channel_id}')

        channel_title = video['items'][0]['snippet']['channelTitle']
        pprint(f'Channel title: {channel_title}')

        channel = self.youtube.channels().list(part="snippet", id=channel_id).execute()
        channel_handle = channel['items'][0]['snippet']['customUrl'].lstrip('@') if channel['pageInfo']['totalResults'] == 1 else None
        pprint(f'Channel handle: {channel_handle}')

        self.new_claims = []
        self.existing_claims = ClaimCollection.fromJSON(
            data=self.commons.simple_request(action="wbgetentities", ids=mid).submit()['entities'][mid]['statements'],
            repo=self.commons
        )

        self.create_youtube_video_id_claim(youtube_id.__str__())
        self.create_published_in_claim(published_at)
        self.create_creator_claim(channel_title, channel_handle, channel_id)
        self.create_source_claim(f'https://www.youtube.com/watch?v={youtube_id}')
        self.process_copyright_license_claim(video_title, channel_title)

        self.save('add [[Commons:Structured data|SDC]] based on metadata from YouTube. Test run.')

    def create_youtube_video_id_claim(self, videoId: str) -> None:
        """
        Creates a YouTube video ID claim if one is not already present.
        
        If no claim for the YouTube video ID exists, this method creates a new claim using
        the provided videoId and appends its JSON representation to the list of new claims.
        """
        if WikidataProperty.YouTubeVideoId in self.existing_claims:
            return

        claim = Claim(self.commons, WikidataProperty.YouTubeVideoId)
        claim.setTarget(videoId)

        self.new_claims.append(claim.toJSON())

    def create_published_in_claim(self, date: str) -> None:
        """
        Creates a published-in claim with a publication date qualifier.
        
        If a PublishedIn claim already exists, no claim is created. Otherwise, this
        method constructs a new claim linking the video to YouTube and adds a qualifier
        for the publication date (normalized to day precision from the provided ISO date
        string). The new claim is then added in JSON format to the list of pending claims.
        
        Args:
            date (str): The video's publication date in ISO format.
        """
        if WikidataProperty.PublishedIn in self.existing_claims:
            return

        claim = Claim(self.commons, WikidataProperty.PublishedIn)
        claim.setTarget(ItemPage(self.wikidata, WikidataEntity.YouTube))

        wb_ts = WbTime.fromTimestamp(Timestamp.fromISOformat(isoparse(date).replace(hour=0, minute=0, second=0).isoformat()), 'day')
        pprint(wb_ts)

        published_date_qualifier = Claim(self.commons, WikidataProperty.PublicationDate)
        published_date_qualifier.setTarget(wb_ts)
        claim.addQualifier(published_date_qualifier)

        self.new_claims.append(claim.toJSON())

    def create_creator_claim(self, channelTitle: str, channelHandle: str | None, channelId: str) -> None:
        """
        Creates a creator claim with channel information.
        
        If no creator claim exists, constructs a new claim that includes qualifiers for the 
        channel title, handle (if provided), and channel ID, then appends the serialized claim 
        to the list of new claims.
        
        Args:
            channelTitle: The channel title used as the author name qualifier.
            channelHandle: The channel handle used as a qualifier, if available.
            channelId: The unique YouTube channel identifier used as a qualifier.
        """
        if WikidataProperty.Creator in self.existing_claims:
            return

        claim = Claim(self.commons, WikidataProperty.Creator)
        claim.setSnakType('somevalue')

        author_name_string_qualifier = Claim(self.commons, WikidataProperty.AuthorNameString)
        author_name_string_qualifier.setTarget(channelTitle)
        claim.addQualifier(author_name_string_qualifier)

        if channelHandle is not None:
            youtube_handle_qualifier = Claim(self.commons, WikidataProperty.YouTubeHandle)
            youtube_handle_qualifier.setTarget(channelHandle)
            claim.addQualifier(youtube_handle_qualifier)

        youtube_channel_id_qualifier = Claim(self.commons, WikidataProperty.YouTubeChannelId)
        youtube_channel_id_qualifier.setTarget(channelId)
        claim.addQualifier(youtube_channel_id_qualifier)

        self.new_claims.append(claim.toJSON())

    def create_source_claim(self, source: str) -> None:
        """
        Creates a YouTube source claim if one does not already exist.
        
        If no source claim has been recorded, this method delegates claim creation to the
        parent class, linking the file to YouTube using the provided source identifier.
        
        Args:
            source: A string representing the source identifier used in the claim.
        """
        if WikidataProperty.SourceOfFile in self.existing_claims:
            return

        super().create_source_claim(source, WikidataEntity.YouTube)

    def process_copyright_license_claim(self, video_title: str, channel_title: str) -> None:
        """
        Add missing title and author name qualifiers to the existing copyright license claim.
        
        If exactly one copyright license claim exists, this method adds a title qualifier using the
        provided video title (with its language detected) and an author name qualifier using the given
        channel title if they are not already present. If any qualifier is added, the updated claim is
        appended to the list of new claims.
            
        Args:
            video_title: The video's title used for the title qualifier.
            channel_title: The channel's title used for the author name qualifier.
        """
        if WikidataProperty.CopyrightLicense not in self.existing_claims or len(self.existing_claims[WikidataProperty.CopyrightLicense]) != 1:
            return

        claim: Claim = self.existing_claims[WikidataProperty.CopyrightLicense][0]
        edited = False

        if WikidataProperty.Title not in claim.qualifiers:
            try:
                language = self.language_detector.detect_language_of(video_title)
                if hasattr(language, 'iso_code_639_1') and language.iso_code_639_1:
                    lang_code = language.iso_code_639_1.name.lower()
                    title_qualifier = Claim(self.commons, WikidataProperty.Title)
                    title_qualifier.setTarget(WbMonolingualText(video_title, lang_code))
                    claim.addQualifier(title_qualifier)
                    edited = True
                else:
                    info(f"Could not determine ISO 639-1 code for detected language: {language.name}")
            except Exception as e:
                info(f"Language detection failed: {str(e)}")
        if WikidataProperty.AuthorNameString not in claim.qualifiers:
            author_name_string_qualifier = Claim(self.commons, WikidataProperty.AuthorNameString)
            author_name_string_qualifier.setTarget(channel_title)
            claim.addQualifier(author_name_string_qualifier)
            edited = True

        if edited:
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
