import base64
import hashlib
import json

import pytest

from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore
from polymarket_scanner.v11.pws_quality import archive_neighborhood
from test_v11_pws_quality import official, policy
from tools import v11_xweather_pws_history_replay as replay

ENDPOINT = "https://data.api.xweather.com/observations/within"
SCHEMA = "ALPHA_V11_XWEATHER_GROUPED_PWS_SOURCE_ONLY_V1"
# Centered on the fixture official() station (test_v11_pws_quality.official,
# 33.,-84.), not real-world KATL -- the Xweather request radius and the
# neighborhood distance-band check must agree on one reference point.
KATL = (33.0, -84.0)
PARAMS = {"filter": "pws", "limit": "100", "p": "33.0,-84.0", "radius": "31miles"}


def _raw_body(stations):
    rows = []
    for sid, lat, lon, temp_c, observed_at in stations:
        rows.append({"id": sid, "dataSource": "PWS",
                     "ob": {"type": "station", "timestamp": observed_at, "tempC": temp_c,
                            "QCcode": 10, "trustFactor": 90, "QC": "OK", "recTimestamp": observed_at},
                     "loc": {"lat": lat, "long": lon}, "profile": {"elevM": 300.0}})
    return json.dumps({"success": True, "response": rows}).encode("utf-8")


def _artifact(name, *, stations, received_epoch, key_slot=1, params=None):
    raw_body = _raw_body(stations)
    raw_sha256 = hashlib.sha256(raw_body).hexdigest()
    record = {"schema": SCHEMA, "endpoint": ENDPOINT, "request_parameters": params or PARAMS,
              "raw_body_b64": base64.b64encode(raw_body).decode("ascii"),
              "raw_sha256": raw_sha256, "raw_bytes": len(raw_body),
              "capture_received_epoch": received_epoch, "key_slot": key_slot}
    blob = json.dumps(record).encode("utf-8")
    return name, blob, raw_sha256, key_slot


def _write(root, *artifacts):
    journal = []
    for name, blob, raw_sha256, key_slot in artifacts:
        (root / name).write_bytes(blob)
        journal.append({"artifact": name, "artifact_sha256": hashlib.sha256(blob).hexdigest(),
                         "source_sha256": raw_sha256, "received_epoch": 0.0,
                         "station_count": 1, "candidate_qc_count": 1, "key_slot": key_slot,
                         "financial_authority": False, "cost_tokens": 1.0})
    (root / "journal.jsonl").write_text("\n".join(json.dumps(r) for r in journal) + "\n")


def _store(tmp_path, name="store.sqlite3"):
    path = tmp_path / "store"
    path.mkdir(mode=0o700)
    return replay.open_replay_store(path / name, "ABLATION:xweather-history-replay-test")


def test_genuine_multi_receipt_history_satisfies_qc_that_a_single_capture_cannot(tmp_path):
    """The exact MADIS failure mode -- a station with only one sample -- is
    solved by feeding multiple real, time-ordered Xweather artifacts, not by
    inventing extra samples inside one artifact."""
    root = tmp_path / "xw"; root.mkdir()
    _write(root,
        _artifact("xweather-20261008T180000-aaaaaa.json", stations=[("PWS_TESTA", 33.01, -84.0, 19.0, 1000.0)], received_epoch=1001.0),
        _artifact("xweather-20261008T180500-bbbbbb.json", stations=[("PWS_TESTA", 33.01, -84.0, 19.5, 1300.0)], received_epoch=1301.0),
        _artifact("xweather-20261008T181000-cccccc.json", stations=[("PWS_TESTA", 33.01, -84.0, 20.0, 1600.0)], received_epoch=1601.0))
    history = replay.verified_history(root)
    assert [a["artifact"] for a in history] == sorted(a["artifact"] for a in history)
    store = _store(tmp_path)
    result = replay.ingest_history(store, history, event_id="KATL-replay", station="KATL",
        latitude=KATL[0], longitude=KATL[1])
    assert all(not r["duplicate_content_skipped"] for r in result)
    qc = archive_neighborhood(store, "qc:katl", event_id="KATL-replay",
        capture_ids=tuple(dict.fromkeys(r["normalized_id"] for r in result)), official=official(),
        policy=policy(), provider="XWEATHER_PWSWEATHER")
    row = qc["body"]["payload"]["stations"][0]
    assert row["sample_count"] == 3 and row["continuous_sample_count"] == 3
    assert "INSUFFICIENT_RECENT_HISTORY" not in row["rejection_reasons"]
    # first_received_at reflects the first receipt of the *latest* observed
    # value (the existing, reviewed pws_quality.neighborhood semantics) --
    # here that is the third artifact's own true receipt, 1601.
    assert row["first_received_at"] == 1601.0


