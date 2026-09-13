from __future__ import annotations

"""Replayable official-observation preimages for the Layer-1 WRH adapter.

``build_wrh_observed_extreme_asof`` proves the accepted O(t) state but its public
result intentionally keeps only compact row identities.  The same-day replay envelope
needs the exact ``OfficialObservation`` objects that produced that state.  This module
reconstructs them from the already digest-validated WRH snapshot and independently
checks that rebuilding O(t) produces the exact evidence digest from the Layer-1
adapter.  Any drift between compact evidence and replay preimages fails closed.
"""

import hashlib
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .weather_only_conditioned_extremes import OfficialObservation, build_observed_extreme
from .weather_only_conditioned_wrh import (
    ConditionedWRHError,
    WRHObservedAsOfEvidence,
    build_wrh_observed_extreme_asof,
)
from .weather_only_contracts import CompiledWeatherEvent
from .weather_only_wrh import WRHSourceSnapshot
from .weather_only_wrh_finality import _validate_snapshot


WRH_PREIMAGE_VERSION = "conditioned_wrh_preimage_v1_exact_layer1_replay"


class WRHPreimageError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _sha(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise WRHPreimageError("WRH_PREIMAGE_JSON_INVALID") from None
    return hashlib.sha256(encoded).hexdigest()


def _target_start(snapshot: WRHSourceSnapshot) -> float:
    zone = ZoneInfo(snapshot.timezone)
    target = snapshot.target_date
    return datetime(target.year, target.month, target.day, tzinfo=zone).timestamp()


def extract_wrh_official_observation_preimages(
    snapshot: WRHSourceSnapshot,
    compiled: CompiledWeatherEvent,
    *,
    as_of: float,
    evidence: WRHObservedAsOfEvidence | None = None,
) -> tuple[OfficialObservation, ...]:
    """Return the exact official rows whose reconstruction equals proven O(t)."""
    try:
        compact = evidence or build_wrh_observed_extreme_asof(snapshot, compiled, as_of=as_of)
    except ConditionedWRHError as exc:
        raise WRHPreimageError(f"WRH_PREIMAGE_LAYER1_INVALID:{exc.code}") from exc
    if not isinstance(compact, WRHObservedAsOfEvidence):
        raise WRHPreimageError("WRH_PREIMAGE_EVIDENCE_TYPE_INVALID")
    try:
        source = _validate_snapshot(snapshot, "CONDITIONED_PREIMAGE")
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise WRHPreimageError(f"WRH_PREIMAGE_SNAPSHOT_INVALID:{code}") from exc

    station = str(compiled.station_hint or "").strip().upper()
    population_id = compact.population_id
    rows: list[OfficialObservation] = []
    for row in source.target_rows:
        observed_at = row.observation_time_local.timestamp()
        if observed_at > float(as_of) + 1e-9:
            raise WRHPreimageError("WRH_PREIMAGE_ROW_AFTER_AS_OF")
        if row.displayed_temp_f is None:
            continue
        revision = _sha({
            "snapshot": source.evidence_sha256,
            "raw_time": row.observation_time_raw,
            "row_kind": row.row_kind,
            "displayed_temp_f": row.displayed_temp_f,
        })
        rows.append(OfficialObservation(
            station=station,
            population_id=population_id,
            unit="F",
            observed_at=observed_at,
            received_at=float(source.received_at),
            value=float(row.displayed_temp_f),
            source_revision=revision,
        ))
    if not rows or len(rows) != compact.accepted_row_count:
        raise WRHPreimageError("WRH_PREIMAGE_ACCEPTED_ROW_COUNT_MISMATCH")

    try:
        rebuilt = build_observed_extreme(
            tuple(rows),
            station=station,
            population_id=population_id,
            unit="F",
            family=compiled.family,
            target_start=_target_start(source),
            as_of=float(as_of),
        )
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise WRHPreimageError(f"WRH_PREIMAGE_REBUILD_INVALID:{code}") from exc
    if rebuilt.evidence_sha256 != compact.observed_state.evidence_sha256:
        raise WRHPreimageError("WRH_PREIMAGE_OBSERVED_STATE_MISMATCH")
    return tuple(rows)
