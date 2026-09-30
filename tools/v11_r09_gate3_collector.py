"""Offline R09 Gate 3 (G3-I) bounded capture-manifest and attempt-ledger collector.

Builds and tests, offline only, the concrete objects the accepted G3-P protocol
(docs/V11_R09_GATE3_COLLECTION_PROTOCOL.md) requires before any G3-L launch review:
source dossiers, a capture-manifest schema, a native IFS/AIFS three-hour request
path kept separate from the production `ECMWFRequest`, an attempt ledger with a
complete terminal-state partition, a persistent restriction ledger, a budget
tracker bound to the protocol's own ceilings (or a tighter already-existing bound),
and a pre-launch feasibility/dry-run estimator.

This module performs NO network access anywhere and ships no default, real
transport: every acquisition-adjacent entrypoint takes a caller-supplied
`Transport`, and this repository supplies only a synthetic transport in its own
tests. It acquires no forecast data, grants no launch/financial/promotion/host
authority, and does not itself satisfy G3-L -- G3-L still requires an
independent review binding the frozen private manifest digest (protocol Section
1) before any request may leave this repository's control.

P3-1 (review docs/V11_R09_GATE3_PROTOCOL_REVIEW_117830a.md): the reviewed
protocol's zero-retry rule (its Section 4) is a documented, deliberate exception
scoped to this single bounded pilot, stricter than -- not a violation of -- the
private master's general collector-resilience retry requirement for V11
collectors at large. This module implements no retry path anywhere: every
ledger key is terminal on first recorded outcome (see AttemptLedger.record).
"""
from __future__ import annotations

import dataclasses
import hashlib
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone as dt_timezone
from types import MappingProxyType

from tools.v11_multimodel_panel import FAMILIES, PROVIDERS, canonical, is_sha, require
from tools.v11_trajectory_contract import Timestamp, require_trusted

GATE_ID = 'R09_GATE3_G3I_COLLECTOR_V1'
VALID_PROVIDERS = tuple(PROVIDERS)  # ('GEFS', 'IFS', 'AIFS'), per the accepted contract

ZERO_RETRY_IS_DELIBERATE_PILOT_EXCEPTION = True


# --------------------------------------------------------------------------- #
# Section 2: versioned provider source identity
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SourceDossier:
    """A versioned provider source identity, separate from any single run or receipt
    (protocol Section 2). No `latest` identifier or guessed release; expected GRIB
    signature fields are recorded explicitly so an observed header hash alone is
    never treated as release proof.

    `index_sidecar_available` / `supports_byte_range_206` are P3-2 dry-run
    confirmations: this dataclass never defaults them to True. A dossier with
    either flag False or unset is not launch_ready, regardless of how complete
    its other identity fields are.
    """
    provider: str
    dataset: str
    operational_release_id: str
    source_dossier_sha256: str
    origin_base_url: str
    path_template: str
    licence_terms_sha256: str
    expected_centre: str
    expected_subcentre: int
    expected_table_version: int
    expected_generating_process: str
    expected_product_template: str
    expected_parameter: str
    expected_level: str
    expected_grid_signature: str
    member_range: tuple
    native_hours: tuple
    native_cadence_hours: int
    original_unit: str
    decoder_sha256: str
    dependency_sha256: str
    index_sidecar_available: bool
    supports_byte_range_206: bool

    def __post_init__(self):
        require(self.provider in VALID_PROVIDERS, 'DOSSIER_PROVIDER_IDENTITY')
        require(isinstance(self.dataset, str) and bool(self.dataset), 'DOSSIER_DATASET_REQUIRED')
        require(isinstance(self.operational_release_id, str) and bool(self.operational_release_id)
                and 'latest' not in self.operational_release_id.lower(), 'DOSSIER_NO_LATEST_ALIAS')
        require(all(is_sha(x) for x in (self.source_dossier_sha256, self.licence_terms_sha256,
                self.decoder_sha256, self.dependency_sha256)), 'DOSSIER_DIGESTS_REQUIRED')
        require(self.origin_base_url.startswith('https://') and '@' not in self.origin_base_url,
                'DOSSIER_ORIGIN_HTTPS_ONLY_NO_CREDENTIALS')
        require(isinstance(self.path_template, str) and bool(self.path_template)
                and '..' not in self.path_template, 'DOSSIER_PATH_TEMPLATE_REQUIRED')
        require(type(self.expected_subcentre) is int and self.expected_subcentre >= 0,
                'DOSSIER_EXPECTED_SUBCENTRE')
        require(self.original_unit == 'K', 'DOSSIER_ORIGINAL_UNIT_MUST_BE_KELVIN')
        require(type(self.member_range) is tuple and len(self.member_range) == 2 and
                0 <= self.member_range[0] <= self.member_range[1] < PROVIDERS[self.provider],
                'DOSSIER_MEMBER_RANGE')
        require(type(self.native_hours) is tuple and bool(self.native_hours) and
                tuple(sorted(set(self.native_hours))) == self.native_hours and
                all(type(h) is int and h >= 0 for h in self.native_hours), 'DOSSIER_NATIVE_HOURS')
        require(type(self.native_cadence_hours) is int and self.native_cadence_hours > 0 and
                self.native_hours == tuple(range(self.native_hours[0], self.native_hours[-1] + 1,
                                                  self.native_cadence_hours)),
                'DOSSIER_NATIVE_CADENCE_CONSISTENCY')
        require(type(self.index_sidecar_available) is bool and type(self.supports_byte_range_206) is bool,
                'DOSSIER_INDEX_AVAILABILITY_FLAGS_MUST_BE_EXPLICIT_BOOL')

    @property
    def launch_ready(self):
        """Section 4's byte-range/206 preconditions plus P3-2's index-availability
        confirmation must both be explicitly True; an unverified/unknown origin
        never defaults to permitted."""
        return self.index_sidecar_available and self.supports_byte_range_206

    def manifest_payload(self):
        return dict(provider=self.provider, dataset=self.dataset,
                    operational_release_id=self.operational_release_id,
                    source_dossier_sha256=self.source_dossier_sha256,
                    origin_base_url=self.origin_base_url, path_template=self.path_template,
                    licence_terms_sha256=self.licence_terms_sha256,
                    expected_centre=self.expected_centre, expected_subcentre=self.expected_subcentre,
                    expected_table_version=self.expected_table_version,
                    expected_generating_process=self.expected_generating_process,
                    expected_product_template=self.expected_product_template,
                    expected_parameter=self.expected_parameter, expected_level=self.expected_level,
                    expected_grid_signature=self.expected_grid_signature,
                    member_range=list(self.member_range), native_hours=list(self.native_hours),
                    native_cadence_hours=self.native_cadence_hours, original_unit=self.original_unit,
                    decoder_sha256=self.decoder_sha256, dependency_sha256=self.dependency_sha256,
                    index_sidecar_available=self.index_sidecar_available,
                    supports_byte_range_206=self.supports_byte_range_206)


