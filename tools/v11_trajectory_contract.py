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

Gate-2 independent review (fed1cbe, 2026-09-30) required eight repairs, all present
below: (1) every point-level receipt dependency's conservative bound, not only
feature_ready_at's own uncertainty, must clear decision_at; (2) each held-out split's
frozen prediction must follow the prior split's cutoff, and V1 is FUTURE_FORECAST-only
so decision/freeze must precede local-day start; (3) validate_corpus only accepts the
immutable ValidatedExample records this module itself produces, bound to the exact
cutoffs and coverage policy used to build them, with cross-split embargo checked over
every split pair rather than only adjacent ones; (4) run_date/cycle must match
run_initialized_at, and every point in one example must share one source_release;
(5) station_version must be consistent across an example's points, and city_day must
name the same local day as local_day.target_date, with duplicate detection keyed off
that bound (station_version, target_date) identity rather than the free-text label;
(6) validate_example resolves an explicit append-only label_history through
validate_label_lineage, requires FINAL status, bounds winner_bucket to a declared
partition size, and a LabelVersionRegistry refuses content changes under a reused
label_version across separate calls; (7) CaptureRegistry is actually invoked per
point and binds byte digest, index digest and decoded value together so a semantic
rewrite under an unchanged byte digest is refused, and LocalDay pins the on-disk
tzdata file it was computed from; (8) ExpectedCoverage is wrapped in a CoveragePolicy
naming the full required provider set (so an absent provider is reported, not
dropped) and validate_corpus requires one frozen policy identity across the corpus.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from itertools import combinations
from pathlib import Path
import math

from tools.v11_multimodel_panel import FAMILIES, PROVIDERS, digest, file_sha, is_sha, local_window, require

CONTRACT_ID = 'R09_NATIVE_2T_TRAJECTORY_V1'

# V1 supports exactly one prediction regime; no other regime is representable by this
# schema, so its day-boundary invariant is enforced unconditionally rather than gated
# behind a field that could be silently misused (docs/V11_R09_DATA_CONTRACT_ADJUDICATION.md:135).
PREDICTION_REGIME = 'FUTURE_FORECAST'

# Identities this contract must never be aliased to (docs/V11_R09_DATA_CONTRACT_ADJUDICATION.md:58-60).
FORBIDDEN_IDENTITY_ALIASES = frozenset({
    'EXACT_LOCAL_DAY_EXTREME',
    'PIECEWISE_LINEAR_POINT_TEMPERATURE_PATH',
    'NATIVE_POINT_PANEL_WITH_SAMPLED_EXTREMA_DIAGNOSTICS_ONLY',
})

SPLITS = ('TRAIN', 'DEVELOPMENT', 'CONFIRMATION')
LABEL_STATUS_VALUES = ('FINAL', 'PENDING', 'DISPUTED')

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
    timezone_file_sha256: str

    def __post_init__(self):
        start, end = local_window(self.target_date, self.timezone)
        require(start == self.start_utc and end == self.end_utc_exclusive, 'LOCAL_DAY_GEOMETRY_MISMATCH')
        require(self.timezone_file_sha256 == file_sha(Path('/usr/share/zoneinfo') / self.timezone),
                'LOCAL_DAY_TIMEZONE_FILE_HASH')


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
        run_dt = datetime.fromtimestamp(self.run_initialized_at.utc, dt_timezone.utc)
        require(run_dt.date().isoformat() == self.run_date and run_dt.hour == self.cycle
                and run_dt.minute == 0 and run_dt.second == 0 and run_dt.microsecond == 0,
                'RUN_DATE_CYCLE_MUST_MATCH_INITIALIZATION')
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


@dataclass(frozen=True)
class CoveragePolicy:
    """A frozen, preregistered expected-coverage protocol for one corpus. Names the
    complete required provider set so an absent provider is reported rather than
    silently missing from the record, and carries one policy_id validate_corpus can
    require to stay constant across every example so no single example can shrink
    its own expectation to match whatever bytes actually arrived."""
    policy_id: str
    required_providers: tuple
    expected: dict

    def __post_init__(self):
        require(is_sha(self.policy_id), 'COVERAGE_POLICY_ID')
        require(bool(self.required_providers) and
                tuple(sorted(set(self.required_providers))) == tuple(sorted(self.required_providers)),
                'REQUIRED_PROVIDERS_SET')
        require(set(self.expected) == set(self.required_providers), 'COVERAGE_POLICY_PROVIDER_SET')
        for p, ec in self.expected.items():
            require(isinstance(ec, ExpectedCoverage) and ec.provider == p, 'COVERAGE_POLICY_PROVIDER_KEY_MATCH')


