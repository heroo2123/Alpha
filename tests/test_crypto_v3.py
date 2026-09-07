from __future__ import annotations

from dataclasses import dataclass

from polymarket_scanner.crypto_v3 import (
    CRYPTO_FEED_VERSION,
    crypto_crossfeed_divergence_v3,
    crypto_resolution_lag_v3,
)
from polymarket_scanner.models import Book, Market
from polymarket_scanner.streams import PriceTick


@dataclass
class FakeRTDS:
    connected: bool = True

    def __post_init__(self):
        self.nearest_map = {}
        self.latest_map = {}

    def nearest(self, topic, symbol, target_ts, tolerance):
        return self.nearest_map.get((topic, symbol, float(target_ts)))

    def latest(self, topic, symbol):
        return self.latest_map.get((topic, symbol))


def updown_market() -> Market:
    return Market(
        id="m1",
        event_id="e1",
        event_slug="btc-updown-5m-1000",
        event_title="Bitcoin Up or Down 5m",
        event_neg_risk=False,
        question="Bitcoin Up or Down?",
        slug="m1",
        condition_id="c1",
        outcomes=["Up", "Down"],
        token_ids=["up", "down"],
        outcome_prices=[0.5, 0.5],
        best_bid=0.7,
        best_ask=0.8,
        liquidity=1000,
        volume_24h=5000,
        active=True,
        closed=False,
        end_date="1970-01-01T00:21:40Z",
        description="Resolved using Chainlink price feed",
        resolution_source="Chainlink",
        category="Crypto",
        tags=[],
        raw={},
    )


def threshold_market() -> Market:
    return Market(
        id="m2",
        event_id="e2",
        event_slug="btc-above",
        event_title="Bitcoin above threshold",
        event_neg_risk=False,
        question="Will Bitcoin be above $50,000?",
        slug="m2",
        condition_id="c2",
        outcomes=["Yes", "No"],
        token_ids=["yes", "no"],
        outcome_prices=[0.5, 0.5],
        best_bid=0.7,
        best_ask=0.8,
        liquidity=1000,
        volume_24h=5000,
        active=True,
        closed=False,
        end_date="1970-01-01T00:21:40Z",
        description="Chainlink resolution source",
        resolution_source="Chainlink",
        category="Crypto",
        tags=[],
        raw={},
    )


def _progress(rtds: FakeRTDS, now_ts: float, *, age: float = 1.0):
    topic = "crypto_prices_chainlink"
    symbol = "btc/usd"
    rtds.latest_map[(topic, symbol)] = PriceTick(topic, symbol, 52000, now_ts - age)


def test_resolution_requires_currently_connected_feed():
    rtds = FakeRTDS(connected=False)
    market = updown_market()
    books = {"up": Book("up", [], [(0.8, 50)])}
    assert crypto_resolution_lag_v3([market], books, rtds, now_ts=1302) == []


def test_resolution_requires_same_topic_to_be_advancing_now():
    rtds = FakeRTDS()
    topic = "crypto_prices_chainlink"
    symbol = "btc/usd"
    rtds.nearest_map[(topic, symbol, 1000.0)] = PriceTick(topic, symbol, 50000, 1000.0)
    rtds.nearest_map[(topic, symbol, 1300.0)] = PriceTick(topic, symbol, 51000, 1300.0)
    books = {"up": Book("up", [], [(0.8, 50)])}

    # Socket flag alone is not evidence of progress.
    assert crypto_resolution_lag_v3([updown_market()], books, rtds, now_ts=1302) == []

    _progress(rtds, 1400, age=21)
    assert crypto_resolution_lag_v3([updown_market()], books, rtds, now_ts=1400) == []


def test_updown_resolution_requires_both_causal_boundary_ticks():
    rtds = FakeRTDS()
    topic = "crypto_prices_chainlink"
    symbol = "btc/usd"
    _progress(rtds, 1314)
    rtds.nearest_map[(topic, symbol, 1000.0)] = PriceTick(topic, symbol, 50000, 1000.0)
    # End tick is outside the configured ±12 second tolerance even if fake nearest returns it.
    rtds.nearest_map[(topic, symbol, 1300.0)] = PriceTick(topic, symbol, 51000, 1313.0)
    books = {"up": Book("up", [], [(0.8, 50)])}
    assert crypto_resolution_lag_v3([updown_market()], books, rtds, now_ts=1314) == []


def test_updown_resolution_rejects_future_source_tick():
    rtds = FakeRTDS()
    topic = "crypto_prices_chainlink"
    symbol = "btc/usd"
    _progress(rtds, 1302)
    rtds.nearest_map[(topic, symbol, 1000.0)] = PriceTick(topic, symbol, 50000, 1000.0)
    rtds.nearest_map[(topic, symbol, 1300.0)] = PriceTick(topic, symbol, 51000, 1305.0)
    books = {"up": Book("up", [], [(0.8, 50)])}
    # Source timestamp is >2 seconds ahead of local now, so it is not causal evidence.
    assert crypto_resolution_lag_v3([updown_market()], books, rtds, now_ts=1302) == []


