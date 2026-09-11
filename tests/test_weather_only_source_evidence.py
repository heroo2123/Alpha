from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from polymarket_scanner.weather_only_calibration import (
    SETTLEMENT_LABEL_EVIDENCE_VERSION,
    CalibrationPolicy,
    ProbabilityCalibrationSample,
    assess_probability_calibration,
)
from polymarket_scanner.weather_only_sources import (
    NWSObservationProxyClient,
    WeatherSourceError,
    parse_nws_station_observation,
)


def _nws_feature(
    *,
    station: str = "KLGA",
    timestamp: str = "2026-09-11T13:51:00+00:00",
    value=24.4,
    unit: str = "wmoUnit:degC",
) -> dict:
    return {
        "id": f"https://api.weather.gov/stations/{station}/observations/2026-09-11T13:51:00+00:00",
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [-73.88, 40.77]},
        "properties": {
            "station": f"https://api.weather.gov/stations/{station}",
            "timestamp": timestamp,
            "rawMessage": f"{station} 111351Z TEST",
            "temperature": {"unitCode": unit, "value": value, "qualityControl": "V"},
        },
    }


def test_official_nws_api_observation_is_explicit_proxy_not_settlement_label():
    row = parse_nws_station_observation(_nws_feature(), requested_station="KLGA")
    assert row.station == "KLGA"
    assert row.temperature_c == 24.4
    assert row.observed_at.tzinfo is not None
    assert row.official_source is True
    assert row.source_role == "OFFICIAL_NWS_OBSERVATION_PROXY_ONLY"
    assert row.settlement_authority is False
    assert row.calibration_label_authority is False
    assert row.correction_state_reconstructable is False
    assert row.financial_authority is False


@pytest.mark.parametrize(
    ("feature", "station", "code"),
    [
        (_nws_feature(station="KJFK"), "KLGA", "NWS_OBSERVATION_STATION_MISMATCH"),
        (_nws_feature(timestamp="2026-09-11T13:51:00"), "KLGA", "NWS_OBSERVATION_TIMESTAMP_NAIVE"),
        (_nws_feature(value=None), "KLGA", "NWS_OBSERVATION_TEMPERATURE_MISSING"),
        (_nws_feature(unit="wmoUnit:degF"), "KLGA", "NWS_OBSERVATION_TEMPERATURE_UNIT_UNSUPPORTED"),
    ],
)
def test_nws_observation_parser_fails_closed_on_identity_time_temperature_or_unit(feature, station, code):
    with pytest.raises(WeatherSourceError) as raised:
        parse_nws_station_observation(feature, requested_station=station)
    assert raised.value.code == code


def test_nws_observation_client_is_bounded_and_preserves_proxy_authority():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={
            "type": "FeatureCollection",
            "features": [
                _nws_feature(timestamp="2026-09-11T13:51:00Z"),
                _nws_feature(timestamp="2026-09-11T14:51:00Z"),
            ],
        })

    async def run():
        client = NWSObservationProxyClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await client.observations(
                station="KLGA",
                start=datetime(2026, 9, 11, 12, tzinfo=timezone.utc),
                end=datetime(2026, 9, 11, 16, tzinfo=timezone.utc),
                limit=10,
            )
        finally:
            await client.close()

    rows = asyncio.run(run())
    assert len(rows) == 2
    assert all(row.settlement_authority is False for row in rows)
    assert all(row.calibration_label_authority is False for row in rows)
    request = seen[0]
    assert request.url.path == "/stations/KLGA/observations"
    assert request.url.params.get("limit") == "10"
    assert request.url.params.get("start").endswith("Z")
    assert request.url.params.get("end").endswith("Z")


def test_nws_client_rejects_duplicate_observation_timestamp():
    feature = _nws_feature(timestamp="2026-09-11T13:51:00Z")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"type": "FeatureCollection", "features": [feature, feature]})

    async def run():
        client = NWSObservationProxyClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await client.observations(
                station="KLGA",
                start=datetime(2026, 9, 11, 12, tzinfo=timezone.utc),
                end=datetime(2026, 9, 11, 16, tzinfo=timezone.utc),
                limit=10,
            )
        finally:
            await client.close()

    with pytest.raises(WeatherSourceError) as raised:
        asyncio.run(run())
    assert raised.value.code == "NWS_OBSERVATION_DUPLICATE_TIMESTAMP"


def _policy() -> CalibrationPolicy:
    return CalibrationPolicy(
        policy_id="fixture-policy-v1",
        probability_bins=((0.0, 0.8), (0.8, 1.0)),
        min_total_resolved=4,
        min_bin_resolved=4,
        min_distinct_stations=2,
        max_brier_score=0.05,
        wilson_z=1.96,
    )


