import datetime
import os
import sys
from time import perf_counter
from typing import Any

import mwparserfromhell
from flickr_photos_api import FlickrApi, PhotoIsPrivate, ResourceNotFound, SinglePhoto, User, LocationInfo, \
    DateTaken
from flickr_url_parser import parse_flickr_url
from mwparserfromhell.wikicode import Wikicode
from pywikibot import Claim, Category, info, warning, error, Coordinate, WbTime, Timestamp, ItemPage
from pywikibot.pagegenerators import CategorizedPageGenerator
from redis import Redis

try:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)) + '/lib')
    from lib.bot import BaseBot
    from lib.wikidata import WikidataEntity, WikidataProperty
except:
    from .lib.bot import BaseBot
    from .lib.wikidata import WikidataEntity, WikidataProperty


class FlickrBot(BaseBot):
    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)

        self.generator = CategorizedPageGenerator(Category(self.commons, 'Flickr images missing SDC creator'), namespaces=[6])

        self.flickr_api = FlickrApi.with_api_key(api_key=os.getenv("FLICKR_API_KEY"), user_agent=self.user_agent)
        self.redis = Redis(host='redis.svc.tools.eqiad1.wikimedia.cloud', db=9)
        self.redis_prefix = 'xQ6cz5J84Viw/K6FIcOH1kxJjfiS8jO56AoSmhBgO/A='

    def treat_page(self) -> None:
        super().treat_page()
        redis_key = f'{self.redis_prefix}:commons:{self.mid}'

        # Check Redis cache to avoid processing the same page multiple times
        if self.redis.get(redis_key) is not None:
            warning('Skipping due to Redis cache')
            return

        if 'Flickr images reviewed by FlickreviewR 2' not in [c.title(with_ns=False) for c in self.current_page.categories()]:
            warning('Skipping as it is not in the Flickr images reviewed by FlickreviewR 2 category')
            self.redis.set(redis_key, 1)
            return

        wikitext: Wikicode = mwparserfromhell.parse(self.current_page.text)
        flickr_review = list(filter(lambda t: t.name == 'FlickreviewR', wikitext.filter_templates()))

        if len(flickr_review) != 1:
            warning('Skipping as it does not have a valid FlickreviewR template')
            self.redis.set(redis_key, 1)
            return

        flickr_url = list(filter(lambda p: p.name == 'sourceurl', flickr_review[0].params))

        if len(flickr_url) != 1:
            warning('Skipping as FlickreviewR does not have a valid sourceurl parameter')
            self.redis.set(redis_key, 1)
            return

        flickr_url = str(flickr_url[0].value)
        info(flickr_url)

        flickr_id = parse_flickr_url(flickr_url)
        info(flickr_id)

        if flickr_id.get('type') != 'single_photo':
            warning('Skipping as it is not a single photo in Flickr')
            self.redis.set(redis_key, 1)
            return

        flickr_photo = self.get_flickr_photo(flickr_id['photo_id'])

        if flickr_photo is None:
            warning('Skipping as photo not found in Flickr')
            self.redis.set(redis_key, 1)
            return

        self.fetch_claims()
        self.create_id_claim(flickr_photo['id'])
        self.create_creator_claim(flickr_photo['owner'])
        self.create_source_claim(flickr_photo['url'], WikidataEntity.Flickr)
        self.create_location_claim(flickr_photo['location'])
        self.create_inception_claim(flickr_photo['date_taken'])
        self.create_published_in_claim(flickr_photo['date_posted'])

        self.save('add [[Commons:Structured data|SDC]] based on metadata from Flickr. Task #2')

    def get_flickr_photo(self, flickr_photo_id: str) -> SinglePhoto | None:
        single_photo = None
        redis_key_photo = f'{self.redis_prefix}:{flickr_photo_id}:photo'

        # Check Redis cache if Flickr photo is not available
        if self.redis.get(redis_key_photo) is not None:
            warning('Flickr photo skipped due to Redis cache')
            return single_photo

        try:
            start = perf_counter()
            single_photo = self.flickr_api.get_single_photo_info(photo_id=flickr_photo_id)
            info(f"Retrieved Flickr photo in {(perf_counter() - start) * 1000:.0f} ms")
        except (PhotoIsPrivate, ResourceNotFound) as e:
            warning(f"[{flickr_photo_id}] {e}")
            self.redis.set(redis_key_photo, 1)
        except Exception as e:
            error(f"[{flickr_photo_id}] {e}")
            time.sleep(60)

        return single_photo

    def create_id_claim(self, flickr_photo_id: str) -> None:
        if WikidataProperty.FlickrPhotoId in self.existing_claims:
            return

        claim = Claim(self.commons, WikidataProperty.FlickrPhotoId)
        claim.setTarget(flickr_photo_id)

        self.new_claims.append(claim.toJSON())

    def create_creator_claim(self, user: User | None) -> None:
        if WikidataProperty.Creator in self.existing_claims or user is None:
            return

        claim = Claim(self.commons, WikidataProperty.Creator)
        claim.setSnakType('somevalue')

        author_name = (user["realname"] or user["username"]).strip()
        author_qualifier = Claim(self.commons, WikidataProperty.AuthorName)
        author_qualifier.setTarget(author_name)
        claim.addQualifier(author_qualifier)

        url_qualifier = Claim(self.commons, WikidataProperty.Url)
        url_qualifier.setTarget(user['profile_url'])
        claim.addQualifier(url_qualifier)

        flickr_user_id_qualifier = Claim(self.commons, WikidataProperty.FlickrUserId)
        flickr_user_id_qualifier.setTarget(user['id'])
        claim.addQualifier(flickr_user_id_qualifier)

        # ToDo Add custom things

        self.new_claims.append(claim.toJSON())

    def create_location_claim(self, location: LocationInfo | None) -> None:
        if WikidataProperty.CoordinatesOfThePointOfView in self.existing_claims:
            return

        """
            Source: https://github.com/Flickr-Foundation/flickypedia/blob/b7c9b711c31ea0ca67714a879571c52c643d9e9f/src/flickypedia/structured_data/statements/location_statement.py
            License: MIT License as this project is under MIT License
                     https://github.com/Flickr-Foundation/flickypedia/blob/b7c9b711c31ea0ca67714a879571c52c643d9e9f/LICENSE-MIT
        """

        """
            START
        """

        """
            Creates a structured data statement for the "coordinates of
            the point of view" statement.

            This is the location of the camera, not the location of the subject.
            There were several discussions about this in the Flickr.org Slack and
            this was agreed as the most suitable.

            See https://flickrfoundation.slack.com/archives/C05AVC1JYL9/p1696947242703349
            """
        if location is None:
            return None

        # Some Flickr photos have "null coordinates" -- location data which
        # is obviously nonsensical.
        #
        # e.g. https://www.flickr.com/photos/ed_webster/16125227798/
        #
        #     <location latitude="0.000000" longitude="0.000000" accuracy="16" context="0">
        #       <neighbourhood woeid="0"/>
        #     </location>
        #
        # In this case we should just discard the information as useless, rather
        # than write null coordinates into WMC.
        #
        # See https://github.com/Flickr-Foundation/flickypedia/issues/461
        if location["latitude"] == 0.0 and location["longitude"] == 0.0:
            return None

        # The accuracy parameter in the Flickr API response tells us
        # the precision of the location information (15 November 2023):
        #
        #     Recorded accuracy level of the location information.
        #     World level is 1, Country is ~3, Region ~6, City ~11, Street ~16.
        #     Current range is 1-16.
        #
        # Flickr doesn't publish any definitive stats on how their accuracy
        # levels map to absolute position on the Earth, so I had to make
        # some rough guesses.  This information is already approximate, so
        # I figure this is probably okay.
        #
        # ============
        # How I did it
        # ============
        #
        # If you look at the map view on Flickr (https://www.flickr.com/map/),
        # there are 17 different zoom levels, which correspond to the
        # different accuracies (0-17, although you can't see accuracy 0
        # on new photos).
        #
        # For each zoom/accuracy level:
        #
        #   1.  Create a new property for "coordinates of the point of view"
        #       in the Wikimedia Commons SDC visual editor.
        #   2.  Click "Select on map"
        #   3.  Zoom the map to roughly match the Flickr map (using the
        #       scale as a guide)
        #   4.  Click a point on the map
        #
        # At this point Wikimedia zooms to a fixed level, and updates its own
        # value for precision (to 1/1000 of an arcsecond, ±0.0001°, etc.)
        #
        # Use that value for precision.
        try:
            wikidata_precision = {
                # Flickr = 50m / WMC = ±0.000001°
                16: 1e-05,
                # Flickr = 100m, 300m / WMC = 1/10 of an arcsecond
                15: 2.777777777777778e-05,
                14: 2.777777777777778e-05,
                # Flickr = 500m, 1km / WMC = ±0.0001°
                13: 0.0001,
                12: 0.0001,
                # Flickr = 3km / WMC = to an arcsecond
                11: 0.0002777777777777778,
                # Flickr = 5km, 10km, 20km, 50km  / WMC = ±0.001°
                10: 0.001,
                9: 0.001,
                8: 0.001,
                7: 0.001,
                # Flickr =  100km / WMC = ±0.01°
                6: 0.01,
                # Flickr =  200km, 300km / WMC = to an arcminute
                5: 0.016666666666666666,
                4: 0.016666666666666666,
                # Flickr = 500km, 1000km, 3000km / WMC = ±0.1°
                3: 0.1,
                2: 0.1,
                1: 0.1,
            }[location["accuracy"]]
        except KeyError:
            """
                END
            """

            error(f'Unrecognised location accuracy: {location["accuracy"]}')
            return

        claim = Claim(self.commons, WikidataProperty.CoordinatesOfThePointOfView)
        claim.setTarget(Coordinate(location["latitude"], location["longitude"], wikidata_precision))

        self.new_claims.append(claim.toJSON())

    def create_inception_claim(self, date_taken: DateTaken | None) -> None:
        if WikidataProperty.Inception in self.existing_claims or date_taken is None:
            return

        precision_map = {
            'second': WbTime.PRECISION['day'],
            'month': WbTime.PRECISION['month'],
            'year': WbTime.PRECISION['year'],
            'circa': WbTime.PRECISION['year'],
        }

        if date_taken['granularity'] not in precision_map:
            error(f'Unrecognised date granularity: {date_taken}')
            return

        ts = Timestamp.fromISOformat(date_taken['value'].isoformat())
        precision = precision_map[date_taken['granularity']]

        year = date_taken['value'].year
        month = 0 if precision < WbTime.PRECISION['month'] else date_taken['value'].month
        day = 0 if precision < WbTime.PRECISION['day'] else date_taken['value'].day

        claim = Claim(self.commons, WikidataProperty.Inception)
        claim.setTarget(WbTime(year, month, day, precision=precision))

        if date_taken['granularity'] == 'circa':
            circa_qualifier = Claim(self.commons, WikidataProperty.SourcingCircumstances)
            circa_qualifier.setTarget(ItemPage(self.wikidata, WikidataEntity.Circa))
            claim.addQualifier(circa_qualifier)

        self.new_claims.append(claim.toJSON())

    def create_published_in_claim(self, date_posted: datetime.datetime) -> None:
        if WikidataProperty.PublishedIn in self.existing_claims:
            return

        claim = Claim(self.commons, WikidataProperty.PublishedIn)
        claim.setTarget(ItemPage(self.wikidata, WikidataEntity.Flickr))

        ts = Timestamp.fromISOformat(date_posted.isoformat())

        date_posted_qualifier = Claim(self.commons, WikidataProperty.PublicationDate)
        date_posted_qualifier.setTarget(WbTime(ts.year, ts.month, ts.day, precision=WbTime.PRECISION['day']))
        claim.addQualifier(date_posted_qualifier)

        self.new_claims.append(claim.toJSON())


def main() -> None:
    FlickrBot().run()


if __name__ == "__main__":
    main()
