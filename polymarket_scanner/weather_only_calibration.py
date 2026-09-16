from __future__ import annotations

"""Pure, preregistered calibration assessment for the weather-only program.

This module deliberately does not choose a probability model, score bins, sample
thresholds or promotion rules after seeing results. A caller must provide an
immutable ``CalibrationPolicy`` that was frozen for the model under study.

Only labels that explicitly attest reconstruction of the rule-selected settlement
state enter the clean sample. Official proxies, historical convenience archives,
legacy rows and mismatched evidence versions remain useful research data but cannot
silently mature a probability model toward trading authority.

A successful assessment means only that the preregistered *research calibration*
gates passed. ``financial_authority`` is permanently false here.
"""

import math
from collections import Counter
from dataclasses import asdict, dataclass


CALIBRATION_ENGINE_VERSION = "weather_only_calibration_v1_preregistered_clean_labels"
SETTLEMENT_LABEL_EVIDENCE_VERSION = "WEATHER_SETTLEMENT_LABEL_V1_EXACT_RULE_STATE"


@dataclass(frozen=True, slots=True)
class CalibrationPolicy:
    policy_id: str
    probability_bins: tuple[tuple[float, float], ...]
    min_total_resolved: int
    min_bin_resolved: int
    min_distinct_stations: int
    max_brier_score: float
    wilson_z: float

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("policy_id is required")
        if not isinstance(self.probability_bins, tuple) or not self.probability_bins:
            raise ValueError("probability_bins are required")
        previous_hi = None
        for index, pair in enumerate(self.probability_bins):
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ValueError("each probability bin must be a (lo, hi) tuple")
            if any(isinstance(value, bool) for value in pair):
                raise ValueError("probability-bin bounds cannot be booleans")
            try:
                lo, hi = float(pair[0]), float(pair[1])
            except (TypeError, ValueError, OverflowError):
                raise ValueError("invalid probability bin")
            if not (math.isfinite(lo) and math.isfinite(hi) and 0.0 <= lo < hi <= 1.0):
                raise ValueError("invalid probability bin")
            if index == 0 and abs(lo) > 1e-12:
                raise ValueError("probability bins must start at zero")
            if previous_hi is not None and abs(lo - previous_hi) > 1e-12:
                raise ValueError("probability bins must be contiguous and non-overlapping")
            previous_hi = hi
        if previous_hi is None or abs(previous_hi - 1.0) > 1e-12:
            raise ValueError("probability bins must end at one")

        for value in (self.min_total_resolved, self.min_bin_resolved, self.min_distinct_stations):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("sample-count gates must be positive integers")
        if isinstance(self.max_brier_score, bool) or not isinstance(self.max_brier_score, (int, float)):
            raise ValueError("max_brier_score must be numeric")
        if not math.isfinite(float(self.max_brier_score)) or not 0.0 <= float(self.max_brier_score) <= 1.0:
            raise ValueError("max_brier_score must be in [0,1]")
        if isinstance(self.wilson_z, bool) or not isinstance(self.wilson_z, (int, float)):
            raise ValueError("wilson_z must be numeric")
        if not math.isfinite(float(self.wilson_z)) or float(self.wilson_z) <= 0.0:
            raise ValueError("wilson_z must be positive")


@dataclass(frozen=True, slots=True)
class ProbabilityCalibrationSample:
    event_id: str
    station: str
    model_version: str
    predicted_probability: float
    final_payout: float
    label_adapter: str
    source_role: str
    evidence_version: str
    label_authority: bool
    settlement_state_reconstructable: bool


@dataclass(frozen=True, slots=True)
class CalibrationAssessment:
    engine_version: str
    policy_id: str
    model_version: str
    target_probability: float
    target_bin_index: int | None
    clean_total_resolved: int
    clean_bin_resolved: int
    distinct_stations: int
    overall_brier_score: float | None
    bin_mean_prediction: float | None
    bin_empirical_payout: float | None
    bin_wilson_lower_bound: float | None
    excluded_counts: tuple[tuple[str, int], ...]
    research_calibration_ready: bool
    reasons: tuple[str, ...]
    financial_authority: bool

    def as_dict(self) -> dict:
        return asdict(self)