def test_no_retroactive_first_receipt_even_when_ingested_out_of_order(tmp_path):
    """Same station/observed_at reported in two polls; the recorded first
    receipt must be the true earlier one regardless of ingest list order."""
    root = tmp_path / "xw"; root.mkdir()
    _write(root,
        _artifact("xweather-20261008T180000-aaaaaa.json", stations=[("PWS_TESTA", 33.01, -84.0, 19.0, 1000.0)], received_epoch=1001.0),
        _artifact("xweather-20261008T180200-bbbbbb.json", stations=[("PWS_TESTA", 33.01, -84.0, 19.0, 1000.0)], received_epoch=1121.0))
    history = sorted(replay.verified_history(root), key=lambda a: a["artifact"], reverse=True)
    store = _store(tmp_path)
    result = replay.ingest_history(store, history, event_id="KATL-replay", station="KATL",
        latitude=KATL[0], longitude=KATL[1])
    qc = archive_neighborhood(store, "qc:katl", event_id="KATL-replay",
        capture_ids=tuple(dict.fromkeys(r["normalized_id"] for r in result)), official=official(),
        policy=policy(), provider="XWEATHER_PWSWEATHER")
    assert qc["body"]["payload"]["stations"][0]["first_received_at"] == 1001.0


def test_duplicate_journal_event_is_skipped_not_double_received(tmp_path):
    root = tmp_path / "xw"; root.mkdir()
    name, blob, raw_sha256, _slot = _artifact("xweather-20261008T180000-aaaaaa.json",
        stations=[("PWS_TESTA", 33.01, -84.0, 19.0, 1000.0)], received_epoch=1001.0)
    (root / name).write_bytes(blob)
    journal_row = {"artifact": name, "artifact_sha256": hashlib.sha256(blob).hexdigest(),
                    "source_sha256": raw_sha256, "received_epoch": 0.0, "station_count": 1,
                    "candidate_qc_count": 1, "key_slot": 1, "financial_authority": False, "cost_tokens": 1.0}
    (root / "journal.jsonl").write_text(json.dumps(journal_row) + "\n" + json.dumps(journal_row) + "\n")
    history = replay.verified_history(root)
    assert len(history) == 2
    store = _store(tmp_path)
    result = replay.ingest_history(store, history, event_id="KATL-replay", station="KATL",
        latitude=KATL[0], longitude=KATL[1])
    assert result[0]["duplicate_content_skipped"] is False
    assert result[1]["duplicate_content_skipped"] is True
    assert result[0]["raw_id"] == result[1]["raw_id"] == "xweather-raw:" + raw_sha256


def test_same_provider_different_key_slot_is_one_station_not_two(tmp_path):
    root = tmp_path / "xw"; root.mkdir()
    _write(root,
        _artifact("xweather-20261008T180000-aaaaaa.json", stations=[("PWS_TESTA", 33.01, -84.0, 19.0, 1000.0)], received_epoch=1001.0, key_slot=1),
        _artifact("xweather-20261008T180200-bbbbbb.json", stations=[("PWS_TESTA", 33.01, -84.0, 19.2, 1120.0)], received_epoch=1121.0, key_slot=2))
    history = replay.verified_history(root)
    assert {a["key_slot"] for a in history} == {1, 2}
    store = _store(tmp_path)
    result = replay.ingest_history(store, history, event_id="KATL-replay", station="KATL",
        latitude=KATL[0], longitude=KATL[1])
    qc = archive_neighborhood(store, "qc:katl", event_id="KATL-replay",
        capture_ids=tuple(dict.fromkeys(r["normalized_id"] for r in result)), official=official(),
        policy=policy(), provider="XWEATHER_PWSWEATHER")
    assert qc["body"]["payload"]["station_count"] == 1


def test_old_sensor_reading_is_not_treated_as_fresh(tmp_path):
    root = tmp_path / "xw"; root.mkdir()
    _write(root,
        _artifact("xweather-20261008T180000-aaaaaa.json", stations=[("PWS_OLD", 33.01, -84.0, 19.0, 100.0)], received_epoch=101.0))
    history = replay.verified_history(root)
    store = _store(tmp_path)
    result = replay.ingest_history(store, history, event_id="KATL-replay", station="KATL",
        latitude=KATL[0], longitude=KATL[1])
    # Evaluate QC at a later instant within the same retroactive timeline
    # (not real wall-clock "now") to see whether the true historical age
    # survives the replay or gets silently treated as fresh.
    replay.open_replay_store.clock_box[0] += 700.
    far_future_policy = policy(fresh_seconds=600., history_seconds=7200.)
    qc = archive_neighborhood(store, "qc:stale", event_id="KATL-replay",
        capture_ids=tuple(dict.fromkeys(r["normalized_id"] for r in result)), official=official(),
        policy=far_future_policy, provider="XWEATHER_PWSWEATHER")
    row = qc["body"]["payload"]["stations"][0]
    assert row["observed_at"] == 100.0 and row["age_seconds"] == qc["body"]["payload"]["as_of"] - 100.0
    assert "STALE" in row["rejection_reasons"] and row["weight"] == 0.


