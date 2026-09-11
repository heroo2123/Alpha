from __future__ import annotations

"""Strict bridge from reconstructed WRH evidence to the preregistered calibration engine.

This module does not invent or select calibration bins, sample thresholds or model
promotion criteria. The caller must supply an immutable ``CalibrationPolicy``. That
keeps policy choice separate from the already-observed prospective dataset and avoids
post-outcome tuning.

Only records emitted by the strict source-recomputing reader are accepted. The exact
label adapter, source role, evidence version and authority/reconstructability flags are
carried through verbatim into ``ProbabilityCalibrationSample``. Stored authorization
JSON is never consumed as calibration authority and financial authority remains false.
"""

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .weather_only_calibration import (
    CalibrationAssessment,
    CalibrationPolicy,
    ProbabilityCalibrationSample,
    assess_probability_calibration,
)
from .weather_only_calibration_reader import (
    WEATHER_CALIBRATION_READER_VERSION,
    read_reconstructed_calibration_dataset,
)


WEATHER_CALIBRATION_DATASET_VERSION = "weather_calibration_dataset_v1_strict_reader_policy_external"


class WeatherCalibrationDatasetError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class ReconstructedCalibrationDataset:
    version: str
    reader_version: str
    authorized_row_count: int
    model_versions: tuple[str, ...]
    samples: tuple[ProbabilityCalibrationSample, ...]
    source_recomputed: bool = field(init=False, default=True)
    stored_authorized_json_used_as_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "reader_version": self.reader_version,
            "authorized_row_count": self.authorized_row_count,
            "model_versions": list(self.model_versions),
            "sample_count": len(self.samples),
            "samples": [asdict(sample) for sample in self.samples],
            "source_recomputed": self.source_recomputed,
            "stored_authorized_json_used_as_authority": self.stored_authorized_json_used_as_authority,
            "financial_authority": self.financial_authority,
        }


def _require_reader_boundary(report: dict) -> None:
    if not isinstance(report, dict):
        raise WeatherCalibrationDatasetError("DATASET_READER_REPORT_INVALID")
    if report.get("version") != WEATHER_CALIBRATION_READER_VERSION:
        raise WeatherCalibrationDatasetError("DATASET_READER_VERSION_MISMATCH")
    if report.get("read_only_database") is not True:
        raise WeatherCalibrationDatasetError("DATASET_READER_NOT_READ_ONLY")
    if report.get("source_recomputed") is not True:
        raise WeatherCalibrationDatasetError("DATASET_SOURCE_NOT_RECOMPUTED")
    if report.get("stored_authorized_json_used_as_authority") is not False:
        raise WeatherCalibrationDatasetError("DATASET_STORED_AUTHORITY_SHORTCUT")
    if report.get("financial_authority") is not False:
        raise WeatherCalibrationDatasetError("DATASET_FINANCIAL_AUTHORITY_BOUNDARY_BROKEN")


def _sample_from_record(record: object) -> ProbabilityCalibrationSample:
    if not isinstance(record, dict):
        raise WeatherCalibrationDatasetError("DATASET_RECORD_INVALID")
    if record.get("source_recomputed") is not True:
        raise WeatherCalibrationDatasetError("DATASET_RECORD_SOURCE_NOT_RECOMPUTED")
    if record.get("stored_authorized_json_used_as_authority") is not False:
        raise WeatherCalibrationDatasetError("DATASET_RECORD_STORED_AUTHORITY_SHORTCUT")
    if record.get("calibration_label_authority") is not True:
        raise WeatherCalibrationDatasetError("DATASET_RECORD_CALIBRATION_AUTHORITY_MISSING")
    if record.get("financial_authority") is not False:
        raise WeatherCalibrationDatasetError("DATASET_RECORD_FINANCIAL_AUTHORITY_BOUNDARY_BROKEN")

    required_text = (
        "event_id",
        "station",
        "model_version",
        "label_adapter",
        "source_role",
        "evidence_version",
    )
    for key in required_text:
        value = record.get(key)
        if not isinstance(value, str) or not value.strip():
            raise WeatherCalibrationDatasetError(f"DATASET_RECORD_FIELD_MISSING:{key}")

    label_authority = record.get("label_authority")
    reconstructable = record.get("settlement_state_reconstructable")
    if type(label_authority) is not bool or type(reconstructable) is not bool:
        raise WeatherCalibrationDatasetError("DATASET_RECORD_LABEL_FLAGS_INVALID")

    return ProbabilityCalibrationSample(
        event_id=record["event_id"],
        station=record["station"],
        model_version=record["model_version"],
        predicted_probability=record.get("predicted_probability"),
        final_payout=record.get("final_payout"),
        label_adapter=record["label_adapter"],
        source_role=record["source_role"],
        evidence_version=record["evidence_version"],
        label_authority=label_authority,
        settlement_state_reconstructable=reconstructable,
    )


def dataset_from_reader_report(report: dict) -> ReconstructedCalibrationDataset:
    """Convert only a strict reader report; useful for tests and offline audit tooling."""
    _require_reader_boundary(report)
    records = report.get("records")
    if not isinstance(records, list):
        raise WeatherCalibrationDatasetError("DATASET_RECORDS_INVALID")
    samples = tuple(_sample_from_record(record) for record in records)
    authorized_count = report.get("authorized_row_count")
    reconstructed_count = report.get("reconstructed_record_count")
    if isinstance(authorized_count, bool) or not isinstance(authorized_count, int) or authorized_count < 0:
        raise WeatherCalibrationDatasetError("DATASET_AUTHORIZED_COUNT_INVALID")
    if reconstructed_count != authorized_count or len(samples) != authorized_count:
        raise WeatherCalibrationDatasetError("DATASET_RECONSTRUCTED_COUNT_MISMATCH")
    event_ids = [sample.event_id.strip() for sample in samples]
    if len(set(event_ids)) != len(event_ids):
        raise WeatherCalibrationDatasetError("DATASET_DUPLICATE_EVENT")
    model_versions = tuple(sorted({sample.model_version for sample in samples}))
    return ReconstructedCalibrationDataset(
        version=WEATHER_CALIBRATION_DATASET_VERSION,
        reader_version=WEATHER_CALIBRATION_READER_VERSION,
        authorized_row_count=authorized_count,
        model_versions=model_versions,
        samples=samples,
    )


def read_calibration_dataset(path: str | Path) -> ReconstructedCalibrationDataset:
    return dataset_from_reader_report(read_reconstructed_calibration_dataset(path))


def assess_reconstructed_calibration(
    path: str | Path,
    *,
    model_version: str,
    target_probability: float,
    policy: CalibrationPolicy,
) -> tuple[ReconstructedCalibrationDataset, CalibrationAssessment]:
    if not isinstance(policy, CalibrationPolicy):
        raise WeatherCalibrationDatasetError("DATASET_CALIBRATION_POLICY_REQUIRED")
    dataset = read_calibration_dataset(path)
    assessment = assess_probability_calibration(
        dataset.samples,
        model_version=model_version,
        target_probability=target_probability,
        policy=policy,
    )
    return dataset, assessment


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    dataset = read_calibration_dataset(args.db)
    payload = json.dumps(dataset.as_dict(), indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(payload + "\n")
    print(payload)


if __name__ == "__main__":
    main()
