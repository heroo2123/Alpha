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
`candidate_*` and diagnostic. "Lead" means Alpha's own PWS receipt timestamp
precedes Alpha's own official-capture receipt timestamp for the same event --
never that the PWS *sensor* timestamp is merely earlier than the official
*sensor* timestamp (source-measurement time is not a substitute for Alpha
receive time, and no timezone/day-boundary reasoning is used to manufacture a
lead). No network import exists in this module; it only opens files already
present on disk.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import math
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
        rows.append(row)
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

    return {
        "near_station_labels": near_stations,
        "snapshot_count": len(snapshots),
        "distinct_raw_payload_count": len(distinct_raw),
        "duplicate_snapshot_count": len(snapshots) - len(distinct_raw),
        "distinct_station_count": len(distance_by_station),
        "distinct_station_count_near_katl": len(near_stations),
        "distinct_station_count_farther": len(far_stations),
        "distinct_observation_pair_count": len(earliest_receipt),
        "distance_drift_flagged_stations": [_redact(s) if redact else s for s in distance_drift],
        "station_cadence": cadence,
        "earliest_receipt_by_observation": earliest_receipt,
        "caveat": (
            "Station count reflects whatever the provider labels QC10/trust>=80 in "
            "this single capture window; it is NOT independent proof of 97 distinct "
            "genuine outdoor sensors (station identity/ownership is unverified here)."
        ),
    }


def load_official_captures(root: Path) -> list[dict]:
    """Independently re-verify retained AWC KATL captures and parse them with
    the already-reviewed `parse_awc_metar` adapter (reused, not re-derived)."""
    import datetime as dt

    official = []
    for path in sorted(root.glob("awc-katl-*.json")):
        blob = _read_bounded(path, MAX_AWC_BYTES)
        record = json.loads(blob)
        if type(record) is not dict:
            raise EvidenceError("AWC_ARTIFACT_SHAPE")
        raw = base64.b64decode(record["raw_body_b64"], validate=True)
        if _sha256(raw) != record.get("raw_sha256") or len(raw) != record.get("raw_byte_length"):
            raise EvidenceError("AWC_RAW_SHA_OR_LENGTH_MISMATCH:" + path.name)
        if record.get("station_identity_verified") is not True:
            continue
        receipt = dt.datetime.fromisoformat(record["response_received_utc"].replace("Z", "+00:00")).timestamp()
        payload = json.loads(raw)
        parsed = parse_awc_metar(payload, station="KATL", received_at=receipt, max_age_seconds=AWC_MAX_AGE_SECONDS)
        for obs in parsed["observations"]:
            official.append({
                "observed_at": obs["observed_at"], "received_at": receipt,
                "temperature_c": obs["temperature_c"], "source_file": path.name,
                "raw_sha256": record["raw_sha256"],
            })
    return official


def summarize_official(official: list[dict]) -> dict:
    first_receipt: dict[float, dict] = {}
    for row in official:
        key = row["observed_at"]
        prior = first_receipt.get(key)
        if prior is None or row["received_at"] < prior["received_at"]:
            first_receipt[key] = row
    return {"distinct_official_observation_count": len(first_receipt),
            "official_capture_count": len(official), "by_observed_at": first_receipt}


def pair_forward_evidence(xweather_summary: dict, official_summary: dict, *, redact: bool) -> dict:
    """Score candidate causal lead: Alpha's PWS receipt strictly before Alpha's
    own first receipt of the official instant. Source/observation timestamps
    are only used to scope which PWS readings are even relevant in time; the
    pass/fail test itself is receipt-time vs receipt-time, never
    observation-time vs observation-time, and never a timezone/day-boundary
    substitute for an actual receipt comparison."""
    earliest = xweather_summary["earliest_receipt_by_observation"]
    near_labels = xweather_summary["near_station_labels"]
    rows = []
    for observed_at, official_row in sorted(official_summary["by_observed_at"].items()):
        official_received_at = official_row["received_at"]
        candidates = [(label, obs_time, alpha_receipt)
                      for (label, obs_time), alpha_receipt in earliest.items()
                      if observed_at - LOOKBACK_SECONDS <= obs_time <= observed_at
                      and alpha_receipt < official_received_at]
        near_candidates = [c for c in candidates if c[0] in near_labels]
        rows.append({
            "official_observed_at": observed_at,
            "official_received_at": official_received_at,
            "official_temperature_c": official_row["temperature_c"],
            "candidate_causal_pws_count": len(candidates),
            "candidate_within_near_distance_count": len(near_candidates),
            "candidate_lead_exists": bool(candidates),
            "candidate_near_lead_exists": bool(near_candidates),
            "candidate_station_ids": sorted({(_redact(label) if redact else label) for label, _, _ in candidates}),
        })
    return {"official_comparison": rows,
            "any_candidate_lead_exists": any(r["candidate_lead_exists"] for r in rows),
            "note": "candidate_* fields only; receipt-time comparison, not observation-time substitution"}


def build_report(xweather_root: Path, awc_root: Path, *, redact: bool = True) -> dict:
    snapshots = load_xweather_snapshots(xweather_root)
    xweather_summary = summarize_xweather(snapshots, redact=redact)
    official_rows = load_official_captures(awc_root)
    official_summary = summarize_official(official_rows)
    pairing = pair_forward_evidence(xweather_summary, official_summary, redact=redact)

    xweather_report = dict(xweather_summary)
    xweather_report.pop("earliest_receipt_by_observation")
    xweather_report.pop("near_station_labels")

    return {
        "version": VERSION,
        "xweather_source": xweather_report,
        "official_awc_comparator": {
            "distinct_official_observation_count": official_summary["distinct_official_observation_count"],
            "official_capture_count": official_summary["official_capture_count"],
            "comparator_role": "DIAGNOSTIC_LEAD_SCOPE_ONLY_NOT_SETTLEMENT_SOURCE",
        },
        "wrh_hourly_comparator": WRH_HOURLY_COMPARATOR,
        "forward_lead_pairing": pairing,
        "limitations": [
            "Single evaluation session, ~35 minutes of Xweather capture history: "
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