# --------------------------------------------------------------------------- #
# Section 7: native IFS/AIFS three-hour request path, kept apart from production
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class NativeECMWFThreeHourRequest:
    """A separate, G3-I-only IFS/AIFS request builder supporting this pilot's native
    three-hour IFS cadence (0,3,...,72). This is NOT a modification of the
    production `polymarket_scanner.v11.ecmwf_sources.ECMWFRequest`, which
    intentionally enforces `step % 6 == 0` for its own, already-reviewed live
    callers -- protocol Section 7 forbids downsampling IFS or altering that live
    caller to hide the incompatibility. This dataclass exists only so G3-I/G3-L
    manifest construction has a reviewed native-cadence request shape to freeze;
    it performs no network access and is imported by no production caller.
    """
    provider: str
    initialized_at: float
    step: int
    member: int
    grib_signature_sha256: str

    def __post_init__(self):
        require(self.provider in ('IFS', 'AIFS'), 'NATIVE_ECMWF_PROVIDER_IDENTITY')
        require(math.isfinite(self.initialized_at) and self.initialized_at > 0,
                'NATIVE_ECMWF_INITIALIZED_AT_FINITE')
        run = datetime.fromtimestamp(self.initialized_at, dt_timezone.utc)
        require(run.hour in (0, 6, 12, 18) and run.minute == 0 and run.second == 0
                and self.initialized_at % 1 == 0, 'NATIVE_ECMWF_INITIALIZATION_CYCLE')
        cadence = 3 if self.provider == 'IFS' else 6
        require(type(self.step) is int and 0 <= self.step <= 72 and self.step % cadence == 0,
                'NATIVE_ECMWF_STEP_BOUND')
        require(type(self.member) is int and 0 <= self.member <= 50, 'NATIVE_ECMWF_MEMBER_BOUND')
        require(is_sha(self.grib_signature_sha256), 'NATIVE_ECMWF_SIGNATURE_DIGEST')

    @property
    def hour_set(self):
        cadence = 3 if self.provider == 'IFS' else 6
        return tuple(range(0, 73, cadence))


# --------------------------------------------------------------------------- #
# Section 3: requested keys and the frozen capture manifest
# --------------------------------------------------------------------------- #

def _validate_requested_key(key):
    require(type(key) is tuple and len(key) == 5, 'REQUESTED_KEY_SHAPE')
    station_id, event_id, rule_id, target_date, family = key
    require(all(isinstance(v, str) and bool(v) for v in (station_id, event_id, rule_id, target_date)),
            'REQUESTED_KEY_FIELDS_REQUIRED')
    require(family in FAMILIES, 'REQUESTED_KEY_FAMILY')
    require(date.fromisoformat(target_date).isoformat() == target_date, 'REQUESTED_KEY_TARGET_DATE_FORMAT')
    return key


