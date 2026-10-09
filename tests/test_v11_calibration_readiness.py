"""Readiness gate for a future non-vacuous calibration path.

Fixture counts below mirror the actual reviewed evidence recorded at
/home/alphaadmin/AlphaV11_BrainForwardUniverseS3/label-score-status.json as
of 2026-10-09 (21 scored city-day events spanning exactly two calendar
days, 2026-10-05 and 2026-10-06, log_loss=4.442..., dependence_unit always
CITY_DAY_NOT_PROVEN_INDEPENDENT). This test proves the gate fails closed on
that real evidence and explains exactly why, without ever touching
probability.py, model_artifacts.py or valuation.py.
"""
import pytest

from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.probability import score_vectors
from polymarket_scanner.v11.calibration_readiness import (
    CalibrationReadinessPolicy, evaluate_calibration_readiness,
)


REVIEW_POLICY = CalibrationReadinessPolicy(
    minimum_city_days=100, minimum_distinct_calendar_days=14, maximum_log_loss=1.0,
)


def _real_evidence_shape():
    # 10 stations x 2 days = 21 scored city-days (KLGA only has 2026-10-06).
    stations = ['KATL', 'KAUS', 'KBKF', 'KDAL', 'KHOU', 'KLAX', 'KMIA', 'KORD', 'KSEA', 'KSFO']
    city_days = [f'{s}:2026-10-05' for s in stations] + [f'{s}:2026-10-06' for s in stations] + ['KLGA:2026-10-06']
    event_ids = [f'event-{i}' for i in range(len(city_days))]
    probabilities = [[.5, .5] for _ in city_days]
    outcomes = [0 for _ in city_days]
    return score_vectors(probabilities, outcomes, event_ids=event_ids, city_days=city_days)


def test_real_recorded_evidence_is_rejected_as_not_independent_and_too_few_days():
    score = _real_evidence_shape()
    assert score['n_city_days'] == 21
    verdict = evaluate_calibration_readiness(
        score, policy=REVIEW_POLICY, calendar_days=frozenset({'2026-10-05', '2026-10-06'}),
    )
    assert verdict['ready_for_reviewed_calibration'] is False
    assert 'INDEPENDENCE_NOT_ATTESTED' in verdict['unmet_requirements']
    assert 'INSUFFICIENT_CITY_DAYS' in verdict['unmet_requirements']
    assert 'INSUFFICIENT_DISTINCT_CALENDAR_DAYS' in verdict['unmet_requirements']
    assert verdict['observed']['n_city_days'] == 21
    assert verdict['observed']['distinct_calendar_days'] == 2
    assert verdict['observed']['dependence_unit'] == 'CITY_DAY_NOT_PROVEN_INDEPENDENT'


def _recorded_score_object():
    # Verbatim "score" object from the actual reviewed evidence file:
    # /home/alphaadmin/AlphaV11_BrainForwardUniverseS3/label-score-status.json
    # as of 2026-10-09 (parent_bundle_sha256
    # fd9a32aa12ac7c8018ec524d1b74514bb57c42c0e60241672a4446b95908e641).
    return {
        'n_predictions': 21, 'n_events': 21, 'n_city_days': 21,
        'effective_independent_samples': None, 'dependence_unit': 'CITY_DAY_NOT_PROVEN_INDEPENDENT',
        'brier': 1.007638780549345, 'log_loss': 4.442111579577472, 'log_loss_infinite': False,
        'sharpness': 0.28164999441620775, 'reliability': [],
        'calibration_status': 'SCORED_NOT_CALIBRATED',
    }


def test_actual_reviewed_log_loss_also_fails_the_policy():
    # The real label-score run scored log_loss=4.442111579577472 (brier=1.0076...),
    # worse than a uniform coin flip (ln 2 ~= 0.693) -- there is no signal to
    # calibrate even before counting independence or volume.
    score = _recorded_score_object()
    verdict = evaluate_calibration_readiness(
        score, policy=REVIEW_POLICY, calendar_days=frozenset({'2026-10-05', '2026-10-06'}),
    )
    assert verdict['ready_for_reviewed_calibration'] is False
    assert 'LOG_LOSS_EXCEEDS_POLICY' in verdict['unmet_requirements']


def test_volume_alone_cannot_satisfy_the_gate_while_independence_is_unattested():
    # Even a hypothetically large, many-day synthetic sample cannot pass,
    # because score_vectors() always hardcodes non-independent dependence
    # units. This is a structural block, not a sample-size shortfall --
    # proving the gate does not quietly accept volume as a substitute for
    # an actual independence attestation that no code in this repository
    # currently produces.
    n = 500
    city_days = [f'city{i % 50}:day{i // 50}' for i in range(n)]
    event_ids = [f'event{i}' for i in range(n)]
    probabilities = [[.5, .5] for _ in range(n)]
    outcomes = [i % 2 for i in range(n)]
    score = score_vectors(probabilities, outcomes, event_ids=event_ids, city_days=city_days)
    calendar_days = frozenset(f'day{i}' for i in range(14))
    verdict = evaluate_calibration_readiness(score, policy=REVIEW_POLICY, calendar_days=calendar_days)
    assert verdict['ready_for_reviewed_calibration'] is False
    assert verdict['unmet_requirements'] == ['INDEPENDENCE_NOT_ATTESTED']
    assert score['dependence_unit'] == 'CITY_DAY_NOT_PROVEN_INDEPENDENT'
    assert score['effective_independent_samples'] is None


def test_gate_requires_well_shaped_score_vectors_output():
    with pytest.raises(EvidenceError):
        evaluate_calibration_readiness({'not': 'a score'}, policy=REVIEW_POLICY,
                                       calendar_days=frozenset({'2026-10-05'}))


def test_gate_rejects_calibrated_status_claims_it_cannot_verify():
    score = _real_evidence_shape()
    tampered = dict(score, calibration_status='CALIBRATED')
    with pytest.raises(EvidenceError):
        evaluate_calibration_readiness(tampered, policy=REVIEW_POLICY,
                                       calendar_days=frozenset({'2026-10-05', '2026-10-06'}))


def test_gate_requires_explicit_calendar_day_evidence():
    score = _real_evidence_shape()
    with pytest.raises(EvidenceError):
        evaluate_calibration_readiness(score, policy=REVIEW_POLICY, calendar_days=frozenset())


def test_policy_bounds_are_enforced():
    with pytest.raises(EvidenceError):
        CalibrationReadinessPolicy(minimum_city_days=1, minimum_distinct_calendar_days=14, maximum_log_loss=1.0)
    with pytest.raises(EvidenceError):
        CalibrationReadinessPolicy(minimum_city_days=100, minimum_distinct_calendar_days=1, maximum_log_loss=1.0)
    with pytest.raises(EvidenceError):
        CalibrationReadinessPolicy(minimum_city_days=100, minimum_distinct_calendar_days=14, maximum_log_loss=0.0)


def test_policy_sha256_is_stable_and_present_in_verdict():
    score = _real_evidence_shape()
    verdict = evaluate_calibration_readiness(
        score, policy=REVIEW_POLICY, calendar_days=frozenset({'2026-10-05', '2026-10-06'}),
    )
    assert verdict['policy_sha256'] == REVIEW_POLICY.sha256
