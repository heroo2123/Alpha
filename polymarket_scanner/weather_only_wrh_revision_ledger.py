from __future__ import annotations

"""Revision-aware Layer-1 evidence for same-day WRH observations.

A changing source row is a *new version of the same observation*, not another vote.
This module consumes a prospective sequence of digest-valid complete WRH snapshots,
keys observations by their UTC instant, preserves every visible superseded version
(including deletions), and derives O(t) only from the latest active version.

This explicitly handles A -> B -> A histories: all three versions remain auditable,
but the final A is counted once.  A row that disappears from a later complete source
snapshot receives a tombstone version and is no longer part of O(t).

The ledger is an as-of research artifact only.  It does not solve R26 exact cutoff
history: polling still cannot reveal revisions that occurred entirely between polls.
No settlement, calibration-label, delivery or financial authority is granted.
"""

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field

from .weather_only_conditioned_wrh import (
    WRHObservedAsOfEvidence,
    build_wrh_observed_extreme_asof,
)
from .weather_only_contracts import CompiledWeatherEvent
from .weather_only_wrh import WRHHourlyRow, WRHSourceSnapshot
from .weather_only_wrh_finality import _validate_snapshot


WRH_REVISION_LEDGER_VERSION = "weather_wrh_revision_ledger_v1_supersession_tombstones"


class WRHRevisionLedgerError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WRHRevisionLedgerError(code)
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise WRHRevisionLedgerError(code)
    return number


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise WRHRevisionLedgerError("WRH_LEDGER_JSON_INVALID") from None


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _instant_key(row: WRHHourlyRow) -> str:
    return row.observation_time_local.timestamp().hex()


def _row_state(row: WRHHourlyRow) -> dict:
    return {
        "observation_time_raw": row.observation_time_raw,
        "observation_time_epoch": row.observation_time_local.timestamp(),
        "local_date": row.local_date.isoformat(),
        "minute": row.minute,
        "row_kind": row.row_kind,
        "raw_temp_f": row.raw_temp_f,
        "displayed_temp_f": row.displayed_temp_f,
        "sea_level_pressure_dataset_present": row.sea_level_pressure_dataset_present,
        "sea_level_pressure_nonnull": row.sea_level_pressure_nonnull,
        "metar": row.metar,
    }


@dataclass(frozen=True, slots=True)
class WRHObservationRevision:
    observation_key: str
    version_number: int
    became_visible_at: float
    present: bool
    row_state_sha256: str | None
    displayed_temp_f: int | None
    source_snapshot_sha256: str
    supersedes_revision_sha256: str | None
    revision_sha256: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WRHRevisionLedgerEvidence:
    version: str
    station: str
    target_date: str
    timezone: str
    as_of: float
    source_snapshot_sha256s: tuple[str, ...]
    revisions: tuple[WRHObservationRevision, ...]
    active_revision_sha256s: tuple[str, ...]
    active_observation_count: int
    superseded_revision_count: int
    tombstone_count: int
    observed_asof: WRHObservedAsOfEvidence
    evidence_sha256: str
    polling_history_complete_between_snapshots: bool = field(init=False, default=False)
    settlement_authority: bool = field(init=False, default=False)
    calibration_label_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "station": self.station,
            "target_date": self.target_date,
            "timezone": self.timezone,
            "as_of": self.as_of,
            "source_snapshot_sha256s": list(self.source_snapshot_sha256s),
            "revisions": [revision.as_dict() for revision in self.revisions],
            "active_revision_sha256s": list(self.active_revision_sha256s),
            "active_observation_count": self.active_observation_count,
            "superseded_revision_count": self.superseded_revision_count,
            "tombstone_count": self.tombstone_count,
            "observed_asof": self.observed_asof.as_dict(),
            "evidence_sha256": self.evidence_sha256,
            "polling_history_complete_between_snapshots": self.polling_history_complete_between_snapshots,
            "settlement_authority": self.settlement_authority,
            "calibration_label_authority": self.calibration_label_authority,
            "financial_authority": self.financial_authority,
        }


def _revision_payload(revision: WRHObservationRevision) -> dict:
    value = revision.as_dict()
    value.pop("revision_sha256", None)
    return value


def _ledger_payload(ledger: WRHRevisionLedgerEvidence) -> dict:
    return {
        "version": ledger.version,
        "station": ledger.station,
        "target_date": ledger.target_date,
        "timezone": ledger.timezone,
        "as_of": ledger.as_of,
        "source_snapshot_sha256s": ledger.source_snapshot_sha256s,
        "revision_sha256s": tuple(revision.revision_sha256 for revision in ledger.revisions),
        "active_revision_sha256s": ledger.active_revision_sha256s,
        "active_observation_count": ledger.active_observation_count,
        "superseded_revision_count": ledger.superseded_revision_count,
        "tombstone_count": ledger.tombstone_count,
        "observed_asof_evidence_sha256": ledger.observed_asof.evidence_sha256,
        "polling_history_complete_between_snapshots": ledger.polling_history_complete_between_snapshots,
        "settlement_authority": ledger.settlement_authority,
        "calibration_label_authority": ledger.calibration_label_authority,
        "financial_authority": ledger.financial_authority,
    }


