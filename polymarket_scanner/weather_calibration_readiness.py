from __future__ import annotations

"""Read-only readiness report for the frozen prospective weather experiment.

The report consumes only the strict reconstructed dataset, separates every immutable
model version, and evaluates all preregistered probability bins under the frozen
outcome-blind policy. It is diagnostic research evidence only: even a bin that passes
all statistical gates does not promote a detector, enable Telegram financial delivery,
or grant calibrated-probability/financial authority.
"""

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from .weather_calibration_experiment import (
    WeatherCalibrationExperimentError,
    build_weather_calibration_experiment_manifest,
    validate_weather_calibration_experiment_manifest,
)
from .weather_calibration_policy import (
    FrozenWeatherCalibrationPolicy,
    frozen_weather_calibration_policy,
    require_frozen_weather_calibration_policy,
)
from .weather_only_calibration import CalibrationAssessment, assess_probability_calibration
from .weather_only_calibration_dataset import (
    ReconstructedCalibrationDataset,
    read_calibration_dataset,
)


WEATHER_CALIBRATION_READINESS_VERSION = "weather_calibration_readiness_v2_strict_dataset_frozen_policy"


class WeatherCalibrationReadinessError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class ModelCalibrationReadiness:
    model_version: str
    clean_total_resolved: int
    distinct_stations: int
    overall_brier_score: float | None
    ready_bin_indices: tuple[int, ...]
    assessments: tuple[CalibrationAssessment, ...]
    research_calibration_ready_any_bin: bool
    calibrated_probability_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return {
            "model_version": self.model_version,
            "clean_total_resolved": self.clean_total_resolved,
            "distinct_stations": self.distinct_stations,
            "overall_brier_score": self.overall_brier_score,
            "ready_bin_indices": list(self.ready_bin_indices),
            "research_calibration_ready_any_bin": self.research_calibration_ready_any_bin,
            "assessments": [assessment.as_dict() for assessment in self.assessments],
            "calibrated_probability_authority": self.calibrated_probability_authority,
            "financial_authority": self.financial_authority,
        }


def _representative_probability(lo: float, hi: float) -> float:
    return float(lo) + (float(hi) - float(lo)) / 2.0


def assess_dataset_readiness(
    dataset: ReconstructedCalibrationDataset,
    *,
    frozen_policy: FrozenWeatherCalibrationPolicy | None = None,
) -> dict:
    if not isinstance(dataset, ReconstructedCalibrationDataset):
        raise WeatherCalibrationReadinessError("READINESS_DATASET_TYPE_INVALID")
    policy_record = frozen_policy or frozen_weather_calibration_policy()
    try:
        policy_record = require_frozen_weather_calibration_policy(policy_record)
        manifest = validate_weather_calibration_experiment_manifest(
            build_weather_calibration_experiment_manifest()
        )
    except (ValueError, WeatherCalibrationExperimentError) as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise WeatherCalibrationReadinessError(f"READINESS_EXPERIMENT_OR_POLICY:{code}") from exc

    if dataset.financial_authority is not False:
        raise WeatherCalibrationReadinessError("READINESS_DATASET_FINANCIAL_AUTHORITY_BROKEN")
    if dataset.source_recomputed is not True or dataset.horizon_recomputed is not True:
        raise WeatherCalibrationReadinessError("READINESS_DATASET_LINEAGE_NOT_RECOMPUTED")
    if dataset.stored_authorized_json_used_as_authority is not False:
        raise WeatherCalibrationReadinessError("READINESS_DATASET_STORED_AUTHORITY_SHORTCUT")
    if dataset.capture_policy_ids and dataset.capture_policy_ids != (manifest.capture_policy_id,):
        raise WeatherCalibrationReadinessError("READINESS_CAPTURE_POLICY_MISMATCH")

    policy = policy_record.policy
    models: list[ModelCalibrationReadiness] = []
    for model_version in dataset.model_versions:
        assessments = tuple(
            assess_probability_calibration(
                dataset.samples,
                model_version=model_version,
                target_probability=_representative_probability(lo, hi),
                policy=policy,
            )
            for lo, hi in policy.probability_bins
        )
        ready = tuple(
            assessment.target_bin_index
            for assessment in assessments
            if assessment.research_calibration_ready and assessment.target_bin_index is not None
        )
        first = assessments[0]
        models.append(ModelCalibrationReadiness(
            model_version=model_version,
            clean_total_resolved=first.clean_total_resolved,
            distinct_stations=first.distinct_stations,
            overall_brier_score=first.overall_brier_score,
            ready_bin_indices=ready,
            assessments=assessments,
            research_calibration_ready_any_bin=bool(ready),
        ))

    return {
        "version": WEATHER_CALIBRATION_READINESS_VERSION,
        "experiment_manifest_sha256": manifest.manifest_sha256,
        "capture_policy_id": manifest.capture_policy_id,
        "statistical_policy_id": manifest.statistical_policy_id,
        "statistical_policy_sha256": manifest.statistical_policy_sha256,
        "dataset_authorized_row_count": dataset.authorized_row_count,
        "model_versions": list(dataset.model_versions),
        "models": [model.as_dict() for model in models],
        "research_calibration_ready_any_model_bin": any(
            model.research_calibration_ready_any_bin for model in models
        ),
        "promotion_authority": False,
        "calibrated_probability_authority": False,
        "financial_authority": False,
        "financial_delivery": False,
        "automatic_order_placement": False,
    }


def read_calibration_readiness(path: str | Path) -> dict:
    return assess_dataset_readiness(read_calibration_dataset(path))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = read_calibration_readiness(args.db)
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
