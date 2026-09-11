from __future__ import annotations

"""Prospective cutoff certification for exact WRH settlement-state labels.

A single WRH snapshot taken after the contract's correction cutoff is insufficient
for exact historical labeling: target-day observations may have been revised after
the first following-date Hourly Data row, and the current page does not expose that
revision history.

The safe path is prospective transition capture.  We require one pinned WRH snapshot
before the first following-date eligible row can exist and a second prompt snapshot
after that row appears.  The complete selected target-day state must be identical on
both sides.  If it changed, timing of the correction is ambiguous and certification
fails closed rather than choosing the earlier or later state.

This layer certifies only the exact source/rule-state cutoff.  It never grants trade
or financial authority; market-bucket labeling is performed separately and still
requires certified contract rule semantics.
"""

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from .weather_only_wrh import (
    WRH_HOURLY_PROFILE,
    WRH_SNAPSHOT_ADAPTER_VERSION,
    WRH_SOURCE_ROLE,
    WRH_SYNOPTIC_ENDPOINT,
    WRH_VIEWER_SCRIPT_SHA256,
    WRH_VIEWER_SCRIPT_URL,
    WRHSourceError,
    WRHSourceSnapshot,
    _hash_payload,
    _snapshot_evidence_payload,
)


WRH_FINALITY_ADAPTER_VERSION = "nws_wrh_first_following_transition_v1_bracketed_unchanged_target"
WRH_FINALITY_SOURCE_ROLE = "NWS_WRH_EXACT_RULE_STATE_CUTOFF"


