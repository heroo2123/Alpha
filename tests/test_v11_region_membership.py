from __future__ import annotations

import pytest

from polymarket_scanner.v11 import certification as cert
from polymarket_scanner.v11.evidence import EvidenceError, digest
from polymarket_scanner.v11.region_membership import build_station_membership
from polymarket_scanner.weather_only_station_region import NWSOfficeRegion, NWSPointOffice


def _metadata(**overrides):
    kw = dict(station='KDEN', city='Denver', country='US', latitude=39.8466, longitude=-104.6562,
              elevation_m=1650.0, timezone='America/Denver', settlement_source='NOAA_WRH',
              observation_providers=('NOAA',), forecast_providers=('GEFS',),
              source_payload_sha256=digest({'x': 1}), retrieved_at=1000.0)
    kw.update(overrides)
    return cert.StationMetadata(**kw)


def _point(**overrides):
    kw = dict(adapter='v', source='s', source_url='https://api.weather.gov/points/39.8466,-104.6562',
              latitude=39.8466, longitude=-104.6562, cwa='BOU',
              forecast_office='https://api.weather.gov/offices/BOU',
              received_at=1.0, source_payload_sha256=digest({'p': 1}), evidence_sha256=digest({'p': 2}))
    kw.update(overrides)
    return NWSPointOffice(**kw)


def _office(**overrides):
    kw = dict(adapter='v', source='s', source_url='https://api.weather.gov/offices/BOU', office='BOU',
              nws_region_code='cr', nws_region='CENTRAL', received_at=1.0,
              source_payload_sha256=digest({'o': 1}), evidence_sha256=digest({'o': 2}))
    kw.update(overrides)
    return NWSOfficeRegion(**kw)


def test_build_station_membership_from_real_certified_and_region_evidence():
    membership = build_station_membership(metadata=_metadata(), point=_point(), office=_office())
    assert membership.station == 'KDEN'
    assert membership.city == 'Denver'
    assert membership.region == 'CENTRAL'
    assert membership.weather_groups == ('CENTRAL',)
    assert membership.source_groups == ('NOAA',)
    assert membership.model_groups == ('GEFS',)
    assert membership.metadata_fingerprint == _metadata().fingerprint


def test_build_station_membership_coordinate_mismatch_fails_closed():
    with pytest.raises(EvidenceError) as raised:
        build_station_membership(metadata=_metadata(latitude=40.0), point=_point(), office=_office())
    assert str(raised.value) == 'REGION_MEMBERSHIP_COORDINATE_MISMATCH'


def test_build_station_membership_office_chain_mismatch_fails_closed():
    with pytest.raises(EvidenceError) as raised:
        build_station_membership(metadata=_metadata(), point=_point(cwa='OUN'), office=_office())
    assert str(raised.value) == 'REGION_MEMBERSHIP_OFFICE_CHAIN_MISMATCH'


def test_build_station_membership_empty_provider_group_fails_closed():
    with pytest.raises(EvidenceError):
        build_station_membership(metadata=_metadata(observation_providers=()), point=_point(), office=_office())


def test_build_station_membership_requires_typed_inputs():
    with pytest.raises(EvidenceError) as raised:
        build_station_membership(metadata='not-metadata', point=_point(), office=_office())
    assert str(raised.value) == 'REGION_MEMBERSHIP_METADATA_REQUIRED'
    with pytest.raises(EvidenceError) as raised:
        build_station_membership(metadata=_metadata(), point='not-point', office=_office())
    assert str(raised.value) == 'REGION_MEMBERSHIP_REGION_EVIDENCE_REQUIRED'
