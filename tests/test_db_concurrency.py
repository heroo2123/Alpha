import sqlite3
import time

import pytest

from polymarket_scanner.db_ops import DEFAULT_BUSY_TIMEOUT_MS, configure_database_runtime
from polymarket_scanner.store import Store


def test_wal_reader_progresses_while_other_process_style_writer_is_open(tmp_path):
    path = str(tmp_path / "signals.db")
    store = Store(path)
    store.set_state("probe", "old")
    runtime = configure_database_runtime(path)
    assert runtime["journal_mode"] == "wal"
    assert runtime["busy_timeout_ms"] == DEFAULT_BUSY_TIMEOUT_MS

    writer = sqlite3.connect(path, timeout=1.0)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE bot_state SET value='new' WHERE key='probe'")

        started = time.monotonic()
        # WAL readers should see the last committed snapshot without waiting for the
        # independent writer transaction to commit.
        assert store.get_state("probe") == "old"
        assert time.monotonic() - started < 0.5
    finally:
        writer.rollback()
        writer.close()


def test_second_writer_wait_is_explicitly_bounded(tmp_path):
    path = str(tmp_path / "signals.db")
    store = Store(path)
    store.set_state("probe", "old")
    configure_database_runtime(path)

    first = sqlite3.connect(path, timeout=1.0)
    second = sqlite3.connect(path, timeout=1.0)
    try:
        first.execute("BEGIN IMMEDIATE")
        first.execute("UPDATE bot_state SET value='held' WHERE key='probe'")
        second.execute("PRAGMA busy_timeout=50")

        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            second.execute("BEGIN IMMEDIATE")
        elapsed = time.monotonic() - started
        assert 0.03 <= elapsed < 0.5
    finally:
        first.rollback()
        second.rollback()
        first.close()
        second.close()
