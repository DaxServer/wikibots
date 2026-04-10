import logging
import os
import time
from datetime import datetime
from time import perf_counter

import httpx
from flickr_api import FlickrApi
from flickr_api.exceptions import PermissionDenied, ResourceNotFound
from flickr_api.models import SinglePhotoInfo
from flickr_api.models.photo import Location
from flickr_url_parser import parse_flickr_url

from wikibots.lib.bot import BaseBot, RateLimitExhausted
from wikibots.lib.claim import WBTIME_PRECISION, Claim
from wikibots.lib.wikidata import WikidataEntity, WikidataProperty

logger = logging.getLogger(__name__)

RATE_LIMIT_DELAYS = (60, 180, 300)


class FlickrBot(BaseBot):
    redis_prefix = "xQ6cz5J84Viw/K6FIcOH1kxJjfiS8jO56AoSmhBgO/A="
    summary = "add [[Commons:Structured data|SDC]] based on metadata from Flickr"
    always_null_edit = True
    search_query = (
        'file: incategory:"Flickr images reviewed by FlickreviewR 2" hastemplate:"FlickreviewR" '
        f"-haswbstatement:{WikidataProperty.FlickrPhotoId}"
    )

    def __init__(self) -> None:
        super().__init__()

        self.flickr_api = FlickrApi.with_api_key(
            api_key=os.getenv("FLICKR_API_KEY", ""),
            user_agent=self.user_agent,
        )
        self.photo: SinglePhotoInfo | None = None

    def treat_page(self) -> None:
        # Reset
        self.photo = None

        self.fetch_claims()

        photo_id = self.extract_flickr_data()
        if photo_id is None:
            return

        self.get_file_metadata()

        # EXIF
        self.create_exposure_time_claim()
        self.create_iso_speed_claim()
        self.create_fnumber_claim()
        self.create_focal_length_claim()

        # Size
        self.create_datasize_claim()
        self.create_height_claim()
        self.create_width_claim()

        # Validation
        self.create_checksum_claim()
        self.create_media_type_claim()

        self.get_flickr_photo(photo_id)

        if self.photo is None:
            self.create_id_claim(WikidataProperty.FlickrPhotoId, photo_id)
            self.save()
            return

        self.create_id_claim(WikidataProperty.FlickrPhotoId, self.photo["id"])
        author_name = (
            self.photo["owner"]["realname"] or self.photo["owner"]["username"]
        ).strip()
        self.create_creator_claim(
            author_name_string=author_name, url=self.photo["owner"]["profile_url"]
        )
        self.create_source_claim(self.photo["url"], WikidataEntity.Flickr)
        date_posted_dt: datetime | None = None
        date_posted = self.photo.get("date_posted")
        if isinstance(date_posted, datetime):
            date_posted_dt = date_posted
        elif isinstance(date_posted, (int, float)):
            date_posted_dt = datetime.fromtimestamp(date_posted)
        elif isinstance(date_posted, str):
            try:
                date_posted_dt = datetime.fromisoformat(
                    date_posted.replace("Z", "+00:00")
                )
            except ValueError:
                date_posted_dt = None
        self.create_published_in_claim(WikidataEntity.Flickr, date_posted_dt)
        self.create_location_claim(self.photo["location"])
        self._create_inception_claim()

        self.save()

    def extract_flickr_data(self) -> str | None:
        assert self.wiki_properties

        review_status = self.retrieve_template_data(["FlickreviewR"], ["status"])
        if review_status not in ("pass", "passed"):
            logger.warning(
                f"Skipping: FlickreviewR status is {review_status!r}, not 'pass' or 'passed'"
            )
            self.redis.set(self.wiki_properties.redis_key, 1)
            return None

        flickr_url = self.retrieve_template_data(["FlickreviewR"], ["sourceurl"])
        if flickr_url is None:
            return None

        logger.info(flickr_url)

        try:
            flickr_id = parse_flickr_url(flickr_url)
        except Exception as e:
            logger.error(f"Failed to parse Flickr URL: {e}")
            self.redis.set(self.wiki_properties.redis_key, 1)
            return None

        logger.info(flickr_id)

        if flickr_id.get("type") != "single_photo" or flickr_id.get("photo_id") is None:
            logger.error("Skipping as it is not a single photo in Flickr")
            logger.info(flickr_id)
            self.redis.set(self.wiki_properties.redis_key, 1)
            return None

        return flickr_id.get("photo_id")

    def get_flickr_photo(self, flickr_photo_id: str) -> None:
        redis_key_photo = f"{self.redis_prefix}:{flickr_photo_id}:photo"

        if self.redis.get(redis_key_photo) is not None:
            logger.warning("Flickr photo skipped due to Redis cache")
            return

        delays = iter(RATE_LIMIT_DELAYS)
        while True:
            try:
                start = perf_counter()
                self.photo = self.flickr_api.get_single_photo_info(photo_id=flickr_photo_id)
                logger.info(
                    f"Retrieved Flickr photo in {(perf_counter() - start) * 1000:.0f} ms"
                )
                return
            except (ResourceNotFound, PermissionDenied) as e:
                logger.warning(f"[{flickr_photo_id}] {e}")
                self.redis.set(redis_key_photo, 1)
                return
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 429:
                    logger.error(f"[{flickr_photo_id}] {e}")
                    return
                delay = next(delays, None)
                if delay is None:
                    logger.critical(f"[{flickr_photo_id}] Rate limit exhausted after all retries")
                    raise RateLimitExhausted
                logger.warning(f"[{flickr_photo_id}] Rate limited, retrying in {delay}s")
                time.sleep(delay)
            except Exception as e:
                logger.error(f"[{flickr_photo_id}] {e}")
                return

    def hook_creator_claim(self, claim: Claim) -> None:
        if not self.photo:
            return
        claim.add_qualifier_string(
            WikidataProperty.FlickrUserId, self.photo["owner"]["id"]
        )

    def _create_inception_claim(self) -> None:
        assert self.photo

        if self.photo["date_taken"] is None:
            return

        granularity = self.photo["date_taken"]["granularity"]
        precision_map = {
            "second": WBTIME_PRECISION["day"],
            "month": WBTIME_PRECISION["month"],
            "year": WBTIME_PRECISION["year"],
            "circa": WBTIME_PRECISION["year"],
        }

        if granularity not in precision_map:
            logger.error(
                f"Unrecognised date granularity: {granularity} in photo {self.photo['date_taken']}"
            )
            return

        precision = precision_map[granularity]
        self.create_inception_claim(
            self.photo["date_taken"]["value"], precision, granularity
        )

    def create_location_claim(self, location: Location | None) -> None:
        assert self.wiki_properties

        if (
            WikidataProperty.CoordinatesOfThePointOfView
            in self.wiki_properties.existing_claims
        ):
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
            return

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
            return

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

            logger.error(f"Unrecognised location accuracy: {location['accuracy']}")
            return

        claim = Claim.coordinate(
            WikidataProperty.CoordinatesOfThePointOfView,
            location["latitude"],
            location["longitude"],
            wikidata_precision,
        )
        self.wiki_properties.new_claims.append(claim)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    FlickrBot().run()


if __name__ == "__main__":
    main()
