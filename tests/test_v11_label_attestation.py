from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.label_attestation import attest_resolved_day

STATION = "KATL"
TZ_NAME = "America/New_York"
TARGET_DATE = "2026-10-05"
PARTITION = [
    {"market_id": "m0", "condition_id": "c0", "lower": None, "upper": 69.0},
    {"market_id": "m1", "condition_id": "c1", "lower": 70.0, "upper": 74.0},
    {"market_id": "m2", "condition_id": "c2", "lower": 75.0, "upper": None},
]
F72_C = (72 - 32) * 5.0 / 9.0
F78_C = (78 - 32) * 5.0 / 9.0
NIGHT_C = 10.0  # well below any bucket boundary; only the daily max is scored


def rule_payload(*, target_date=TARGET_DATE, statistic="DAILY_HIGHEST_TEMP", partition=PARTITION):
    return {"station": STATION, "timezone": TZ_NAME, "target_date": target_date,
            "statistic": statistic, "unit": "F", "precision_rounding": "WHOLE_DEGREE_F",
            "partition": partition}


def label_record(market_id, value, *, knowable_at=2_000_000_000.0, source_capture_sha256="f" * 64):
    return {"kind": "LABEL", "body": {"provider": "GAMMA_CLOSED_MARKET_EXACT_TOKEN_PAYOUT",
            "payload": {"target_identity": {"market_id": market_id}, "knowable_at": knowable_at,
                        "value": value, "source_capture_sha256": source_capture_sha256}}}


def labels_m1_wins(**kw):
    return {"m0": label_record("m0", 0, **kw), "m1": label_record("m1", 1, **kw), "m2": label_record("m2", 0, **kw)}


def official_record(observations, *, raw_evidence_sha256="a" * 64, provider="NOAA_AWC", kind="OFFICIAL_OBSERVATION"):
    return {"kind": kind, "body": {"provider": provider,
            "payload": {"observations": observations, "raw_evidence_sha256": raw_evidence_sha256}}}


def obs(station, observed_at, temp_c):
    return {"station": station, "observed_at": observed_at, "temperature_c": temp_c}


def local_epoch(hour, minute=0, *, day=5, tz=TZ_NAME):
    return datetime(2026, 10, day, hour, minute, tzinfo=ZoneInfo(tz)).timestamp()


def full_day_observations(peak_c, *, peak_hour=14, day=5):
    return [obs(STATION, local_epoch(hour, day=day), peak_c if hour == peak_hour else NIGHT_C)
            for hour in range(24)]


NOW = 2_000_000_100.0


def test_consistent_official_observation_corroborates_without_independent_attestation():
    result = attest_resolved_day(rule_fingerprint_payload=rule_payload(), label_records=labels_m1_wins(),
                                  official_observation_records=[official_record(full_day_observations(F72_C))],
                                  now=NOW)
    assert result["state"] == "OFFICIAL_OBSERVATION_PROXY_CORROBORATION_CONSISTENT"
    assert result["official_observation_corroboration"] == "CONSISTENT"
    # NOAA_AWC is a proxy population, not the exact settlement extreme -- a
    # consistent proxy reading must never be reported as independent
    # attestation of the Gamma payout label.
    assert result["independent_label_attestation"] is False
    assert result["official_winning_market_id"] == "m1" == result["gamma_winning_market_id"]
    assert result["settlement_authority"] is False
    assert result["financial_authority"] is False
    assert result["automatic_promotion"] is False


def test_inconsistent_official_observation_is_reported_not_hidden():
    result = attest_resolved_day(rule_fingerprint_payload=rule_payload(), label_records=labels_m1_wins(),
                                  official_observation_records=[official_record(full_day_observations(F78_C))],
                                  now=NOW)
    assert result["state"] == "OFFICIAL_OBSERVATION_PROXY_CORROBORATION_INCONSISTENT"
    assert result["official_observation_corroboration"] == "INCONSISTENT"
    assert result["independent_label_attestation"] is False
    assert result["official_winning_market_id"] == "m2"
    assert result["gamma_winning_market_id"] == "m1"