def validate_coverage(points, coverage_policy):
    """Fail closed on any expected (member, hour) with no message. Points outside the
    expected set (finer native cadence, or points beyond the frozen policy window) are
    a legitimate geometry/cadence gap, not a missing message, and are reported but not
    gating. Every point must belong to the same provider and the same single selected
    run/release; a required provider absent from this example is reported explicitly
    rather than disappearing from the result."""
    require(bool(points), 'NO_POINTS')
    providers_present = {p.provider for p in points}
    require(len(providers_present) == 1, 'SINGLE_PROVIDER_PER_EXAMPLE')
    provider = next(iter(providers_present))
    require(provider in coverage_policy.required_providers, 'PROVIDER_NOT_IN_REQUIRED_SET')
    expected = coverage_policy.expected[provider]
    present = [(p.member, p.forecast_hour) for p in points]
    require(len(set(present)) == len(present), 'DUPLICATE_MEMBER_HOUR_POINT')
    present_set = set(present)
    required = {(m, h) for m in expected.expected_members for h in expected.expected_hours}
    missing = required - present_set
    require(not missing, 'MISSING_EXPECTED_MESSAGE:' + ','.join(f'{m}:{h}' for m, h in sorted(missing)))
    absent_providers = tuple(sorted(set(coverage_policy.required_providers) - {provider}))
    return dict(provider=provider, required=sorted(required), present_all=sorted(present_set),
                extra_native_points=sorted(present_set - required), absent_providers=absent_providers)


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


def validate_station_consistency(points):
    versions = {p.station_version for p in points}
    require(len(versions) == 1, 'STATION_DRIFT_WITHIN_EXAMPLE')
    return next(iter(versions))


def validate_city_day_identity(city_day, local_day):
    """city_day must name the same local day local_day itself was built for; it is
    never an arbitrary alias a caller can point at an unrelated day."""
    require(isinstance(city_day, str) and city_day.count('|') == 1, 'CITY_DAY_FORMAT')
    station_label, date_part = city_day.split('|')
    require(bool(station_label), 'CITY_DAY_STATION_LABEL')
    require(date_part == local_day.target_date, 'CITY_DAY_TARGET_DATE_MISMATCH')
    return station_label


@dataclass(frozen=True)
class LabelFact:
    label_version: str
    label_knowable_at: Timestamp
    family: str
    bucket_count: int
    winner_bucket: int
    status: str
    revision_of: str | None

    def __post_init__(self):
        require(is_sha(self.label_version), 'LABEL_VERSION_DIGEST')
        require(self.family in FAMILIES, 'LABEL_FAMILY_IDENTITY')
        require(type(self.bucket_count) is int and 2 <= self.bucket_count <= 128, 'LABEL_BUCKET_COUNT_RANGE')
        require(type(self.winner_bucket) is int and 0 <= self.winner_bucket < self.bucket_count,
                'LABEL_WINNER_BUCKET_RANGE')
        require(self.status in LABEL_STATUS_VALUES, 'LABEL_STATUS_ENUM')
        require_trusted(self.label_knowable_at, 'label_knowable_at')
        require(self.label_knowable_at.origin not in ('CATALOG_CREATED_AT', 'WEATHER_DAY_END_HEURISTIC'),
                'LABEL_KNOWABLE_AT_MUST_NOT_BE_CATALOG_OR_DAY_END')
        require(self.revision_of is None or is_sha(self.revision_of), 'LABEL_REVISION_LINK')
        require(self.revision_of != self.label_version, 'LABEL_SELF_REVISION')


def validate_label_lineage(facts):
    """Ordered, append-only correction chain. A correction is a new label_version
    linked to the prior one, never a rewrite of an existing version. Returns the
    current (most recent) fact; callers gate admission on its status themselves."""
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


class LabelVersionRegistry:
    """Immutable content-bound label-version identity across a corpus's lifetime.
    Reusing a label_version with different content -- even across separate
    validate_example calls that never see each other's arguments -- is refused;
    identical replay is a no-op."""

    def __init__(self):
        self._store = {}

    def register(self, fact):
        content = digest(dict(family=fact.family, bucket_count=fact.bucket_count,
                               winner_bucket=fact.winner_bucket, status=fact.status,
                               revision_of=fact.revision_of, label_knowable_at_utc=fact.label_knowable_at.utc))
        if fact.label_version in self._store:
            require(self._store[fact.label_version] == content,
                    'LABEL_VERSION_CONTENT_REWRITE:' + fact.label_version)
            return False
        self._store[fact.label_version] = content
        return True


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


