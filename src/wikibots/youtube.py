import json
import os
import sys
from pprint import pprint
from time import perf_counter
from typing import Any

import googleapiclient.discovery
import googleapiclient.errors
import mwparserfromhell
from dateutil.parser import isoparse
from deepdiff import DeepDiff
from lingua.lingua import LanguageDetectorBuilder
from pywikibot import Site, textlib, Claim, ItemPage, info, critical, Timestamp, WbTime, WbMonolingualText
from pywikibot.bot import ExistingPageBot
from pywikibot.page._collections import ClaimCollection
from pywikibot.pagegenerators import SearchPageGenerator, PagesFromTitlesGenerator
from pywikibot.scripts.generate_user_files import pywikibot
from pywikibot.titletranslate import translate

try:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)) + '/lib')
    from lib.bot import BaseBot
    from lib.wikidata import WikidataEntity, WikidataProperty
except:
    from .lib.bot import BaseBot
    from .lib.wikidata import WikidataEntity, WikidataProperty


class YouTubeBot(BaseBot):
    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

        self.generator = SearchPageGenerator(f'file: deepcat:"License reviewed by YouTubeReviewBot" filemime:video hastemplate:"YouTubeReview" -haswbstatement:{WikidataProperty.YouTubeVideoId}', site=self.commons)

        self.youtube = googleapiclient.discovery.build('youtube', 'v3', developerKey=os.getenv('YOUTUBE_API_KEY'))
        self.language_detector = LanguageDetectorBuilder.from_all_languages().build()

    def treat_page(self) -> None:
        mid = f'M{self.current_page.pageid}'
        info(self.current_page.full_url())
        info(mid)

        wikitext = mwparserfromhell.parse(self.current_page.text)

        youtube_id = [w for w in wikitext.filter_templates() if w.name == 'YouTubeReview'][0].get('id').value
        pprint(f'Video ID: {youtube_id}')

        video = self.youtube.videos().list(part="snippet", id=youtube_id).execute()

        if video['pageInfo']['totalResults'] == 0:
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
        if WikidataProperty.YouTubeVideoId in self.existing_claims:
            return

        claim = Claim(self.commons, WikidataProperty.YouTubeVideoId)
        claim.setTarget(videoId)

        self.new_claims.append(claim.toJSON())

    def create_published_in_claim(self, date: str) -> None:
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
        if WikidataProperty.SourceOfFile in self.existing_claims:
            return

        super().create_source_claim(source, WikidataEntity.YouTube)

    def process_copyright_license_claim(self, video_title: str, channel_title: str) -> None:
        if WikidataProperty.CopyrightLicense not in self.existing_claims or len(self.existing_claims[WikidataProperty.CopyrightLicense]) != 1:
            return

        claim: Claim = self.existing_claims[WikidataProperty.CopyrightLicense][0]
        edited = False

        if WikidataProperty.Title not in claim.qualifiers:
            language = self.language_detector.detect_language_of(video_title)
            pprint(language.name)

            title_qualifier = Claim(self.commons, WikidataProperty.Title)
            title_qualifier.setTarget(WbMonolingualText(video_title, language.iso_code_639_1.name.lower()))
            claim.addQualifier(title_qualifier)
            edited = True

        if WikidataProperty.AuthorNameString not in claim.qualifiers:
            author_name_string_qualifier = Claim(self.commons, WikidataProperty.AuthorNameString)
            author_name_string_qualifier.setTarget(channel_title)
            claim.addQualifier(author_name_string_qualifier)
            edited = True

        if edited:
            self.new_claims.append(claim.toJSON())


def main():
    YouTubeBot().run()


if __name__ == "__main__":
    main()
