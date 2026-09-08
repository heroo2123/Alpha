import json
from pathlib import Path

import pytest

from polymarket_scanner.crypto_v3 import CRYPTO_FEED_VERSION
from polymarket_scanner.models import Signal
from polymarket_scanner.store import CRYPTO_PRE_FIX_GROUP, Store


def _sig(version: str | None, key: str) -> Signal:
    metadata = {"fingerprint_key": key}
    if version is not None:
        metadata["crypto_feed_version"] = version
    return Signal(
        detector="crypto_resolution_lag",
        confidence="ACTIONABLE",
        event_id=f"e-{key}",
        market_id=f"m-{key}",
        title="crypto",
        detail="crypto",
        url="https://example.com",
        edge=0.10,
        entry_cost=0.80,
        theoretical_payout=1.0,
        token_ids=[f"t-{key}"],
        metadata=metadata,
    )


def test_pre_strict_crypto_rows_are_excluded_without_rewriting_forensic_pnl(tmp_path: Path):
    store = Store(str(tmp_path / "signals.db"))
    current = store.save_signal(_sig(CRYPTO_FEED_VERSION, "current"))
    old = store.save_signal(_sig("rtds_v3_connected_causal_progress", "old"))
    missing = store.save_signal(_sig(None, "missing-version"))
    assert current and old and missing

    store.resolve_payout(current, 1.0, 100.0)
    store.resolve_payout(old, 0.0, 100.0)
    store.resolve_payout(missing, 1.0, 100.0)

    old_row = store.get_signal(old)
    missing_row = store.get_signal(missing)
    assert old_row["status"] == "LOST" and old_row["pnl"] == -100.0
    assert missing_row["status"] == "WON" and missing_row["pnl"] == 25.0

    stats = store.stats()
    audit = stats["audit"]
    assert audit["directional_total"] == 1
    assert audit["directional_resolved"] == 1
    assert audit["directional_won"] == 1
    assert round(audit["resolved_pnl"], 2) == 25.0
    assert audit["known_bug_excluded"] == 2
    assert audit["known_bug_resolved"] == 2
    assert round(audit["known_bug_pnl"], 2) == -75.0

    by = {row["detector"]: row for row in stats["detectors"]}
    assert by["crypto_resolution_lag"]["evidence"] == "RESOLUTION_SCORED"
    assert by[CRYPTO_PRE_FIX_GROUP]["evidence"] == "KNOWN_BUG_EXCLUDED"
    assert by[CRYPTO_PRE_FIX_GROUP]["n"] == 2


def test_open_directional_and_new_manual_execution_require_current_crypto_feed_version(tmp_path: Path):
    store = Store(str(tmp_path / "signals.db"))
    current = store.save_signal(_sig(CRYPTO_FEED_VERSION, "current-open"))
    old = store.save_signal(_sig("rtds_v3_connected_causal_progress", "old-open"))
    assert current and old

    open_ids = {row["id"] for row in store.open_directional()}
    assert current in open_ids
    assert old not in open_ids

    recorded = store.record_manual(current, 100.0, 0.81)
    assert recorded["entry_source"] == "USER_REPORTED_EXECUTION"
    with pytest.raises(ValueError, match="strict source-timestamp"):
        store.record_manual(old, 100.0, 0.81)


def test_historical_user_reported_crypto_execution_is_preserved_but_excluded_after_version_floor(tmp_path: Path):
    store = Store(str(tmp_path / "signals.db"))
    signal_id = store.save_signal(_sig(CRYPTO_FEED_VERSION, "manual-history"))
    assert signal_id
    trade = store.record_manual(signal_id, 100.0, 0.80)
    assert store.manual_stats()["total"] == 1

    # Model a record captured under the former feed semantics without deleting or
    # rewriting its execution/settlement history.
    with store._conn() as c:
        row = c.execute("SELECT metadata FROM signals WHERE id=?", (signal_id,)).fetchone()
        meta = json.loads(row["metadata"])
        meta["crypto_feed_version"] = "rtds_v3_connected_causal_progress"
        c.execute("UPDATE signals SET metadata=? WHERE id=?", (json.dumps(meta), signal_id))

    stats = store.manual_stats()
    assert stats["total"] == 0
    assert stats["known_bug_excluded"] == 1
    with store._conn() as c:
        persisted = c.execute("SELECT stake,entry_cost,entry_source,status FROM manual_trades WHERE id=?", (trade["id"],)).fetchone()
    assert float(persisted["stake"]) == 100.0
    assert float(persisted["entry_cost"]) == 0.80
    assert persisted["entry_source"] == "USER_REPORTED_EXECUTION"
    assert persisted["status"] == "OPEN"
