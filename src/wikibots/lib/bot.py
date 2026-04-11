import json
import logging
import os
import sys
import time
from pprint import pprint
from time import perf_counter
from typing import Any, Iterator

import mwparserfromhell
import requests
from deepdiff import DeepDiff
from redis import Redis
from requests_oauthlib import OAuth1

from wikibots.lib.claim import WikiProperties
from wikibots.lib.claims import ClaimsMixin

logger = logging.getLogger(__name__)

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"


class DryRunStop(Exception):
    pass


class RateLimitExhausted(Exception):
    pass


class BaseBot(ClaimsMixin):
    summary = "add [[Commons:Structured data|SDC]] based on metadata"
    redis_prefix = ""
    throttle = 5
    search_query = ""
    always_null_edit = False

    def __init__(self) -> None:
        self.dry_run = "--dry-run" in sys.argv
        self.current_page: dict[str, Any] = {}
        self.wiki_properties: WikiProperties | None = None

        self._username = os.getenv("PWB_USERNAME", "")
        self.user_agent = f"{self._username} / Wikimedia Commons / {os.getenv('EMAIL')}"

        auth = OAuth1(
            client_key=os.getenv("PWB_CONSUMER_TOKEN", ""),
            client_secret=os.getenv("PWB_CONSUMER_SECRET", ""),
            resource_owner_key=os.getenv("PWB_ACCESS_TOKEN", ""),
            resource_owner_secret=os.getenv("PWB_ACCESS_SECRET", ""),
        )
        self._commons_session = requests.Session()
        self._commons_session.auth = auth
        self._commons_session.headers.update({"User-Agent": self.user_agent})

        _redis_uri = os.getenv("TOOL_REDIS_URI")
        if _redis_uri:
            self.redis = Redis.from_url(_redis_uri, db=9)
        else:
            try:
                self.redis = Redis(db=9)
                self.redis.ping()
            except Exception as e:
                logger.critical(str(e))
                raise

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent})

    def _commons_api(
        self,
        params: dict[str, Any],
        method: str = "GET",
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make a request to the Commons API."""
        params.setdefault("format", "json")
        response = self._commons_session.request(
            method, COMMONS_API, params=params, data=data, timeout=60.0
        )
        response.raise_for_status()
        return response.json()

    def _get_csrf_token(self) -> str:
        result = self._commons_api(
            {"action": "query", "meta": "tokens", "type": "csrf"}
        )
        return result["query"]["tokens"]["csrftoken"]

    def _search_pages(self) -> Iterator[dict[str, Any]]:
        """Yield file pages matching the search query with pagination."""
        params: dict[str, Any] = {
            "action": "query",
            "list": "search",
            "srsearch": self.search_query,
            "srnamespace": 6,
            "srlimit": 50,
            "srinfo": "",
            "srprop": "",
        }
        while True:
            result = self._commons_api(params)
            for page in result.get("query", {}).get("search", []):
                yield page
            if "continue" not in result:
                break
            params.update(result["continue"])

    def _sparql_query(self, query: str) -> list[str]:
        """Execute a SPARQL query on Wikidata, return list of entity IDs."""
        response = self.session.get(
            WIKIDATA_SPARQL,
            params={"query": query, "format": "json"},
            timeout=30,
        )
        response.raise_for_status()
        return [
            r["item"]["value"].split("/")[-1]
            for r in response.json()["results"]["bindings"]
        ]

    def run(self) -> None:
        try:
            for page in self._search_pages():
                mid = f"M{page['pageid']}"
                if self.redis.exists(f"{self.redis_prefix}:commons:{mid}"):
                    continue

                if self.skip_page(page):
                    continue

                self.current_page = page
                self.wiki_properties = WikiProperties(
                    mid=mid,
                    redis_key=f"{self.redis_prefix}:commons:{mid}",
                    existing_claims={},
                )

                logger.info(f"https://commons.wikimedia.org/wiki/{page['title']}")
                logger.info(mid)

                self.treat_page()
                time.sleep(self.throttle)
        except (DryRunStop, RateLimitExhausted):
            pass

    def skip_page(self, page: dict[str, Any]) -> bool:
        return False

    def treat_page(self) -> None:
        pass

    def fetch_claims(self) -> None:
        assert self.wiki_properties
        result = self._commons_api(
            {"action": "wbgetentities", "ids": self.wiki_properties.mid}
        )
        entity = result.get("entities", {}).get(self.wiki_properties.mid, {})
        self.wiki_properties.existing_claims = entity.get("statements", {})

    def parse_wikicode(self) -> None:
        assert self.wiki_properties
        if self.wiki_properties.wikicode is not None:
            return
        result = self._commons_api(
            {
                "action": "query",
                "pageids": self.current_page["pageid"],
                "prop": "revisions",
                "rvprop": "content",
                "rvslots": "*",
                "formatversion": 2,
            }
        )
        content = result["query"]["pages"][0]["revisions"][0]["slots"]["main"][
            "content"
        ]
        self.wiki_properties.wikicode = mwparserfromhell.parse(content)

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
            logger.warning(f"No match template found for {templates}")
            self.redis.set(self.wiki_properties.redis_key, 1)
            return None

        t = _templates[0]
        for param in parameters:
            if t.has(param):
                data = str(t.get(param).value).strip()
                logger.info(f"{t.name.strip()} has {param}: {data}")
                return data

        logger.warning(f"{t.name.strip()} template is missing parameters: {parameters}")
        self.redis.set(self.wiki_properties.redis_key, 1)
        return None

    def get_file_metadata(self) -> None:
        assert self.current_page
        assert self.wiki_properties
        try:
            start = perf_counter()
            result = self._commons_api(
                {
                    "action": "query",
                    "pageids": self.current_page["pageid"],
                    "prop": "imageinfo",
                    "iiprop": "metadata|size|sha1|mime",
                    "formatversion": 2,
                }
            )
            logger.info(
                f"Queried Wiki file metadata in {(perf_counter() - start) * 1000:.1f} ms"
            )
            imageinfo = result["query"]["pages"][0]["imageinfo"][0]
            self.wiki_properties.metadata = {
                m["name"]: m["value"] for m in imageinfo.get("metadata") or []
            }
            self.wiki_properties.size = imageinfo["size"]
            self.wiki_properties.width = imageinfo["width"]
            self.wiki_properties.height = imageinfo["height"]
            self.wiki_properties.mime = imageinfo["mime"]
            self.wiki_properties.sha1 = imageinfo["sha1"]
        except Exception as e:
            logger.critical(f"Failed to get file metadata: {e}")

    def null_edit(self) -> None:
        title = self.current_page["title"]
        result = self._commons_api(
            {
                "action": "query",
                "prop": "revisions",
                "rvprop": "content",
                "rvlimit": 1,
                "rvslots": "*",
                "titles": title,
                "formatversion": 2,
            }
        )
        content = result["query"]["pages"][0]["revisions"][0]["slots"]["main"][
            "content"
        ]
        self._commons_session.post(
            COMMONS_API,
            data={
                "action": "edit",
                "title": title,
                "text": content,
                "summary": "null edit",
                "format": "json",
                "token": self._get_csrf_token(),
            },
            timeout=60.0,
        )

    def save(self) -> None:
        assert self.wiki_properties

        if not self.wiki_properties.new_claims:
            logger.info("No claims to set")

            if self.dry_run:
                logger.info("Dry run mode: skipping save operation")
                raise DryRunStop()

            if self.always_null_edit:
                logger.info("Performing null edit to flush any tracker categories")
                self.null_edit()

            self.redis.set(self.wiki_properties.redis_key, 1)
            return

        claims_data = [c.to_dict() for c in self.wiki_properties.new_claims]
        pprint(DeepDiff([], claims_data))

        if self.dry_run:
            logger.info("Dry run mode: skipping save operation")
            raise DryRunStop()

        try:
            start = perf_counter()
            response = self._commons_session.post(
                COMMONS_API,
                data={
                    "action": "wbeditentity",
                    "id": self.wiki_properties.mid,
                    "data": json.dumps({"claims": claims_data}),
                    "summary": self.summary,
                    "tags": "BotSDC",
                    "bot": "1",
                    "format": "json",
                    "token": self._get_csrf_token(),
                },
                timeout=60.0,
            )
            response.raise_for_status()
            result = response.json()
            if "error" in result:
                logger.critical(
                    f"API error saving {self.wiki_properties.mid}: {result['error']}"
                )
                return
            logger.info(
                f"Updating {self.wiki_properties.mid} took {(perf_counter() - start):.1f} s"
            )
            self.redis.set(self.wiki_properties.redis_key, 1)
            self.null_edit()
        except Exception as e:
            logger.critical(f"Failed to update: {e}")

    def teardown(self) -> None:
        """Close HTTP sessions."""
        self.session.close()
        self._commons_session.close()
