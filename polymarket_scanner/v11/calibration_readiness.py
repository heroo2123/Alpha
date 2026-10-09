"""Evidence-bound readiness check for a future, reviewed calibration path.

This module answers one narrow question: does a presented scoring result
(the shape produced by ``probability.score_vectors``) meet an explicit,
documented minimum bar for a human to even consider reviewing a non-vacuous
calibration? It never computes, returns, or approves a calibrated
probability, and it is not imported by ``probability.py``, ``valuation.py``
or ``model_artifacts.py``. Those three remain the actual fail-closed gates:
``probability.predict_buckets`` always emits ``UNCALIBRATED``/vacuous
``[0,1]`` bounds, ``model_artifacts.validate_artifact`` rejects any
``CALIBRATION`` artifact other than ``VACUOUS_BOUNDS``/``UNCALIBRATED``, and
``valuation._prediction`` requires the vacuous contract to hold. This module
exists only to make the *reason* a given body of evidence falls short
explicit and testable, so a reviewer can see precisely what is missing
instead of re-deriving it from the scorer's hardcoded fields each time.

Structural fact driving this gate: ``probability.score_vectors`` always
sets ``dependence_unit='CITY_DAY_NOT_PROVEN_INDEPENDENT'`` and
``effective_independent_samples=None`` on every call. No code path in this
repository currently produces an attested-independent sample count. Until a
reviewed attestation mechanism exists, this gate can never return
``ready=True`` for evidence produced by the current scorer, regardless of
sample size.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .evidence import EvidenceError, finite, identity


REQUIRED_SCORE_KEYS = {
    'n_predictions', 'n_events', 'n_city_days', 'effective_independent_samples',
    'dependence_unit', 'brier', 'log_loss', 'log_loss_infinite', 'sharpness',
    'reliability', 'calibration_status',
}


@dataclass(frozen=True)
class CalibrationReadinessPolicy:
    """Minimum bar for even reviewing a non-vacuous calibration. All fields
    must be explicitly set; there is no default that lets evidence pass
    silently."""
    minimum_city_days: int
    minimum_distinct_calendar_days: int
    maximum_log_loss: float
    require_attested_independence: bool = True

    def __post_init__(self):
        if type(self.minimum_city_days) is not int or self.minimum_city_days < 2:
            raise EvidenceError('CALIBRATION_POLICY_CITY_DAY_BOUND')
        if type(self.minimum_distinct_calendar_days) is not int or self.minimum_distinct_calendar_days < 2:
            raise EvidenceError('CALIBRATION_POLICY_CALENDAR_DAY_BOUND')
        if not 0 < finite(self.maximum_log_loss) <= 50:
            raise EvidenceError('CALIBRATION_POLICY_LOG_LOSS_BOUND')
        if type(self.require_attested_independence) is not bool:
            raise EvidenceError('CALIBRATION_POLICY_INDEPENDENCE_FLAG_REQUIRED')

    @property
    def sha256(self):
        from .evidence import digest
        return digest(asdict(self))


def evaluate_calibration_readiness(score: dict, *, policy: CalibrationReadinessPolicy,
                                   calendar_days: frozenset[str]) -> dict:
    """Score a `probability.score_vectors` result against `policy`.

    Returns a structured verdict naming every unmet requirement. Raises
    EvidenceError on malformed input; it never guesses a shape. This
    function cannot be used to construct a probability, bucket, bundle, or
    artifact -- it returns a plain report dict only.
    """
    if not isinstance(score, dict) or not REQUIRED_SCORE_KEYS <= set(score):
        raise EvidenceError('CALIBRATION_READINESS_SCORE_SHAPE_REQUIRED')
    if not isinstance(policy, CalibrationReadinessPolicy):
        raise EvidenceError('CALIBRATION_READINESS_POLICY_REQUIRED')
    if (not isinstance(calendar_days, frozenset) or not calendar_days
            or any(type(d) is not str for d in calendar_days)):
        raise EvidenceError('CALIBRATION_READINESS_CALENDAR_DAYS_REQUIRED')
    for d in calendar_days:
        identity(d, maximum=10)
    if score['calibration_status'] != 'SCORED_NOT_CALIBRATED':
        raise EvidenceError('CALIBRATION_READINESS_SCORE_STATUS_UNSUPPORTED')
    n_city_days = score['n_city_days']
    if type(n_city_days) is not int or n_city_days < 0:
        raise EvidenceError('CALIBRATION_READINESS_CITY_DAY_COUNT_INVALID')

    reasons = []
    if policy.require_attested_independence and (
            score['dependence_unit'] != 'INDEPENDENT_ATTESTED'
            or score['effective_independent_samples'] is None):
        reasons.append('INDEPENDENCE_NOT_ATTESTED')
    if n_city_days < policy.minimum_city_days:
        reasons.append('INSUFFICIENT_CITY_DAYS')
    if len(calendar_days) < policy.minimum_distinct_calendar_days:
        reasons.append('INSUFFICIENT_DISTINCT_CALENDAR_DAYS')
    if score['log_loss_infinite']:
        reasons.append('INFINITE_LOG_LOSS')
    elif score['log_loss'] is None or finite(score['log_loss'], nonnegative=False) > policy.maximum_log_loss:
        reasons.append('LOG_LOSS_EXCEEDS_POLICY')

    return {
        'ready_for_reviewed_calibration': not reasons,
        'unmet_requirements': reasons,
        'policy_sha256': policy.sha256,
        'observed': {
            'n_city_days': n_city_days,
            'distinct_calendar_days': len(calendar_days),
            'dependence_unit': score['dependence_unit'],
            'effective_independent_samples': score['effective_independent_samples'],
            'log_loss': score['log_loss'],
            'log_loss_infinite': score['log_loss_infinite'],
        },
        'note': 'This verdict is a readiness report for human review only. '
                'A True verdict does not authorize code to emit a non-vacuous '
                'probability; probability.py, model_artifacts.py and '
                'valuation.py are unmodified and remain the enforced gates.',
    }
