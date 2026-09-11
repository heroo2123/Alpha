from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace

import pytest

from polymarket_scanner.weather_only_acceptance_containment import (
    WeatherW7ContainmentError,
    attest_weather_w7_read_only_surface,
    build_weather_w7_containment_manifest,
    derive_weather_w7_containment_counters,
    parse_linux_process_identity,
    read_linux_process_identity,
    read_weather_w7_database_snapshot,
    validate_weather_w7_process_identity,
    weather_w7_containment_reasons,
)
from polymarket_scanner.weather_only_clob import WeatherCLOBClient


BOOT_ID = "12345678-1234-1234-1234-123456789abc"


def _db(path):
    with sqlite3.connect(path) as c:
        c.executescript(
            """
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT UNIQUE,
                detector TEXT NOT NULL,
                confidence TEXT NOT NULL,
                event_id TEXT,
                market_id TEXT,
                title TEXT,
                detail TEXT,
                url TEXT,
                edge REAL,
                entry_cost REAL,
                theoretical_payout REAL,
                token_ids TEXT,
                metadata TEXT,
                status TEXT NOT NULL DEFAULT 'OPEN',
                pnl REAL,
                settlement_payout REAL,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );
            CREATE TABLE manual_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL,
                stake REAL NOT NULL,
                entry_cost REAL NOT NULL,
                entry_source TEXT NOT NULL,
                execution_at TEXT,
                status TEXT NOT NULL DEFAULT 'OPEN',
                pnl REAL,
                settlement_payout REAL,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );
            CREATE TABLE telegram_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL UNIQUE,
                priority INTEGER NOT NULL DEFAULT 10,
                status TEXT NOT NULL DEFAULT 'PENDING',
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at REAL NOT NULL,
                sent_at REAL,
                claimed_at REAL
            );
            """
        )
        c.execute(
            "INSERT INTO signals(fingerprint,detector,confidence,created_at) VALUES('f1','legacy','WATCH','2026-09-11T00:00:00Z')"
        )
        c.execute(
            "INSERT INTO telegram_outbox(signal_id,priority,status,attempts,next_attempt_at,created_at) VALUES(1,10,'PENDING',0,0,1.0)"
        )


def _stat(pid=321, start=987654):
    suffix = ["S"] + ["0"] * 18 + [str(start)] + ["0"] * 8
    return f"{pid} (python weather scanner) " + " ".join(suffix)


def _proc(root, *, pid=321, start=987654, cmdline=b"python\0-m\0polymarket_scanner.weather_only_runtime\0"):
    (root / str(pid)).mkdir(parents=True)
    (root / "sys" / "kernel" / "random").mkdir(parents=True)
    (root / str(pid) / "stat").write_text(_stat(pid, start), encoding="utf-8")
    (root / str(pid) / "cmdline").write_bytes(cmdline)
    (root / "sys" / "kernel" / "random" / "boot_id").write_text(BOOT_ID + "\n", encoding="ascii")


def test_sqlite_containment_snapshot_is_read_only_and_binds_full_table_state(tmp_path):
    path = tmp_path / "signals.db"
    _db(path)
    before_bytes = path.read_bytes()
    before_stat = path.stat()
    row = read_weather_w7_database_snapshot(path, captured_at=10.0)
    after_stat = path.stat()

    assert row.telegram_outbox_rows == 1
    assert row.signals_rows == 1
    assert row.manual_trades_rows == 0
    assert len(row.telegram_outbox_state_sha256) == 64
    assert len(row.schema_evidence_sha256) == 64
    assert row.financial_authority is False
    assert path.read_bytes() == before_bytes
    assert after_stat.st_size == before_stat.st_size

    # A status mutation is detected even when row count stays identical.
    with sqlite3.connect(path) as c:
        c.execute("UPDATE telegram_outbox SET status='SENT', sent_at=2.0 WHERE id=1")
    changed = read_weather_w7_database_snapshot(path, captured_at=20.0)
    assert changed.telegram_outbox_rows == row.telegram_outbox_rows
    assert changed.telegram_outbox_state_sha256 != row.telegram_outbox_state_sha256


def test_database_reader_rejects_symlink_and_schema_drift(tmp_path):
    path = tmp_path / "signals.db"
    _db(path)
    link = tmp_path / "link.db"
    link.symlink_to(path)
    with pytest.raises(WeatherW7ContainmentError) as symlink:
        read_weather_w7_database_snapshot(link)
    assert symlink.value.code == "W7_CONTAINMENT_DB_PATH_INVALID"

    broken = tmp_path / "broken.db"
    with sqlite3.connect(broken) as c:
        c.execute("CREATE TABLE signals(id INTEGER PRIMARY KEY)")
    with pytest.raises(WeatherW7ContainmentError) as schema:
        read_weather_w7_database_snapshot(broken)
    assert schema.value.code.startswith("W7_CONTAINMENT_DB_SCHEMA_INVALID")


