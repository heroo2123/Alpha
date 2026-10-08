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
    LOOKBACK_SECONDS,
    MAX_STRADDLE_GAP_SECONDS,
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


def _compliant_xweather_name(label: str) -> str:
    """Map an arbitrary test label to a journal-name-pattern-compliant artifact
    filename, so existing test call sites can keep using short labels while
    exercising the real F5 artifact-name containment check."""
    return "xweather-20261008T180000-" + hashlib.sha256(label.encode()).hexdigest()[:8] + ".json"


def _station(sid, *, lat=33.64028, lon=-84.5, observed_at=1_700_000_000, temp=20.0,
             qc=10, trust=100, source="PWS", kind="station"):
    return {"id": sid, "dataSource": source,
            "ob": {"timestamp": observed_at, "recTimestamp": observed_at + 30,
                   "tempC": temp, "QCcode": qc, "trustFactor": trust, "type": kind},
            "loc": {"lat": lat, "long": lon}}


def _xweather_payload(stations):
    return {"success": True, "response": stations}


def _write_xweather_capture(root, name, *, stations, received_epoch, gzip_codec=False, key_slot=1):
    fname = _compliant_xweather_name(name)
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
    path = root / fname
    path.write_bytes(encoded)
    return {"artifact": fname, "artifact_sha256": _sha(encoded), "source_sha256": record["raw_sha256"],
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


def _write_awc_capture(root, name, *, observed_at, temp, received_iso, raw_ob=None,
                        station_identity_verified=True):
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
              "station_identity_verified": station_identity_verified}
    encoded = (json.dumps(record, sort_keys=True) + "\n").encode("ascii")
    path = root / name
    path.write_bytes(encoded)
    return {"artifact": name, "artifact_sha256": _sha(encoded), "raw_sha256": record["raw_sha256"],
            "receipt_time": received_iso, "station_identity_verified": station_identity_verified}


def _awc_root(tmp_path, captures):
    root = tmp_path / "awc"
    root.mkdir()
    rows = []
    for i, spec in enumerate(captures):
        rows.append(_write_awc_capture(root, f"awc-katl-{i:03d}.json", **spec))
    with (root / "journal.jsonl").open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    return root


def _capture(received_at, observed_ats):
    return {"source_file": f"cap-{received_at}.json", "received_at": received_at, "raw_sha256": "x",
            "observations": [{"observed_at": o, "temperature_c": 20.0} for o in observed_ats]}


def _official_loaded(captures, *, skipped=0, rejected=0):
    return {"captures": captures, "skipped_unverified_identity_capture_count": skipped,
            "rejected_item_count": rejected}


def _xw_summary(earliest, near_labels, *, collection_started_at=0.0):
    return {"earliest_receipt_by_observation": earliest, "near_station_labels": near_labels,
            "collection_started_at": collection_started_at}


def _official_row(observed_at, received_at, temp, *, straddle_confirmed=True,
                   straddle_lower_bound=None, censored_reason=None):
    if straddle_confirmed and straddle_lower_bound is None:
        straddle_lower_bound = received_at - 500.0
    return {"observed_at": observed_at, "received_at": received_at, "temperature_c": temp,
            "straddle_confirmed": straddle_confirmed, "straddle_lower_bound": straddle_lower_bound,
            "censored_reason": censored_reason}


def _official_summary(rows_by_observed_at):
    return {"by_observed_at": rows_by_observed_at}


# ---------------------------------------------------------------------------
# Xweather load/verify
# ---------------------------------------------------------------------------

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
    path = root / _compliant_xweather_name("a.json")
    record = json.loads(path.read_text())
    record["capture_received_epoch"] += 1  # mutate after journal sha was recorded
    path.write_text(json.dumps(record, sort_keys=True) + "\n")
    with pytest.raises(EvidenceError, match="XWEATHER_ARTIFACT_SHA_JOURNAL_MISMATCH"):
        load_xweather_snapshots(root)


