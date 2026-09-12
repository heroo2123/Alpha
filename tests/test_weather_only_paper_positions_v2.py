from __future__ import annotations

import asyncio
from pathlib import Path

from polymarket_scanner.weather_only_paper_control import WeatherPaperSettlementEngine
from polymarket_scanner.weather_only_paper_positions import WeatherPaperPositionStore
from polymarket_scanner.weather_only_paper_store import WeatherPaperStore


def _signal(store: WeatherPaperStore, *, fingerprint: str, ask_size: float, entry_cost: float = 0.00105) -> int:
    signal_id = store.save_signal(
        fingerprint=fingerprint,
        lane="weather_forecast_raw_gap",
        evidence_class="RESEARCH_ONLY_UNCALIBRATED",
        event_id="event-1",
        market_id="market-1",
        side="NO",
        token_id="token-no",
        model_probability=1.0,
        entry_cost=entry_cost,
        raw_gap=1.0 - entry_cost,
        theoretical_payout=1.0,
        created_at=100.0,
        payload={
            "event_title": "Temperature test",
            "ask_size": ask_size,
            "source_timestamp": 99.5,
        },
    )
    assert signal_id is not None
    store.mark_telegram_sent(signal_id, 123, sent_at=101.0)
    return signal_id


def test_sent_signal_becomes_visible_capacity_capped_position(tmp_path: Path):
    path = tmp_path / "paper.sqlite"
    signals = WeatherPaperStore(path)
    sid = _signal(signals, fingerprint="a", ask_size=100.0)
    positions = WeatherPaperPositionStore(path)

    row = positions.ensure_position_for_signal(sid, 10.0)
    assert row is not None
    assert row["status"] == "OPEN"
    # $10 / $0.00105 would request thousands of shares, but only 100 were
    # actually visible at the captured best ask.
    assert row["filled_units"] == 100.0
    assert abs(row["capital_used"] - 0.105) < 1e-12
    assert row["telegram_sent_at"] == 101.0
    assert row["opened_at"] == 101.0
    assert row["financial_authority"] == 0
    assert row["automatic_order_placement"] == 0


def test_zero_visible_capacity_is_recorded_as_no_fill_not_fake_position(tmp_path: Path):
    path = tmp_path / "paper.sqlite"
    signals = WeatherPaperStore(path)
    sid = _signal(signals, fingerprint="b", ask_size=0.0)
    positions = WeatherPaperPositionStore(path)

    row = positions.ensure_position_for_signal(sid, 10.0)
    assert row is not None
    assert row["status"] == "NO_FILL"
    assert row["capital_used"] == 0.0
    assert row["filled_units"] == 0.0
    assert row["no_fill_reason"] == "NO_VISIBLE_TOP_OF_BOOK_CAPACITY"


def test_backfill_preserves_already_sent_signals_and_is_idempotent(tmp_path: Path):
    path = tmp_path / "paper.sqlite"
    signals = WeatherPaperStore(path)
    _signal(signals, fingerprint="c", ask_size=20.0, entry_cost=0.5)
    positions = WeatherPaperPositionStore(path)

    first = positions.ensure_sent_positions(10.0)
    second = positions.ensure_sent_positions(10.0)
    assert len(first) == 1
    assert second == []
    assert positions.stats()["total"] == 1


def test_structural_signal_tracks_equal_share_bundle(tmp_path: Path):
    path = tmp_path / "paper.sqlite"
    signals = WeatherPaperStore(path)
    sid = signals.save_signal(
        fingerprint="struct",
        lane="weather_complete_bucket_underround",
        evidence_class="DETERMINISTIC_EXECUTION_UNVERIFIED",
        event_id="event-2",
        payload={
            "event_title": "Complete bucket set",
            "market_ids": ["m1", "m2"],
            "token_ids": ["t1", "t2"],
            "common_best_ask_shares": 2.0,
            "source_timestamp": 200.0,
        },
        entry_cost=0.95,
        raw_gap=0.05,
        theoretical_payout=1.0,
        created_at=200.0,
    )
    assert sid is not None
    signals.mark_telegram_sent(sid, 222, sent_at=201.0)
    positions = WeatherPaperPositionStore(path)

    row = positions.ensure_position_for_signal(sid, 10.0)
    assert row is not None
    assert row["position_kind"] == "STRUCTURAL"
    assert row["side"] == "BASKET"
    assert row["filled_units"] == 2.0
    assert abs(row["capital_used"] - 1.90) < 1e-12


class _FakeTelegram:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_html(self, text: str, **_kwargs) -> int:
        self.messages.append(text)
        return 777


class _FakeGamma:
    async def market_by_id(self, market_id: str):
        assert market_id == "market-1"
        return {
            "closed": True,
            "clobTokenIds": '["token-yes","token-no"]',
            "outcomePrices": '["0","1"]',
        }


async def _settle(path: Path):
    positions = WeatherPaperPositionStore(path)
    telegram = _FakeTelegram()
    engine = WeatherPaperSettlementEngine(store=positions, telegram=telegram, gamma=_FakeGamma())
    result = await engine.settle_once()
    return positions, telegram, result


def test_exact_no_token_winner_auto_resolves_and_updates_stats(tmp_path: Path):
    path = tmp_path / "paper.sqlite"
    signals = WeatherPaperStore(path)
    sid = _signal(signals, fingerprint="d", ask_size=10.0, entry_cost=0.5)
    positions = WeatherPaperPositionStore(path)
    positions.ensure_position_for_signal(sid, 10.0)

    positions, telegram, result = asyncio.run(_settle(path))
    assert result["resolved_now"] == 1
    assert result["resolution_messages_sent"] == 1
    assert len(telegram.messages) == 1
    row = positions.recent_positions(1, resolved_only=True)[0]
    assert row["status"] == "WON"
    assert row["settlement_payout_per_unit"] == 1.0
    assert row["capital_used"] == 5.0  # visible size caps 10 shares @ $0.50
    assert row["proceeds"] == 10.0
    assert row["pnl"] == 5.0
    assert row["roi"] == 1.0
    stats = positions.stats()
    assert stats["won"] == 1
    assert stats["lost"] == 0
    assert stats["pnl"] == 5.0
    assert stats["resolved_roi"] == 1.0
