import json
import os
import re
from pprint import pprint
from time import perf_counter
from typing import Any

import mwparserfromhell
from dateutil import parser
from deepdiff import DeepDiff
from pywikibot import Site, textlib, Claim, ItemPage, info, critical, Timestamp, WbTime
from pywikibot.bot import ExistingPageBot
from pywikibot.page import BasePage
from pywikibot.page._collections import ClaimCollection
from pywikibot.pagegenerators import SearchPageGenerator
from pywikibot.scripts.generate_user_files import pywikibot as pwb


class WikidataEntity:
    Circa = "Q5727902"
    Copyrighted = "Q50423863"
    DedicatedToPublicDomainByCopyrightOwner = "Q88088423"
    FileAvailableOnInternet = "Q74228490"
    PublicDomain = "Q19652"
    StatedByCopyrightHolderAtSourceWebsite = "Q61045577"
    USACE = 'Q1049334'
    WorkOfTheFederalGovernmentOfTheUnitedStates = "Q60671452"


class WikidataProperty:
    AppliesToJurisdiction = "P1001"
    AuthorName = "P2093"
    CopyrightLicense = "P275"
    CopyrightStatus = "P6216"
    Creator = "P170"
    DescribedAtUrl = "P973"
    DeterminationMethod = "P459"
    Inception = "P571"
    Operator = "P137"
    PublicationDate = "P577"
    PublishedIn = "P1433"
    SourceOfFile = "P7482"
    SourcingCircumstances = "P1480"
    Title = "P1476"
    Url = "P2699"


def parse_date(date: str) -> re.Match:
    return re.match(r'^(\d{4}(-\d{2}(-\d{2})?)?)$', date)


class UsaceBot(ExistingPageBot):
    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

        if os.getenv('PWB_CONSUMER_TOKEN') and os.getenv('PWB_CONSUMER_SECRET') and os.getenv(
                'PWB_ACCESS_TOKEN') and os.getenv('PWB_ACCESS_SECRET'):
            authenticate = (
                os.getenv('PWB_CONSUMER_TOKEN'),
                os.getenv('PWB_CONSUMER_SECRET'),
                os.getenv('PWB_ACCESS_TOKEN'),
                os.getenv('PWB_ACCESS_SECRET'),
            )
            pwb.config.authenticate["commons.wikimedia.org"] = authenticate
        else:
            pwb.config.password_file = "user-password.py"

        self.wikidata = Site("wikidata", "wikidata")
        self.commons = Site("commons", "commons", user=os.getenv("PWB_USERNAME") or "CuratorBot")
        self.commons.login()

        self.generator = SearchPageGenerator(f'deepcat:"Images from USACE" -haswbstatement:{WikidataProperty.SourceOfFile}', site=self.commons)
        self.user_agent = f"{self.commons.username()} / Wikimedia Commons"

    def skip_page(self, page: BasePage) -> bool:
        return 'CuratorBot' != page.oldest_revision.user

    def treat_page(self) -> None:
        info(self.current_page.full_url())

        mid = f'M{self.current_page.pageid}'
        templ = textlib.extract_templates_and_params(self.current_page.text, True, True)

        photograph = list(filter(lambda t: t[0] == 'Photograph' or t[0] == 'Book', templ))[0]
        date = photograph[1]['date'] if 'date' in photograph[1] else None
        source = photograph[1]['source'] if 'source' in photograph[1] else ''

        pprint(date)
        pprint(source)

        new_claims = []
        existing_claims = ClaimCollection.fromJSON(
            data=self.commons.simple_request(action="wbgetentities", ids=mid).submit()['entities'][mid]['statements'],
            repo=self.commons
        )

        if date is not None and (inception_claim := self.process_inception_claim(existing_claims, date)) is not None:
            new_claims.append(inception_claim.toJSON())

        if (source_claim := self.process_source_claim(existing_claims, source)) is not None:
            new_claims.append(source_claim.toJSON())

        if not new_claims:
            info("No claims to set")
            return

        payload = {
            "action": "wbeditentity",
            "id": mid,
            "data": json.dumps({"claims": new_claims}),
            "token": self.commons.get_tokens("csrf")['csrf'],
            "summary": "add [[Commons:Structured data|SDC]] based on metadata. Task #3",
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

    def process_inception_claim(self, existing_claims: ClaimCollection, date: str) -> Claim | None:
        if WikidataProperty.Inception in existing_claims:
            return None

        if (date_matches := parse_date(date)) is not None:
            return self.create_inception_claim(date, date_matches)

        wikitext = mwparserfromhell.parse(date)
        complex_date = [t for t in wikitext.filter_templates() if t.name.matches("complex date")]

        if len(complex_date) != 1 or len(complex_date[0].params) != 2:
            return None

        param0 = complex_date[0].params[0].value.get(0).value
        param1 = complex_date[0].params[1].value.get(0).value

        if param0 == 'ca' and (date_matches := parse_date(param1)) is not None:
            circa_qualifier = Claim(self.commons, WikidataProperty.SourcingCircumstances)
            circa_qualifier.setTarget(ItemPage(self.wikidata, WikidataEntity.Circa))

            return self.create_inception_claim(param1, date_matches, [circa_qualifier])

    def create_inception_claim(self, date: str, date_matches: re.Match, qualifiers: list[Claim] = ()) -> Claim:
        pprint(date_matches.groups())

        ts = Timestamp.fromisoformat(parser.isoparse(date).isoformat())
        precision = 'day' if date_matches.group(3) else 'month' if date_matches.group(2) else 'year'
        wb_ts = WbTime.fromTimestamp(ts, precision)

        pprint(wb_ts)

        claim = Claim(self.commons, WikidataProperty.Inception)
        claim.setTarget(wb_ts)

        for qualifier in qualifiers:
            claim.addQualifier(qualifier)

        return claim

    def process_source_claim(self, existing_claims: ClaimCollection, source: str) -> Claim | None:
        if WikidataProperty.SourceOfFile in existing_claims:
            return None

        if re.match(r'^https://usace\.contentdm\.oclc\.org/digital/collection/p\d+coll\d+/id/\d+$', source) is None:
            return None

        claim = Claim(self.commons, WikidataProperty.SourceOfFile)
        claim.setTarget(ItemPage(self.wikidata, WikidataEntity.FileAvailableOnInternet))

        qualifier_described_at_url = Claim(self.commons, WikidataProperty.DescribedAtUrl)
        qualifier_described_at_url.setTarget(source)
        claim.addQualifier(qualifier_described_at_url)

        qualifier_operator = Claim(self.commons, WikidataProperty.Operator)
        qualifier_operator.setTarget(ItemPage(self.wikidata, WikidataEntity.USACE))
        claim.addQualifier(qualifier_operator)

        return claim


def main():
    UsaceBot().run()


if __name__ == "__main__":
    main()
