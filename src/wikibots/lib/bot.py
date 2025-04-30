import json
import os
from contextlib import suppress
from pprint import pprint
from time import perf_counter
from typing import Any

import mwparserfromhell
from deepdiff import DeepDiff
from pywikibot import Site, info, critical, Claim, ItemPage, warning
from pywikibot.bot import ExistingPageBot
from pywikibot.data.api import Request
from pywikibot.page import BasePage
from pywikibot.page._collections import ClaimCollection
from pywikibot.scripts.wrapper import pwb
from redis import Redis

try:
    from wikidata import WikidataEntity, WikidataProperty
except ImportError:
    from wikibots.lib.wikidata import WikidataEntity, WikidataProperty


class BaseBot(ExistingPageBot):
    summary = 'add [[Commons:Structured data|SDC]] based on metadata'
    redis_prefix = ''
    main_redis_key = ''
    throttle = 10

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

        pwb.config.put_throttle = self.throttle

        self.wikidata = Site("wikidata", "wikidata")
        self.commons = Site("commons", "commons", user=os.getenv("PWB_USERNAME"))
        self.commons.login()

        self.redis = Redis(host='redis.svc.tools.eqiad1.wikimedia.cloud', db=9)

        self.user_agent = f"{self.commons.username()} / Wikimedia Commons / {os.getenv("EMAIL")}"
        self.mid = ''
        self.new_claims: list[dict] = []
        self.existing_claims: ClaimCollection = ClaimCollection(repo=self.commons)

    def skip_page(self, page: BasePage) -> bool:
        return self.redis.exists(f'{self.redis_prefix}:commons:M{page.pageid}')

    def treat_page(self) -> None:
        self.mid = f'M{self.current_page.pageid}'
        info(self.current_page.full_url())
        info(self.mid)

        self.main_redis_key = f'{self.redis_prefix}:commons:{self.mid}'

    def fetch_claims(self) -> None:
        request: Request = self.commons.simple_request(action="wbgetentities", ids=self.mid)
        statements = request.submit().get('entities').get(self.mid).get('statements', [])

        self.new_claims = []
        self.existing_claims = ClaimCollection.fromJSON(data=statements, repo=self.commons)

    def retrieve_template_data(self, templates: list[str], parameters: list[str]) -> str | None:
        wikitext = mwparserfromhell.parse(self.current_page.text)

        inaturalist_templates = [w for w in wikitext.filter_templates() if w.name.strip() in templates]
        if not inaturalist_templates:
            warning(f'No match template found for {templates}')
            self.redis.set(self.main_redis_key, 1)
            return None

        t = inaturalist_templates[0]
        for param in parameters:
            if t.has(param):
                data = str(t.get(param).value).strip()
                info(f'{t.name} has {param} : {data}')

                return data

        warning(f'{t.name} template is missing parameters: {parameters}')
        self.redis.set(self.main_redis_key, 1)

        return None

    def create_id_claim(self, property: str, value: str) -> None:
        if property in self.existing_claims:
            return

        claim = Claim(self.commons, property)
        claim.setTarget(value)

        self.new_claims.append(claim.toJSON())

    def create_creator_claim_tmp(self, author_name_string: str | None = None, url: str | None = None) -> None:
        if WikidataProperty.Creator in self.existing_claims:
            return

        claim = Claim(self.commons, WikidataProperty.Creator)
        claim.setSnakType('somevalue')

        if author_name_string is not None:
            author_qualifier = Claim(self.commons, WikidataProperty.AuthorNameString)
            author_qualifier.setTarget(author_name_string)
            claim.addQualifier(author_qualifier)

        if url is not None:
            url_qualifier = Claim(self.commons, WikidataProperty.Url)
            url_qualifier.setTarget(url)
            claim.addQualifier(url_qualifier)

        with suppress(AssertionError):
            self.hook_creator_claim(claim)

        self.new_claims.append(claim.toJSON())

    def hook_creator_claim(self, claim: Claim) -> None:
        pass

    def create_source_claim(self, source: str, operator: str) -> None:
        if WikidataProperty.SourceOfFile in self.existing_claims:
            return

        claim = Claim(self.commons, WikidataProperty.SourceOfFile)
        claim.setTarget(ItemPage(self.wikidata, WikidataEntity.FileAvailableOnInternet))

        described_at_url_qualifier = Claim(self.commons, WikidataProperty.DescribedAtUrl)
        described_at_url_qualifier.setTarget(source)
        claim.addQualifier(described_at_url_qualifier)

        operator_qualifier = Claim(self.commons, WikidataProperty.Operator)
        operator_qualifier.setTarget(ItemPage(self.wikidata, operator))
        claim.addQualifier(operator_qualifier)

        self.new_claims.append(claim.toJSON())

    def create_depicts_claim(self, depicts: ItemPage | None) -> None:
        if WikidataProperty.Depicts in self.existing_claims or depicts is None:
            return

        claim = Claim(self.commons, WikidataProperty.Depicts)
        claim.setTarget(depicts)

        with suppress(AssertionError):
            self.hook_depicts_claim(claim)

        self.new_claims.append(claim.toJSON())

    def hook_depicts_claim(self, claim: Claim) -> None:
        pass

    def save(self) -> None:
        if not self.new_claims:
            info("No claims to set")
            return

        payload = {
            "action": "wbeditentity",
            "id": self.mid,
            "data": json.dumps({"claims": self.new_claims}),
            "token": self.commons.get_tokens("csrf")['csrf'],
            "summary": self.summary,
            "tags": "BotSDC",
            "bot": True,
        }
        request = self.commons.simple_request(**payload)

        pprint(DeepDiff([], self.new_claims))

        try:
            start = perf_counter()
            request.submit()
            info(f"Updating {self.mid} took {(perf_counter() - start):.1f} s")
        except Exception as e:
            critical(f"Failed to update: {e}")

        self.mid = ''
        self.new_claims = []
