"""Offline, read-only Xweather PWSweather forward-evidence reader.

Pairs *already-retained* genuine Xweather PWSweather captures (host-private
evaluation directory, no live provider request here) with *already-retained*
genuine AWC KATL METAR captures, scored strictly by Alpha's own first-receipt
wall-clock time. AWC METAR is a diagnostic comparator only; it is *not* the
Polymarket settlement source. The contractual settlement population is the
NOAA WRH KATL HOURLY Temp column (see `official_settlement_source.py`), which
this module does not have a retained capture of -- that comparator is reported
as a separate, explicit NOT_CAPTURED block, never silently substituted.

Nothing here grants PAPER/Gate3/R09 qualification. Every reported lead is
`candidate_*` and diagnostic, and deliberately never called "causal" --
physical causality is never established, only a receipt-time ordering. A
candidate requires: (1) Alpha's own PWS receipt timestamp strictly precedes
Alpha's own official-capture receipt timestamp for the same event (never that
the PWS *sensor* timestamp is merely earlier than the official *sensor*
timestamp -- source-measurement time is not a substitute for Alpha receive
time); (2) a confirmed AWC "straddle" -- the AWC poll immediately preceding
the official instant's first-containing capture must demonstrably NOT yet
have contained it, with no excessive inter-poll gap, so the lead window is
pinned to that narrow bracket rather than an arbitrary lookback; and (3) the
PWS observation time is not from before Xweather's own collection began (no
credit for the collector's own startup backfill). The headline
`any_candidate_lead_exists` flag is restricted to near (<=30km) stations; an
any-distance count is reported separately as a secondary diagnostic only. No
timezone/day-boundary reasoning is used to manufacture a lead, and no network
import exists in this module; it only opens files already present on disk.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.weather_sources import parse_awc_metar

VERSION = "alpha_v11_xweather_pws_forward_evidence_v1"

KATL = (33.64028, -84.42694)
MAX_DISTANCE_KM = 51.0          # Matches the capture script's own station-admission radius.
NEAR_DISTANCE_KM = 30.0         # "close" vs "farther" split, same threshold used elsewhere for this radius.
ACCEPT_QC_CODE = 10
ACCEPT_TRUST_MIN = 80
LOOKBACK_SECONDS = 3600.0       # Candidate PWS observations must be no older than this before the official instant.
MAX_ARTIFACT_BYTES = 2_000_000
MAX_AWC_BYTES = 1_310_720
AWC_MAX_AGE_SECONDS = 10800.0
MAX_STRADDLE_GAP_SECONDS = 1800.0   # AWC poll cadence is ~416-615s; a wider gap means the two polls
                                    # bracketing an instant don't reliably pin its publish moment, so
                                    # that instant is excluded rather than treated as a lead window.
XWEATHER_ARTIFACT_NAME_RE = re.compile(r"^xweather-[0-9]{8}T[0-9]{6}-[0-9a-f]{6,}\.json$")

DEFAULT_XWEATHER_ROOT = Path("/home/alphaadmin/AlphaV11_XweatherEvaluation")
DEFAULT_AWC_ROOT = Path("/home/alphaadmin/AlphaV11_OfficialObservationWatch")

WRH_HOURLY_COMPARATOR = {
    "comparator": "NOAA_WRH_KATL_HOURLY_TEMP_COLUMN",
    "status": "NOT_CAPTURED",
    "reason": (
        "No retained NOAA WRH KATL HOURLY Temp-column capture exists locally. "
        "A NOAA MADIS on-demand application was submitted and is pending approval; "
        "only two unscheduled past MADIS guest snapshots exist, not a retained "
        "scheduled WRH HOURLY feed. AWC METAR captures used elsewhere in this report "
        "are a diagnostic scope only ('lead relative to our AWC receipt') and must "
        "never be read as proof of first official public observation or of the "
        "actual contractual hourly settlement source."
    ),
    "settlement_authority": False,
}


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _geodistance_km(lat: float, lon: float) -> float:
    a, b = KATL
    dx = math.radians(lon - b)
    dy = math.radians(lat - a)
    h = math.sin(dy / 2) ** 2 + math.cos(math.radians(a)) * math.cos(math.radians(lat)) * math.sin(dx / 2) ** 2
    return 12742 * math.asin(min(1.0, math.sqrt(h)))


def _finite(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceError("XWEATHER_FIELD_NOT_NUMERIC")
    out = float(value)
    if not math.isfinite(out):
        raise EvidenceError("XWEATHER_FIELD_NOT_FINITE")
    return out


def _read_bounded(path: Path, max_bytes: int) -> bytes:
    if path.is_symlink():
        raise EvidenceError("XWEATHER_SYMLINK_REFUSED")
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raise EvidenceError("XWEATHER_ARTIFACT_TOO_LARGE")
    return raw


def _load_journal(root: Path) -> list[dict]:
    path = root / "journal.jsonl"
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if type(row) is not dict:
            raise EvidenceError("XWEATHER_JOURNAL_ROW_SHAPE")
        for key in ("artifact", "artifact_sha256", "source_sha256", "received_epoch",
                    "station_count", "candidate_qc_count"):
            if key not in row:
                raise EvidenceError("XWEATHER_JOURNAL_ROW_MISSING_FIELD:" + key)
        row = dict(row)
        row.setdefault("key_slot", 1)
        if row["key_slot"] not in (1, 2):
            raise EvidenceError("XWEATHER_JOURNAL_UNRECOGNIZED_SLOT")
        if (type(row["artifact"]) is not str or not XWEATHER_ARTIFACT_NAME_RE.match(row["artifact"])
                or "/" in row["artifact"] or "\\" in row["artifact"]):
            raise EvidenceError("XWEATHER_JOURNAL_ARTIFACT_NAME_REJECTED:" + str(row["artifact"]))
        rows.append(row)
    return rows


def _load_awc_journal(root: Path) -> dict[str, dict]:
    path = root / "journal.jsonl"
    rows: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if type(row) is not dict:
            raise EvidenceError("AWC_JOURNAL_ROW_SHAPE")
        for key in ("artifact", "artifact_sha256", "raw_sha256", "receipt_time"):
            if key not in row:
                raise EvidenceError("AWC_JOURNAL_ROW_MISSING_FIELD:" + key)
        if type(row["artifact"]) is not str or "/" in row["artifact"] or row["artifact"] in (".", ".."):
            raise EvidenceError("AWC_JOURNAL_ARTIFACT_NAME_REJECTED:" + str(row["artifact"]))
        rows[row["artifact"]] = row
    return rows


def _decode_raw(record: dict) -> bytes:
    codec = record.get("raw_body_codec")
    if codec is None:
        raw_b64 = record.get("raw_body_b64")
        if type(raw_b64) is not str:
            raise EvidenceError("XWEATHER_RAW_BODY_MISSING")
        raw = base64.b64decode(raw_b64, validate=True)
    elif codec == "gzip+base64":
        gz_b64 = record.get("raw_body_gzip_b64")
        if type(gz_b64) is not str:
            raise EvidenceError("XWEATHER_GZIP_BODY_MISSING")
        gz = base64.b64decode(gz_b64, validate=True)
        if _sha256(gz) != record.get("raw_gzip_sha256"):
            raise EvidenceError("XWEATHER_GZIP_SHA_MISMATCH")
        raw = gzip.decompress(gz)
    else:
        raise EvidenceError("XWEATHER_CODEC_UNSUPPORTED")
    if _sha256(raw) != record.get("raw_sha256") or len(raw) != record.get("raw_bytes"):
        raise EvidenceError("XWEATHER_RAW_SHA_OR_LENGTH_MISMATCH")
    return raw


def _independent_station_projection(data: object, received_epoch: float) -> dict:
    """Independently re-derive the station/QC projection straight from raw bytes.

    Deliberately re-implemented rather than trusting the capture's own stored
    `projection` field -- a tampered or stale stored field must not pass review
    unexamined. Mirrors the capture script's acceptance predicate exactly
    (PWS station type, QC code 10, trust >= 80, plausible temperature/timestamp,
    distance <= MAX_DISTANCE_KM) so the independent and stored counts are
    expected to agree; disagreement is treated as an integrity failure.
    """
    if type(data) is not dict or data.get("success") is not True:
        return {"station_count": 0, "accepted_observation_count": 0, "stations": []}
    response = data.get("response")
    if type(response) is dict:
        response = [response]
    if type(response) is not list or len(response) > 200:
        return {"station_count": 0, "accepted_observation_count": 0, "stations": []}
    stations = []
    seen_ids = set()
    for row in response:
        if type(row) is not dict:
            continue
        ob, loc, sid = row.get("ob"), row.get("loc"), row.get("id")
        if type(ob) is not dict or type(loc) is not dict or type(sid) is not str or not (0 < len(sid) <= 128):
            continue
        if sid in seen_ids:
            continue
        seen_ids.add(sid)
        lat, lon = loc.get("lat"), loc.get("long")
        if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float))
                and not isinstance(lat, bool) and not isinstance(lon, bool)
                and math.isfinite(float(lat)) and math.isfinite(float(lon))
                and -90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        dist = _geodistance_km(float(lat), float(lon))
        if dist > MAX_DISTANCE_KM:
            continue
        observed, provider_receipt, temperature = ob.get("timestamp"), ob.get("recTimestamp"), ob.get("tempC")
        qc, trust = ob.get("QCcode"), ob.get("trustFactor")
        source, actual_type = row.get("dataSource"), ob.get("type")
        station_raw = source == "PWS" and actual_type == "station"
        plausible_temp = isinstance(temperature, (int, float)) and not isinstance(temperature, bool) and math.isfinite(float(temperature)) and -60 <= temperature <= 60
        plausible_stamp = (isinstance(observed, (int, float)) and not isinstance(observed, bool)
                           and math.isfinite(float(observed)) and 1_500_000_000 <= observed <= received_epoch + 300)
        qc_ok = isinstance(qc, (int, float)) and not isinstance(qc, bool) and math.isfinite(float(qc)) and int(qc) == ACCEPT_QC_CODE
        trust_ok = isinstance(trust, (int, float)) and not isinstance(trust, bool) and math.isfinite(float(trust)) and trust >= ACCEPT_TRUST_MIN
        accepted = station_raw and plausible_temp and plausible_stamp and qc_ok and trust_ok
        stations.append({
            "id": sid, "distance_km": round(dist, 2),
            "observed_at": float(observed) if plausible_stamp else None,
            "provider_received_at": float(provider_receipt) if isinstance(provider_receipt, (int, float)) and not isinstance(provider_receipt, bool) else None,
            "candidate_qc": bool(accepted),
        })
    return {"station_count": len(stations),
            "accepted_observation_count": sum(1 for s in stations if s["candidate_qc"]),
            "stations": stations}


@dataclass(frozen=True)
class XweatherSnapshot:
    artifact: str
    key_slot: int
    received_epoch: float
    raw_sha256: str
    stations: tuple


def load_xweather_snapshots(root: Path) -> list[XweatherSnapshot]:
    """Read + independently re-verify every retained Xweather capture.

    Fail-closed: a hash or codec integrity violation (artifact tampering,
    journal/artifact disagreement, or a stored projection count that disagrees
    with this reader's own independent re-derivation) aborts the whole read
    rather than silently dropping the offending capture.
    """
    rows = _load_journal(root)
    snapshots = []
    for row in rows:
        path = root / row["artifact"]
        blob = _read_bounded(path, MAX_ARTIFACT_BYTES)
        if _sha256(blob) != row["artifact_sha256"]:
            raise EvidenceError("XWEATHER_ARTIFACT_SHA_JOURNAL_MISMATCH:" + row["artifact"])
        record = json.loads(blob)
        if type(record) is not dict:
            raise EvidenceError("XWEATHER_ARTIFACT_SHAPE")
        if record.get("raw_sha256") != row["source_sha256"]:
            raise EvidenceError("XWEATHER_RAW_SHA_JOURNAL_MISMATCH:" + row["artifact"])
        raw = _decode_raw(record)
        received_epoch = _finite(record.get("capture_received_epoch"))
        data = json.loads(raw)
        independent = _independent_station_projection(data, received_epoch)
        stored = record.get("projection") or {}
        if (independent["station_count"] != stored.get("station_count")
                or independent["accepted_observation_count"] != stored.get("accepted_observation_count")
                or independent["station_count"] != row["station_count"]
                or independent["accepted_observation_count"] != row["candidate_qc_count"]):
            raise EvidenceError("XWEATHER_PROJECTION_DISAGREES_WITH_INDEPENDENT_RECOMPUTE:" + row["artifact"])
        snapshots.append(XweatherSnapshot(
            artifact=row["artifact"], key_slot=int(row["key_slot"]), received_epoch=received_epoch,
            raw_sha256=row["source_sha256"], stations=tuple(independent["stations"])))
    return snapshots


def _redact(station_id: str) -> str:
    return "PWS_" + hashlib.sha256(station_id.encode()).hexdigest()[:10]


def summarize_xweather(snapshots: list[XweatherSnapshot], *, redact: bool) -> dict:
    distinct_raw = {}
    for snap in snapshots:
        distinct_raw.setdefault(snap.raw_sha256, []).append(snap)

    earliest_receipt: dict[tuple, float] = {}
    distance_by_station: dict[str, set] = {}
    observed_by_station: dict[str, set] = {}
    for snap in snapshots:
        for s in snap.stations:
            label = s["id"]
            distance_by_station.setdefault(label, set()).add(s["distance_km"])
            if not s["candidate_qc"] or s["observed_at"] is None:
                continue
            key = (label, s["observed_at"])
            prior = earliest_receipt.get(key)
            if prior is None or snap.received_epoch < prior:
                earliest_receipt[key] = snap.received_epoch
            observed_by_station.setdefault(label, set()).add(s["observed_at"])

    distance_drift = sorted(label for label, dists in distance_by_station.items() if len(dists) > 1)

    cadence = []
    for label, observed in observed_by_station.items():
        ordered = sorted(observed)
        deltas = [b - a for a, b in zip(ordered, ordered[1:])]
        near = any(d <= NEAR_DISTANCE_KM for d in distance_by_station.get(label, ()))
        cadence.append({
            "station": _redact(label) if redact else label,
            "distinct_observation_count": len(ordered),
            "near_katl": near,
            "min_interval_seconds": min(deltas) if deltas else None,
            "max_interval_seconds": max(deltas) if deltas else None,
        })
    cadence.sort(key=lambda c: c["station"])

    near_stations = {label for label, dists in distance_by_station.items() if min(dists) <= NEAR_DISTANCE_KM}
    far_stations = set(distance_by_station) - near_stations
    qc_passing_station_count = len(observed_by_station)
    collection_started_at = min((snap.received_epoch for snap in snapshots), default=None)

    return {
        "near_station_labels": near_stations,
        "snapshot_count": len(snapshots),
        "distinct_raw_payload_count": len(distinct_raw),
        "duplicate_snapshot_count": len(snapshots) - len(distinct_raw),
        "distinct_station_count": len(distance_by_station),
        "distinct_station_count_qc_passing": qc_passing_station_count,
        "distinct_station_count_near_katl": len(near_stations),
        "distinct_station_count_farther": len(far_stations),
        "distinct_observation_pair_count": len(earliest_receipt),
        "distance_drift_flagged_stations": [_redact(s) if redact else s for s in distance_drift],
        "station_cadence": cadence,
        "earliest_receipt_by_observation": earliest_receipt,
        "collection_started_at": collection_started_at,
        "caveat": (
            f"distinct_station_count ({len(distance_by_station)}) is every station seen in this "
            f"capture window at any QC/trust level, NOT independent proof of distinct genuine "
            f"outdoor sensors (station identity/ownership is unverified here); only "
            f"distinct_station_count_qc_passing ({qc_passing_station_count}) reflects stations the "
            "provider labelled QC10/trust>=80 at least once."
        ),
    }


def load_official_captures(root: Path) -> dict:
    """Independently re-verify retained AWC KATL captures, binding every file to
    the AWC journal (artifact SHA + declared receipt time), and parse each with
    the already-reviewed `parse_awc_metar` adapter (reused, not re-derived).

    Fail-closed: any file not present in the journal, or whose artifact SHA,
    raw SHA, or declared receipt time disagrees with the journal's recorded
    value, aborts the whole read -- same style as the Xweather-side journal
    check in this module. Returns one row per capture *file* (not per parsed
    observation) so later straddle/gap reasoning can see the full AWC poll
    timeline, including polls that did not yet contain any usable observation.
    """
    import datetime as dt

    journal = _load_awc_journal(root)
    captures = []
    skipped_unverified_identity_count = 0
    rejected_item_count = 0
    for path in sorted(root.glob("awc-katl-*.json")):
        journal_row = journal.get(path.name)
        if journal_row is None:
            raise EvidenceError("AWC_ARTIFACT_NOT_JOURNALED:" + path.name)
        blob = _read_bounded(path, MAX_AWC_BYTES)
        if _sha256(blob) != journal_row["artifact_sha256"]:
            raise EvidenceError("AWC_ARTIFACT_SHA_JOURNAL_MISMATCH:" + path.name)
        record = json.loads(blob)
        if type(record) is not dict:
            raise EvidenceError("AWC_ARTIFACT_SHAPE")
        raw = base64.b64decode(record["raw_body_b64"], validate=True)
        if _sha256(raw) != record.get("raw_sha256") or len(raw) != record.get("raw_byte_length"):
            raise EvidenceError("AWC_RAW_SHA_OR_LENGTH_MISMATCH:" + path.name)
        if record.get("raw_sha256") != journal_row["raw_sha256"]:
            raise EvidenceError("AWC_RAW_SHA_JOURNAL_MISMATCH:" + path.name)
        if record.get("response_received_utc") != journal_row["receipt_time"]:
            raise EvidenceError("AWC_RECEIPT_TIME_JOURNAL_MISMATCH:" + path.name)
        if record.get("station_identity_verified") is not True:
            skipped_unverified_identity_count += 1
            continue
        receipt = dt.datetime.fromisoformat(record["response_received_utc"].replace("Z", "+00:00")).timestamp()
        payload = json.loads(raw)
        parsed = parse_awc_metar(payload, station="KATL", received_at=receipt, max_age_seconds=AWC_MAX_AGE_SECONDS)
        rejected_item_count += sum(parsed["rejections"].values())
        captures.append({
            "source_file": path.name,
            "received_at": receipt,
            "raw_sha256": record["raw_sha256"],
            "observations": [{"observed_at": obs["observed_at"], "temperature_c": obs["temperature_c"]}
                              for obs in parsed["observations"]],
        })
    captures.sort(key=lambda c: c["received_at"])
    return {"captures": captures,
            "skipped_unverified_identity_capture_count": skipped_unverified_identity_count,
            "rejected_item_count": rejected_item_count}


def summarize_official(official: dict) -> dict:
    """Build the first-receipt table, and for each official instant determine
    whether the AWC poll immediately preceding its first-containing capture
    confirms a genuine straddle: that poll must exist (collector was already
    running -- no credit for the collector's own startup/warm-up gap), must not
    already contain the observation, and must not be separated from the
    first-containing capture by an abnormally large AWC poll gap. An instant
    that fails any of those is `straddle_confirmed=False` and is excluded from
    lead evaluation entirely rather than silently treated as a lead window."""
    ordered = sorted(official["captures"], key=lambda c: c["received_at"])
    first_receipt: dict[float, dict] = {}
    official_capture_count = 0
    for idx, cap in enumerate(ordered):
        official_capture_count += len(cap["observations"])
        for obs in cap["observations"]:
            key = obs["observed_at"]
            prior = first_receipt.get(key)
            if prior is None or cap["received_at"] < prior["received_at"]:
                first_receipt[key] = {"received_at": cap["received_at"],
                                       "temperature_c": obs["temperature_c"], "capture_index": idx}

    by_observed_at = {}
    for observed_at, row in first_receipt.items():
        idx = row["capture_index"]
        straddle_confirmed, straddle_lower_bound, censored_reason = False, None, None
        if idx == 0:
            censored_reason = "NO_PRECEDING_AWC_CAPTURE_COLLECTOR_STARTUP"
        else:
            preceding = ordered[idx - 1]
            if any(o["observed_at"] == observed_at for o in preceding["observations"]):
                censored_reason = "PRECEDING_CAPTURE_ALREADY_CONTAINED_OBSERVATION"
            elif row["received_at"] - preceding["received_at"] > MAX_STRADDLE_GAP_SECONDS:
                censored_reason = "AWC_POLL_GAP_TOO_LARGE"
            else:
                straddle_confirmed = True
                straddle_lower_bound = preceding["received_at"]
        by_observed_at[observed_at] = {
            "observed_at": observed_at, "received_at": row["received_at"],
            "temperature_c": row["temperature_c"], "straddle_confirmed": straddle_confirmed,
            "straddle_lower_bound": straddle_lower_bound, "censored_reason": censored_reason,
        }

    return {"distinct_official_observation_count": len(by_observed_at),
            "official_capture_count": official_capture_count, "by_observed_at": by_observed_at,
            "skipped_unverified_identity_capture_count": official["skipped_unverified_identity_capture_count"],
            "rejected_item_count": official["rejected_item_count"]}


def pair_forward_evidence(xweather_summary: dict, official_summary: dict, *, redact: bool) -> dict:
    """Score candidate receipt lead: Alpha's PWS receipt strictly after the
    confirmed straddle's lower bound (the preceding AWC poll that did NOT yet
    contain the official observation) and strictly before Alpha's own first
    receipt of the official instant. This is deliberately not called a
    "causal" lead -- causality in the physical sense is never established,
    only receipt-time ordering bounded by a confirmed AWC straddle. Source/
    observation timestamps only scope which PWS readings are even relevant in
    time; the pass/fail test itself is receipt-time vs receipt-time."""
    earliest = xweather_summary["earliest_receipt_by_observation"]
    near_labels = xweather_summary["near_station_labels"]
    collection_started_at = xweather_summary["collection_started_at"]
    rows = []
    for observed_at, official_row in sorted(official_summary["by_observed_at"].items()):
        official_received_at = official_row["received_at"]
        if not official_row["straddle_confirmed"] or collection_started_at is None:
            rows.append({
                "official_observed_at": observed_at,
                "official_received_at": official_received_at,
                "official_temperature_c": official_row["temperature_c"],
                "lead_evaluation_excluded": True,
                "lead_evaluation_excluded_reason": (
                    official_row["censored_reason"] or "NO_XWEATHER_COLLECTION_HISTORY"),
                "candidate_receipt_lead_count": 0,
                "candidate_within_near_distance_count": 0,
                "candidate_lead_exists": False,
                "candidate_near_lead_exists": False,
                "candidate_station_ids": [],
            })
            continue
        lower_bound = official_row["straddle_lower_bound"]
        candidates = [(label, obs_time, alpha_receipt)
                      for (label, obs_time), alpha_receipt in earliest.items()
                      if observed_at - LOOKBACK_SECONDS <= obs_time <= observed_at
                      and obs_time >= collection_started_at
                      and lower_bound < alpha_receipt < official_received_at]
        near_candidates = [c for c in candidates if c[0] in near_labels]
        rows.append({
            "official_observed_at": observed_at,
            "official_received_at": official_received_at,
            "official_temperature_c": official_row["temperature_c"],
            "lead_evaluation_excluded": False,
            "lead_evaluation_excluded_reason": None,
            "candidate_receipt_lead_count": len(candidates),
            "candidate_within_near_distance_count": len(near_candidates),
            "candidate_lead_exists": bool(candidates),
            "candidate_near_lead_exists": bool(near_candidates),
            "candidate_station_ids": sorted({(_redact(label) if redact else label) for label, _, _ in candidates}),
        })
    return {"official_comparison": rows,
            "any_candidate_lead_exists": any(r["candidate_near_lead_exists"] for r in rows),
            "any_candidate_any_distance_lead_exists_secondary_diagnostic_only": any(
                r["candidate_lead_exists"] for r in rows),
            "note": ("any_candidate_lead_exists is the near (<=30km) headline flag, gated on a confirmed "
                     "AWC straddle; the any-distance field is a secondary, non-headline diagnostic only. "
                     "candidate_* fields are receipt-time comparisons, not observation-time substitution, "
                     "and are not evidence of physical causality.")}


def build_report(xweather_root: Path, awc_root: Path, *, redact: bool = True) -> dict:
    snapshots = load_xweather_snapshots(xweather_root)
    xweather_summary = summarize_xweather(snapshots, redact=redact)
    official_loaded = load_official_captures(awc_root)
    official_summary = summarize_official(official_loaded)
    pairing = pair_forward_evidence(xweather_summary, official_summary, redact=redact)

    xweather_report = dict(xweather_summary)
    xweather_report.pop("earliest_receipt_by_observation")
    xweather_report.pop("near_station_labels")

    receipts = [snap.received_epoch for snap in snapshots]
    capture_span_minutes = round((max(receipts) - min(receipts)) / 60.0, 1) if receipts else 0.0

    return {
        "version": VERSION,
        "xweather_source": xweather_report,
        "official_awc_comparator": {
            "distinct_official_observation_count": official_summary["distinct_official_observation_count"],
            "official_capture_count": official_summary["official_capture_count"],
            "skipped_unverified_identity_capture_count": official_summary["skipped_unverified_identity_capture_count"],
            "rejected_item_count": official_summary["rejected_item_count"],
            "comparator_role": "DIAGNOSTIC_LEAD_SCOPE_ONLY_NOT_SETTLEMENT_SOURCE",
        },
        "wrh_hourly_comparator": WRH_HOURLY_COMPARATOR,
        "forward_lead_pairing": pairing,
        "limitations": [
            f"Single evaluation session, ~{capture_span_minutes} minutes of Xweather capture history: "
            "insufficient for temporal-stability or quality qualification.",
            "Xweather-reported station QC10/trust>=80 count is not independent "
            "proof of genuinely distinct, independently sited outdoor sensors.",
            "AWC METAR is a diagnostic receipt-time comparator only, not the "
            "Polymarket settlement source (NOAA WRH KATL HOURLY Temp column).",
            "NOAA WRH HOURLY capture is NOT_CAPTURED locally; see wrh_hourly_comparator.",
            "No cross-day, backfilled, or corrected-archive data is used; all "
            "timestamps are first-receipt, as captured, never re-dated.",
            "This reader performs no network request, grants no PAPER/Gate3/R09 "
            "credit, and does not alter any live score or ledger.",
        ],
        "PWS_R09_accepted": False, "PAPER_R08_accepted": False, "Gate3_accepted": False,
        "qualification_credit": 0, "financial_authority": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xweather-root", type=Path, default=DEFAULT_XWEATHER_ROOT)
    parser.add_argument("--awc-root", type=Path, default=DEFAULT_AWC_ROOT)
    parser.add_argument("--reveal-stations", action="store_true",
                        help="Print real station IDs instead of redacted labels (never use for shared output).")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    report = build_report(args.xweather_root, args.awc_root, redact=not args.reveal_stations)
    encoded = json.dumps(report, sort_keys=True, indent=2, default=str)
    if args.out:
        args.out.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
