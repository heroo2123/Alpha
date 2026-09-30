"""Offline typed source-native sampled-trajectory / capture contract: R09_NATIVE_2T_TRAJECTORY_V1.

Separate from the existing exact-local-day panel (tools/v11_multimodel_panel.py) and
grouped-pool stacking evaluator (tools/v11_multimodel_stacking.py), which this module
does not modify and does not feed. Per docs/V11_R09_DATA_CONTRACT_ADJUDICATION.md, a
trajectory of ordered native instantaneous samples is a predictor, never a claimed
exact-day extreme, and this contract identity must never be aliased to the legacy
ones. This module is schema/admission validation only: it acquires no data, fits no
model, and admits no real (non-SYNTHETIC) example. Unknown or untrusted receipt/label
facts fail closed; a passing validation here is not causal admission of any historical
or live example.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math

from tools.v11_multimodel_panel import FAMILIES, PROVIDERS, is_sha, local_window, require

CONTRACT_ID = 'R09_NATIVE_2T_TRAJECTORY_V1'

# Identities this contract must never be aliased to (docs/V11_R09_DATA_CONTRACT_ADJUDICATION.md:58-60).
FORBIDDEN_IDENTITY_ALIASES = frozenset({
    'EXACT_LOCAL_DAY_EXTREME',
    'PIECEWISE_LINEAR_POINT_TEMPERATURE_PATH',
    'NATIVE_POINT_PANEL_WITH_SAMPLED_EXTREMA_DIAGNOSTICS_ONLY',
})

SPLITS = ('TRAIN', 'DEVELOPMENT', 'CONFIRMATION')

# Per-clock-role trusted origin whitelists. A clock whose origin is not in its role's
# set, or whose health is not SYNCED, is untrusted and must fail closed wherever it
# gates causal admission. "Unknown is not zero or initialization time."
ALLOWED_ORIGINS = {
    'decision_at': frozenset({'ALPHA_PREREGISTERED_PROTOCOL'}),
    'fit_cutoff': frozenset({'ALPHA_PREREGISTERED_PROTOCOL'}),
    'selection_freeze_at': frozenset({'ALPHA_PREREGISTERED_PROTOCOL'}),
    'evaluation_asof': frozenset({'ALPHA_PREREGISTERED_PROTOCOL'}),
    'response_completed_at': frozenset({'LOCAL_RECEIPT_WALL_CLOCK'}),
    'feature_ready_at': frozenset({'LOCAL_RECEIPT_WALL_CLOCK'}),
    'prediction_frozen_at': frozenset({'LOCAL_RECEIPT_WALL_CLOCK'}),
    'label_knowable_at': frozenset({'INDEPENDENT_CONTEMPORANEOUS_ATTESTATION', 'LOCAL_RECEIPT_WALL_CLOCK'}),
    'source_published_at': frozenset({'INDEPENDENT_CONTEMPORANEOUS_ATTESTATION'}),
}
# Declared-only clocks: initialization/valid time is never an availability attestation.
DECLARED_ORIGIN = 'PROVIDER_METADATA'
CLOCK_HEALTH_VALUES = ('SYNCED', 'DEGRADED', 'UNKNOWN')
ALL_ORIGINS = frozenset().union(*ALLOWED_ORIGINS.values()) | {
    DECLARED_ORIGIN, 'PROVIDER_SCHEDULE', 'HTTP_LAST_MODIFIED_HEADER',
    'CATALOG_CREATED_AT', 'WEATHER_DAY_END_HEURISTIC', 'INITIALIZATION_TIME_COPY', 'UNKNOWN',
}


@dataclass(frozen=True)
class Timestamp:
    utc: float
    origin: str
    uncertainty_seconds: float
    clock_health: str

    def __post_init__(self):
        require(math.isfinite(self.utc) and self.utc > 0, 'TIMESTAMP_UTC_REQUIRED')
        require(self.origin in ALL_ORIGINS, 'TIMESTAMP_ORIGIN_UNKNOWN')
        require(math.isfinite(self.uncertainty_seconds) and self.uncertainty_seconds >= 0, 'TIMESTAMP_UNCERTAINTY')
        require(self.clock_health in CLOCK_HEALTH_VALUES, 'TIMESTAMP_CLOCK_HEALTH_ENUM')

    @property
    def conservative_upper_bound(self):
        return self.utc + self.uncertainty_seconds


def require_trusted(ts, role):
    require(ts.origin in ALLOWED_ORIGINS[role], role.upper() + '_ORIGIN_UNTRUSTED')
    require(ts.clock_health == 'SYNCED', role.upper() + '_CLOCK_HEALTH_NOT_SYNCED')


@dataclass(frozen=True)
class UnitConversion:
    original_unit: str
    stored_unit: str
    formula: str

    def __post_init__(self):
        require(self.original_unit == 'K', 'ORIGINAL_UNIT')
        require(self.stored_unit == 'C', 'STORED_UNIT')
        require(self.formula == 'SUBTRACT_273.15', 'CONVERSION_FORMULA')


@dataclass(frozen=True)
class GridExtraction:
    nearest_lat: float
    nearest_lon: float
    distance_km: float
    grid_resolution_deg: float
    grid_sha256: str

    def __post_init__(self):
        require(-90 <= self.nearest_lat <= 90 and -180 <= self.nearest_lon <= 360, 'GRID_COORDINATES')
        require(0 <= self.distance_km <= 50, 'GRID_DISTANCE')
        require(math.isfinite(self.grid_resolution_deg) and self.grid_resolution_deg > 0, 'GRID_RESOLUTION')
        require(is_sha(self.grid_sha256), 'GRID_DIGEST')


@dataclass(frozen=True)
class CaptureRef:
    byte_sha256: str
    index_sha256: str
    store: str
    immutable: bool

    def __post_init__(self):
        require(is_sha(self.byte_sha256) and is_sha(self.index_sha256), 'CAPTURE_DIGESTS')
        require(self.immutable is True, 'CAPTURE_MUST_BE_IMMUTABLE')
        require(isinstance(self.store, str) and 'private' in self.store, 'CAPTURE_STORE_MUST_BE_PRIVATE_CONTENT_ADDRESSED')


@dataclass(frozen=True)
class LocalDay:
    target_date: str
    timezone: str
    start_utc: float
    end_utc_exclusive: float

    def __post_init__(self):
        start, end = local_window(self.target_date, self.timezone)
        require(start == self.start_utc and end == self.end_utc_exclusive, 'LOCAL_DAY_GEOMETRY_MISMATCH')


@dataclass(frozen=True)
class TrajectoryPoint:
    contract_identity: str
    provider: str
    source_release: str
    release_binding: str
    run_date: str
    cycle: int
    member: int
    forecast_hour: int
    run_initialized_at: Timestamp
    valid_at: Timestamp
    source_published_at: Timestamp | None
    response_completed_at: Timestamp
    feature_ready_at: Timestamp
    value_native_k: float
    conversion: UnitConversion
    grid: GridExtraction
    capture: CaptureRef
    rule_version: str
    station_version: str

    def __post_init__(self):
        require(self.contract_identity == CONTRACT_ID, 'CONTRACT_IDENTITY')
        require(self.contract_identity not in FORBIDDEN_IDENTITY_ALIASES, 'CONTRACT_IDENTITY_ALIAS_FORBIDDEN')
        require(self.provider in PROVIDERS, 'PROVIDER_IDENTITY')
        require(type(self.member) is int and 0 <= self.member < PROVIDERS[self.provider], 'MEMBER_RANGE')
        require(type(self.forecast_hour) is int and self.forecast_hour >= 0, 'FORECAST_HOUR')
        require(self.release_binding in ('OBSERVED_HEADER_ONLY', 'ATTESTED'), 'RELEASE_BINDING')
        require(self.cycle in (0, 6, 12, 18), 'CYCLE')
        require(self.source_release == f'{self.provider}:{self.run_date}:{self.cycle:02d}Z', 'SOURCE_RELEASE_IDENTITY')
        require(is_sha(self.rule_version) and is_sha(self.station_version), 'RULE_STATION_VERSION_DIGESTS')
        require(math.isfinite(self.value_native_k) and 150 <= self.value_native_k <= 350, 'NATIVE_VALUE_RANGE')
        require(self.run_initialized_at.origin == DECLARED_ORIGIN, 'RUN_INITIALIZED_AT_NOT_AN_AVAILABILITY_ATTESTATION')
        require(self.valid_at.origin == DECLARED_ORIGIN, 'VALID_AT_NOT_AN_AVAILABILITY_ATTESTATION')
        require(abs(self.valid_at.utc - (self.run_initialized_at.utc + self.forecast_hour * 3600)) < 1,
                'VALID_AT_HOUR_CONSISTENCY')
        require_trusted(self.response_completed_at, 'response_completed_at')
        require_trusted(self.feature_ready_at, 'feature_ready_at')
        # Initialization-as-availability fraud: actual byte receipt cannot precede or
        # coincide with model initialization; a forecast takes nonzero time to run,
        # transmit and land locally. Equal timestamps or a copied origin are rejected.
        require(self.response_completed_at.utc > self.run_initialized_at.utc,
                'INITIALIZATION_AS_AVAILABILITY_FRAUD')
        require(self.feature_ready_at.utc >= self.response_completed_at.utc, 'FEATURE_READY_BEFORE_RECEIPT')
        if self.source_published_at is not None:
            require_trusted(self.source_published_at, 'source_published_at')


@dataclass(frozen=True)
class ExpectedCoverage:
    provider: str
    expected_members: tuple
    expected_hours: tuple

    def __post_init__(self):
        require(self.provider in PROVIDERS, 'PROVIDER_IDENTITY')
        require(tuple(sorted(set(self.expected_members))) == tuple(range(PROVIDERS[self.provider])),
                'EXPECTED_MEMBER_SET_COMPLETE')
        require(bool(self.expected_hours) and tuple(sorted(set(self.expected_hours))) == tuple(self.expected_hours),
                'EXPECTED_HOUR_SET_ORDERED_UNIQUE')


def validate_coverage(points, expected):
    """Fail closed on any expected (member, hour) with no message. Points outside the
    expected set (finer native cadence, or points beyond the frozen policy window) are
    a legitimate geometry/cadence gap, not a missing message, and are reported but not
    gating."""
    require(all(p.provider == expected.provider for p in points), 'PROVIDER_MISMATCH')
    present = [(p.member, p.forecast_hour) for p in points]
    require(len(set(present)) == len(present), 'DUPLICATE_MEMBER_HOUR_POINT')
    present_set = set(present)
    required = {(m, h) for m in expected.expected_members for h in expected.expected_hours}
    missing = required - present_set
    require(not missing, 'MISSING_EXPECTED_MESSAGE:' + ','.join(f'{m}:{h}' for m, h in sorted(missing)))
    return dict(required=sorted(required), present_all=sorted(present_set),
                extra_native_points=sorted(present_set - required))


def local_day_window(target_date, timezone_name):
    return local_window(target_date, timezone_name)


def in_day_observation(point, local_day):
    return local_day.start_utc <= point.valid_at.utc < local_day.end_utc_exclusive


def annotate_relative_time(points, local_day):
    """A forecast's valid time may be later (or earlier) than the target day and is
    still legitimate predictor information, provided its bytes were available before
    the decision. Out-of-day points are retained with explicit relative coordinates
    and are never counted as in-day observations."""
    return [dict(point=p, in_day_observation=in_day_observation(p, local_day),
                 relative_to_day_start_hours=(p.valid_at.utc - local_day.start_utc) / 3600)
            for p in points]


def validate_rule_consistency(points):
    versions = {p.rule_version for p in points}
    require(len(versions) == 1, 'RULE_DRIFT_WITHIN_EXAMPLE')
    return next(iter(versions))


@dataclass(frozen=True)
class LabelFact:
    label_version: str
    label_knowable_at: Timestamp
    family: str
    winner_bucket: int
    revision_of: str | None

    def __post_init__(self):
        require(is_sha(self.label_version), 'LABEL_VERSION_DIGEST')
        require(self.family in FAMILIES, 'LABEL_FAMILY_IDENTITY')
        require(type(self.winner_bucket) is int and self.winner_bucket >= 0, 'LABEL_WINNER_BUCKET')
        require_trusted(self.label_knowable_at, 'label_knowable_at')
        require(self.label_knowable_at.origin not in ('CATALOG_CREATED_AT', 'WEATHER_DAY_END_HEURISTIC'),
                'LABEL_KNOWABLE_AT_MUST_NOT_BE_CATALOG_OR_DAY_END')
        require(self.revision_of is None or is_sha(self.revision_of), 'LABEL_REVISION_LINK')
        require(self.revision_of != self.label_version, 'LABEL_SELF_REVISION')


def validate_label_lineage(facts):
    """Ordered, append-only correction chain. A correction is a new label_version
    linked to the prior one, never a rewrite of an existing version."""
    require(bool(facts), 'LABEL_LINEAGE_EMPTY')
    seen = set()
    prev = None
    for fact in facts:
        require(fact.label_version not in seen, 'LABEL_VERSION_REUSED')
        seen.add(fact.label_version)
        if prev is None:
            require(fact.revision_of is None, 'FIRST_LABEL_NOT_REVISION')
        else:
            require(fact.revision_of == prev.label_version, 'LABEL_LINEAGE_BROKEN')
            require(fact.label_knowable_at.utc > prev.label_knowable_at.utc, 'LABEL_CORRECTION_NOT_LATER')
        prev = fact
    return prev


@dataclass(frozen=True)
class CausalClocks:
    decision_at: Timestamp
    prediction_frozen_at: Timestamp

    def __post_init__(self):
        require_trusted(self.decision_at, 'decision_at')
        require_trusted(self.prediction_frozen_at, 'prediction_frozen_at')
        require(self.decision_at.conservative_upper_bound <= self.prediction_frozen_at.utc,
                'PREDICTION_NOT_FROZEN_AFTER_DECISION')


CUTOFF_ROLE = {'TRAIN': 'fit_cutoff', 'DEVELOPMENT': 'selection_freeze_at', 'CONFIRMATION': 'evaluation_asof'}


def validate_split_cutoffs(cutoffs):
    """Separate TRAIN/DEVELOPMENT/CONFIRMATION cutoffs, never one global cutoff."""
    require(set(cutoffs) == set(SPLITS), 'SPLIT_CUTOFF_SET')
    ordered = [cutoffs[s] for s in SPLITS]
    for split, ts in cutoffs.items():
        require_trusted(ts, CUTOFF_ROLE[split])
    for a, b in zip(ordered, ordered[1:]):
        require(a.conservative_upper_bound < b.utc, 'SPLIT_CUTOFFS_MUST_BE_DISTINCT_AND_ORDERED')


class CaptureRegistry:
    """Immutable content-addressed capture identity. Replaying identical bytes for an
    already-registered coordinate is a no-op; any conflicting overwrite is refused."""

    def __init__(self):
        self._store = {}

    def register(self, key, byte_sha256):
        require(is_sha(byte_sha256), 'CAPTURE_DIGEST')
        if key in self._store:
            require(self._store[key] == byte_sha256, 'IMMUTABLE_CAPTURE_CONFLICT:' + repr(key))
            return False
        self._store[key] = byte_sha256
        return True


def validate_example(*, city_day, split, points, expected, local_day, clocks, label,
                      split_cutoffs, evidence_class='SYNTHETIC'):
    """Validate one city-day trajectory/capture example end to end. Fails closed on
    any incomplete coverage, untrusted or missing clock, or out-of-order causal
    dependency. Never admits a non-SYNTHETIC example: no real adapter exists yet."""
    require(evidence_class == 'SYNTHETIC', 'REAL_ADAPTER_NOT_IMPLEMENTED')
    require(split in SPLITS, 'SPLIT_IDENTITY')
    require(bool(city_day), 'CITY_DAY_IDENTITY')
    coverage = validate_coverage(points, expected)
    rule_version = validate_rule_consistency(points)
    annotated = annotate_relative_time(points, local_day)
    for p in points:
        require(p.feature_ready_at.conservative_upper_bound <= clocks.decision_at.utc,
                'FEATURE_NOT_AVAILABLE_BY_DECISION')
    require(clocks.prediction_frozen_at.conservative_upper_bound <= label.label_knowable_at.utc,
            'LABEL_KNOWABLE_BEFORE_FROZEN')
    cutoff = split_cutoffs[split]
    require_trusted(cutoff, CUTOFF_ROLE[split])
    if split == 'TRAIN':
        require(label.label_knowable_at.conservative_upper_bound <= cutoff.utc,
                'TRAIN_LABEL_NOT_KNOWABLE_BY_FIT_CUTOFF')
    elif split == 'DEVELOPMENT':
        require(label.label_knowable_at.conservative_upper_bound <= cutoff.utc,
                'DEVELOPMENT_LABEL_NOT_KNOWABLE_BY_SELECTION_FREEZE')
    else:
        require(clocks.prediction_frozen_at.utc <= cutoff.utc,
                'CONFIRMATION_NOT_FROZEN_BEFORE_EVALUATION_ASOF')
        require(label.label_knowable_at.conservative_upper_bound <= cutoff.utc,
                'CONFIRMATION_LABEL_NOT_KNOWABLE_BY_EVALUATION_ASOF')
    return dict(city_day=city_day, split=split, coverage=coverage, rule_version=rule_version,
                label_version=label.label_version, points=len(points),
                in_day_observations=sum(1 for a in annotated if a['in_day_observation']),
                out_of_day_points=sum(1 for a in annotated if not a['in_day_observation']),
                decision_at=clocks.decision_at.utc,
                label_knowable_upper_bound=label.label_knowable_at.conservative_upper_bound,
                learner_admitted=False, evidence_class=evidence_class,
                financial_authority=False, promotion_authority=False,
                host_approved=False, order_authority=False)


def validate_no_duplicate_city_days(records):
    seen = set()
    for r in records:
        require(r['city_day'] not in seen, 'DUPLICATE_CITY_DAY:' + r['city_day'])
        seen.add(r['city_day'])


def validate_cross_split_embargo(records):
    """Frozen trained parameters must precede each held-out prediction they claim to
    simulate; label delay overlapping the next split must be embargoed."""
    by_split = defaultdict(list)
    for r in records:
        by_split[r['split']].append(r)
    for before, after in zip(SPLITS, SPLITS[1:]):
        if by_split[before] and by_split[after]:
            require(max(r['label_knowable_upper_bound'] for r in by_split[before]) <=
                    min(r['decision_at'] for r in by_split[after]), 'CROSS_SPLIT_LABEL_EMBARGO_LEAKAGE')


def validate_corpus(records, split_cutoffs):
    validate_split_cutoffs(split_cutoffs)
    validate_no_duplicate_city_days(records)
    validate_cross_split_embargo(records)
    return dict(city_days=len(records), splits={s: sum(1 for r in records if r['split'] == s) for s in SPLITS},
                admitted_real_examples=0, historical_541_day_admissions=0)
