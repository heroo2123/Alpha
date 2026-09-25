"""Coherent temperature inference with explicit targets and conservative fallback.

This is a data-only research engine. Kernel smoothing and quantization are model
hypotheses, not declarations about how a source rounds observations. Calibration,
station eligibility and financial approval are separate evidence gates.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
import math
from zoneinfo import ZoneInfo

from ..weather_only_contracts import DAILY_HIGH, DAILY_LOW
from .evidence import EvidenceError, canonical, digest, finite, identity, sha
from .rules import RuleFingerprint


VERSION = "alpha_v11_coherent_temperature_v1"
FINAL_EXTREME = "FINAL_CONTRACT_EXTREME"
NEXT_OBSERVATION = "NEXT_OFFICIAL_OBSERVATION"
UNRESOLVED_EXTREME = "UNRESOLVED_CONTRACT_DAY_EXTREME"
MODEL_QUANTIZATION = "NEAREST_WHOLE_DEGREE_HALF_AWAY_FROM_ZERO"


def target_identity(rule: RuleFingerprint, kind: str) -> str:
    if kind not in {FINAL_EXTREME, NEXT_OBSERVATION, UNRESOLVED_EXTREME}:
        raise EvidenceError("PREDICTION_TARGET_UNSUPPORTED")
    p = rule.payload
    return digest({"kind": kind, "rule": rule.sha256, "event_id": p["event_id"],
                   "station": p["station"], "date": p["target_date"], "unit": p["unit"],
                   "population": p["observation_population"], "family": p["family"]})


@dataclass(frozen=True)
class ProbabilityInterval:
    point: float
    lower: float
    upper: float

    def __post_init__(self):
        if not 0 <= finite(self.lower) <= finite(self.point) <= finite(self.upper) <= 1:
            raise EvidenceError("PROBABILITY_INTERVAL_INVALID")

    def for_side(self, side: str) -> "ProbabilityInterval":
        if side == "YES":
            return self
        if side == "NO":
            return ProbabilityInterval(1-self.point, 1-self.upper, 1-self.lower)
        raise EvidenceError("BINARY_SIDE_INVALID")


@dataclass(frozen=True)
class AuxiliaryFeatures:
    schema_sha256: str
    evidence_sha256: str
    values: tuple[tuple[str, float | None], ...]

    def __post_init__(self):
        sha(self.schema_sha256); sha(self.evidence_sha256)
        if (type(self.values) is not tuple or not 1 <= len(self.values) <= 64
                or any(type(p) is not tuple or len(p) != 2 for p in self.values)
                or len({p[0] for p in self.values}) != len(self.values)):
            raise EvidenceError('AUXILIARY_FEATURE_INPUT_SHAPE')
        for key,value in self.values:
            identity(key)
            if value is not None and abs(finite(value,nonnegative=False)) > 1_000_000:
                raise EvidenceError('AUXILIARY_FEATURE_VALUE_BOUND')


@dataclass(frozen=True)
class ForecastComponent:
    model_id: str
    dependence_group: str
    target_sha256: str
    members: tuple[float, ...]
    bias: float
    kernel_sigma: float
    within_group_weight: float
    evidence_sha256: str
    received_at: float
    feature_ready_at: float
    issued_at: float | None
    auxiliary: AuxiliaryFeatures | None = None

    def __post_init__(self):
        identity(self.model_id)
        identity(self.dependence_group)
        sha(self.target_sha256)
        sha(self.evidence_sha256)
        if not isinstance(self.members, tuple) or not 1 <= len(self.members) <= 1000:
            raise EvidenceError("ENSEMBLE_MEMBER_BOUND")
        if any(abs(finite(v, nonnegative=False)) > 250 for v in self.members):
            raise EvidenceError("ENSEMBLE_TEMPERATURE_BOUND")
        if (abs(finite(self.bias, nonnegative=False)) > 30
                or not .01 <= finite(self.kernel_sigma) <= 50
                or not 0 < finite(self.within_group_weight) <= 1):
            raise EvidenceError("MODEL_PARAMETER_BOUND")
        if finite(self.feature_ready_at) < finite(self.received_at):
            raise EvidenceError("FEATURE_PRECEDES_RECEIPT")
        if self.issued_at is not None and finite(self.issued_at) > self.received_at:
            raise EvidenceError("MODEL_ISSUE_AFTER_RECEIPT")
        if self.auxiliary is not None and not isinstance(self.auxiliary,AuxiliaryFeatures):
            raise EvidenceError('TYPED_AUXILIARY_FEATURES_REQUIRED')

    def cdf(self, x: float) -> float:
        # The empirical members are correlated model scenarios, never n IID trials.
        return math.fsum(.5 * math.erfc(-(x-v-self.bias)/(self.kernel_sigma*math.sqrt(2)))
                         for v in self.members) / len(self.members)


@dataclass(frozen=True)
class ObservedConstraint:
    """Exact source-reported whole-degree extreme, conditional on one revision.

    Upstream source certification must establish the record's authority. A PWS or
    METAR proxy cannot be promoted into this type by numerical agreement.
    """
    rule_fingerprint: str
    station: str
    population: str
    unit: str
    family: str
    whole_degree_value: int
    received_at: float
    feature_ready_at: float
    source_revision: str
    evidence_sha256: str
    source_role: str = "EXACT_CONTRACT_OBSERVATION"

    def validate(self, rule: RuleFingerprint, as_of: float):
        p = rule.payload
        sha(self.evidence_sha256)
        identity(self.source_revision)
        if (self.source_role != "EXACT_CONTRACT_OBSERVATION"
                or self.rule_fingerprint != rule.sha256
                or (self.station, self.population, self.unit, self.family) !=
                   (p["station"], p["observation_population"], p["unit"], p["family"])):
            raise EvidenceError("EXACT_OBSERVATION_IDENTITY_REQUIRED")
        if type(self.whole_degree_value) is not int or abs(self.whole_degree_value) > 250:
            raise EvidenceError("SOURCE_WHOLE_DEGREE_REQUIRED")
        if not finite(self.received_at) <= finite(self.feature_ready_at) <= as_of:
            raise EvidenceError("OBSERVATION_NOT_CAUSALLY_AVAILABLE")


@dataclass(frozen=True)
class RemainingPathCoverage:
    """A source-derived partition of the local day, including unresolved gaps.

    Interval coverage is necessary but not proof that a provider forecast models
    those intervals correctly. Source/model capability certification remains gated.
    """
    rule_fingerprint: str
    as_of: float
    accepted_intervals: tuple[tuple[float, float], ...]
    unresolved_intervals: tuple[tuple[float, float], ...]
    model_evidence_sha256: tuple[str, ...]
    source_coverage_sha256: str

    def validate(self, rule: RuleFingerprint, components: tuple[ForecastComponent, ...], as_of: float):
        p = rule.payload
        sha(self.source_coverage_sha256)
        if self.rule_fingerprint != rule.sha256 or finite(self.as_of) != as_of:
            raise EvidenceError("REMAINING_COVERAGE_IDENTITY")
        if (not isinstance(self.model_evidence_sha256, tuple)
                or set(self.model_evidence_sha256) != {c.evidence_sha256 for c in components}):
            raise EvidenceError("REMAINING_MODEL_COVERAGE_BINDING")
        d = date.fromisoformat(p["target_date"])
        tz = ZoneInfo(p["timezone"])
        start = datetime.combine(d, time.min, tz).timestamp()
        end = datetime.combine(d+timedelta(days=1), time.min, tz).timestamp()
        if not start <= as_of < end:
            raise EvidenceError("SAME_DAY_WINDOW_REQUIRED")
        if (not isinstance(self.accepted_intervals, tuple) or not isinstance(self.unresolved_intervals, tuple)
                or not self.unresolved_intervals or len(self.accepted_intervals)+len(self.unresolved_intervals) > 1000):
            raise EvidenceError("COVERAGE_INTERVAL_BOUND")
        intervals = []
        for accepted, rows in ((True, self.accepted_intervals), (False, self.unresolved_intervals)):
            for a, b in rows:
                if not start <= finite(a) < finite(b) <= end or accepted and b > as_of:
                    raise EvidenceError("COVERAGE_INTERVAL_INVALID")
                intervals.append((a, b))
        cursor = start
        for a, b in sorted(intervals):
            if a != cursor:
                raise EvidenceError("UNRESOLVED_DAY_GAP_OR_OVERLAP")
            cursor = b
        if cursor != end:
            raise EvidenceError("UNRESOLVED_DAY_INCOMPLETE")


@dataclass(frozen=True)
class BucketPrediction:
    canonical_json: str
    sha256: str

    @property
    def payload(self) -> dict:
        import json
        result = json.loads(self.canonical_json)
        if digest(result) != self.sha256:
            raise EvidenceError("PREDICTION_INTEGRITY")
        return result

    def binary(self, market_id: str, side: str, *, required_target: str) -> ProbabilityInterval:
        p = self.payload
        if required_target != p["target"]:
            raise EvidenceError("OBSERVATION_IS_NOT_SETTLEMENT_OR_EXIT")
        rows = [b for b in p["buckets"] if b["market_id"] == market_id]
        if len(rows) != 1:
            raise EvidenceError("PREDICTION_MARKET_UNKNOWN")
        b = rows[0]
        return ProbabilityInterval(b["point"], b["lower"], b["upper"]).for_side(side)


def _partition(rule: RuleFingerprint) -> list[dict]:
    p = rule.payload
    if p["precision_rounding"] != "WHOLE_DEGREE_" + p["unit"]:
        raise EvidenceError("PROBABILITY_PRECISION_UNSUPPORTED")
    buckets = sorted(p["partition"], key=lambda b: -math.inf if b["lower"] is None else b["lower"])
    if not 2 <= len(buckets) <= 100 or buckets[0]["lower"] is not None or buckets[-1]["upper"] is not None:
        raise EvidenceError("COMPLETE_BUCKET_PARTITION_REQUIRED")
    for i, b in enumerate(buckets):
        for field in ("lower", "upper"):
            value = b[field]
            if value is not None and (not finite(value, nonnegative=False).is_integer() or abs(value) > 250):
                raise EvidenceError("WHOLE_DEGREE_PARTITION_REQUIRED")
        if b["lower"] is not None and b["upper"] is not None and b["lower"] > b["upper"]:
            raise EvidenceError("BUCKET_INTERVAL_INVALID")
        if i and (b["lower"] is None or buckets[i-1]["upper"] is None
                  or b["lower"] != buckets[i-1]["upper"]+1):
            raise EvidenceError("BUCKET_GAP_OR_OVERLAP")
    return buckets


def predict_buckets(rule: RuleFingerprint, components: tuple[ForecastComponent, ...], *,
                    group_weights: tuple[tuple[str, float], ...], as_of: float,
                    bundle_sha256: str, max_source_age_seconds: float,
                    target: str = FINAL_EXTREME, observed: ObservedConstraint | None = None,
                    remaining_coverage: RemainingPathCoverage | None = None) -> BucketPrediction:
    """A mixture CDF yields one coherent vector, independent of member counts.

    With an exact accepted observation, model inputs must describe *all unresolved
    portions*, including elapsed gaps. The output is conditional on that revision;
    unmodeled revision/fallback risk remains explicit in full-width bounds.
    """
    as_of = finite(as_of)
    sha(bundle_sha256)
    if target not in {FINAL_EXTREME, NEXT_OBSERVATION}:
        raise EvidenceError("OUTPUT_TARGET_UNSUPPORTED")
    if not 0 < finite(max_source_age_seconds) <= 86400:
        raise EvidenceError("SOURCE_FRESHNESS_POLICY_REQUIRED")
    if not isinstance(components, tuple) or not 1 <= len(components) <= 16:
        raise EvidenceError("MODEL_COUNT_BOUND")
    if len({c.model_id for c in components}) != len(components):
        raise EvidenceError("DUPLICATE_MODEL")
    if not isinstance(group_weights, tuple) or not 1 <= len(group_weights) <= 16:
        raise EvidenceError("DEPENDENCE_GROUP_BOUND")
    groups = dict(group_weights)
    if len(groups) != len(group_weights) or set(groups) != {c.dependence_group for c in components}:
        raise EvidenceError("DEPENDENCE_GROUP_MISMATCH")
    if any(not 0 < finite(w) <= 1 for w in groups.values()) or not math.isclose(math.fsum(groups.values()), 1, abs_tol=1e-12):
        raise EvidenceError("GROUP_WEIGHTS_MUST_SUM_TO_ONE")
    if observed is not None:
        if target != FINAL_EXTREME:
            raise EvidenceError("EXTREME_CONSTRAINT_NOT_NEXT_OBSERVATION")
        observed.validate(rule, as_of)
        if not isinstance(remaining_coverage, RemainingPathCoverage):
            raise EvidenceError("REMAINING_COVERAGE_REQUIRED")
        remaining_coverage.validate(rule, components, as_of)
    elif remaining_coverage is not None:
        raise EvidenceError("COVERAGE_WITHOUT_OBSERVED_CONSTRAINT")
    input_target = UNRESOLVED_EXTREME if observed is not None else target
    expected = target_identity(rule, input_target)
    for c in components:
        if c.target_sha256 != expected:
            raise EvidenceError("MODEL_TARGET_MISMATCH")
        if c.feature_ready_at > as_of or as_of-c.received_at > max_source_age_seconds:
            raise EvidenceError("MODEL_NOT_FRESH_OR_CAUSALLY_AVAILABLE")
        if c.issued_at is not None and as_of-c.issued_at > max_source_age_seconds:
            raise EvidenceError("MODEL_RUN_TOO_OLD")
    totals = {g: math.fsum(c.within_group_weight for c in components if c.dependence_group == g) for g in groups}
    weights = {c.model_id: groups[c.dependence_group]*c.within_group_weight/totals[c.dependence_group] for c in components}
    p = rule.payload
    if p["family"] not in {DAILY_HIGH, DAILY_LOW}:
        raise EvidenceError("EXTREME_FAMILY_UNSUPPORTED")

    def cdf(cut: float) -> float:
        if observed is not None:
            value = observed.whole_degree_value
            if p["family"] == DAILY_HIGH and cut <= value:
                return 0.0
            if p["family"] == DAILY_LOW and cut > value:
                return 1.0
        return math.fsum(weights[c.model_id]*c.cdf(cut) for c in components)

    rows, previous = [], 0.0
    for b in _partition(rule):
        cumulative = 1.0 if b["upper"] is None else cdf(b["upper"]+.5)
        point = cumulative-previous
        if point < -1e-12 or point > 1+1e-12:
            raise EvidenceError("CDF_COHERENCE_FAILURE")
        rows.append({"market_id": b["market_id"], "yes_token": b["yes_token"], "no_token": b["no_token"],
                     "point": min(1.0, max(0.0, point)), "lower": 0.0, "upper": 1.0})
        previous = cumulative
    if not math.isclose(math.fsum(b["point"] for b in rows), 1, abs_tol=1e-12):
        raise EvidenceError("FAIR_VECTOR_SUM_FAILURE")
    model_means = {c.model_id: math.fsum(c.members)/len(c.members)+c.bias for c in components}
    mean = math.fsum(weights[k]*v for k, v in model_means.items())
    body = {"version": VERSION, "event_id": p["event_id"], "target": target,
            "rule_fingerprint": rule.sha256, "bundle_sha256": bundle_sha256, "as_of": as_of,
            "station": p["station"], "target_date": p["target_date"], "family": p["family"], "unit": p["unit"],
            "buckets": rows, "fair_probability_sum": math.fsum(b["point"] for b in rows),
            "calibration_status": "UNCALIBRATED", "uncertainty_method": "VACUOUS_BOUNDS_NO_CALIBRATION_EVIDENCE",
            "lower_bounds_normalized": False, "model_quantization_hypothesis": MODEL_QUANTIZATION,
            "quantization_is_source_rule": False, "group_weights": dict(group_weights),
            "model_weights": weights, "model_inputs": [{k:v for k,v in asdict(c).items()
                if k != 'auxiliary' or v is not None} for c in components],
            "independent_sample_count": None, "member_count_is_effective_sample_size": False,
            "dependence_validation": "DECLARED_GROUPS_NOT_EMPIRICALLY_VALIDATED",
            "between_model_mean_variance": math.fsum(weights[k]*(v-mean)**2 for k, v in model_means.items()),
            "model_run_age_known": all(c.issued_at is not None for c in components),
            "observed_constraint": asdict(observed) if observed else None,
            "remaining_coverage": asdict(remaining_coverage) if remaining_coverage else None,
            "revision_risk": "CONDITIONAL_ON_ACCEPTED_REVISION_UNMODELED" if observed else "UNMODELED",
            "source_fallback_risk": "UNMODELED", "settlement_authority": False,
            "financial_authority": False, "executable_exit_value": None}
    return BucketPrediction(canonical(body), digest(body))


CALIBRATION_ERROR_METHOD = 'CITY_DAY_EVENT_SNAPSHOT_BUCKET_EQUAL_WIDTH_10_V1'


def score_vectors(probabilities: list[list[float]], outcomes: list[int], *,
                  event_ids: list[str], city_days: list[str], calibration_error_method: str | None = None) -> dict:
    """Equal city-day weight, then event weight, then repeated-decision weight.

    Correlated snapshots are retained but cannot inflate effective sample count.
    Infinite log loss is reported, never silently clipped away.
    Optional ECE gives each vector's buckets equal shares of that vector's weight.
    Ten fixed [lower,upper) bins include p=1 in the final bin. The reported value
    is sum(bin weight * abs(mean probability - observed frequency)). It is a
    finite-bin descriptive statistic, not a confidence bound or calibration proof.
    Omitted method preserves the original serialized output and learner identities.
    """
    if calibration_error_method not in {None, CALIBRATION_ERROR_METHOD}:
        raise EvidenceError('CALIBRATION_ERROR_METHOD_UNSUPPORTED')
    n = len(probabilities)
    if not 1 <= n <= 100_000 or not len(outcomes) == len(event_ids) == len(city_days) == n:
        raise EvidenceError("SCORE_SHAPE_INVALID")
    if sum(len(v) for v in probabilities) > 1_000_000:
        raise EvidenceError("SCORE_BUCKET_BOUND")
    groups, event_groups, event_outcomes = {}, {}, {}
    rows = []
    for vector, outcome, event, group in zip(probabilities, outcomes, event_ids, city_days):
        identity(event)
        identity(group)
        if (not isinstance(vector, list) or not 2 <= len(vector) <= 100
                or any(not 0 <= finite(p) <= 1 for p in vector)
                or not math.isclose(math.fsum(vector), 1, abs_tol=1e-10)
                or type(outcome) is not int or not 0 <= outcome < len(vector)):
            raise EvidenceError("SCORE_VECTOR_INVALID")
        if event in event_groups and event_groups[event] != group:
            raise EvidenceError("EVENT_CITY_DAY_CONFLICT")
        if event in event_outcomes and event_outcomes[event] != (len(vector), outcome):
            raise EvidenceError("EVENT_OUTCOME_CONFLICT")
        event_groups[event] = group
        event_outcomes[event] = (len(vector), outcome)
        groups.setdefault(group, {}).setdefault(event, []).append(len(rows))
        rows.append({"brier": math.fsum((p-(i == outcome))**2 for i, p in enumerate(vector)),
                     "log_loss": None if vector[outcome] == 0 else -math.log(vector[outcome]),
                     "sharpness": math.fsum(p*p for p in vector)})
    weights = [0.0]*n
    for events in groups.values():
        for indices in events.values():
            for i in indices:
                weights[i] = 1/(len(groups)*len(events)*len(indices))
    infinite = any(r["log_loss"] is None for r in rows)
    bins = []
    for low in range(10):
        entries = [(p, int(j == outcomes[i]), weights[i]) for i, v in enumerate(probabilities)
                   for j, p in enumerate(v) if min(9, int(p*10)) == low]
        total = math.fsum(w for _, _, w in entries)
        bins.append({"lower": low/10, "upper": (low+1)/10, "n_bucket_predictions": len(entries),
                     "mean_probability": math.fsum(p*w for p, _, w in entries)/total if total else None,
                     "outcome_rate": math.fsum(y*w for _, y, w in entries)/total if total else None})
    result = {"n_predictions": n, "n_events": len(event_groups), "n_city_days": len(groups),
            "effective_independent_samples": None, "dependence_unit": "CITY_DAY_NOT_PROVEN_INDEPENDENT",
            "brier": math.fsum(w*r["brier"] for w, r in zip(weights, rows)),
            "log_loss": None if infinite else math.fsum(w*r["log_loss"] for w, r in zip(weights, rows)),
            "log_loss_infinite": infinite, "sharpness": math.fsum(w*r["sharpness"] for w, r in zip(weights, rows)),
            "reliability": bins, "calibration_status": "SCORED_NOT_CALIBRATED"}
    if calibration_error_method is not None:
        calibration_bins = []
        for low in range(10):
            entries = [(p, int(j == outcomes[i]), weights[i]/len(v)) for i, v in enumerate(probabilities)
                       for j, p in enumerate(v) if min(9, int(p*10)) == low]
            mass = math.fsum(w for _, _, w in entries)
            mean = math.fsum(p*w for p, _, w in entries)/mass if mass else None
            rate = math.fsum(y*w for _, y, w in entries)/mass if mass else None
            calibration_bins.append(dict(lower=low/10, upper=(low+1)/10,
                upper_inclusive=low==9, weight=mass, n_bucket_predictions=len(entries),
                mean_probability=mean, outcome_rate=rate, absolute_gap=abs(mean-rate) if mass else None))
        result['calibration_error'] = dict(method=calibration_error_method,
            value=math.fsum(b['weight']*b['absolute_gap'] for b in calibration_bins if b['weight']),
            bins=calibration_bins, confidence_interval=None, independent_sample_claim=False,
            interpretation='BIN_DEPENDENT_DESCRIPTIVE_ECE_NOT_CALIBRATION_ACCEPTANCE')
    return result
