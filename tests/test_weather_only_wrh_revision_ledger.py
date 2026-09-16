from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from polymarket_scanner.weather_only_contracts import compile_weather_event
from polymarket_scanner.weather_only_rules import apply_rule_authority, compile_temperature_rule_authority
from polymarket_scanner.weather_only_wrh import parse_synoptic_wrh_hourly_snapshot
from polymarket_scanner.weather_only_wrh_revision_ledger import (
    WRHRevisionLedgerError,
    build_wrh_revision_ledger,
)
from test_weather_only_rules import _nws_event
from test_weather_only_wrh import _payload


TARGET = date(2026, 9, 11)
ZONE = ZoneInfo("America/New_York")
T0 = datetime(2026, 9, 11, 14, 0, tzinfo=ZONE).timestamp()
AS_OF = datetime(2026, 9, 11, 14, 3, tzinfo=ZONE).timestamp()


def _compiled():
    event = _nws_event(high=True, hourly=True)
    raw = compile_weather_event(event)
    return apply_rule_authority(raw, compile_temperature_rule_authority(event, raw))


def _partial_payload(*, peak: float = 82.4):
    payload = deepcopy(_payload())
    observations = payload["STATION"][0]["OBSERVATIONS"]
    for values in observations.values():
        if isinstance(values, list):
            del values[3:]
    observations["air_temp_set_1"][2] = peak
    return payload


def _snapshot(payload, received_at):
    return parse_synoptic_wrh_hourly_snapshot(
        payload,
        station="KLGA",
        target_date=TARGET,
        query_start_date=TARGET,
        query_end_date=date(2026, 9, 12),
        received_at=received_at,
    )


def test_a_to_b_to_a_revision_history_is_preserved_but_final_row_counts_once():
    first = _snapshot(_partial_payload(peak=82.4), T0)
    second = _snapshot(_partial_payload(peak=84.4), T0 + 60)
    third = _snapshot(_partial_payload(peak=82.4), T0 + 120)

    ledger = build_wrh_revision_ledger(
        (first, second, third),
        _compiled(),
        as_of=AS_OF,
        max_snapshot_age_seconds=120,
    )
    assert ledger.active_observation_count == 3
    assert ledger.observed_asof.observed_state.observation_count == 3
    assert ledger.observed_asof.observed_state.extreme_value == 82.0
    assert len(ledger.active_revision_sha256s) == 3
    # Three initial observations + B version + restored A version.
    assert len(ledger.revisions) == 5
    assert ledger.superseded_revision_count == 2
    assert ledger.tombstone_count == 0

    revised = [revision for revision in ledger.revisions if revision.version_number > 1]
    assert [revision.version_number for revision in revised] == [2, 3]
    assert [revision.displayed_temp_f for revision in revised] == [84, 82]
    assert all(revision.revision_sha256 for revision in revised)
    assert ledger.polling_history_complete_between_snapshots is False
    assert ledger.settlement_authority is False
    assert ledger.calibration_label_authority is False
    assert ledger.financial_authority is False


def test_disappearing_source_row_gets_tombstone_and_is_removed_from_o_t():
    first_payload = _partial_payload(peak=82.4)
    second_payload = _partial_payload(peak=82.4)
    observations = second_payload["STATION"][0]["OBSERVATIONS"]
    for values in observations.values():
        if isinstance(values, list):
            values.pop(2)

    first = _snapshot(first_payload, T0)
    second = _snapshot(second_payload, T0 + 60)
    ledger = build_wrh_revision_ledger(
        (first, second),
        _compiled(),
        as_of=T0 + 90,
        max_snapshot_age_seconds=60,
    )
    assert ledger.tombstone_count == 1
    assert ledger.active_observation_count == 2
    assert ledger.observed_asof.observed_state.observation_count == 2
    assert ledger.observed_asof.observed_state.extreme_value == 81.0
    tombstone = next(revision for revision in ledger.revisions if not revision.present)
    assert tombstone.displayed_temp_f is None
    assert tombstone.row_state_sha256 is None
    assert tombstone.supersedes_revision_sha256 is not None


def test_late_appearing_row_becomes_one_new_active_observation_not_duplicate_history():
    first_payload = _partial_payload()
    observations = first_payload["STATION"][0]["OBSERVATIONS"]
    for values in observations.values():
        if isinstance(values, list):
            values.pop(2)
    first = _snapshot(first_payload, T0)
    second = _snapshot(_partial_payload(), T0 + 60)
    ledger = build_wrh_revision_ledger(
        (first, second),
        _compiled(),
        as_of=T0 + 90,
        max_snapshot_age_seconds=60,
    )
    assert ledger.active_observation_count == 3
    assert ledger.observed_asof.observed_state.observation_count == 3
    assert len(ledger.revisions) == 3
    assert ledger.superseded_revision_count == 0


def test_out_of_order_or_future_snapshot_sequence_is_rejected():
    first = _snapshot(_partial_payload(), T0)
    second = _snapshot(_partial_payload(peak=83.4), T0 + 60)
    with pytest.raises(WRHRevisionLedgerError) as order:
        build_wrh_revision_ledger(
            (second, first), _compiled(), as_of=AS_OF, max_snapshot_age_seconds=120
        )
    assert order.value.code == "WRH_LEDGER_SNAPSHOT_ORDER_INVALID"

    with pytest.raises(WRHRevisionLedgerError) as future:
        build_wrh_revision_ledger(
            (first, second), _compiled(), as_of=T0 + 30, max_snapshot_age_seconds=120
        )
    assert future.value.code == "WRH_LEDGER_SNAPSHOT_AFTER_AS_OF"


def test_source_identity_drift_cannot_be_folded_into_one_revision_history():
    first = _snapshot(_partial_payload(), T0)
    payload = _partial_payload()
    payload["STATION"][0]["SHORTNAME"] = "ASOS/AWOS"
    second = _snapshot(payload, T0 + 60)
    with pytest.raises(WRHRevisionLedgerError) as raised:
        build_wrh_revision_ledger(
            (first, second), _compiled(), as_of=AS_OF, max_snapshot_age_seconds=120
        )
    assert raised.value.code == "WRH_LEDGER_SOURCE_IDENTITY_DRIFT"