def test_stored_projection_disagreeing_with_independent_recompute_fails_closed(tmp_path):
    root = _xweather_root(tmp_path, [
        dict(name="a.json", stations=[_station("PWS_X")], received_epoch=1_700_000_100.0),
    ])
    path = root / _compliant_xweather_name("a.json")
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


def test_journal_station_count_mismatch_fails_closed(tmp_path):
    root = _xweather_root(tmp_path, [
        dict(name="a.json", stations=[_station("PWS_X")], received_epoch=1_700_000_100.0),
    ])
    journal = root / "journal.jsonl"
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    rows[0]["station_count"] = 99  # journal disagrees with the artifact's own (correct) projection
    journal.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")
    with pytest.raises(EvidenceError, match="XWEATHER_PROJECTION_DISAGREES_WITH_INDEPENDENT_RECOMPUTE"):
        load_xweather_snapshots(root)


def test_raw_sha_journal_mismatch_fails_closed(tmp_path):
    root = _xweather_root(tmp_path, [
        dict(name="a.json", stations=[_station("PWS_X")], received_epoch=1_700_000_100.0),
    ])
    journal = root / "journal.jsonl"
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    rows[0]["source_sha256"] = "0" * 64
    journal.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")
    with pytest.raises(EvidenceError, match="XWEATHER_RAW_SHA_JOURNAL_MISMATCH"):
        load_xweather_snapshots(root)


def test_gzip_sha_mismatch_fails_closed(tmp_path):
    root = _xweather_root(tmp_path, [
        dict(name="a.json", stations=[_station("PWS_GZ")], received_epoch=1_700_000_100.0, gzip_codec=True),
    ])
    path = root / _compliant_xweather_name("a.json")
    record = json.loads(path.read_text())
    record["raw_gzip_sha256"] = "0" * 64
    encoded = json.dumps(record, sort_keys=True) + "\n"
    path.write_text(encoded)
    journal = root / "journal.jsonl"
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    rows[0]["artifact_sha256"] = _sha(encoded.encode("ascii"))
    journal.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")
    with pytest.raises(EvidenceError, match="XWEATHER_GZIP_SHA_MISMATCH"):
        load_xweather_snapshots(root)


def test_symlink_artifact_refused(tmp_path):
    root = _xweather_root(tmp_path, [
        dict(name="a.json", stations=[_station("PWS_X")], received_epoch=1_700_000_100.0),
    ])
    fname = _compliant_xweather_name("a.json")
    real_path = root / fname
    data = real_path.read_bytes()
    target = root / "real_target.json"
    target.write_bytes(data)
    real_path.unlink()
    real_path.symlink_to(target)
    with pytest.raises(EvidenceError, match="XWEATHER_SYMLINK_REFUSED"):
        load_xweather_snapshots(root)


def test_unknown_codec_rejected(tmp_path):
    root = _xweather_root(tmp_path, [
        dict(name="a.json", stations=[_station("PWS_X")], received_epoch=1_700_000_100.0),
    ])
    path = root / _compliant_xweather_name("a.json")
    record = json.loads(path.read_text())
    record["raw_body_codec"] = "lz4+base64"
    encoded = json.dumps(record, sort_keys=True) + "\n"
    path.write_text(encoded)
    journal = root / "journal.jsonl"
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    rows[0]["artifact_sha256"] = _sha(encoded.encode("ascii"))
    journal.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")
    with pytest.raises(EvidenceError, match="XWEATHER_CODEC_UNSUPPORTED"):
        load_xweather_snapshots(root)


def test_xweather_journal_artifact_path_escape_rejected(tmp_path):
    root = tmp_path / "xweather"
    root.mkdir()
    (root / "journal.jsonl").write_text(json.dumps({
        "artifact": "../outside.json", "artifact_sha256": "0" * 64, "source_sha256": "0" * 64,
        "received_epoch": 1_700_000_000.0, "station_count": 0, "candidate_qc_count": 0, "key_slot": 1,
    }) + "\n")
    with pytest.raises(EvidenceError, match="XWEATHER_JOURNAL_ARTIFACT_NAME_REJECTED"):
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
    assert summary["collection_started_at"] == 1_700_000_050.0
    cadence = {c["station"]: c for c in summary["station_cadence"]}
    assert cadence["PWS_NEAR"]["distinct_observation_count"] == 2
    assert cadence["PWS_NEAR"]["min_interval_seconds"] == 300
    assert cadence["PWS_FAR"]["distinct_observation_count"] == 1
    assert cadence["PWS_FAR"]["min_interval_seconds"] is None


