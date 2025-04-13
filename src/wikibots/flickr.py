import os
import sys
from typing import Any

from pywikibot import Claim
from pywikibot.pagegenerators import SearchPageGenerator

try:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)) + '/lib')
    from lib.bot import BaseBot
    from lib.wikidata import WikidataEntity, WikidataProperty
except:
    from .lib.bot import BaseBot
    from .lib.wikidata import WikidataEntity, WikidataProperty


class FlickrBot(BaseBot):
    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

        self.generator = SearchPageGenerator(f'file: haswbstatement:{WikidataProperty.Creator} haswbstatement:{WikidataProperty.PublishedIn}={WikidataEntity.Flickr} insource:/Joe Mabel/', site=self.commons)

    def treat_page(self) -> None:
        super().treat_page()
        self.fetch_claims()

        if len(self.existing_claims.get(WikidataProperty.Creator, [])) != 1:
            return

        creator_claim: Claim = self.existing_claims[WikidataProperty.Creator][0]

        if not creator_claim.has_qualifier(WikidataProperty.AuthorName, 'Joe Mabel'):
            return

        edited = False

        if not creator_claim.has_qualifier(WikidataProperty.Url, 'https://commons.wikimedia.org/wiki/User:Jmabel'):
            _q = creator_claim.qualifiers.get(WikidataProperty.Url)
            url_qualifier: Claim = _q[0] if _q else Claim(self.commons, WikidataProperty.Url)
            url_qualifier.setTarget('https://commons.wikimedia.org/wiki/User:Jmabel')

            creator_claim.addQualifier(url_qualifier)
            edited = True

        WikimediaUsername = 'P4174'

        if not creator_claim.has_qualifier(WikimediaUsername, 'Jmabel'):
            _q = creator_claim.qualifiers.get(WikimediaUsername)
            wikimedia_username_qualifier: Claim = _q[0] if _q else Claim(self.commons, WikimediaUsername)
            wikimedia_username_qualifier.setTarget('Jmabel')

            creator_claim.addQualifier(wikimedia_username_qualifier)
            edited = True

        if not edited:
            return

        self.new_claims.append(creator_claim.toJSON())
        self.save('add [[Commons:Structured data|SDC]] based on Flickr metadata. Task #2')


def main() -> None:
    FlickrBot().run()


if __name__ == "__main__":
    main()
