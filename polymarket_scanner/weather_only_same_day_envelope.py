from __future__ import annotations

"""Replayable immutable evidence envelope for same-day weather research.

R19 requires more than hashes without preimages. This envelope stores the actual
inputs needed to reproduce one three-layer research decision offline:

* exact compiled contract partition, exact rule/population semantics and mapping policy;
* the accepted official observation rows used to rebuild O(t);
* the deterministic U(t) coverage plan;
* raw Layer-2 provider evidence plus its digest-bound verified coverage object;
* the full 31-member hourly GEFS target-day trajectory;
* the reduced Layer-3 path; and
* the final bucket-frequency decision.

Construction independently rebuilds O(t), projects the raw hourly GEFS source onto
the frozen Layer-3 U(t) mask, and rebuilds the final three-layer decision. Any digest,
identity or result mismatch fails closed. Release/config/protocol cohort identifiers
are mandatory so fixes cannot silently contaminate historical strategy metrics.

This is research provenance only. It cannot authorize same-day delivery, settlement
or real-money execution.
"""

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Iterable

from .weather_only_conditioned_extremes import OfficialObservation, build_observed_extreme
from .weather_only_conditioned_paths import VerifiedRemainingHoursPath
from .weather_only_contracts import CompiledWeatherEvent
from .weather_only_forecast import EnsembleMappingPolicy
from .weather_only_gefs_hourly import (
    GEFSHourlyTargetDay,
    build_verified_gefs_path_from_hourly,
    verify_gefs_hourly_evidence,
)
from .weather_only_near_term import (
    VerifiedNearTermCoverage,
    verify_near_term_coverage_integrity,
)
from .weather_only_same_day_contract import (
    SameDayContractSemantics,
    verify_same_day_contract_semantics,
)
from .weather_only_three_layer import (
    ThreeLayerResearchDecision,
    build_three_layer_research_decision,
    verify_three_layer_decision_integrity,
)
from .weather_only_three_layer_integrity import (
    verify_remaining_path_integrity,
    verify_unresolved_coverage_integrity,
)
from .weather_only_unresolved_coverage import UnresolvedCoveragePlan


SAME_DAY_ENVELOPE_VERSION = "weather_same_day_replay_envelope_v2_rule_population_full_preimages"


class SameDayEnvelopeError(RuntimeError):
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
        raise SameDayEnvelopeError("SAME_DAY_ENVELOPE_JSON_INVALID") from None


def canonical_evidence_sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _identity(value: object, code: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise SameDayEnvelopeError(code)
    return text


def _sha_identity(value: object, code: str) -> str:
    text = _identity(value, code).lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise SameDayEnvelopeError(code)
    return text


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise SameDayEnvelopeError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise SameDayEnvelopeError(code) from None
    if not math.isfinite(number) or number < 0.0:
        raise SameDayEnvelopeError(code)
    return number


def _observation_dict(row: OfficialObservation) -> dict:
    return row.as_dict()


@dataclass(frozen=True, slots=True)
class SameDayEvidenceEnvelope:
    version: str
    release_sha: str
    config_sha256: str
    execution_protocol_id: str
    created_at: float
    contract: dict
    contract_semantics: dict
    mapping_policy: dict
    official_observations: tuple[dict, ...]
    observed_state: dict
    coverage_plan: dict
    near_term_raw_evidence: dict
    near_term_verified: dict
    hourly_gefs_raw: dict
    remaining_path: dict
    final_decision: dict
    envelope_sha256: str
    offline_replay_verified: bool = field(init=False, default=True)
    calibrated_probability: bool = field(init=False, default=False)
    same_day_delivery_enabled: bool = field(init=False, default=False)
    settlement_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "release_sha": self.release_sha,
            "config_sha256": self.config_sha256,
            "execution_protocol_id": self.execution_protocol_id,
            "created_at": self.created_at,
            "contract": self.contract,
            "contract_semantics": self.contract_semantics,
            "mapping_policy": self.mapping_policy,
            "official_observations": list(self.official_observations),
            "observed_state": self.observed_state,
            "coverage_plan": self.coverage_plan,
            "near_term_raw_evidence": self.near_term_raw_evidence,
            "near_term_verified": self.near_term_verified,
            "hourly_gefs_raw": self.hourly_gefs_raw,
            "remaining_path": self.remaining_path,
            "final_decision": self.final_decision,
            "envelope_sha256": self.envelope_sha256,
            "offline_replay_verified": self.offline_replay_verified,
            "calibrated_probability": self.calibrated_probability,
            "same_day_delivery_enabled": self.same_day_delivery_enabled,
            "settlement_authority": self.settlement_authority,
            "financial_authority": self.financial_authority,
        }


