"""Bind a certified station's real coordinates to a real NWS region for I2.

Master upgrade I2 requires a "versioned mapping or clustering layer" covering
station -> city, city/station -> region and common model/source dependence,
feeding the regional/correlated-exposure ceilings in ``scenario_risk``. This
module builds that mapping's per-station membership from already-certified,
receipt-bound evidence only:

- ``StationMetadata`` (v11/certification.py) already carries the station's real
  city, coordinates and real observation/forecast provider identities;
- ``NWSPointOffice``/``NWSOfficeRegion`` (weather_only_station_region.py) carry
  the real public NWS county-warning-area and regional-headquarters assignment
  for that exact coordinate.

No region, city or dependence group is invented, defaulted or guessed: a
coordinate/office chain mismatch or an empty provider set fails closed rather
than falling back to an approximate or placeholder value.
"""
from __future__ import annotations

from .certification import StationMetadata
from .evidence import EvidenceError
from .scenario_risk import StationMembership
from ..weather_only_station_region import NWSOfficeRegion, NWSPointOffice

VERSION = "alpha_v11_region_membership_v1"


def build_station_membership(
    *,
    metadata: StationMetadata,
    point: NWSPointOffice,
    office: NWSOfficeRegion,
    weather_groups: tuple[str, ...] | None = None,
    source_groups: tuple[str, ...] | None = None,
    model_groups: tuple[str, ...] | None = None,
) -> StationMembership:
    if not isinstance(metadata, StationMetadata):
        raise EvidenceError("REGION_MEMBERSHIP_METADATA_REQUIRED")
    if not isinstance(point, NWSPointOffice) or not isinstance(office, NWSOfficeRegion):
        raise EvidenceError("REGION_MEMBERSHIP_REGION_EVIDENCE_REQUIRED")
    # The point query must have been made against this exact certified station's
    # own real coordinates, and the office fetched must be the same office the
    # point actually resolved to -- otherwise a stale or mismatched region could
    # be silently carried over to a different station.
    if metadata.latitude != point.latitude or metadata.longitude != point.longitude:
        raise EvidenceError("REGION_MEMBERSHIP_COORDINATE_MISMATCH")
    if office.office != point.cwa:
        raise EvidenceError("REGION_MEMBERSHIP_OFFICE_CHAIN_MISMATCH")

    # Conservative, interpretable defaults built only from evidence already on
    # hand: the real NWS macro-region as a synoptic-exposure proxy, and the
    # station's own certified real provider identities as source/model
    # dependence groups. An empty provider set is a genuine unknown, not
    # something this layer may default or guess around.
    if weather_groups is None:
        weather_groups = (office.nws_region,)
    if source_groups is None:
        source_groups = tuple(sorted(set(metadata.observation_providers)))
    if model_groups is None:
        model_groups = tuple(sorted(set(metadata.forecast_providers)))

    return StationMembership(
        station=metadata.station,
        city=metadata.city,
        region=office.nws_region,
        weather_groups=weather_groups,
        source_groups=source_groups,
        model_groups=model_groups,
        metadata_fingerprint=metadata.fingerprint,
    )