def gate2_trial_key(requested_key):
    """The Gate 2 trial key this requested key would map onto (protocol Section 3.3):
    HIGH/LOW share one trial per station/event/date, never two independent trials."""
    station_id, event_id, _rule_id, target_date, _family = requested_key
    return station_id, event_id, target_date


@dataclass(frozen=True)
class CaptureManifest:
    """The concrete, frozen launch-manifest schema required by protocol Section 3.
    A G3-I instance built and tested here uses only synthetic identities; the real
    private station/event/date values, their independent review, and the frozen
    manifest digest all live in a private store outside Git at G3-L (protocol
    Section 3.6), never in this repository.

    `manifest_id` is left `''` until `seal_manifest` computes it from every other
    field's canonical bytes -- a caller cannot forge an id independent of content.
    """
    manifest_id: str
    collector_commit_sha: str
    dossiers: tuple
    requested_keys: tuple
    decision_at: Timestamp
    acquisition_window_start_utc: float
    acquisition_window_end_utc: float
    feature_sealing_deadline_utc: float
    allowed_cycles: tuple
    max_run_age_seconds: int
    run_selection: str
    fallback_mode: str
    max_requests: int
    max_total_received_bytes: int
    min_interval_between_starts_seconds: float
    max_elapsed_seconds: float
    single_request_in_flight: bool
    max_request_deadline_seconds: float
    min_free_disk_bytes: int
    min_available_memory_bytes: int
    raw_directory_index_verified: bool
    ifs_three_hour_path_verified: bool
    financial_authority: bool = False
    promotion_authority: bool = False
    host_approved: bool = False

    def __post_init__(self):
        require(self.manifest_id == '' or is_sha(self.manifest_id), 'MANIFEST_ID_DIGEST_OR_UNSEALED')
        require(is_sha(self.collector_commit_sha), 'MANIFEST_COLLECTOR_COMMIT_REQUIRED')
        require(type(self.dossiers) is tuple and all(isinstance(d, SourceDossier) for d in self.dossiers)
                and {d.provider for d in self.dossiers} == set(VALID_PROVIDERS)
                and len(self.dossiers) == len(VALID_PROVIDERS), 'MANIFEST_DOSSIER_SET_COMPLETE')
        require(type(self.requested_keys) is tuple and bool(self.requested_keys) and
                len(self.requested_keys) <= 2 and
                len(set(self.requested_keys)) == len(self.requested_keys), 'MANIFEST_REQUESTED_KEYS_BOUND')
        for key in self.requested_keys:
            _validate_requested_key(key)
        require(len({k[0] for k in self.requested_keys}) == 1, 'MANIFEST_SINGLE_STATION_PILOT')
        require(len({k[3] for k in self.requested_keys}) == 1, 'MANIFEST_SINGLE_TARGET_DATE_PILOT')
        families = [k[4] for k in self.requested_keys]
        require(len(set(families)) == len(families), 'MANIFEST_ONE_EVENT_PER_FAMILY')
        require(isinstance(self.decision_at, Timestamp), 'MANIFEST_DECISION_AT_TYPE')
        require_trusted(self.decision_at, 'decision_at')
        for value in (self.acquisition_window_start_utc, self.acquisition_window_end_utc,
                      self.feature_sealing_deadline_utc):
            require(math.isfinite(value), 'MANIFEST_WINDOW_TIMESTAMP_FINITE')
        require(self.acquisition_window_start_utc < self.acquisition_window_end_utc <=
                self.feature_sealing_deadline_utc <= self.decision_at.conservative_lower_bound,
                'MANIFEST_WINDOW_MUST_PRECEDE_DECISION')
        require(self.allowed_cycles == (0,), 'MANIFEST_ALLOWED_CYCLES_FROZEN')
        require(self.max_run_age_seconds == 86400, 'MANIFEST_MAX_RUN_AGE_FROZEN')
        require(self.run_selection == 'LATEST_COMPLETE_READY', 'MANIFEST_RUN_SELECTION_FROZEN')
        require(self.fallback_mode == 'NONE', 'MANIFEST_FALLBACK_MODE_FROZEN')
        require(type(self.max_requests) is int and 0 < self.max_requests <= 3600,
                'MANIFEST_MAX_REQUESTS_CEILING')
        require(type(self.max_total_received_bytes) is int and
                0 < self.max_total_received_bytes <= 1024 ** 3, 'MANIFEST_MAX_BYTES_CEILING')
        require(self.min_interval_between_starts_seconds >= 2.0, 'MANIFEST_MIN_INTERVAL_CEILING')
        require(0 < self.max_elapsed_seconds <= 3 * 3600, 'MANIFEST_MAX_ELAPSED_CEILING')
        require(self.single_request_in_flight is True, 'MANIFEST_SINGLE_IN_FLIGHT_REQUIRED')
        require(0 < self.max_request_deadline_seconds <= 30, 'MANIFEST_REQUEST_DEADLINE_CEILING')
        require(self.min_free_disk_bytes >= 2 * 1024 ** 3, 'MANIFEST_MIN_FREE_DISK_FLOOR')
        require(self.min_available_memory_bytes >= 512 * 1024 ** 2, 'MANIFEST_MIN_AVAILABLE_MEMORY_FLOOR')
        require(self.financial_authority is False and self.promotion_authority is False and
                self.host_approved is False, 'MANIFEST_AUTHORITY_FLAGS_MUST_STAY_FALSE')
        require(type(self.raw_directory_index_verified) is bool and
                type(self.ifs_three_hour_path_verified) is bool,
                'MANIFEST_DRY_RUN_VERIFICATION_FLAGS_MUST_BE_EXPLICIT_BOOL')

    @property
    def launchable(self):
        """False until every P3-2 dry-run confirmation and every dossier's own
        launch_ready flag is explicitly True; an unknown origin-availability check
        never defaults to permitted (protocol Section 3: "a template with nulls is
        NOT launchable"). True here is necessary, not sufficient, for G3-L."""
        return (self.raw_directory_index_verified and self.ifs_three_hour_path_verified and
                all(d.launch_ready for d in self.dossiers) and
                self.financial_authority is False and self.promotion_authority is False and
                self.host_approved is False)

    @property
    def expected_raw_message_keys(self):
        """Every (provider, member, hour) raw message this manifest's cohort implies.
        HIGH/LOW requested keys share these raw captures rather than multiplying them
        (protocol Section 3: "HIGH/LOW share captures without becoming independent
        trials"), so this enumeration does not depend on `requested_keys` at all."""
        keys = []
        for dossier in self.dossiers:
            lo, hi = dossier.member_range
            for member in range(lo, hi + 1):
                for hour in dossier.native_hours:
                    keys.append((dossier.provider, member, hour))
        require(len(set(keys)) == len(keys), 'EXPECTED_RAW_MESSAGE_KEYS_MUST_BE_UNIQUE')
        return tuple(keys)

    @property
    def nominal_denominator(self):
        return len(self.expected_raw_message_keys)

    @property
    def feature_eligibility_keys(self):
        """requested_key x provider cohort for feature/eligibility accounting,
        distinct from the shared raw-message denominator above (protocol Section
        3.3/6: "never multiply sample counts by members, snapshots or shared
        HIGH/LOW features")."""
        return tuple((key, provider) for key in self.requested_keys for provider in VALID_PROVIDERS)


