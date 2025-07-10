import json
import os
import sys
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from pprint import pprint
from time import perf_counter
from typing import Any

import mwparserfromhell
import requests
from deepdiff import DeepDiff
from mwparserfromhell.wikicode import Wikicode
from pywikibot import Site, info, critical, Claim, ItemPage, warning, WbTime, Timestamp
from pywikibot.bot import ExistingPageBot
from pywikibot.data.api import Request
from pywikibot.page import BasePage
from pywikibot.page._collections import ClaimCollection
from pywikibot.scripts.wrapper import pwb
from redis import Redis

from wikibots.lib.wikidata import WikidataProperty, WikidataEntity


@dataclass
class WikiProperties:
    mid: str
    redis_key: str
    existing_claims: ClaimCollection
    new_claims: list[Claim] = field(default_factory=list)
    wikicode: Wikicode | None = None
    hash: str | None = None


class BaseBot(ExistingPageBot):
    summary = "add [[Commons:Structured data|SDC]] based on metadata"
    redis_prefix = ""
    throttle = 10

    def __init__(self, **kwargs: Any):
        self.dry_run = "--dry-run" in sys.argv

        super().__init__(**kwargs)

        if (
            os.getenv("PWB_CONSUMER_TOKEN")
            and os.getenv("PWB_CONSUMER_SECRET")
            and os.getenv("PWB_ACCESS_TOKEN")
            and os.getenv("PWB_ACCESS_SECRET")
        ):
            authenticate = (
                os.getenv("PWB_CONSUMER_TOKEN"),
                os.getenv("PWB_CONSUMER_SECRET"),
                os.getenv("PWB_ACCESS_TOKEN"),
                os.getenv("PWB_ACCESS_SECRET"),
            )
            pwb.config.authenticate["commons.wikimedia.org"] = authenticate
        else:
            pwb.config.password_file = "user-password.py"

        pwb.config.put_throttle = self.throttle

        self.wikidata = Site("wikidata", "wikidata")
        self.commons = Site("commons", "commons", user=os.getenv("PWB_USERNAME"))
        self.commons.login()

        if os.getenv("TOOL_REDIS_URI"):
            self.redis = Redis.from_url(os.getenv("TOOL_REDIS_URI", ""), db=9)
        else:
            try:
                self.redis = Redis(db=9)
                self.redis.ping()
            except Exception as e:
                critical(e)
                raise

        self.user_agent = (
            f"{self.commons.username()} / Wikimedia Commons / {os.getenv('EMAIL')}"
        )
        self.wiki_properties: WikiProperties | None = None

        # Initialize a session for HTTP requests
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent})

    def skip_page(self, page: BasePage) -> bool:
        return bool(self.redis.exists(f"{self.redis_prefix}:commons:M{page.pageid}"))

    def treat_page(self) -> None:
        mid = f"M{self.current_page.pageid}"
        self.wiki_properties = WikiProperties(
            mid=mid,
            redis_key=f"{self.redis_prefix}:commons:{mid}",
            existing_claims=ClaimCollection(repo=self.commons),
        )

        info(self.current_page.full_url())
        info(self.wiki_properties.mid)

    def fetch_claims(self) -> None:
        assert self.wiki_properties

        request: Request = self.commons.simple_request(
            action="wbgetentities", ids=self.wiki_properties.mid
        )
        statements = (
            request.submit()
            .get("entities", {})
            .get(self.wiki_properties.mid, {})
            .get("statements", [])
        )

        self.wiki_properties.existing_claims = ClaimCollection.fromJSON(
            data=statements, repo=self.commons
        )

    def parse_wikicode(self) -> None:
        assert self.wiki_properties

        if self.wiki_properties.wikicode is None:
            self.wiki_properties.wikicode = mwparserfromhell.parse(
                self.current_page.text
            )

    def retrieve_template_data(
        self, templates: list[str], parameters: list[str]
    ) -> str | None:
        assert self.wiki_properties
        self.parse_wikicode()
        assert self.wiki_properties.wikicode

        _templates = [
            w
            for w in self.wiki_properties.wikicode.filter_templates()
            if w.name.strip() in templates
        ]
        if not _templates:
            warning(f"No match template found for {templates}")
            self.redis.set(self.wiki_properties.redis_key, 1)
            return None

        t = _templates[0]
        for param in parameters:
            if t.has(param):
                data = str(t.get(param).value).strip()
                info(f"{t.name.strip()} has {param}: {data}")

                return data

        warning(f"{t.name.strip()} template is missing parameters: {parameters}")
        self.redis.set(self.wiki_properties.redis_key, 1)

        return None

    def create_creator_claim(
        self, author_name_string: str | None = None, url: str | None = None
    ) -> None:
        assert self.wiki_properties

        if WikidataProperty.Creator in self.wiki_properties.existing_claims:
            return

        claim = Claim(self.commons, WikidataProperty.Creator)

        with suppress(AssertionError):
            self.hook_creator_target(claim)

        if author_name_string:
            author_qualifier = Claim(self.commons, WikidataProperty.AuthorNameString)
            author_qualifier.setTarget(author_name_string)
            claim.addQualifier(author_qualifier)

        if url:
            url_qualifier = Claim(self.commons, WikidataProperty.Url)
            url_qualifier.setTarget(url)
            claim.addQualifier(url_qualifier)

        with suppress(AssertionError):
            self.hook_creator_claim(claim)

        self.wiki_properties.new_claims.append(claim)

    def create_depicts_claim(self, depicts: ItemPage | None) -> None:
        assert self.wiki_properties

        if (
            WikidataProperty.Depicts in self.wiki_properties.existing_claims
            or depicts is None
        ):
            return

        claim = Claim(self.commons, WikidataProperty.Depicts)
        claim.setTarget(depicts)

        with suppress(AssertionError):
            self.hook_depicts_claim(claim)

        self.wiki_properties.new_claims.append(claim)

    def create_id_claim(self, property: str, value: str) -> None:
        assert self.wiki_properties

        if property in self.wiki_properties.existing_claims:
            return

        claim = Claim(self.commons, property)
        claim.setTarget(value)

        self.wiki_properties.new_claims.append(claim)

    def create_inception_claim(self, wbtime: WbTime, granularity: str) -> None:
        assert self.wiki_properties

        if WikidataProperty.Inception in self.wiki_properties.existing_claims:
            return

        claim = Claim(self.commons, WikidataProperty.Inception)
        claim.setTarget(wbtime)

        if granularity == "circa":
            circa_qualifier = Claim(
                self.commons, WikidataProperty.SourcingCircumstances
            )
            circa_qualifier.setTarget(ItemPage(self.wikidata, WikidataEntity.Circa))
            claim.addQualifier(circa_qualifier)

        self.wiki_properties.new_claims.append(claim)

    def create_published_in_claim(
        self, published_in: str, date_posted: datetime | None = None
    ) -> None:
        assert self.wiki_properties

        if WikidataProperty.PublishedIn in self.wiki_properties.existing_claims:
            return

        claim = Claim(self.commons, WikidataProperty.PublishedIn)
        claim.setTarget(ItemPage(self.wikidata, published_in))

        if date_posted is not None:
            ts = Timestamp.fromISOformat(date_posted.isoformat())

            date_posted_qualifier = Claim(
                self.commons, WikidataProperty.PublicationDate
            )
            date_posted_qualifier.setTarget(
                WbTime(ts.year, ts.month, ts.day, precision=WbTime.PRECISION["day"])
            )
            claim.addQualifier(date_posted_qualifier)

        self.wiki_properties.new_claims.append(claim)

    def create_source_claim(self, source: str, operator: str | None = None) -> None:
        assert self.wiki_properties

        if WikidataProperty.SourceOfFile in self.wiki_properties.existing_claims:
            return

        claim = Claim(self.commons, WikidataProperty.SourceOfFile)
        claim.setTarget(ItemPage(self.wikidata, WikidataEntity.FileAvailableOnInternet))

        described_at_url_qualifier = Claim(
            self.commons, WikidataProperty.DescribedAtUrl
        )
        described_at_url_qualifier.setTarget(source)
        claim.addQualifier(described_at_url_qualifier)

        if operator:
            operator_qualifier = Claim(self.commons, WikidataProperty.Operator)
            operator_qualifier.setTarget(ItemPage(self.wikidata, operator))
            claim.addQualifier(operator_qualifier)

        with suppress(AssertionError):
            self.hook_source_claim(claim)

        self.wiki_properties.new_claims.append(claim)

    def hook_creator_claim(self, claim: Claim) -> None:
        pass

    def hook_creator_target(self, claim: Claim) -> None:
        claim.setSnakType("somevalue")

    def hook_depicts_claim(self, claim: Claim) -> None:
        pass

    def hook_source_claim(self, claim: Claim) -> None:
        pass

    def get_file_hash(self) -> str:
        assert self.wiki_properties

        if self.wiki_properties.hash:
            return self.wiki_properties.hash

        payload = {
            "action": "query",
            "prop": "imageinfo",
            "pageids": self.current_page.pageid,
            "iiprop": "sha1",
            "format": "json",
            "formatversion": "2",
        }

        try:
            start = perf_counter()
            response = self.commons.simple_request(**payload).submit()
            info(f"Queried Wiki file hash in {(perf_counter() - start) * 1000:.1f} ms")
            self.wiki_properties.hash = response["query"]["pages"][0]["imageinfo"][0][
                "sha1"
            ]
        except Exception as e:
            critical(f"Failed to get file hash: {e}")

        return self.wiki_properties.hash or ""

    def save(self) -> None:
        assert self.wiki_properties

        if not self.wiki_properties.new_claims:
            info("No claims to set")
            return

        claims = [claim.toJSON() for claim in self.wiki_properties.new_claims]

        pprint(DeepDiff([], claims))

        if self.dry_run:
            info("Dry run mode: skipping save operation")
            self.quit()

        payload = {
            "action": "wbeditentity",
            "id": self.wiki_properties.mid,
            "data": json.dumps({"claims": claims}),
            "token": self.commons.get_tokens("csrf")["csrf"],
            "summary": self.summary,
            "tags": "BotSDC",
            "bot": True,
        }
        request = self.commons.simple_request(**payload)

        try:
            start = perf_counter()
            request.submit()
            info(
                f"Updating {self.wiki_properties.mid} took {(perf_counter() - start):.1f} s"
            )
        except Exception as e:
            critical(f"Failed to update: {e}")

    def teardown(self) -> None:
        """Close the session and any other resources."""
        self.session.close()
