from __future__ import annotations

"""Fail-closed acceptance checks for the first canonical weather PAPER cycle."""

import math
import time
from dataclasses import dataclass

from .weather_only_live_paper import MODE
from .weather_only_live_paper_corrective import CANONICAL_CORRECTIVE_VERSION
from .weather_only_live_paper_final import (
    FINAL_MARKET_STATE_POLICY,
    FINAL_PAPER_RUNTIME_VERSION,
)


DEPLOYMENT_ACCEPTANCE_VERSION = "weather_paper_first_cycle_acceptance_v3_final_runtime_guard_proven"
EXPECTED_FORECAST_POLICY = "STRICT_FUTURE_LOCAL_DAY_RAW_GEFS_V4"
EXPECTED_STRUCTURAL_POLICY = "DISABLED_PENDING_COMMON_RESOLUTION_PROOF"


class WeatherDeploymentAcceptanceError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherDeploymentAcceptanceError(code)
    number = float(value)
    if not math.isfinite(number):
        raise WeatherDeploymentAcceptanceError(code)
    return number


def _sha(value: object, code: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 40 or any(ch not in "0123456789abcdef" for ch in text):
        raise WeatherDeploymentAcceptanceError(code)
    return text


def _require_false(payload: dict, key: str, code: str) -> None:
    if payload.get(key) is not False:
        raise WeatherDeploymentAcceptanceError(code)


@dataclass(frozen=True, slots=True)
class WeatherPaperFirstCycleAcceptance:
    version: str
    release_sha: str
    cycle_finished_at: float
    cycle_age_seconds: float
    canonical_corrective_version: str
    same_day_research_enabled: bool
    same_day_delivery_enabled: bool
    structural_delivery_enabled: bool
    paper_telegram_delivery: bool
    accepted: bool
    financial_authority: bool = False
    automatic_order_placement: bool = False

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "release_sha": self.release_sha,
            "cycle_finished_at": self.cycle_finished_at,
            "cycle_age_seconds": self.cycle_age_seconds,
            "canonical_corrective_version": self.canonical_corrective_version,
            "same_day_research_enabled": self.same_day_research_enabled,
            "same_day_delivery_enabled": self.same_day_delivery_enabled,
            "structural_delivery_enabled": self.structural_delivery_enabled,
            "paper_telegram_delivery": self.paper_telegram_delivery,
            "accepted": self.accepted,
            "financial_authority": self.financial_authority,
            "automatic_order_placement": self.automatic_order_placement,
        }


