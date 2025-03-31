import json
import os
import re
from pprint import pprint
from time import perf_counter
from typing import Any

import pywikibot
from dateutil import parser
from deepdiff import DeepDiff
from flickypedia.apis import WikimediaApi
from flickypedia.backfillr.actions import create_actions
from flickypedia.structured_data import WikidataProperties
from flickypedia.structured_data.statements import create_source_statement
from httpx import Client
from pywikibot import Site, textlib, Claim
from pywikibot.bot import ExistingPageBot
from pywikibot.pagegenerators import SearchPageGenerator


class WDEntities:
    USACE = 'Q1049334'


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

        self.site = Site("commons", "commons", user=os.getenv("PWB_USERNAME") or "CuratorBot")
        self.site.login()

        self.generator = SearchPageGenerator('deepcat:"Images from USACE" -haswbstatement:P571', site=self.site)

        self.user_agent = f"{self.site.username()} / Wikimedia Commons"
        self.http_client = Client(headers={"User-Agent": self.user_agent})
        self.wikimedia_api = WikimediaApi(client=self.http_client)

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

        existing_claims = self.wikimedia_api.get_structured_data(mid=mid)

        statements = []

        if (date_matches := re.match(r'^(\d{4}(-\d{2}(-\d{2})?)?)$', date or '')) is not None:
            pprint(date_matches.groups())

            precision = 'day' if date_matches.group(3) else 'month' if date_matches.group(2) else 'year'

            ts = pywikibot.Timestamp.fromisoformat(parser.isoparse(date).isoformat())
            wb_ts = pywikibot.WbTime.fromTimestamp(ts, precision)

            pprint(wb_ts)

            claim = Claim(self.site, WikidataProperties.Inception)
            claim.setTarget(wb_ts)

            statements.append(claim.toJSON())

        if re.match(r'^https://usace\.contentdm\.oclc\.org/digital/collection/p\d+coll\d+/id/\d+$', source) is not None:
            source_statement = create_source_statement(
                described_at_url=source,
                operator=WDEntities.USACE,
                original_url=None,
                retrieved_at=None,
            )

            statements.append(source_statement)

        new_claims = {"claims": statements}

        actions = create_actions(existing_claims, new_claims, None)

        claims = []

        for a in actions:
            if a["action"] == "unknown" or a["action"] == "do_nothing":
                continue
            elif a["action"] == "add_missing":
                claims.append(a["statement"])
            elif a["action"] == "add_qualifiers" or a["action"] == "replace_statement":
                statement = a["statement"]
                statement["id"] = a["statement_id"]
                claims.append(statement)
            elif a["action"] == "remove_statement":
                claims.append({
                    "id": a["statement_id"],
                    "remove": "",
                })
            else:
                raise ValueError(f"Unrecognised action: {a['action']}")

        if not claims:
            pywikibot.info("No claims to set")
            return

        # pprint(claims)

        payload = {
            "action": "wbeditentity",
            "id": mid,
            "data": json.dumps({"claims": claims}),
            "token": self.site.get_tokens("csrf")['csrf'],
            "summary": "add [[Commons:Structured data|SDC]] based on metadata. Task #3",
            "tags": "BotSDC",
            "bot": True,
        }
        request = self.site.simple_request(**payload)

        pprint(DeepDiff([], claims))

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