def test_absent_key_reads_never_touched(tmp_path):
    root = tmp_path / "xw"; root.mkdir()
    _write(root,
        _artifact("xweather-20261008T180000-aaaaaa.json", stations=[("PWS_TESTA", 33.01, -84.0, 19.0, 1000.0)], received_epoch=1001.0))
    assert not list(root.glob("*.xweather_api_key*"))
    history = replay.verified_history(root)
    assert len(history) == 1
    store = _store(tmp_path)
    replay.ingest_history(store, history, event_id="KATL-replay", station="KATL",
        latitude=KATL[0], longitude=KATL[1])
    import inspect
    source = inspect.getsource(replay)
    assert "api_key" not in source.lower() and ".xweather_api_key" not in source


@pytest.mark.parametrize("bad_name", ["../escape.json", "sub/dir.json"])
def test_injected_path_rejected(tmp_path, bad_name):
    root = tmp_path / "xw"; root.mkdir()
    name, blob, raw_sha256, _slot = _artifact("xweather-20261008T180000-aaaaaa.json",
        stations=[("PWS_TESTA", 33.01, -84.0, 19.0, 1000.0)], received_epoch=1001.0)
    journal_row = {"artifact": bad_name, "artifact_sha256": hashlib.sha256(blob).hexdigest(),
                   "source_sha256": raw_sha256, "received_epoch": 0.0, "station_count": 1,
                   "candidate_qc_count": 1, "key_slot": 1, "financial_authority": False, "cost_tokens": 1.0}
    (root / "journal.jsonl").write_text(json.dumps(journal_row) + "\n")
    with pytest.raises(EvidenceError):
        replay.verified_history(root)


def test_wrong_sha_rejected(tmp_path):
    """Artifact bytes modified after journaling: the journal's recorded
    artifact_sha256 must no longer match the file on disk, and the read
    must be refused rather than silently accepting the tampered content."""
    root = tmp_path / "xw"; root.mkdir()
    _write(root,
        _artifact("xweather-20261008T180000-aaaaaa.json", stations=[("PWS_TESTA", 33.01, -84.0, 19.0, 1000.0)], received_epoch=1001.0))
    path = root / "xweather-20261008T180000-aaaaaa.json"
    record = json.loads(path.read_bytes())
    record["raw_bytes"] += 1  # tamper after the journal entry was already written
    path.write_bytes(json.dumps(record).encode("utf-8"))
    with pytest.raises(EvidenceError):
        replay.verified_history(root)


def test_overlong_real_station_id_is_rejected_per_row_not_whole_neighborhood(tmp_path):
    """Real retained Xweather data contained a 34-char station id; it must be
    visibly rejected by the parser while the remaining stations still QC."""
    long_id = "PWS_PRINCESSDONUTTHEQUEENANNECHONK"
    assert len(long_id) == 34
    root = tmp_path / "xw"; root.mkdir()
    _write(root,
        _artifact("xweather-20261008T180000-aaaaaa.json", stations=[("PWS_TESTA", 33.01, -84.0, 19.0, 1000.0),
            (long_id, 33.02, -84.0, 19.1, 1000.0)], received_epoch=1001.0),
        _artifact("xweather-20261008T180500-bbbbbb.json", stations=[("PWS_TESTA", 33.01, -84.0, 19.5, 1300.0),
            (long_id, 33.02, -84.0, 19.6, 1300.0)], received_epoch=1301.0),
        _artifact("xweather-20261008T181000-cccccc.json", stations=[("PWS_TESTA", 33.01, -84.0, 20.0, 1600.0),
            (long_id, 33.02, -84.0, 20.1, 1600.0)], received_epoch=1601.0))
    store = _store(tmp_path)
    result = replay.ingest_history(store, replay.verified_history(root), event_id="KATL-replay", station="KATL",
        latitude=KATL[0], longitude=KATL[1])
    for r in result:
        payload = store.get(r["normalized_id"])["body"]["payload"]
        assert payload["rejections"] == {"XWEATHER_STATION_ID_INVALID": 1}
        assert [o["station"] for o in payload["observations"]] == ["PWS_TESTA"]
    qc = archive_neighborhood(store, "qc:long-id", event_id="KATL-replay",
        capture_ids=tuple(dict.fromkeys(r["normalized_id"] for r in result)), official=official(),
        policy=policy(), provider="XWEATHER_PWSWEATHER")
    payload = qc["body"]["payload"]
    assert payload["station_count"] == 1 and len(payload["stations"]) == 1