def _finite_probability(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        return None
    return number


def _bin_index(value: float, bins: tuple[tuple[float, float], ...]) -> int | None:
    for index, (lo, hi) in enumerate(bins):
        # The final bin is closed at one; all previous bins are half-open.
        if lo <= value < hi or (index == len(bins) - 1 and value == 1.0):
            return index
    return None


def _wilson_lower(successes: float, n: int, z: float) -> float | None:
    """Wilson lower bound using fractional payouts as conservative successes."""
    if n <= 0:
        return None
    p = min(1.0, max(0.0, float(successes) / float(n)))
    z2 = z * z
    denom = 1.0 + z2 / n
    center = p + z2 / (2.0 * n)
    radius = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
    return max(0.0, (center - radius) / denom)


def _clean_sample_reason(sample: ProbabilityCalibrationSample, model_version: str) -> str | None:
    if str(sample.model_version or "") != str(model_version or ""):
        return "MODEL_VERSION_MISMATCH"
    if sample.label_authority is not True:
        return "LABEL_AUTHORITY_FALSE"
    if sample.settlement_state_reconstructable is not True:
        return "SETTLEMENT_STATE_NOT_RECONSTRUCTABLE"
    if str(sample.evidence_version or "") != SETTLEMENT_LABEL_EVIDENCE_VERSION:
        return "LABEL_EVIDENCE_VERSION_MISMATCH"
    if not str(sample.label_adapter or "").strip():
        return "LABEL_ADAPTER_MISSING"
    if not str(sample.source_role or "").strip():
        return "SOURCE_ROLE_MISSING"
    if "PROXY" in str(sample.source_role).upper() or "PROXY" in str(sample.label_adapter).upper():
        return "PROXY_LABEL_EXCLUDED"
    if not str(sample.event_id or "").strip():
        return "EVENT_ID_MISSING"
    if not str(sample.station or "").strip():
        return "STATION_MISSING"
    if _finite_probability(sample.predicted_probability) is None:
        return "PREDICTED_PROBABILITY_INVALID"
    if _finite_probability(sample.final_payout) is None:
        return "FINAL_PAYOUT_INVALID"
    return None


def assess_probability_calibration(
    samples: list[ProbabilityCalibrationSample] | tuple[ProbabilityCalibrationSample, ...],
    *,
    model_version: str,
    target_probability: float,
    policy: CalibrationPolicy,
) -> CalibrationAssessment:
    """Assess one target probability against a caller-frozen policy and clean labels."""
    if not str(model_version or "").strip():
        raise ValueError("model_version is required")
    target = _finite_probability(target_probability)
    if target is None:
        raise ValueError("target_probability must be in [0,1]")
    target_bin = _bin_index(target, policy.probability_bins)
    if target_bin is None:
        raise ValueError("target_probability is outside policy bins")

    excluded = Counter()
    clean: list[ProbabilityCalibrationSample] = []
    seen_events: set[str] = set()
    for sample in samples:
        if not isinstance(sample, ProbabilityCalibrationSample):
            excluded["SAMPLE_TYPE_INVALID"] += 1
            continue
        reason = _clean_sample_reason(sample, model_version)
        if reason:
            excluded[reason] += 1
            continue
        event_id = sample.event_id.strip()
        if event_id in seen_events:
            excluded["DUPLICATE_EVENT_LABEL"] += 1
            continue
        seen_events.add(event_id)
        clean.append(sample)

    total = len(clean)
    stations = len({sample.station.strip().upper() for sample in clean})
    brier = (
        sum((float(sample.predicted_probability) - float(sample.final_payout)) ** 2 for sample in clean) / total
        if total else None
    )
    in_bin = [
        sample for sample in clean
        if _bin_index(float(sample.predicted_probability), policy.probability_bins) == target_bin
    ]
    bin_n = len(in_bin)
    bin_mean = sum(float(sample.predicted_probability) for sample in in_bin) / bin_n if bin_n else None
    empirical = sum(float(sample.final_payout) for sample in in_bin) / bin_n if bin_n else None
    lower = _wilson_lower(
        sum(float(sample.final_payout) for sample in in_bin),
        bin_n,
        float(policy.wilson_z),
    ) if bin_n else None

    reasons: list[str] = []
    if total < policy.min_total_resolved:
        reasons.append(f"need {policy.min_total_resolved} clean resolved samples; have {total}")
    if bin_n < policy.min_bin_resolved:
        reasons.append(f"need {policy.min_bin_resolved} clean samples in target bin; have {bin_n}")
    if stations < policy.min_distinct_stations:
        reasons.append(f"need {policy.min_distinct_stations} distinct stations; have {stations}")
    if brier is None or brier > float(policy.max_brier_score):
        shown = "n/a" if brier is None else f"{brier:.6f}"
        reasons.append(f"Brier score {shown} exceeds policy gate {float(policy.max_brier_score):.6f}")
    if lower is None:
        reasons.append("target-bin Wilson lower bound unavailable")

    ready = not reasons
    return CalibrationAssessment(
        engine_version=CALIBRATION_ENGINE_VERSION,
        policy_id=policy.policy_id,
        model_version=str(model_version),
        target_probability=target,
        target_bin_index=target_bin,
        clean_total_resolved=total,
        clean_bin_resolved=bin_n,
        distinct_stations=stations,
        overall_brier_score=brier,
        bin_mean_prediction=bin_mean,
        bin_empirical_payout=empirical,
        bin_wilson_lower_bound=lower,
        excluded_counts=tuple(sorted(excluded.items())),
        research_calibration_ready=ready,
        reasons=tuple(reasons),
        financial_authority=False,
    )
