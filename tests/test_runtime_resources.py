from pathlib import Path

from polymarket_scanner.runtime_resources import (
    RESOURCE_SNAPSHOT_VERSION,
    runtime_resource_snapshot,
)


def _fake_proc(root: Path) -> None:
    (root / "self" / "fd").mkdir(parents=True)
    (root / "self" / "status").write_text(
        "VmSize:\t  2000 kB\n"
        "VmRSS:\t   500 kB\n"
        "VmSwap:\t   25 kB\n"
        "Threads:\t7\n",
        encoding="utf-8",
    )
    (root / "loadavg").write_text("0.10 0.20 0.30 1/100 123\n", encoding="utf-8")
    for name in ("0", "1", "2"):
        (root / "self" / "fd" / name).write_text("x", encoding="utf-8")


def test_runtime_resource_snapshot_reports_proc_disk_and_sqlite_bytes(tmp_path):
    proc = tmp_path / "proc"
    _fake_proc(proc)
    db = tmp_path / "signals.db"
    db.write_bytes(b"a" * 11)
    (tmp_path / "signals.db-wal").write_bytes(b"b" * 7)
    (tmp_path / "signals.db-shm").write_bytes(b"c" * 5)

    snap = runtime_resource_snapshot(db_path=db, proc_root=proc, disk_path=tmp_path)

    assert snap["version"] == RESOURCE_SNAPSHOT_VERSION
    assert snap["scope"] == "LOCAL_PROCESS_AND_HOST_POINT_IN_TIME_NOT_CLOUD_BILLING"
    assert snap["procfs_available"] is True
    assert snap["process_rss_bytes"] == 500 * 1024
    assert snap["process_virtual_bytes"] == 2000 * 1024
    assert snap["process_swap_bytes"] == 25 * 1024
    assert snap["process_threads"] == 7
    assert snap["open_file_descriptors"] == 3
    assert snap["load_average_1m"] == 0.10
    assert snap["load_average_5m"] == 0.20
    assert snap["load_average_15m"] == 0.30
    assert snap["disk_total_bytes"] >= snap["disk_used_bytes"]
    assert snap["disk_free_bytes"] >= 0
    assert snap["database_bytes"] == 11
    assert snap["database_wal_bytes"] == 7
    assert snap["database_shm_bytes"] == 5
    assert snap["errors"] == []


def test_runtime_resource_snapshot_degrades_without_procfs(tmp_path):
    snap = runtime_resource_snapshot(
        db_path=tmp_path / "missing.db",
        proc_root=tmp_path / "missing-proc",
        disk_path=tmp_path,
    )
    assert snap["procfs_available"] is False
    assert snap["process_rss_bytes"] is None
    assert snap["process_swap_bytes"] is None
    assert snap["open_file_descriptors"] is None
    assert snap["database_bytes"] == 0
    assert snap["database_wal_bytes"] == 0
    assert snap["errors"]


def test_trade_only_health_persists_resource_snapshot_for_shadow_evidence():
    source = Path("app_trade_only.py").read_text(encoding="utf-8")
    assert "from polymarket_scanner.runtime_resources import runtime_resource_snapshot" in source
    assert 'snapshot["runtime_resources"] = runtime_resource_snapshot(db_path=base.settings.db_path)' in source
