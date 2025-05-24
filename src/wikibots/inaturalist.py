import re
import string
from dataclasses import dataclass
from typing import Any

from pywikibot import warning, info, Claim, ItemPage
from pywikibot.page import BasePage
from pywikibot.pagegenerators import SearchPageGenerator, WikidataSPARQLPageGenerator

from wikibots.lib.bot import BaseBot
from wikibots.lib.wikidata import WikidataProperty, WikidataEntity

sparql_taxa_query = string.Template(f'''
SELECT DISTINCT ?item
WHERE
{{
    ?item wdt:{WikidataProperty.INaturalistTaxonId} "$taxa".
}}
''')

sparql_user_query = string.Template(f'''
SELECT DISTINCT ?item
WHERE
{{
    ?item wdt:{WikidataProperty.INaturalistUserId} "$user_id".
}}
''')


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
    depicts: ItemPage | None = None


def _extract_orcid_id(orcid_url: str | None) -> str | None:
    """Extract ORCID ID from URL.

    Args:
        orcid_url: ORCID URL or None

    Returns:
        ORCID ID or None
    """
    if not orcid_url:
        return None

    # Extract ORCID ID from URL (e.g., https://orcid.org/0000-0000-0000-0000)
    # Using a simpler regex pattern that matches the ORCID format
    # WikidataProperty.ORCID is P496
    matches = re.search(r'(\d{4}-\d{4}-\d{4}-\d{3}[\dX])', orcid_url)

    return matches.group(1) if matches else None