def split_cutoffs_sha256(cutoffs):
    return digest({s: dict(utc=cutoffs[s].utc, origin=cutoffs[s].origin,
                            uncertainty_seconds=cutoffs[s].uncertainty_seconds,
                            clock_health=cutoffs[s].clock_health) for s in SPLITS})


class CaptureRegistry:
    """Immutable content-addressed capture identity. Replaying an identical byte
    digest, index digest and decoded value for an already-registered coordinate is a
    no-op; any conflicting overwrite -- including a semantic rewrite that keeps the
    byte digest but changes the index digest or decoded value -- is refused."""

    def __init__(self):
        self._store = {}

    def register(self, key, capture, value_native_k):
        require(is_sha(capture.byte_sha256) and is_sha(capture.index_sha256), 'CAPTURE_DIGEST')
        content = digest(dict(byte_sha256=capture.byte_sha256, index_sha256=capture.index_sha256,
                               value_native_k=value_native_k))
        if key in self._store:
            require(self._store[key] == content, 'IMMUTABLE_CAPTURE_CONFLICT:' + repr(key))
            return False
        self._store[key] = content
        return True


@dataclass(frozen=True)
class ValidatedExample:
    """Immutable record produced only by validate_example. validate_corpus accepts
    only instances of this type, closing the trust boundary a mutable plain dict
    would otherwise leave open at the public corpus-validation API."""
    city_day: str
    dedup_key: tuple
    split: str
    coverage: dict
    rule_version: str
    station_version: str
    label_version: str
    family: str
    winner_bucket: int
    bucket_count: int
    points: int
    in_day_observations: int
    out_of_day_points: int
    decision_at: float
    prediction_frozen_at: float
    label_knowable_upper_bound: float
    split_cutoffs_sha256: str
    coverage_policy_id: str
    evidence_class: str
    learner_admitted: bool
    financial_authority: bool
    promotion_authority: bool
    host_approved: bool
    order_authority: bool