def test_distinct_station_count_separates_all_seen_from_qc_passing(tmp_path):
    root = _xweather_root(tmp_path, [
        dict(name="a.json", stations=[_station("PWS_OK"), _station("PWS_BAD", qc=0)],
             received_epoch=1_700_000_100.0),
    ])
    snapshots = load_xweather_snapshots(root)
    summary = summarize_xweather(snapshots, redact=False)
    assert summary["distinct_station_count"] == 2
    assert summary["distinct_station_count_qc_passing"] == 1
    assert "2" in summary["caveat"] and "1" in summary["caveat"]


def test_redaction_hides_real_station_ids_by_default(tmp_path):
    root = _xweather_root(tmp_path, [
        dict(name="a.json", stations=[_station("PWS_SECRET_NAME")], received_epoch=1_700_000_050.0),
    ])
    snapshots = load_xweather_snapshots(root)
    redacted = summarize_xweather(snapshots, redact=True)
    labels = {c["station"] for c in redacted["station_cadence"]}
    assert "PWS_SECRET_NAME" not in labels
    assert all(label.startswith("PWS_") and label != "PWS_SECRET_NAME" for label in labels)


# ---------------------------------------------------------------------------
# AWC load/verify, journal binding (F3), straddle computation (F1)
# ---------------------------------------------------------------------------

def test_awc_captures_loaded_journaled_and_straddle_confirmed(tmp_path):
    t0 = dt.datetime(2026, 10, 8, 18, 0, 0, tzinfo=dt.timezone.utc)
    t1 = dt.datetime(2026, 10, 8, 18, 10, 0, tzinfo=dt.timezone.utc)
    observed = t1.timestamp() - 120
    root = _awc_root(tmp_path, [
        dict(observed_at=observed - 3600, temp=19.0, received_iso=t0.isoformat().replace("+00:00", "Z")),
        dict(observed_at=observed, temp=21.0, received_iso=t1.isoformat().replace("+00:00", "Z")),
    ])
    official = load_official_captures(root)
    assert len(official["captures"]) == 2
    summary = summarize_official(official)
    row = summary["by_observed_at"][observed]
    assert row["straddle_confirmed"] is True
    assert row["straddle_lower_bound"] == t0.timestamp()
    assert row["temperature_c"] == 21.0


def test_first_ever_awc_capture_is_censored_as_collector_startup(tmp_path):
    received = dt.datetime(2026, 10, 8, 18, 0, 0, tzinfo=dt.timezone.utc)
    observed = received.timestamp() - 120
    root = _awc_root(tmp_path, [
        dict(observed_at=observed, temp=21.0, received_iso=received.isoformat().replace("+00:00", "Z")),
    ])
    official = load_official_captures(root)
    summary = summarize_official(official)
    row = summary["by_observed_at"][observed]
    assert row["straddle_confirmed"] is False
    assert row["censored_reason"] == "NO_PRECEDING_AWC_CAPTURE_COLLECTOR_STARTUP"
    assert row["straddle_lower_bound"] is None


def test_large_awc_poll_gap_is_censored_not_confirmed():
    official = _official_loaded([
        _capture(100.0, []),
        _capture(100.0 + MAX_STRADDLE_GAP_SECONDS + 1.0, [900.0]),
    ])
    summary = summarize_official(official)
    row = summary["by_observed_at"][900.0]
    assert row["straddle_confirmed"] is False
    assert row["censored_reason"] == "AWC_POLL_GAP_TOO_LARGE"


