import json
import os
import re
import sys
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

try:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)) + '/lib')
    from lib.bot import BaseBot
    from lib.wikidata import WikidataEntity, WikidataProperty
except:
    from .lib.bot import BaseBot
    from .lib.wikidata import WikidataEntity, WikidataProperty


def parse_date(date: str) -> re.Match:
    return re.match(r'^(\d{4}(-\d{2}(-\d{2})?)?)$', date)


class UsaceBot(BaseBot):
    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

        self.generator = SearchPageGenerator(f'file: deepcat:"Images from USACE" -haswbstatement:{WikidataProperty.SourceOfFile}', site=self.commons)

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

        self.new_claims = []
        self.existing_claims = ClaimCollection.fromJSON(
            data=self.commons.simple_request(action="wbgetentities", ids=mid).submit()['entities'][mid]['statements'],
            repo=self.commons
        )

        if date is not None:
            self.process_inception_claim(date)

        self.create_source_claim(source)

        self.save('add [[Commons:Structured data|SDC]] based on metadata. Task #3')

    def process_inception_claim(self, date: str) -> None:
        if WikidataProperty.Inception in self.existing_claims:
            return

        if (date_matches := parse_date(date)) is not None:
            self.create_inception_claim(date, date_matches)
            return

        wikitext = mwparserfromhell.parse(date)
        complex_date = [t for t in wikitext.filter_templates() if t.name.matches("complex date")]

        if len(complex_date) != 1 or len(complex_date[0].params) != 2:
            return

        param0 = complex_date[0].params[0].value.get(0).value
        param1 = complex_date[0].params[1].value.get(0).value

        if param0 == 'ca' and (date_matches := parse_date(param1)) is not None:
            circa_qualifier = Claim(self.commons, WikidataProperty.SourcingCircumstances)
            circa_qualifier.setTarget(ItemPage(self.wikidata, WikidataEntity.Circa))

            self.create_inception_claim(param1, date_matches, [circa_qualifier])
            return

    def create_inception_claim(self, date: str, date_matches: re.Match, qualifiers: list[Claim] = ()) -> None:
        pprint(date_matches.groups())

        ts = Timestamp.fromisoformat(parser.isoparse(date).isoformat())
        precision = 'day' if date_matches.group(3) else 'month' if date_matches.group(2) else 'year'
        wb_ts = WbTime.fromTimestamp(ts, precision)

        pprint(wb_ts)

        claim = Claim(self.commons, WikidataProperty.Inception)
        claim.setTarget(wb_ts)

        for qualifier in qualifiers:
            claim.addQualifier(qualifier)

        self.new_claims.append(claim.toJSON())

    def create_source_claim(self, source: str) -> None:
        if WikidataProperty.SourceOfFile in self.existing_claims:
            return

        if re.match(r'^https://usace\.contentdm\.oclc\.org/digital/collection/p\d+coll\d+/id/\d+$', source) is None:
            return

        super().create_source_claim(source, WikidataEntity.USACE)


def main():
    UsaceBot().run()


if __name__ == "__main__":
    main()