def test_missing_official_observation_fails_closed_and_names_what_is_needed():
    result = attest_resolved_day(rule_fingerprint_payload=rule_payload(), label_records=labels_m1_wins(),
                                  official_observation_records=[], now=NOW)
    assert result["state"] == "ATTESTATION_BLOCKED_MISSING_OFFICIAL_OBSERVATION"
    assert result["independent_label_attestation"] is False
    needed = result["needed_observation"]
    assert needed["provider"] == "NOAA_AWC"
    assert needed["station"] == STATION
    assert needed["timezone"] == TZ_NAME
    assert needed["target_date"] == TARGET_DATE


def test_partial_day_coverage_fails_closed_rather_than_guessing():
    sparse = [obs(STATION, local_epoch(h), NIGHT_C) for h in range(10, 15)]
    result = attest_resolved_day(rule_fingerprint_payload=rule_payload(), label_records=labels_m1_wins(),
                                  official_observation_records=[official_record(sparse)], now=NOW)
    assert result["state"] == "ATTESTATION_BLOCKED_INSUFFICIENT_COVERAGE"
    assert result["independent_label_attestation"] is False


def test_two_boundary_readings_with_a_wide_interior_gap_do_not_count_as_coverage():
    # Both readings individually satisfy the old earliest/latest boundary
    # check (hour 1 <= COVERAGE_MARGIN_HOURS, hour 22 >= 24-COVERAGE_MARGIN_HOURS),
    # but nothing was observed across the 21-hour middle of the day where the
    # true daily high (F78_C, well above the night-time reading) actually
    # occurred -- it must not be mistaken for a complete daily population.
    sparse = [obs(STATION, local_epoch(1), NIGHT_C), obs(STATION, local_epoch(22), F78_C)]
    result = attest_resolved_day(rule_fingerprint_payload=rule_payload(), label_records=labels_m1_wins(),
                                  official_observation_records=[official_record(sparse)], now=NOW)
    assert result["state"] == "ATTESTATION_BLOCKED_INSUFFICIENT_COVERAGE"
    assert result["independent_label_attestation"] is False


def test_hourly_readings_with_one_large_midday_gap_fail_closed():
    # Realistic-looking hourly coverage except for a single dropped stretch
    # spanning the middle of the day (hours 8-18 missing) -- still enough of
    # a gap to hide the true peak, so this must block rather than silently
    # trust the night-time readings that remain.
    observations = [obs(STATION, local_epoch(h), NIGHT_C) for h in range(0, 8)]
    observations += [obs(STATION, local_epoch(h), NIGHT_C) for h in range(18, 24)]
    result = attest_resolved_day(rule_fingerprint_payload=rule_payload(), label_records=labels_m1_wins(),
                                  official_observation_records=[official_record(observations)], now=NOW)
    assert result["state"] == "ATTESTATION_BLOCKED_INSUFFICIENT_COVERAGE"


def test_station_day_timezone_boundary_excludes_adjacent_local_day():
    observations = full_day_observations(F72_C)
    # Just before local midnight on Oct 4 -- one minute outside the Oct 5 local
    # day -- carries a value that would flip the winner (m2) if wrongly folded
    # into Oct 5's statistic.
    spike_utc = datetime(2026, 10, 5, 3, 59, tzinfo=timezone.utc).timestamp()
    observations.append(obs(STATION, spike_utc, F78_C))
    result = attest_resolved_day(rule_fingerprint_payload=rule_payload(), label_records=labels_m1_wins(),
                                  official_observation_records=[official_record(observations)], now=NOW)
    assert result["state"] == "OFFICIAL_OBSERVATION_PROXY_CORROBORATION_CONSISTENT"

    # One minute into the Oct 5 local day: now inside scope, correctly flips it.
    observations_in_day = full_day_observations(F72_C)
    inside_utc = datetime(2026, 10, 5, 4, 1, tzinfo=timezone.utc).timestamp()
    observations_in_day.append(obs(STATION, inside_utc, F78_C))
    result_in = attest_resolved_day(rule_fingerprint_payload=rule_payload(), label_records=labels_m1_wins(),
                                     official_observation_records=[official_record(observations_in_day)], now=NOW)
    assert result_in["state"] == "OFFICIAL_OBSERVATION_PROXY_CORROBORATION_INCONSISTENT"
    assert result_in["official_winning_market_id"] == "m2"


