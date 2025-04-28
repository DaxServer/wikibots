import os
import re
import sys
from dataclasses import dataclass
from typing import Any

import requests
from pywikibot import warning, info, Claim
from pywikibot.page import BasePage
from pywikibot.pagegenerators import SearchPageGenerator

try:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)) + '/lib')
    from lib.bot import BaseBot
    from lib.wikidata import WikidataEntity, WikidataProperty
except:
    from .lib.bot import BaseBot
    from .lib.wikidata import WikidataEntity, WikidataProperty


@dataclass
class User:
    id: str
    username: str | None = None
    realname: str | None = None


@dataclass
class PhotoData:
    id: str
    observation_id: str
    creator: User | None = None


class INaturalistBot(BaseBot):
    photo: PhotoData
    redis_prefix = 'ZHXgxFHT4ZBJjR+fLxCH9quuLYl7ky4N6fNV/oC4fbs='
    summary = 'add [[Commons:Structured data|SDC]] based on metadata from iNaturalist. Test run.'

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

        self.generator = SearchPageGenerator(f'file: hastemplate:iNaturalist hastemplate:iNaturalistReview -haswbstatement:{WikidataProperty.INaturalistPhotoId}', site=self.commons)

    def skip_page(self, page: BasePage) -> bool:
        redis = self.redis.exists(f'{self.redis_prefix}:commons:M{page.pageid}')
        touched = self.commons.username() in page.contributors()

        return redis or touched

    def treat_page(self) -> None:
        super().treat_page()

        status = self.retrieve_template_data(['iNaturalistReview', 'iNaturalistreview'], ['status'])
        if status is None:
            return

        if status not in ['pass', 'pass-change']:
            warning(f'Skipping as iNaturalistReview status is {status}')
            self.redis.set(self.main_redis_key, 1)
            return

        self.fetch_claims()
        self.fetch_observation_data()

        try:
            self.photo
        except:
            return

        self.create_id_claim(WikidataProperty.INaturalistPhotoId, self.photo.id)
        self.create_id_claim(WikidataProperty.INaturalistObservationId, self.photo.observation_id)
        self.create_creator_claim_tmp(author_name_string=self.photo.creator.realname)
        self.create_source_claim(f'https://www.inaturalist.org/photos/{self.photo.id}', WikidataEntity.iNaturalist)

        self.save()

    def fetch_observation_data(self) -> None:
        observation_id = self.retrieve_template_data(['iNaturalist', 'inaturalist'], ['id', '1'])
        if observation_id is None:
            return

        photo_url = self.retrieve_template_data(['iNaturalistReview', 'iNaturalistreview'], ['sourceurl'])
        if photo_url is None:
            return

        matches = re.match(r'https://www.inaturalist.org/photos/(\d+)', photo_url)
        if matches is None:
            warning(f'Skipping as INaturalist URL is invalid {photo_url}')
            self.redis.set(self.main_redis_key, 1)
            return

        self.photo = PhotoData(id=matches.group(1), observation_id=observation_id)
        info(f'INaturalist Photo ID: {self.photo.id}')
        info(f'INaturalist Observation ID: {observation_id}')

        try:
            observation = requests.get(f'https://api.inaturalist.org/v1/observations/{observation_id}', headers={
                'Accept': 'application/json',
                'User-Agent': self.user_agent,
            }).json()['results'][0]
        except Exception as e:
            warning(f'Failed to fetch observation: {e}')
            self.redis.set(self.main_redis_key, 1)
            return

        if self.photo.id not in [o['photo_id'].__str__() for o in observation['observation_photos']]:
            warning('Skipping as iNaturalist photo is not attached to observation')
            self.redis.set(self.main_redis_key, 1)
            return

        if 'user' in observation:
            self.photo.creator = User(id=observation['user']['id'].__str__())

            if 'name' in observation['user']:
                self.photo.creator.realname = observation['user']['name']

            if 'login' in observation['user']:
                self.photo.creator.username = observation['user']['login']

    def hook_creator_claim(self, claim: Claim) -> None:
        inaturalist_user_id_qualifier = Claim(self.commons, WikidataProperty.INaturalistUserId)
        inaturalist_user_id_qualifier.setTarget(self.photo.creator.id)
        claim.addQualifier(inaturalist_user_id_qualifier)


def main() -> None:
    INaturalistBot().run()


if __name__ == "__main__":
    main()
