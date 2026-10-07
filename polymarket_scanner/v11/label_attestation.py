"""Offline bridge: bind a Gamma closed-market payout LABEL set to an
OFFICIAL_OBSERVATION record for the same station/day and check consistency,
or fail closed and report the exact observation still needed.

NOAA_AWC METAR is documented elsewhere (see weather_sources.py,
`OFFICIAL_METAR_PROXY_NOT_EXACT_CONTRACT_POPULATION`) as a proxy, not the
exact settlement daily-extreme population -- a Celsius-sourced METAR reading
can legitimately round to a different whole degree Fahrenheit than the
station's official daily high/low. This module therefore never claims
settlement, financial, or independent-attestation authority and never
rewrites the Gamma LABEL record (which is append-only evidence anyway).
A CONSISTENT result is proxy corroboration only, reported back to the caller
under `official_observation_corroboration` for review -- it is not an
automatic promotion and it never sets `independent_label_attestation` True;
that field stays False in every result this module returns, regardless of
corroboration outcome.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .evidence import EvidenceError, finite, identity, sha

ATTESTATION_VERSION = "alpha_v11_label_attestation_v3"
GAMMA_LABEL_PROVIDER = "GAMMA_CLOSED_MARKET_EXACT_TOKEN_PAYOUT"
OFFICIAL_OBSERVATION_PROVIDER = "NOAA_AWC"
WHOLE_DEGREE_PRECISION = "WHOLE_DEGREE_F"
# METAR reports roughly hourly. This margin bounds both ends of the local day
# (catching a capture that starts late or ends early) AND every gap between
# consecutive readings (catching a sparse capture, e.g. one reading near each
# end and nothing between, that would otherwise look boundary-complete while
# missing the actual daily extreme in the uncovered middle).
COVERAGE_MARGIN_HOURS = 3


def _station_local_datetime(observed_at: float, tz_name: str) -> datetime:
    try:
        zone = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        raise EvidenceError("ATTESTATION_TIMEZONE_UNKNOWN") from None
    return datetime.fromtimestamp(finite(observed_at), zone)


def _celsius_to_whole_f(temp_c: float) -> int | None:
    value = finite(temp_c, nonnegative=False) * 9.0 / 5.0 + 32.0
    rounded = round(value)
    if abs(value - rounded) >= 0.5 - 1e-9:
        return None
    return int(rounded)


def _bucket_contains(bucket: dict, value: int) -> bool:
    lower, upper = bucket.get("lower"), bucket.get("upper")
    if lower is not None and value < lower:
        return False
    if upper is not None and value > upper:
        return False
    return True


def _winning_bucket_from_labels(partition: list[dict], labels: dict[str, int]) -> tuple[str, dict]:
    if {b["market_id"] for b in partition} != set(labels):
        raise EvidenceError("ATTESTATION_LABEL_PARTITION_MISMATCH")
    winners = tuple(mid for mid, value in labels.items() if value == 1)
    if len(winners) != 1:
        raise EvidenceError("ATTESTATION_LABEL_SET_NOT_SINGLE_WINNER")
    winner_id = winners[0]
    return winner_id, next(b for b in partition if b["market_id"] == winner_id)


def _needed_observation_spec(station: str, tz_name: str, target_date: date) -> dict:
    return {
        "provider": OFFICIAL_OBSERVATION_PROVIDER,
        "station": station,
        "timezone": tz_name,
        "target_date": target_date.isoformat(),
        "required_local_coverage": {
            "earliest_local_hour_at_or_before": COVERAGE_MARGIN_HOURS,
            "latest_local_hour_at_or_after": 24 - COVERAGE_MARGIN_HOURS,
        },
    }


def _extract_labels(rule_fingerprint_payload: dict, label_records: dict[str, dict]) -> tuple[dict[str, int], float]:
    labels: dict[str, int] = {}
    knowable_ats = []
    for market_id, record in label_records.items():
        if record.get("kind") != "LABEL" or record["body"].get("provider") != GAMMA_LABEL_PROVIDER:
            raise EvidenceError("ATTESTATION_LABEL_PROVENANCE_UNSUPPORTED")
        payload = record["body"]["payload"]
        target_identity = payload.get("target_identity")
        if not isinstance(target_identity, dict) or target_identity.get("market_id") != market_id:
            raise EvidenceError("ATTESTATION_LABEL_IDENTITY_MISMATCH")
        value = payload.get("value")
        if value not in (0, 1):
            raise EvidenceError("ATTESTATION_LABEL_VALUE_INVALID")
        labels[market_id] = int(value)
        knowable_ats.append(finite(payload["knowable_at"]))
    if not labels:
        raise EvidenceError("ATTESTATION_LABEL_SET_EMPTY")
    return labels, max(knowable_ats)


def _collect_target_day_observations(
    official_observation_records: list[dict], *, station: str, tz_name: str, target_date: date, now: float,
) -> dict[float, float]:
    gamma_free = identity(station)
    by_time: dict[float, float] = {}
    for record in official_observation_records:
        if record.get("kind") != "OFFICIAL_OBSERVATION" or record["body"].get("provider") != OFFICIAL_OBSERVATION_PROVIDER:
            raise EvidenceError("ATTESTATION_OFFICIAL_SOURCE_PROVENANCE_UNSUPPORTED")
        for obs in record["body"]["payload"].get("observations", ()):
            if identity(obs.get("station", "")) != gamma_free:
                raise EvidenceError("ATTESTATION_STATION_MISMATCH")
            observed_at = finite(obs["observed_at"])
            if observed_at > now:
                raise EvidenceError("ATTESTATION_OFFICIAL_OBSERVATION_IN_FUTURE")
            local_dt = _station_local_datetime(observed_at, tz_name)
            if local_dt.date() != target_date:
                continue
            temp_c = finite(obs["temperature_c"], nonnegative=False)
            key = observed_at
            if key in by_time and by_time[key] != temp_c:
                raise EvidenceError("ATTESTATION_OFFICIAL_OBSERVATION_CONFLICT")
            by_time[key] = temp_c
    return by_time


def attest_resolved_day(
    *, rule_fingerprint_payload: dict, label_records: dict[str, dict],
    official_observation_records: list[dict], now: float,
) -> dict:
    """Check whether a NOAA AWC proxy observation agrees with the Gamma LABEL
    set for one station/day. This does not attest settlement truth.

    `label_records` and `official_observation_records` must be full evidence
    records (as returned by EvidenceStore.get/records), not bare payloads, so
    their kind and provider -- and hence source lineage -- can be checked.
    Raises EvidenceError for malformed/inconsistent-shaped input. Returns a
    result dict (never raises) once the inputs are well-formed, including the
    ATTESTATION_BLOCKED_* states that report exactly what observation is
    still needed.
    """
    now = finite(now)
    station = identity(rule_fingerprint_payload["station"])
    tz_name = rule_fingerprint_payload["timezone"]
    statistic = rule_fingerprint_payload["statistic"]
    if rule_fingerprint_payload["unit"] != "F" or rule_fingerprint_payload["precision_rounding"] != WHOLE_DEGREE_PRECISION:
        raise EvidenceError("ATTESTATION_CONTRACT_PROFILE_UNSUPPORTED")
    if statistic not in ("DAILY_HIGHEST_TEMP", "DAILY_LOWEST_TEMP"):
        raise EvidenceError("ATTESTATION_STATISTIC_UNSUPPORTED")
    partition = rule_fingerprint_payload["partition"]
    target_date = date.fromisoformat(rule_fingerprint_payload["target_date"])

    labels, label_knowable_at = _extract_labels(rule_fingerprint_payload, label_records)
    winner_id, winner_bucket = _winning_bucket_from_labels(partition, labels)
    if now < label_knowable_at:
        raise EvidenceError("ATTESTATION_CLOCK_INVALID")

    # sha() rejects malformed digests. The archive runner resolves both
    # references and verifies their content before calling this pure check.
    gamma_raw_shas = {sha(record["body"]["payload"].get("source_capture_sha256"))
                      for record in label_records.values()}
    by_time = _collect_target_day_observations(
        official_observation_records, station=station, tz_name=tz_name, target_date=target_date, now=now)
    for record in official_observation_records:
        if sha(record["body"]["payload"].get("raw_evidence_sha256")) in gamma_raw_shas:
            raise EvidenceError("ATTESTATION_SOURCE_LINEAGE_NOT_DISTINCT")

    base_result = {
        "version": ATTESTATION_VERSION, "station": station, "target_date": target_date.isoformat(),
        "timezone": tz_name, "statistic": statistic, "gamma_winning_market_id": winner_id,
        "gamma_winning_bucket": {"lower": winner_bucket["lower"], "upper": winner_bucket["upper"]},
        "independent_label_attestation": False, "settlement_authority": False,
        "financial_authority": False, "automatic_promotion": False,
    }

    if not by_time:
        return dict(base_result, state="ATTESTATION_BLOCKED_MISSING_OFFICIAL_OBSERVATION",
                    needed_observation=_needed_observation_spec(station, tz_name, target_date))

    zone = ZoneInfo(tz_name)
    day_start = datetime.combine(target_date, time.min, zone).timestamp()
    day_end = datetime.combine(target_date + timedelta(days=1), time.min, zone).timestamp()
    if now < day_end:
        return dict(base_result, state="ATTESTATION_BLOCKED_DAY_NOT_ENDED",
                    target_day_end_at=day_end)
    times = sorted(by_time)
    max_interior_gap = max((b - a for a, b in zip(times, times[1:])), default=0.0)
    margin = COVERAGE_MARGIN_HOURS * 3600
    if (times[0] - day_start > margin or day_end - times[-1] > margin
            or max_interior_gap > margin):
        return dict(base_result, state="ATTESTATION_BLOCKED_INSUFFICIENT_COVERAGE",
                    needed_observation=_needed_observation_spec(station, tz_name, target_date))

    temp_c_stat = max(by_time.values()) if statistic == "DAILY_HIGHEST_TEMP" else min(by_time.values())
    official_value_f = _celsius_to_whole_f(temp_c_stat)
    if official_value_f is None:
        return dict(base_result, state="ATTESTATION_BLOCKED_OFFICIAL_VALUE_ROUNDING_TIE")

    official_winners = tuple(b["market_id"] for b in partition if _bucket_contains(b, official_value_f))
    if len(official_winners) != 1:
        raise EvidenceError("ATTESTATION_OFFICIAL_VALUE_BUCKET_AMBIGUOUS")
    consistent = official_winners[0] == winner_id
    # independent_label_attestation is deliberately NOT set from `consistent`:
    # NOAA_AWC is a proxy population (see module docstring), so agreement here
    # is corroboration, not independently attested settlement truth. It stays
    # False (from base_result) in every branch of this function.
    return dict(base_result,
                state=("OFFICIAL_OBSERVATION_PROXY_CORROBORATION_CONSISTENT" if consistent
                       else "OFFICIAL_OBSERVATION_PROXY_CORROBORATION_INCONSISTENT"),
                official_observation_corroboration="CONSISTENT" if consistent else "INCONSISTENT",
                official_observation_value_f=official_value_f,
                official_winning_market_id=official_winners[0])
