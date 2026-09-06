import asyncio
import threading

import polymarket_scanner.evaluator as evaluator
from polymarket_scanner.config import settings
from polymarket_scanner.models import Signal
from polymarket_scanner.store import Store
from polymarket_scanner.telegram import Telegram


def test_structural_detectors_are_not_repeated_on_every_market_tick(monkeypatch):
    calls = {"binary": 0, "neg": 0, "nested": 0}

    def counted(name):
        def inner(*_args):
            calls[name] += 1
            return []
        return inner

    monkeypatch.setattr(evaluator, "hardened_binary_buy_both", counted("binary"))
    monkeypatch.setattr(evaluator, "hardened_neg_risk_underround", counted("neg"))
    monkeypatch.setattr(evaluator, "hardened_nested_threshold_arbitrage", counted("nested"))
    monkeypatch.setattr(evaluator, "weather_late_lock", lambda *_: [])
    monkeypatch.setattr(evaluator, "sports_result_lag", lambda *_: [])
    monkeypatch.setattr(evaluator, "crypto_resolution_lag", lambda *_: [])
    monkeypatch.setattr(evaluator, "official_macro_release_lag", lambda *_: [])
    monkeypatch.setattr(evaluator, "crypto_crossfeed_divergence", lambda *_: [])
    monkeypatch.setattr(evaluator, "duplicate_divergence", lambda *_: [])
    monkeypatch.setattr(evaluator, "wide_spread_watch", lambda *_: [])
    monkeypatch.setattr(settings, "structural_scan_min_interval_seconds", 3600)

    evaluator._last_structural_at = 0.0
    evaluator._last_expensive_watch_at = 0.0

    kwargs = dict(
        markets=[], books={}, weather_markets=[], weather_cache={}, sports_cache={},
        crypto_stream=None, macro=None, fast_market=True, weather_refreshed=False,
        sports_trigger=False, crypto_trigger=False, macro_refreshed=False,
        run_watch=False,
    )
    evaluator.evaluate_signals(**kwargs)
    evaluator.evaluate_signals(**kwargs)

    assert calls == {"binary": 1, "neg": 1, "nested": 1}


def test_production_alert_outbox_write_is_off_event_loop(tmp_path, monkeypatch):
    db = tmp_path / "scanner.db"
    monkeypatch.setattr(settings, "db_path", str(db))
    monkeypatch.setattr(settings, "telegram_commands_in_app", False)

    store = Store(str(db))
    tg = Telegram(store)
    event_loop_thread = threading.get_ident()
    writer_threads = []

    def fake_enqueue(_signal_id, _priority):
        writer_threads.append(threading.get_ident())
        return True

    monkeypatch.setattr(tg.outbox, "enqueue_signal", fake_enqueue)
    signal = Signal(
        detector="test",
        confidence="WATCH",
        event_id="e",
        market_id="m",
        title="test",
        detail="test",
        url="https://example.com",
        edge=None,
        entry_cost=None,
        theoretical_payout=None,
        token_ids=[],
        metadata={},
    )

    async def run():
        await tg.send_signal(1, signal)
        await tg.close()

    asyncio.run(run())

    assert writer_threads
    assert writer_threads[0] != event_loop_thread
