import hashlib
import re
from datetime import datetime
from time import perf_counter
from typing import Any

from mwparserfromhell.nodes import ExternalLink
from pywikibot import warning, info
from pywikibot.pagegenerators import SearchPageGenerator

from wikibots.lib.bot import BaseBot
from wikibots.lib.wikidata import WikidataProperty, WikidataEntity


class PortableAntiquitiesSchemeBot(BaseBot):
    redis_prefix = "pas"
    summary = "add [[Commons:Structured data|SDC]] based on metadata from Portable Antiquities Scheme Database"

    res = [
        r"https?://finds\.org\.uk/database/ajax/download/id/(\d+)/?",
        r"https?://finds\.org\.uk/database/images/image/id/(\d+)/recordtype/artefacts/?",
    ]

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

        self.generator = SearchPageGenerator(
            f'file: incategory:"Portable Antiquities Scheme" -haswbstatement:{WikidataProperty.PortableAntiquitiesSchemeImageID}',
            site=self.commons,
        )

        self.image_id = set()

    def treat_page(self) -> None:
        super().treat_page()
        assert self.wiki_properties

        self.image_id.clear()
        self.parse_wikicode()

        assert self.wiki_properties.wikicode

        links: list[ExternalLink] = (
            self.wiki_properties.wikicode.filter_external_links()
        )

        for link in links:
            self.find_matches(link.url.strip_code().strip())

        if len(self.image_id) != 1:
            warning(f"Invalid number of image IDs found: {self.image_id}")
            self.redis.set(self.wiki_properties.redis_key, 1)
            return

        image_id = self.image_id.pop()
        info(f"Image ID: {image_id}")

        start = perf_counter()
        try:
            image_json = self.session.get(
                f"https://finds.org.uk/database/images/image/id/{image_id}/recordtype/artefacts/format/json",
                timeout=30,
            ).json()["image"][0]
        except Exception as e:
            warning(f"Failed to fetch image: {e}")
            self.redis.set(self.wiki_properties.redis_key, 1)
            return

        if image_json["id"] != image_id:
            warning(f'Invalid image ID found: {image_id} != {image_json["id"]}')
            self.redis.set(self.wiki_properties.redis_key, 1)
            return

        info(f"Fetched image record in {(perf_counter() - start) * 1000:.0f} ms")

        start = perf_counter()
        try:
            sha1_hash = hashlib.sha1()
            with self.session.get(
                f"https://finds.org.uk/database/ajax/download/id/{image_id}",
                stream=True,
                timeout=30,
            ) as image:
                for data in image.iter_content(chunk_size=1024):
                    sha1_hash.update(data)
            image_hash = sha1_hash.hexdigest()
            info(f"Calculated hash in {(perf_counter() - start):.1f} s")
        except Exception as e:
            warning(f"Failed to fetch image: {e}")
            self.redis.set(self.wiki_properties.redis_key, 1)
            return

        if image_hash != self.get_file_hash():
            warning(f"Invalid image hash found: {image_hash} != {self.get_file_hash()}")
            self.redis.set(self.wiki_properties.redis_key, 1)
            return

        self.create_id_claim(
            WikidataProperty.PortableAntiquitiesSchemeImageID, image_id
        )

        # Add Published in claim with date qualifier if available
        date_obj = None

        if "created" in image_json and image_json["created"]:
            try:
                date_obj = datetime.strptime(image_json["created"], "%Y-%m-%d %H:%M:%S")
            except Exception as e:
                warning(f"Failed to parse created date: {e}")
        else:
            warning("No created field found in API response")

        self.create_published_in_claim(
            WikidataEntity.PortableAntiquitiesSchemeDatabase, date_obj
        )

        self.save()

    def find_matches(self, url: str) -> None:
        info(f"Testing URL: {url}")

        for r in self.res:
            matches = re.match(r, url)

            if matches:
                self.image_id.add(matches.group(1))
                return


def main():
    PortableAntiquitiesSchemeBot().run()


if __name__ == "__main__":
    main()
