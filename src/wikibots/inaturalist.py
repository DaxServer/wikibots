import logging
import re
import string
from dataclasses import dataclass

from wikibots.lib.bot import BaseBot
from wikibots.lib.claim import Claim
from wikibots.lib.wikidata import WikidataEntity, WikidataProperty

logger = logging.getLogger(__name__)

sparql_taxa_query = string.Template(
    f"""
SELECT DISTINCT ?item
WHERE
{{
    ?item wdt:{WikidataProperty.INaturalistTaxonId} "$taxa".
}}
"""
)

sparql_user_query = string.Template(
    f"""
SELECT DISTINCT ?item
WHERE
{{
    ?item wdt:{WikidataProperty.INaturalistUserId} "$user_id".
}}
"""
)


@dataclass
class User:
    id: str
    name: str | None = None
    orcid: str | None = None


@dataclass
class PhotoData:
    id: str
    observation_id: str
    creator: User | None = None
    depicts: str | None = None


def _extract_orcid_id(orcid_url: str | None) -> str | None:
    """Extract ORCID ID from a URL like https://orcid.org/0000-0000-0000-0000."""
    if not orcid_url:
        return None

    matches = re.search(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", orcid_url)

    return matches.group(1) if matches else None


class INaturalistBot(BaseBot):
    redis_prefix = "ZHXgxFHT4ZBJjR+fLxCH9quuLYl7ky4N6fNV/oC4fbs="
    summary = "add [[Commons:Structured data|SDC]] based on metadata from iNaturalist"
    search_query = (
        f"file: hastemplate:iNaturalist hastemplate:iNaturalistReview"
        f" -haswbstatement:{WikidataProperty.INaturalistPhotoId}"
    )

    def __init__(self) -> None:
        super().__init__()

        # Cache for taxa-Wikidata item mappings
        self.taxa_wikidata_map: dict[int, str] = {}
        self.photo: PhotoData | None = None

    def treat_page(self) -> None:
        # Reset
        self.photo = None

        assert self.wiki_properties

        status = self.retrieve_template_data(
            ["iNaturalistReview", "iNaturalistreview"], ["status"]
        )
        if status is None:
            return

        if status not in ["pass", "pass-change"]:
            logger.warning(f"Skipping as iNaturalistReview status is {status}")
            self.redis.set(self.wiki_properties.redis_key, 1)
            return

        self.fetch_claims()
        self.fetch_observation_data()

        if self.photo is None:
            return

        self.create_id_claim(WikidataProperty.INaturalistPhotoId, self.photo.id)
        self.create_id_claim(
            WikidataProperty.INaturalistObservationId, self.photo.observation_id
        )
        self.create_source_claim(
            f"https://www.inaturalist.org/photos/{self.photo.id}",
            WikidataEntity.iNaturalist,
        )
        self.create_depicts_claim(self.photo.depicts)

        if self.photo.creator:
            self.create_creator_claim(author_name_string=self.photo.creator.name)

        self.save()

    def fetch_observation_data(self) -> None:
        assert self.wiki_properties

        observation_id = self.retrieve_template_data(
            ["iNaturalist", "inaturalist"], ["id", "1"]
        )
        if observation_id is None:
            return

        photo_url = self.retrieve_template_data(
            ["iNaturalistReview", "iNaturalistreview"], ["sourceurl"]
        )
        if photo_url is None:
            return

        matches = re.match(r"https://www.inaturalist.org/photos/(\d+)", photo_url)
        if matches is None:
            logger.warning(f"Skipping as INaturalist URL is invalid {photo_url}")
            self.redis.set(self.wiki_properties.redis_key, 1)
            return

        self.photo = PhotoData(id=matches.group(1), observation_id=observation_id)
        logger.info(f"INaturalist Photo ID: {self.photo.id}")
        logger.info(f"INaturalist Observation ID: {observation_id}")

        try:
            observation = self.session.get(
                f"https://api.inaturalist.org/v1/observations/{observation_id}",
                headers={"Accept": "application/json"},
                timeout=30,
            ).json()["results"][0]
        except Exception as e:
            logger.warning(f"Failed to fetch observation: {e}")
            self.redis.set(self.wiki_properties.redis_key, 1)
            return

        if self.photo.id not in [
            o["photo_id"].__str__() for o in observation["observation_photos"]
        ]:
            logger.warning(
                "Skipping as iNaturalist photo is not attached to observation"
            )
            self.redis.set(self.wiki_properties.redis_key, 1)
            return

        if "user" in observation:
            self.photo.creator = User(
                id=observation["user"]["id"].__str__(),
                name=observation["user"].get("name", None)
                or observation["user"].get("login", None),
                orcid=_extract_orcid_id(observation["user"].get("orcid", None)),
            )

        self.determine_taxa(observation)

    def determine_taxa(self, observation: dict) -> None:
        assert self.photo

        if observation["quality_grade"] != "research":
            logger.warning(
                f"Skipping as observation quality grade is {observation['quality_grade']}"
            )
            return

        if (
            "prefers_community_taxon" in observation["preferences"]
            and observation["preferences"]["prefers_community_taxon"]
        ):
            logger.info("Using community taxon")
            taxa = observation["community_taxon"]
        else:
            taxa = observation["taxon"]

        for taxa_id in reversed(taxa["ancestor_ids"]):
            if taxa_id in self.taxa_wikidata_map:
                logger.info(
                    f"Using cached Wikidata item for taxa https://www.inaturalist.org/taxa/{taxa_id}"
                    f" - {self.taxa_wikidata_map[taxa_id]}"
                )
                self.photo.depicts = self.taxa_wikidata_map[taxa_id]
                break

            logger.info(f"Searching Wikidata for taxon with ID {taxa_id}")
            items = self._sparql_query(sparql_taxa_query.substitute(taxa=taxa_id))

            if len(items) == 0:
                logger.warning(
                    f"No Wikidata items found for taxa https://www.inaturalist.org/taxa/{taxa_id}"
                )
                continue

            if len(items) > 1:
                logger.warning(
                    f"Found multiple Wikidata items for taxa"
                    f" https://www.inaturalist.org/taxa/{taxa_id}: {items}"
                )
                return

            item = items[0]
            logger.info(
                f"Found Wikidata item for taxa https://www.inaturalist.org/taxa/{taxa_id}"
                f" - {item}"
            )

            self.taxa_wikidata_map[taxa_id] = item
            self.photo.depicts = item
            break

    def hook_creator_claim(self, claim: Claim) -> None:
        if not self.photo or not self.photo.creator:
            return
        claim.add_qualifier_string(
            WikidataProperty.INaturalistUserId, self.photo.creator.id
        )
        if self.photo.creator.orcid:
            claim.add_qualifier_string(WikidataProperty.ORCID, self.photo.creator.orcid)

    def hook_creator_target(self, claim: Claim) -> None:
        if not self.photo or not self.photo.creator:
            return
        creator_item = self.find_creator_wikidata_item()
        if creator_item:
            claim.set_entity_target(creator_item)

    def hook_depicts_claim(self, claim: Claim) -> None:
        if not self.photo or not self.photo.depicts:
            return
        claim.add_reference_entity(
            WikidataProperty.StatedIn, WikidataEntity.iNaturalist
        )

    def find_creator_wikidata_item(self) -> str | None:
        """Find Wikidata item for the creator based on iNaturalist user ID."""
        assert self.photo and self.photo.creator

        logger.info(
            f"Searching Wikidata for iNaturalist user with ID {self.photo.creator.id}"
        )
        items = self._sparql_query(
            sparql_user_query.substitute(user_id=self.photo.creator.id)
        )

        if len(items) == 0:
            return None

        if len(items) > 1:
            logger.warning(
                f"Found multiple Wikidata items for iNaturalist user"
                f" https://www.inaturalist.org/people/{self.photo.creator.id}: {items}"
            )
            return None

        item = items[0]
        logger.info(f"Wikidata item for iNaturalist user found - {item}")
        return item


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    INaturalistBot().run()


if __name__ == "__main__":
    main()
