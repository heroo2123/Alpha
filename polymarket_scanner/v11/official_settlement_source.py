"""Pure offline check of a *claimed* WRH/WU settlement-source day snapshot.

The byte document and its completeness manifest are caller supplied. Passing this
check is a synthetic/structural derivation, never publisher or rights attestation.
No fetch, archive write, or promotion path exists here.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import math
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .rules import RuleFingerprint


VERSION = "offline_official_settlement_source_contract_v1"
FALLBACK = "WEATHER_UNDERGROUND_DAILY_OBSERVATIONS"
POLICIES = (
    "WEATHER_UNDERGROUND_IF_WRH_UNAVAILABLE_BY_NEXT_DAY_2359_ET",
    "FIRST_FOLLOWING_DATE_DATAPOINT_OR_NEXT_DAY_2359_ET",
    "ACCEPT_REVISIONS_UNTIL_FIRST_FOLLOWING_DATE_DATAPOINT",
    "LOWEST_BRACKET",
)


def _result(code: str, rule_sha: str | None = None, **details: object) -> dict:
    return {"version": VERSION, "code": code, "rule_fingerprint_sha256": rule_sha,
            "synthetic_mechanism_only": True, "independent_label_attestation": False,
            "settlement_authority": False, "financial_authority": False,
            "automatic_promotion": False, **details}


def _clock(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _partition(rows: object, unit: str) -> bool:
    if not isinstance(rows, list) or not rows:
        return False
    seen = set()
    if any(not isinstance(b, dict) or b.get("lower") is not None
           and type(b.get("lower")) not in (int, float) for b in rows):
        return False
    ordered = sorted(rows, key=lambda b: float("-inf") if b.get("lower") is None else b["lower"])
    for i, b in enumerate(ordered):
        if not isinstance(b, dict) or b.get("unit") != unit:
            return False
        if "lower" not in b or "upper" not in b:
            return False
        for key in ("market_id", "condition_id", "yes_token", "no_token"):
            v = b.get(key)
            if not isinstance(v, str) or not v or v in seen:
                return False
            seen.add(v)
        lo, hi = b.get("lower"), b.get("upper")
        if any(v is not None and (type(v) not in (int, float) or not math.isfinite(v) or not float(v).is_integer()) for v in (lo, hi)):
            return False
        if (i == 0) != (lo is None) or (i == len(ordered)-1) != (hi is None):
            return False
        if lo is not None and hi is not None and lo > hi:
            return False
        if i and ordered[i-1]["upper"] + 1 != lo:
            return False
    return True


def derive_offline_settlement_source(*, rule: RuleFingerprint, raw_bytes: bytes,
                                     raw_sha256: str, expected_source_version: str,
                                     gamma_raw_sha256s: tuple[str, ...], as_of: float) -> dict:
    """Derive one winner only from a complete, self-declared byte snapshot.

    ``raw_bytes`` is a UTF-8 JSON document. It must contain source_id,
    source_version, station, target_date, timezone, unit, population, statistic,
    observations, corrections, manifest, primary_status, finality, and snapshot.
    Each observation has id/observed_at/published_at/received_at/value; each
    correction additionally has replaces_id. Times are Unix UTC seconds.
    The manifest enumerates every row id, boundary ids and explicit completeness.
    Its claim cannot authenticate the publisher; a separate byte/rights review is
    required before any real label use.
    """
    rule_sha = rule.sha256 if isinstance(rule, RuleFingerprint) else None
    if not isinstance(rule, RuleFingerprint):
        return _result("SOURCE_SEMANTICS_MISMATCH")
    try:
        p = rule.payload
    except Exception:
        return _result("SOURCE_SEMANTICS_MISMATCH", rule_sha)
    if not _clock(as_of):
        return _result("UNKNOWN_CLOCK", rule_sha)
    if (not isinstance(raw_bytes, bytes) or not _sha(raw_sha256)
            or hashlib.sha256(raw_bytes).hexdigest() != raw_sha256):
        return _result("MISSING_SOURCE", rule_sha)
    if (not isinstance(expected_source_version, str) or not expected_source_version.strip()
            or not isinstance(gamma_raw_sha256s, tuple)
            or any(not _sha(x) for x in gamma_raw_sha256s)
            or raw_sha256 in gamma_raw_sha256s):
        return _result("MISSING_SOURCE", rule_sha)
    try:
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON key")
                result[key] = value
            return result

        d = json.loads(raw_bytes, object_pairs_hook=unique_object)
        if not isinstance(d, dict) or not raw_bytes or len(raw_bytes) > 2_000_000:
            raise ValueError
        target = date.fromisoformat(p["target_date"])
        zone = ZoneInfo(p["timezone"])
        eastern = ZoneInfo("America/New_York")
    except (ValueError, TypeError, KeyError, ZoneInfoNotFoundError, UnicodeError):
        return _result("SOURCE_SEMANTICS_MISMATCH", rule_sha)
    if (p.get("source_family") != "NWS_WRH_TIMESERIES"
            or p.get("observation_population") not in ("WRH_ALL_TIMES", "WRH_HOURLY_DATA")
            or p.get("statistic") not in ("DAILY_HIGHEST_TEMP", "DAILY_LOWEST_TEMP")
            or p.get("unit") not in ("F", "C")
            or p.get("precision_rounding") != "WHOLE_DEGREE_" + p.get("unit", "")
            or tuple(p.get(k) for k in ("fallback_policy", "finality_and_deadline_policy",
                                         "correction_policy", "no_data_outcome")) != POLICIES
            or not _partition(p.get("partition"), p.get("unit"))):
        return _result("SOURCE_SEMANTICS_MISMATCH", rule_sha)
    status = d.get("primary_status")
    if not isinstance(status, dict) or type(status.get("available_by_deadline")) is not bool:
        return _result("FALLBACK_UNPROVED", rule_sha)
    fallback = not status["available_by_deadline"]
    source = FALLBACK if fallback else p.get("primary_source")
    fields = {"source_id": source, "source_version": expected_source_version,
              "station": p["station"], "target_date": p["target_date"],
              "timezone": p["timezone"], "unit": p["unit"],
              "population": "WU_DAILY_OBSERVATIONS" if fallback else p["observation_population"],
              "statistic": p["statistic"], "rule_fingerprint_sha256": rule.sha256}
    if any(d.get(k) != v for k, v in fields.items()):
        return _result("SOURCE_SEMANTICS_MISMATCH", rule_sha)
    start = datetime.combine(target, time.min, zone).timestamp()
    end = datetime.combine(target + timedelta(days=1), time.min, zone).timestamp()
    deadline = datetime.combine(target + timedelta(days=2), time(0, 0), eastern).timestamp() - 60
    snap = d.get("snapshot")
    fin = d.get("finality")
    if not isinstance(snap, dict) or not isinstance(fin, dict):
        return _result("UNKNOWN_CLOCK", rule_sha)
    first = fin.get("first_following_point_at")
    clocks = [snap.get("published_at"), snap.get("received_at"),
              status.get("checked_at"), fin.get("trigger_at")]
    if first is not None:
        point = fin.get("first_following_point")
        if not isinstance(point, dict):
            return _result("UNKNOWN_CLOCK", rule_sha)
        clocks.extend((first, point.get("observed_at"), point.get("published_at"),
                       point.get("received_at")))
    if not all(_clock(x) for x in clocks):
        return _result("UNKNOWN_CLOCK", rule_sha)
    if (first is not None and first < end) or status["checked_at"] < deadline:
        return _result("FALLBACK_UNPROVED" if fallback else "UNKNOWN_CLOCK", rule_sha)
    if first is not None and (point["observed_at"] != first
                              or not first <= point["published_at"] <= point["received_at"] <= snap["received_at"]
                              or point["published_at"] > snap["published_at"]):
        return _result("UNKNOWN_CLOCK", rule_sha)
    trigger = min(first, deadline) if first is not None else deadline
    if (fin.get("trigger_at") != trigger
            or fin.get("kind") != ("FIRST_FOLLOWING_POINT" if first is not None and first <= deadline else "DEADLINE")
            or type(fin.get("no_earlier_point_proven")) is not bool
            or not fin["no_earlier_point_proven"]):
        return _result("UNFINAL", rule_sha)
    if (snap["published_at"] < trigger or snap["received_at"] < snap["published_at"]
            or snap["received_at"] > as_of or status["checked_at"] > snap["published_at"]):
        return _result("UNFINAL", rule_sha)
    if fallback and (status.get("primary_snapshot_sha256") is None
                     or not _sha(status["primary_snapshot_sha256"])
                     or status["primary_snapshot_sha256"] == raw_sha256):
        return _result("FALLBACK_UNPROVED", rule_sha)
    rows, corrections, manifest = d.get("observations"), d.get("corrections"), d.get("manifest")
    if not isinstance(rows, list) or not isinstance(corrections, list) or not isinstance(manifest, dict):
        return _result("INCOMPLETE_POPULATION", rule_sha)
    if any(not isinstance(r, dict) for r in rows + corrections):
        return _result("INCOMPLETE_POPULATION", rule_sha)
    all_rows = rows + corrections
    ids = [r.get("id") for r in all_rows]
    if (any(not isinstance(x, str) or not x for x in ids)
            or len(set(ids)) != len(ids)):
        return _result("REVISION_CONFLICT", rule_sha)
    if (manifest.get("complete") is not True or manifest.get("observation_ids") != [r["id"] for r in rows]
            or manifest.get("correction_ids") != [r["id"] for r in corrections]
            or type(manifest.get("source_row_count")) is not int
            or manifest["source_row_count"] != len(rows)
            or manifest.get("day_start_at") != start or manifest.get("next_day_start_at") != end):
        return _result("INCOMPLETE_POPULATION", rule_sha)
    # A claimed complete all-times export must retain every source row ordinal.
    # The ordinal and count are still self-declared until publisher bytes are reviewed.
    if [r.get("source_row_index") for r in rows] != list(range(manifest["source_row_count"])):
        return _result("INCOMPLETE_POPULATION", rule_sha)
    if rows and not all(_clock(r.get("observed_at")) for r in rows):
        return _result("UNKNOWN_CLOCK", rule_sha)
    if rows and (manifest.get("first_observation_id") != rows[0]["id"]
                 or manifest.get("last_observation_id") != rows[-1]["id"]
                 or rows[0].get("observed_at") != start
                 or not end - 3600 <= rows[-1].get("observed_at", -1) < end):
        return _result("INCOMPLETE_POPULATION", rule_sha)
    if not rows and manifest.get("no_data") is not True:
        return _result("INCOMPLETE_POPULATION", rule_sha)
    if not rows and corrections:
        return _result("REVISION_CONFLICT", rule_sha)
    if rows and manifest.get("no_data") is not False:
        return _result("INCOMPLETE_POPULATION", rule_sha)
    by_id = {}
    current = {}
    for r in all_rows:
        ts = [r.get("observed_at"), r.get("published_at"), r.get("received_at")]
        if not all(_clock(x) for x in ts):
            return _result("UNKNOWN_CLOCK", rule_sha)
        observed, published, received = ts
        if (not start <= observed < end or observed > published or published > received
                or received > as_of or received > snap["received_at"]):
            return _result("UNKNOWN_CLOCK", rule_sha)
        if published >= trigger:
            return _result("REVISION_CONFLICT" if r in corrections else "UNFINAL", rule_sha)
        try:
            value = Decimal(str(r["value"]))
        except (KeyError, InvalidOperation, ValueError):
            return _result("SOURCE_SEMANTICS_MISMATCH", rule_sha)
        if not value.is_finite() or abs(value) > 200 or type(r["value"]) is bool:
            return _result("SOURCE_SEMANTICS_MISMATCH", rule_sha)
        if r in corrections:
            prior = by_id.get(r.get("replaces_id"))
            if (prior is None or prior["observed_at"] != observed
                    or published <= prior["published_at"] or received < prior["received_at"]):
                return _result("REVISION_CONFLICT", rule_sha)
        elif observed in current:
            return _result("REVISION_CONFLICT", rule_sha)
        by_id[r["id"]] = r
        current[observed] = r
    if any(r.get("replaces_id") is not None for r in rows):
        return _result("REVISION_CONFLICT", rule_sha)
    if rows and [r["observed_at"] for r in rows] != sorted(r["observed_at"] for r in rows):
        return _result("INCOMPLETE_POPULATION", rule_sha)
    if rows and p["observation_population"] == "WRH_HOURLY_DATA" and not fallback:
        expected_hours = [datetime.fromtimestamp(start + 3600*i, zone).timestamp() for i in range(round((end-start)/3600))]
        if [r["observed_at"] for r in rows] != expected_hours:
            return _result("INCOMPLETE_POPULATION", rule_sha)
    if not rows:
        winner = sorted(p["partition"], key=lambda b: float("-inf") if b["lower"] is None else b["lower"])[0]
        whole = None
    else:
        extreme = (max if p["statistic"] == "DAILY_HIGHEST_TEMP" else min)(Decimal(str(r["value"])) for r in current.values())
        whole = int(extreme.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        winners = [b for b in p["partition"] if (b["lower"] is None or b["lower"] <= whole)
                   and (b["upper"] is None or whole <= b["upper"])]
        if len(winners) != 1:
            return _result("SOURCE_SEMANTICS_MISMATCH", rule_sha)
        winner = winners[0]
    return _result("SYNTHETIC_DERIVATION_ONLY", rule_sha, source_id=source,
                   source_version=expected_source_version, raw_sha256=raw_sha256,
                   target_date=p["target_date"], station=p["station"],
                   population=fields["population"], finality_at=trigger,
                   whole_degree_value=whole, winning_market_id=winner["market_id"],
                   winning_yes_token=winner["yes_token"],
                   losing_yes_tokens=tuple(b["yes_token"] for b in p["partition"] if b is not winner),
                   no_data=not rows)
