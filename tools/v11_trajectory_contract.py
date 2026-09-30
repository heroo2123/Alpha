"""Offline synthetic R09 native 2m-temperature trajectory schema and admission checks.

This module is separate from the exact-local-day panel and grouped stacking
interfaces. It acquires no provider data, fits no model, and grants no real,
financial, production, or promotion authority. The validator checks complete
source facts again at the corpus boundary with independently injected synthetic
bytes. A passing synthetic case establishes schema behavior only; real capture,
source attestation, fitter, and forward acceptance remain later gates.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from itertools import combinations
from pathlib import Path
from types import MappingProxyType
from zoneinfo import ZoneInfo
import hashlib
import math

from tools.v11_multimodel_panel import FAMILIES, PROVIDERS, canonical, digest, file_sha, is_sha, require, strict_json

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
UNIT_VALUES = ('CELSIUS',)
ROUNDING_VALUES = ('NEAREST_INTEGER',)

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
    'artifact_available_at': frozenset({'LOCAL_RECEIPT_WALL_CLOCK'}),
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
    evidence_sha256: str | None = None

    def __post_init__(self):
        require(math.isfinite(self.utc) and self.utc > 0, 'TIMESTAMP_UTC_REQUIRED')
        require(self.origin in ALL_ORIGINS, 'TIMESTAMP_ORIGIN_UNKNOWN')
        require(math.isfinite(self.uncertainty_seconds) and self.uncertainty_seconds >= 0, 'TIMESTAMP_UNCERTAINTY')
        require(self.clock_health in CLOCK_HEALTH_VALUES, 'TIMESTAMP_CLOCK_HEALTH_ENUM')
        require(self.evidence_sha256 is None or is_sha(self.evidence_sha256), 'TIMESTAMP_EVIDENCE_DIGEST')

    @property
    def conservative_upper_bound(self):
        return self.utc + self.uncertainty_seconds

    @property
    def conservative_lower_bound(self):
        return self.utc - self.uncertainty_seconds


def require_trusted(ts, role):
    require(ts.origin in ALLOWED_ORIGINS[role], role.upper() + '_ORIGIN_UNTRUSTED')
    require(ts.clock_health == 'SYNCED', role.upper() + '_CLOCK_HEALTH_NOT_SYNCED')
    require(is_sha(ts.evidence_sha256), role.upper() + '_EVIDENCE_REQUIRED')


def clock_payload(ts, subject):
    return canonical(dict(utc=ts.utc, origin=ts.origin,
                          uncertainty_seconds=ts.uncertainty_seconds,
                          clock_health=ts.clock_health, subject=subject))


def station_metadata_subject(station_id, event_id, station_version, metadata_sha256):
    return digest(dict(station_id=station_id, event_id=event_id,
                       station_version=station_version,
                       metadata_sha256=metadata_sha256))


def rule_metadata_subject(rule_id, rule_version, family, unit, rounding, bucket_edges):
    return digest(dict(rule_id=rule_id, rule_version=rule_version, family=family,
                       unit=unit, rounding=rounding, bucket_edges=list(bucket_edges)))


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
class CaptureEvidence:
    """Synthetic response and clock evidence, including unadmitted partial attempts.
    The verifier supplies sealed evidence
    independently at admission; these assertions alone do not attest a real host."""
    request_url: str
    range_start: int
    range_end: int
    resource_size: int
    request_started_at: Timestamp
    response_completed_at: Timestamp
    http_status: int
    content_range: str
    response_headers: tuple
    raw_bytes: bytes
    index_bytes: bytes
    clock_evidence_bytes: bytes
    sealed: bool
    state: str = 'SEALED'

    def __post_init__(self):
        require(self.request_url.startswith('https://') and '@' not in self.request_url,
                'CAPTURE_REQUEST_URL')
        require(type(self.range_start) is int and type(self.range_end) is int and
                0 <= self.range_start <= self.range_end, 'CAPTURE_REQUEST_RANGE')
        require(type(self.resource_size) is int and self.resource_size > self.range_end,
                'CAPTURE_RESOURCE_SIZE')
        require(self.state in ('SEALED', 'PARTIAL', 'FAILED') and
                self.sealed is (self.state == 'SEALED'), 'CAPTURE_STATE_SEAL_CONSISTENCY')
        require(type(self.raw_bytes) is bytes and
                (self.state != 'SEALED' or len(self.raw_bytes) ==
                 self.range_end - self.range_start + 1), 'CAPTURE_RANGE_BYTE_COUNT')
        require(type(self.index_bytes) is bytes and
                (self.state != 'SEALED' or bool(self.index_bytes)), 'CAPTURE_INDEX_BYTES')
        require(type(self.clock_evidence_bytes) is bytes and bool(self.clock_evidence_bytes),
                'CAPTURE_CLOCK_EVIDENCE_BYTES')
        require(type(self.http_status) is int and
                (self.state != 'SEALED' or self.http_status == 206), 'CAPTURE_HTTP_STATUS')
        require(self.state != 'SEALED' or self.content_range ==
                f'bytes {self.range_start}-{self.range_end}/{self.resource_size}',
                'CAPTURE_CONTENT_RANGE')
        require(type(self.response_headers) is tuple and
                all(type(h) is tuple and len(h) == 2 and all(type(v) is str for v in h)
                    for h in self.response_headers) and
                (self.state != 'SEALED' or (
                    ('content-range', self.content_range) in self.response_headers and
                    ('content-length', str(len(self.raw_bytes))) in self.response_headers and
                    any(name == 'etag' and bool(value) for name, value in self.response_headers))) and
                len(set(self.response_headers)) == len(self.response_headers), 'CAPTURE_RESPONSE_HEADERS')
        require_trusted(self.request_started_at, 'response_completed_at')
        require_trusted(self.response_completed_at, 'response_completed_at')
        require(self.request_started_at.conservative_upper_bound <=
                self.response_completed_at.conservative_lower_bound,
                'CAPTURE_REQUEST_AFTER_RESPONSE')
        require(self.response_completed_at.evidence_sha256 ==
                hashlib.sha256(self.clock_evidence_bytes).hexdigest(), 'CAPTURE_CLOCK_EVIDENCE_MISMATCH')
        require(self.clock_evidence_bytes == clock_payload(self.response_completed_at,
                hashlib.sha256(self.raw_bytes).hexdigest()), 'CAPTURE_CLOCK_CONTENT_MISMATCH')

    @property
    def manifest_bytes(self):
        return canonical(dict(request_url=self.request_url, range_start=self.range_start,
            range_end=self.range_end, resource_size=self.resource_size,
            request_started_at=asdict(self.request_started_at),
            response_completed_at=asdict(self.response_completed_at), http_status=self.http_status,
            content_range=self.content_range, response_headers=list(self.response_headers),
            raw_sha256=hashlib.sha256(self.raw_bytes).hexdigest(),
            index_sha256=hashlib.sha256(self.index_bytes).hexdigest(),
            clock_evidence_sha256=hashlib.sha256(self.clock_evidence_bytes).hexdigest(),
            sealed=self.sealed, state=self.state))


@dataclass(frozen=True)
class ExtractionEvidence:
    raw_sha256: str
    index_sha256: str
    provider: str
    source_release: str
    run_date: str
    cycle: int
    member: int
    forecast_hour: int
    valid_utc: float
    parameter: str
    unit: str
    response_range_start: int
    response_range_end: int
    message_offset: int
    message_length: int
    station_id: str
    station_version: str
    grid: GridExtraction
    value_native_k: float
    decoder_sha256: str
    extraction_sha256: str
    code_sha256: str
    policy_sha256: str
    dependency_sha256: str

    def __post_init__(self):
        require(isinstance(self.station_id, str) and bool(self.station_id.strip()),
                'EXTRACTION_STATION_ID')
        require(all(is_sha(x) for x in (self.raw_sha256, self.index_sha256,
                self.station_version, self.decoder_sha256, self.extraction_sha256,
                self.code_sha256, self.policy_sha256, self.dependency_sha256)),
                'EXTRACTION_IDENTITY_DIGESTS')
        require(self.provider in PROVIDERS and self.source_release ==
                f'{self.provider}:{self.run_date}:{self.cycle:02d}Z' and
                type(self.member) is int and 0 <= self.member < PROVIDERS[self.provider] and
                type(self.forecast_hour) is int and self.forecast_hour >= 0,
                'EXTRACTION_MESSAGE_COORDINATES')
        require(self.parameter == '2t' and self.unit == 'K', 'EXTRACTION_PARAMETER_UNIT')
        require(type(self.response_range_start) is int and type(self.response_range_end) is int and
                type(self.message_offset) is int and type(self.message_length) is int and
                self.response_range_start >= 0 and self.response_range_end >= self.response_range_start and
                self.message_offset >= 0 and self.message_length > 0 and
                self.message_offset + self.message_length <=
                self.response_range_end - self.response_range_start + 1,
                'EXTRACTION_MESSAGE_LOCATION')
        require(isinstance(self.grid, GridExtraction), 'EXTRACTION_GRID_REQUIRED')
        require(math.isfinite(self.value_native_k), 'EXTRACTION_VALUE')


@dataclass(frozen=True)
class CaptureRef:
    byte_sha256: str
    index_sha256: str
    store: str
    immutable: bool
    evidence: CaptureEvidence | None = None

    def __post_init__(self):
        require(is_sha(self.byte_sha256) and is_sha(self.index_sha256), 'CAPTURE_DIGESTS')
        require(self.immutable is True, 'CAPTURE_MUST_BE_IMMUTABLE')
        require(isinstance(self.store, str) and 'private' in self.store and
                self.byte_sha256 in self.store, 'CAPTURE_STORE_MUST_BE_PRIVATE_CONTENT_ADDRESSED')


@dataclass(frozen=True)
class LocalDay:
    target_date: str
    timezone: str
    start_utc: float
    end_utc_exclusive: float
    timezone_file_sha256: str

    def __post_init__(self):
        start, end = local_day_window(self.target_date, self.timezone)
        require(start == self.start_utc and end == self.end_utc_exclusive, 'LOCAL_DAY_GEOMETRY_MISMATCH')
        require(self.timezone_file_sha256 == file_sha(_timezone_path(self.timezone)),
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
    extraction: ExtractionEvidence | None
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
        require(isinstance(self.extraction, ExtractionEvidence), 'EXTRACTION_EVIDENCE_REQUIRED')
        require(self.extraction.raw_sha256 == self.capture.byte_sha256 and
                self.extraction.index_sha256 == self.capture.index_sha256 and
                self.extraction.provider == self.provider and
                self.extraction.source_release == self.source_release and
                self.extraction.run_date == self.run_date and
                self.extraction.cycle == self.cycle and
                self.extraction.member == self.member and
                self.extraction.forecast_hour == self.forecast_hour and
                self.extraction.valid_utc == self.valid_at.utc and
                self.extraction.response_range_start == self.capture.evidence.range_start and
                self.extraction.response_range_end == self.capture.evidence.range_end and
                self.extraction.station_version == self.station_version and
                self.extraction.grid == self.grid and
                self.extraction.value_native_k == self.value_native_k,
                'EXTRACTION_POINT_MISMATCH')
        require(self.extraction.extraction_sha256 == extraction_digest(self.extraction),
                'EXTRACTION_CONTENT_MISMATCH')
        if self.source_published_at is not None:
            require_trusted(self.source_published_at, 'source_published_at')
        require(self.release_binding != 'ATTESTED' or self.source_published_at is not None,
                'ATTESTED_RELEASE_REQUIRES_PUBLICATION_EVIDENCE')


@dataclass(frozen=True)
class ExpectedCoverage:
    provider: str
    expected_members: tuple
    expected_hours: tuple
    native_cadence_hours: int = 12
    native_window_hours: tuple = (12, 24)

    def __post_init__(self):
        require(self.provider in PROVIDERS, 'PROVIDER_IDENTITY')
        require(type(self.expected_members) is tuple and
                self.expected_members == tuple(range(PROVIDERS[self.provider])),
                'EXPECTED_MEMBER_SET_COMPLETE')
        require(type(self.expected_hours) is tuple and bool(self.expected_hours) and
                tuple(sorted(set(self.expected_hours))) == self.expected_hours,
                'EXPECTED_HOUR_SET_ORDERED_UNIQUE')
        require(all(type(h) is int and h >= 0 for h in self.expected_hours), 'EXPECTED_HOUR_RANGE')
        require(type(self.native_cadence_hours) is int and self.native_cadence_hours > 0 and
                type(self.native_window_hours) is tuple and len(self.native_window_hours) == 2 and
                all(type(h) is int for h in self.native_window_hours) and
                0 <= self.native_window_hours[0] <= self.native_window_hours[1],
                'NATIVE_WINDOW_CADENCE_REQUIRED')
        require(self.expected_hours == tuple(range(self.native_window_hours[0],
                self.native_window_hours[1] + 1, self.native_cadence_hours)),
                'COVERAGE_NATIVE_CADENCE_WINDOW_MISMATCH')


@dataclass(frozen=True)
class OutageEvidence:
    provider: str
    observed_at: Timestamp
    evidence_sha256: str

    def __post_init__(self):
        require(self.provider in PROVIDERS, 'OUTAGE_PROVIDER')
        require_trusted(self.observed_at, 'response_completed_at')
        require(is_sha(self.evidence_sha256), 'OUTAGE_EVIDENCE_DIGEST')


@dataclass(frozen=True)
class RunCandidate:
    provider: str
    run_initialized_at: Timestamp
    ready_at: Timestamp
    complete: bool
    status: str = 'READY'

    def __post_init__(self):
        require(self.provider in PROVIDERS and self.run_initialized_at.origin == DECLARED_ORIGIN,
                'RUN_CANDIDATE_IDENTITY')
        require_trusted(self.ready_at, 'feature_ready_at')
        require(type(self.complete) is bool, 'RUN_CANDIDATE_COMPLETENESS')
        require(self.status in ('READY', 'INCOMPLETE', 'UNAVAILABLE') and
                (self.status == 'READY') == self.complete,
                'RUN_CANDIDATE_STATUS_CONSISTENCY')
        require(self.ready_at.utc > self.run_initialized_at.utc,
                'RUN_CANDIDATE_STATUS_BEFORE_INITIALIZATION')

    @property
    def subject(self):
        return digest(dict(provider=self.provider, run_initialized_at=self.run_initialized_at.utc,
                           complete=self.complete, status=self.status))


def run_candidate_slots(policy, decision_lower):
    """The finite UTC cycle inventory implied by the frozen lookback protocol."""
    first_day = datetime.fromtimestamp(decision_lower - policy.max_run_age_seconds,
                                       dt_timezone.utc).date()
    last_day = datetime.fromtimestamp(decision_lower, dt_timezone.utc).date()
    slots = set()
    day = first_day
    while day <= last_day:
        for hour in policy.allowed_cycles:
            stamp = datetime.combine(day, time(hour), dt_timezone.utc).timestamp()
            if decision_lower - policy.max_run_age_seconds <= stamp <= decision_lower:
                slots.add(stamp)
        day += timedelta(days=1)
    return tuple(sorted(slots))


def run_inventory_bytes(policy, decision_at, candidates, providers):
    return canonical(dict(protocol_id=policy.policy_id, decision_at=asdict(decision_at),
                          providers=sorted(providers),
                          candidates=[asdict(c) for c in sorted(candidates,
                              key=lambda c: (c.provider, c.run_initialized_at.utc))]))


@dataclass(frozen=True)
class CoveragePolicy:
    """A frozen, preregistered expected-coverage protocol for one corpus. Names the
    complete required provider set so an absent provider is reported rather than
    silently missing from the record. policy_id is a computed digest of this policy's
    own content, never a caller-supplied string, so a same-ID content mutation is
    impossible by construction; expected is frozen (MappingProxyType) so no example
    can mutate the shared expectation in place after construction (gate-2 review R7)."""
    required_providers: tuple
    expected: object
    preregistered_at: Timestamp
    requested_cohort: tuple
    decision_schedule: tuple
    max_run_age_seconds: int
    run_selection: str
    allowed_cycles: tuple
    fallback_mode: str

    def __post_init__(self):
        require(type(self.required_providers) is tuple and bool(self.required_providers) and
                tuple(sorted(set(self.required_providers))) == tuple(sorted(self.required_providers)),
                'REQUIRED_PROVIDERS_SET')
        expected = dict(self.expected)
        require(set(expected) == set(self.required_providers), 'COVERAGE_POLICY_PROVIDER_SET')
        for p, ec in expected.items():
            require(isinstance(ec, ExpectedCoverage) and ec.provider == p, 'COVERAGE_POLICY_PROVIDER_KEY_MATCH')
        require_trusted(self.preregistered_at, 'decision_at')
        require(type(self.requested_cohort) is tuple and bool(self.requested_cohort) and
                all(type(key) is tuple and len(key) == 5 and
                    all(type(v) is str and bool(v) for v in key) and key[4] in FAMILIES
                    for key in self.requested_cohort) and
                len(set(self.requested_cohort)) == len(self.requested_cohort),
                'REQUESTED_COHORT_REQUIRED')
        requested_trials = {(key[0], key[1], key[3]) for key in self.requested_cohort}
        require(type(self.decision_schedule) is tuple and
                all(type(item) is tuple and len(item) == 2 and
                    type(item[0]) is tuple and len(item[0]) == 3 and
                    isinstance(item[1], Timestamp) for item in self.decision_schedule) and
                len({item[0] for item in self.decision_schedule}) == len(self.decision_schedule) and
                {item[0] for item in self.decision_schedule} == requested_trials,
                'DECISION_SCHEDULE_COHORT_MISMATCH')
        for _, timestamp in self.decision_schedule:
            require_trusted(timestamp, 'decision_at')
            require(self.preregistered_at.conservative_upper_bound <=
                    timestamp.conservative_lower_bound,
                    'PROTOCOL_NOT_PREREGISTERED_BY_DECISION')
        require(type(self.max_run_age_seconds) is int and self.max_run_age_seconds > 0,
                'MAX_RUN_AGE_REQUIRED')
        require(self.run_selection == 'LATEST_COMPLETE_READY', 'RUN_SELECTION_POLICY_REQUIRED')
        require(type(self.allowed_cycles) is tuple and bool(self.allowed_cycles) and
                set(self.allowed_cycles) <= {0, 6, 12, 18}, 'RUN_SELECTION_CYCLES')
        require(self.fallback_mode in ('NONE', 'FROZEN_OUTAGE'), 'FALLBACK_MODE')
        object.__setattr__(self, 'expected', MappingProxyType(expected))

    @property
    def protocol_subject(self):
        return digest(dict(
            required_providers=list(self.required_providers),
            expected={p: dict(provider=ec.provider, expected_members=list(ec.expected_members),
                               expected_hours=list(ec.expected_hours),
                               native_cadence_hours=ec.native_cadence_hours,
                               native_window_hours=list(ec.native_window_hours))
                      for p, ec in self.expected.items()},
            requested_cohort=list(self.requested_cohort),
            decision_schedule=[dict(trial_key=list(key), decision_at=asdict(ts))
                               for key, ts in self.decision_schedule],
            max_run_age_seconds=self.max_run_age_seconds, run_selection=self.run_selection,
            allowed_cycles=list(self.allowed_cycles),
            fallback_mode=self.fallback_mode))

    @property
    def policy_id(self):
        return digest(dict(protocol_subject=self.protocol_subject,
                           preregistered_at=asdict(self.preregistered_at)))


@dataclass(frozen=True)
class CoverageResult:
    provider: str
    required: tuple
    present_all: tuple
    extra_native_points: tuple
    absent_providers: tuple


def validate_coverage(points, coverage_policy):
    """Fail closed on any expected (member, hour) with no message. Points outside the
    expected native window may be retained as out-of-window forecast context.
    In-window unregistered hours fail. Required providers missing from this example
    are reported for the frozen outage policy to gate at admission."""
    require(bool(points), 'NO_POINTS')
    providers_present = {p.provider for p in points}
    require(providers_present <= set(coverage_policy.required_providers), 'PROVIDER_NOT_IN_REQUIRED_SET')
    present = [(p.provider, p.member, p.forecast_hour) for p in points]
    require(len(set(present)) == len(present), 'DUPLICATE_MEMBER_HOUR_POINT')
    present_set = set(present)
    required = {(provider, m, h) for provider in providers_present
                for m in coverage_policy.expected[provider].expected_members
                for h in coverage_policy.expected[provider].expected_hours}
    missing = required - present_set
    require(not missing, 'MISSING_EXPECTED_MESSAGE:' + repr(sorted(missing)))
    extra = present_set - required
    require(all(h < coverage_policy.expected[p].native_window_hours[0] or
                h > coverage_policy.expected[p].native_window_hours[1] for p, _, h in extra),
            'UNREGISTERED_NATIVE_HOUR')
    absent_providers = tuple(sorted(set(coverage_policy.required_providers) - providers_present))
    return CoverageResult(provider=next(iter(providers_present)) if len(providers_present) == 1 else 'MULTI',
                           required=tuple(sorted(required)), present_all=tuple(sorted(present_set)),
                           extra_native_points=tuple(sorted(extra)),
                           absent_providers=absent_providers)


def local_day_window(target_date, timezone_name):
    day = date.fromisoformat(target_date)
    require(day.isoformat() == target_date, 'LOCAL_DATE')
    with _timezone_path(timezone_name).open('rb') as stream:
        timezone = ZoneInfo.from_file(stream, key=timezone_name)
    start = datetime.combine(day, time(), timezone).timestamp()
    end = datetime.combine(day + timedelta(days=1), time(), timezone).timestamp()
    require(0 < end - start <= 26 * 3600, 'LOCAL_DAY_DURATION')
    return start, end


def _timezone_path(timezone_name):
    require(isinstance(timezone_name, str) and bool(timezone_name) and
            not timezone_name.startswith('/'), 'TIMEZONE_NAME')
    root = Path('/usr/share/zoneinfo').resolve()
    path = (root / timezone_name).resolve()
    require(path.is_relative_to(root) and path.is_file(), 'TIMEZONE_FILE_REQUIRED')
    return path


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
    never an arbitrary alias a caller can point at an unrelated day. This is a display
    label only: canonical grouping/dedup is by SettlementTarget.key, not by this text
    (gate-2 review R3)."""
    require(isinstance(city_day, str) and city_day.count('|') == 1, 'CITY_DAY_FORMAT')
    station_label, date_part = city_day.split('|')
    require(bool(station_label), 'CITY_DAY_STATION_LABEL')
    require(date_part == local_day.target_date, 'CITY_DAY_TARGET_DATE_MISMATCH')
    return station_label


