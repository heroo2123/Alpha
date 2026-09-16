"""Populated SQLite upgrades and original Telegram receipt custody, offline."""
import asyncio
import json
import sqlite3

import pytest

from polymarket_scanner.production.config import ConfigurationError
from polymarket_scanner.production.legacy import import_legacy
from polymarket_scanner.production.service import SignalService
from polymarket_scanner.production.signals import SignalStore, SignalError
from polymarket_scanner.production.io import lease
from polymarket_scanner.weather_only_operator_state_corrective_v5 import OperatorStatePostReceiptStoreV5
from polymarket_scanner.weather_only_maker_paper_accounting import MakerPaperAccountingStore
from test_weather_stage2_semantic_terminal_maker import _signal, _legacy_order
from test_production_lifecycle import config

IDENTITY = {"bot_id": "100", "chat_id": "101"}


def populated(tmp_path):
    path = tmp_path / "predecessor" / "weather.db"
    path.parent.mkdir()
    old = OperatorStatePostReceiptStoreV5(path)
    sid, _, candidate = _signal(old, suffix="production-import")
    old.terminalize_delivered_signal(sid, terminal_status="EXPIRED", reason="old delivered alert",
        decision_id=candidate["decision_id"], event_id=candidate["event_id"],
        market_id=candidate["market_id"], side="YES", recorded_at=102)
    maker = MakerPaperAccountingStore(path)
    order = _legacy_order()
    maker.save_new_order(order, recorded_at=100.0)
    maker.record_settlement(order, payout_per_share=1.0, evidence={"source": "legacy-fixture"}, settled_at=210.0)
    maker.close()
    return path, sid


def import_to(store, source, **kwargs):
    with lease(store.path.parent / "weather-paper-runtime.lock"):
        return import_legacy(store, source, identity=IDENTITY, expected_identity=kwargs.get("expected", IDENTITY))


def test_populated_cutover_preserves_source_history_and_drains_original_outbox(tmp_path):
    source, _ = populated(tmp_path)
    with sqlite3.connect(source) as old:
        before = list(old.iterdump())
    cfg = config(tmp_path, "LIVE_SIGNALS")
    store = SignalStore(cfg.signal_db)
    first = import_to(store, source)
    assert first["new_research_rows"] > 3
    assert first["new_terminal_sync_rows"] == 1
    assert first["financial_rows_imported"] == 0
    assert not store.outcome_pending()
    assert not store.active()
    assert store.summary()["observed_signal_outcomes"] == 0
    assert store.summary()["signal_states"] == {}
    second = import_to(store, source)
    assert second["new_research_rows"] == second["new_terminal_sync_rows"] == 0
    with store.connect() as db:
        events = db.execute("SELECT evidence FROM live_legacy_research WHERE table_name='weather_maker_shadow_events'").fetchall()
        payloads = [json.loads(json.loads(row[0])["payload_json"]) for row in events]
        assert any(payload.get("paper_pnl") == 6.0 for payload in payloads)
        assert not db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'execution_%'").fetchall()
        with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE_LEGACY"):
            db.execute("UPDATE live_legacy_research SET classification='ACTUAL'")
    class Telegram:
        delivery_identity = IDENTITY
        edited = []
        async def invalidate(self, row):
            self.edited.append(row)
            return "DELETED"
    telegram = Telegram()
    asyncio.run(SignalService(cfg, store, telegram, None).sync())
    assert telegram.edited[0]["message_id"] == 1001
    assert store.summary()["operator_sync_pending_or_escalated"] == 0
    with sqlite3.connect(source) as old:
        assert list(old.iterdump()) == before
    store.close()


def test_legacy_chat_mismatch_and_source_mutation_are_explicit(tmp_path):
    source, sid = populated(tmp_path)
    store = SignalStore(config(tmp_path).signal_db)
    with pytest.raises(ConfigurationError, match="IDENTITY_MISMATCH"):
        import_to(store, source, expected=dict(IDENTITY, chat_id="202"))
    assert store.summary()["excluded_legacy_research_rows"] == 0
    import_to(store, source)
    with sqlite3.connect(source) as old:
        old.execute("UPDATE weather_paper_signals SET status='SETTLED' WHERE id=?", (sid,))
    with pytest.raises(SignalError, match="SOURCE_CHANGED"):
        import_to(store, source)
    assert len(store.pending_sync()) == 1
    store.close()


def test_legacy_import_outbox_failure_rolls_back_archives_and_receipt(tmp_path):
    source, _ = populated(tmp_path)
    store = SignalStore(config(tmp_path).signal_db)
    with store.connect() as db:
        db.execute("CREATE TRIGGER fail_import BEFORE INSERT ON live_sync BEGIN SELECT RAISE(ABORT,'injected migration failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="migration failure"):
        import_to(store, source)
    assert store.summary()["excluded_legacy_research_rows"] == 0
    assert not store.recent()
    assert not store.pending_sync()
    store.close()


def test_legacy_attempt_budget_is_preserved_at_cutover(tmp_path):
    source, sid = populated(tmp_path)
    with sqlite3.connect(source) as old:
        old.execute("UPDATE weather_paper_operator_sync SET attempts=8,state='FAILED' WHERE signal_id=?", (sid,))
    store = SignalStore(config(tmp_path).signal_db)
    import_to(store, source)
    assert not store.pending_sync()
    assert store.summary()["operator_sync_pending_or_escalated"] == 1
    with store.connect() as db:
        assert tuple(db.execute("SELECT attempts,state FROM live_sync").fetchone()) == (8, "ESCALATED")
    store.close()


def test_legacy_active_writer_blocks_import(tmp_path):
    source, _ = populated(tmp_path)
    store = SignalStore(config(tmp_path).signal_db)
    with lease(source.parent / "weather-paper-runtime.lock"):
        with pytest.raises(ConfigurationError, match="WORKER_ALREADY_RUNNING"):
            import_to(store, source)
    assert store.summary()["excluded_legacy_research_rows"] == 0
    store.close()
