import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import polymarket_scanner.trade_only as trade_only
from polymarket_scanner.execution_certificate import (
    EXECUTION_CERTIFICATE_TTL_SECONDS,
    EXECUTION_CERTIFICATE_VERSION,
)
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


def _weather_execution_cert():
    checked = datetime.now(timezone.utc)
    expires = checked + timedelta(seconds=EXECUTION_CERTIFICATE_TTL_SECONDS)
    return {
        "version": EXECUTION_CERTIFICATE_VERSION,
        "checked_at": checked.isoformat(),
        "expires_at": expires.isoformat(),
        "ttl_seconds": EXECUTION_CERTIFICATE_TTL_SECONDS,
        "legs": [{
            "market_id": "m1",
            "condition_id": "c1",
            "token_id": "yes-token",
            "question": "Will today's high be in this bucket?",
            "outcome": "Yes",
            "ask": "0.925",
            "safe_limit": "0.925",
            "safe_limit_text": "0.925",
            "tick_size": "0.001",
            "minimum_order_size": "5",
            "visible_best_ask_size": "100",
            "book_timestamp": "",
            "fee_rate": "0",
            "fee_exponent": 0,
            "fee_taker_only": True,
            "fee_per_share": "0",
            "cost_per_share": "0.925",
            "url": "https://polymarket.com/market/weather-test",
        }],
        "combined_cost": "0.925",
        "common_visible_shares": "100",
        "capacity_fraction": "0.50",
        "safe_common_shares": "50.00",
        "capacity_usd": "46.25000",
        "minimum_bundle_shares": "5",
        "minimum_bundle_notional_usd": "4.625",
        "depth_basis": "CURRENT_BATCH_BEST_ASK_WITH_50_PERCENT_SAFETY_HAIRCUT_UNCALIBRATED",
        "fee_basis": "test",
    }


def test_promoted_weather_cannot_use_raw_heuristic_as_money_probability(monkeypatch):
    monkeypatch.setitem(trade_only._CERTIFICATIONS, "weather_late_lock", "WEATHER_SEMANTICS_VERIFIED")
    signal = _trade_weather_signal()
    signal.metadata["execution_certificate"] = _weather_execution_cert()
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

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "mts": "0.001",
                "mos": "5",
                "t": [
                    {"t": "yes-token", "o": "Yes"},
                    {"t": "no-token", "o": "No"},
                ],
                "fd": {"r": 0, "e": 0, "to": True},
                "itode": False,
            }

    class FakeHTTP:
        async def get(self, _url):
            return FakeResponse()

    class FakePoly:
        def __init__(self):
            self.http = FakeHTTP()

        async def market_by_id(self, _mid):
            return {
                "id": "m1",
                "conditionId": "c1",
                "question": "Will today's high be in this bucket?",
                "slug": "weather-test",
                "active": True,
                "closed": False,
                "acceptingOrders": True,
                "enableOrderBook": True,
                "clobTokenIds": '["yes-token", "no-token"]',
                "outcomes": '["Yes", "No"]',
            }

        async def books(self, _tokens):
            # Raw heuristic 0.999 would look profitable. The prospective empirical
            # lower bound 0.94 minus the current exact ask 0.925 is only 1.5%.
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