@dataclass(frozen=True)
class SettlementTarget:
    """Canonical, stable settlement-target identity: WHICH station/event/day/rule/
    partition/unit/rounding a label and its trajectory claim to settle. station_id
    and rule_id are stable canonical identities, distinct from station_version and
    rule_version, which are metadata REVISIONS of this same target and must never by
    themselves mint an independent target or grouping (gate-2 review R3)."""
    station_id: str
    event_id: str
    rule_id: str
    station_version: str
    rule_version: str
    target_date: str
    family: str
    bucket_count: int
    unit: str
    rounding: str
    bucket_edges: tuple
    settlement_timezone: str
    station_metadata_sha256: str
    station_metadata_available_at: Timestamp
    rule_metadata_available_at: Timestamp

    def __post_init__(self):
        require(isinstance(self.station_id, str) and bool(self.station_id.strip()), 'SETTLEMENT_STATION_ID')
        require(isinstance(self.event_id, str) and bool(self.event_id.strip()), 'SETTLEMENT_EVENT_ID')
        require(isinstance(self.rule_id, str) and bool(self.rule_id.strip()), 'SETTLEMENT_RULE_ID')
        require(is_sha(self.station_version) and is_sha(self.rule_version), 'SETTLEMENT_METADATA_VERSION_DIGESTS')
        require(self.family in FAMILIES, 'SETTLEMENT_FAMILY_IDENTITY')
        require(type(self.bucket_count) is int and 2 <= self.bucket_count <= 128, 'SETTLEMENT_BUCKET_COUNT_RANGE')
        require(self.unit in UNIT_VALUES, 'SETTLEMENT_UNIT_ENUM')
        require(self.rounding in ROUNDING_VALUES, 'SETTLEMENT_ROUNDING_ENUM')
        require(type(self.bucket_edges) is tuple and len(self.bucket_edges) == self.bucket_count + 1 and
                all(type(x) in (int, float) and math.isfinite(x) for x in self.bucket_edges) and
                all(a < b for a, b in zip(self.bucket_edges, self.bucket_edges[1:])),
                'SETTLEMENT_BUCKET_PARTITION')
        require_trusted(self.station_metadata_available_at, 'feature_ready_at')
        require_trusted(self.rule_metadata_available_at, 'feature_ready_at')
        require(is_sha(self.station_metadata_sha256), 'STATION_METADATA_MANIFEST_DIGEST')
        _timezone_path(self.settlement_timezone)

    @property
    def key(self):
        """Canonical grouping/dedup identity: stable station+rule+day+family, never
        the metadata revision hashes and never the free-text city_day label."""
        return (self.station_id, self.event_id, self.rule_id, self.target_date, self.family)


