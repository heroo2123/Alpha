from __future__ import annotations

import time
from pathlib import Path

import pytest

from polymarket_scanner.weather_only_contract_strict import (
    StrictWeatherContractError,
    compile_strict_temperature_event,
)
from polymarket_scanner.weather_only_paper_corrective import (
    PAPER_EXECUTION_PROTOCOL_V4,
    ClearWeatherPaperCommandController,
    CorrectivePaperError,
    final_token_payout_v4,
)
from polymarket_scanner.weather_only_paper_facade import CorrectiveWeatherPaperStore
from polymarket_scanner.weather_only_paper_positions import WeatherPaperPositionStore
from polymarket_scanner.weather_only_paper_store import WeatherPaperStore


def _market(mid: str, question: str, *, description: str = "") -> dict:
    return {
        "id": mid,
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "question": question,
        "description": description,
        "slug": f"market-{mid}",
        "conditionId": f"condition-{mid}",
        "outcomes": '["Yes","No"]',
        "clobTokenIds": f'["{mid}-yes","{mid}-no"]',
    }


def _event(*, title_day: int = 13, rule_day: int = 13, source: str = "https://www.weather.gov/wrh/timeseries?site=EDDM") -> dict:
    return {
        "id": "event-1",
        "slug": "munich-low",
        "title": f"Lowest temperature in Munich on September {title_day}?",
        "description": (
            f"Observation date {rule_day} Sep '26, in whole degrees Celsius. "
            "The market resolves using the lowest reading in the \"Temp\" column across all times on this day. "
            "On WRH select Hourly Data and show Hourly Data. "
            "If WRH is unavailable, use the Weather Underground Daily Observations table by 11:59 PM ET on the day following the observation date. "
            "If there is no data, the market resolves to the lowest bracket. "
            "Revisions are accepted until the first data point for the following date, whichever comes first, after which any alterations will not be considered."
        ),
        "resolutionSource": source,
        "markets": [
            _market("a", "Will the lowest temperature be 6°C or lower?"),
            _market("b", "Will the lowest temperature be 7-8°C?"),
            _market("c", "Will the lowest temperature be 9°C or higher?"),
        ],
    }


def test_strict_contract_rejects_yearless_title_rule_date_conflict():
    with pytest.raises(StrictWeatherContractError) as raised:
        compile_strict_temperature_event(_event(title_day=12, rule_day=13))
    assert raised.value.code == "STRICT_TITLE_DATE_MISMATCH"


def test_strict_contract_rejects_fake_wrh_host_and_mixed_statistic_child():
    with pytest.raises(StrictWeatherContractError) as raised:
        compile_strict_temperature_event(
            _event(source="https://untrusted.invalid/weather.gov/wrh/timeseries?site=EDDM")
        )
    assert raised.value.code == "STRICT_SOURCE_URL_UNTRUSTED"

    mixed = _event()
    mixed["markets"][1]["description"] = "This child uses the highest temperature."
    with pytest.raises(StrictWeatherContractError) as raised:
        compile_strict_temperature_event(mixed)
    assert raised.value.code == "STRICT_CHILD_STATISTIC_CONFLICT"


def test_strict_contract_rejects_exclusive_and_negated_bucket_wording():
    exclusive = _event()
    exclusive["markets"][1]["question"] = "Will the lowest temperature be less than 8°C?"
    with pytest.raises(StrictWeatherContractError) as raised:
        compile_strict_temperature_event(exclusive)
    assert raised.value.code == "STRICT_BUCKET_GRAMMAR_UNSUPPORTED"

    negated = _event()
    negated["markets"][1]["question"] = "Will the lowest temperature be not 8°C?"
    with pytest.raises(StrictWeatherContractError) as raised:
        compile_strict_temperature_event(negated)
    assert raised.value.code == "STRICT_BUCKET_GRAMMAR_UNSUPPORTED"


def test_closed_but_proposed_market_never_settles_and_arbitrary_prices_are_not_final():
    proposed = {
        "closed": True,
        "umaResolutionStatus": "proposed",
        "conditionId": "condition-a",
        "clobTokenIds": '["yes","no"]',
        "outcomes": '["Yes","No"]',
        "outcomePrices": '["0.99","0.01"]',
    }
    assert final_token_payout_v4(
        "yes", proposed, expected_condition_id="condition-a", expected_side="YES"
    ) is None

    malformed_final = dict(proposed)
    malformed_final["umaResolutionStatus"] = "resolved"
    with pytest.raises(CorrectivePaperError) as raised:
        final_token_payout_v4(
            "yes",
            malformed_final,
            expected_condition_id="condition-a",
            expected_side="YES",
        )
    assert raised.value.code == "V4_SETTLEMENT_PAYOUT_VECTOR_INVALID"


