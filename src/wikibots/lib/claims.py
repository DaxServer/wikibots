from datetime import datetime
from fractions import Fraction

from wikibots.lib.claim import (
    WBTIME_PRECISION,
    Claim,
    WbTime,
    WikiProperties,
)
from wikibots.lib.wikidata import WikidataEntity, WikidataProperty


class ClaimsMixin:
    wiki_properties: WikiProperties | None

    def _to_number(self, value: str | int | float | None) -> int | float | None:
        """Convert a string/fraction/number to int or float."""
        if value is None:
            return None
        try:
            fraction = Fraction(value)
            if fraction.is_integer():
                return int(fraction)
            return float(fraction)
        except (ValueError, ZeroDivisionError):
            return None

    def create_checksum_claim(self) -> None:
        assert self.wiki_properties
        checksum = self.wiki_properties.sha1
        if (
            checksum is None
            or WikidataProperty.Checksum in self.wiki_properties.existing_claims
        ):
            return
        claim = Claim.string(WikidataProperty.Checksum, checksum)
        claim.add_qualifier_entity(
            WikidataProperty.DeterminationMethod, WikidataEntity.SHA1
        )
        self.wiki_properties.new_claims.append(claim)

    def create_creator_claim(
        self, author_name_string: str | None = None, url: str | None = None
    ) -> None:
        assert self.wiki_properties
        if WikidataProperty.Creator in self.wiki_properties.existing_claims:
            return
        claim = Claim.somevalue(WikidataProperty.Creator)
        self.hook_creator_target(claim)
        if author_name_string:
            claim.add_qualifier_string(
                WikidataProperty.AuthorNameString, author_name_string
            )
        if url:
            claim.add_qualifier_string(WikidataProperty.Url, url)
        self.hook_creator_claim(claim)
        self.wiki_properties.new_claims.append(claim)

    def create_datasize_claim(self) -> None:
        assert self.wiki_properties
        datasize = self._to_number(self.wiki_properties.size)
        if (
            datasize is None
            or WikidataProperty.DataSize in self.wiki_properties.existing_claims
        ):
            return
        self.wiki_properties.new_claims.append(
            Claim.quantity(WikidataProperty.DataSize, datasize, WikidataEntity.Byte)
        )

    def create_depicts_claim(self, depicts: str | None) -> None:
        assert self.wiki_properties
        if (
            WikidataProperty.Depicts in self.wiki_properties.existing_claims
            or depicts is None
        ):
            return
        claim = Claim.entity(WikidataProperty.Depicts, depicts)
        self.hook_depicts_claim(claim)
        self.wiki_properties.new_claims.append(claim)

    def create_exposure_time_claim(self) -> None:
        assert self.wiki_properties
        exposure_time = self._to_number(
            self.wiki_properties.metadata.get("ExposureTime")
        )
        if (
            exposure_time is None
            or WikidataProperty.ExposureTime in self.wiki_properties.existing_claims
        ):
            return
        self.wiki_properties.new_claims.append(
            Claim.quantity(
                WikidataProperty.ExposureTime, exposure_time, WikidataEntity.Second
            )
        )

    def create_fnumber_claim(self) -> None:
        assert self.wiki_properties
        fnumber = self._to_number(self.wiki_properties.metadata.get("FNumber"))
        if (
            fnumber is None
            or WikidataProperty.FNumber in self.wiki_properties.existing_claims
        ):
            return
        self.wiki_properties.new_claims.append(
            Claim.quantity(WikidataProperty.FNumber, fnumber)
        )

    def create_focal_length_claim(self) -> None:
        assert self.wiki_properties
        focal_length = self._to_number(self.wiki_properties.metadata.get("FocalLength"))
        if (
            focal_length is None
            or WikidataProperty.FocalLength in self.wiki_properties.existing_claims
        ):
            return
        self.wiki_properties.new_claims.append(
            Claim.quantity(
                WikidataProperty.FocalLength, focal_length, WikidataEntity.MilliMeter
            )
        )

    def create_height_claim(self) -> None:
        assert self.wiki_properties
        height = self._to_number(self.wiki_properties.height)
        if (
            height is None
            or WikidataProperty.Height in self.wiki_properties.existing_claims
        ):
            return
        self.wiki_properties.new_claims.append(
            Claim.quantity(WikidataProperty.Height, height, WikidataEntity.Pixel)
        )

    def create_id_claim(self, property: str, value: str) -> None:
        assert self.wiki_properties
        if property in self.wiki_properties.existing_claims:
            return
        self.wiki_properties.new_claims.append(Claim.string(property, value))

    def create_inception_claim(
        self, dt: datetime, precision: int, granularity: str
    ) -> None:
        assert self.wiki_properties
        if WikidataProperty.Inception in self.wiki_properties.existing_claims:
            return
        month = 0 if precision < WBTIME_PRECISION["month"] else dt.month
        day = 0 if precision < WBTIME_PRECISION["day"] else dt.day
        claim = Claim.time(
            WikidataProperty.Inception, WbTime(dt.year, month, day, precision)
        )
        if granularity == "circa":
            claim.add_qualifier_entity(
                WikidataProperty.SourcingCircumstances, WikidataEntity.Circa
            )
        self.wiki_properties.new_claims.append(claim)

    def create_iso_speed_claim(self) -> None:
        assert self.wiki_properties
        iso_speed = self._to_number(
            self.wiki_properties.metadata.get("ISOSpeedRatings")
        )
        if (
            iso_speed is None
            or WikidataProperty.ISOSpeed in self.wiki_properties.existing_claims
        ):
            return
        self.wiki_properties.new_claims.append(
            Claim.quantity(WikidataProperty.ISOSpeed, iso_speed)
        )

    def create_media_type_claim(self) -> None:
        assert self.wiki_properties
        media_type = self.wiki_properties.mime
        if (
            media_type is None
            or WikidataProperty.MediaType in self.wiki_properties.existing_claims
        ):
            return
        self.wiki_properties.new_claims.append(
            Claim.string(WikidataProperty.MediaType, media_type)
        )

    def create_published_in_claim(
        self, published_in: str, date_posted: datetime | None = None
    ) -> None:
        assert self.wiki_properties
        if WikidataProperty.PublishedIn in self.wiki_properties.existing_claims:
            return
        claim = Claim.entity(WikidataProperty.PublishedIn, published_in)
        if date_posted is not None:
            claim.add_qualifier_time(
                WikidataProperty.PublicationDate, date_posted, WBTIME_PRECISION["day"]
            )
        self.wiki_properties.new_claims.append(claim)

    def create_source_claim(self, source: str, operator: str | None = None) -> None:
        assert self.wiki_properties
        if WikidataProperty.SourceOfFile in self.wiki_properties.existing_claims:
            return
        claim = Claim.entity(
            WikidataProperty.SourceOfFile, WikidataEntity.FileAvailableOnInternet
        )
        claim.add_qualifier_string(WikidataProperty.DescribedAtUrl, source)
        if operator:
            claim.add_qualifier_entity(WikidataProperty.Operator, operator)
        self.hook_source_claim(claim)
        self.wiki_properties.new_claims.append(claim)

    def create_width_claim(self) -> None:
        assert self.wiki_properties
        width = self._to_number(self.wiki_properties.width)
        if (
            width is None
            or WikidataProperty.Width in self.wiki_properties.existing_claims
        ):
            return
        self.wiki_properties.new_claims.append(
            Claim.quantity(WikidataProperty.Width, width, WikidataEntity.Pixel)
        )

    def hook_creator_claim(self, claim: Claim) -> None:
        pass

    def hook_creator_target(self, claim: Claim) -> None:
        pass

    def hook_depicts_claim(self, claim: Claim) -> None:
        pass

    def hook_source_claim(self, claim: Claim) -> None:
        pass
