import logging
from typing import Any

from wikibots.lib.bot import BaseBot

logger = logging.getLogger(__name__)

FLICKRREVIEW_TEMPLATE = "{{Flickrreview}}\n"


class FlickreviewrBot(BaseBot):
    redis_prefix = "flickrreview"
    summary = (
        "add [[Template:Flickrreview|Flickrreview]] template to Flickypedia uploads. test run."
    )
    search_query = (
        'file: incategory:"Uploads using Flickypedia" -hastemplate:Flickrreview'
    )

    def skip_page(self, page: dict[str, Any]) -> bool:
        mid = f"M{page['pageid']}"
        redis_key = f"{self.redis_prefix}:commons:{mid}"

        if self.has_template(page, "FlickreviewR"):
            logger.info(f"Skipping: FlickreviewR already present on {page['title']}")
            self.redis.set(redis_key, 1)
            return True

        if self.has_user_edited(page):
            logger.info(f"Skipping: bot has already edited {page['title']}")
            self.redis.set(redis_key, 1)
            return True

        return False

    def treat_page(self) -> None:
        assert self.wiki_properties

        self.parse_wikicode()
        assert self.wiki_properties.wikicode

        wikicode = self.wiki_properties.wikicode
        templates = wikicode.filter_templates()

        flickypedia = next(
            (t for t in templates if t.name.strip() == "Uploaded with Flickypedia"),
            None,
        )
        if flickypedia is None:
            logger.warning(
                f"No Flickypedia template found on {self.current_page['title']}"
            )
            self.redis.set(self.wiki_properties.redis_key, 1)
            return

        wikicode.insert_before(flickypedia, FLICKRREVIEW_TEMPLATE)

        new_text = str(wikicode).replace("\n\n{{Flickrreview}}", "\n{{Flickrreview}}")

        if self.dry_run:
            logger.info(f"Dry run: would edit {self.current_page['title']}")
            logger.info(new_text)
            return

        token = self._get_csrf_token()
        response = self._commons_session.post(
            "https://commons.wikimedia.org/w/api.php",
            data={
                "action": "edit",
                "pageid": self.current_page["pageid"],
                "text": new_text,
                "summary": self.summary,
                "bot": "1",
                "format": "json",
                "token": token,
            },
            timeout=60.0,
        )
        response.raise_for_status()
        result = response.json()
        if "error" in result:
            logger.critical(
                f"API error editing {self.current_page['title']}: {result['error']}"
            )
            return

        logger.info(f"Added Flickrreview to {self.current_page['title']}")
        self.redis.set(self.wiki_properties.redis_key, 1)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    FlickreviewrBot().run()


if __name__ == "__main__":
    main()