@dataclass(frozen=True)
class LabelFact:
    label_version: str
    label_knowable_at: Timestamp
    family: str
    bucket_count: int
    winner_bucket: int
    status: str
    revision_of: str | None
    target: SettlementTarget
    source_revision_id: str = ''

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
        require(isinstance(self.target, SettlementTarget), 'LABEL_TARGET_TYPE')
        require(self.target.family == self.family, 'LABEL_TARGET_FAMILY_MISMATCH')
        require(self.target.bucket_count == self.bucket_count, 'LABEL_TARGET_BUCKET_COUNT_MISMATCH')
        require(isinstance(self.source_revision_id, str) and bool(self.source_revision_id),
                'LABEL_SOURCE_REVISION_ID')


def station_metadata_bytes(target):
    return canonical(dict(station_id=target.station_id, event_id=target.event_id,
                          station_version=target.station_version,
                          settlement_timezone=target.settlement_timezone))


def label_payload_bytes(fact):
    # The version is the hash of this payload. The clock refers to the version,
    # so neither the version nor its clock evidence enters this preimage.
    return canonical(dict(source_revision_id=fact.source_revision_id,
                          family=fact.family, bucket_count=fact.bucket_count,
                          winner_bucket=fact.winner_bucket, status=fact.status,
                          revision_of=fact.revision_of, target=asdict(fact.target)))