def validate_example(*, city_day, split, points, coverage_policy, local_day, clocks, label_history,
                      split_cutoffs, capture_registry, label_registry, evidence_class='SYNTHETIC'):
    """Validate one city-day trajectory/capture example end to end. Fails closed on
    any incomplete coverage, untrusted or missing clock, out-of-order causal
    dependency, or non-FINAL label. Never admits a non-SYNTHETIC example: no real
    adapter exists yet."""
    require(evidence_class == 'SYNTHETIC', 'REAL_ADAPTER_NOT_IMPLEMENTED')
    require(split in SPLITS, 'SPLIT_IDENTITY')
    station_label = validate_city_day_identity(city_day, local_day)
    require(len({p.source_release for p in points}) == 1, 'MULTIPLE_SOURCE_RELEASES_IN_EXAMPLE')
    validate_split_cutoffs(split_cutoffs)
    coverage = validate_coverage(points, coverage_policy)
    rule_version = validate_rule_consistency(points)
    station_version = validate_station_consistency(points)
    annotated = annotate_relative_time(points, local_day)
    for p in points:
        # Every upstream receipt dependency's conservative bound -- not only
        # feature_ready_at's own uncertainty -- must clear the decision cutoff. A
        # fresh, apparently precise feature timestamp must not erase an earlier
        # dependency's uncertainty (gate-2 review F1).
        require(p.response_completed_at.conservative_upper_bound <= clocks.decision_at.utc,
                'FEATURE_NOT_AVAILABLE_BY_DECISION')
        require(p.feature_ready_at.conservative_upper_bound <= clocks.decision_at.utc,
                'FEATURE_NOT_AVAILABLE_BY_DECISION')
        capture_registry.register((p.provider, p.run_date, p.cycle, p.member, p.forecast_hour),
                                   p.capture, p.value_native_k)
    for fact in label_history:
        label_registry.register(fact)
    current_label = validate_label_lineage(label_history)
    require(current_label.status == 'FINAL', 'LABEL_NOT_FINAL_FOR_ADMISSION')
    require(clocks.prediction_frozen_at.conservative_upper_bound <= current_label.label_knowable_at.utc,
            'LABEL_KNOWABLE_BEFORE_FROZEN')
    # V1 is FUTURE_FORECAST-only: decision and frozen prediction must precede
    # local-day start (gate-2 review F2).
    require(clocks.prediction_frozen_at.utc <= local_day.start_utc,
            'PREDICTION_NOT_FROZEN_BEFORE_LOCAL_DAY_START')
    cutoff = split_cutoffs[split]
    require_trusted(cutoff, CUTOFF_ROLE[split])
    if split == 'TRAIN':
        require(current_label.label_knowable_at.conservative_upper_bound <= cutoff.utc,
                'TRAIN_LABEL_NOT_KNOWABLE_BY_FIT_CUTOFF')
    elif split == 'DEVELOPMENT':
        require(current_label.label_knowable_at.conservative_upper_bound <= cutoff.utc,
                'DEVELOPMENT_LABEL_NOT_KNOWABLE_BY_SELECTION_FREEZE')
        require(clocks.prediction_frozen_at.utc >= split_cutoffs['TRAIN'].conservative_upper_bound,
                'DEVELOPMENT_PREDICTION_BEFORE_FIT_CUTOFF')
    else:
        require(clocks.prediction_frozen_at.utc <= cutoff.utc,
                'CONFIRMATION_NOT_FROZEN_BEFORE_EVALUATION_ASOF')
        require(current_label.label_knowable_at.conservative_upper_bound <= cutoff.utc,
                'CONFIRMATION_LABEL_NOT_KNOWABLE_BY_EVALUATION_ASOF')
        require(clocks.prediction_frozen_at.utc >= split_cutoffs['DEVELOPMENT'].conservative_upper_bound,
                'CONFIRMATION_PREDICTION_BEFORE_SELECTION_FREEZE')
    return ValidatedExample(
        city_day=city_day, dedup_key=(station_version, local_day.target_date), split=split, coverage=coverage,
        rule_version=rule_version, station_version=station_version, label_version=current_label.label_version,
        family=current_label.family, winner_bucket=current_label.winner_bucket,
        bucket_count=current_label.bucket_count, points=len(points),
        in_day_observations=sum(1 for a in annotated if a['in_day_observation']),
        out_of_day_points=sum(1 for a in annotated if not a['in_day_observation']),
        decision_at=clocks.decision_at.utc, prediction_frozen_at=clocks.prediction_frozen_at.utc,
        label_knowable_upper_bound=current_label.label_knowable_at.conservative_upper_bound,
        split_cutoffs_sha256=split_cutoffs_sha256(split_cutoffs), coverage_policy_id=coverage_policy.policy_id,
        learner_admitted=False, evidence_class=evidence_class,
        financial_authority=False, promotion_authority=False,
        host_approved=False, order_authority=False)


def validate_no_duplicate_city_days(records):
    seen = set()
    for r in records:
        require(r.dedup_key not in seen, 'DUPLICATE_CITY_DAY:' + '|'.join(r.dedup_key))
        seen.add(r.dedup_key)


def _embargo_field(record, name):
    return getattr(record, name) if hasattr(record, name) else record[name]


def validate_cross_split_embargo(records):
    """Frozen trained parameters must precede each held-out prediction they claim to
    simulate; label delay overlapping a later split must be embargoed. Checked over
    every earlier/later split pair, not only splits adjacent in SPLITS order, so an
    absent middle split cannot hide leakage between the splits that remain."""
    by_split = defaultdict(list)
    for r in records:
        by_split[_embargo_field(r, 'split')].append(r)
    for before, after in combinations(SPLITS, 2):
        if by_split[before] and by_split[after]:
            require(max(_embargo_field(r, 'label_knowable_upper_bound') for r in by_split[before]) <=
                    min(_embargo_field(r, 'decision_at') for r in by_split[after]), 'CROSS_SPLIT_LABEL_EMBARGO_LEAKAGE')


def validate_corpus(records, split_cutoffs):
    require(all(isinstance(r, ValidatedExample) for r in records), 'CORPUS_RECORDS_MUST_BE_VALIDATED_EXAMPLES')
    validate_split_cutoffs(split_cutoffs)
    expected_sha = split_cutoffs_sha256(split_cutoffs)
    require(all(r.split_cutoffs_sha256 == expected_sha for r in records), 'DETACHED_CORPUS_CUTOFFS')
    require(len({r.coverage_policy_id for r in records}) <= 1, 'CORPUS_COVERAGE_POLICY_MUST_BE_FROZEN')
    validate_no_duplicate_city_days(records)
    validate_cross_split_embargo(records)
    return dict(city_days=len(records), splits={s: sum(1 for r in records if r.split == s) for s in SPLITS},
                admitted_real_examples=0, historical_541_day_admissions=0)
