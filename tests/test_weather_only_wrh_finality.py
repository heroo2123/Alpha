from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime

import pytest

from polymarket_scanner.weather_only_wrh import WRHSourceError, parse_synoptic_wrh_hourly_snapshot
from polymarket_scanner.weather_only_wrh_finality import (
    WRHFinalityPolicy,
    certify_wrh_first_following_transition,
)


TARGET = date(2026, 9, 11)
START = date(2026, 9, 11)
END = date(2026, 9, 12)
FOLLOWING = datetime.fromisoformat("2026-09-12T00:51:00-04:00").timestamp()


def _payload(*, network: str = "GLOBAL-METAR", include_following: bool = True) -> dict:
    observations = {
        "date_time": [
            "2026-09-11T00:51:00-04:00",
            "2026-09-11T12:51:00-04:00",
            "2026-09-11T13:20:00-04:00",
            "2026-09-11T23:59:00-04:00",
            "2026-09-12T00:51:00-04:00",
        ],
        "air_temp_set_1": [70.4, 80.5, 82.4, 79.4, 75.0],
        "metar_set_1": [
            "KLGA 110451Z AUTO ...",
            "KLGA 111651Z AUTO ...",
            "KLGA 111720Z SPECI ...",
            "KLGA 120359Z AUTO ...",
            "KLGA 120451Z AUTO ...",
        ],
        "sea_level_pressure_set_1": [1012.0, 1010.0, None, 1009.0, 1011.0],
    }
    if not include_following:
        for values in observations.values():
            values.pop()
    return {
        "SUMMARY": {"RESPONSE_MESSAGE": "OK"},
        "STATION": [{
            "STID": "KLGA",
            "SHORTNAME": network,
            "TIMEZONE": "America/New_York",
            "OBSERVATIONS": observations,
        }],
    }


def _snapshot(*, include_following: bool, received_at: float, payload: dict | None = None, network: str = "GLOBAL-METAR"):
    return parse_synoptic_wrh_hourly_snapshot(
        payload if payload is not None else _payload(network=network, include_following=include_following),
        station="KLGA",
        target_date=TARGET,
        query_start_date=START,
        query_end_date=END,
        received_at=received_at,
    )


def _policy(*, gap: int = 120, age: int = 60) -> WRHFinalityPolicy:
    return WRHFinalityPolicy(
        policy_id="wrh-test-transition-v1",
        max_transition_gap_seconds=gap,
        max_following_row_age_seconds=age,
    )


def _valid_pair():
    previous = _snapshot(include_following=False, received_at=FOLLOWING - 30)
    current = _snapshot(include_following=True, received_at=FOLLOWING + 20)
    return previous, current


def test_bracketed_unchanged_transition_certifies_exact_rule_state_without_financial_authority():
    previous, current = _valid_pair()
    state = certify_wrh_first_following_transition(previous, current, policy=_policy())

    assert state.station == "KLGA"
    assert state.target_date == TARGET
    assert state.target_high_f == 82
    assert state.target_low_f == 70
    assert state.target_display_temperatures_f == (70, 81, 82, 79)
    assert state.target_state_sha256 == previous.target_state_sha256 == current.target_state_sha256
    assert state.previous_snapshot_sha256 == previous.evidence_sha256
    assert state.current_snapshot_sha256 == current.evidence_sha256
    assert state.transition_gap_seconds == pytest.approx(50.0)
    assert state.first_following_row_age_seconds == pytest.approx(20.0)
    assert len(state.finality_evidence_sha256) == 64
    assert state.correction_state_reconstructable is True
    assert state.calibration_label_authority is True
    assert state.settlement_label_authority is True
    assert state.financial_authority is False


def test_target_state_change_across_cutoff_fails_closed_even_if_extreme_stays_same():
    previous = _snapshot(include_following=False, received_at=FOLLOWING - 30)
    payload = deepcopy(_payload(include_following=True))
    # Change an eligible target-day row without changing the 82F daily high.
    payload["STATION"][0]["OBSERVATIONS"]["air_temp_set_1"][0] = 71.4
    current = _snapshot(include_following=True, received_at=FOLLOWING + 20, payload=payload)

    with pytest.raises(WRHSourceError) as raised:
        certify_wrh_first_following_transition(previous, current, policy=_policy())
    assert raised.value.code == "WRH_FINALITY_TARGET_STATE_CHANGED_ACROSS_CUTOFF"


def test_previous_snapshot_that_already_contains_following_row_is_rejected():
    previous = _snapshot(include_following=True, received_at=FOLLOWING - 30)
    current = _snapshot(include_following=True, received_at=FOLLOWING + 20)
    with pytest.raises(WRHSourceError) as raised:
        certify_wrh_first_following_transition(previous, current, policy=_policy())
    assert raised.value.code == "WRH_FINALITY_PREVIOUS_ALREADY_CROSSED"


