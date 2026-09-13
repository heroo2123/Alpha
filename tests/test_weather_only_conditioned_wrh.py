from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from polymarket_scanner.weather_only_conditioned_wrh import (
    ConditionedWRHError,
    build_wrh_observed_extreme_asof,
)
from polymarket_scanner.weather_only_contracts import compile_weather_event
from polymarket_scanner.weather_only_rules import apply_rule_authority, compile_temperature_rule_authority
from polymarket_scanner.weather_only_wrh import parse_synoptic_wrh_hourly_snapshot
from test_weather_only_rules import _nws_event
from test_weather_only_wrh import _payload


TARGET = date(2026, 9, 11)
ZONE = ZoneInfo("America/New_York")
RECEIVED = datetime(2026, 9, 11, 14, 0, tzinfo=ZONE).timestamp()
AS_OF = datetime(2026, 9, 11, 14, 1, tzinfo=ZONE).timestamp()


def _compiled(*, high=True):
    event = _nws_event(high=high, hourly=True)
    raw = compile_weather_event(event)
    authority = compile_temperature_rule_authority(event, raw)
    return apply_rule_authority(raw, authority)


def _partial_payload():
    payload = deepcopy(_payload())
    observations = payload["STATION"][0]["OBSERVATIONS"]
    # Keep 00:51, 12:51 and the eligible station-prefixed 13:20 SPECI only.
    for name, values in observations.items():
        if isinstance(values, list):
            del values[3:]
    return payload


def _snapshot(payload=None, *, received_at=RECEIVED):
    return parse_synoptic_wrh_hourly_snapshot(
        payload or _partial_payload(),
        station="KLGA",
        target_date=TARGET,
        query_start_date=TARGET,
        query_end_date=date(2026, 9, 12),
        received_at=received_at,
    )


def test_layer1_uses_only_pinned_displayed_wrh_rows_already_known_as_of():
    snapshot = _snapshot()
    evidence = build_wrh_observed_extreme_asof(
        snapshot,
        _compiled(high=True),
        as_of=AS_OF,
        max_snapshot_age_seconds=120,
    )
    assert evidence.station == "KLGA"
    assert evidence.target_date == TARGET.isoformat()
    assert evidence.family == "DAILY_HIGH"
    assert evidence.unit == "F"
    assert evidence.accepted_row_count == 3
    assert evidence.observed_state.observation_count == 3
    # WRH viewer rounding is 70, 81, 82; Layer 1 high is therefore 82F.
    assert evidence.observed_state.extreme_value == 82.0
    assert evidence.population_id.startswith("WRH_HOURLY_DATA:")
    assert evidence.source_snapshot_sha256 == snapshot.evidence_sha256
    assert len(evidence.evidence_sha256) == 64
    assert evidence.settlement_authority is False
    assert evidence.financial_authority is False


def test_layer1_low_uses_same_exact_population_without_reinterpreting_source():
    evidence = build_wrh_observed_extreme_asof(
        _snapshot(),
        _compiled(high=False),
        as_of=AS_OF,
        max_snapshot_age_seconds=120,
    )
    assert evidence.family == "DAILY_LOW"
    assert evidence.observed_state.extreme_value == 70.0


def test_stale_snapshot_cannot_drive_same_day_observed_state():
    stale_as_of = datetime(2026, 9, 11, 14, 10, tzinfo=ZONE).timestamp()
    with pytest.raises(ConditionedWRHError) as raised:
        build_wrh_observed_extreme_asof(
            _snapshot(),
            _compiled(),
            as_of=stale_as_of,
            max_snapshot_age_seconds=60,
        )
    assert raised.value.code == "CONDITIONED_WRH_SNAPSHOT_STALE"


def test_snapshot_received_after_decision_time_is_lookahead():
    before_receipt = datetime(2026, 9, 11, 13, 59, tzinfo=ZONE).timestamp()
    with pytest.raises(ConditionedWRHError) as raised:
        build_wrh_observed_extreme_asof(
            _snapshot(),
            _compiled(),
            as_of=before_receipt,
            max_snapshot_age_seconds=300,
        )
    assert raised.value.code == "CONDITIONED_WRH_SNAPSHOT_LOOKAHEAD"


def test_contract_station_identity_cannot_be_substituted():
    compiled = replace(_compiled(), station_hint="KJFK")
    with pytest.raises(ConditionedWRHError) as raised:
        build_wrh_observed_extreme_asof(
            _snapshot(),
            compiled,
            as_of=AS_OF,
            max_snapshot_age_seconds=120,
        )
    assert raised.value.code == "CONDITIONED_WRH_STATION_MISMATCH"


def test_snapshot_target_date_must_equal_frozen_contract_date():
    compiled = replace(_compiled(), target_date=date(2026, 9, 12))
    with pytest.raises(ConditionedWRHError) as raised:
        build_wrh_observed_extreme_asof(
            _snapshot(),
            compiled,
            as_of=AS_OF,
            max_snapshot_age_seconds=120,
        )
    assert raised.value.code == "CONDITIONED_WRH_TARGET_DATE_MISMATCH"


def test_small_transport_future_skew_is_still_rejected_by_decision_asof_boundary():
    payload = _partial_payload()
    observations = payload["STATION"][0]["OBSERVATIONS"]
    observations["date_time"][-1] = "2026-09-11T14:02:00-04:00"
    observations["metar_set_1"][-1] = "KLGA 111802Z SPECI ..."
    # Parser permits <=5 minute source-clock skew relative to receipt, but Layer 1
    # must still reject a row later than the actual decision as-of time.
    snapshot = _snapshot(payload, received_at=RECEIVED)
    with pytest.raises(ConditionedWRHError) as raised:
        build_wrh_observed_extreme_asof(
            snapshot,
            _compiled(),
            as_of=AS_OF,
            max_snapshot_age_seconds=120,
        )
    assert raised.value.code == "CONDITIONED_WRH_ROW_AFTER_AS_OF"


def test_layer1_refuses_unproven_contract_authority_even_with_valid_source_snapshot():
    raw = compile_weather_event(_nws_event(high=True, hourly=True))
    assert raw.exactly_one_outcome_proven is False
    with pytest.raises(ConditionedWRHError) as raised:
        build_wrh_observed_extreme_asof(
            _snapshot(),
            raw,
            as_of=AS_OF,
            max_snapshot_age_seconds=120,
        )
    assert raised.value.code == "CONDITIONED_WRH_RULE_AUTHORITY_UNPROVEN"
