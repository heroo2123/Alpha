"""Adversarial + end-to-end regressions for the forward (not retroactive)
Xweather PWSweather EvidenceStore ingest path.

Every fixture here is synthetic; none of it is genuine retained evidence and
none of it grants any PAPER/Gate3/R09 acceptance, lead, or calibration claim.
"""
import base64
import hashlib
import inspect
import json

import pytest

from polymarket_scanner.v11 import runtime_health as health_module
from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore
from test_v11_paper_coordinator import expect_refusal
from test_v11_pws_quality import official, policy
from tools import v11_xweather_pws_forward_ingest as ingest

ENDPOINT = "https://data.api.xweather.com/observations/within"
SCHEMA = "ALPHA_V11_XWEATHER_GROUPED_PWS_SOURCE_ONLY_V1"
KATL = (33.0, -84.0)
PARAMS = {"filter": "pws", "limit": "100", "p": "33.0,-84.0", "radius": "31miles"}
STATION = "KATL"
EVENT = "KATL-forward-ingest"


def _raw_body(stations):
    rows = []
    for sid, lat, lon, temp_c, observed_at in stations:
        rows.append({"id": sid, "dataSource": "PWS",
                     "ob": {"type": "station", "timestamp": observed_at, "tempC": temp_c,
                            "QCcode": 10, "trustFactor": 90, "QC": "OK", "recTimestamp": observed_at},
                     "loc": {"lat": lat, "long": lon}, "profile": {"elevM": 300.0}})
    return json.dumps({"success": True, "response": rows}).encode("utf-8")


def _artifact_blob(*, stations, received_epoch, key_slot=1, params=None, schema=SCHEMA, endpoint=ENDPOINT):
    raw_body = _raw_body(stations)
    raw_sha256 = hashlib.sha256(raw_body).hexdigest()
    record = {"schema": schema, "endpoint": endpoint, "request_parameters": params or PARAMS,
              "raw_body_b64": base64.b64encode(raw_body).decode("ascii"),
              "raw_sha256": raw_sha256, "raw_bytes": len(raw_body),
              "capture_received_epoch": received_epoch, "key_slot": key_slot}
    return json.dumps(record).encode("utf-8"), raw_sha256


def _write(root, *entries):
    """entries: (name, stations, received_epoch[, journal_received_epoch]) tuples."""
    journal = []
    for entry in entries:
        name, stations, received_epoch = entry[0], entry[1], entry[2]
        journal_epoch = entry[3] if len(entry) > 3 else received_epoch
        blob, raw_sha256 = _artifact_blob(stations=stations, received_epoch=received_epoch)
        (root / name).write_bytes(blob)
        journal.append({"artifact": name, "artifact_sha256": hashlib.sha256(blob).hexdigest(),
                         "source_sha256": raw_sha256, "received_epoch": journal_epoch,
                         "station_count": len(stations), "candidate_qc_count": len(stations),
                         "key_slot": 1, "financial_authority": False, "cost_tokens": 1.0})
    (root / "journal.jsonl").write_text("\n".join(json.dumps(r) for r in journal) + "\n")


def _store(tmp_path, *, now, name="store.sqlite3", namespace="CHALLENGER:forward-ingest-test"):
    path = tmp_path / "store_dir"
    path.mkdir(mode=0o700, exist_ok=True)
    return EvidenceStore(path / name, namespace, clock=lambda: now[0])


# ---------------------------------------------------------------------------
# Namespace refusal
# ---------------------------------------------------------------------------

def test_namespace_refuses_v11_paper(tmp_path):
    with expect_refusal("XWEATHER_FORWARD_NAMESPACE_MUST_BE_CHALLENGER"):
        ingest.open_forward_store(tmp_path / "s.sqlite3", "V11_PAPER")


def test_namespace_refuses_ablation(tmp_path):
    with expect_refusal("XWEATHER_FORWARD_NAMESPACE_MUST_BE_CHALLENGER"):
        ingest.open_forward_store(tmp_path / "s.sqlite3", "ABLATION:xweather-katl-qc-20261009")


def test_namespace_accepts_challenger(tmp_path):
    store = ingest.open_forward_store(tmp_path / "s.sqlite3", "CHALLENGER:forward-ingest-test")
    assert store.namespace == "CHALLENGER:forward-ingest-test"


# ---------------------------------------------------------------------------
# Window selection: recent-but-not-raised, future refusal, journal mismatch
# ---------------------------------------------------------------------------

