from __future__ import annotations

from polymarket_scanner.weather_calibration_policy import (
    MAX_BRIER_SCORE,
    MIN_BIN_RESOLVED,
    MIN_DISTINCT_STATIONS,
    MIN_TOTAL_RESOLVED,
    POLICY_BASIS,
    PROBABILITY_BINS,
    WILSON_Z,
    frozen_weather_calibration_policy,
    require_frozen_weather_calibration_policy,
)
from polymarket_scanner.weather_only_calibration import (
    ProbabilityCalibrationSample,
    assess_probability_calibration,
)
from polymarket_scanner.weather_only_predictions import (
    EXACT_SETTLEMENT_SOURCE_ROLE,
    NWS_WRH_EXACT_LABEL_ADAPTER,
)
from polymarket_scanner.weather_only_calibration import SETTLEMENT_LABEL_EVIDENCE_VERSION


def _sample(i: int, *, probability: float = 0.99, payout: float = 1.0) -> ProbabilityCalibrationSample:
    return ProbabilityCalibrationSample(
        event_id=f"event-{i}",
        station=f"K{i % 8:03d}"[-4:],
        model_version="frozen-policy-model-v1",
        predicted_probability=probability,
        final_payout=payout,
        label_adapter=NWS_WRH_EXACT_LABEL_ADAPTER,
        source_role=EXACT_SETTLEMENT_SOURCE_ROLE,
        evidence_version=SETTLEMENT_LABEL_EVIDENCE_VERSION,
        label_authority=True,
        settlement_state_reconstructable=True,
    )


def test_frozen_policy_is_outcome_blind_deciles_with_conservative_fixed_gates():
    frozen = frozen_weather_calibration_policy()
    policy = frozen.policy
    assert POLICY_BASIS == "OUTCOME_BLIND_EQUAL_WIDTH_DECILES"
    assert policy.probability_bins == PROBABILITY_BINS
    assert PROBABILITY_BINS == tuple((i / 10.0, (i + 1) / 10.0) for i in range(10))
    assert policy.probability_bins[0][0] == 0.0
    assert policy.probability_bins[-1][1] == 1.0
    for previous, current in zip(policy.probability_bins, policy.probability_bins[1:]):
        assert previous[1] == current[0]
    assert policy.min_total_resolved == MIN_TOTAL_RESOLVED == 100
    assert policy.min_bin_resolved == MIN_BIN_RESOLVED == 30
    assert policy.min_distinct_stations == MIN_DISTINCT_STATIONS == 8
    assert policy.max_brier_score == MAX_BRIER_SCORE == 0.08
    assert policy.wilson_z == WILSON_Z == 1.96
    assert frozen.frozen_before_production_collection is True
    assert frozen.retrospective_relaxation_permitted is False
    assert frozen.research_calibration_authority_only is True
    assert frozen.financial_authority is False
    assert len(frozen.policy_sha256) == 64
    assert require_frozen_weather_calibration_policy(frozen) == frozen


def test_frozen_policy_digest_is_deterministic():
    first = frozen_weather_calibration_policy()
    second = frozen_weather_calibration_policy()
    assert first == second
    assert first.policy_sha256 == second.policy_sha256


def test_frozen_policy_stays_not_ready_below_total_sample_gate():
    frozen = frozen_weather_calibration_policy()
    samples = [_sample(i) for i in range(MIN_TOTAL_RESOLVED - 1)]
    assessment = assess_probability_calibration(
        samples,
        model_version="frozen-policy-model-v1",
        target_probability=0.99,
        policy=frozen.policy,
    )
    assert assessment.clean_total_resolved == MIN_TOTAL_RESOLVED - 1
    assert assessment.research_calibration_ready is False
    assert assessment.calibrated_probability_lower_bound is None
    assert assessment.financial_authority is False


def test_frozen_policy_can_mature_only_when_all_fixed_gates_pass():
    frozen = frozen_weather_calibration_policy()
    samples = [_sample(i) for i in range(MIN_TOTAL_RESOLVED)]
    assessment = assess_probability_calibration(
        samples,
        model_version="frozen-policy-model-v1",
        target_probability=0.99,
        policy=frozen.policy,
    )
    assert assessment.clean_total_resolved == MIN_TOTAL_RESOLVED
    assert assessment.clean_bin_resolved == MIN_TOTAL_RESOLVED
    assert assessment.distinct_stations == MIN_DISTINCT_STATIONS
    assert assessment.overall_brier is not None and assessment.overall_brier < MAX_BRIER_SCORE
    assert assessment.research_calibration_ready is True
    assert assessment.calibrated_probability_lower_bound is not None
    assert assessment.calibrated_probability_lower_bound < 1.0
    assert assessment.financial_authority is False


def test_frozen_policy_brier_gate_blocks_large_systematic_error_even_with_enough_samples():
    frozen = frozen_weather_calibration_policy()
    samples = [_sample(i, probability=0.99, payout=0.0) for i in range(MIN_TOTAL_RESOLVED)]
    assessment = assess_probability_calibration(
        samples,
        model_version="frozen-policy-model-v1",
        target_probability=0.99,
        policy=frozen.policy,
    )
    assert assessment.clean_total_resolved == MIN_TOTAL_RESOLVED
    assert assessment.clean_bin_resolved == MIN_TOTAL_RESOLVED
    assert assessment.overall_brier is not None and assessment.overall_brier > MAX_BRIER_SCORE
    assert assessment.research_calibration_ready is False
    assert assessment.calibrated_probability_lower_bound is None
    assert assessment.financial_authority is False