def test_updown_resolution_accepts_connected_boundary_evidence_and_versions_it():
    rtds = FakeRTDS()
    topic = "crypto_prices_chainlink"
    symbol = "btc/usd"
    _progress(rtds, 1302)
    rtds.nearest_map[(topic, symbol, 1000.0)] = PriceTick(topic, symbol, 50000, 1000.0)
    rtds.nearest_map[(topic, symbol, 1300.0)] = PriceTick(topic, symbol, 51000, 1300.0)
    books = {"up": Book("up", [], [(0.8, 50)])}

    signals = crypto_resolution_lag_v3([updown_market()], books, rtds, now_ts=1302)
    assert len(signals) == 1
    signal = signals[0]
    assert signal.token_ids == ["up"]
    assert signal.metadata["crypto_feed_version"] == CRYPTO_FEED_VERSION
    assert signal.metadata["reference_start_tick_ts"] == 1000.0
    assert signal.metadata["reference_end_tick_ts"] == 1300.0
    assert signal.metadata["feed_progress_age_seconds"] == 1.0


def test_threshold_resolution_rejects_bad_boundary_and_accepts_exact_tick():
    rtds = FakeRTDS()
    topic = "crypto_prices_chainlink"
    symbol = "btc/usd"
    market = threshold_market()
    books = {"yes": Book("yes", [], [(0.8, 50)])}
    _progress(rtds, 1302)

    rtds.nearest_map[(topic, symbol, 1300.0)] = PriceTick(topic, symbol, 51000, 1287.0)
    assert crypto_resolution_lag_v3([market], books, rtds, now_ts=1302) == []

    rtds.nearest_map[(topic, symbol, 1300.0)] = PriceTick(topic, symbol, 51000, 1300.0)
    signals = crypto_resolution_lag_v3([market], books, rtds, now_ts=1302)
    assert len(signals) == 1
    assert signals[0].token_ids == ["yes"]


def test_crossfeed_requires_connected_and_two_fresh_ticks():
    market = updown_market()
    rtds = FakeRTDS(connected=False)
    assert crypto_crossfeed_divergence_v3([market], rtds, now_ts=2000) == []

    rtds.connected = True
    rtds.latest_map[("crypto_prices_chainlink", "btc/usd")] = PriceTick(
        "crypto_prices_chainlink", "btc/usd", 50000, 1979.0
    )
    rtds.latest_map[("crypto_prices", "btcusdt")] = PriceTick(
        "crypto_prices", "btcusdt", 50500, 1999.0
    )
    # Chainlink side is >20 seconds old.
    assert crypto_crossfeed_divergence_v3([market], rtds, now_ts=2000) == []


def test_crossfeed_rejects_timestamp_skew_and_future_ticks():
    market = updown_market()
    rtds = FakeRTDS()
    rtds.latest_map[("crypto_prices_chainlink", "btc/usd")] = PriceTick(
        "crypto_prices_chainlink", "btc/usd", 50000, 1990.0
    )
    rtds.latest_map[("crypto_prices", "btcusdt")] = PriceTick(
        "crypto_prices", "btcusdt", 50500, 1999.0
    )
    assert crypto_crossfeed_divergence_v3([market], rtds, now_ts=2000) == []

    rtds.latest_map[("crypto_prices_chainlink", "btc/usd")] = PriceTick(
        "crypto_prices_chainlink", "btc/usd", 50000, 2003.0
    )
    rtds.latest_map[("crypto_prices", "btcusdt")] = PriceTick(
        "crypto_prices", "btcusdt", 50500, 2000.0
    )
    assert crypto_crossfeed_divergence_v3([market], rtds, now_ts=2000) == []


def test_crossfeed_accepts_simultaneously_fresh_divergence():
    market = updown_market()
    rtds = FakeRTDS()
    rtds.latest_map[("crypto_prices_chainlink", "btc/usd")] = PriceTick(
        "crypto_prices_chainlink", "btc/usd", 50000, 1999.0
    )
    rtds.latest_map[("crypto_prices", "btcusdt")] = PriceTick(
        "crypto_prices", "btcusdt", 50500, 2000.0
    )

    signals = crypto_crossfeed_divergence_v3([market], rtds, now_ts=2000)
    assert len(signals) == 1
    signal = signals[0]
    assert signal.metadata["crypto_feed_version"] == CRYPTO_FEED_VERSION
    assert signal.metadata["tick_skew_seconds"] == 1.0