def test_final_binary_and_true_half_half_payouts_map_exact_token_meaning():
    winner = {
        "closed": True,
        "umaResolutionStatus": "resolved",
        "conditionId": "condition-a",
        "clobTokenIds": '["yes","no"]',
        "outcomes": '["Yes","No"]',
        "outcomePrices": '["1","0"]',
    }
    assert final_token_payout_v4(
        "yes", winner, expected_condition_id="condition-a", expected_side="YES"
    ) == 1.0
    assert final_token_payout_v4(
        "no", winner, expected_condition_id="condition-a", expected_side="NO"
    ) == 0.0

    split = dict(winner)
    split["outcomePrices"] = '["0.5","0.5"]'
    assert final_token_payout_v4(
        "yes", split, expected_condition_id="condition-a", expected_side="YES"
    ) == 0.5


def _v4_signal(
    store: CorrectiveWeatherPaperStore,
    *,
    fingerprint: str,
    message_id: int,
    book_hash: str = "same-book",
    ask_size: float = 5.0,
    frozen_stake: float = 10.0,
    minimum_order_size: float = 1.0,
) -> int:
    quote = 100.0
    payload = {
        "event_title": "Weather test",
        "station": "EDDM",
        "target_date": "2026-09-14",
        "condition_id": "condition-1",
        "ask": 0.50,
        "fee": 0.0,
        "entry_cost": 0.50,
        "ask_size": ask_size,
        "quote_observed_at": quote,
        "paper_fill_at": 101.0,
        "decision_expires_at": 120.0,
        "book_hash": book_hash,
        "minimum_order_size": minimum_order_size,
        "minimum_tick_size": 0.01,
        "decision_id": fingerprint,
        "paper_target_stake_usd": frozen_stake,
        "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V4,
    }
    sid = store.save_signal(
        fingerprint=fingerprint,
        lane="weather_forecast_raw_gap",
        evidence_class="RESEARCH_ONLY_UNCALIBRATED_V4",
        event_id="event-1",
        market_id="market-1",
        side="YES",
        token_id="token-yes",
        model_probability=0.8,
        entry_cost=0.5,
        raw_gap=0.3,
        theoretical_payout=1.0,
        created_at=101.0,
        payload=payload,
    )
    assert sid is not None
    store.mark_telegram_sent(sid, message_id, sent_at=102.0)
    store.set_signal_status(sid, "ACKNOWLEDGED")
    return sid


def test_v4_freezes_stake_and_does_not_reuse_same_captured_capacity(tmp_path: Path):
    path = tmp_path / "paper.sqlite"
    store = CorrectiveWeatherPaperStore(path)
    first = _v4_signal(store, fingerprint="d1", message_id=1)
    second = _v4_signal(store, fingerprint="d2", message_id=2)

    row1 = store.ensure_position_for_signal(first, 999.0)
    row2 = store.ensure_position_for_signal(second, 999.0)
    assert row1 is not None and row2 is not None
    assert row1["target_stake_usd"] == 10.0
    assert row1["filled_units"] == 5.0
    assert row1["capital_used"] == 2.5
    assert row1["opened_at"] == 101.0  # objective paper-fill time, not Telegram receipt
    assert row2["status"] == "NO_FILL"
    assert row2["filled_units"] == 0.0


def test_v4_below_minimum_order_is_explicit_no_fill(tmp_path: Path):
    store = CorrectiveWeatherPaperStore(tmp_path / "paper.sqlite")
    sid = _v4_signal(
        store,
        fingerprint="dust",
        message_id=3,
        ask_size=0.1,
        frozen_stake=0.01,
        minimum_order_size=5.0,
    )
    row = store.ensure_position_for_signal(sid, 500.0)
    assert row is not None
    assert row["status"] == "NO_FILL"
    assert row["no_fill_reason"] == "BELOW_MINIMUM_ORDER_SIZE"
    assert row["capital_used"] == 0.0


def test_pre_v4_positions_default_to_unverified_and_do_not_enter_v4_pnl(tmp_path: Path):
    path = tmp_path / "paper.sqlite"
    signals = WeatherPaperStore(path)
    sid = signals.save_signal(
        fingerprint="legacy",
        lane="weather_forecast_raw_gap",
        evidence_class="RESEARCH_ONLY_UNCALIBRATED",
        event_id="event-old",
        market_id="market-old",
        side="YES",
        token_id="old-yes",
        model_probability=0.9,
        entry_cost=0.2,
        raw_gap=0.7,
        theoretical_payout=1.0,
        created_at=10.0,
        payload={"ask_size": 10.0, "source_timestamp": 10.0},
    )
    assert sid is not None
    signals.mark_telegram_sent(sid, 100, sent_at=11.0)
    legacy = WeatherPaperPositionStore(path)
    legacy.ensure_position_for_signal(sid, 10.0)

    corrective = CorrectiveWeatherPaperStore(path)
    stats = corrective.stats()
    assert stats["total"] == 0
    assert stats["unverified"] == 1
    assert stats["open"] == 0
    assert stats["pnl"] == 0.0


def test_clear_stats_text_answers_open_finished_and_not_counted(tmp_path: Path):
    store = CorrectiveWeatherPaperStore(tmp_path / "paper.sqlite")
    controller = object.__new__(ClearWeatherPaperCommandController)
    controller.store = store
    text = controller._stats_text()
    assert "OPEN NOW" in text
    assert "FINISHED TRADES" in text
    assert "NOT TRADED / NOT COUNTED" in text
    assert "Net paper P&amp;L" in text
