import json
from pathlib import Path
import sqlite3

import pytest

from polymarket_scanner.v11.forensics import analyze_snapshot
from tools import v11_snapshot as snapshots


IDENTITY = dict(release_sha="a"*40, tree_sha="b"*40, config_sha256="c"*64)


def snapshot(source, destination, **kwargs):
    return snapshots.snapshot_database(source, destination, **IDENTITY, **kwargs)


def test_snapshot_includes_committed_wal_and_pins_one_read_transaction(tmp_path, monkeypatch):
    source = tmp_path / "active.sqlite"
    writer = sqlite3.connect(source)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE samples(id INTEGER PRIMARY KEY, value BLOB)")
        writer.execute("INSERT INTO samples VALUES(1, zeroblob(1048576))")
        writer.commit()
        assert Path(str(source) + "-wal").stat().st_size > 0
        inserted = []

        def concurrent_commit(_):
            if not inserted:
                writer.execute("INSERT INTO samples VALUES(2, zeroblob(1048576))")
                writer.commit()
                inserted.append(True)

        monkeypatch.setattr(snapshots.time, "sleep", concurrent_commit)
        result = snapshot(source, tmp_path / "snapshot")
        assert inserted and result["committed_wal_included"]
        reader, manifest = snapshots.open_verified_snapshot(tmp_path / "snapshot")
        try:
            assert reader.execute("SELECT id FROM samples").fetchall()[0][0] == 1
            assert reader.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                reader.execute("DELETE FROM samples")
        finally:
            reader.close()
        assert writer.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 2
        assert manifest["source_tree_sha"] == "b"*40
    finally:
        writer.close()


def test_bounds_do_not_delete_history_or_modify_source(tmp_path):
    source = tmp_path / "active.sqlite"
    with sqlite3.connect(source) as writer:
        writer.execute("CREATE TABLE samples(value TEXT)")
        writer.execute("INSERT INTO samples VALUES('retained')")
    before = source.read_bytes()
    for kwargs, reason in [({"max_bytes":4096}, "SIZE_LIMIT"),
                           ({"max_seconds":1e-9}, "DEADLINE")]:
        with pytest.raises(snapshots.SnapshotError, match=reason):
            snapshot(source, tmp_path / "failed", **kwargs)
        assert not (tmp_path / "failed").exists()
        assert not list(tmp_path.glob(".v11-snapshot-*"))
        assert source.read_bytes() == before
    snapshot(source, tmp_path / "complete")
    original = (tmp_path / "complete" / "manifest.json").read_bytes()
    with pytest.raises(snapshots.SnapshotError, match="NEW_DESTINATION"):
        snapshot(source, tmp_path / "complete")
    assert (tmp_path / "complete" / "manifest.json").read_bytes() == original


def test_snapshot_reader_rejects_active_or_tampered_copy(tmp_path):
    source = tmp_path / "active.sqlite"
    with sqlite3.connect(source) as writer:
        writer.execute("CREATE TABLE samples(id INTEGER)")
    directory = tmp_path / "complete"
    snapshot(source, directory)
    database = directory / "control.sqlite3"
    companion = Path(str(database) + "-wal")
    companion.touch()
    with pytest.raises(snapshots.SnapshotError, match="NOT_STANDALONE"):
        snapshots.open_verified_snapshot(directory)
    companion.unlink()
    database.chmod(0o600)
    with pytest.raises(snapshots.SnapshotError, match="READ_ONLY"):
        snapshots.open_verified_snapshot(directory)
    with database.open("ab") as stream:
        stream.write(b"tamper")
    database.chmod(0o400)
    with pytest.raises(snapshots.SnapshotError, match="HASH_MISMATCH"):
        snapshots.open_verified_snapshot(directory)


def test_symlink_source_is_refused(tmp_path):
    target = tmp_path / "target"
    target.write_text("not a database")
    link = tmp_path / "linked"
    link.symlink_to(target)
    with pytest.raises(snapshots.SnapshotError, match="SYMLINK"):
        snapshot(link, tmp_path / "complete")


