from __future__ import annotations

from polymarket_scanner.weather_only_paper_store import WeatherPaperStore


def test_weather_paper_store_isolated_deduped_and_zero_authority(tmp_path):
    path = tmp_path / "weather-paper.sqlite"
    store = WeatherPaperStore(path)
    assert path.stat().st_mode & 0o777 == 0o600

    payload = {
        "lane": "weather_forecast_raw_gap",
        "event_id": "event-1",
        "paper_mode": True,
        "financial_authority": False,
        "automatic_order_placement": False,
    }
    signal_id = store.save_signal(
        fingerprint="fingerprint-1",
        lane="weather_forecast_raw_gap",
        evidence_class="RESEARCH_ONLY_UNCALIBRATED",
        event_id="event-1",
        market_id="market-1",
        side="YES",
        token_id="token-1",
        model_probability=0.70,
        entry_cost=0.50,
        raw_gap=0.20,
        theoretical_payout=1.0,
        payload=payload,
        created_at=123.0,
    )
    assert signal_id == 1
    assert store.save_signal(
        fingerprint="fingerprint-1",
        lane="weather_forecast_raw_gap",
        evidence_class="RESEARCH_ONLY_UNCALIBRATED",
        event_id="event-1",
        payload=payload,
        created_at=124.0,
    ) is None

    store.mark_telegram_sent(signal_id, 99, sent_at=125.0)
    row = store.recent(1)[0]
    assert row["telegram_message_id"] == 99
    assert row["execution_verified"] == 0
    assert row["financial_authority"] == 0
    assert row["automatic_order_placement"] == 0
    assert store.summary()["telegram_sent"] == 1
