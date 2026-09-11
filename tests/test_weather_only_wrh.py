from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime

import pytest

from polymarket_scanner.weather_only_wrh import (
    ROW_NONFED_MINUTE,
    ROW_OFFICIAL_PRESSURE,
    ROW_SPECI,
    WRHSourceError,
    js_math_round,
    parse_synoptic_wrh_hourly_snapshot,
)


TARGET = date(2026, 9, 11)
START = date(2026, 9, 11)
END = date(2026, 9, 12)


def _payload(*, network: str = "GLOBAL-METAR", include_slp: bool = True) -> dict:
    observations = {
        "date_time": [
            "2026-09-11T00:51:00-04:00",
            "2026-09-11T12:51:00-04:00",
            "2026-09-11T13:20:00-04:00",
            "2026-09-11T13:30:00-04:00",
            "2026-09-11T23:59:00-04:00",
            "2026-09-12T00:51:00-04:00",
        ],
        "air_temp_set_1": [70.4, 80.5, 82.4, 99.0, 79.4, 75.0],
        "metar_set_1": [
            "KLGA 110451Z AUTO ...",
            "KLGA 111651Z AUTO ...",
            "KLGA 111720Z SPECI ...",
            "KJFK 111730Z SPECI ...",
            "KLGA 120359Z AUTO ...",
            "KLGA 120451Z AUTO ...",
        ],
    }
    if include_slp:
        observations["sea_level_pressure_set_1"] = [1012.0, 1010.0, None, None, 1009.0, 1011.0]
    return {
        "SUMMARY": {"RESPONSE_MESSAGE": "OK"},
        "STATION": [{
            "STID": "KLGA",
            "SHORTNAME": network,
            "TIMEZONE": "America/New_York",
            "OBSERVATIONS": observations,
        }],
    }


def _parse(payload=None, **kwargs):
    values = {
        "station": "KLGA",
        "target_date": TARGET,
        "query_start_date": START,
        "query_end_date": END,
        "received_at": 1789160400.0,
    }
    values.update(kwargs)
    return parse_synoptic_wrh_hourly_snapshot(payload or _payload(), **values)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (-2.5, -2),
        (-1.5, -1),
        (-0.5, 0),
        (0.49, 0),
        (0.5, 1),
        (1.5, 2),
        (80.5, 81),
    ],
)
def test_js_math_round_matches_javascript_half_toward_positive_infinity(raw, expected):
    assert js_math_round(raw) == expected


def test_global_metar_normalizes_to_asos_and_mirrors_pressure_and_speci_predicate():
    snapshot = _parse()
    assert snapshot.raw_network == "GLOBAL-METAR"
    assert snapshot.normalized_network == "ASOS/AWOS"

    # 13:30 is excluded even at 99F because pressure is null and its METAR belongs
    # to KJFK. The 13:20 KLGA SPECI survives despite not being near minute 51-59.
    assert [(row.minute, row.row_kind) for row in snapshot.target_rows] == [
        (51, ROW_OFFICIAL_PRESSURE),
        (51, ROW_OFFICIAL_PRESSURE),
        (20, ROW_SPECI),
        (59, ROW_OFFICIAL_PRESSURE),
    ]
    assert snapshot.target_display_temperatures_f == (70, 81, 82, 79)
    assert snapshot.target_high_f == 82
    assert snapshot.target_low_f == 70
    assert snapshot.first_following_row is not None
    assert snapshot.first_following_row.local_date == date(2026, 9, 12)
    assert snapshot.first_following_row.row_kind == ROW_OFFICIAL_PRESSURE


def test_null_temperature_row_can_establish_hourly_row_presence_without_entering_extreme():
    payload = _payload()
    payload["STATION"][0]["OBSERVATIONS"]["air_temp_set_1"][-1] = None
    snapshot = _parse(payload)
    assert snapshot.first_following_row is not None
    assert snapshot.first_following_row.displayed_temp_f is None
    assert snapshot.target_high_f == 82


def test_missing_slp_dataset_uses_wrh_nonfed_minute_51_to_59_fallback_only():
    payload = _payload(include_slp=False)
    # Add an otherwise tempting minute-20 row and a minute-04 row; neither survives
    # the ASOS/AWOS no-pressure-dataset fallback, which is strictly 51..59.
    snapshot = _parse(payload)
    assert all(row.row_kind == ROW_NONFED_MINUTE for row in snapshot.selected_rows)
    assert [row.minute for row in snapshot.target_rows] == [51, 51, 59]
    assert snapshot.target_high_f == 81


def test_asos_awos_network_is_accepted_without_normalization():
    snapshot = _parse(_payload(network="ASOS/AWOS"))
    assert snapshot.raw_network == snapshot.normalized_network == "ASOS/AWOS"


