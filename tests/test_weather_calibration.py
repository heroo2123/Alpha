import asyncio
from datetime import datetime, timezone

import pytest

import polymarket_scanner.trade_only as trade_only
from polymarket_scanner.models import Book, Signal
from polymarket_scanner.store import Store
from polymarket_scanner.weather_calibration import (
    MIN_BIN_RESOLVED,
    MIN_DISTINCT_STATIONS,
    MIN_TOTAL_RESOLVED,
    WEATHER_CALIBRATION_VERSION,
    apply_weather_calibration,
    calibration_for_score,
)


def _weather_signal(i: int, station: str, *, score: float = 0.99, model_version: str = "uncalibrated_v1") -> Signal:
    return Signal(
        detector="weather_late_lock",
        confidence="WATCH",
        event_id=f"event-{i}",
        market_id=f"market-{i}",
        title="weather calibration sample",
        detail="sample",
        url="https://example.com",
        edge=0.04,
        entry_cost=0.95,
        theoretical_payout=1.0,
        token_ids=[f"token-{i}"],
        metadata={
            "fingerprint_key": f"weather-{i}",
            "station": station,
            "lock_probability": score,
            "weather_model_version": model_version,
            "settlement_source_verified": True,
        },
    )


def _resolved_weather_samples(store: Store, n: int, *, wins: int | None = None, score: float = 0.99):
    wins = n if wins is None else wins
    for i in range(n):
        station = f"K{i % max(MIN_DISTINCT_STATIONS, 8):03d}"[-4:]
        signal_id = store.save_signal(_weather_signal(i, station, score=score))
        assert signal_id is not None
        store.resolve_payout(signal_id, 1.0 if i < wins else 0.0, 100.0)


def test_weather_calibration_fails_closed_with_small_sample(tmp_path):
    store = Store(str(tmp_path / "signals.db"))
    _resolved_weather_samples(store, 10)
    report = calibration_for_score(store.path, "weather_late_lock", "uncalibrated_v1", 0.99)
    assert report.ready is False
    assert report.total_resolved == 10
    assert "need" in report.reason


def test_weather_calibration_can_mature_only_with_fixed_clean_evidence(tmp_path):
    store = Store(str(tmp_path / "signals.db"))
    n = max(MIN_TOTAL_RESOLVED, MIN_BIN_RESOLVED)
    _resolved_weather_samples(store, n, score=0.99)
    report = calibration_for_score(store.path, "weather_late_lock", "uncalibrated_v1", 0.99)
    assert report.total_resolved == n
    assert report.bin_resolved == n
    assert report.distinct_stations >= MIN_DISTINCT_STATIONS
    assert report.overall_brier is not None and report.overall_brier < 0.001
    assert report.bin_lower_bound is not None and 0.0 < report.bin_lower_bound < 1.0
    assert report.ready is True


def test_weather_calibration_excludes_wrong_model_version_and_unverified_source(tmp_path):
    store = Store(str(tmp_path / "signals.db"))
    signal = _weather_signal(1, "KORD", model_version="old_buggy_model")
    signal_id = store.save_signal(signal)
    assert signal_id is not None
    store.resolve_payout(signal_id, 1.0, 100.0)

    signal2 = _weather_signal(2, "KJFK")
    signal2.metadata["settlement_source_verified"] = False
    signal_id2 = store.save_signal(signal2)
    assert signal_id2 is not None
    store.resolve_payout(signal_id2, 1.0, 100.0)

    report = calibration_for_score(store.path, "weather_late_lock", "uncalibrated_v1", 0.99)
    assert report.total_resolved == 0
    assert report.ready is False


def _trade_weather_signal() -> Signal:
    now = datetime.now(timezone.utc).isoformat()
    return Signal(
        detector="weather_late_lock",
        confidence="ACTIONABLE",
        event_id="e1",
        market_id="m1",
        title="weather",
        detail="weather",
        url="https://example.com",
        edge=0.06,
        entry_cost=0.93,
        theoretical_payout=1.0,
        token_ids=["yes-token"],
        metadata={
            "certification_status": "WEATHER_SEMANTICS_VERIFIED",
            "rest_confirmed_at": now,
            "confirmed_asks": [0.925],
            "confirmed_sizes": [100.0],
            "visible_common_shares": 100.0,
            "max_visible_notional_usd": 93.0,
            "lock_probability": 0.999,
            "weather_model_version": "uncalibrated_v1",
            "settlement_source_verified": True,
        },
    )


def test_promoted_weather_cannot_use_raw_heuristic_as_money_probability(monkeypatch):
    monkeypatch.setitem(trade_only._CERTIFICATIONS, "weather_late_lock", "WEATHER_SEMANTICS_VERIFIED")
    signal = _trade_weather_signal()
    assert trade_only.mark_trade_readiness(signal) is False
    assert "calibration" in signal.metadata["trade_ready_reason"]


def test_promoted_weather_uses_calibration_lower_bound_not_raw_score(monkeypatch):
    monkeypatch.setitem(trade_only._CERTIFICATIONS, "weather_late_lock", "WEATHER_SEMANTICS_VERIFIED")
    signal = _trade_weather_signal()
    signal.metadata.update({
        "weather_calibration_version": WEATHER_CALIBRATION_VERSION,
        "weather_calibration_ready": True,
        "calibrated_probability_lower_bound": 0.94,
    })

    class FakePoly:
        async def market_by_id(self, _mid):
            return {
                "active": True,
                "closed": False,
                "acceptingOrders": True,
                "enableOrderBook": True,
                "clobTokenIds": '["yes-token", "no-token"]',
            }

        async def books(self, _tokens):
            # Raw heuristic 0.999 would make this look profitable, but a 0.94
            # conservative empirical floor cannot clear the production edge gate.
            return {"yes-token": Book("yes-token", bids=[], asks=[(0.925, 100.0)])}

    assert asyncio.run(trade_only.refresh_trade_readiness(signal, FakePoly())) is False
    assert signal.metadata["trade_probability_basis"] == "WEATHER_EMPIRICAL_LOWER_BOUND"
    assert signal.edge is not None and signal.edge < 0.025


def test_apply_weather_calibration_attaches_current_database_evidence(tmp_path):
    store = Store(str(tmp_path / "signals.db"))
    n = max(MIN_TOTAL_RESOLVED, MIN_BIN_RESOLVED)
    _resolved_weather_samples(store, n, score=0.99)
    candidate = _weather_signal(9999, "KORD", score=0.99)
    assert apply_weather_calibration(candidate, store.path) is True
    assert candidate.metadata["weather_calibration_version"] == WEATHER_CALIBRATION_VERSION
    assert candidate.metadata["weather_calibration_n"] == n
    assert candidate.metadata["weather_calibration_ready"] is True
    assert candidate.metadata["calibrated_probability_lower_bound"] is not None
