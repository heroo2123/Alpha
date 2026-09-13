from __future__ import annotations

"""Silent same-day three-layer research capture.

This is the live-facing boundary for the new three-layer model. It consumes evidence
that was already acquired before an immutable decision ``as_of`` was frozen, then
builds as much of the research state as can be proven without silently promoting a
scientific assumption.

The current WRH-hourly -> hourly-model population alignment has *not* been certified.
Therefore callers must leave ``population_alignment_certified=False`` in production.
The capture still records Layer 1 official O(t), Layer 2 NWS near-term path and Layer 3
31-member GEFS remaining-hours path when available, but marks the record BLOCKED and
never produces Telegram, paper P&L or financial authority. A future prospective
scientific validation may supply a separately reviewed certification policy.
"""

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .weather_only_conditioned_wrh import build_wrh_observed_extreme_asof
from .weather_only_conditioned_wrh_preimage import extract_wrh_official_observation_preimages
from .weather_only_contracts import CompiledWeatherEvent
from .weather_only_forecast import EnsembleMappingPolicy
from .weather_only_gefs_hourly import (
    GEFSHourlyTargetDay,
    build_verified_gefs_path_from_hourly,
    verify_gefs_hourly_evidence,
)
from .weather_only_near_term_path import (
    reduce_sample_path_to_research_coverage,
    verify_near_term_path_integrity,
)
from .weather_only_nws_near_term import (
    NWSNearTermRawSnapshot,
    path_from_nws_raw_snapshot,
    verify_nws_raw_snapshot,
)
from .weather_only_same_day_contract import (
    SameDayContractSemantics,
    expected_layer1_population_instance,
    verify_same_day_contract_semantics,
)
from .weather_only_three_layer import build_three_layer_research_decision
from .weather_only_unresolved_coverage import build_unresolved_coverage_plan
from .weather_only_wrh import WRHSourceSnapshot


SAME_DAY_CAPTURE_VERSION = "weather_same_day_silent_capture_v1_three_layers_fail_closed"
SAME_DAY_CAPTURE_BLOCKED = "BLOCKED_RESEARCH"
SAME_DAY_CAPTURE_READY = "RESEARCH_READY_UNCALIBRATED"
DEFAULT_MAPPING_POLICY = EnsembleMappingPolicy(
    policy_id="SAME_DAY_THREE_LAYER_NEAREST_WHOLE_V1",
    include_control=True,
)


class SameDayCaptureError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


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
        raise SameDayCaptureError("SAME_DAY_CAPTURE_JSON_INVALID") from None


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise SameDayCaptureError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise SameDayCaptureError(code) from None
    if not math.isfinite(number):
        raise SameDayCaptureError(code)
    return number


def _metadata_dict(value: object) -> dict:
    method = getattr(value, "as_dict", None)
    if not callable(method):
        raise SameDayCaptureError("SAME_DAY_CAPTURE_STATION_METADATA_INVALID")
    result = method()
    if not isinstance(result, dict):
        raise SameDayCaptureError("SAME_DAY_CAPTURE_STATION_METADATA_INVALID")
    return result


def _path_dict(value: object | None) -> dict | None:
    if value is None:
        return None
    method = getattr(value, "as_dict", None)
    if not callable(method):
        raise SameDayCaptureError("SAME_DAY_CAPTURE_PATH_SERIALIZATION_INVALID")
    result = method()
    if not isinstance(result, dict):
        raise SameDayCaptureError("SAME_DAY_CAPTURE_PATH_SERIALIZATION_INVALID")
    return result


@dataclass(frozen=True, slots=True)
class SameDayCaptureRecord:
    version: str
    event_id: str
    station: str
    target_date: str
    family: str
    unit: str
    as_of: float
    status: str
    block_reasons: tuple[str, ...]
    contract: dict
    contract_semantics: dict
    station_metadata: dict
    wrh_snapshot: dict
    official_observations: tuple[dict, ...]
    observed_state: dict
    coverage_plan: dict
    near_term_raw_snapshot: dict
    near_term_path: dict | None
    hourly_gefs: dict
    remaining_hours_path: dict | None
    mapping_policy: dict
    final_decision: dict | None
    capture_sha256: str
    included_in_validated_pnl: bool = field(init=False, default=False)
    calibrated_probability: bool = field(init=False, default=False)
    same_day_delivery_enabled: bool = field(init=False, default=False)
    settlement_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "event_id": self.event_id,
            "station": self.station,
            "target_date": self.target_date,
            "family": self.family,
            "unit": self.unit,
            "as_of": self.as_of,
            "status": self.status,
            "block_reasons": list(self.block_reasons),
            "contract": self.contract,
            "contract_semantics": self.contract_semantics,
            "station_metadata": self.station_metadata,
            "wrh_snapshot": self.wrh_snapshot,
            "official_observations": list(self.official_observations),
            "observed_state": self.observed_state,
            "coverage_plan": self.coverage_plan,
            "near_term_raw_snapshot": self.near_term_raw_snapshot,
            "near_term_path": self.near_term_path,
            "hourly_gefs": self.hourly_gefs,
            "remaining_hours_path": self.remaining_hours_path,
            "mapping_policy": self.mapping_policy,
            "final_decision": self.final_decision,
            "capture_sha256": self.capture_sha256,
            "included_in_validated_pnl": self.included_in_validated_pnl,
            "calibrated_probability": self.calibrated_probability,
            "same_day_delivery_enabled": self.same_day_delivery_enabled,
            "settlement_authority": self.settlement_authority,
            "financial_authority": self.financial_authority,
        }