def test_private_runtime_context_is_bracketed_and_hashed_not_claimed_atomic(tmp_path):
    source = tmp_path / "active.sqlite"
    with sqlite3.connect(source) as writer:
        writer.execute("CREATE TABLE samples(id INTEGER)")
    status = tmp_path / "status.json"
    status.write_text('{"cycle":"fixture"}')
    result = snapshot(source, tmp_path / "complete", context_files={"status":status})
    assert result["context_consistency"] == "BRACKETING_READS_NOT_SQL_TRANSACTION_ATOMIC"
    assert [r["phase"] for r in result["context_files"]] == ["before","after"]
    assert result["context_files"][0]["sha256"] == snapshots.sha256_file(status)
    for record in result["context_files"]:
        output = tmp_path / "complete" / record["filename"]
        assert output.read_bytes() == status.read_bytes()
        assert output.stat().st_mode & 0o777 == 0o400
    damaged = tmp_path / "complete" / result["context_files"][0]["filename"]
    damaged.chmod(0o600)
    damaged.write_bytes(b"changed")
    with pytest.raises(snapshots.SnapshotError, match="CONTEXT_HASH"):
        snapshots.open_verified_snapshot(tmp_path / "complete")


def test_cli_reassigns_only_new_private_outputs(tmp_path, monkeypatch, capsys):
    import pwd
    import sys
    from types import SimpleNamespace
    source = tmp_path / "active.sqlite"
    with sqlite3.connect(source) as writer:
        writer.execute("CREATE TABLE samples(id INTEGER)")
    source_mode = source.stat().st_mode
    destination = tmp_path / "complete"
    calls = []
    monkeypatch.setattr(pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=1000, pw_gid=1000))
    monkeypatch.setattr(snapshots.os, "geteuid", lambda: 0)
    monkeypatch.setattr(snapshots.os, "chown", lambda path,*args,**kwargs: calls.append(path))
    monkeypatch.setattr(sys, "argv", ["v11_snapshot.py", "--source",str(source),
        "--destination",str(destination),"--release-sha","a"*40,"--tree-sha","b"*40,
        "--config-sha256","c"*64,"--output-owner","alphaadmin"])
    assert snapshots.main() == 0
    assert set(calls) == {destination,destination/"control.sqlite3",destination/"manifest.json"}
    assert source.stat().st_mode == source_mode
    assert json.loads(capsys.readouterr().out)["status"] == "CAPTURED"


@pytest.fixture
def control(tmp_path):
    source = tmp_path / "synthetic-control.sqlite"
    with sqlite3.connect(source) as db:
        db.executescript("""
          CREATE TABLE weather_paper_signals(id INTEGER PRIMARY KEY, lane TEXT,
            event_id TEXT, token_id TEXT, side TEXT, payload_json TEXT, model_probability REAL);
          CREATE TABLE weather_paper_positions(id INTEGER PRIMARY KEY, signal_id INTEGER,
            lane TEXT,event_id TEXT,token_id TEXT,side TEXT,status TEXT,filled_units REAL,
            entry_cost_per_unit REAL,capital_used REAL,pnl REAL,proceeds REAL,
            settlement_payout_per_unit REAL,execution_protocol TEXT,validation_state TEXT,
            no_fill_reason TEXT);
          CREATE TABLE weather_paper_decisions(outcome TEXT,reason TEXT);
          CREATE TABLE weather_maker_test(status TEXT);
          INSERT INTO weather_paper_decisions VALUES('REJECT','insufficient_liquidity');
          INSERT INTO weather_maker_test VALUES('EXPIRED');
        """)
        for i, protocol, validation, pnl, status in [
            (1,"v5","VALIDATED",6,"WON"),
            (2,"v4","QUARANTINED",6,"WON"),
            (3,"v5","VALIDATED",999,"WON"),
            (4,"v5","VALIDATED",None,"NO_FILL"),
        ]:
            data = {"station":"TEST", "target_date":"2026-01-01", "side":"YES",
                    "family":"MAX", "raw_probability":.8, "raw_gap":.4, "ask_size":40}
            lane = "weather_forecast_raw_gap"
            db.execute("INSERT INTO weather_paper_signals VALUES(?,?,?,?,?,?,?)",
                       (i,lane,"event1","yes-token","YES",json.dumps(data),.8))
            filled = status != "NO_FILL"
            db.execute("INSERT INTO weather_paper_positions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (i,i,lane,"event1","yes-token","YES",status,10 if filled else 0,
                        .4,4 if filled else 0,pnl,10 if filled else None,1 if filled else None,
                        protocol,validation,None if filled else "BOOK_ABSENT"))
    directory = tmp_path / "synthetic-snapshot"
    snapshot(source, directory)
    return source, directory