def test_process_identity_parser_handles_comm_spaces_and_binds_start_boot_and_cmdline(tmp_path):
    parsed = parse_linux_process_identity(
        process_id=321,
        stat_text=_stat(),
        boot_id_text=BOOT_ID,
        cmdline_bytes=b"python\0-m\0weather\0",
    )
    assert parsed.process_id == 321
    assert parsed.start_time_ticks == 987654
    assert parsed.boot_id_sha256 == hashlib.sha256(BOOT_ID.encode("ascii")).hexdigest()
    assert len(parsed.cmdline_sha256) == 64
    assert validate_weather_w7_process_identity(parsed) == parsed

    root = tmp_path / "proc"
    _proc(root)
    assert read_linux_process_identity(321, proc_root=root).start_time_ticks == 987654


def test_process_identity_rejects_pid_recycling_tamper_and_bad_types():
    row = parse_linux_process_identity(
        process_id=321,
        stat_text=_stat(),
        boot_id_text=BOOT_ID,
        cmdline_bytes=b"python\0weather\0",
    )
    with pytest.raises(WeatherW7ContainmentError) as tamper:
        validate_weather_w7_process_identity(replace(row, start_time_ticks=row.start_time_ticks + 1))
    assert tamper.value.code == "W7_PROCESS_EVIDENCE_DIGEST_MISMATCH"

    for pid in (True, 0, -1, "321"):
        with pytest.raises(WeatherW7ContainmentError):
            parse_linux_process_identity(
                process_id=pid,
                stat_text=_stat(),
                boot_id_text=BOOT_ID,
                cmdline_bytes=b"python\0weather\0",
            )


def test_read_only_surface_fails_closed_if_order_method_appears(monkeypatch):
    row = attest_weather_w7_read_only_surface()
    assert row.order_api_exposed is False
    assert row.financial_delivery_api_exposed is False

    async def place_order(self):  # pragma: no cover - never invoked
        raise AssertionError

    monkeypatch.setattr(WeatherCLOBClient, "place_order", place_order, raising=False)
    with pytest.raises(WeatherW7ContainmentError) as raised:
        attest_weather_w7_read_only_surface()
    assert raised.value.code == "W7_CONTAINMENT_CLOB_METHOD_SURFACE_DRIFT"


def test_manifest_detects_same_count_outbox_change_signal_fill_change_and_process_restart(tmp_path):
    path = tmp_path / "signals.db"
    _db(path)
    before_db = read_weather_w7_database_snapshot(path, captured_at=10.0)
    before_proc = parse_linux_process_identity(
        process_id=321,
        stat_text=_stat(start=1000),
        boot_id_text=BOOT_ID,
        cmdline_bytes=b"python\0weather\0",
    )

    with sqlite3.connect(path) as c:
        c.execute("UPDATE telegram_outbox SET status='SENT' WHERE id=1")
        c.execute(
            "INSERT INTO signals(fingerprint,detector,confidence,created_at) VALUES('f2','unexpected','ACTIONABLE','2026-09-11T00:01:00Z')"
        )
        c.execute(
            "INSERT INTO manual_trades(signal_id,stake,entry_cost,entry_source,status,created_at) VALUES(1,10,0.5,'USER_REPORTED','OPEN','2026-09-11T00:01:00Z')"
        )
    after_db = read_weather_w7_database_snapshot(path, captured_at=20.0)
    after_proc = parse_linux_process_identity(
        process_id=322,
        stat_text=_stat(pid=322, start=2000),
        boot_id_text=BOOT_ID,
        cmdline_bytes=b"python\0weather\0",
    )
    manifest = build_weather_w7_containment_manifest(
        before_database=before_db,
        after_database=after_db,
        before_process=before_proc,
        after_process=after_proc,
    )
    reasons = weather_w7_containment_reasons(manifest)
    assert "TELEGRAM_OUTBOX_STATE_CHANGED" in reasons
    assert "SIGNAL_REGISTRY_STATE_CHANGED" in reasons
    assert "MANUAL_TRADE_STATE_CHANGED" in reasons
    assert "SCANNER_PROCESS_IDENTITY_CHANGED" in reasons

    counters = derive_weather_w7_containment_counters(manifest)
    assert counters["telegram_outbox_before"] == 1
    assert counters["telegram_outbox_after"] == 1  # same count, state digest still catches mutation
    assert counters["detector_promotions"] == 1
    assert counters["actual_fills_recorded"] == 1
    assert counters["service_restart_count"] == 1
    assert counters["order_attempts"] == 0
    assert counters["actual_orders_placed"] == 0


def test_unchanged_manifest_derives_all_zero_side_effect_counters(tmp_path):
    path = tmp_path / "signals.db"
    _db(path)
    before = read_weather_w7_database_snapshot(path, captured_at=10.0)
    after = read_weather_w7_database_snapshot(path, captured_at=20.0)
    process = parse_linux_process_identity(
        process_id=321,
        stat_text=_stat(),
        boot_id_text=BOOT_ID,
        cmdline_bytes=b"python\0weather\0",
    )
    manifest = build_weather_w7_containment_manifest(
        before_database=before,
        after_database=after,
        before_process=process,
        after_process=process,
    )
    assert weather_w7_containment_reasons(manifest) == ()
    assert derive_weather_w7_containment_counters(manifest) == {
        "telegram_outbox_before": 1,
        "telegram_outbox_after": 1,
        "detector_promotions": 0,
        "order_attempts": 0,
        "actual_orders_placed": 0,
        "actual_fills_recorded": 0,
        "service_restart_count": 0,
    }