def test_awc_poll_gap_at_boundary_is_confirmed():
    official = _official_loaded([
        _capture(100.0, []),
        _capture(100.0 + MAX_STRADDLE_GAP_SECONDS, [900.0]),
    ])
    summary = summarize_official(official)
    row = summary["by_observed_at"][900.0]
    assert row["straddle_confirmed"] is True
    assert row["straddle_lower_bound"] == 100.0


def test_awc_raw_tamper_fails_closed(tmp_path):
    root = _awc_root(tmp_path, [
        dict(observed_at=1_791_000_000.0, temp=20.0, received_iso="2026-10-08T18:00:00Z"),
    ])
    path = root / "awc-katl-000.json"
    record = json.loads(path.read_text())
    record["raw_byte_length"] += 1
    encoded = json.dumps(record, sort_keys=True) + "\n"
    path.write_text(encoded)
    journal = root / "journal.jsonl"
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    rows[0]["artifact_sha256"] = _sha(encoded.encode("ascii"))  # keep artifact-sha check passing
    journal.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")
    with pytest.raises(EvidenceError, match="AWC_RAW_SHA_OR_LENGTH_MISMATCH"):
        load_official_captures(root)


def test_awc_artifact_not_journaled_fails_closed(tmp_path):
    root = tmp_path / "awc"
    root.mkdir()
    _write_awc_capture(root, "awc-katl-000.json", observed_at=1_791_000_000.0, temp=20.0,
                        received_iso="2026-10-08T18:00:00Z")
    (root / "journal.jsonl").write_text("")  # file exists but is never journaled
    with pytest.raises(EvidenceError, match="AWC_ARTIFACT_NOT_JOURNALED"):
        load_official_captures(root)


def test_awc_artifact_sha_journal_mismatch_fails_closed(tmp_path):
    root = _awc_root(tmp_path, [
        dict(observed_at=1_791_000_000.0, temp=20.0, received_iso="2026-10-08T18:00:00Z"),
    ])
    journal = root / "journal.jsonl"
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    rows[0]["artifact_sha256"] = "0" * 64
    journal.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")
    with pytest.raises(EvidenceError, match="AWC_ARTIFACT_SHA_JOURNAL_MISMATCH"):
        load_official_captures(root)


def test_awc_raw_sha_journal_mismatch_fails_closed(tmp_path):
    root = _awc_root(tmp_path, [
        dict(observed_at=1_791_000_000.0, temp=20.0, received_iso="2026-10-08T18:00:00Z"),
    ])
    journal = root / "journal.jsonl"
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    rows[0]["raw_sha256"] = "0" * 64
    journal.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")
    with pytest.raises(EvidenceError, match="AWC_RAW_SHA_JOURNAL_MISMATCH"):
        load_official_captures(root)


def test_awc_receipt_time_journal_mismatch_fails_closed(tmp_path):
    root = _awc_root(tmp_path, [
        dict(observed_at=1_791_000_000.0, temp=20.0, received_iso="2026-10-08T18:00:00Z"),
    ])
    journal = root / "journal.jsonl"
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    rows[0]["receipt_time"] = "2026-10-08T19:00:00Z"
    journal.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")
    with pytest.raises(EvidenceError, match="AWC_RECEIPT_TIME_JOURNAL_MISMATCH"):
        load_official_captures(root)


def test_awc_journal_artifact_path_escape_rejected(tmp_path):
    root = tmp_path / "awc"
    root.mkdir()
    (root / "journal.jsonl").write_text(json.dumps({
        "artifact": "../outside.json", "artifact_sha256": "0" * 64, "raw_sha256": "0" * 64,
        "receipt_time": "2026-10-08T18:00:00Z",
    }) + "\n")
    with pytest.raises(EvidenceError, match="AWC_JOURNAL_ARTIFACT_NAME_REJECTED"):
        load_official_captures(root)