def test_forensics_separates_epochs_quarantine_missing_funnels_and_bad_accounting(control):
    source, directory = control
    before = source.read_bytes()
    report = analyze_snapshot(directory)
    assert source.read_bytes() == before
    assert report["thresholds_selected"] is False
    assert report["all_core_records_diagnostic_only"]["reconciled_paper_pnl"] == 12
    assert report["all_core_records_diagnostic_only"]["live_validated_pnl"] is None
    assert len(report["epochs"]) == 2
    assert report["accounting_findings"] == [{"position_id":3,"findings":["PNL_RECONCILIATION_FAILED"]}]
    validated = next(r for r in report["epochs"] if r["validation"] == "VALIDATED")
    assert validated["reconciled_paper_pnl"] == 6
    assert validated["unique_station_days"] == 1
    funnel = report["zero_lane_funnels"]["weather_same_day_friend_lock"]
    assert funnel["discovered"] is None and funnel["persisted_signals"] == 0
    assert funnel["diagnosis"].startswith("UNKNOWN")
    assert report["separate_research_and_capture_tables"]["weather_maker_test"]["included_in_core_pnl"] is False
    assert all(r["slice"] == "UNKNOWN_NOT_PERSISTED" for r in report["future_forecast_slices"]["horizon_hours"])
    assert all(r["n_declared_groups"] == 1 and not r["independence_validated"]
               for r in report["stored_probability_diagnostics"])


def test_forensics_is_bounded_and_refuses_mutable_database(control):
    source, directory = control
    with pytest.raises(snapshots.SnapshotError, match="POSITION_LIMIT"):
        analyze_snapshot(directory, max_positions=1)
    with pytest.raises(snapshots.SnapshotError):
        analyze_snapshot(source.parent)


@pytest.mark.parametrize("update, expected",[
    ("settlement_payout_per_unit=-1,proceeds=-10,pnl=-14", "SETTLEMENT_PAYOUT_OUT_OF_RANGE"),
    ("settlement_payout_per_unit=2,proceeds=20,pnl=16", "SETTLEMENT_PAYOUT_OUT_OF_RANGE"),
    ("settlement_payout_per_unit=0,proceeds=0,pnl=-4", "SETTLEMENT_STATUS_PAYOUT_MISMATCH"),
    ("status='LOST'", "SETTLEMENT_STATUS_PAYOUT_MISMATCH"),
    ("token_id='other-token'", "SIGNAL_TOKEN_ID_MISMATCH"),
])
def test_algebraic_balance_does_not_validate_impossible_settlement_or_wrong_token(control,update,expected):
    source, _ = control
    with sqlite3.connect(source) as db:
        db.execute("UPDATE weather_paper_positions SET "+update+" WHERE id=1")
    directory = source.parent / "corrupt-synthetic-snapshot"
    snapshot(source, directory)
    report = analyze_snapshot(directory)
    findings = next(r["findings"] for r in report["accounting_findings"] if r["position_id"] == 1)
    assert expected in findings
    validated = next(r for r in report["epochs"] if r["validation"] == "VALIDATED")
    assert validated["reconciled_paper_pnl"] == 0