def manifest_bytes(manifest):
    """Canonical preimage of every manifest field except `manifest_id` itself."""
    require(isinstance(manifest, CaptureManifest), 'MANIFEST_BYTES_TYPE')
    return canonical(dict(
        collector_commit_sha=manifest.collector_commit_sha,
        dossiers=[d.manifest_payload() for d in sorted(manifest.dossiers, key=lambda d: d.provider)],
        requested_keys=[list(k) for k in manifest.requested_keys],
        decision_at=dict(utc=manifest.decision_at.utc, origin=manifest.decision_at.origin,
                          uncertainty_seconds=manifest.decision_at.uncertainty_seconds,
                          clock_health=manifest.decision_at.clock_health,
                          evidence_sha256=manifest.decision_at.evidence_sha256),
        acquisition_window_start_utc=manifest.acquisition_window_start_utc,
        acquisition_window_end_utc=manifest.acquisition_window_end_utc,
        feature_sealing_deadline_utc=manifest.feature_sealing_deadline_utc,
        allowed_cycles=list(manifest.allowed_cycles), max_run_age_seconds=manifest.max_run_age_seconds,
        run_selection=manifest.run_selection, fallback_mode=manifest.fallback_mode,
        max_requests=manifest.max_requests, max_total_received_bytes=manifest.max_total_received_bytes,
        min_interval_between_starts_seconds=manifest.min_interval_between_starts_seconds,
        max_elapsed_seconds=manifest.max_elapsed_seconds,
        single_request_in_flight=manifest.single_request_in_flight,
        max_request_deadline_seconds=manifest.max_request_deadline_seconds,
        min_free_disk_bytes=manifest.min_free_disk_bytes,
        min_available_memory_bytes=manifest.min_available_memory_bytes,
        raw_directory_index_verified=manifest.raw_directory_index_verified,
        ifs_three_hour_path_verified=manifest.ifs_three_hour_path_verified,
        financial_authority=manifest.financial_authority,
        promotion_authority=manifest.promotion_authority, host_approved=manifest.host_approved))


def seal_manifest(manifest):
    require(manifest.manifest_id == '', 'MANIFEST_ALREADY_SEALED')
    sealed_id = hashlib.sha256(manifest_bytes(manifest)).hexdigest()
    return dataclasses.replace(manifest, manifest_id=sealed_id)