def test_unverified_station_identity_is_excluded_not_fabricated(tmp_path):
    """Rebuilt per review F4: the fixture uses a FRESH observation (well inside
    AWC_MAX_AGE_SECONDS) so the only thing that can reject it is the identity
    gate, not staleness. The companion test below proves the same fixture is
    accepted once identity is verified, isolating the real tested reason."""
    received = dt.datetime(2026, 10, 8, 18, 0, 0, tzinfo=dt.timezone.utc)
    observed_at = received.timestamp() - 120
    received_iso = received.isoformat().replace("+00:00", "Z")
    root = _awc_root(tmp_path, [
        dict(observed_at=observed_at, temp=20.0, received_iso=received_iso, station_identity_verified=False),
    ])
    official = load_official_captures(root)
    assert official["captures"] == []
    assert official["skipped_unverified_identity_capture_count"] == 1


def test_same_fresh_observation_is_accepted_when_identity_verified(tmp_path):
    received = dt.datetime(2026, 10, 8, 18, 0, 0, tzinfo=dt.timezone.utc)
    observed_at = received.timestamp() - 120
    received_iso = received.isoformat().replace("+00:00", "Z")
    root = _awc_root(tmp_path, [
        dict(observed_at=observed_at, temp=20.0, received_iso=received_iso, station_identity_verified=True),
    ])
    official = load_official_captures(root)
    assert len(official["captures"]) == 1
    assert official["captures"][0]["observations"][0]["observed_at"] == observed_at


# ---------------------------------------------------------------------------
# pair_forward_evidence: lead window, strict comparisons, straddle gating (F1/F2)
# ---------------------------------------------------------------------------

def test_lead_requires_receipt_time_not_observation_time():
    """Source-earlier-but-received-later must NOT count as a lead."""
    xweather_summary = _xw_summary({("PWS_A", 1_700_000_000.0): 1_700_005_000.0}, {"PWS_A"})
    official_summary = _official_summary({
        1_700_000_900.0: _official_row(1_700_000_900.0, 1_700_001_000.0, 20.0,
                                        straddle_lower_bound=1_700_000_400.0),
    })
    result = pair_forward_evidence(xweather_summary, official_summary, redact=False)
    row = result["official_comparison"][0]
    assert row["candidate_lead_exists"] is False
    assert row["candidate_receipt_lead_count"] == 0


def test_lead_recognized_when_alpha_received_pws_strictly_before_official_receipt():
    xweather_summary = _xw_summary({("PWS_A", 1_700_000_000.0): 1_700_000_500.0}, {"PWS_A"})
    official_summary = _official_summary({
        1_700_000_900.0: _official_row(1_700_000_900.0, 1_700_001_000.0, 20.0,
                                        straddle_lower_bound=1_700_000_400.0),
    })
    result = pair_forward_evidence(xweather_summary, official_summary, redact=False)
    row = result["official_comparison"][0]
    assert row["candidate_lead_exists"] is True
    assert row["candidate_within_near_distance_count"] == 1
    assert row["candidate_station_ids"] == ["PWS_A"]


def test_far_station_excluded_from_near_count_and_from_headline_flag():
    xweather_summary = _xw_summary({("PWS_FAR", 1_700_000_000.0): 1_700_000_500.0}, set())
    official_summary = _official_summary({
        1_700_000_900.0: _official_row(1_700_000_900.0, 1_700_001_000.0, 20.0,
                                        straddle_lower_bound=1_700_000_400.0),
    })
    result = pair_forward_evidence(xweather_summary, official_summary, redact=False)
    row = result["official_comparison"][0]
    assert row["candidate_lead_exists"] is True
    assert row["candidate_within_near_distance_count"] == 0
    assert row["candidate_near_lead_exists"] is False
    # F2: the headline flag is near-only; the far candidate must not flip it.
    assert result["any_candidate_lead_exists"] is False
    assert result["any_candidate_any_distance_lead_exists_secondary_diagnostic_only"] is True