@dataclass(frozen=True, slots=True)
class WRHFinalityPolicy:
    policy_id: str
    max_transition_gap_seconds: int
    max_following_row_age_seconds: int

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("policy_id is required")
        if self.policy_id != self.policy_id.strip():
            raise ValueError("policy_id must not contain surrounding whitespace")
        for name, value in (
            ("max_transition_gap_seconds", self.max_transition_gap_seconds),
            ("max_following_row_age_seconds", self.max_following_row_age_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class WRHFinalizedRuleState:
    adapter: str
    source_role: str
    source_profile: str
    finality_policy_id: str
    station: str
    timezone: str
    target_date: date
    previous_snapshot_sha256: str
    current_snapshot_sha256: str
    target_state_sha256: str
    previous_received_at: float
    current_received_at: float
    transition_gap_seconds: float
    first_following_observation_time: str
    first_following_row_age_seconds: float
    target_display_temperatures_f: tuple[int, ...]
    target_high_f: int
    target_low_f: int
    finality_evidence_sha256: str
    correction_state_reconstructable: bool = field(init=False, default=True)
    calibration_label_authority: bool = field(init=False, default=True)
    settlement_label_authority: bool = field(init=False, default=True)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        value = asdict(self)
        value["target_date"] = self.target_date.isoformat()
        return value


def _sha256(value: object, code: str) -> str:
    text = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise WRHSourceError(code)
    return text


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WRHSourceError(code)
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise WRHSourceError(code)
    return number


def _target_state_digest(snapshot: WRHSourceSnapshot) -> str:
    return _hash_payload({
        "station": snapshot.station,
        "target_date": snapshot.target_date.isoformat(),
        "rows": [row.as_dict() for row in snapshot.target_rows],
    })


def _validate_snapshot(snapshot: object, prefix: str) -> WRHSourceSnapshot:
    if not isinstance(snapshot, WRHSourceSnapshot):
        raise WRHSourceError(f"WRH_{prefix}_SNAPSHOT_TYPE_INVALID")
    if snapshot.adapter != WRH_SNAPSHOT_ADAPTER_VERSION:
        raise WRHSourceError(f"WRH_{prefix}_ADAPTER_MISMATCH")
    if snapshot.source_role != WRH_SOURCE_ROLE or snapshot.source_profile != WRH_HOURLY_PROFILE:
        raise WRHSourceError(f"WRH_{prefix}_SOURCE_PROFILE_MISMATCH")
    if snapshot.viewer_script_url != WRH_VIEWER_SCRIPT_URL or snapshot.viewer_script_sha256 != WRH_VIEWER_SCRIPT_SHA256:
        raise WRHSourceError(f"WRH_{prefix}_VIEWER_IDENTITY_MISMATCH")
    if snapshot.source_endpoint != WRH_SYNOPTIC_ENDPOINT:
        raise WRHSourceError(f"WRH_{prefix}_SOURCE_ENDPOINT_MISMATCH")
    if snapshot.normalized_network != "ASOS/AWOS":
        raise WRHSourceError(f"WRH_{prefix}_NETWORK_UNSUPPORTED")
    if snapshot.query_start_date > snapshot.target_date or snapshot.query_end_date < snapshot.target_date + timedelta(days=1):
        raise WRHSourceError(f"WRH_{prefix}_QUERY_COVERAGE_INVALID")
    _finite(snapshot.received_at, f"WRH_{prefix}_RECEIVED_AT_INVALID")
    supplied_evidence = _sha256(snapshot.evidence_sha256, f"WRH_{prefix}_EVIDENCE_SHA_INVALID")
    if supplied_evidence != _hash_payload(_snapshot_evidence_payload(snapshot)):
        raise WRHSourceError(f"WRH_{prefix}_EVIDENCE_DIGEST_MISMATCH")
    supplied_target = _sha256(snapshot.target_state_sha256, f"WRH_{prefix}_TARGET_STATE_SHA_INVALID")
    if supplied_target != _target_state_digest(snapshot):
        raise WRHSourceError(f"WRH_{prefix}_TARGET_STATE_DIGEST_MISMATCH")
    _sha256(snapshot.source_payload_sha256, f"WRH_{prefix}_SOURCE_PAYLOAD_SHA_INVALID")

    expected_target = tuple(row for row in snapshot.selected_rows if row.local_date == snapshot.target_date)
    following = tuple(row for row in snapshot.selected_rows if row.local_date > snapshot.target_date)
    expected_following = following[0] if following else None
    if snapshot.target_rows != expected_target:
        raise WRHSourceError(f"WRH_{prefix}_TARGET_ROWS_INCONSISTENT")
    if snapshot.first_following_row != expected_following:
        raise WRHSourceError(f"WRH_{prefix}_FOLLOWING_ROW_INCONSISTENT")
    values = tuple(row.displayed_temp_f for row in snapshot.target_rows if row.displayed_temp_f is not None)
    if snapshot.target_display_temperatures_f != values:
        raise WRHSourceError(f"WRH_{prefix}_TARGET_TEMPERATURES_INCONSISTENT")
    expected_high = max(values) if values else None
    expected_low = min(values) if values else None
    if snapshot.target_high_f != expected_high or snapshot.target_low_f != expected_low:
        raise WRHSourceError(f"WRH_{prefix}_TARGET_EXTREME_INCONSISTENT")

    if any((
        snapshot.correction_state_reconstructable,
        snapshot.calibration_label_authority,
        snapshot.settlement_label_authority,
        snapshot.financial_authority,
    )):
        raise WRHSourceError(f"WRH_{prefix}_SNAPSHOT_AUTHORITY_BOUNDARY_BROKEN")
    return snapshot


def _finality_digest_payload(state: WRHFinalizedRuleState) -> dict:
    return {
        "adapter": state.adapter,
        "source_role": state.source_role,
        "source_profile": state.source_profile,
        "finality_policy_id": state.finality_policy_id,
        "station": state.station,
        "timezone": state.timezone,
        "target_date": state.target_date.isoformat(),
        "previous_snapshot_sha256": state.previous_snapshot_sha256,
        "current_snapshot_sha256": state.current_snapshot_sha256,
        "target_state_sha256": state.target_state_sha256,
        "previous_received_at": state.previous_received_at,
        "current_received_at": state.current_received_at,
        "transition_gap_seconds": state.transition_gap_seconds,
        "first_following_observation_time": state.first_following_observation_time,
        "first_following_row_age_seconds": state.first_following_row_age_seconds,
        "target_display_temperatures_f": state.target_display_temperatures_f,
        "target_high_f": state.target_high_f,
        "target_low_f": state.target_low_f,
    }


def certify_wrh_first_following_transition(
    previous: WRHSourceSnapshot,
    current: WRHSourceSnapshot,
    *,
    policy: WRHFinalityPolicy,
) -> WRHFinalizedRuleState:
    """Certify the first-following-row correction cutoff from two snapshots.

    The previous snapshot must be captured before the timestamp of the first
    following-date Hourly row and contain no such row.  The current snapshot must be
    captured promptly after that row's observation timestamp.  Most importantly,
    the complete selected target-day row state must be byte-identical by digest on
    both sides of the transition.
    """
    before = _validate_snapshot(previous, "PREVIOUS")
    after = _validate_snapshot(current, "CURRENT")
    if not isinstance(policy, WRHFinalityPolicy):
        raise WRHSourceError("WRH_FINALITY_POLICY_INVALID")

    identity_before = (
        before.station,
        before.target_date,
        before.timezone,
        before.raw_network,
        before.normalized_network,
        before.source_profile,
        before.viewer_script_sha256,
        before.source_endpoint,
    )
    identity_after = (
        after.station,
        after.target_date,
        after.timezone,
        after.raw_network,
        after.normalized_network,
        after.source_profile,
        after.viewer_script_sha256,
        after.source_endpoint,
    )
    if identity_before != identity_after:
        raise WRHSourceError("WRH_FINALITY_SOURCE_IDENTITY_MISMATCH")
    if before.first_following_row is not None:
        raise WRHSourceError("WRH_FINALITY_PREVIOUS_ALREADY_CROSSED")
    if after.first_following_row is None:
        raise WRHSourceError("WRH_FINALITY_FOLLOWING_ROW_NOT_OBSERVED")
    if after.first_following_row.local_date != after.target_date + timedelta(days=1):
        raise WRHSourceError("WRH_FINALITY_FIRST_FOLLOWING_DATE_NOT_NEXT_DAY")
    if before.target_state_sha256 != after.target_state_sha256:
        raise WRHSourceError("WRH_FINALITY_TARGET_STATE_CHANGED_ACROSS_CUTOFF")
    if not after.target_display_temperatures_f:
        # The separate contract no-data/fallback path is not implemented here.
        raise WRHSourceError("WRH_FINALITY_TARGET_TEMPERATURE_DATA_MISSING")

    previous_received = _finite(before.received_at, "WRH_FINALITY_PREVIOUS_RECEIVED_AT_INVALID")
    current_received = _finite(after.received_at, "WRH_FINALITY_CURRENT_RECEIVED_AT_INVALID")
    if current_received <= previous_received:
        raise WRHSourceError("WRH_FINALITY_SNAPSHOT_TIME_ORDER_INVALID")
    transition_gap = current_received - previous_received
    if transition_gap > policy.max_transition_gap_seconds:
        raise WRHSourceError("WRH_FINALITY_TRANSITION_GAP_EXCEEDED")

    first_following_timestamp = after.first_following_row.observation_time_local.timestamp()
    if previous_received >= first_following_timestamp:
        # We intentionally require a poll before the observation time itself. This is
        # stricter than merely seeing the row absent and eliminates publication-delay
        # ambiguity from the exact-label path.
        raise WRHSourceError("WRH_FINALITY_PREVIOUS_NOT_BEFORE_FOLLOWING_OBSERVATION")
    if current_received < first_following_timestamp:
        raise WRHSourceError("WRH_FINALITY_CURRENT_PREDATES_FOLLOWING_OBSERVATION")
    row_age = current_received - first_following_timestamp
    if row_age > policy.max_following_row_age_seconds:
        raise WRHSourceError("WRH_FINALITY_FOLLOWING_ROW_TOO_OLD")

    shell = WRHFinalizedRuleState(
        adapter=WRH_FINALITY_ADAPTER_VERSION,
        source_role=WRH_FINALITY_SOURCE_ROLE,
        source_profile=after.source_profile,
        finality_policy_id=policy.policy_id,
        station=after.station,
        timezone=after.timezone,
        target_date=after.target_date,
        previous_snapshot_sha256=before.evidence_sha256,
        current_snapshot_sha256=after.evidence_sha256,
        target_state_sha256=after.target_state_sha256,
        previous_received_at=previous_received,
        current_received_at=current_received,
        transition_gap_seconds=transition_gap,
        first_following_observation_time=after.first_following_row.observation_time_local.isoformat(),
        first_following_row_age_seconds=row_age,
        target_display_temperatures_f=after.target_display_temperatures_f,
        target_high_f=int(after.target_high_f),
        target_low_f=int(after.target_low_f),
        finality_evidence_sha256="0" * 64,
    )
    digest = _hash_payload(_finality_digest_payload(shell))
    return WRHFinalizedRuleState(
        **{
            name: getattr(shell, name)
            for name, definition in shell.__dataclass_fields__.items()
            if definition.init and name != "finality_evidence_sha256"
        },
        finality_evidence_sha256=digest,
    )
