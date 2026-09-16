from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from polymarket_scanner.weather_only_live_paper_final import (
    FINAL_PAPER_RUNTIME_VERSION,
    FinalWeatherLivePaperService,
)
from polymarket_scanner.weather_only_live_paper_three_layer_validation import (
    ThreeLayerValidationWeatherLivePaperService,
)
from polymarket_scanner.weather_only_live_paper_v4 import WeatherLivePaperV4Service
from polymarket_scanner.weather_only_paper_corrective import (
    CorrectivePaperError,
    final_token_payout_v4,
)
from polymarket_scanner.weather_only_same_day_capture_store import SameDayCaptureStore


FINAL_MODULE = "polymarket_scanner.weather_only_live_paper_three_layer_validation"


def test_structural_signal_lane_is_still_hard_disabled():
    service = object.__new__(WeatherLivePaperV4Service)
    service._v4_structural_suppressed_total = 0

    async def exercise():
        return await WeatherLivePaperV4Service._save_and_send_structural(
            service,
            {"event_id": "fixture"},
            None,
        )

    sent, error = asyncio.run(exercise())
    assert sent is False
    assert error is None
    assert service._v4_structural_suppressed_total == 1


def test_indicative_closed_prices_cannot_be_counted_as_final_payout():
    market = {
        "closed": True,
        "umaResolutionStatus": "resolved",
        "conditionId": "condition-1",
        "clobTokenIds": '["yes-token","no-token"]',
        "outcomes": '["Yes","No"]',
        "outcomePrices": '["0.99","0.01"]',
    }
    with pytest.raises(CorrectivePaperError, match="V4_SETTLEMENT_PAYOUT_VECTOR_INVALID"):
        final_token_payout_v4(
            "yes-token",
            market,
            expected_condition_id="condition-1",
            expected_side="YES",
        )


def test_same_day_research_store_can_never_count_as_trade_pnl(tmp_path):
    store = SameDayCaptureStore(tmp_path / "weather-paper.sqlite")
    summary = store.summary()
    assert summary["included_in_validated_pnl"] is False
    assert summary["same_day_delivery_enabled"] is False
    assert summary["financial_authority"] is False


def test_deployment_renderer_points_to_guarded_three_layer_wrapper():
    renderer = Path("deploy/render-weather-paper-unit.py").read_text(encoding="utf-8")
    assert FINAL_MODULE in renderer
    assert "{FINAL_WEATHER_MODULE}" in renderer
    assert issubclass(
        ThreeLayerValidationWeatherLivePaperService,
        FinalWeatherLivePaperService,
    )
    assert FINAL_PAPER_RUNTIME_VERSION == (
        "weather_live_paper_final_v4_fresh_gamma_semantic_binding"
    )


def test_code_gate_does_not_enable_real_money_pws_or_same_day_delivery():
    final_source = Path("polymarket_scanner/weather_only_live_paper_final.py").read_text(
        encoding="utf-8"
    )
    validation_source = Path(
        "polymarket_scanner/weather_only_live_paper_three_layer_validation.py"
    ).read_text(encoding="utf-8")
    combined = final_source + "\n" + validation_source
    assert '"same_day_delivery_enabled": False' in combined
    assert '"structural_delivery_enabled": False' in final_source
    assert '"financial_authority": False' in combined
    assert '"automatic_order_placement": False' in combined
    assert '"pws_enabled": False' in validation_source