def test_lookback_window_boundary_inclusive_and_exclusive():
    observed_at = 1_700_000_900.0
    lower = observed_at - LOOKBACK_SECONDS
    official_summary = _official_summary({
        observed_at: _official_row(observed_at, observed_at + 100.0, 20.0,
                                    straddle_lower_bound=observed_at - 10.0),
    })
    xw_inside = _xw_summary({("PWS_IN", lower): observed_at - 5.0}, {"PWS_IN"})
    inside = pair_forward_evidence(xw_inside, official_summary, redact=False)["official_comparison"][0]
    assert inside["candidate_lead_exists"] is True

    xw_outside = _xw_summary({("PWS_OUT", lower - 1.0): observed_at - 5.0}, {"PWS_OUT"})
    outside = pair_forward_evidence(xw_outside, official_summary, redact=False)["official_comparison"][0]
    assert outside["candidate_lead_exists"] is False


def test_observation_time_upper_bound_boundary():
    observed_at = 1_700_000_900.0
    official_summary = _official_summary({
        observed_at: _official_row(observed_at, observed_at + 100.0, 20.0,
                                    straddle_lower_bound=observed_at - 10.0),
    })
    xw_at = _xw_summary({("PWS_AT", observed_at): observed_at - 5.0}, {"PWS_AT"})
    at = pair_forward_evidence(xw_at, official_summary, redact=False)["official_comparison"][0]
    assert at["candidate_lead_exists"] is True

    xw_after = _xw_summary({("PWS_AFTER", observed_at + 1.0): observed_at - 5.0}, {"PWS_AFTER"})
    after = pair_forward_evidence(xw_after, official_summary, redact=False)["official_comparison"][0]
    assert after["candidate_lead_exists"] is False


def test_receipt_equal_to_official_receipt_is_not_a_lead():
    observed_at = 1_700_000_900.0
    official_received_at = observed_at + 100.0
    official_summary = _official_summary({
        observed_at: _official_row(observed_at, official_received_at, 20.0,
                                    straddle_lower_bound=observed_at - 10.0),
    })
    xw_equal = _xw_summary({("PWS_EQ", observed_at): official_received_at}, {"PWS_EQ"})
    equal = pair_forward_evidence(xw_equal, official_summary, redact=False)["official_comparison"][0]
    assert equal["candidate_lead_exists"] is False

    xw_before = _xw_summary({("PWS_BEFORE", observed_at): official_received_at - 1.0}, {"PWS_BEFORE"})
    before = pair_forward_evidence(xw_before, official_summary, redact=False)["official_comparison"][0]
    assert before["candidate_lead_exists"] is True


def test_receipt_equal_to_straddle_lower_bound_is_not_a_lead():
    observed_at = 1_700_000_900.0
    straddle_lower_bound = observed_at - 10.0
    official_summary = _official_summary({
        observed_at: _official_row(observed_at, observed_at + 100.0, 20.0,
                                    straddle_lower_bound=straddle_lower_bound),
    })
    xw_equal = _xw_summary({("PWS_EQ", observed_at): straddle_lower_bound}, {"PWS_EQ"})
    equal = pair_forward_evidence(xw_equal, official_summary, redact=False)["official_comparison"][0]
    assert equal["candidate_lead_exists"] is False

    xw_after = _xw_summary({("PWS_AFTER", observed_at): straddle_lower_bound + 1.0}, {"PWS_AFTER"})
    after = pair_forward_evidence(xw_after, official_summary, redact=False)["official_comparison"][0]
    assert after["candidate_lead_exists"] is True


def test_unconfirmed_straddle_excludes_lead_even_when_old_window_would_have_counted():
    """Regression for the HIGH finding: without a confirmed AWC straddle, a PWS
    reading received up to an hour before the official receipt must not be
    treated as a lead, even though it falls inside LOOKBACK_SECONDS and before
    official_received_at -- this is exactly the degenerate case the review
    reproduced (every PWS reading from the previous hour 'counting')."""
    observed_at = 1_700_000_900.0
    official_received_at = observed_at + 100.0
    official_summary = _official_summary({
        observed_at: _official_row(observed_at, official_received_at, 20.0, straddle_confirmed=False,
                                    censored_reason="NO_PRECEDING_AWC_CAPTURE_COLLECTOR_STARTUP"),
    })
    xweather_summary = _xw_summary(
        {("PWS_OLD", observed_at - 3500.0): official_received_at - 3480.0}, {"PWS_OLD"})
    row = pair_forward_evidence(xweather_summary, official_summary, redact=False)["official_comparison"][0]
    assert row["lead_evaluation_excluded"] is True
    assert row["lead_evaluation_excluded_reason"] == "NO_PRECEDING_AWC_CAPTURE_COLLECTOR_STARTUP"
    assert row["candidate_lead_exists"] is False
    assert row["candidate_receipt_lead_count"] == 0
    assert row["candidate_station_ids"] == []


