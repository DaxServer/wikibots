from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from mwparserfromhell.wikicode import Wikicode

WBTIME_PRECISION = {"year": 9, "month": 10, "day": 11}


@dataclass
class WbTime:
    year: int
    month: int = 0
    day: int = 0
    precision: int = 11

    PRECISION: ClassVar[dict[str, int]] = WBTIME_PRECISION


def _value_snak(property: str, datavalue: dict) -> dict:
    return {"snaktype": "value", "property": property, "datavalue": datavalue}


def _somevalue_snak(property: str) -> dict:
    return {"snaktype": "somevalue", "property": property}


def _make_statement(mainsnak: dict) -> dict:
    return {"type": "statement", "rank": "normal", "mainsnak": mainsnak}


def _add_qualifier(claim: dict, snak: dict) -> None:
    prop = snak["property"]
    qualifiers = claim.setdefault("qualifiers", {})
    qualifiers_order = claim.setdefault("qualifiers-order", [])
    if prop not in qualifiers:
        qualifiers[prop] = []
        qualifiers_order.append(prop)
    qualifiers[prop].append(snak)


def _add_reference(claim: dict, snaks: list[dict]) -> None:
    ref_snaks: dict[str, list] = {}
    ref_snaks_order: list[str] = []
    for snak in snaks:
        prop = snak["property"]
        if prop not in ref_snaks:
            ref_snaks[prop] = []
            ref_snaks_order.append(prop)
        ref_snaks[prop].append(snak)
    claim.setdefault("references", []).append(
        {"snaks": ref_snaks, "snaks-order": ref_snaks_order}
    )


def _entity_dv(entity_id: str) -> dict:
    return {
        "type": "wikibase-entityid",
        "value": {
            "entity-type": "item",
            "numeric-id": int(entity_id[1:]),
            "id": entity_id,
        },
    }


def _string_dv(value: str) -> dict:
    return {"type": "string", "value": value}


def _quantity_dv(amount: int | float, unit_id: str | None = None) -> dict:
    unit = f"http://www.wikidata.org/entity/{unit_id}" if unit_id else "1"
    return {"type": "quantity", "value": {"amount": f"+{amount}", "unit": unit}}


def _time_dv(wbtime: WbTime) -> dict:
    time_str = f"+{wbtime.year:04d}-{wbtime.month:02d}-{wbtime.day:02d}T00:00:00Z"
    return {
        "type": "time",
        "value": {
            "time": time_str,
            "timezone": 0,
            "before": 0,
            "after": 0,
            "precision": wbtime.precision,
            "calendarmodel": "http://www.wikidata.org/entity/Q1985727",
        },
    }


def _globecoordinate_dv(lat: float, lon: float, precision: float) -> dict:
    return {
        "type": "globecoordinate",
        "value": {
            "latitude": lat,
            "longitude": lon,
            "precision": precision,
            "globe": "http://www.wikidata.org/entity/Q2",
        },
    }


class Claim:
    @classmethod
    def somevalue(cls, property: str) -> "Claim":
        return cls(_make_statement(_somevalue_snak(property)))

    @classmethod
    def string(cls, property: str, value: str) -> "Claim":
        return cls(_make_statement(_value_snak(property, _string_dv(value))))

    @classmethod
    def entity(cls, property: str, entity_id: str) -> "Claim":
        return cls(_make_statement(_value_snak(property, _entity_dv(entity_id))))

    @classmethod
    def quantity(
        cls, property: str, amount: int | float, unit_id: str | None = None
    ) -> "Claim":
        return cls(
            _make_statement(_value_snak(property, _quantity_dv(amount, unit_id)))
        )

    @classmethod
    def time(cls, property: str, wbtime: WbTime) -> "Claim":
        return cls(_make_statement(_value_snak(property, _time_dv(wbtime))))

    @classmethod
    def coordinate(
        cls, property: str, lat: float, lon: float, precision: float
    ) -> "Claim":
        return cls(
            _make_statement(
                _value_snak(property, _globecoordinate_dv(lat, lon, precision))
            )
        )

    def __init__(self, data: dict) -> None:
        self._data = data

    def add_qualifier_string(self, property: str, value: str) -> None:
        _add_qualifier(self._data, _value_snak(property, _string_dv(value)))

    def add_qualifier_entity(self, property: str, entity_id: str) -> None:
        _add_qualifier(self._data, _value_snak(property, _entity_dv(entity_id)))

    def add_qualifier_time(self, property: str, dt: datetime, precision: int) -> None:
        _add_qualifier(
            self._data,
            _value_snak(
                property, _time_dv(WbTime(dt.year, dt.month, dt.day, precision))
            ),
        )

    def add_reference_entity(self, property: str, entity_id: str) -> None:
        _add_reference(self._data, [_value_snak(property, _entity_dv(entity_id))])

    def set_entity_target(self, entity_id: str) -> None:
        """Replace a somevalue snak with a resolved entity target."""
        property = self._data["mainsnak"]["property"]
        self._data["mainsnak"] = _value_snak(property, _entity_dv(entity_id))

    def to_dict(self) -> dict:
        return self._data


@dataclass
class WikiProperties:
    mid: str
    redis_key: str
    existing_claims: dict[str, list]
    new_claims: list[Claim] = field(default_factory=list)
    wikicode: Wikicode | None = None
    sha1: str | None = None
    mime: str | None = None
    metadata: dict[str, str | int | float] = field(default_factory=dict)
    size: int | None = None
    width: int | None = None
    height: int | None = None