def test_window_excludes_artifact_older_than_window_without_raising(tmp_path):
    root = tmp_path / "xw"; root.mkdir()
    now = [10_000.0]
    _write(root,
        ("xweather-20261008T180000-aaaaaa.json", [("PWS_OLD", 33.01, -84.0, 19.0, 100.0)], 100.0),
        ("xweather-20261009T000000-bbbbbb.json", [("PWS_NEW", 33.01, -84.0, 19.0, 9_990.0)], 9_990.0))
    store = _store(tmp_path, now=now)
    batch = ingest.select_forward_batch(root, store, now=now[0], window_seconds=600.0)
    assert [a["artifact"] for a in batch] == ["xweather-20261009T000000-bbbbbb.json"]


def test_future_collector_receipt_is_refused_not_dropped(tmp_path):
    root = tmp_path / "xw"; root.mkdir()
    now = [10_000.0]
    _write(root, ("xweather-20261009T010000-cccccc.json", [("PWS_FUT", 33.01, -84.0, 19.0, 9_990.0)], 10_500.0))
    store = _store(tmp_path, now=now)
    with expect_refusal("XWEATHER_FORWARD_COLLECTOR_RECEIPT_IN_FUTURE:xweather-20261009T010000-cccccc.json"):
        ingest.select_forward_batch(root, store, now=now[0], window_seconds=7200.0)


def test_tampered_artifact_sha_refused(tmp_path):
    root = tmp_path / "xw"; root.mkdir()
    now = [10_000.0]
    _write(root, ("xweather-20261008T180000-aaaaaa.json", [("PWS_A", 33.01, -84.0, 19.0, 9_990.0)], 9_995.0))
    path = root / "xweather-20261008T180000-aaaaaa.json"
    record = json.loads(path.read_bytes())
    record["raw_bytes"] += 1  # tamper after journaling
    path.write_bytes(json.dumps(record).encode("utf-8"))
    store = _store(tmp_path, now=now)
    with expect_refusal("XWEATHER_FORWARD_ARTIFACT_SHA_JOURNAL_MISMATCH:xweather-20261008T180000-aaaaaa.json"):
        ingest.select_forward_batch(root, store, now=now[0], window_seconds=7200.0)


def test_receipt_journal_mismatch_refused(tmp_path):
    """Journal declares one receipt; the artifact's own capture_received_epoch
    disagrees -- this must fail closed rather than trust either value alone."""
    root = tmp_path / "xw"; root.mkdir()
    now = [10_000.0]
    _write(root, ("xweather-20261008T180000-aaaaaa.json", [("PWS_A", 33.01, -84.0, 19.0, 9_990.0)], 9_995.0, 9_996.0))
    store = _store(tmp_path, now=now)
    with expect_refusal("XWEATHER_FORWARD_RECEIPT_JOURNAL_MISMATCH:xweather-20261008T180000-aaaaaa.json"):
        ingest.select_forward_batch(root, store, now=now[0], window_seconds=7200.0)


def test_absent_key_files_never_read_and_source_has_no_key_access():
    import inspect as _inspect
    source = _inspect.getsource(ingest)
    assert "api_key" not in source.lower() and "xweather_api_key" not in source.lower()
    assert "glob(" not in source  # Never scans the eval directory for arbitrary files.


# ---------------------------------------------------------------------------
# Batch bound (per-run, not whole-journal) and ascending order
# ---------------------------------------------------------------------------

def test_max_artifacts_bounds_batch_and_defers_remainder(tmp_path):
    root = tmp_path / "xw"; root.mkdir()
    now = [10_000.0]
    _write(root,
        ("xweather-20261008T180000-aaaaaa.json", [("PWS_A", 33.01, -84.0, 19.0, 9_000.0)], 9_001.0),
        ("xweather-20261008T190000-bbbbbb.json", [("PWS_A", 33.01, -84.0, 19.1, 9_100.0)], 9_101.0),
        ("xweather-20261008T200000-cccccc.json", [("PWS_A", 33.01, -84.0, 19.2, 9_200.0)], 9_201.0))
    store = _store(tmp_path, now=now)
    first = ingest.select_forward_batch(root, store, now=now[0], window_seconds=7200.0, max_artifacts=1)
    assert [a["artifact"] for a in first] == ["xweather-20261008T180000-aaaaaa.json"]
    ingest.ingest_forward_batch(store, first, event_id=EVENT, station=STATION, latitude=KATL[0], longitude=KATL[1])
    second = ingest.select_forward_batch(root, store, now=now[0], window_seconds=7200.0, max_artifacts=1)
    assert [a["artifact"] for a in second] == ["xweather-20261008T190000-bbbbbb.json"]  # a.json already ingested; never re-selected.