def test_bucket_boundary_value_lands_in_correct_adjacent_bucket():
    # F=75 is the lower edge of m2, not the upper edge of m1 -- must resolve
    # to m2 cleanly, with no ambiguity, since the partition is contiguous.
    f75_c = (75 - 32) * 5.0 / 9.0
    result = attest_resolved_day(rule_fingerprint_payload=rule_payload(), label_records=labels_m1_wins(),
                                  official_observation_records=[official_record(full_day_observations(f75_c))],
                                  now=NOW)
    assert result["official_winning_market_id"] == "m2"
    assert result["state"] == "OFFICIAL_OBSERVATION_PROXY_CORROBORATION_INCONSISTENT"


def test_rounding_tie_blocks_rather_than_guessing_a_bucket():
    tie_c = (70.5 - 32) * 5.0 / 9.0  # converts back to exactly F=70.5
    result = attest_resolved_day(rule_fingerprint_payload=rule_payload(), label_records=labels_m1_wins(),
                                  official_observation_records=[official_record(full_day_observations(tie_c))],
                                  now=NOW)
    assert result["state"] == "ATTESTATION_BLOCKED_OFFICIAL_VALUE_ROUNDING_TIE"
    assert result["independent_label_attestation"] is False


def test_duplicate_observation_is_idempotent():
    observations = full_day_observations(F72_C)
    peak_at = local_epoch(14)
    observations.append(obs(STATION, peak_at, F72_C))  # exact replay of the peak reading
    result = attest_resolved_day(rule_fingerprint_payload=rule_payload(), label_records=labels_m1_wins(),
                                  official_observation_records=[official_record(observations)], now=NOW)
    assert result["state"] == "OFFICIAL_OBSERVATION_PROXY_CORROBORATION_CONSISTENT"


def test_conflicting_replay_at_same_timestamp_is_rejected():
    observations = full_day_observations(F72_C)
    peak_at = local_epoch(14)
    observations.append(obs(STATION, peak_at, F78_C))  # tampered re-submission, different value
    with pytest.raises(EvidenceError, match="ATTESTATION_OFFICIAL_OBSERVATION_CONFLICT"):
        attest_resolved_day(rule_fingerprint_payload=rule_payload(), label_records=labels_m1_wins(),
                            official_observation_records=[official_record(observations)], now=NOW)


def test_attestation_before_label_was_knowable_is_rejected():
    with pytest.raises(EvidenceError, match="ATTESTATION_CLOCK_INVALID"):
        attest_resolved_day(rule_fingerprint_payload=rule_payload(), label_records=labels_m1_wins(knowable_at=NOW + 10),
                            official_observation_records=[official_record(full_day_observations(F72_C))], now=NOW)


def test_observation_from_the_future_is_rejected():
    observations = full_day_observations(F72_C)
    observations.append(obs(STATION, NOW + 1_000_000, F72_C))
    with pytest.raises(EvidenceError, match="ATTESTATION_OFFICIAL_OBSERVATION_IN_FUTURE"):
        attest_resolved_day(rule_fingerprint_payload=rule_payload(), label_records=labels_m1_wins(),
                            official_observation_records=[official_record(observations)], now=NOW)


def test_gamma_label_cannot_self_attest_as_its_own_official_observation():
    fake_official = {"kind": "OFFICIAL_OBSERVATION",
                     "body": {"provider": "GAMMA_CLOSED_MARKET_EXACT_TOKEN_PAYOUT",
                              "payload": {"observations": full_day_observations(F72_C),
                                          "raw_evidence_sha256": "f" * 64}}}
    with pytest.raises(EvidenceError, match="ATTESTATION_OFFICIAL_SOURCE_PROVENANCE_UNSUPPORTED"):
        attest_resolved_day(rule_fingerprint_payload=rule_payload(), label_records=labels_m1_wins(),
                            official_observation_records=[fake_official], now=NOW)