def _capture_payload(value: SameDayCaptureRecord) -> dict:
    payload = value.as_dict()
    payload.pop("capture_sha256", None)
    return payload


def verify_same_day_capture_record(value: object) -> SameDayCaptureRecord:
    if not isinstance(value, SameDayCaptureRecord):
        raise SameDayCaptureError("SAME_DAY_CAPTURE_TYPE_INVALID")
    if value.version != SAME_DAY_CAPTURE_VERSION:
        raise SameDayCaptureError("SAME_DAY_CAPTURE_VERSION_INVALID")
    if value.status not in {SAME_DAY_CAPTURE_BLOCKED, SAME_DAY_CAPTURE_READY}:
        raise SameDayCaptureError("SAME_DAY_CAPTURE_STATUS_INVALID")
    if value.status == SAME_DAY_CAPTURE_BLOCKED and not value.block_reasons:
        raise SameDayCaptureError("SAME_DAY_CAPTURE_BLOCK_REASON_MISSING")
    if value.status == SAME_DAY_CAPTURE_READY and (value.block_reasons or value.final_decision is None):
        raise SameDayCaptureError("SAME_DAY_CAPTURE_READY_STATE_INVALID")
    if value.capture_sha256 != _sha(_capture_payload(value)):
        raise SameDayCaptureError("SAME_DAY_CAPTURE_DIGEST_MISMATCH")
    if any((
        value.included_in_validated_pnl,
        value.calibrated_probability,
        value.same_day_delivery_enabled,
        value.settlement_authority,
        value.financial_authority,
    )):
        raise SameDayCaptureError("SAME_DAY_CAPTURE_AUTHORITY_BOUNDARY_BROKEN")
    return value