def test_ascending_collector_receipt_order(tmp_path):
    root = tmp_path / "xw"; root.mkdir()
    now = [10_000.0]
    _write(root,
        ("xweather-20261008T190000-bbbbbb.json", [("PWS_A", 33.01, -84.0, 19.1, 9_100.0)], 9_101.0),
        ("xweather-20261008T180000-aaaaaa.json", [("PWS_A", 33.01, -84.0, 19.0, 9_000.0)], 9_001.0))
    store = _store(tmp_path, now=now)
    batch = ingest.select_forward_batch(root, store, now=now[0], window_seconds=7200.0)
    assert [a["artifact"] for a in batch] == ["xweather-20261008T180000-aaaaaa.json", "xweather-20261008T190000-bbbbbb.json"]


# ---------------------------------------------------------------------------
# No backdating + idempotency
# ---------------------------------------------------------------------------

def test_v11_receipt_is_store_clock_not_collector_epoch(tmp_path):
    root = tmp_path / "xw"; root.mkdir()
    now = [10_000.0]
    collector_epoch = 9_000.0
    _write(root, ("xweather-20261008T180000-aaaaaa.json", [("PWS_A", 33.01, -84.0, 19.0, collector_epoch - 5.0)], collector_epoch))
    store = _store(tmp_path, now=now)
    batch = ingest.select_forward_batch(root, store, now=now[0], window_seconds=7200.0)
    result = ingest.ingest_forward_batch(store, batch, event_id=EVENT, station=STATION,
        latitude=KATL[0], longitude=KATL[1])
    raw = store.get(result[0]["raw_id"])
    assert raw["body"]["received_at"] == now[0] == 10_000.0
    assert raw["body"]["received_at"] != collector_epoch
    assert raw["body"]["payload"]["collector_received_epoch"] == collector_epoch
    assert result[0]["v11_receipt_minus_collector_receipt_seconds"] == now[0] - collector_epoch


def test_ingest_idempotent_does_not_renew_receipt_or_duplicate(tmp_path):
    root = tmp_path / "xw"; root.mkdir()
    now = [10_000.0]
    _write(root, ("xweather-20261008T180000-aaaaaa.json", [("PWS_A", 33.01, -84.0, 19.0, 9_995.0)], 9_999.0))
    store = _store(tmp_path, now=now)
    batch = ingest.select_forward_batch(root, store, now=now[0], window_seconds=7200.0)
    first = ingest.ingest_forward_batch(store, batch, event_id=EVENT, station=STATION,
        latitude=KATL[0], longitude=KATL[1])
    assert not first[0]["duplicate_content_skipped"]
    now[0] += 300.0  # Real wall time advances; the already-ingested receipt must not.
    batch_again = ingest.select_forward_batch(root, store, now=now[0], window_seconds=7200.0)
    assert batch_again == []  # Already ingested; never re-selected even though still in window.
    # Re-running ingest directly on the same (already-ingested) artifact dict
    # must also be a no-op, not a renewal -- exercised independently of selection.
    second = ingest.ingest_forward_batch(store, batch, event_id=EVENT, station=STATION,
        latitude=KATL[0], longitude=KATL[1])
    assert second[0]["duplicate_content_skipped"]
    assert second[0]["raw_id"] == first[0]["raw_id"]
    first_raw = store.get(first[0]["raw_id"])
    second_raw = store.get(second[0]["raw_id"])
    assert first_raw["body"]["received_at"] == second_raw["body"]["received_at"] == 10_000.0


# ---------------------------------------------------------------------------
# End-to-end: forward ingest -> PWSQualityWorker across two real-time-separated runs
# ---------------------------------------------------------------------------

def _patch_host_stamp(monkeypatch, mono, boot):
    monkeypatch.setattr(health_module, "host_stamp",
        lambda store: dict(boot_id=boot[0], monotonic=mono[0], wall=store.clock()))