def _payload(value: SameDayEvidenceEnvelope) -> dict:
    result = value.as_dict()
    result.pop("envelope_sha256", None)
    return result


def _path_dict(path: VerifiedRemainingHoursPath) -> dict:
    return {
        "version": path.version,
        "policy": path.policy.as_dict(),
        "station": path.station,
        "unit": path.unit,
        "family": path.family,
        "as_of": path.as_of,
        "issued_at": path.issued_at,
        "received_at": path.received_at,
        "target_end": path.target_end,
        "provider": path.provider,
        "provider_run_id": path.provider_run_id,
        "unresolved_segments": [segment.as_dict() for segment in path.unresolved_segments],
        "expected_valid_times": list(path.expected_valid_times),
        "member_labels": list(path.member_labels),
        "point_count": path.point_count,
        "path_evidence_sha256": path.path_evidence_sha256,
        "ensemble": path.ensemble.as_dict(),
        "coverage_complete": path.coverage_complete,
        "calibrated_probability": path.calibrated_probability,
        "settlement_authority": path.settlement_authority,
        "financial_authority": path.financial_authority,
    }


def build_same_day_evidence_envelope(
    *,
    compiled: CompiledWeatherEvent,
    contract_semantics: SameDayContractSemantics,
    mapping_policy: EnsembleMappingPolicy,
    official_observations: Iterable[OfficialObservation],
    coverage: UnresolvedCoveragePlan,
    near_term_raw_evidence: dict,
    near_term: VerifiedNearTermCoverage,
    hourly_gefs: GEFSHourlyTargetDay,
    remaining_path: VerifiedRemainingHoursPath,
    decision: ThreeLayerResearchDecision,
    release_sha: str,
    config_sha256: str,
    execution_protocol_id: str,
    created_at: float,
) -> SameDayEvidenceEnvelope:
    if not isinstance(compiled, CompiledWeatherEvent):
        raise SameDayEnvelopeError("SAME_DAY_ENVELOPE_CONTRACT_TYPE_INVALID")
    if not isinstance(mapping_policy, EnsembleMappingPolicy):
        raise SameDayEnvelopeError("SAME_DAY_ENVELOPE_MAPPING_POLICY_TYPE_INVALID")
    release = _sha_identity(release_sha, "SAME_DAY_ENVELOPE_RELEASE_SHA_INVALID")
    config = _sha_identity(config_sha256, "SAME_DAY_ENVELOPE_CONFIG_SHA_INVALID")
    protocol = _identity(execution_protocol_id, "SAME_DAY_ENVELOPE_PROTOCOL_MISSING")
    created = _finite(created_at, "SAME_DAY_ENVELOPE_CREATED_AT_INVALID")
    rows = tuple(official_observations)
    if not rows or any(not isinstance(row, OfficialObservation) for row in rows):
        raise SameDayEnvelopeError("SAME_DAY_ENVELOPE_OFFICIAL_OBSERVATIONS_INVALID")
    if not isinstance(near_term_raw_evidence, dict) or not near_term_raw_evidence:
        raise SameDayEnvelopeError("SAME_DAY_ENVELOPE_NEAR_TERM_RAW_MISSING")

    try:
        semantics_checked = verify_same_day_contract_semantics(contract_semantics, compiled)
        coverage_checked = verify_unresolved_coverage_integrity(coverage)
        near_checked = verify_near_term_coverage_integrity(near_term)
        hourly_checked = verify_gefs_hourly_evidence(hourly_gefs)
        path_checked = verify_remaining_path_integrity(remaining_path)
        decision_checked = verify_three_layer_decision_integrity(decision)
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise SameDayEnvelopeError(f"SAME_DAY_ENVELOPE_INPUT_INVALID:{code}") from exc

    if not semantics_checked.layer1_adapter_capable:
        raise SameDayEnvelopeError(
            f"SAME_DAY_ENVELOPE_LAYER1_ADAPTER_BLOCKED:{semantics_checked.layer1_adapter_block_reason}"
        )
    if coverage_checked.population_id != semantics_checked.observation_population:
        raise SameDayEnvelopeError("SAME_DAY_ENVELOPE_OBSERVATION_POPULATION_MISMATCH")
    if (
        coverage_checked.station != semantics_checked.station
        or coverage_checked.target_date != semantics_checked.target_date
        or decision_checked.event_id != semantics_checked.event_id
    ):
        raise SameDayEnvelopeError("SAME_DAY_ENVELOPE_CONTRACT_SEMANTICS_IDENTITY_MISMATCH")

    near_raw_sha = canonical_evidence_sha256(near_term_raw_evidence)
    if near_raw_sha != near_checked.source_evidence_sha256:
        raise SameDayEnvelopeError("SAME_DAY_ENVELOPE_NEAR_TERM_RAW_DIGEST_MISMATCH")
    if hourly_checked.received_at > coverage_checked.as_of + 1e-6:
        raise SameDayEnvelopeError("SAME_DAY_ENVELOPE_GEFS_LOOKAHEAD")

    try:
        observed_rebuilt = build_observed_extreme(
            rows,
            station=semantics_checked.station,
            population_id=semantics_checked.observation_population,
            unit=semantics_checked.unit,
            family=semantics_checked.family,
            target_start=coverage_checked.target_start,
            as_of=coverage_checked.as_of,
        )
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise SameDayEnvelopeError(f"SAME_DAY_ENVELOPE_OBSERVED_REBUILD_FAILED:{code}") from exc
    if observed_rebuilt.evidence_sha256 != decision_checked.observed_evidence_sha256:
        raise SameDayEnvelopeError("SAME_DAY_ENVELOPE_OBSERVED_STATE_MISMATCH")

    try:
        path_rebuilt = build_verified_gefs_path_from_hourly(
            hourly_checked,
            family=compiled.family,
            as_of=coverage_checked.as_of,
            target_end=coverage_checked.target_end,
            unresolved_segments=coverage_checked.ensemble_segments,
        )
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise SameDayEnvelopeError(f"SAME_DAY_ENVELOPE_PATH_REPLAY_FAILED:{code}") from exc
    if (
        path_rebuilt.path_evidence_sha256 != path_checked.path_evidence_sha256
        or path_rebuilt.ensemble.evidence_sha256 != path_checked.ensemble.evidence_sha256
    ):
        raise SameDayEnvelopeError("SAME_DAY_ENVELOPE_PATH_REPLAY_MISMATCH")

    try:
        replayed_decision = build_three_layer_research_decision(
            compiled,
            observed_rebuilt,
            coverage_checked,
            near_checked,
            path_rebuilt,
            mapping_policy,
            as_of=coverage_checked.as_of,
        )
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise SameDayEnvelopeError(f"SAME_DAY_ENVELOPE_DECISION_REPLAY_FAILED:{code}") from exc
    if replayed_decision.evidence_sha256 != decision_checked.evidence_sha256:
        raise SameDayEnvelopeError("SAME_DAY_ENVELOPE_DECISION_REPLAY_MISMATCH")

    shell = SameDayEvidenceEnvelope(
        version=SAME_DAY_ENVELOPE_VERSION,
        release_sha=release,
        config_sha256=config,
        execution_protocol_id=protocol,
        created_at=created,
        contract=compiled.as_dict(),
        contract_semantics=semantics_checked.as_dict(),
        mapping_policy=asdict(mapping_policy),
        official_observations=tuple(_observation_dict(row) for row in rows),
        observed_state=observed_rebuilt.as_dict(),
        coverage_plan=coverage_checked.as_dict(),
        near_term_raw_evidence=json.loads(_canonical(near_term_raw_evidence)),
        near_term_verified=near_checked.as_dict(),
        hourly_gefs_raw=hourly_checked.as_dict(),
        remaining_path=_path_dict(path_rebuilt),
        final_decision=replayed_decision.as_dict(),
        envelope_sha256="0" * 64,
    )
    return SameDayEvidenceEnvelope(
        **{
            name: getattr(shell, name)
            for name, definition in shell.__dataclass_fields__.items()
            if definition.init and name != "envelope_sha256"
        },
        envelope_sha256=canonical_evidence_sha256(_payload(shell)),
    )


def verify_same_day_evidence_envelope(value: object) -> SameDayEvidenceEnvelope:
    if not isinstance(value, SameDayEvidenceEnvelope):
        raise SameDayEnvelopeError("SAME_DAY_ENVELOPE_TYPE_INVALID")
    if value.envelope_sha256 != canonical_evidence_sha256(_payload(value)):
        raise SameDayEnvelopeError("SAME_DAY_ENVELOPE_DIGEST_MISMATCH")
    if any((
        value.calibrated_probability,
        value.same_day_delivery_enabled,
        value.settlement_authority,
        value.financial_authority,
    )):
        raise SameDayEnvelopeError("SAME_DAY_ENVELOPE_AUTHORITY_BOUNDARY_BROKEN")
    return value