def label_version_for(*, source_revision_id, family, bucket_count, winner_bucket,
                      status, revision_of, target):
    return hashlib.sha256(canonical(dict(source_revision_id=source_revision_id,
        family=family, bucket_count=bucket_count, winner_bucket=winner_bucket,
        status=status, revision_of=revision_of, target=asdict(target)))).hexdigest()


def validate_label_lineage(facts):
    """Ordered, append-only correction chain. A correction is a new label_version
    linked to the prior one, never a rewrite of an existing version. The settlement
    target (station/rule/day/family/partition) must stay stable across every
    correction: a version bump must never silently change WHAT is being settled
    (gate-2 review R4). Returns the current (most recent) fact; callers gate
    admission on its status themselves."""
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
            require(fact.target == prev.target, 'LABEL_LINEAGE_TARGET_DRIFT')
        prev = fact
    return prev


def _label_fact_seal(fact):
    return digest(dict(
        family=fact.family, bucket_count=fact.bucket_count, winner_bucket=fact.winner_bucket,
        status=fact.status, revision_of=fact.revision_of,
        label_knowable_at=dict(utc=fact.label_knowable_at.utc, origin=fact.label_knowable_at.origin,
                                uncertainty_seconds=fact.label_knowable_at.uncertainty_seconds,
                                clock_health=fact.label_knowable_at.clock_health),
        target=asdict(fact.target)))


class LabelVersionRegistry:
    """Immutable content-bound label-version identity. Reusing a label_version with
    different content -- even across separate validate_example calls that never see
    each other's arguments -- is refused; identical replay is a no-op. register_seal
    lets validate_corpus independently re-verify this binding across an entire corpus
    using a FRESH registry, regardless of which transient registry object individual
    validate_example calls happened to share (gate-2 review R4)."""

    def __init__(self):
        self._store = {}

    def register(self, fact):
        return self.register_seal(fact.label_version, _label_fact_seal(fact))

    def register_seal(self, label_version, seal):
        if label_version in self._store:
            require(self._store[label_version] == seal, 'LABEL_VERSION_CONTENT_REWRITE:' + label_version)
            return False
        self._store[label_version] = seal
        return True


@dataclass(frozen=True)
class CausalClocks:
    decision_at: Timestamp
    prediction_frozen_at: Timestamp

    def __post_init__(self):
        require_trusted(self.decision_at, 'decision_at')
        require_trusted(self.prediction_frozen_at, 'prediction_frozen_at')
        require(self.decision_at.conservative_upper_bound <= self.prediction_frozen_at.conservative_lower_bound,
                'PREDICTION_NOT_FROZEN_AFTER_DECISION')


CUTOFF_ROLE = {'TRAIN': 'fit_cutoff', 'DEVELOPMENT': 'selection_freeze_at', 'CONFIRMATION': 'evaluation_asof'}


def validate_split_cutoffs(cutoffs):
    """Separate TRAIN/DEVELOPMENT/CONFIRMATION cutoffs, never one global cutoff."""
    require(set(cutoffs) == set(SPLITS), 'SPLIT_CUTOFF_SET')
    ordered = [cutoffs[s] for s in SPLITS]
    for split, ts in cutoffs.items():
        require_trusted(ts, CUTOFF_ROLE[split])
    for a, b in zip(ordered, ordered[1:]):
        require(a.conservative_upper_bound < b.conservative_lower_bound, 'SPLIT_CUTOFFS_MUST_BE_DISTINCT_AND_ORDERED')


def split_cutoffs_sha256(cutoffs):
    return digest({s: dict(utc=cutoffs[s].utc, origin=cutoffs[s].origin,
                            uncertainty_seconds=cutoffs[s].uncertainty_seconds,
                            clock_health=cutoffs[s].clock_health) for s in SPLITS})


# Which prior split's frozen cutoff must have already passed before an artifact used
# for THIS split's held-out prediction could legitimately exist.
ARTIFACT_SOURCE_SPLIT = {'DEVELOPMENT': 'TRAIN', 'CONFIRMATION': 'DEVELOPMENT'}


@dataclass(frozen=True)
class TrainedArtifact:
    """Synthetic manifest binding a held-out split's prediction to the specific
    frozen trained/selected artifact it used, and to that artifact's own causal
    availability. Fitting/selecting a model takes nonzero wall-clock time after its
    governing split's decision cutoff; a prediction cannot honestly use an artifact
    that does not yet causally exist. No real fitter is implemented anywhere; this is
    a schema/causality binding only (gate-2 review R1)."""
    artifact_id: str
    produced_after_split: str
    split_cutoffs_sha256: str
    available_at: Timestamp
    artifact_bytes: bytes
    family: str
    bucket_partition_sha256: str
    coverage_policy_id: str

    def __post_init__(self):
        require(is_sha(self.artifact_id), 'ARTIFACT_ID_DIGEST')
        require(self.produced_after_split in SPLITS, 'ARTIFACT_SPLIT_IDENTITY')
        require_trusted(self.available_at, 'artifact_available_at')
        require(type(self.artifact_bytes) is bytes and bool(self.artifact_bytes) and
                hashlib.sha256(self.artifact_bytes).hexdigest() == self.artifact_id,
                'ARTIFACT_BYTES_IDENTITY')
        require(self.family in FAMILIES and is_sha(self.bucket_partition_sha256) and
                is_sha(self.coverage_policy_id), 'ARTIFACT_TARGET_POLICY_IDENTITY')


def _raw_content(capture):
    ev = capture.evidence
    return digest(dict(byte_sha256=capture.byte_sha256, index_sha256=capture.index_sha256,
                       request_url=ev.request_url, range_start=ev.range_start,
                       range_end=ev.range_end, resource_size=ev.resource_size,
                       request_started_at=asdict(ev.request_started_at),
                       response_completed_at=asdict(ev.response_completed_at),
                       http_status=ev.http_status, content_range=ev.content_range,
                       response_headers=list(ev.response_headers), sealed=ev.sealed,
                       state=ev.state,
                       clock_evidence_sha256=hashlib.sha256(ev.clock_evidence_bytes).hexdigest()))


def extraction_digest(extraction):
    return hashlib.sha256(extraction_manifest_bytes(extraction)).hexdigest()


def extraction_manifest_bytes(extraction):
    return canonical({key: value for key, value in asdict(extraction).items()
                      if key != 'extraction_sha256'})


def verify_synthetic_decoding(capture, extraction):
    """Decode the content-addressed synthetic message at its indexed byte span.
    This is deliberately a small offline wire format, not a GRIB parser or a real
    provider attestation. The raw bytes, not a caller's manifest, supply the
    message coordinates and station-specific value/geometry."""
    ev = capture.evidence
    start, length = extraction.message_offset, extraction.message_length
    raw = ev.raw_bytes
    require(raw[start:start + length] and start + length <= len(raw),
            'SYNTHETIC_MESSAGE_SPAN_MISSING')
    try:
        index = strict_json(ev.index_bytes)
    except (ValueError, UnicodeDecodeError, TypeError):
        require(False, 'SYNTHETIC_MESSAGE_INDEX_DECODE_FAILED')
    require(type(index) is list and dict(offset=start, length=length) in index,
            'SYNTHETIC_MESSAGE_INDEX_MISMATCH')
    message_bytes = raw[start:start + length]
    try:
        message = strict_json(message_bytes)
    except (ValueError, UnicodeDecodeError, TypeError):
        require(False, 'SYNTHETIC_MESSAGE_DECODE_FAILED')
    require(canonical(message) == message_bytes and type(message) is dict,
            'SYNTHETIC_MESSAGE_NOT_CANONICAL')
    expected = dict(provider=extraction.provider, source_release=extraction.source_release,
                    run_date=extraction.run_date, cycle=extraction.cycle,
                    member=extraction.member, forecast_hour=extraction.forecast_hour,
                    valid_utc=extraction.valid_utc, parameter=extraction.parameter,
                    unit=extraction.unit)
    require(all(message.get(k) == v for k, v in expected.items()),
            'SYNTHETIC_MESSAGE_SEMANTICS_MISMATCH')
    stations = message.get('stations')
    require(type(stations) is dict and extraction.station_id in stations,
            'SYNTHETIC_STATION_EXTRACTION_MISSING')
    station = stations[extraction.station_id]
    require(type(station) is dict and station.get('station_version') == extraction.station_version and
            station.get('grid') == asdict(extraction.grid) and
            station.get('value_native_k') == extraction.value_native_k and
            station.get('policy_sha256') == extraction.policy_sha256,
            'SYNTHETIC_STATION_EXTRACTION_MISMATCH')


