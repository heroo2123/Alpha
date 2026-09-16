from __future__ import annotations

"""Frozen statistical policy for the prospective GEFS top-bucket experiment.

The policy is intentionally simple and outcome-blind: ten equal-width probability
bins over [0,1], rather than quantiles fitted to observed forecast scores or outcome
results. Sample-size/station/Brier/Wilson gates carry forward the conservative weather
calibration thresholds already used by the project.

This file is the preregistration record. After prospective outcomes accumulate, these
bins and thresholds must not be loosened, shifted or merged to make the current model
pass. A failed assessment means the model remains research-only and requires a new,
separately versioned future experiment for any revised policy.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field

from .weather_only_calibration import CalibrationPolicy


WEATHER_GEFS_CALIBRATION_POLICY_VERSION = "weather_gefs_calibration_policy_v1_outcome_blind_deciles"
WEATHER_GEFS_CALIBRATION_POLICY_ID = "weather_gefs_top_bucket_deciles_n100_bin30_station8_brier008_wilson95_v1"
POLICY_PREREGISTRATION_DATE = "2026-09-11"
POLICY_BASIS = "OUTCOME_BLIND_EQUAL_WIDTH_DECILES"
PROBABILITY_BINS = tuple((i / 10.0, (i + 1) / 10.0) for i in range(10))
MIN_TOTAL_RESOLVED = 100
MIN_BIN_RESOLVED = 30
MIN_DISTINCT_STATIONS = 8
MAX_BRIER_SCORE = 0.08
WILSON_Z = 1.96


@dataclass(frozen=True, slots=True)
class FrozenWeatherCalibrationPolicy:
    version: str
    preregistration_date: str
    basis: str
    policy: CalibrationPolicy
    policy_sha256: str
    frozen_before_production_collection: bool = field(init=False, default=True)
    retrospective_relaxation_permitted: bool = field(init=False, default=False)
    research_calibration_authority_only: bool = field(init=False, default=True)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        value = asdict(self)
        value["policy"]["probability_bins"] = [list(pair) for pair in self.policy.probability_bins]
        return value


def _hash_payload(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _digest_payload(value: FrozenWeatherCalibrationPolicy) -> dict:
    payload = value.as_dict()
    payload.pop("policy_sha256", None)
    return payload


def frozen_weather_calibration_policy() -> FrozenWeatherCalibrationPolicy:
    policy = CalibrationPolicy(
        policy_id=WEATHER_GEFS_CALIBRATION_POLICY_ID,
        probability_bins=PROBABILITY_BINS,
        min_total_resolved=MIN_TOTAL_RESOLVED,
        min_bin_resolved=MIN_BIN_RESOLVED,
        min_distinct_stations=MIN_DISTINCT_STATIONS,
        max_brier_score=MAX_BRIER_SCORE,
        wilson_z=WILSON_Z,
    )
    shell = FrozenWeatherCalibrationPolicy(
        version=WEATHER_GEFS_CALIBRATION_POLICY_VERSION,
        preregistration_date=POLICY_PREREGISTRATION_DATE,
        basis=POLICY_BASIS,
        policy=policy,
        policy_sha256="0" * 64,
    )
    return FrozenWeatherCalibrationPolicy(
        version=shell.version,
        preregistration_date=shell.preregistration_date,
        basis=shell.basis,
        policy=shell.policy,
        policy_sha256=_hash_payload(_digest_payload(shell)),
    )


def require_frozen_weather_calibration_policy(value: object) -> FrozenWeatherCalibrationPolicy:
    expected = frozen_weather_calibration_policy()
    if not isinstance(value, FrozenWeatherCalibrationPolicy) or value != expected:
        raise ValueError("weather GEFS calibration policy does not match frozen preregistration")
    if (
        value.frozen_before_production_collection is not True
        or value.retrospective_relaxation_permitted is not False
        or value.research_calibration_authority_only is not True
        or value.financial_authority is not False
    ):
        raise ValueError("weather GEFS calibration authority boundary broken")
    return value