# --------------------------------------------------------------------------- #
# Section 6: terminal reasons and the attempt ledger
# --------------------------------------------------------------------------- #

TERMINAL_REASONS = frozenset({
    'NOT_ATTEMPTED_BUDGET', 'NOT_ATTEMPTED_PROVIDER_HOLD', 'HTTP_404_AT_ATTEMPT',
    'HTTP_THROTTLED', 'PARTIAL_RESPONSE', 'INDEX_OBJECT_CONFLICT',
    'SOURCE_RELEASE_UNRESOLVED', 'DECODE_UNSUPPORTED', 'EXPECTED_MESSAGE_MISSING',
    'CLOCK_UNTRUSTED', 'LATE_FEATURE_READY', 'METADATA_UNAVAILABLE', 'RULE_CHANGED',
    'CAPTURE_ELIGIBLE_LABEL_PENDING',
})

# Stable primary-reason precedence when multiple reasons apply to the same key
# (protocol Section 6: "one stable primary reason under a precedence list frozen
# in the manifest"). Frozen here, not chosen per-run.
TERMINAL_PRECEDENCE = (
    'CLOCK_UNTRUSTED', 'SOURCE_RELEASE_UNRESOLVED', 'RULE_CHANGED',
    'NOT_ATTEMPTED_PROVIDER_HOLD', 'HTTP_THROTTLED', 'HTTP_404_AT_ATTEMPT',
    'INDEX_OBJECT_CONFLICT', 'PARTIAL_RESPONSE', 'DECODE_UNSUPPORTED',
    'EXPECTED_MESSAGE_MISSING', 'LATE_FEATURE_READY', 'METADATA_UNAVAILABLE',
    'NOT_ATTEMPTED_BUDGET', 'CAPTURE_ELIGIBLE_LABEL_PENDING',
)
assert set(TERMINAL_PRECEDENCE) == TERMINAL_REASONS


class AttemptLedger:
    """Append-only per-raw-message-key terminal-state ledger. Every frozen manifest
    key must reach exactly one terminal record before finalize(); finalize() proves
    the recorded keys exactly partition the manifest's own denominator -- no absent
    row, no duplicate (protocol Section 6). A key already recorded is refused, not
    overwritten: this is the ledger-level enforcement of the pilot's zero-retry rule.
    """

    def __init__(self, expected_keys):
        require(type(expected_keys) is tuple and len(set(expected_keys)) == len(expected_keys),
                'LEDGER_EXPECTED_KEYS_UNIQUE')
        self._expected = frozenset(expected_keys)
        self._records = {}

    def record(self, key, *reasons):
        require(key in self._expected, 'LEDGER_KEY_NOT_IN_MANIFEST')
        require(key not in self._records, 'LEDGER_KEY_ALREADY_TERMINAL_NO_RETRY')
        require(bool(reasons) and all(r in TERMINAL_REASONS for r in reasons), 'LEDGER_REASON_UNKNOWN')
        require(len(set(reasons)) == len(reasons), 'LEDGER_REASON_DUPLICATE')
        self._records[key] = tuple(reasons)

    def reasons(self, key):
        return self._records[key]

    def primary_reason(self, key):
        recorded = self._records[key]
        for candidate in TERMINAL_PRECEDENCE:
            if candidate in recorded:
                return candidate
        raise AssertionError('unreachable: reasons already validated against TERMINAL_REASONS')

    @property
    def pending(self):
        return frozenset(self._expected - self._records.keys())

    def finalize(self):
        require(not self.pending, 'LEDGER_INCOMPLETE_PARTITION')
        return MappingProxyType(dict(self._records))


# --------------------------------------------------------------------------- #
# Section 4: persistent per-origin restriction ledger
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RestrictionRecord:
    origin: str
    reason: str
    observed_at_utc: float
    scope: str

    def __post_init__(self):
        require(isinstance(self.origin, str) and bool(self.origin), 'RESTRICTION_ORIGIN_REQUIRED')
        require(self.reason in ('HTTP_401', 'HTTP_403', 'HTTP_429', 'HTTP_503',
                'PROVIDER_DENIAL', 'RETRY_AFTER'), 'RESTRICTION_REASON_ENUM')
        require(self.scope in ('ORIGIN', 'WINDOW'), 'RESTRICTION_SCOPE_ENUM')
        require((self.reason in ('HTTP_401', 'HTTP_403')) == (self.scope == 'ORIGIN'),
                'RESTRICTION_SCOPE_REASON_CONSISTENCY')
        require(math.isfinite(self.observed_at_utc), 'RESTRICTION_TIMESTAMP_FINITE')

    def payload(self):
        return dict(origin=self.origin, reason=self.reason,
                    observed_at_utc=self.observed_at_utc, scope=self.scope)