def _extraction_content(capture, value_native_k, grid, receipt, extraction, source_published_at):
    return digest(dict(
        byte_sha256=capture.byte_sha256, index_sha256=capture.index_sha256, value_native_k=value_native_k,
        grid=dict(nearest_lat=grid.nearest_lat, nearest_lon=grid.nearest_lon, distance_km=grid.distance_km,
                  grid_resolution_deg=grid.grid_resolution_deg, grid_sha256=grid.grid_sha256),
        receipt=asdict(receipt), extraction=asdict(extraction),
        source_published_at=asdict(source_published_at) if source_published_at else None))


def verify_evidence_bytes(resolver, claimed_bytes, claimed_sha):
    require(callable(resolver), 'INJECTED_EVIDENCE_RESOLVER_REQUIRED')
    actual = resolver(claimed_sha)
    require(type(actual) is bytes and actual == claimed_bytes and
            hashlib.sha256(actual).hexdigest() == claimed_sha,
            'INJECTED_EVIDENCE_VERIFICATION_FAILED')


def verify_timestamp_evidence(resolver, ts):
    require(is_sha(ts.evidence_sha256) and callable(resolver), 'CLOCK_EVIDENCE_REQUIRED')
    actual = resolver(ts.evidence_sha256)
    require(type(actual) is bytes and hashlib.sha256(actual).hexdigest() == ts.evidence_sha256,
            'CLOCK_EVIDENCE_VERIFICATION_FAILED')


def verify_digest_payload(resolver, identity):
    require(callable(resolver) and is_sha(identity), 'INJECTED_EVIDENCE_RESOLVER_REQUIRED')
    payload = resolver(identity)
    require(type(payload) is bytes and hashlib.sha256(payload).hexdigest() == identity,
            'INJECTED_DEPENDENCY_BYTES_VERIFICATION_FAILED')


def verify_bound_clock(resolver, ts, subject):
    verify_evidence_bytes(resolver, clock_payload(ts, subject), ts.evidence_sha256)


class CaptureRegistry:
    """Two-level immutable content-addressed identity. The RAW store binds one
    immutable message per (provider, run_date, cycle, member, forecast_hour)
    coordinate -- multiple stations may legitimately extract different values from
    the SAME raw message, so the raw identity is bound only to byte/index digests
    (gate-2 review R6). The EXTRACTION store additionally binds station identity and
    the decoded value/grid/receipt for that station; a semantic rewrite of the SAME
    extraction -- including a changed grid or receipt timestamp under unchanged
    coordinates -- is refused even though the raw message is unchanged (gate-2 review
    R5). An injected resolver supplies the exact synthetic raw, index, manifest,
    extraction and clock bytes at every registration."""

    def __init__(self):
        self._raw = {}
        self._extractions = {}

    def register(self, raw_key, station_id, station_version, capture, value_native_k, grid, receipt,
                 extraction, source_published_at, evidence_resolver):
        require(is_sha(capture.byte_sha256) and is_sha(capture.index_sha256), 'CAPTURE_DIGEST')
        require(isinstance(capture.evidence, CaptureEvidence), 'CAPTURE_EVIDENCE_REQUIRED')
        ev = capture.evidence
        require(ev.state == 'SEALED' and ev.sealed is True, 'CAPTURE_NOT_SEALED')
        verify_evidence_bytes(evidence_resolver, ev.raw_bytes, capture.byte_sha256)
        verify_evidence_bytes(evidence_resolver, ev.index_bytes, capture.index_sha256)
        verify_evidence_bytes(evidence_resolver, ev.clock_evidence_bytes,
                              ev.response_completed_at.evidence_sha256)
        verify_bound_clock(evidence_resolver, ev.request_started_at, 'LOCAL_CLOCK')
        verify_evidence_bytes(evidence_resolver, ev.manifest_bytes,
                              hashlib.sha256(ev.manifest_bytes).hexdigest())
        verify_synthetic_decoding(capture, extraction)
        verify_evidence_bytes(evidence_resolver, extraction_manifest_bytes(extraction),
                              extraction.extraction_sha256)
        for identity in (grid.grid_sha256, extraction.decoder_sha256,
                         extraction.code_sha256, extraction.policy_sha256,
                         extraction.dependency_sha256):
            verify_digest_payload(evidence_resolver, identity)
        require(ev.response_completed_at == receipt, 'CAPTURE_RECEIPT_MISMATCH')
        require(extraction.raw_sha256 == capture.byte_sha256 and
                extraction.index_sha256 == capture.index_sha256 and
                (extraction.provider, extraction.run_date, extraction.cycle,
                 extraction.member, extraction.forecast_hour) == raw_key and
                extraction.response_range_start == ev.range_start and
                extraction.response_range_end == ev.range_end and
                extraction.message_offset + extraction.message_length <= len(ev.raw_bytes) and
                extraction.station_id == station_id and
                extraction.station_version == station_version and
                extraction.grid == grid and
                extraction.value_native_k == value_native_k and
                extraction.extraction_sha256 == extraction_digest(extraction),
                'CAPTURE_EXTRACTION_MISMATCH')
        raw_content = _raw_content(capture)
        if raw_key in self._raw:
            require(self._raw[raw_key] == raw_content, 'IMMUTABLE_RAW_MESSAGE_CONFLICT:' + repr(raw_key))
        else:
            self._raw[raw_key] = raw_content
        extraction_key = raw_key + (station_id, station_version)
        extraction_content = _extraction_content(capture, value_native_k, grid, receipt,
                                                 extraction, source_published_at)
        if extraction_key in self._extractions:
            require(self._extractions[extraction_key] == extraction_content,
                    'IMMUTABLE_CAPTURE_CONFLICT:' + repr(extraction_key))
            return False
        self._extractions[extraction_key] = extraction_content
        return True


def _validated_example_seal(*, city_day, dedup_key, split, coverage, rule_version, station_version, station_id,
                             rule_id, label_version, label_revision_of, family, winner_bucket, bucket_count,
                             points, in_day_observations, out_of_day_points, decision_at, prediction_frozen_at,
                             label_knowable_upper_bound, split_cutoffs_sha256, coverage_policy_id, evidence_class,
                             label_content_seal, capture_entries):
    return digest(dict(
        city_day=city_day, dedup_key=list(dedup_key), split=split, coverage=asdict(coverage),
        rule_version=rule_version, station_version=station_version, station_id=station_id, rule_id=rule_id,
        label_version=label_version, label_revision_of=label_revision_of, family=family,
        winner_bucket=winner_bucket, bucket_count=bucket_count, points=points,
        in_day_observations=in_day_observations, out_of_day_points=out_of_day_points,
        decision_at=decision_at, prediction_frozen_at=prediction_frozen_at,
        label_knowable_upper_bound=label_knowable_upper_bound, split_cutoffs_sha256=split_cutoffs_sha256,
        coverage_policy_id=coverage_policy_id, evidence_class=evidence_class,
        label_content_seal=label_content_seal, capture_entries=[list(e) for e in capture_entries]))


@dataclass(frozen=True)
class ExampleInputs:
    city_day: str
    split: str
    points: tuple
    coverage_policy: CoveragePolicy
    local_day: LocalDay
    clocks: CausalClocks
    label_history: tuple
    split_cutoffs: tuple
    station_id: str
    rule_id: str
    artifact: TrainedArtifact | None
    run_candidates: tuple
    run_inventory_sha256: str
    outage_evidence: tuple

    def kwargs(self):
        return dict(city_day=self.city_day, split=self.split, points=self.points,
                    coverage_policy=self.coverage_policy, local_day=self.local_day, clocks=self.clocks,
                    label_history=self.label_history, split_cutoffs=dict(self.split_cutoffs),
                    station_id=self.station_id, rule_id=self.rule_id, artifact=self.artifact,
                    run_candidates=self.run_candidates,
                    run_inventory_sha256=self.run_inventory_sha256,
                    outage_evidence=self.outage_evidence)