def _validated_sequence(
    snapshots: tuple[WRHSourceSnapshot, ...], *, as_of: float
) -> tuple[WRHSourceSnapshot, ...]:
    if not snapshots:
        raise WRHRevisionLedgerError("WRH_LEDGER_SNAPSHOTS_REQUIRED")
    validated: list[WRHSourceSnapshot] = []
    for snapshot in snapshots:
        try:
            value = _validate_snapshot(snapshot, "LEDGER")
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            raise WRHRevisionLedgerError(f"WRH_LEDGER_SNAPSHOT_INVALID:{code}") from exc
        if value.received_at > as_of:
            raise WRHRevisionLedgerError("WRH_LEDGER_SNAPSHOT_AFTER_AS_OF")
        validated.append(value)

    identity = (
        validated[0].station,
        validated[0].target_date,
        validated[0].timezone,
        validated[0].response_temperature_unit,
        validated[0].raw_network,
        validated[0].normalized_network,
        validated[0].source_profile,
        validated[0].viewer_script_sha256,
        validated[0].source_endpoint,
    )
    prior_receipt = -1.0
    seen_snapshot_sha: set[str] = set()
    for value in validated:
        current_identity = (
            value.station,
            value.target_date,
            value.timezone,
            value.response_temperature_unit,
            value.raw_network,
            value.normalized_network,
            value.source_profile,
            value.viewer_script_sha256,
            value.source_endpoint,
        )
        if current_identity != identity:
            raise WRHRevisionLedgerError("WRH_LEDGER_SOURCE_IDENTITY_DRIFT")
        if value.received_at <= prior_receipt:
            raise WRHRevisionLedgerError("WRH_LEDGER_SNAPSHOT_ORDER_INVALID")
        if value.evidence_sha256 in seen_snapshot_sha:
            raise WRHRevisionLedgerError("WRH_LEDGER_SNAPSHOT_DUPLICATE")
        prior_receipt = value.received_at
        seen_snapshot_sha.add(value.evidence_sha256)
    return tuple(validated)


def build_wrh_revision_ledger(
    snapshots: tuple[WRHSourceSnapshot, ...],
    compiled: CompiledWeatherEvent,
    *,
    as_of: float,
    max_snapshot_age_seconds: float = 180.0,
) -> WRHRevisionLedgerEvidence:
    cutoff = _finite(as_of, "WRH_LEDGER_AS_OF_INVALID")
    sequence = _validated_sequence(snapshots, as_of=cutoff)

    # The latest complete snapshot is the current-state authority for O(t); the
    # historical sequence only explains how visible row versions changed over time.
    try:
        observed_asof = build_wrh_observed_extreme_asof(
            sequence[-1],
            compiled,
            as_of=cutoff,
            max_snapshot_age_seconds=max_snapshot_age_seconds,
        )
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise WRHRevisionLedgerError(f"WRH_LEDGER_OBSERVED_STATE_INVALID:{code}") from exc

    revisions: list[WRHObservationRevision] = []
    active: dict[str, tuple[dict, WRHObservationRevision]] = {}
    version_counts: dict[str, int] = {}

    for snapshot in sequence:
        current_rows: dict[str, dict] = {}
        for row in snapshot.target_rows:
            if row.observation_time_local.timestamp() > cutoff + 1e-9:
                raise WRHRevisionLedgerError("WRH_LEDGER_ROW_AFTER_AS_OF")
            key = _instant_key(row)
            if key in current_rows:
                raise WRHRevisionLedgerError("WRH_LEDGER_DUPLICATE_OBSERVATION_KEY")
            current_rows[key] = _row_state(row)

        all_keys = set(active) | set(current_rows)
        for key in sorted(all_keys):
            previous = active.get(key)
            current = current_rows.get(key)
            previous_state = previous[0] if previous is not None else None
            if current == previous_state:
                continue
            if previous is None and current is None:
                continue
            version = version_counts.get(key, 0) + 1
            version_counts[key] = version
            row_sha = _sha(current) if current is not None else None
            shell = WRHObservationRevision(
                observation_key=key,
                version_number=version,
                became_visible_at=float(snapshot.received_at),
                present=current is not None,
                row_state_sha256=row_sha,
                displayed_temp_f=(None if current is None else current["displayed_temp_f"]),
                source_snapshot_sha256=snapshot.evidence_sha256,
                supersedes_revision_sha256=(None if previous is None else previous[1].revision_sha256),
                revision_sha256="0" * 64,
            )
            revision = WRHObservationRevision(
                **{
                    name: getattr(shell, name)
                    for name, definition in shell.__dataclass_fields__.items()
                    if definition.init and name != "revision_sha256"
                },
                revision_sha256=_sha(_revision_payload(shell)),
            )
            revisions.append(revision)
            if current is None:
                active.pop(key, None)
            else:
                active[key] = (current, revision)

    active_revisions = tuple(sorted(value[1].revision_sha256 for value in active.values()))
    # Active versions must exactly match the latest complete target-day source state.
    latest_keys = {_instant_key(row) for row in sequence[-1].target_rows}
    if set(active) != latest_keys:
        raise WRHRevisionLedgerError("WRH_LEDGER_ACTIVE_STATE_MISMATCH")

    superseded = sum(1 for revision in revisions if revision.revision_sha256 not in set(active_revisions))
    tombstones = sum(1 for revision in revisions if not revision.present)
    shell = WRHRevisionLedgerEvidence(
        version=WRH_REVISION_LEDGER_VERSION,
        station=sequence[-1].station,
        target_date=sequence[-1].target_date.isoformat(),
        timezone=sequence[-1].timezone,
        as_of=cutoff,
        source_snapshot_sha256s=tuple(snapshot.evidence_sha256 for snapshot in sequence),
        revisions=tuple(revisions),
        active_revision_sha256s=active_revisions,
        active_observation_count=len(active),
        superseded_revision_count=superseded,
        tombstone_count=tombstones,
        observed_asof=observed_asof,
        evidence_sha256="0" * 64,
    )
    return WRHRevisionLedgerEvidence(
        **{
            name: getattr(shell, name)
            for name, definition in shell.__dataclass_fields__.items()
            if definition.init and name != "evidence_sha256"
        },
        evidence_sha256=_sha(_ledger_payload(shell)),
    )