def test_unsupported_network_fails_closed_instead_of_generalizing_other_wrh_predicates():
    with pytest.raises(WRHSourceError) as raised:
        _parse(_payload(network="RAWS"))
    assert raised.value.code == "WRH_NETWORK_UNSUPPORTED"


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (lambda p: p["SUMMARY"].update(RESPONSE_MESSAGE="ERROR"), "WRH_RESPONSE_NOT_OK"),
        (lambda p: p["STATION"][0].update(STID="KJFK"), "WRH_STATION_ID_MISMATCH"),
        (lambda p: p["STATION"][0].update(TIMEZONE="Not/AZone"), "WRH_TIMEZONE_INVALID"),
        (lambda p: p["STATION"][0]["OBSERVATIONS"]["air_temp_set_1"].pop(), "WRH_SERIES_LENGTH_MISMATCH"),
        (lambda p: p["STATION"][0]["OBSERVATIONS"]["sea_level_pressure_set_1"].pop(), "WRH_SLP_SERIES_LENGTH_MISMATCH"),
        (lambda p: p["STATION"][0]["OBSERVATIONS"]["metar_set_1"].pop(), "WRH_METAR_SERIES_LENGTH_MISMATCH"),
        (lambda p: p["STATION"][0]["OBSERVATIONS"]["date_time"].__setitem__(1, p["STATION"][0]["OBSERVATIONS"]["date_time"][0]), "WRH_OBSERVATION_TIME_DUPLICATE"),
        (lambda p: p["STATION"][0]["OBSERVATIONS"]["air_temp_set_1"].__setitem__(0, float("nan")), "WRH_TEMPERATURE_INVALID"),
        (lambda p: p["STATION"][0]["OBSERVATIONS"]["metar_set_1"].__setitem__(0, 123), "WRH_METAR_VALUE_INVALID"),
    ],
)
def test_payload_identity_and_series_corruption_fail_closed(mutator, code):
    payload = _payload()
    mutator(payload)
    with pytest.raises(WRHSourceError) as raised:
        _parse(payload)
    assert raised.value.code == code


def test_datetime_cannot_masquerade_as_calendar_contract_date():
    with pytest.raises(WRHSourceError) as raised:
        _parse(target_date=datetime(2026, 9, 11, 0, 0))
    assert raised.value.code == "WRH_TARGET_DATE_INVALID"


def test_query_must_cover_full_target_date_and_at_least_following_calendar_date():
    with pytest.raises(WRHSourceError) as raised:
        _parse(query_end_date=TARGET)
    assert raised.value.code == "WRH_QUERY_COVERAGE_INSUFFICIENT"

    with pytest.raises(WRHSourceError) as raised:
        _parse(query_start_date=date(2026, 9, 12))
    assert raised.value.code == "WRH_QUERY_COVERAGE_INSUFFICIENT"


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"viewer_script_url": "https://www.weather.gov/source/wrh/timeseries/obs.js?vOLD"}, "WRH_VIEWER_SCRIPT_URL_MISMATCH"),
        ({"viewer_script_sha256": "0" * 64}, "WRH_VIEWER_SCRIPT_SHA_MISMATCH"),
        ({"source_endpoint": "https://example.invalid"}, "WRH_SOURCE_ENDPOINT_MISMATCH"),
        ({"requested_unit": "C"}, "WRH_UNIT_UNSUPPORTED"),
        ({"complete": 0}, "WRH_COMPLETE_QUERY_REQUIRED"),
        ({"complete": True}, "WRH_COMPLETE_QUERY_REQUIRED"),
        ({"obtimezone": "utc"}, "WRH_LOCAL_TIMEZONE_QUERY_REQUIRED"),
    ],
)
def test_source_transport_identity_is_pinned_and_fails_closed_on_drift(overrides, code):
    with pytest.raises(WRHSourceError) as raised:
        _parse(**overrides)
    assert raised.value.code == code


def test_snapshot_binds_unselected_source_inputs_and_never_self_promotes_to_label_authority():
    first = _parse()
    payload = deepcopy(_payload())
    # Change the excluded KJFK SPECI row only. The selected target high remains 82F,
    # but the exact source-payload/evidence lineage must still change.
    payload["STATION"][0]["OBSERVATIONS"]["air_temp_set_1"][3] = 98.0
    second = _parse(payload)

    assert first.target_high_f == second.target_high_f == 82
    assert first.target_state_sha256 == second.target_state_sha256
    assert first.source_payload_sha256 != second.source_payload_sha256
    assert first.evidence_sha256 != second.evidence_sha256

    assert first.transport_semantics_certified is True
    assert first.exact_wrh_snapshot is True
    assert first.correction_state_reconstructable is False
    assert first.calibration_label_authority is False
    assert first.settlement_label_authority is False
    assert first.financial_authority is False


def test_target_state_digest_changes_when_selected_target_state_changes():
    first = _parse()
    payload = deepcopy(_payload())
    payload["STATION"][0]["OBSERVATIONS"]["air_temp_set_1"][2] = 83.4
    second = _parse(payload)
    assert first.target_high_f == 82
    assert second.target_high_f == 83
    assert first.target_state_sha256 != second.target_state_sha256
    assert first.evidence_sha256 != second.evidence_sha256
