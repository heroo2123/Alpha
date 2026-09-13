from __future__ import annotations

from copy import deepcopy

import pytest

from polymarket_scanner.weather_only_deployment_acceptance import (
    WeatherDeploymentAcceptanceError,
    accept_first_weather_paper_cycle,
)
from polymarket_scanner.weather_only_live_paper import MODE
from polymarket_scanner.weather_only_live_paper_corrective import CANONICAL_CORRECTIVE_VERSION


SHA = "a" * 40


def _status() -> dict:
    return {
        "mode": MODE,
        "release_sha": SHA,
        "canonical_corrective_version": CANONICAL_CORRECTIVE_VERSION,
        "started_at": 1_950.0,
        "finished_at": 1_990.0,
        "cycle_ok": True,
        "errors": [],
        "paper_telegram_delivery": True,
        "financial_delivery": False,
        "financial_authority": False,
        "automatic_order_placement": False,
        "wallet_or_order_api_loaded": False,
        "same_day_delivery_enabled": False,
        "same_day_three_layer": {
            "enabled": True,
            "silent_research_only": True,
            "errors": [],
            "population_alignment_certified": False,
            "calibrated_probability": False,
            "included_in_validated_pnl": False,
            "telegram_delivery": False,
            "financial_authority": False,
        },
    }


def _accept(status=None, **kwargs):
    return accept_first_weather_paper_cycle(
        status or _status(),
        expected_release_sha=kwargs.pop("expected_release_sha", SHA),
        not_before=kwargs.pop("not_before", 1_900.0),
        now=kwargs.pop("now", 2_000.0),
        max_age_seconds=kwargs.pop("max_age_seconds", 600.0),
        **kwargs,
    )


def test_fresh_healthy_exact_release_paper_cycle_is_accepted():
    result = _accept()
    assert result.accepted is True
    assert result.release_sha == SHA
    assert result.same_day_research_enabled is True
    assert result.same_day_delivery_enabled is False
    assert result.paper_telegram_delivery is True
    assert result.financial_authority is False
    assert result.automatic_order_placement is False


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("release_sha", "b" * 40, "DEPLOY_STATUS_RELEASE_MISMATCH"),
        ("cycle_ok", False, "DEPLOY_FIRST_CYCLE_UNHEALTHY"),
        ("paper_telegram_delivery", False, "DEPLOY_PAPER_DELIVERY_MODE_INVALID"),
        ("financial_delivery", True, "DEPLOY_FINANCIAL_DELIVERY_NOT_FALSE"),
        ("financial_authority", True, "DEPLOY_FINANCIAL_AUTHORITY_NOT_FALSE"),
        ("automatic_order_placement", True, "DEPLOY_ORDER_PLACEMENT_NOT_FALSE"),
        ("wallet_or_order_api_loaded", True, "DEPLOY_WALLET_ORDER_API_NOT_FALSE"),
        ("same_day_delivery_enabled", True, "DEPLOY_SAME_DAY_DELIVERY_NOT_FALSE"),
    ],
)
def test_top_level_safety_or_identity_drift_fails(field, value, code):
    status = _status()
    status[field] = value
    with pytest.raises(WeatherDeploymentAcceptanceError, match=code):
        _accept(status)


def test_old_status_from_before_service_start_cannot_accept_new_deployment():
    with pytest.raises(WeatherDeploymentAcceptanceError, match="DEPLOY_STATUS_PREDATES_START"):
        _accept(not_before=1_995.0)


def test_stale_or_future_status_fails_closed():
    with pytest.raises(WeatherDeploymentAcceptanceError, match="DEPLOY_STATUS_STALE"):
        _accept(now=3_000.0, max_age_seconds=600.0)
    status = _status()
    status["finished_at"] = 2_006.0
    with pytest.raises(WeatherDeploymentAcceptanceError, match="DEPLOY_STATUS_FROM_FUTURE"):
        _accept(status, now=2_000.0)


def test_same_day_research_must_remain_silent_uncalibrated_and_out_of_pnl():
    cases = (
        ("enabled", False, "DEPLOY_SAME_DAY_RESEARCH_MODE_INVALID"),
        ("silent_research_only", False, "DEPLOY_SAME_DAY_RESEARCH_MODE_INVALID"),
        ("telegram_delivery", True, "DEPLOY_SAME_DAY_TELEGRAM_NOT_FALSE"),
        ("included_in_validated_pnl", True, "DEPLOY_SAME_DAY_PNL_NOT_FALSE"),
        ("calibrated_probability", True, "DEPLOY_SAME_DAY_CALIBRATION_NOT_FALSE"),
        ("population_alignment_certified", True, "DEPLOY_SAME_DAY_ALIGNMENT_NOT_FALSE"),
        ("financial_authority", True, "DEPLOY_SAME_DAY_FINANCIAL_AUTHORITY_NOT_FALSE"),
    )
    for field, value, code in cases:
        status = deepcopy(_status())
        status["same_day_three_layer"][field] = value
        with pytest.raises(WeatherDeploymentAcceptanceError, match=code):
            _accept(status)


def test_same_day_source_error_blocks_first_cycle_acceptance():
    status = deepcopy(_status())
    status["same_day_three_layer"]["errors"] = ["SOURCE_DOWN"]
    with pytest.raises(
        WeatherDeploymentAcceptanceError,
        match="DEPLOY_SAME_DAY_SOURCE_ERRORS_PRESENT",
    ):
        _accept(status)