def test_observation_before_xweather_collection_start_excluded_as_warmup_backfill():
    observed_at = 1_700_000_900.0
    straddle_lower_bound = observed_at - 10.0
    official_summary = _official_summary({
        observed_at: _official_row(observed_at, observed_at + 100.0, 20.0,
                                    straddle_lower_bound=straddle_lower_bound),
    })
    collection_started_at = observed_at - 50.0
    xw_before_start = _xw_summary({("PWS_BACKFILL", observed_at - 60.0): straddle_lower_bound + 1.0},
                                   {"PWS_BACKFILL"}, collection_started_at=collection_started_at)
    before = pair_forward_evidence(xw_before_start, official_summary, redact=False)["official_comparison"][0]
    assert before["candidate_lead_exists"] is False

    xw_after_start = _xw_summary({("PWS_LIVE", observed_at - 40.0): straddle_lower_bound + 1.0},
                                  {"PWS_LIVE"}, collection_started_at=collection_started_at)
    after = pair_forward_evidence(xw_after_start, official_summary, redact=False)["official_comparison"][0]
    assert after["candidate_lead_exists"] is True


def test_no_xweather_collection_history_excludes_all_rows():
    observed_at = 1_700_000_900.0
    official_summary = _official_summary({
        observed_at: _official_row(observed_at, observed_at + 100.0, 20.0,
                                    straddle_lower_bound=observed_at - 10.0),
    })
    xweather_summary = _xw_summary({}, set(), collection_started_at=None)
    row = pair_forward_evidence(xweather_summary, official_summary, redact=False)["official_comparison"][0]
    assert row["lead_evaluation_excluded"] is True
    assert row["lead_evaluation_excluded_reason"] == "NO_XWEATHER_COLLECTION_HISTORY"


def test_candidate_station_ids_redacted_by_default():
    observed_at = 1_700_000_900.0
    official_summary = _official_summary({
        observed_at: _official_row(observed_at, observed_at + 100.0, 20.0,
                                    straddle_lower_bound=observed_at - 10.0),
    })
    xweather_summary = _xw_summary({("PWS_SECRET", observed_at): observed_at - 5.0}, {"PWS_SECRET"})
    redacted_row = pair_forward_evidence(xweather_summary, official_summary, redact=True)["official_comparison"][0]
    assert redacted_row["candidate_station_ids"] != ["PWS_SECRET"]
    assert all("PWS_SECRET" not in sid for sid in redacted_row["candidate_station_ids"])

    raw_row = pair_forward_evidence(xweather_summary, official_summary, redact=False)["official_comparison"][0]
    assert raw_row["candidate_station_ids"] == ["PWS_SECRET"]


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------

def test_build_report_is_non_qualifying_and_reports_wrh_as_not_captured(tmp_path):
    xw_root = _xweather_root(tmp_path, [
        dict(name="a.json", stations=[_station("PWS_ONE")], received_epoch=1_700_000_050.0),
    ])
    received = dt.datetime(2026, 10, 8, 18, 0, 0, tzinfo=dt.timezone.utc)
    awc_root = _awc_root(tmp_path, [
        dict(observed_at=received.timestamp() - 60, temp=19.0,
             received_iso=received.isoformat().replace("+00:00", "Z")),
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
    assert report["official_awc_comparator"]["skipped_unverified_identity_capture_count"] == 0
    assert report["official_awc_comparator"]["rejected_item_count"] == 0
    assert "earliest_receipt_by_observation" not in report["xweather_source"]
    assert "near_station_labels" not in report["xweather_source"]
    assert json.dumps(report)  # fully JSON-serializable, no stray tuple keys
