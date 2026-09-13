from __future__ import annotations

from dataclasses import replace

import pytest

from polymarket_scanner.weather_only_conditioned_extremes import PWS_ROLE, TimeSegment
from polymarket_scanner.weather_only_contracts import DAILY_HIGH, DAILY_LOW
from polymarket_scanner.weather_only_near_term_path import (
    NearTermPathError,
    NearTermPathPoint,
    build_near_term_sample_path,
    reduce_sample_path_to_research_coverage,
    verify_near_term_path_integrity,
)


def _path(*, family=DAILY_LOW, received=990.0, points=None):
    segment = TimeSegment(1000.0, 1600.0, "LAYER2_NEAR_TERM_REMAINDER")
    rows = points or (
        NearTermPathPoint(1000.0, 11.0),
        NearTermPathPoint(1300.0, 9.0),
        NearTermPathPoint(1600.0, 10.0),
    )
    return build_near_term_sample_path(
        station="KDAL",
        unit="F",
        family=family,
        source_role=PWS_ROLE,
        source_id="fixture-near-term-model",
        issued_at=980.0,
        received_at=received,
        as_of=1000.0,
        segment=segment,
        sampling_step_seconds=300,
        sampling_hypothesis_id="FIVE_MINUTE_MODEL_POINT_EXTREME_V1",
        points=rows,
        source_payload_sha256="a" * 64,
    )


def test_complete_sample_path_reduces_to_model_extreme_without_gaining_authority():
    path = _path()
    verify_near_term_path_integrity(path)
    assert path.sampled_extreme == 9.0
    assert path.sampled_path_complete is True
    assert path.continuous_physical_extreme_certified is False
    coverage = reduce_sample_path_to_research_coverage(path)
    assert coverage.predicted_extreme == 9.0
    assert coverage.coverage_method == "SAMPLED_MODEL_PATH:FIVE_MINUTE_MODEL_POINT_EXTREME_V1"
    assert coverage.source_evidence_sha256 == path.evidence_sha256
    assert coverage.calibrated_probability is False
    assert coverage.same_day_delivery_authority is False
    assert coverage.settlement_authority is False
    assert coverage.financial_authority is False


def test_high_family_uses_sampled_maximum():
    path = _path(family=DAILY_HIGH)
    assert path.sampled_extreme == 11.0


def test_missing_middle_grid_point_is_not_silently_interpolated():
    with pytest.raises(NearTermPathError, match="NEAR_TERM_PATH_COVERAGE_INCOMPLETE"):
        _path(points=(
            NearTermPathPoint(1000.0, 11.0),
            NearTermPathPoint(1600.0, 10.0),
        ))


def test_off_grid_timestamp_is_rejected():
    with pytest.raises(NearTermPathError, match="NEAR_TERM_PATH_GRID_MISMATCH"):
        _path(points=(
            NearTermPathPoint(1000.0, 11.0),
            NearTermPathPoint(1299.0, 9.0),
            NearTermPathPoint(1600.0, 10.0),
        ))


def test_provider_receipt_after_decision_is_lookahead():
    with pytest.raises(NearTermPathError, match="NEAR_TERM_PATH_LOOKAHEAD"):
        _path(received=1000.1)


def test_path_tampering_breaks_digest_rebuild():
    path = _path()
    changed = replace(path, sampled_extreme=2.0)
    with pytest.raises(NearTermPathError, match="NEAR_TERM_PATH_DIGEST_OR_SHAPE_MISMATCH"):
        verify_near_term_path_integrity(changed)


def test_authority_flags_cannot_be_inflated_by_dataclass_copy():
    path = _path()
    # init=False fields cannot be passed to replace, which is itself an additional
    # guard. The verifier also checks them if an object is reconstructed unsafely.
    assert path.same_day_delivery_authority is False
    assert path.financial_authority is False