def assemble_same_day_capture(
    *,
    compiled: CompiledWeatherEvent,
    contract_semantics: SameDayContractSemantics,
    station_metadata: object,
    wrh_snapshot: WRHSourceSnapshot,
    near_term_raw_snapshot: NWSNearTermRawSnapshot,
    hourly_gefs: GEFSHourlyTargetDay,
    as_of: float,
    mapping_policy: EnsembleMappingPolicy = DEFAULT_MAPPING_POLICY,
    population_alignment_certified: bool = False,
) -> SameDayCaptureRecord:
    """Assemble all three layers while preserving current scientific fail-closed gate."""
    cutoff = _finite(as_of, "SAME_DAY_CAPTURE_AS_OF_INVALID")
    if cutoff < 0.0:
        raise SameDayCaptureError("SAME_DAY_CAPTURE_AS_OF_INVALID")
    if type(population_alignment_certified) is not bool:
        raise SameDayCaptureError("SAME_DAY_CAPTURE_POPULATION_CERTIFICATION_INVALID")
    if not isinstance(mapping_policy, EnsembleMappingPolicy):
        raise SameDayCaptureError("SAME_DAY_CAPTURE_MAPPING_POLICY_INVALID")

    try:
        semantics = verify_same_day_contract_semantics(contract_semantics, compiled)
        population_instance = expected_layer1_population_instance(semantics)
        nws = verify_nws_raw_snapshot(near_term_raw_snapshot)
        gefs = verify_gefs_hourly_evidence(hourly_gefs)
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise SameDayCaptureError(f"SAME_DAY_CAPTURE_SOURCE_IDENTITY_INVALID:{code}") from exc

    metadata = _metadata_dict(station_metadata)
    station = str(metadata.get("station") or "").strip().upper()
    timezone_name = str(metadata.get("timezone") or "").strip()
    latitude = _finite(metadata.get("latitude"), "SAME_DAY_CAPTURE_STATION_METADATA_INVALID")
    longitude = _finite(metadata.get("longitude"), "SAME_DAY_CAPTURE_STATION_METADATA_INVALID")
    if station != semantics.station:
        raise SameDayCaptureError("SAME_DAY_CAPTURE_STATION_METADATA_IDENTITY_MISMATCH")
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        raise SameDayCaptureError("SAME_DAY_CAPTURE_TIMEZONE_INVALID") from None
    if datetime.fromtimestamp(cutoff, tz=zone).date().isoformat() != semantics.target_date:
        raise SameDayCaptureError("SAME_DAY_CAPTURE_NOT_TARGET_LOCAL_DAY")

    if (
        nws.station != station
        or abs(nws.latitude - latitude) > 1e-6
        or abs(nws.longitude - longitude) > 1e-6
    ):
        raise SameDayCaptureError("SAME_DAY_CAPTURE_LAYER2_LOCATION_IDENTITY_MISMATCH")
    if (
        gefs.station != station
        or gefs.target_date.isoformat() != semantics.target_date
        or gefs.unit != semantics.unit
        or gefs.timezone != timezone_name
        or abs(gefs.requested_latitude - latitude) > 1e-6
        or abs(gefs.requested_longitude - longitude) > 1e-6
    ):
        raise SameDayCaptureError("SAME_DAY_CAPTURE_LAYER3_LOCATION_IDENTITY_MISMATCH")
    if max(float(nws.received_at), float(gefs.received_at), float(wrh_snapshot.received_at)) > cutoff + 1e-6:
        raise SameDayCaptureError("SAME_DAY_CAPTURE_SOURCE_LOOKAHEAD")

    try:
        layer1 = build_wrh_observed_extreme_asof(wrh_snapshot, compiled, as_of=cutoff)
        observations = extract_wrh_official_observation_preimages(
            wrh_snapshot,
            compiled,
            as_of=cutoff,
            evidence=layer1,
        )
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise SameDayCaptureError(f"SAME_DAY_CAPTURE_LAYER1_INVALID:{code}") from exc
    if layer1.population_id != population_instance:
        raise SameDayCaptureError("SAME_DAY_CAPTURE_LAYER1_POPULATION_INSTANCE_MISMATCH")

    try:
        coverage = build_unresolved_coverage_plan(
            station=station,
            population_id=population_instance,
            timezone=timezone_name,
            target_date=compiled.target_date,
            as_of=cutoff,
            accepted_observation_times=[row.observed_at for row in observations],
            population_alignment_certified=population_alignment_certified,
        )
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise SameDayCaptureError(f"SAME_DAY_CAPTURE_COVERAGE_INVALID:{code}") from exc

    near_path = None
    remaining_path = None
    reasons: list[str] = []
    if coverage.near_term_segment is None:
        reasons.append("LAYER2_SEGMENT_NOT_AVAILABLE")
    else:
        try:
            near_path = path_from_nws_raw_snapshot(
                nws,
                unit=compiled.unit,
                family=compiled.family,
                segment=coverage.near_term_segment,
            )
            verify_near_term_path_integrity(near_path)
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            reasons.append(f"LAYER2_BLOCKED:{code}")

    if not coverage.ensemble_segments:
        reasons.append("LAYER3_REMAINING_SEGMENTS_NOT_AVAILABLE")
    else:
        try:
            remaining_path = build_verified_gefs_path_from_hourly(
                gefs,
                family=compiled.family,
                as_of=cutoff,
                target_end=coverage.target_end,
                unresolved_segments=coverage.ensemble_segments,
            )
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            reasons.append(f"LAYER3_BLOCKED:{code}")

    if coverage.elapsed_gap_segments:
        reasons.append("ELAPSED_OFFICIAL_OBSERVATION_GAPS_PRESENT")
    if not population_alignment_certified:
        reasons.append("WRH_TO_MODEL_POPULATION_ALIGNMENT_UNPROVEN")

    final_decision = None
    if not reasons and near_path is not None and remaining_path is not None:
        try:
            near_coverage = reduce_sample_path_to_research_coverage(near_path)
            final_decision = build_three_layer_research_decision(
                compiled,
                layer1.observed_state,
                coverage,
                near_coverage,
                remaining_path,
                mapping_policy,
                as_of=cutoff,
            )
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            reasons.append(f"THREE_LAYER_DECISION_BLOCKED:{code}")
            final_decision = None

    status = SAME_DAY_CAPTURE_BLOCKED if reasons else SAME_DAY_CAPTURE_READY
    shell = SameDayCaptureRecord(
        version=SAME_DAY_CAPTURE_VERSION,
        event_id=compiled.event_id,
        station=station,
        target_date=semantics.target_date,
        family=compiled.family,
        unit=compiled.unit,
        as_of=cutoff,
        status=status,
        block_reasons=tuple(dict.fromkeys(reasons)),
        contract=compiled.as_dict(),
        contract_semantics=semantics.as_dict(),
        station_metadata=metadata,
        wrh_snapshot=wrh_snapshot.as_dict(),
        official_observations=tuple(row.as_dict() for row in observations),
        observed_state=layer1.observed_state.as_dict(),
        coverage_plan=coverage.as_dict(),
        near_term_raw_snapshot=nws.as_dict(),
        near_term_path=_path_dict(near_path),
        hourly_gefs=gefs.as_dict(),
        remaining_hours_path=_path_dict(remaining_path),
        mapping_policy=asdict(mapping_policy),
        final_decision=None if final_decision is None else final_decision.as_dict(),
        capture_sha256="0" * 64,
    )
    return SameDayCaptureRecord(
        **{
            name: getattr(shell, name)
            for name, definition in shell.__dataclass_fields__.items()
            if definition.init and name != "capture_sha256"
        },
        capture_sha256=_sha(_capture_payload(shell)),
    )
