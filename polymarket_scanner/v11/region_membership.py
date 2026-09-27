"""Bind station metadata's real coordinates to a real NWS region for I2.

Master upgrade I2 requires a "versioned mapping or clustering layer" covering
station -> city, city/station -> region and common model/source dependence,
feeding the regional/correlated-exposure ceilings in ``scenario_risk``. This
module builds per-station membership from schema-validated metadata and
receipt-bound public evidence.
StationMetadata alone is not proof of protected station certification:

- ``StationMetadata`` (v11/certification.py) already carries the station's real
  city, coordinates and real observation/forecast provider identities;
- ``NWSPointOffice``/``NWSOfficeRegion`` (weather_only_station_region.py) carry
  the real public NWS county-warning-area and regional-headquarters assignment
  for its rounded query coordinate while preserving the exact request identity.

NWS regions are administrative identity, not weather-system boundaries.
Provider names do not establish independent upstream sources or models. Shared
UNKNOWN groups conservatively aggregate unresolved dependence; optional groups
can only add constraints. Protected review and runtime admission remain separate.
"""
from __future__ import annotations

from .certification import StationMetadata
from .evidence import EvidenceError, identity
from .scenario_risk import StationMembership
from ..weather_only_station_region import NWSOfficeRegion, NWSPointOffice, WeatherStationRegionError

VERSION = "alpha_v11_region_membership_v2"
UNKNOWN_WEATHER = "SHARED_UNKNOWN_WEATHER"
UNKNOWN_SOURCE = "SHARED_UNKNOWN_SOURCE"
UNKNOWN_MODEL = "SHARED_UNKNOWN_MODEL"


def _groups(required: tuple[str, ...], extra: tuple[str, ...] | None) -> tuple[str, ...]:
    if extra is not None:
        if type(extra) is not tuple or not 1 <= len(extra) <= 16:
            raise EvidenceError("REGION_MEMBERSHIP_ADDITIONAL_GROUPS_INVALID")
        for value in extra:
            identity(value)
    return tuple(sorted(set(required + (extra or ()))))


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
    try:
        point.validate()
        office.validate()
    except WeatherStationRegionError as exc:
        raise EvidenceError("REGION_MEMBERSHIP_REGION_EVIDENCE_INVALID:" + exc.code) from None
    # The point query must have been made against this exact metadata record's
    # own real coordinates, and the office fetched must be the same office the
    # point actually resolved to -- otherwise a stale or mismatched region could
    # be silently carried over to a different station.
    if metadata.latitude != point.latitude or metadata.longitude != point.longitude:
        raise EvidenceError("REGION_MEMBERSHIP_COORDINATE_MISMATCH")
    if office.office != point.cwa:
        raise EvidenceError("REGION_MEMBERSHIP_OFFICE_CHAIN_MISMATCH")

    # Metadata declares provider identities, not their upstream independence.
    # Overrides are additive, so known/common and unresolved risks cannot vanish.
    if not metadata.observation_providers or not metadata.forecast_providers:
        raise EvidenceError("REGION_MEMBERSHIP_PROVIDER_EVIDENCE_REQUIRED")
    weather_groups = _groups((UNKNOWN_WEATHER,), weather_groups)
    source_groups = _groups((UNKNOWN_SOURCE,) + metadata.observation_providers + metadata.forecast_providers,
                            source_groups)
    model_groups = _groups((UNKNOWN_MODEL,) + metadata.forecast_providers, model_groups)

    return StationMembership(
        station=metadata.station,
        city=metadata.city,
        region=office.nws_region,
        weather_groups=weather_groups,
        source_groups=source_groups,
        model_groups=model_groups,
        metadata_fingerprint=metadata.fingerprint,
    )
