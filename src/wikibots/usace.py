import json
import os
import re
from pprint import pprint
from time import perf_counter
from typing import Any

import pywikibot
from dateutil import parser
from deepdiff import DeepDiff
from pywikibot import Site, textlib, Claim, ItemPage
from pywikibot.bot import ExistingPageBot
from pywikibot.page import WikibaseEntity
from pywikibot.page._collections import ClaimCollection
from pywikibot.pagegenerators import SearchPageGenerator

from .lib.wikidata_entities import WikidataEntity
from .lib.wikidata_properties import WikidataProperty


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
            pywikibot.config.authenticate["commons.wikimedia.org"] = authenticate
        else:
            pywikibot.config.password_file = "user-password.py"

        self.wikidata = Site("wikidata", "wikidata")
        self.commons = Site("commons", "commons", user=os.getenv("PWB_USERNAME") or "CuratorBot")
        self.commons.login()

        self.generator = SearchPageGenerator(f'deepcat:"Images from USACE" -haswbstatement:{WikidataProperty.SourceOfFile}', site=self.commons)
        self.user_agent = f"{self.commons.username()} / Wikimedia Commons"

    def skip_page(self, page: pywikibot.page.BasePage) -> bool:
        return 'CuratorBot' != page.oldest_revision.user

    def treat_page(self) -> None:
        pywikibot.info(self.current_page.full_url())

        mid = f'M{self.current_page.pageid}'
        templ = textlib.extract_templates_and_params(self.current_page.text, True, True)

        photograph = list(filter(lambda t: t[0] == 'Photograph' or t[0] == 'Book', templ))[0]
        date = photograph[1]['date'] if 'date' in photograph[1] else ''
        source = photograph[1]['source'] if 'source' in photograph[1] else ''

        pprint(date)
        pprint(source)

        new_claims = []
        existing_claims = ClaimCollection.fromJSON(
            data=self.commons.simple_request(action="wbgetentities", ids=mid).submit()['entities'][mid]['statements'],
            repo=self.commons
        )

        if WikidataProperty.Inception not in existing_claims and (date_matches := re.match(r'^(\d{4}(-\d{2}(-\d{2})?)?)$', date or '')) is not None:
            pprint(date_matches.groups())

            ts = pywikibot.Timestamp.fromisoformat(parser.isoparse(date).isoformat())
            precision = 'day' if date_matches.group(3) else 'month' if date_matches.group(2) else 'year'
            wb_ts = pywikibot.WbTime.fromTimestamp(ts, precision)

            pprint(wb_ts)

            claim = Claim(self.commons, WikidataProperty.Inception)
            claim.setTarget(wb_ts)

            new_claims.append(claim.toJSON())

        if WikidataProperty.SourceOfFile not in existing_claims and re.match(r'^https://usace\.contentdm\.oclc\.org/digital/collection/p\d+coll\d+/id/\d+$', source) is not None:
            claim = Claim(self.commons, WikidataProperty.SourceOfFile)
            claim.setTarget(ItemPage(self.wikidata, WikidataEntity.FileAvailableOnInternet))

            qualifier_described_at_url = Claim(self.commons, WikidataProperty.DescribedAtUrl)
            qualifier_described_at_url.setTarget(source)
            claim.addQualifier(qualifier_described_at_url)

            qualifier_operator = Claim(self.commons, WikidataProperty.Operator)
            qualifier_operator.setTarget(ItemPage(self.wikidata, WikidataEntity.USACE))
            claim.addQualifier(qualifier_operator)

            new_claims.append(claim.toJSON())

        if not new_claims:
            pywikibot.info("No claims to set")
            return

        pprint(new_claims)

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
            pywikibot.info(f"Updating {mid} took {(perf_counter() - start):.1f} s")
        except Exception as e:
            pywikibot.critical(f"Failed to update: {e}")


def main():
    UsaceBot().run()


if __name__ == "__main__":
    main()