class RestrictionLedger:
    """Persistent, append-only per-origin restriction/cooldown ledger meant to be
    shared across restarts (protocol Section 4). 401/403 stop that origin for the
    remainder of this pilot; 429/503/explicit denial/Retry-After stop that origin
    for the remainder of the acquisition window only. No mirror, rotation, or
    alternative client may evade a recorded restriction; resume() requires the
    caller to prove the persisted content hash matches before any restriction is
    trusted, and never resets or truncates prior restrictions."""

    def __init__(self):
        self._records = []

    def record(self, restriction):
        require(isinstance(restriction, RestrictionRecord), 'RESTRICTION_RECORD_TYPE')
        self._records.append(restriction)

    def is_restricted(self, origin, *, now_utc, window_end_utc):
        for r in self._records:
            if r.origin != origin:
                continue
            if r.scope == 'ORIGIN':
                return True
            if r.scope == 'WINDOW' and now_utc <= window_end_utc:
                return True
        return False

    @property
    def records(self):
        return tuple(self._records)

    def content_sha256(self):
        return hashlib.sha256(canonical([r.payload() for r in self._records])).hexdigest()

    @classmethod
    def resume(cls, records, *, expected_sha256):
        """Rehydrate a persisted ledger only after verifying its own recorded hash;
        never resets or truncates prior restrictions on resume (protocol Section 4:
        "verify existing hashes first... never reset counters")."""
        ledger = cls()
        for r in records:
            ledger.record(r)
        require(ledger.content_sha256() == expected_sha256,
                'RESTRICTION_LEDGER_HASH_MISMATCH_ON_RESUME')
        return ledger


# --------------------------------------------------------------------------- #
# Section 4: budget ceilings
# --------------------------------------------------------------------------- #

class BudgetCeilingExceeded(ValueError):
    pass


# Duplicated BY VALUE, not by import, from the already-reviewed production bounds
# (`polymarket_scanner/v11/ecmwf_sources.py::MAX_INDEX_BYTES` and
# `polymarket_scanner/v11/model_panel.py::MAX_RAW_BYTES`). This module intentionally
# imports no production/network-capable service (protocol Section 7), so these
# ceilings are pinned here as named constants rather than the protocol's own looser
# nominal figures (3 MiB/16 MiB) -- the protocol itself requires "the stricter
# existing parser/field bound where present" and that "this document cannot loosen
# it" (Section 4). If the real production bounds are ever tightened further, this
# module's ceiling must be re-reviewed and updated to match; it must never be looser.
_EXISTING_MAX_INDEX_BYTES = 3 * 1024 * 1024
_EXISTING_MAX_FIELD_BYTES = 4 * 1024 * 1024
_EXISTING_GEFS_MAX_FIELD_BYTES = 64 * 1024  # grib_fields.MAX_BYTES