@dataclass(frozen=True)
class ValidatedExample:
    """Deeply immutable derived summary retaining complete typed source inputs.
    Public construction and a checksum do not establish admission: validate_corpus
    replays those inputs through the full validator and compares every field."""
    city_day: str
    dedup_key: tuple
    split: str
    coverage: CoverageResult
    rule_version: str
    station_version: str
    station_id: str
    rule_id: str
    label_version: str
    label_revision_of: str | None
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
    content_seal: str
    label_content_seal: str
    capture_entries: tuple
    source_inputs: ExampleInputs
    learner_admitted: bool
    financial_authority: bool
    promotion_authority: bool
    host_approved: bool
    order_authority: bool

    def __post_init__(self):
        require(self.split in SPLITS, 'VALIDATED_EXAMPLE_SPLIT_ENUM')
        require(self.evidence_class == 'SYNTHETIC', 'VALIDATED_EXAMPLE_EVIDENCE_CLASS_ENUM')
        require(self.learner_admitted is False, 'VALIDATED_EXAMPLE_LEARNER_ADMITTED_FORGED')
        require(self.financial_authority is False, 'VALIDATED_EXAMPLE_FINANCIAL_AUTHORITY_FORGED')
        require(self.promotion_authority is False, 'VALIDATED_EXAMPLE_PROMOTION_AUTHORITY_FORGED')
        require(self.host_approved is False, 'VALIDATED_EXAMPLE_HOST_APPROVED_FORGED')
        require(self.order_authority is False, 'VALIDATED_EXAMPLE_ORDER_AUTHORITY_FORGED')
        require(type(self.winner_bucket) is int and type(self.bucket_count) is int and
                0 <= self.winner_bucket < self.bucket_count, 'VALIDATED_EXAMPLE_WINNER_BUCKET_RANGE')
        require(is_sha(self.rule_version) and is_sha(self.station_version) and is_sha(self.label_version)
                and is_sha(self.coverage_policy_id) and is_sha(self.split_cutoffs_sha256),
                'VALIDATED_EXAMPLE_IDENTITY_DIGESTS')
        require(isinstance(self.station_id, str) and bool(self.station_id.strip())
                and isinstance(self.rule_id, str) and bool(self.rule_id.strip()),
                'VALIDATED_EXAMPLE_TARGET_IDENTITY')
        require(isinstance(self.coverage, CoverageResult), 'VALIDATED_EXAMPLE_COVERAGE_TYPE')
        require(isinstance(self.dedup_key, tuple) and len(self.dedup_key) == 5 and
                self.dedup_key[0] == self.station_id and self.dedup_key[2] == self.rule_id and
                self.dedup_key[4] == self.family, 'VALIDATED_EXAMPLE_DEDUP_KEY_BOUND')
        require(isinstance(self.capture_entries, tuple), 'VALIDATED_EXAMPLE_CAPTURE_ENTRIES_TYPE')
        require(isinstance(self.source_inputs, ExampleInputs), 'VALIDATED_EXAMPLE_SOURCE_INPUTS_REQUIRED')
        require(self.points == self.in_day_observations + self.out_of_day_points,
                'VALIDATED_EXAMPLE_POINT_COUNT_INCONSISTENT')
        expected_seal = _validated_example_seal(
            city_day=self.city_day, dedup_key=self.dedup_key, split=self.split, coverage=self.coverage,
            rule_version=self.rule_version, station_version=self.station_version, station_id=self.station_id,
            rule_id=self.rule_id, label_version=self.label_version, label_revision_of=self.label_revision_of,
            family=self.family, winner_bucket=self.winner_bucket, bucket_count=self.bucket_count,
            points=self.points, in_day_observations=self.in_day_observations,
            out_of_day_points=self.out_of_day_points, decision_at=self.decision_at,
            prediction_frozen_at=self.prediction_frozen_at,
            label_knowable_upper_bound=self.label_knowable_upper_bound,
            split_cutoffs_sha256=self.split_cutoffs_sha256, coverage_policy_id=self.coverage_policy_id,
            evidence_class=self.evidence_class, label_content_seal=self.label_content_seal,
            capture_entries=self.capture_entries)
        require(self.content_seal == expected_seal, 'VALIDATED_EXAMPLE_CONTENT_SEAL_MISMATCH')

    @property
    def trial_key(self):
        # HIGH/LOW and provider rows share one station/event/local-day trial.
        return (self.dedup_key[0], self.dedup_key[1], self.dedup_key[3])