def accept_first_weather_paper_cycle(
    status: object,
    *,
    expected_release_sha: str,
    not_before: float,
    now: float | None = None,
    max_age_seconds: float = 600.0,
) -> WeatherPaperFirstCycleAcceptance:
    if not isinstance(status, dict):
        raise WeatherDeploymentAcceptanceError("DEPLOY_STATUS_TYPE_INVALID")
    release = _sha(expected_release_sha, "DEPLOY_EXPECTED_RELEASE_INVALID")
    if _sha(status.get("release_sha"), "DEPLOY_STATUS_RELEASE_INVALID") != release:
        raise WeatherDeploymentAcceptanceError("DEPLOY_STATUS_RELEASE_MISMATCH")
    if status.get("mode") != MODE:
        raise WeatherDeploymentAcceptanceError("DEPLOY_STATUS_MODE_MISMATCH")
    if status.get("canonical_corrective_version") != CANONICAL_CORRECTIVE_VERSION:
        raise WeatherDeploymentAcceptanceError("DEPLOY_STATUS_CANONICAL_VERSION_MISMATCH")
    # The deployment gate must prove the final guarded wrapper actually completed the
    # cycle, not merely an inherited corrective/v4 runtime that happens to share many
    # status fields.
    if status.get("final_paper_runtime_version") != FINAL_PAPER_RUNTIME_VERSION:
        raise WeatherDeploymentAcceptanceError("DEPLOY_FINAL_RUNTIME_VERSION_MISMATCH")
    if status.get("current_market_state_policy") != FINAL_MARKET_STATE_POLICY:
        raise WeatherDeploymentAcceptanceError("DEPLOY_FINAL_MARKET_STATE_POLICY_MISMATCH")
    if status.get("exclusive_writer_lease") is not True:
        raise WeatherDeploymentAcceptanceError("DEPLOY_EXCLUSIVE_WRITER_LEASE_NOT_PROVEN")
    if status.get("forecast_policy") != EXPECTED_FORECAST_POLICY:
        raise WeatherDeploymentAcceptanceError("DEPLOY_FORECAST_POLICY_MISMATCH")
    if status.get("structural_policy") != EXPECTED_STRUCTURAL_POLICY:
        raise WeatherDeploymentAcceptanceError("DEPLOY_STRUCTURAL_CONTAINMENT_MISSING")
    _require_false(
        status,
        "structural_delivery_enabled",
        "DEPLOY_STRUCTURAL_DELIVERY_NOT_FALSE",
    )
    if status.get("cycle_ok") is not True:
        raise WeatherDeploymentAcceptanceError("DEPLOY_FIRST_CYCLE_UNHEALTHY")
    if list(status.get("errors") or []):
        raise WeatherDeploymentAcceptanceError("DEPLOY_FIRST_CYCLE_ERRORS_PRESENT")

    finished = _finite(status.get("finished_at"), "DEPLOY_FINISHED_AT_INVALID")
    boundary = _finite(not_before, "DEPLOY_NOT_BEFORE_INVALID")
    current = time.time() if now is None else _finite(now, "DEPLOY_NOW_INVALID")
    max_age = _finite(max_age_seconds, "DEPLOY_MAX_AGE_INVALID")
    if max_age <= 0.0:
        raise WeatherDeploymentAcceptanceError("DEPLOY_MAX_AGE_INVALID")
    if finished < boundary:
        raise WeatherDeploymentAcceptanceError("DEPLOY_STATUS_PREDATES_START")
    if finished > current + 5.0:
        raise WeatherDeploymentAcceptanceError("DEPLOY_STATUS_FROM_FUTURE")
    age = max(0.0, current - finished)
    if age > max_age:
        raise WeatherDeploymentAcceptanceError("DEPLOY_STATUS_STALE")

    if status.get("paper_telegram_delivery") is not True:
        raise WeatherDeploymentAcceptanceError("DEPLOY_PAPER_DELIVERY_MODE_INVALID")
    _require_false(status, "financial_delivery", "DEPLOY_FINANCIAL_DELIVERY_NOT_FALSE")
    _require_false(status, "financial_authority", "DEPLOY_FINANCIAL_AUTHORITY_NOT_FALSE")
    _require_false(status, "automatic_order_placement", "DEPLOY_ORDER_PLACEMENT_NOT_FALSE")
    _require_false(status, "wallet_or_order_api_loaded", "DEPLOY_WALLET_ORDER_API_NOT_FALSE")
    _require_false(status, "same_day_delivery_enabled", "DEPLOY_SAME_DAY_DELIVERY_NOT_FALSE")

    same_day = status.get("same_day_three_layer")
    if not isinstance(same_day, dict):
        raise WeatherDeploymentAcceptanceError("DEPLOY_SAME_DAY_STATUS_MISSING")
    if same_day.get("enabled") is not True or same_day.get("silent_research_only") is not True:
        raise WeatherDeploymentAcceptanceError("DEPLOY_SAME_DAY_RESEARCH_MODE_INVALID")
    if list(same_day.get("errors") or []):
        raise WeatherDeploymentAcceptanceError("DEPLOY_SAME_DAY_SOURCE_ERRORS_PRESENT")
    _require_false(same_day, "telegram_delivery", "DEPLOY_SAME_DAY_TELEGRAM_NOT_FALSE")
    _require_false(same_day, "included_in_validated_pnl", "DEPLOY_SAME_DAY_PNL_NOT_FALSE")
    _require_false(same_day, "calibrated_probability", "DEPLOY_SAME_DAY_CALIBRATION_NOT_FALSE")
    _require_false(same_day, "population_alignment_certified", "DEPLOY_SAME_DAY_ALIGNMENT_NOT_FALSE")
    _require_false(same_day, "financial_authority", "DEPLOY_SAME_DAY_FINANCIAL_AUTHORITY_NOT_FALSE")

    return WeatherPaperFirstCycleAcceptance(
        version=DEPLOYMENT_ACCEPTANCE_VERSION,
        release_sha=release,
        cycle_finished_at=finished,
        cycle_age_seconds=age,
        canonical_corrective_version=CANONICAL_CORRECTIVE_VERSION,
        same_day_research_enabled=True,
        same_day_delivery_enabled=False,
        structural_delivery_enabled=False,
        paper_telegram_delivery=True,
        accepted=True,
    )