class BudgetTracker:
    """Enforces the pilot's hard ceilings (protocol Section 4), constructed with the
    STRICTER of the protocol's own numbers and any already-existing, already-reviewed
    tighter provider bound -- e.g. ECMWF's `MAX_INDEX_BYTES` (3 MiB) / `MAX_RAW_BYTES`
    (4 MiB) in `polymarket_scanner/v11/ecmwf_sources.py` and `model_panel.py`, or
    GEFS's `grib_fields.MAX_BYTES` (64 KiB) -- so this module can never loosen an
    existing bound (the protocol document's own explicit constraint). Performs no
    network access itself; callers report each attempt's start/outcome to it."""

    def __init__(self, *, max_requests=3600, max_total_received_bytes=1024 ** 3,
                 min_interval_seconds=2.0, max_elapsed_seconds=3 * 3600.,
                 max_index_bytes=_EXISTING_MAX_INDEX_BYTES, max_field_bytes=_EXISTING_MAX_FIELD_BYTES):
        require(type(max_requests) is int and 0 < max_requests <= 3600, 'BUDGET_MAX_REQUESTS_CEILING')
        require(0 < max_total_received_bytes <= 1024 ** 3, 'BUDGET_MAX_BYTES_CEILING')
        require(min_interval_seconds >= 2.0, 'BUDGET_MIN_INTERVAL_CEILING')
        require(0 < max_elapsed_seconds <= 3 * 3600, 'BUDGET_MAX_ELAPSED_CEILING')
        require(0 < max_index_bytes <= _EXISTING_MAX_INDEX_BYTES,
                'BUDGET_MAX_INDEX_BYTES_CANNOT_LOOSEN_PROTOCOL')
        require(0 < max_field_bytes <= _EXISTING_MAX_FIELD_BYTES,
                'BUDGET_MAX_FIELD_BYTES_CANNOT_LOOSEN_PROTOCOL')
        self.max_requests = max_requests
        self.max_total_received_bytes = max_total_received_bytes
        self.min_interval_seconds = min_interval_seconds
        self.max_elapsed_seconds = max_elapsed_seconds
        self.max_index_bytes = max_index_bytes
        self.max_field_bytes = max_field_bytes
        self.request_count = 0
        self.total_received_bytes = 0
        self.window_started_at = None
        self.last_request_started_at = None
        self.in_flight = False

    def start_window(self, now_monotonic):
        require(self.window_started_at is None, 'BUDGET_WINDOW_ALREADY_STARTED')
        self.window_started_at = now_monotonic

    def check_before_request(self, *, now_monotonic, provider=None,
                             index_bytes=None, field_bytes=None):
        require(self.window_started_at is not None, 'BUDGET_WINDOW_NOT_STARTED')
        if self.in_flight:
            raise BudgetCeilingExceeded('BUDGET_SINGLE_REQUEST_IN_FLIGHT_VIOLATION')
        if self.request_count >= self.max_requests:
            raise BudgetCeilingExceeded('NOT_ATTEMPTED_BUDGET')
        if now_monotonic - self.window_started_at >= self.max_elapsed_seconds:
            raise BudgetCeilingExceeded('NOT_ATTEMPTED_BUDGET')
        if (self.last_request_started_at is not None and
                now_monotonic - self.last_request_started_at < self.min_interval_seconds):
            raise BudgetCeilingExceeded('NOT_ATTEMPTED_BUDGET')
        if index_bytes is not None and index_bytes > self.max_index_bytes:
            raise BudgetCeilingExceeded('NOT_ATTEMPTED_BUDGET')
        if field_bytes is not None:
            require(provider in VALID_PROVIDERS, 'BUDGET_FIELD_PROVIDER_REQUIRED')
            provider_ceiling = (_EXISTING_GEFS_MAX_FIELD_BYTES if provider == 'GEFS'
                                else _EXISTING_MAX_FIELD_BYTES)
            if field_bytes > min(self.max_field_bytes, provider_ceiling):
                raise BudgetCeilingExceeded('NOT_ATTEMPTED_BUDGET')

    def begin_request(self, now_monotonic, *, provider=None, index_bytes=None,
                      field_bytes=None):
        self.check_before_request(now_monotonic=now_monotonic, provider=provider,
                                  index_bytes=index_bytes, field_bytes=field_bytes)
        self.in_flight = True
        self.last_request_started_at = now_monotonic
        self.request_count += 1

    def complete_request(self, received_bytes):
        require(self.in_flight, 'BUDGET_NO_REQUEST_IN_FLIGHT')
        require(type(received_bytes) is int and received_bytes >= 0, 'BUDGET_RECEIVED_BYTES_TYPE')
        self.in_flight = False
        if self.total_received_bytes + received_bytes > self.max_total_received_bytes:
            raise BudgetCeilingExceeded('NOT_ATTEMPTED_BUDGET')
        self.total_received_bytes += received_bytes


# --------------------------------------------------------------------------- #
# P3-3: dry-run feasibility estimator using real observed message sizes
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class FeasibilityPlan:
    launchable_at_full_denominator: bool
    nominal_denominator: int
    estimated_total_bytes: int
    ceiling_bytes: int
    fallback_denominator: int
    fallback_keys: tuple
    provider_message_size_estimate_bytes: object

    def __post_init__(self):
        require(type(self.nominal_denominator) is int and self.nominal_denominator > 0,
                'FEASIBILITY_DENOMINATOR')
        require(0 <= self.fallback_denominator <= self.nominal_denominator, 'FEASIBILITY_FALLBACK_BOUND')
        require(len(self.fallback_keys) == self.fallback_denominator, 'FEASIBILITY_FALLBACK_KEY_COUNT')
        require((self.fallback_denominator == self.nominal_denominator) ==
                self.launchable_at_full_denominator, 'FEASIBILITY_FULL_DENOMINATOR_CONSISTENCY')


def _fallback_priority(key):
    """Deterministic fallback priority, frozen here rather than chosen after seeing
    any response: within each provider, the control member (0) and lowest forecast
    hours carry the most standalone predictive information per byte and are kept
    first. This is itself the "prespecified bounded feasibility attempt" the
    protocol requires when the full denominator does not fit (Section 3)."""
    provider, member, hour = key
    return provider, member != 0, member, hour