def validate_example(*, city_day, split, points, coverage_policy, local_day, clocks, label_history,
                      split_cutoffs, capture_registry, label_registry, station_id, rule_id,
                      evidence_resolver, artifact=None, run_candidates=(),
                      run_inventory_sha256=None, outage_evidence=(),
                      evidence_class='SYNTHETIC'):
    """Validate one city-day trajectory/capture example end to end. Fails closed on
    any incomplete coverage, untrusted or missing clock, out-of-order causal
    dependency, non-FINAL label, or settlement-target mismatch between the label and
    this example's own points. Never admits a non-SYNTHETIC example: no real adapter
    exists yet. station_id/rule_id are this example's own canonical (revision-
    independent) settlement identity claims, cross-checked against the admitted
    label's target (gate-2 review R3)."""
    require(evidence_class == 'SYNTHETIC', 'REAL_ADAPTER_NOT_IMPLEMENTED')
    require(callable(evidence_resolver), 'INJECTED_EVIDENCE_RESOLVER_REQUIRED')
    require(split in SPLITS, 'SPLIT_IDENTITY')
    validate_city_day_identity(city_day, local_day)
    require(all(len({p.source_release for p in points if p.provider == provider}) == 1
                for provider in {p.provider for p in points}), 'MULTIPLE_SOURCE_RELEASES_IN_EXAMPLE')
    validate_split_cutoffs(split_cutoffs)
    decision_lower = clocks.decision_at.conservative_lower_bound
    require(coverage_policy.preregistered_at.conservative_upper_bound <= decision_lower,
            'PROTOCOL_NOT_PREREGISTERED_BY_DECISION')
    verify_bound_clock(evidence_resolver, coverage_policy.preregistered_at,
                       coverage_policy.protocol_subject)
    require(coverage_policy.requested_cohort and
            any(key[0] == station_id and key[2] == rule_id and key[3] == local_day.target_date
                for key in coverage_policy.requested_cohort), 'TARGET_NOT_IN_REQUESTED_COHORT')
    coverage = validate_coverage(points, coverage_policy)
    present_providers = {p.provider for p in points}
    if coverage.absent_providers:
        require(coverage_policy.fallback_mode == 'FROZEN_OUTAGE', 'REQUIRED_PROVIDER_ABSENT_NO_FALLBACK')
        require(set(coverage.absent_providers) == {o.provider for o in outage_evidence},
                'OUTAGE_EVIDENCE_PROVIDER_SET')
        for outage in outage_evidence:
            verify_bound_clock(evidence_resolver, outage.observed_at, outage.provider)
            actual = evidence_resolver(outage.evidence_sha256)
            require(type(actual) is bytes and hashlib.sha256(actual).hexdigest() == outage.evidence_sha256,
                    'OUTAGE_EVIDENCE_VERIFICATION_FAILED')
            require(outage.observed_at.conservative_upper_bound <= decision_lower,
                    'OUTAGE_NOT_KNOWN_BY_DECISION')
    else:
        require(not outage_evidence, 'UNDECLARED_OUTAGE_EVIDENCE')
    rule_version = validate_rule_consistency(points)
    station_version = validate_station_consistency(points)
    annotated = annotate_relative_time(points, local_day)
    verify_bound_clock(evidence_resolver, clocks.decision_at, 'decision_at')
    verify_bound_clock(evidence_resolver, clocks.prediction_frozen_at, 'LOCAL_CLOCK')
    for name, ts in split_cutoffs.items():
        verify_bound_clock(evidence_resolver, ts, CUTOFF_ROLE[name])
    capture_entries = []
    for p in points:
        # Every upstream receipt dependency's conservative bound -- not only
        # feature_ready_at's own uncertainty -- must clear the decision cutoff. A
        # fresh, apparently precise feature timestamp must not erase an earlier
        # dependency's uncertainty (gate-2 review F1).
        require(p.extraction.station_id == station_id, 'EXTRACTION_STATION_TARGET_MISMATCH')
        require(p.response_completed_at.conservative_upper_bound <= decision_lower,
                'FEATURE_NOT_AVAILABLE_BY_DECISION')
        require(p.feature_ready_at.conservative_upper_bound <= decision_lower,
                'FEATURE_NOT_AVAILABLE_BY_DECISION')
        verify_bound_clock(evidence_resolver, p.response_completed_at, p.capture.byte_sha256)
        verify_bound_clock(evidence_resolver, p.feature_ready_at,
                           p.extraction.extraction_sha256)
        if p.source_published_at is not None:
            verify_bound_clock(evidence_resolver, p.source_published_at,
                               p.capture.byte_sha256)
        raw_key = (p.provider, p.run_date, p.cycle, p.member, p.forecast_hour)
        capture_registry.register(raw_key=raw_key, station_id=station_id,
                                   station_version=p.station_version, capture=p.capture,
                                   value_native_k=p.value_native_k, grid=p.grid, receipt=p.response_completed_at,
                                   extraction=p.extraction, source_published_at=p.source_published_at,
                                   evidence_resolver=evidence_resolver)
        capture_entries.append((raw_key, station_id, p.station_version, _raw_content(p.capture),
                                 _extraction_content(p.capture, p.value_native_k, p.grid,
                                                     p.response_completed_at, p.extraction,
                                                     p.source_published_at)))
    capture_entries = tuple(capture_entries)
    for fact in label_history:
        verify_evidence_bytes(evidence_resolver, label_payload_bytes(fact), fact.label_version)
        verify_bound_clock(evidence_resolver, fact.label_knowable_at, fact.label_version)
        label_registry.register(fact)
    validate_label_lineage(label_history)
    cutoff = split_cutoffs[split]
    eligible_labels = [fact for fact in label_history if
                       fact.label_knowable_at.conservative_upper_bound <= cutoff.conservative_lower_bound]
    require(bool(eligible_labels), 'LABEL_NOT_KNOWABLE_BY_SPLIT_ASOF')
    current_label = eligible_labels[-1]
    verify_bound_clock(evidence_resolver, current_label.label_knowable_at,
                       current_label.label_version)
    require(current_label.status == 'FINAL', 'LABEL_NOT_FINAL_FOR_ADMISSION')
    require(current_label.target.station_id == station_id, 'SETTLEMENT_TARGET_STATION_MISMATCH')
    require(current_label.target.rule_id == rule_id, 'SETTLEMENT_TARGET_RULE_MISMATCH')
    require(current_label.target.target_date == local_day.target_date, 'SETTLEMENT_TARGET_DATE_MISMATCH')
    require(current_label.target.station_version == station_version, 'SETTLEMENT_TARGET_STATION_VERSION_MISMATCH')
    require(current_label.target.rule_version == rule_version, 'SETTLEMENT_TARGET_RULE_VERSION_MISMATCH')
    verify_digest_payload(evidence_resolver, station_version)
    verify_digest_payload(evidence_resolver, rule_version)
    require(current_label.target.settlement_timezone == local_day.timezone,
            'SETTLEMENT_TIMEZONE_MISMATCH')
    verify_evidence_bytes(evidence_resolver, station_metadata_bytes(current_label.target),
                          current_label.target.station_metadata_sha256)
    require(current_label.target.key in coverage_policy.requested_cohort, 'TARGET_NOT_IN_REQUESTED_COHORT')
    station_metadata = current_label.target.station_metadata_available_at
    rule_metadata = current_label.target.rule_metadata_available_at
    verify_bound_clock(evidence_resolver, station_metadata,
        station_metadata_subject(station_id, current_label.target.event_id, station_version,
                                 current_label.target.station_metadata_sha256))
    verify_bound_clock(evidence_resolver, rule_metadata,
        rule_metadata_subject(rule_id, rule_version, current_label.family,
                              current_label.target.unit, current_label.target.rounding,
                              current_label.target.bucket_edges))
    require(station_metadata.conservative_upper_bound <= decision_lower and
            rule_metadata.conservative_upper_bound <= decision_lower,
            'TARGET_METADATA_NOT_AVAILABLE_BY_DECISION')
    require(clocks.prediction_frozen_at.conservative_upper_bound <= current_label.label_knowable_at.conservative_lower_bound,
            'LABEL_KNOWABLE_BEFORE_FROZEN')
    # V1 is FUTURE_FORECAST-only: decision and frozen prediction must precede
    # local-day start, using the CONSERVATIVE (uncertainty-inclusive) bound so a
    # prediction whose uncertainty extends past day start cannot pass on its nominal
    # value alone (gate-2 review R1).
    require(clocks.prediction_frozen_at.conservative_upper_bound < local_day.start_utc,
            'PREDICTION_NOT_FROZEN_BEFORE_LOCAL_DAY_START')
    trial_key = (station_id, current_label.target.event_id, local_day.target_date)
    require(clocks.decision_at == dict(coverage_policy.decision_schedule)[trial_key],
            'DECISION_NOT_ON_FROZEN_SCHEDULE')
    require(is_sha(run_inventory_sha256), 'RUN_INVENTORY_EVIDENCE_REQUIRED')
    require({candidate.provider for candidate in run_candidates} == present_providers and
            len({(candidate.provider, candidate.run_initialized_at.utc)
                 for candidate in run_candidates}) == len(run_candidates),
            'RUN_CANDIDATE_INVENTORY_INCOMPLETE_OR_DUPLICATE')
    slots = set(run_candidate_slots(coverage_policy, decision_lower))
    require({(candidate.provider, candidate.run_initialized_at.utc)
             for candidate in run_candidates} ==
            {(provider, stamp) for provider in present_providers for stamp in slots},
            'RUN_CANDIDATE_INVENTORY_INCOMPLETE_OR_DUPLICATE')
    verify_evidence_bytes(evidence_resolver,
                          run_inventory_bytes(coverage_policy, clocks.decision_at,
                                              run_candidates, present_providers),
                          run_inventory_sha256)
    for candidate in run_candidates:
        verify_bound_clock(evidence_resolver, candidate.ready_at, candidate.subject)
        if not candidate.complete:
            require(candidate.ready_at.conservative_upper_bound <= decision_lower,
                    'RUN_CANDIDATE_STATUS_NOT_KNOWN_BY_DECISION')
    for provider in present_providers:
        selected = next(p for p in points if p.provider == provider)
        eligible = [c for c in run_candidates if c.provider == provider and c.complete and
                    c.ready_at.conservative_upper_bound <= decision_lower and
                    c.run_initialized_at.utc <= decision_lower and
                    decision_lower - c.run_initialized_at.utc <= coverage_policy.max_run_age_seconds and
                    datetime.fromtimestamp(c.run_initialized_at.utc, dt_timezone.utc).hour in coverage_policy.allowed_cycles]
        require(bool(eligible), 'NO_ELIGIBLE_RUN_CANDIDATE')
        chosen = max(eligible, key=lambda c: c.run_initialized_at.utc)
        require(selected.run_initialized_at.utc == chosen.run_initialized_at.utc,
                'RUN_SELECTION_NOT_LATEST_ELIGIBLE')
        require(selected.cycle in coverage_policy.allowed_cycles, 'RUN_CYCLE_NOT_IN_PROTOCOL')
        require(chosen.ready_at.utc >=
                max(p.feature_ready_at.conservative_upper_bound for p in points if p.provider == provider),
                'RUN_READY_BEFORE_COMPLETE_FEATURES')
    require_trusted(cutoff, CUTOFF_ROLE[split])
    if split == 'TRAIN':
        require(current_label.label_knowable_at.conservative_upper_bound <= cutoff.conservative_lower_bound,
                'TRAIN_LABEL_NOT_KNOWABLE_BY_FIT_CUTOFF')
    elif split == 'DEVELOPMENT':
        require(current_label.label_knowable_at.conservative_upper_bound <= cutoff.conservative_lower_bound,
                'DEVELOPMENT_LABEL_NOT_KNOWABLE_BY_SELECTION_FREEZE')
        require(artifact is not None, 'ARTIFACT_IDENTITY_REQUIRED_FOR_HELD_OUT_SPLIT')
        require(artifact.split_cutoffs_sha256 == split_cutoffs_sha256(split_cutoffs),
                'ARTIFACT_DETACHED_FROM_SPLIT_CUTOFFS')
        require(artifact.produced_after_split == 'TRAIN', 'ARTIFACT_WRONG_SOURCE_SPLIT')
        require(artifact.available_at.conservative_lower_bound > split_cutoffs['TRAIN'].conservative_upper_bound,
                'ARTIFACT_NOT_AVAILABLE_AFTER_DECISION')
        require(artifact.available_at.conservative_upper_bound <= decision_lower,
                'DEVELOPMENT_PREDICTION_BEFORE_FIT_CUTOFF')
    else:
        require(clocks.prediction_frozen_at.conservative_upper_bound <= cutoff.conservative_lower_bound,
                'CONFIRMATION_NOT_FROZEN_BEFORE_EVALUATION_ASOF')
        require(current_label.label_knowable_at.conservative_upper_bound <= cutoff.conservative_lower_bound,
                'CONFIRMATION_LABEL_NOT_KNOWABLE_BY_EVALUATION_ASOF')
        require(artifact is not None, 'ARTIFACT_IDENTITY_REQUIRED_FOR_HELD_OUT_SPLIT')
        require(artifact.split_cutoffs_sha256 == split_cutoffs_sha256(split_cutoffs),
                'ARTIFACT_DETACHED_FROM_SPLIT_CUTOFFS')
        require(artifact.produced_after_split == 'DEVELOPMENT', 'ARTIFACT_WRONG_SOURCE_SPLIT')
        require(artifact.available_at.conservative_lower_bound > split_cutoffs['DEVELOPMENT'].conservative_upper_bound,
                'ARTIFACT_NOT_AVAILABLE_AFTER_DECISION')
        require(artifact.available_at.conservative_upper_bound <= decision_lower,
                'CONFIRMATION_PREDICTION_BEFORE_SELECTION_FREEZE')
    if artifact is not None:
        verify_evidence_bytes(evidence_resolver, artifact.artifact_bytes, artifact.artifact_id)
        verify_bound_clock(evidence_resolver, artifact.available_at, artifact.artifact_id)
        require(artifact.family == current_label.family and
                artifact.bucket_partition_sha256 == digest(list(current_label.target.bucket_edges)) and
                artifact.coverage_policy_id == coverage_policy.policy_id,
                'ARTIFACT_TARGET_POLICY_MISMATCH')
    label_content_seal = _label_fact_seal(current_label)
    dedup_key = current_label.target.key
    in_day_observations = sum(1 for a in annotated if a['in_day_observation'])
    out_of_day_points = sum(1 for a in annotated if not a['in_day_observation'])
    content_seal = _validated_example_seal(
        city_day=city_day, dedup_key=dedup_key, split=split, coverage=coverage,
        rule_version=rule_version, station_version=station_version, station_id=station_id, rule_id=rule_id,
        label_version=current_label.label_version, label_revision_of=current_label.revision_of,
        family=current_label.family, winner_bucket=current_label.winner_bucket,
        bucket_count=current_label.bucket_count, points=len(points),
        in_day_observations=in_day_observations, out_of_day_points=out_of_day_points,
        decision_at=decision_lower, prediction_frozen_at=clocks.prediction_frozen_at.conservative_upper_bound,
        label_knowable_upper_bound=current_label.label_knowable_at.conservative_upper_bound,
        split_cutoffs_sha256=split_cutoffs_sha256(split_cutoffs), coverage_policy_id=coverage_policy.policy_id,
        evidence_class=evidence_class, label_content_seal=label_content_seal, capture_entries=capture_entries)
    source_inputs = ExampleInputs(city_day=city_day, split=split, points=tuple(points),
                                  coverage_policy=coverage_policy, local_day=local_day, clocks=clocks,
                                  label_history=tuple(label_history),
                                  split_cutoffs=tuple((s, split_cutoffs[s]) for s in SPLITS),
                                  station_id=station_id, rule_id=rule_id, artifact=artifact,
                                  run_candidates=tuple(run_candidates),
                                  run_inventory_sha256=run_inventory_sha256,
                                  outage_evidence=tuple(outage_evidence))
    return ValidatedExample(
        city_day=city_day, dedup_key=dedup_key, split=split, coverage=coverage,
        rule_version=rule_version, station_version=station_version, station_id=station_id, rule_id=rule_id,
        label_version=current_label.label_version, label_revision_of=current_label.revision_of,
        family=current_label.family, winner_bucket=current_label.winner_bucket,
        bucket_count=current_label.bucket_count, points=len(points),
        in_day_observations=in_day_observations, out_of_day_points=out_of_day_points,
        decision_at=decision_lower, prediction_frozen_at=clocks.prediction_frozen_at.conservative_upper_bound,
        label_knowable_upper_bound=current_label.label_knowable_at.conservative_upper_bound,
        split_cutoffs_sha256=split_cutoffs_sha256(split_cutoffs), coverage_policy_id=coverage_policy.policy_id,
        evidence_class=evidence_class, content_seal=content_seal, label_content_seal=label_content_seal,
        capture_entries=capture_entries, source_inputs=source_inputs, learner_admitted=False,
        financial_authority=False, promotion_authority=False, host_approved=False, order_authority=False)


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


