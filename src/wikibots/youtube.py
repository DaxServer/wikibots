import json
import os
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


class WikidataEntity:
    FileAvailableOnInternet = "Q74228490"
    YouTube = 'Q866'


class WikidataProperty:
    AuthorNameString = "P2093"
    CopyrightLicense = "P275"
    Creator = "P170"
    DescribedAtUrl = "P973"
    Inception = "P571"
    Operator = "P137"
    PublicationDate = "P577"
    PublishedIn = "P1433"
    SourceOfFile = "P7482"
    Title = "P1476"
    Url = "P2699"
    YouTubeChannelId = "P2397"
    YouTubeHandle = "P11245"
    YouTubeVideoId = "P1651"


class YouTubeBot(ExistingPageBot):
    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

        if os.getenv('PWB_CONSUMER_TOKEN') and os.getenv('PWB_CONSUMER_SECRET') and os.getenv('PWB_ACCESS_TOKEN') and os.getenv('PWB_ACCESS_SECRET'):
            authenticate = (
                os.getenv('PWB_CONSUMER_TOKEN'),
                os.getenv('PWB_CONSUMER_SECRET'),
                os.getenv('PWB_ACCESS_TOKEN'),
                os.getenv('PWB_ACCESS_SECRET'),
            )
            pywikibot.config.authenticate["commons.wikimedia.org"] = authenticate
        else:
            pywikibot.config.password_file = "user-password.py"

        self.wikidata = Site("wikidata", "wikidata")
        self.commons = Site("commons", "commons", user=os.getenv("PWB_USERNAME") or "YouTubeBot")
        self.commons.login()

        self.generator = PagesFromTitlesGenerator(['File:(TV텐) 위아이(WEi) 김준서, 내가 바로 화보장인.webm'], site=self.commons)
        self.generator = SearchPageGenerator(f'file: deepcat:"License reviewed by YouTubeReviewBot" filemime:video hastemplate:"YouTubeReview" -haswbstatement:{WikidataProperty.YouTubeVideoId}', site=self.commons)
        self.user_agent = f"{self.commons.username()} / Wikimedia Commons"

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

        new_claims = []
        existing_claims = ClaimCollection.fromJSON(
            data=self.commons.simple_request(action="wbgetentities", ids=mid).submit()['entities'][mid]['statements'],
            repo=self.commons
        )

        if (video_id_claim := self.process_youtube_video_id_claim(existing_claims, youtube_id.__str__())) is not None:
            new_claims.append(video_id_claim.toJSON())

        if (published_in_claim := self.process_published_in_claim(existing_claims, published_at)) is not None:
            new_claims.append(published_in_claim.toJSON())

        if (creator_claim := self.process_creator_claim(existing_claims, channel_title, channel_handle, channel_id)) is not None:
            new_claims.append(creator_claim.toJSON())

        if (source_claim := self.process_source_claim(existing_claims, f'https://www.youtube.com/watch?v={youtube_id}')) is not None:
            new_claims.append(source_claim.toJSON())

        if (license_claim := self.process_copyright_license_claim(existing_claims, video_title, channel_title)) is not None:
            new_claims.append(license_claim.toJSON())

        if not new_claims:
            info("No claims to set")
            return

        payload = {
            "action": "wbeditentity",
            "id": mid,
            "data": json.dumps({"claims": new_claims}),
            "token": self.commons.get_tokens("csrf")['csrf'],
            "summary": "add [[Commons:Structured data|SDC]] based on metadata from YouTube. Test run.",
            "tags": "BotSDC",
            "bot": True,
        }
        request = self.commons.simple_request(**payload)

        pprint(DeepDiff([], new_claims))

        try:
            start = perf_counter()
            request.submit()
            info(f"Updating {mid} took {(perf_counter() - start):.1f} s")
        except Exception as e:
            critical(f"Failed to update: {e}")

    def process_published_in_claim(self, existing_claims: ClaimCollection, date: str) -> Claim | None:
        if WikidataProperty.PublishedIn in existing_claims:
            return None

        claim = Claim(self.commons, WikidataProperty.PublishedIn)
        claim.setTarget(ItemPage(self.wikidata, WikidataEntity.YouTube))

        wb_ts = WbTime.fromTimestamp(Timestamp.fromISOformat(isoparse(date).replace(hour=0, minute=0, second=0).isoformat()), 'day')
        pprint(wb_ts)

        published_date_qualifier = Claim(self.commons, WikidataProperty.PublicationDate)
        published_date_qualifier.setTarget(wb_ts)
        claim.addQualifier(published_date_qualifier)

        return claim

    def process_creator_claim(self, existing_claims: ClaimCollection, channelTitle: str, channelHandle: str | None, channelId: str) -> Claim | None:
        if WikidataProperty.Creator in existing_claims:
            return None

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

        return claim

    def process_youtube_video_id_claim(self, existing_claims: ClaimCollection, videoId: str) -> Claim | None:
        if WikidataProperty.YouTubeVideoId in existing_claims:
            return None

        claim = Claim(self.commons, WikidataProperty.YouTubeVideoId)
        claim.setTarget(videoId)

        return claim

    def process_source_claim(self, existing_claims: ClaimCollection, source: str) -> Claim | None:
        if WikidataProperty.SourceOfFile in existing_claims:
            return None

        claim = Claim(self.commons, WikidataProperty.SourceOfFile)
        claim.setTarget(ItemPage(self.wikidata, WikidataEntity.FileAvailableOnInternet))

        described_at_url_qualifier = Claim(self.commons, WikidataProperty.DescribedAtUrl)
        described_at_url_qualifier.setTarget(source)
        claim.addQualifier(described_at_url_qualifier)

        operator_qualifier = Claim(self.commons, WikidataProperty.Operator)
        operator_qualifier.setTarget(ItemPage(self.wikidata, WikidataEntity.YouTube))
        claim.addQualifier(operator_qualifier)

        return claim

    def process_copyright_license_claim(self, existing_claims: ClaimCollection, video_title: str, channel_title: str) -> Claim | None:
        if WikidataProperty.CopyrightLicense not in existing_claims or len(existing_claims[WikidataProperty.CopyrightLicense]) != 1:
            return None

        claim: Claim = existing_claims[WikidataProperty.CopyrightLicense][0]
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

        return claim if edited else None


def main():
    YouTubeBot().run()


if __name__ == "__main__":
    main()