def estimate_feasibility(manifest, *, provider_message_size_estimate_bytes, ceiling_bytes=1024 ** 3):
    """Dry-run budget estimator (P3-3). Uses REAL OBSERVED per-provider message-size
    estimates the caller supplies (from dossier/historical evidence), not an assumed
    nominal figure, to decide whether the manifest's full raw-message denominator is
    feasible under the byte ceiling, and to size the prespecified bounded fallback
    the protocol requires when it is not (Section 3: "record a prespecified bounded
    feasibility attempt; do not increase budgets mid-window or reduce the
    denominator"). Performs no I/O and inspects no response content."""
    require(isinstance(manifest, CaptureManifest), 'FEASIBILITY_MANIFEST_TYPE')
    keys = manifest.expected_raw_message_keys
    require(set(provider_message_size_estimate_bytes) == set(VALID_PROVIDERS),
            'FEASIBILITY_SIZE_ESTIMATE_MUST_COVER_ALL_PROVIDERS')
    for size in provider_message_size_estimate_bytes.values():
        require(type(size) is int and size > 0, 'FEASIBILITY_SIZE_ESTIMATE_POSITIVE')
    total = sum(provider_message_size_estimate_bytes[provider] for provider, _m, _h in keys)
    if total <= ceiling_bytes:
        return FeasibilityPlan(True, len(keys), total, ceiling_bytes, len(keys), keys,
                                dict(provider_message_size_estimate_bytes))
    running = 0
    fallback_set = set()
    for key in sorted(keys, key=_fallback_priority):
        size = provider_message_size_estimate_bytes[key[0]]
        if running + size > ceiling_bytes:
            continue
        running += size
        fallback_set.add(key)
    fallback_keys = tuple(k for k in keys if k in fallback_set)
    return FeasibilityPlan(False, len(keys), total, ceiling_bytes, len(fallback_keys), fallback_keys,
                            dict(provider_message_size_estimate_bytes))


# --------------------------------------------------------------------------- #
# P3-2: origin index/byte-range dry-run confirmation, caller-supplied transport only
# --------------------------------------------------------------------------- #

class Transport:
    """Caller-supplied network transport interface. This module defines no default,
    real implementation and performs no network access itself; G3-L must supply and
    independently review its own transport before any actual dry-run confirmation
    against a live origin. This repository's own tests use only a synthetic
    subclass -- no request ever reaches a real host from this module."""

    def head_or_range_probe(self, url):  # pragma: no cover - interface only
        raise NotImplementedError


@dataclass(frozen=True)
class IndexAvailabilityResult:
    origin: str
    index_sidecar_available: bool
    supports_byte_range_206: bool
    probed_at_utc: float


def check_index_availability(transport, origin_url, *, now_utc):
    """Runs the P3-2 dry-run confirmation the review asked for -- that a candidate
    raw-directory origin actually publishes a byte-range-compatible index sidecar,
    as opposed to the different, already-reviewed production GEFS CGI filter
    endpoint -- using a caller-supplied transport. Without a transport there is no
    result at all (NOT_VERIFIED), never an assumed pass; a CaptureManifest's own
    `launchable` stays False until a caller explicitly records a True result onto
    the corresponding SourceDossier's `index_sidecar_available` /
    `supports_byte_range_206` fields."""
    require(isinstance(transport, Transport), 'INDEX_AVAILABILITY_TRANSPORT_REQUIRED')
    require(isinstance(origin_url, str) and origin_url.startswith('https://'),
            'INDEX_AVAILABILITY_ORIGIN_HTTPS')
    probe = transport.head_or_range_probe(origin_url)
    require(type(probe) is dict and {'index_sidecar_available', 'supports_byte_range_206'} <= set(probe),
            'INDEX_AVAILABILITY_PROBE_SHAPE')
    return IndexAvailabilityResult(origin_url, bool(probe['index_sidecar_available']),
                                    bool(probe['supports_byte_range_206']), now_utc)


# --------------------------------------------------------------------------- #
# Final stop-gate and causal-clock reuse
# --------------------------------------------------------------------------- #

def require_launch_prerequisites(manifest):
    """The explicit stop-gate this G3-I collector implementation enforces before any
    caller could even attempt network acquisition: every dry-run confirmation must
    be recorded True and every authority flag must be False. This function performs
    no network access and starts no capture; it only exercises the manifest's own
    `.launchable` computation end-to-end. Passing this check is NOT G3-L approval --
    it is necessary, not sufficient; G3-L still requires independent review of the
    frozen private manifest digest (protocol Section 1)."""
    require(isinstance(manifest, CaptureManifest), 'LAUNCH_PREREQ_MANIFEST_TYPE')
    require(manifest.launchable, 'MANIFEST_NOT_LAUNCHABLE')
    return True


def causal_feature_eligible(feature_ready_at, decision_at):
    """Section 5's causal-eligibility rule: the upper bound of a feature dependency's
    receipt/feature-ready time must be no later than the lower bound of the fixed
    decision. Reuses the already-reviewed Timestamp/trusted-origin machinery from
    the accepted Gate 2 contract (`tools.v11_trajectory_contract`) rather than
    redefining clock trust here (protocol Section 7: "reuse proven parsers only
    where their semantics match")."""
    require_trusted(feature_ready_at, 'feature_ready_at')
    require_trusted(decision_at, 'decision_at')
    return feature_ready_at.conservative_upper_bound <= decision_at.conservative_lower_bound
