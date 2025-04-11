import json
import os
from pprint import pprint
from time import perf_counter
from typing import Any

from deepdiff import DeepDiff
from pywikibot import Site, info, critical, Claim, ItemPage
from pywikibot.bot import ExistingPageBot
from pywikibot.data.api import Request
from pywikibot.page._collections import ClaimCollection
from pywikibot.scripts.wrapper import pwb

try:
    from wikidata import WikidataEntity, WikidataProperty
except:
    from wikibots.lib.wikidata import WikidataProperty


class BaseBot(ExistingPageBot):
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
        self.commons = Site("commons", "commons", user=os.getenv("PWB_USERNAME"))
        self.commons.login()

        self.user_agent = f"{self.commons.username()} / Wikimedia Commons"

        self.mid: str = ''
        self.existing_claims: ClaimCollection = ClaimCollection(repo=self.commons)
        self.new_claims: list[dict] = []

    def treat_page(self) -> None:
        self.mid = f'M{self.current_page.pageid}'
        info(self.current_page.full_url())
        info(self.mid)

    def fetch_claims(self) -> None:
        request: Request = self.commons.simple_request(action="wbgetentities", ids=self.mid)
        statements = request.submit().get('entities').get(self.mid).get('statements', [])

        self.new_claims = []
        self.existing_claims = ClaimCollection.fromJSON(data=statements, repo=self.commons)

    def create_source_claim(self, source: str, operator: str) -> None:
        claim = Claim(self.commons, WikidataProperty.SourceOfFile)
        claim.setTarget(ItemPage(self.wikidata, WikidataEntity.FileAvailableOnInternet))

        described_at_url_qualifier = Claim(self.commons, WikidataProperty.DescribedAtUrl)
        described_at_url_qualifier.setTarget(source)
        claim.addQualifier(described_at_url_qualifier)

        operator_qualifier = Claim(self.commons, WikidataProperty.Operator)
        operator_qualifier.setTarget(ItemPage(self.wikidata, operator))
        claim.addQualifier(operator_qualifier)

        self.new_claims.append(claim.toJSON())

    def save(self, summary: str) -> None:
        if not self.new_claims:
            info("No claims to set")
            return

        mid = f'M{self.current_page.pageid}'

        payload = {
            "action": "wbeditentity",
            "id": mid,
            "data": json.dumps({"claims": self.new_claims}),
            "token": self.commons.get_tokens("csrf")['csrf'],
            "summary": summary,
            "tags": "BotSDC",
            "bot": True,
        }
        request = self.commons.simple_request(**payload)

        pprint(DeepDiff([], self.new_claims))

        try:
            start = perf_counter()
            request.submit()
            info(f"Updating {mid} took {(perf_counter() - start):.1f} s")
        except Exception as e:
            critical(f"Failed to update: {e}")

        self.new_claims = []
