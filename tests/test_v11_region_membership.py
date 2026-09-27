from __future__ import annotations

from dataclasses import replace

import pytest

from polymarket_scanner.v11 import certification as cert
from polymarket_scanner.v11.evidence import EvidenceError, digest
from polymarket_scanner.v11.region_membership import (
    build_station_membership, UNKNOWN_WEATHER, UNKNOWN_SOURCE, UNKNOWN_MODEL,
)
from polymarket_scanner.weather_only_station_region import parse_nws_office_region, parse_nws_point_office
from test_weather_only_station_region import _point_payload, _office_payload


def _metadata(**overrides):
    kw = dict(station='KDEN', city='Denver', country='US', latitude=39.8466, longitude=-104.6562,
              elevation_m=1650.0, timezone='America/Denver', settlement_source='NOAA_WRH',
              observation_providers=('NOAA',), forecast_providers=('GEFS',),
              source_payload_sha256=digest({'x': 1}), retrieved_at=1000.0)
    kw.update(overrides)
    return cert.StationMetadata(**kw)


def _point(*, latitude=39.8466, longitude=-104.6562, cwa="BOU"):
    return parse_nws_point_office(
        _point_payload(lat=round(latitude, 4), lon=round(longitude, 4), cwa=cwa),
        requested_latitude=latitude, requested_longitude=longitude, received_at=1.0,
    )


def _office(*, office="BOU", region="cr"):
    return parse_nws_office_region(_office_payload(office=office, region=region),
                                   requested_office=office, received_at=1.0)


def test_build_station_membership_from_metadata_and_parsed_region_evidence():
    membership = build_station_membership(metadata=_metadata(), point=_point(), office=_office())
    assert membership.station == 'KDEN'
    assert membership.city == 'Denver'
    assert membership.region == 'CENTRAL'
    assert membership.weather_groups == (UNKNOWN_WEATHER,)
    assert membership.source_groups == tuple(sorted(('NOAA', 'GEFS', UNKNOWN_SOURCE)))
    assert membership.model_groups == tuple(sorted(('GEFS', UNKNOWN_MODEL)))
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


def test_exact_station_coordinates_survive_query_rounding():
    metadata = _metadata(latitude=39.84658, longitude=-104.65622)
    point = _point(latitude=metadata.latitude, longitude=metadata.longitude)
    result = build_station_membership(metadata=metadata, point=point, office=_office())
    assert result.metadata_fingerprint == metadata.fingerprint
    # Another station within the same rounded cell is still the wrong identity.
    with pytest.raises(EvidenceError, match="COORDINATE_MISMATCH"):
        build_station_membership(metadata=replace(metadata, latitude=39.84659), point=point, office=_office())


@pytest.mark.parametrize("target,changes", [
    ("office", {"nws_region": "MADE_UP", "nws_region_code": "zz"}),
    ("office", {"nws_region": "EASTERN", "nws_region_code": "er"}),
    ("office", {"source_url": "https://other.invalid/offices/BOU"}),
    ("office", {"received_at": 2.0}),
    ("point", {"latitude": 40.0}),
    ("point", {"cwa": "OUN"}),
    ("point", {"source_payload_sha256": "f" * 64}),
    ("point", {"adapter": "unknown"}),
])
def test_modified_region_records_fail_integrity(target, changes):
    point, office = _point(), _office()
    if target == "point":
        point = replace(point, **changes)
    else:
        office = replace(office, **changes)
    with pytest.raises(EvidenceError, match="REGION_EVIDENCE_INVALID"):
        build_station_membership(metadata=_metadata(), point=point, office=office)


@pytest.mark.parametrize("field", ["observation_providers", "forecast_providers"])
def test_group_overrides_cannot_supply_missing_provider_evidence(field):
    with pytest.raises(EvidenceError, match="PROVIDER_EVIDENCE_REQUIRED"):
        build_station_membership(metadata=_metadata(**{field: ()}), point=_point(), office=_office(),
                                 source_groups=("isolated",), model_groups=("isolated",))


def test_additional_groups_retain_known_and_unresolved_dependence():
    result = build_station_membership(metadata=_metadata(), point=_point(), office=_office(),
                                     weather_groups=("local",), source_groups=("local",), model_groups=("local",))
    assert set(result.weather_groups) == {UNKNOWN_WEATHER, "local"}
    assert set(result.source_groups) == {UNKNOWN_SOURCE, "GEFS", "NOAA", "local"}
    assert set(result.model_groups) == {UNKNOWN_MODEL, "GEFS", "local"}


@pytest.mark.parametrize("field", ["weather_groups", "source_groups", "model_groups"])
@pytest.mark.parametrize("value", [(), "isolated", (None,), tuple(str(i) for i in range(17))])
def test_invalid_additional_groups_fail_closed(field, value):
    with pytest.raises(EvidenceError):
        build_station_membership(metadata=_metadata(), point=_point(), office=_office(), **{field: value})


@pytest.mark.parametrize("ceiling,gate,unknown", [
    ("per_weather_group", "WEATHER", UNKNOWN_WEATHER),
    ("per_source_group", "SOURCE", UNKNOWN_SOURCE),
    ("per_model_group", "MODEL", UNKNOWN_MODEL),
])
def test_different_regions_and_provider_names_do_not_imply_independence(ceiling, gate, unknown):
    from test_v11_probability import rule
    from test_v11_scenario_risk import distinct_rule, limits, view, A
    from polymarket_scanner.v11.rules import RuleFingerprint
    from polymarket_scanner.v11.evidence import canonical
    from polymarket_scanner.v11.scenario_risk import CorrelationMap, Position, portfolio_risk

    metadata = (_metadata(), _metadata(station="KJFK", city="New York", latitude=40.63915,
                longitude=-73.76393, observation_providers=("other-observer",), forecast_providers=("other-model",)))
    memberships = tuple(build_station_membership(
        metadata=m, point=_point(latitude=m.latitude, longitude=m.longitude, cwa=cwa),
        office=_office(office=cwa, region=region), weather_groups=(m.station,),
        source_groups=(m.station,), model_groups=(m.station,),
    ) for m, cwa, region in zip(metadata, ("BOU", "OKX"), ("cr", "er")))
    views = []
    for r, m in zip((rule(), distinct_rule(rule())), metadata):
        payload = r.payload
        payload.update(station=m.station, city=m.city, metadata_fingerprint=m.fingerprint)
        r = RuleFingerprint(canonical(payload), digest(payload), r.source_event_sha256)
        views.append(view(r, (Position("one", r.payload["partition"][0]["yes_token"], "10", "6", A),)))
    result = portfolio_risk(tuple(views), correlation=CorrelationMap("review-v2", "a"*64, memberships),
                            limits=limits(**{ceiling: "10"}), execution_namespace="V11_PAPER", account_id="account")
    assert not result["accepted"]
    assert result["groups"][gate][unknown] == "12"
    assert any(f["gate"] == gate+"_LOSS_LIMIT" and f["group"] == unknown for f in result["faults"])