def test_current_snapshot_without_following_row_is_rejected():
    previous = _snapshot(include_following=False, received_at=FOLLOWING - 30)
    current = _snapshot(include_following=False, received_at=FOLLOWING + 20)
    with pytest.raises(WRHSourceError) as raised:
        certify_wrh_first_following_transition(previous, current, policy=_policy())
    assert raised.value.code == "WRH_FINALITY_FOLLOWING_ROW_NOT_OBSERVED"


def test_source_identity_drift_across_transition_is_rejected():
    previous = _snapshot(include_following=False, received_at=FOLLOWING - 30, network="GLOBAL-METAR")
    current = _snapshot(include_following=True, received_at=FOLLOWING + 20, network="ASOS/AWOS")
    with pytest.raises(WRHSourceError) as raised:
        certify_wrh_first_following_transition(previous, current, policy=_policy())
    assert raised.value.code == "WRH_FINALITY_SOURCE_IDENTITY_MISMATCH"


def test_tampered_snapshot_evidence_digest_is_rejected_before_finality():
    previous, current = _valid_pair()
    previous = replace(previous, evidence_sha256="f" * 64)
    with pytest.raises(WRHSourceError) as raised:
        certify_wrh_first_following_transition(previous, current, policy=_policy())
    assert raised.value.code == "WRH_PREVIOUS_EVIDENCE_DIGEST_MISMATCH"


def test_polling_gap_must_be_bounded():
    previous = _snapshot(include_following=False, received_at=FOLLOWING - 150)
    current = _snapshot(include_following=True, received_at=FOLLOWING + 20)
    with pytest.raises(WRHSourceError) as raised:
        certify_wrh_first_following_transition(previous, current, policy=_policy(gap=120, age=60))
    assert raised.value.code == "WRH_FINALITY_TRANSITION_GAP_EXCEEDED"


def test_previous_poll_must_precede_following_observation_timestamp():
    previous = _snapshot(include_following=False, received_at=FOLLOWING + 1)
    current = _snapshot(include_following=True, received_at=FOLLOWING + 20)
    with pytest.raises(WRHSourceError) as raised:
        certify_wrh_first_following_transition(previous, current, policy=_policy())
    assert raised.value.code == "WRH_FINALITY_PREVIOUS_NOT_BEFORE_FOLLOWING_OBSERVATION"


def test_current_snapshot_cannot_claim_a_following_row_from_the_future():
    previous = _snapshot(include_following=False, received_at=FOLLOWING - 30)
    current = _snapshot(include_following=True, received_at=FOLLOWING - 1)
    with pytest.raises(WRHSourceError) as raised:
        certify_wrh_first_following_transition(previous, current, policy=_policy())
    assert raised.value.code == "WRH_FINALITY_CURRENT_PREDATES_FOLLOWING_OBSERVATION"


def test_following_row_capture_must_be_prompt_not_stale():
    previous = _snapshot(include_following=False, received_at=FOLLOWING - 30)
    current = _snapshot(include_following=True, received_at=FOLLOWING + 61)
    with pytest.raises(WRHSourceError) as raised:
        certify_wrh_first_following_transition(previous, current, policy=_policy(gap=120, age=60))
    assert raised.value.code == "WRH_FINALITY_FOLLOWING_ROW_TOO_OLD"


def test_snapshot_time_order_is_strict():
    previous = _snapshot(include_following=False, received_at=FOLLOWING - 10)
    current = _snapshot(include_following=True, received_at=FOLLOWING - 20)
    with pytest.raises(WRHSourceError) as raised:
        certify_wrh_first_following_transition(previous, current, policy=_policy())
    assert raised.value.code == "WRH_FINALITY_SNAPSHOT_TIME_ORDER_INVALID"


def test_empty_target_temperature_state_cannot_become_an_exact_temperature_label():
    before_payload = _payload(include_following=False)
    after_payload = _payload(include_following=True)
    for payload in (before_payload, after_payload):
        observations = payload["STATION"][0]["OBSERVATIONS"]
        limit = 4 if len(observations["air_temp_set_1"]) == 5 else len(observations["air_temp_set_1"])
        for index in range(limit):
            observations["air_temp_set_1"][index] = None

    previous = _snapshot(include_following=False, received_at=FOLLOWING - 30, payload=before_payload)
    current = _snapshot(include_following=True, received_at=FOLLOWING + 20, payload=after_payload)
    with pytest.raises(WRHSourceError) as raised:
        certify_wrh_first_following_transition(previous, current, policy=_policy())
    assert raised.value.code == "WRH_FINALITY_TARGET_TEMPERATURE_DATA_MISSING"


def test_policy_rejects_ambiguous_or_nonpositive_limits():
    with pytest.raises(ValueError):
        WRHFinalityPolicy(" x ", 120, 60)
    with pytest.raises(ValueError):
        WRHFinalityPolicy("x", True, 60)
    with pytest.raises(ValueError):
        WRHFinalityPolicy("x", 120, 0)
