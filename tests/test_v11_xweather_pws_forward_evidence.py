"""Regressions for the offline Xweather PWSweather / AWC KATL forward-evidence reader.

Every fixture below is synthetic and must never be mistaken for genuine
retained evidence; it only exercises the reader's integrity checks and
receipt-time (not observation-time) lead arithmetic.
"""
import base64
import datetime as dt
import gzip
import hashlib
import json

import pytest

from polymarket_scanner.v11.evidence import EvidenceError
from tools.v11_xweather_pws_forward_evidence import (
    WRH_HOURLY_COMPARATOR,
    build_report,
    load_official_captures,
    load_xweather_snapshots,
    pair_forward_evidence,
    summarize_official,
    summarize_xweather,
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _station(sid, *, lat=33.64028, lon=-84.5, observed_at=1_700_000_000, temp=20.0,
             qc=10, trust=100, source="PWS", kind="station"):
    return {"id": sid, "dataSource": source,
            "ob": {"timestamp": observed_at, "recTimestamp": observed_at + 30,
                   "tempC": temp, "QCcode": qc, "trustFactor": trust, "type": kind},
            "loc": {"lat": lat, "long": lon}}


def _xweather_payload(stations):
    return {"success": True, "response": stations}


def _write_xweather_capture(root, name, *, stations, received_epoch, gzip_codec=False, key_slot=1):
    raw = json.dumps(_xweather_payload(stations)).encode("utf-8")
    record = {
        "schema": "ALPHA_V11_XWEATHER_GROUPED_PWS_SOURCE_ONLY_V1",
        "key_slot": key_slot,
        "capture_received_epoch": received_epoch,
        "raw_sha256": _sha(raw), "raw_bytes": len(raw),
        "projection": {
            "station_count": len(stations),
            "accepted_observation_count": sum(
                1 for s in stations
                if s["dataSource"] == "PWS" and s["ob"]["type"] == "station"
                and s["ob"]["QCcode"] == 10 and s["ob"]["trustFactor"] >= 80),
        },
    }
    if gzip_codec:
        gz = gzip.compress(raw, mtime=0)
        record["raw_body_codec"] = "gzip+base64"
        record["raw_gzip_sha256"] = _sha(gz)
        record["raw_body_gzip_b64"] = base64.b64encode(gz).decode("ascii")
    else:
        record["raw_body_b64"] = base64.b64encode(raw).decode("ascii")
    encoded = (json.dumps(record, sort_keys=True) + "\n").encode("ascii")
    path = root / name
    path.write_bytes(encoded)
    return {"artifact": name, "artifact_sha256": _sha(encoded), "source_sha256": record["raw_sha256"],
            "received_epoch": received_epoch, "key_slot": key_slot,
            "station_count": record["projection"]["station_count"],
            "candidate_qc_count": record["projection"]["accepted_observation_count"]}


def _xweather_root(tmp_path, captures):
    root = tmp_path / "xweather"
    root.mkdir()
    rows = []
    for spec in captures:
        rows.append(_write_xweather_capture(root, **spec))
    with (root / "journal.jsonl").open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    return root


def _write_awc_capture(root, name, *, observed_at, temp, received_iso, raw_ob=None):
    raw_ob = raw_ob or (
        "METAR KATL " + dt.datetime.fromtimestamp(observed_at, dt.timezone.utc).strftime("%d%H%M") + "Z "
        "01006KT 10SM SCT200 28/13 A3004 RMK AO2"
    )
    payload = [{"icaoId": "KATL", "obsTime": observed_at, "temp": temp,
                "rawOb": raw_ob, "reportTime": received_iso}]
    raw = json.dumps(payload).encode("utf-8")
    record = {"station": "KATL", "response_received_utc": received_iso,
              "raw_sha256": _sha(raw), "raw_byte_length": len(raw),
              "raw_body_b64": base64.b64encode(raw).decode("ascii"),
              "station_identity_verified": True}
    path = root / name
    path.write_bytes((json.dumps(record, sort_keys=True) + "\n").encode("ascii"))


def _awc_root(tmp_path, captures):
    root = tmp_path / "awc"
    root.mkdir()
    for i, spec in enumerate(captures):
        _write_awc_capture(root, f"awc-katl-{i:03d}.json", **spec)
    return root


def test_loads_and_independently_reverifies_plain_codec(tmp_path):
    root = _xweather_root(tmp_path, [
        dict(name="a.json", stations=[_station("PWS_NEAR")], received_epoch=1_700_000_100.0),
    ])
    snapshots = load_xweather_snapshots(root)
    assert len(snapshots) == 1
    assert snapshots[0].stations[0]["candidate_qc"] is True


def test_loads_gzip_codec_capture(tmp_path):
    root = _xweather_root(tmp_path, [
        dict(name="a.json", stations=[_station("PWS_GZ")], received_epoch=1_700_000_100.0, gzip_codec=True),
    ])
    snapshots = load_xweather_snapshots(root)
    assert len(snapshots) == 1
    assert snapshots[0].stations[0]["id"] == "PWS_GZ"


def test_tampered_artifact_bytes_fail_closed(tmp_path):
    root = _xweather_root(tmp_path, [
        dict(name="a.json", stations=[_station("PWS_X")], received_epoch=1_700_000_100.0),
    ])
    path = root / "a.json"
    record = json.loads(path.read_text())
    record["capture_received_epoch"] += 1  # mutate after journal sha was recorded
    path.write_text(json.dumps(record, sort_keys=True) + "\n")
    with pytest.raises(EvidenceError, match="XWEATHER_ARTIFACT_SHA_JOURNAL_MISMATCH"):
        load_xweather_snapshots(root)


def test_stored_projection_disagreeing_with_independent_recompute_fails_closed(tmp_path):
    root = _xweather_root(tmp_path, [
        dict(name="a.json", stations=[_station("PWS_X")], received_epoch=1_700_000_100.0),
    ])
    path = root / "a.json"
    record = json.loads(path.read_text())
    record["projection"]["accepted_observation_count"] = 99  # lie about acceptance
    encoded = json.dumps(record, sort_keys=True) + "\n"
    path.write_text(encoded)
    journal = root / "journal.jsonl"
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    rows[0]["artifact_sha256"] = _sha(encoded.encode("ascii"))  # keep artifact-sha check passing
    journal.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")
    with pytest.raises(EvidenceError, match="XWEATHER_PROJECTION_DISAGREES_WITH_INDEPENDENT_RECOMPUTE"):
        load_xweather_snapshots(root)


def test_non_station_or_low_trust_rows_are_not_independently_accepted(tmp_path):
    root = _xweather_root(tmp_path, [
        dict(name="a.json", stations=[
            _station("PWS_OK"),
            _station("PWS_LOWTRUST", trust=10),
            _station("PWS_BADQC", qc=20),
            _station("PWS_NOTSTATION", kind="other"),
            _station("PWS_NOTPWS", source="OTHER_SOURCE"),
        ], received_epoch=1_700_000_100.0),
    ])
    snapshots = load_xweather_snapshots(root)
    accepted = {s["id"] for s in snapshots[0].stations if s["candidate_qc"]}
    assert accepted == {"PWS_OK"}


def test_duplicate_raw_bytes_are_deduplicated_in_summary(tmp_path):
    stations = [_station("PWS_DUP")]
    root = _xweather_root(tmp_path, [
        dict(name="a.json", stations=stations, received_epoch=1_700_000_000.0),
        dict(name="b.json", stations=stations, received_epoch=1_700_000_300.0),
    ])
    snapshots = load_xweather_snapshots(root)
    summary = summarize_xweather(snapshots, redact=True)
    assert summary["snapshot_count"] == 2
    assert summary["distinct_raw_payload_count"] == 1
    assert summary["duplicate_snapshot_count"] == 1


def test_earliest_receipt_uses_minimum_across_duplicate_and_distinct_snapshots(tmp_path):
    station_v1 = _station("PWS_EARLY", observed_at=1_700_000_000)
    station_v2 = _station("PWS_EARLY", observed_at=1_700_000_300)
    root = _xweather_root(tmp_path, [
        dict(name="a.json", stations=[station_v1], received_epoch=1_700_000_050.0, key_slot=1),
        dict(name="b.json", stations=[station_v1], received_epoch=1_700_000_010.0, key_slot=2),  # earlier receipt, same content
        dict(name="c.json", stations=[station_v2], received_epoch=1_700_000_360.0, key_slot=1),
    ])
    snapshots = load_xweather_snapshots(root)
    summary = summarize_xweather(snapshots, redact=False)
    earliest = summary["earliest_receipt_by_observation"]
    assert earliest[("PWS_EARLY", 1_700_000_000.0)] == 1_700_000_010.0
    assert earliest[("PWS_EARLY", 1_700_000_300.0)] == 1_700_000_360.0


def test_cadence_and_distance_buckets(tmp_path):
    near = _station("PWS_NEAR", lat=33.65, lon=-84.43, observed_at=1_700_000_000)
    far = _station("PWS_FAR", lat=33.9, lon=-84.6, observed_at=1_700_000_000)  # ~33km: within 51km radius, past the 30km near split
    near2 = _station("PWS_NEAR", lat=33.65, lon=-84.43, observed_at=1_700_000_300)
    root = _xweather_root(tmp_path, [
        dict(name="a.json", stations=[near, far], received_epoch=1_700_000_050.0),
        dict(name="b.json", stations=[near2], received_epoch=1_700_000_350.0),
    ])
    snapshots = load_xweather_snapshots(root)
    summary = summarize_xweather(snapshots, redact=False)
    assert summary["distinct_station_count_near_katl"] == 1
    assert summary["distinct_station_count_farther"] == 1
    cadence = {c["station"]: c for c in summary["station_cadence"]}
    assert cadence["PWS_NEAR"]["distinct_observation_count"] == 2
    assert cadence["PWS_NEAR"]["min_interval_seconds"] == 300
    assert cadence["PWS_FAR"]["distinct_observation_count"] == 1
    assert cadence["PWS_FAR"]["min_interval_seconds"] is None


def test_redaction_hides_real_station_ids_by_default(tmp_path):
    root = _xweather_root(tmp_path, [
        dict(name="a.json", stations=[_station("PWS_SECRET_NAME")], received_epoch=1_700_000_050.0),
    ])
    snapshots = load_xweather_snapshots(root)
    redacted = summarize_xweather(snapshots, redact=True)
    labels = {c["station"] for c in redacted["station_cadence"]}
    assert "PWS_SECRET_NAME" not in labels
    assert all(label.startswith("PWS_") and label != "PWS_SECRET_NAME" for label in labels)


def test_awc_capture_loaded_and_reused_parser_applied(tmp_path):
    received = dt.datetime(2026, 10, 8, 18, 0, 0, tzinfo=dt.timezone.utc)
    observed = received.timestamp() - 120
    root = _awc_root(tmp_path, [
        dict(observed_at=observed, temp=21.0, received_iso=received.isoformat().replace("+00:00", "Z")),
    ])
    official = load_official_captures(root)
    assert len(official) == 1
    assert official[0]["temperature_c"] == 21.0
    assert official[0]["received_at"] == received.timestamp()


def test_awc_raw_tamper_fails_closed(tmp_path):
    root = _awc_root(tmp_path, [
        dict(observed_at=1_791_000_000.0, temp=20.0, received_iso="2026-10-08T18:00:00Z"),
    ])
    path = root / "awc-katl-000.json"
    record = json.loads(path.read_text())
    record["raw_byte_length"] += 1
    path.write_text(json.dumps(record, sort_keys=True) + "\n")
    with pytest.raises(EvidenceError, match="AWC_RAW_SHA_OR_LENGTH_MISMATCH"):
        load_official_captures(root)


def test_unverified_station_identity_is_excluded_not_fabricated(tmp_path):
    received_iso = "2026-10-08T18:00:00Z"
    payload = [{"icaoId": "KATL", "obsTime": 1_791_000_000.0, "temp": 20.0,
                "rawOb": "METAR KATL 081800Z 00000KT 10SM CLR 20/10 A3000 RMK AO2", "reportTime": received_iso}]
    raw = json.dumps(payload).encode("utf-8")
    record = {"station": "KATL", "response_received_utc": received_iso, "raw_sha256": _sha(raw),
              "raw_byte_length": len(raw), "raw_body_b64": base64.b64encode(raw).decode("ascii"),
              "station_identity_verified": False}
    root = tmp_path / "awc"
    root.mkdir()
    (root / "awc-katl-000.json").write_bytes((json.dumps(record, sort_keys=True) + "\n").encode("ascii"))
    assert load_official_captures(root) == []


def test_lead_requires_receipt_time_not_observation_time():
    """Source-earlier-but-received-later must NOT count as a lead."""
    xweather_summary = {
        "earliest_receipt_by_observation": {("PWS_A", 1_700_000_000.0): 1_700_005_000.0},  # received LATE
        "near_station_labels": {"PWS_A"},
    }
    official_summary = {"by_observed_at": {1_700_000_900.0: {"received_at": 1_700_001_000.0, "temperature_c": 20.0}}}
    result = pair_forward_evidence(xweather_summary, official_summary, redact=False)
    row = result["official_comparison"][0]
    assert row["candidate_lead_exists"] is False
    assert row["candidate_causal_pws_count"] == 0


def test_lead_recognized_when_alpha_received_pws_strictly_before_official_receipt():
    xweather_summary = {
        "earliest_receipt_by_observation": {("PWS_A", 1_700_000_000.0): 1_700_000_500.0},  # received EARLY
        "near_station_labels": {"PWS_A"},
    }
    official_summary = {"by_observed_at": {1_700_000_900.0: {"received_at": 1_700_001_000.0, "temperature_c": 20.0}}}
    result = pair_forward_evidence(xweather_summary, official_summary, redact=False)
    row = result["official_comparison"][0]
    assert row["candidate_lead_exists"] is True
    assert row["candidate_within_near_distance_count"] == 1
    assert row["candidate_station_ids"] == ["PWS_A"]


def test_far_station_lead_excluded_from_near_count():
    xweather_summary = {
        "earliest_receipt_by_observation": {("PWS_FAR", 1_700_000_000.0): 1_700_000_500.0},
        "near_station_labels": set(),
    }
    official_summary = {"by_observed_at": {1_700_000_900.0: {"received_at": 1_700_001_000.0, "temperature_c": 20.0}}}
    result = pair_forward_evidence(xweather_summary, official_summary, redact=False)
    row = result["official_comparison"][0]
    assert row["candidate_lead_exists"] is True
    assert row["candidate_within_near_distance_count"] == 0


def test_build_report_is_non_qualifying_and_reports_wrh_as_not_captured(tmp_path):
    xw_root = _xweather_root(tmp_path, [
        dict(name="a.json", stations=[_station("PWS_ONE")], received_epoch=1_700_000_050.0),
    ])
    received = dt.datetime(2026, 10, 8, 18, 0, 0, tzinfo=dt.timezone.utc)
    awc_root = _awc_root(tmp_path, [
        dict(observed_at=received.timestamp() - 60, temp=19.0, received_iso=received.isoformat().replace("+00:00", "Z")),
    ])
    report = build_report(xw_root, awc_root, redact=True)
    assert report["PWS_R09_accepted"] is False
    assert report["PAPER_R08_accepted"] is False
    assert report["Gate3_accepted"] is False
    assert report["qualification_credit"] == 0
    assert report["financial_authority"] is False
    assert report["wrh_hourly_comparator"] == WRH_HOURLY_COMPARATOR
    assert report["wrh_hourly_comparator"]["status"] == "NOT_CAPTURED"
    assert report["official_awc_comparator"]["comparator_role"] == "DIAGNOSTIC_LEAD_SCOPE_ONLY_NOT_SETTLEMENT_SOURCE"
    assert "earliest_receipt_by_observation" not in report["xweather_source"]
    assert "near_station_labels" not in report["xweather_source"]
    assert json.dumps(report)  # fully JSON-serializable, no stray tuple keys