def _replay_label_versions(records):
    """Independently re-verify label_version content binding across the WHOLE corpus
    using a fresh registry, regardless of which transient registry object individual
    validate_example calls happened to share (gate-2 review R4)."""
    registry = LabelVersionRegistry()
    for r in records:
        registry.register_seal(r.label_version, r.label_content_seal)


def _replay_capture_entries(records):
    """Independently re-verify raw/extraction capture content binding across the
    WHOLE corpus using a fresh store, regardless of which transient registry object
    individual validate_example calls happened to share (gate-2 review R5)."""
    raw_store, extraction_store = {}, {}
    for r in records:
        for raw_key, station_id, station_version, raw_content, extraction_content in r.capture_entries:
            if raw_key in raw_store:
                require(raw_store[raw_key] == raw_content, 'IMMUTABLE_RAW_MESSAGE_CONFLICT:' + repr(raw_key))
            else:
                raw_store[raw_key] = raw_content
            extraction_key = raw_key + (station_id, station_version)
            if extraction_key in extraction_store:
                require(extraction_store[extraction_key] == extraction_content,
                        'IMMUTABLE_CAPTURE_CONFLICT:' + repr(extraction_key))
            else:
                extraction_store[extraction_key] = extraction_content


def validate_corpus(records, split_cutoffs, coverage_policy, evidence_resolver):
    """Revalidate complete source facts and injected bytes against the frozen policy.
    Report missingness over its full requested cohort, grouping providers under one
    canonical settlement target rather than counting them as independent days."""
    require(all(isinstance(r, ValidatedExample) for r in records), 'CORPUS_RECORDS_MUST_BE_VALIDATED_EXAMPLES')
    require(isinstance(coverage_policy, CoveragePolicy), 'CORPUS_PROTOCOL_REQUIRED')
    require(callable(evidence_resolver), 'INJECTED_EVIDENCE_RESOLVER_REQUIRED')
    verify_bound_clock(evidence_resolver, coverage_policy.preregistered_at,
                       coverage_policy.protocol_subject)
    for _, scheduled_at in coverage_policy.decision_schedule:
        verify_bound_clock(evidence_resolver, scheduled_at, 'decision_at')
    validate_split_cutoffs(split_cutoffs)
    expected_sha = split_cutoffs_sha256(split_cutoffs)
    require(all(r.split_cutoffs_sha256 == expected_sha for r in records), 'DETACHED_CORPUS_CUTOFFS')
    require(all(r.coverage_policy_id == coverage_policy.policy_id for r in records),
            'CORPUS_COVERAGE_POLICY_MUST_BE_FROZEN')
    # A public dataclass and its checksum are not an admission boundary. Re-run the
    # COMPLETE retained source facts with fresh, shared registries and independently
    # injected bytes. Then compare every derived field, including timing and counts.
    captures, labels = CaptureRegistry(), LabelVersionRegistry()
    for record in records:
        source = record.source_inputs
        require(source.coverage_policy.policy_id == coverage_policy.policy_id,
                'CORPUS_COVERAGE_POLICY_MUST_BE_FROZEN')
        require(dict(source.split_cutoffs) == split_cutoffs, 'DETACHED_CORPUS_CUTOFFS')
        replay = validate_example(**source.kwargs(), capture_registry=captures,
                                  label_registry=labels, evidence_resolver=evidence_resolver)
        require(record == replay, 'CORPUS_RECORD_NOT_SOURCE_REVALIDATION')
    validate_no_duplicate_city_days(records)
    validate_cross_split_embargo(records)
    admitted_keys = {r.dedup_key for r in records}
    trial_splits = defaultdict(set)
    for r in records:
        trial_splits[r.trial_key].add(r.split)
    require(all(len(splits) == 1 for splits in trial_splits.values()),
            'CITY_DAY_SPLIT_DRIFT')
    require(admitted_keys <= set(coverage_policy.requested_cohort), 'UNREQUESTED_COHORT_TARGET')
    missing_from_requested_cohort = tuple(sorted(set(coverage_policy.requested_cohort) - admitted_keys))
    examples_with_absent_required_providers = {r.dedup_key: r.coverage.absent_providers
                                                for r in records if r.coverage.absent_providers}
    provider_missing_by_target = {key: tuple(coverage_policy.required_providers)
                                  for key in missing_from_requested_cohort}
    provider_missing_by_target.update(examples_with_absent_required_providers)
    provider_counts = {p: len({r.trial_key for r in records if
                              p in {entry[0] for entry in r.coverage.present_all}})
                       for p in coverage_policy.required_providers}
    requested_trials = {(key[0], key[1], key[3]) for key in coverage_policy.requested_cohort}
    return dict(city_days=len(trial_splits),
                splits={s: sum(s in splits for splits in trial_splits.values()) for s in SPLITS},
                admitted_real_examples=0, historical_541_day_admissions=0,
                missing_from_requested_cohort=missing_from_requested_cohort,
                examples_with_absent_required_providers=examples_with_absent_required_providers,
                provider_missing_by_target=provider_missing_by_target, provider_counts=provider_counts,
                requested_city_days=len(requested_trials))
