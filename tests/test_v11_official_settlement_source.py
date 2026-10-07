"""Synthetic contract exercises. These fixtures are not publisher evidence."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta
import hashlib
import json
from zoneinfo import ZoneInfo

import pytest

from polymarket_scanner.v11.evidence import canonical, digest
from polymarket_scanner.v11.official_settlement_source import derive_offline_settlement_source
from polymarket_scanner.v11.rules import RuleFingerprint


ET = ZoneInfo("America/New_York")
SOURCE = "https://www.weather.gov/wrh/timeseries?site=KATL"
PARTITION = [
    {"market_id": "m0", "condition_id": "c0", "yes_token": "y0", "no_token": "n0", "lower": None, "upper": 69, "unit": "F"},
    {"market_id": "m1", "condition_id": "c1", "yes_token": "y1", "no_token": "n1", "lower": 70, "upper": 74, "unit": "F"},
    {"market_id": "m2", "condition_id": "c2", "yes_token": "y2", "no_token": "n2", "lower": 75, "upper": None, "unit": "F"},
]


def rule(day="2026-10-05", unit="F", population="WRH_HOURLY_DATA"):
    partition = deepcopy(PARTITION)
    if unit == "C":
        for b in partition:
            b["unit"] = "C"
        partition[0]["upper"], partition[1]["lower"], partition[1]["upper"], partition[2]["lower"] = 19, 20, 24, 25
    p = {"event_id": "synthetic-event", "station": "KATL", "target_date": day,
         "timezone": "America/New_York", "unit": unit,
         "statistic": "DAILY_HIGHEST_TEMP", "observation_population": population,
         "precision_rounding": "WHOLE_DEGREE_" + unit,
         "primary_source": SOURCE, "source_family": "NWS_WRH_TIMESERIES",
         "fallback_policy": "WEATHER_UNDERGROUND_IF_WRH_UNAVAILABLE_BY_NEXT_DAY_2359_ET",
         "finality_and_deadline_policy": "FIRST_FOLLOWING_DATE_DATAPOINT_OR_NEXT_DAY_2359_ET",
         "correction_policy": "ACCEPT_REVISIONS_UNTIL_FIRST_FOLLOWING_DATE_DATAPOINT",
         "no_data_outcome": "LOWEST_BRACKET", "partition": partition}
    return RuleFingerprint(canonical(p), digest(p), "a" * 64)


def fixture(day="2026-10-05", unit="F", population="WRH_HOURLY_DATA", fallback=False):
    r = rule(day, unit, population)
    d = date.fromisoformat(day)
    start = datetime.combine(d, time.min, ET).timestamp()
    end = datetime.combine(d + timedelta(days=1), time.min, ET).timestamp()
    deadline = datetime.combine(d + timedelta(days=2), time.min, ET).timestamp() - 60
    hours = [start + i * 3600 for i in range(round((end - start) / 3600))]
    rows = [{"id": f"o{i}", "source_row_index": i,
             "observed_at": t, "published_at": t + 60,
             "received_at": t + 120, "value": 72 if unit == "F" else 22}
            for i, t in enumerate(hours)]
    trigger = end + 3600
    doc = {"rule_fingerprint_sha256": r.sha256, "source_id": FALLBACK_SOURCE if fallback else SOURCE,
           "source_version": "synthetic-v1", "station": "KATL", "target_date": day,
           "timezone": "America/New_York", "unit": unit,
           "population": "WU_DAILY_OBSERVATIONS" if fallback else population,
           "statistic": "DAILY_HIGHEST_TEMP", "observations": rows, "corrections": [],
           "manifest": {"complete": True, "source_row_count": len(rows),
                        "observation_ids": [x["id"] for x in rows],
                        "correction_ids": [], "first_observation_id": rows[0]["id"],
                        "last_observation_id": rows[-1]["id"], "day_start_at": start,
                        "next_day_start_at": end, "no_data": False},
           "primary_status": {"available_by_deadline": not fallback,
                              "checked_at": deadline, "primary_snapshot_sha256": "b" * 64},
           "finality": {"first_following_point_at": trigger,
                        "first_following_point": {"observed_at": trigger,
                                                  "published_at": trigger + 60,
                                                  "received_at": trigger + 120},
                        "trigger_at": trigger,
                        "kind": "FIRST_FOLLOWING_POINT", "no_earlier_point_proven": True},
           "snapshot": {"published_at": deadline + 60, "received_at": deadline + 120}}
    return r, doc


FALLBACK_SOURCE = "WEATHER_UNDERGROUND_DAILY_OBSERVATIONS"


def check(r, doc, *, as_of=None, gamma=(), expected_version="synthetic-v1"):
    raw = json.dumps(doc, sort_keys=True).encode()
    return derive_offline_settlement_source(
        rule=r, raw_bytes=raw, raw_sha256=hashlib.sha256(raw).hexdigest(),
        expected_source_version=expected_version, gamma_raw_sha256s=gamma,
        as_of=doc["snapshot"]["received_at"] if as_of is None else as_of)


def assert_code(result, code):
    must(result["code"] == code)
    must(result["synthetic_mechanism_only"] is True)
    must(result["independent_label_attestation"] is False)
    must(result["settlement_authority"] is False)
    must(result["financial_authority"] is False)
    must(result["automatic_promotion"] is False)


def must(condition):
    if not condition:
        pytest.fail("offline synthetic settlement contract condition failed")


def test_complete_synthetic_partition_and_celsius_edge():
    r, d = fixture()
    result = check(r, d)
    assert_code(result, "SYNTHETIC_DERIVATION_ONLY")
    must(result["winning_market_id"] == "m1")
    must(result["winning_yes_token"] == "y1")
    must(set(result["losing_yes_tokens"]) == {"y0", "y2"})
    d["observations"][10]["value"] = "74.5"
    must(check(r, d)["winning_market_id"] == "m2")
    for row in d["observations"]:
        row["value"] = 69
    d["observations"][10]["value"] = "69.5"
    must(check(r, d)["winning_market_id"] == "m1")
    r, d = fixture(unit="C")
    d["observations"][10]["value"] = "24.5"
    must(check(r, d)["winning_market_id"] == "m2")
    d["observations"][10]["value"] = "19.5"
    for row in d["observations"]:
        if row["id"] != "o10":
            row["value"] = 19
    must(check(r, d)["winning_market_id"] == "m1")


@pytest.mark.parametrize("change,code", [
    (lambda d: d["observations"].pop(10), "INCOMPLETE_POPULATION"),
    (lambda d: d["observations"].pop(0), "INCOMPLETE_POPULATION"),
    (lambda d: d.update(station="KJFK"), "SOURCE_SEMANTICS_MISMATCH"),
    (lambda d: d.update(timezone="UTC"), "SOURCE_SEMANTICS_MISMATCH"),
    (lambda d: d.update(unit="C"), "SOURCE_SEMANTICS_MISMATCH"),
    (lambda d: d.update(source_version="synthetic-v2"), "SOURCE_SEMANTICS_MISMATCH"),
    (lambda d: d["manifest"].update(complete=False), "INCOMPLETE_POPULATION"),
    (lambda d: d["finality"].update(no_earlier_point_proven=False), "UNFINAL"),
    (lambda d: d["finality"]["first_following_point"].update(published_at=d["snapshot"]["published_at"] + 1, received_at=d["snapshot"]["published_at"] + 2), "UNKNOWN_CLOCK"),
    (lambda d: d["observations"][2].update(received_at=None), "UNKNOWN_CLOCK"),
    (lambda d: d["observations"][-1].update(observed_at=None), "UNKNOWN_CLOCK"),
    (lambda d: d["observations"][2].update(id=[]), "REVISION_CONFLICT"),
    (lambda d: d["observations"][2].update(published_at=d["snapshot"]["received_at"] + 1), "UNKNOWN_CLOCK"),
    (lambda d: d["observations"][2].update(published_at=d["finality"]["trigger_at"], received_at=d["finality"]["trigger_at"]), "UNFINAL"),
    (lambda d: d["snapshot"].update(received_at=d["snapshot"]["published_at"] - 1), "UNFINAL"),
])
def test_adversarial_population_identity_and_clocks(change, code):
    r, d = fixture()
    change(d)
    assert_code(check(r, d), code)


def test_dst_fall_and_spring_have_25_and_23_hours():
    for day, count in (("2026-11-01", 25), ("2026-03-08", 23)):
        r, d = fixture(day)
        must(len(d["observations"]) == count)
        assert_code(check(r, d), "SYNTHETIC_DERIVATION_ONLY")
        d["observations"].pop(count // 2)
        assert_code(check(r, d), "INCOMPLETE_POPULATION")


def test_all_times_interior_gap_even_with_rewritten_id_manifest():
    r, d = fixture(population="WRH_ALL_TIMES")
    assert_code(check(r, d), "SYNTHETIC_DERIVATION_ONLY")
    d["observations"].pop(12)
    d["manifest"]["observation_ids"] = [x["id"] for x in d["observations"]]
    d["manifest"]["source_row_count"] = len(d["observations"])
    assert_code(check(r, d), "INCOMPLETE_POPULATION")


def test_fallback_deadline_no_data_and_missing_proof():
    r, d = fixture(fallback=True)
    assert_code(check(r, d), "SYNTHETIC_DERIVATION_ONLY")
    d["primary_status"]["checked_at"] -= 60
    assert_code(check(r, d), "FALLBACK_UNPROVED")
    d["primary_status"]["checked_at"] += 60
    d["observations"] = []
    d["manifest"].update(observation_ids=[], source_row_count=0, first_observation_id=None,
                         last_observation_id=None, no_data=True)
    result = check(r, d)
    assert_code(result, "SYNTHETIC_DERIVATION_ONLY")
    must(result["winning_market_id"] == "m0" and result["no_data"] is True)
    d["primary_status"].pop("primary_snapshot_sha256")
    assert_code(check(r, d), "FALLBACK_UNPROVED")


def test_correction_before_and_after_trigger_conflicts():
    r, d = fixture()
    old = d["observations"][12]
    correction = {**old, "id": "c0", "replaces_id": old["id"],
                  "published_at": d["finality"]["trigger_at"] - 30,
                  "received_at": d["finality"]["trigger_at"] - 20, "value": 78}
    d["corrections"] = [correction]
    d["manifest"]["correction_ids"] = ["c0"]
    must(check(r, d)["winning_market_id"] == "m2")
    correction["published_at"] = d["finality"]["trigger_at"] + 1
    correction["received_at"] = correction["published_at"] + 1
    assert_code(check(r, d), "REVISION_CONFLICT")
    correction["published_at"] = d["finality"]["trigger_at"]
    correction["received_at"] = correction["published_at"]
    assert_code(check(r, d), "REVISION_CONFLICT")
    correction["id"] = old["id"]
    d["manifest"]["correction_ids"] = [old["id"]]
    assert_code(check(r, d), "REVISION_CONFLICT")


def test_hash_and_gamma_lineage_and_partition_fail_closed():
    r, d = fixture()
    raw = json.dumps(d, sort_keys=True).encode()
    h = hashlib.sha256(raw).hexdigest()
    assert_code(derive_offline_settlement_source(rule=r, raw_bytes=raw + b" ", raw_sha256=h,
                expected_source_version="synthetic-v1", gamma_raw_sha256s=(), as_of=d["snapshot"]["received_at"]), "MISSING_SOURCE")
    assert_code(check(r, d, gamma=(h,)), "MISSING_SOURCE")
    p = r.payload
    p["partition"][1]["lower"] = 71
    bad = RuleFingerprint(canonical(p), digest(p), r.source_event_sha256)
    assert_code(check(bad, d), "SOURCE_SEMANTICS_MISMATCH")


def test_real_evidence_admission_remains_external():
    r, d = fixture()
    result = check(r, d)
    must(result["code"] == "SYNTHETIC_DERIVATION_ONLY")
    # A real-evidence admission test needs separately retained publisher bytes,
    # source-version interpretation, rights review, and independent acceptance.
    must(result["independent_label_attestation"] is False)