class INaturalistBot(BaseBot):
    redis_prefix = 'ZHXgxFHT4ZBJjR+fLxCH9quuLYl7ky4N6fNV/oC4fbs='
    summary = 'add [[Commons:Structured data|SDC]] based on metadata from iNaturalist'
    # Cache for taxa-Wikidata item mappings
    taxa_wikidata_map = {}

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

        self.generator = SearchPageGenerator(
            f'file: hastemplate:iNaturalist hastemplate:iNaturalistReview -haswbstatement:{WikidataProperty.INaturalistPhotoId}',
            site=self.commons)

        self.inaturalist_wd = ItemPage(self.wikidata, WikidataEntity.iNaturalist)
        self.photo: PhotoData | None = None

    def skip_page(self, page: BasePage) -> bool:
        redis_key = f'{self.redis_prefix}:commons:M{page.pageid}'

        return self.redis.exists(redis_key) or self.commons.username() in page.contributors()

    def treat_page(self) -> None:
        # Reset
        self.photo = None

        super().treat_page()

        status = self.retrieve_template_data(['iNaturalistReview', 'iNaturalistreview'], ['status'])
        if status is None:
            return

        if status not in ['pass', 'pass-change']:
            warning(f'Skipping as iNaturalistReview status is {status}')
            self.redis.set(self.wiki_properties.redis_key, 1)
            return

        self.fetch_claims()
        self.fetch_observation_data()

        if self.photo is None:
            return

        self.create_id_claim(WikidataProperty.INaturalistPhotoId, self.photo.id)
        self.create_id_claim(WikidataProperty.INaturalistObservationId, self.photo.observation_id)
        self.create_source_claim(f'https://www.inaturalist.org/photos/{self.photo.id}', WikidataEntity.iNaturalist)
        self.create_depicts_claim(self.photo.depicts)

        if self.photo.creator:
            self.create_creator_claim(author_name_string=self.photo.creator.name)

        self.save()

    def fetch_observation_data(self) -> None:
        assert self.wiki_properties

        observation_id = self.retrieve_template_data(['iNaturalist', 'inaturalist'], ['id', '1'])
        if observation_id is None:
            return

        photo_url = self.retrieve_template_data(['iNaturalistReview', 'iNaturalistreview'], ['sourceurl'])
        if photo_url is None:
            return

        matches = re.match(r'https://www.inaturalist.org/photos/(\d+)', photo_url)
        if matches is None:
            warning(f'Skipping as INaturalist URL is invalid {photo_url}')
            self.redis.set(self.wiki_properties.redis_key, 1)
            return

        self.photo = PhotoData(id=matches.group(1), observation_id=observation_id)
        info(f'INaturalist Photo ID: {self.photo.id}')
        info(f'INaturalist Observation ID: {observation_id}')

        try:
            observation = self.session.get(f'https://api.inaturalist.org/v1/observations/{observation_id}', headers={
                'Accept': 'application/json',
            }, timeout=30).json()['results'][0]
        except Exception as e:
            warning(f'Failed to fetch observation: {e}')
            self.redis.set(self.wiki_properties.redis_key, 1)
            return

        if self.photo.id not in [o['photo_id'].__str__() for o in observation['observation_photos']]:
            warning('Skipping as iNaturalist photo is not attached to observation')
            self.redis.set(self.wiki_properties.redis_key, 1)
            return

        if 'user' in observation:
            self.photo.creator = User(
                id=observation['user']['id'].__str__(),
                name=observation['user'].get('name', None) or observation['user'].get('login', None),
                orcid=_extract_orcid_id(observation['user'].get('orcid', None)),
            )

        self.determine_taxa(observation)

    def determine_taxa(self, observation: dict) -> None:
        assert self.photo

        if observation['quality_grade'] != 'research':
            warning(f'Skipping as observation quality grade is {observation["quality_grade"]}')
            return

        if 'prefers_community_taxon' in observation['preferences'] and observation['preferences'][
            'prefers_community_taxon']:
            info('Using community taxon')
            taxa = observation['community_taxon']
        else:
            taxa = observation['taxon']

        for taxa_id in reversed(taxa['ancestor_ids']):
            # Check if taxa_id is already in the cache
            if taxa_id in self.__class__.taxa_wikidata_map:
                info(f'Using cached Wikidata item for taxa https://www.inaturalist.org/taxa/{taxa_id} - {self.__class__.taxa_wikidata_map[taxa_id].getID()}')
                self.photo.depicts = self.__class__.taxa_wikidata_map[taxa_id]
                break

            info(f'Searching Wikidata for taxon with ID {taxa_id}')
            gen = WikidataSPARQLPageGenerator(sparql_taxa_query.substitute(taxa=taxa_id), site=self.wikidata)
            items = list(gen)

            if len(items) == 0:
                warning(f'No Wikidata items found for taxa https://www.inaturalist.org/taxa/{taxa_id}')
                continue

            if len(items) > 1:
                warning(f'Found multiple Wikidata items for taxa https://www.inaturalist.org/taxa/{taxa_id}: {items}')
                return

            info(f'Found Wikidata item for taxa https://www.inaturalist.org/taxa/{taxa_id} - {items[0].getID()}')

            # Store the mapping in the cache
            self.__class__.taxa_wikidata_map[taxa_id] = items[0]
            self.photo.depicts = items[0]
            break

    def hook_creator_claim(self, claim: Claim) -> None:
        assert self.photo and self.photo.creator

        inaturalist_user_id_qualifier = Claim(self.commons, WikidataProperty.INaturalistUserId)
        inaturalist_user_id_qualifier.setTarget(self.photo.creator.id)
        claim.addQualifier(inaturalist_user_id_qualifier)

        if self.photo.creator.orcid:
            orcid_qualifier = Claim(self.commons, WikidataProperty.ORCID)
            orcid_qualifier.setTarget(self.photo.creator.orcid)
            claim.addQualifier(orcid_qualifier)

    def hook_creator_target(self, claim: Claim) -> None:
        assert self.photo and self.photo.creator

        creator_item = self.find_creator_wikidata_item()

        if creator_item:
            claim.setTarget(creator_item)
        else:
            claim.setSnakType('somevalue')

    def hook_depicts_claim(self, claim: Claim) -> None:
        assert self.photo and self.photo.depicts

        stated_in_ref = Claim(self.commons, WikidataProperty.StatedIn)
        stated_in_ref.setTarget(self.inaturalist_wd)
        claim.addSource(stated_in_ref)

    def find_creator_wikidata_item(self) -> ItemPage | None:
        """Find Wikidata item for the creator based on iNaturalist user ID."""
        assert self.photo and self.photo.creator

        info(f'Searching Wikidata for iNaturalist user with ID {self.photo.creator.id}')
        gen = WikidataSPARQLPageGenerator(sparql_user_query.substitute(user_id=self.photo.creator.id),
                                          site=self.wikidata)
        items = list(gen)

        if len(items) == 0:
            return None

        if len(items) > 1:
            warning(
                f'Found multiple Wikidata items for iNaturalist user https://www.inaturalist.org/people/{self.photo.creator.id}: {items}')
            return None

        info(f'Wikidata item for iNaturalist user found - {items[0].getID()}')

        return items[0]


def main() -> None:
    INaturalistBot().run()


if __name__ == "__main__":
    main()