def test_first_run_defers_clock_unhealthy_second_run_records_qc(tmp_path, monkeypatch):
    root = tmp_path / "xw"; root.mkdir()
    now = [100_000.0]
    mono, boot = [500.0], ["boot-a" * 4]
    _patch_host_stamp(monkeypatch, mono, boot)
    sync_probe = lambda: dict(synchronized=True, mechanism="SYNTHETIC_OFF_HOST_FIXTURE",
                              reason="TEST_ONLY", offset_seconds=None)
    small_policy = health_module.HealthPolicy(ingest.VERSION, 120.0, 120.0, 2.0, 2, 5.0, ("forward-ingest",))

    store = _store(tmp_path, now=now)
    # Two retained collector artifacts 600s apart: the reviewed PWSPolicy needs
    # >= minimum_samples observations spanning minimum_history_span per station.
    earlier = [("PWS_A", 33.01, -84.0, 19.6, now[0] - 630.0), ("PWS_B", 33.03, -84.0, 19.8, now[0] - 630.0),
               ("PWS_C", 33.05, -84.0, 19.9, now[0] - 630.0)]
    stations = [("PWS_A", 33.01, -84.0, 20.0, now[0] - 30.0), ("PWS_B", 33.03, -84.0, 20.1, now[0] - 30.0),
                ("PWS_C", 33.05, -84.0, 20.2, now[0] - 30.0)]
    _write(root, ("xweather-20261008T175000-cccccc.json", earlier, now[0] - 620.0),
           ("xweather-20261008T180000-aaaaaa.json", stations, now[0] - 20.0))
    batch = ingest.select_forward_batch(root, store, now=now[0], window_seconds=7200.0)
    ingest.ingest_forward_batch(store, batch, event_id=EVENT, station=STATION, latitude=KATL[0], longitude=KATL[1])

    first = ingest.run_quality_step(store, event_id=EVENT, official=official(), policy=policy(),
        command_id="run-1", health_policy=small_policy, sync_probe=sync_probe)
    assert first["body"]["details"]["outcome"] == "DEFERRED_CLOCK_UNHEALTHY"

    now[0] += 180.0; mono[0] += 180.0  # A second real-time-separated run, same persistent store.
    second = ingest.run_quality_step(store, event_id=EVENT, official=official(), policy=policy(),
        command_id="run-2", health_policy=small_policy, sync_probe=sync_probe)
    details = second["body"]["details"]
    assert details["outcome"] == "PWS_QC_RECORDED"
    qc_row = store.get(details["qc_id"])
    assert qc_row["body"]["payload"]["health"] == "HEALTHY"
    assert qc_row["body"]["payload"]["usable_station_count"] == 3
    assert qc_row["body"]["payload"]["provider"] == "XWEATHER_PWSWEATHER"
    assert not qc_row["body"]["payload"]["lead_advantage_verified"]
    assert not qc_row["body"]["payload"]["settlement_authority"]


def test_backlog_observation_older_than_parser_default_matches_qc_reparse(tmp_path, monkeypatch):
    """Regression (real Oct9 07:47Z run): a window-wide age gate archived
    observations the reviewed QC re-parse drops -> PWS_NORMALIZED_RAW_MISMATCH."""
    root = tmp_path / "xw"; root.mkdir()
    now = [100_000.0]
    mono, boot = [500.0], ["boot-a" * 4]
    _patch_host_stamp(monkeypatch, mono, boot)
    sync_probe = lambda: dict(synchronized=True, mechanism="SYNTHETIC_OFF_HOST_FIXTURE",
                              reason="TEST_ONLY", offset_seconds=None)
    small_policy = health_module.HealthPolicy(ingest.VERSION, 120.0, 120.0, 2.0, 2, 5.0, ("forward-ingest",))
    store = _store(tmp_path, now=now)
    stale = [("PWS_A", 33.01, -84.0, 19.0, now[0] - 5_000.0), ("PWS_B", 33.03, -84.0, 19.1, now[0] - 5_000.0),
             ("PWS_C", 33.05, -84.0, 19.2, now[0] - 5_000.0)]
    _write(root, ("xweather-20261008T163000-dddddd.json", stale, now[0] - 4_990.0))
    batch = ingest.select_forward_batch(root, store, now=now[0], window_seconds=7200.0)
    done = ingest.ingest_forward_batch(store, batch, event_id=EVENT, station=STATION,
                                       latitude=KATL[0], longitude=KATL[1])
    assert store.get(done[0]["normalized_id"])["body"]["payload"]["observations"] == []
    ingest.run_quality_step(store, event_id=EVENT, official=official(), policy=policy(),
        command_id="run-1", health_policy=small_policy, sync_probe=sync_probe)
    now[0] += 180.0; mono[0] += 180.0
    second = ingest.run_quality_step(store, event_id=EVENT, official=official(), policy=policy(),
        command_id="run-2", health_policy=small_policy, sync_probe=sync_probe)
    assert second["body"]["details"].get("reason") != "PWS_NORMALIZED_RAW_MISMATCH"


def test_module_has_stable_version_string():
    assert ingest.VERSION == "alpha_v11_xweather_pws_forward_ingest_v1"