def _sample(
    index: int,
    *,
    station: str | None = None,
    model_version: str = "model-v1",
    predicted_probability: float = 0.9,
    final_payout: float = 1.0,
    label_adapter: str = "NWS_WRH_EXACT_RULE_STATE_V1",
    source_role: str = "SETTLEMENT_RULE_STATE",
    evidence_version: str = SETTLEMENT_LABEL_EVIDENCE_VERSION,
    label_authority: bool = True,
    reconstructable: bool = True,
) -> ProbabilityCalibrationSample:
    return ProbabilityCalibrationSample(
        event_id=f"event-{index}",
        station=station or ("KLGA" if index % 2 else "KJFK"),
        model_version=model_version,
        predicted_probability=predicted_probability,
        final_payout=final_payout,
        label_adapter=label_adapter,
        source_role=source_role,
        evidence_version=evidence_version,
        label_authority=label_authority,
        settlement_state_reconstructable=reconstructable,
    )


def test_calibration_policy_must_cover_probability_space_without_gaps():
    with pytest.raises(ValueError):
        CalibrationPolicy("bad", ((0.0, 0.4), (0.5, 1.0)), 10, 5, 2, 0.1, 1.96)
    with pytest.raises(ValueError):
        CalibrationPolicy("bad", ((0.1, 1.0),), 10, 5, 2, 0.1, 1.96)


def test_large_proxy_dataset_cannot_mature_clean_calibration_even_if_marked_authoritative():
    samples = [
        _sample(
            i,
            label_adapter="NWS_API_STATION_OBSERVATION_V1_OFFICIAL_PROXY",
            source_role="OFFICIAL_NWS_OBSERVATION_PROXY_ONLY",
            label_authority=True,
            reconstructable=True,
        )
        for i in range(200)
    ]
    assessment = assess_probability_calibration(
        samples,
        model_version="model-v1",
        target_probability=0.9,
        policy=_policy(),
    )
    assert assessment.clean_total_resolved == 0
    assert dict(assessment.excluded_counts)["PROXY_LABEL_EXCLUDED"] == 200
    assert assessment.research_calibration_ready is False
    assert assessment.financial_authority is False


def test_wrong_evidence_version_and_unreconstructable_labels_are_excluded():
    samples = [
        _sample(1, evidence_version="LEGACY"),
        _sample(2, reconstructable=False),
        _sample(3, label_authority=False),
        _sample(4, model_version="other-model"),
    ]
    assessment = assess_probability_calibration(
        samples,
        model_version="model-v1",
        target_probability=0.9,
        policy=_policy(),
    )
    excluded = dict(assessment.excluded_counts)
    assert excluded["LABEL_EVIDENCE_VERSION_MISMATCH"] == 1
    assert excluded["SETTLEMENT_STATE_NOT_RECONSTRUCTABLE"] == 1
    assert excluded["LABEL_AUTHORITY_FALSE"] == 1
    assert excluded["MODEL_VERSION_MISMATCH"] == 1
    assert assessment.clean_total_resolved == 0
    assert assessment.research_calibration_ready is False


def test_duplicate_event_label_is_not_double_counted():
    first = _sample(1)
    duplicate = ProbabilityCalibrationSample(
        event_id=first.event_id,
        station="KJFK",
        model_version=first.model_version,
        predicted_probability=first.predicted_probability,
        final_payout=first.final_payout,
        label_adapter=first.label_adapter,
        source_role=first.source_role,
        evidence_version=first.evidence_version,
        label_authority=True,
        settlement_state_reconstructable=True,
    )
    assessment = assess_probability_calibration(
        [first, duplicate],
        model_version="model-v1",
        target_probability=0.9,
        policy=_policy(),
    )
    assert assessment.clean_total_resolved == 1
    assert dict(assessment.excluded_counts)["DUPLICATE_EVENT_LABEL"] == 1


def test_clean_preregistered_samples_can_pass_research_gate_but_never_financial_authority():
    assessment = assess_probability_calibration(
        [_sample(i) for i in range(1, 5)],
        model_version="model-v1",
        target_probability=0.9,
        policy=_policy(),
    )
    assert assessment.clean_total_resolved == 4
    assert assessment.clean_bin_resolved == 4
    assert assessment.distinct_stations == 2
    assert assessment.overall_brier_score == pytest.approx(0.01)
    assert assessment.bin_empirical_payout == 1.0
    assert assessment.bin_wilson_lower_bound is not None
    assert assessment.research_calibration_ready is True
    assert assessment.reasons == ()
    assert assessment.financial_authority is False