def test_shared_raw_evidence_lineage_with_label_is_rejected():
    shared_sha = "d" * 64
    labels = labels_m1_wins(source_capture_sha256=shared_sha)
    official = official_record(full_day_observations(F72_C), raw_evidence_sha256=shared_sha)
    with pytest.raises(EvidenceError, match="ATTESTATION_SOURCE_LINEAGE_NOT_DISTINCT"):
        attest_resolved_day(rule_fingerprint_payload=rule_payload(), label_records=labels,
                            official_observation_records=[official], now=NOW)


def test_official_observation_forged_missing_hash_is_rejected_even_when_labels_have_real_hashes():
    # The labels all carry well-formed, mutually distinct hashes, so the old
    # `.get(...) in gamma_raw_shas` membership check would have compared
    # `None` against a set of real hex digests, found no match, and let a
    # completely absent raw-evidence hash pass as "independently sourced".
    official = official_record(full_day_observations(F72_C), raw_evidence_sha256=None)
    with pytest.raises(EvidenceError) as exc_info:
        attest_resolved_day(rule_fingerprint_payload=rule_payload(), label_records=labels_m1_wins(),
                            official_observation_records=[official], now=NOW)
    assert str(exc_info.value) == "INVALID_DIGEST"


def test_official_observation_empty_string_hash_is_rejected():
    official = official_record(full_day_observations(F72_C), raw_evidence_sha256="")
    with pytest.raises(EvidenceError) as exc_info:
        attest_resolved_day(rule_fingerprint_payload=rule_payload(), label_records=labels_m1_wins(),
                            official_observation_records=[official], now=NOW)
    assert str(exc_info.value) == "INVALID_DIGEST"


def test_label_missing_source_capture_hash_is_rejected():
    labels = labels_m1_wins(source_capture_sha256=None)
    with pytest.raises(EvidenceError) as exc_info:
        attest_resolved_day(rule_fingerprint_payload=rule_payload(), label_records=labels,
                            official_observation_records=[official_record(full_day_observations(F72_C))], now=NOW)
    assert str(exc_info.value) == "INVALID_DIGEST"


def test_label_malformed_source_capture_hash_is_rejected():
    labels = labels_m1_wins(source_capture_sha256="f" * 63)  # one short of a real sha256 hex digest
    with pytest.raises(EvidenceError) as exc_info:
        attest_resolved_day(rule_fingerprint_payload=rule_payload(), label_records=labels,
                            official_observation_records=[official_record(full_day_observations(F72_C))], now=NOW)
    assert str(exc_info.value) == "INVALID_DIGEST"


def test_consistent_result_never_claims_independent_attestation_regardless_of_corroboration():
    # Belt-and-suspenders on risk #2: whichever corroboration outcome the
    # proxy observation produces, independent_label_attestation must stay
    # False and the state name must say PROXY_CORROBORATION, never ATTESTED.
    for peak_c, expected_corroboration in ((F72_C, "CONSISTENT"), (F78_C, "INCONSISTENT")):
        result = attest_resolved_day(rule_fingerprint_payload=rule_payload(), label_records=labels_m1_wins(),
                                      official_observation_records=[official_record(full_day_observations(peak_c))],
                                      now=NOW)
        assert result["independent_label_attestation"] is False
        assert result["official_observation_corroboration"] == expected_corroboration
        assert result["state"].startswith("OFFICIAL_OBSERVATION_PROXY_CORROBORATION_")
        assert "ATTESTED" not in result["state"]


def test_no_automatic_promotion_claim_anywhere_in_a_consistent_result():
    result = attest_resolved_day(rule_fingerprint_payload=rule_payload(), label_records=labels_m1_wins(),
                                  official_observation_records=[official_record(full_day_observations(F72_C))],
                                  now=NOW)
    assert result["automatic_promotion"] is False
    assert "CONTROLLED_LEARNING_READY" not in str(result)
    assert "ACCEPTED_CHAMPION" not in str(result)
