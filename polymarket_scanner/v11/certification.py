"""Capability-scoped station records and reviewed eligibility data gates.

Discovery and evidence collection are automatic. A root-custodied, separately
reviewed manifest is required for eligibility; the application cannot provision
that manifest. Eligibility never grants funding, activation or order authority.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import stat
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .evidence import EvidenceError, EvidenceStore, canonical, digest, finite, identity, sha
from .rules import history


REVIEW_PATH = Path("/etc/alpha-v11/approvals/station-capabilities.json")
STAGES = {"PAPER", "SHADOW", "CANARY_ELIGIBLE", "CANARY_VERIFIED", "LIVE_LIMITED"}
BASE_CAPABILITIES = {"IDENTITY", "RULE_SEMANTICS", "SOURCE_INTEGRITY", "EXECUTION_MECHANICS",
                     "ACCOUNTING", "PROTECTED_RISK"}
STRATEGY_CAPABILITIES = {
    "FUTURE_FORECAST": {"FORECAST_IDENTITY", "CONSERVATIVE_CONTRACT_VALUATION"},
    "SAME_DAY_LATE_LOCK": {"OFFICIAL_EXTREME_POPULATION", "REMAINING_EXTREME_MODEL", "CONSERVATIVE_CONTRACT_VALUATION"},
    "PWS_OBSERVATION_LEAD": {"PWS_SOURCE_QC", "PWS_CAUSAL_LEAD", "NEXT_OFFICIAL_MODEL",
                             "SEPARATE_CONTRACT_ECONOMICS", "PRE_CONFIRMATION_REVALIDATION", "NO_DOUBLE_RISK"},
    "SOURCE_SHOCK": {"CAUSAL_RELEASE_REVALIDATION", "CONSERVATIVE_CONTRACT_VALUATION",
                     "OFFICIAL_EXTREME_POPULATION", "REMAINING_EXTREME_MODEL", "EVENT_DIRECTIONAL_REVALIDATION"},
    "RELEASE_OPPORTUNITY": {"CAUSAL_RELEASE_REVALIDATION", "CONSERVATIVE_CONTRACT_VALUATION",
                            "OFFICIAL_EXTREME_POPULATION", "REMAINING_EXTREME_MODEL", "EVENT_DIRECTIONAL_REVALIDATION"},
    "CROSS_TEMPERATURE": {"JOINT_OUTCOME_MODEL", "MULTILEG_RECONCILIATION", "CONSERVATIVE_CONTRACT_VALUATION"},
    "CROSS_TEMP_RELATIVE_VALUE": {"JOINT_OUTCOME_MODEL", "MULTILEG_RECONCILIATION", "CONSERVATIVE_CONTRACT_VALUATION"},
    "STRUCTURAL": {"COMMON_RESOLUTION_PROOF", "MULTILEG_RECONCILIATION"},
    "RESULT_LAG": {"EXACT_FINALITY", "FINAL_PAYOUT_IDENTITY"},
    "MAKER_RESEARCH": {"BOUNDED_MAKER_BASELINE", "CANCEL_GUARDIAN", "CONSERVATIVE_CONTRACT_VALUATION"},
}
FAIL_STATES = {"QUARANTINED", "RULE_DRIFT", "SOURCE_UNAVAILABLE", "CALIBRATION_DEGRADED", "DISABLED"}


@dataclass(frozen=True)
class StationMetadata:
    station: str
    city: str
    country: str | None
    latitude: float
    longitude: float
    elevation_m: float | None
    timezone: str
    settlement_source: str
    observation_providers: tuple[str, ...]
    forecast_providers: tuple[str, ...]
    source_payload_sha256: str
    retrieved_at: float

    def __post_init__(self):
        if not re.fullmatch(r"[A-Z0-9]{4,12}", self.station):
            raise EvidenceError("STATION_ID_INVALID")
        for x in (self.city, self.settlement_source, *self.observation_providers, *self.forecast_providers):
            identity(x, maximum=512)
        if self.country is not None and not re.fullmatch(r"[A-Z]{2}", self.country):
            raise EvidenceError("STATION_COUNTRY_INVALID")
        if not -90 <= finite(self.latitude, nonnegative=False) <= 90 or not -180 <= finite(self.longitude, nonnegative=False) <= 180:
            raise EvidenceError("STATION_COORDINATES_INVALID")
        if self.elevation_m is not None:
            finite(self.elevation_m, nonnegative=False)
        try:
            ZoneInfo(self.timezone)
        except (ValueError, TypeError, ZoneInfoNotFoundError):
            raise EvidenceError("STATION_TIMEZONE_INVALID") from None
        sha(self.source_payload_sha256)
        finite(self.retrieved_at)
        if len(self.observation_providers) > 32 or len(self.forecast_providers) > 32:
            raise EvidenceError("STATION_PROVIDER_BOUND")
        if type(self.observation_providers) is not tuple or type(self.forecast_providers) is not tuple:
            raise EvidenceError("IMMUTABLE_PROVIDER_IDENTITIES_REQUIRED")

    @property
    def fingerprint(self):
        material = asdict(self)
        # Response timestamps, JSON field order and retrieval time are provenance,
        # not relocation. Their raw evidence remains separately hash-bound.
        material.pop("source_payload_sha256")
        material.pop("retrieved_at")
        for key in ("observation_providers", "forecast_providers"):
            material[key] = sorted(set(material[key]))
        return digest(material)


@dataclass(frozen=True)
class CapabilityScope:
    station: str
    family: str
    source_rule_family: str
    model_version: str
    horizon: str
    strategy: str
    season: str
    time_of_day: str

    def __post_init__(self):
        for value in asdict(self).values():
            identity(value)
        if self.family not in {"HIGH", "LOW"} or self.strategy not in STRATEGY_CAPABILITIES:
            raise EvidenceError("CERTIFICATION_SCOPE_UNSUPPORTED")
        if any(value == "*" for value in asdict(self).values()):
            raise EvidenceError("CERTIFICATION_WILDCARD_FORBIDDEN")

    @property
    def key(self):
        return digest(asdict(self))


def _root_custody(path: Path):
    if not path.is_absolute() or ".." in path.parts:
        raise EvidenceError("CERTIFICATION_REVIEW_PATH_INVALID")
    for parent in path.parents:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise EvidenceError("CERTIFICATION_REVIEW_PARENT_CUSTODY")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise EvidenceError("CERTIFICATION_REVIEW_FILE_CUSTODY")
    return info


def protected_reviews() -> dict:
    """Read only: no installer, environment override, key or writer in this plane."""
    try:
        before = _root_custody(REVIEW_PATH)
        fd = os.open(REVIEW_PATH, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            current = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
                raise EvidenceError("CERTIFICATION_REVIEW_CHANGED")
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise EvidenceError("CERTIFICATION_REVIEW_SIZE_BOUND")
        value = json.loads(raw)
        if (not isinstance(value, dict) or value.get("version") != "alpha_v11_certification_reviews_v1"
                or not isinstance(value.get("reviews"), list) or len(value["reviews"]) > 1000):
            raise EvidenceError("CERTIFICATION_REVIEW_SCHEMA")
        canonical(value)
        return value
    except (OSError, ValueError) as exc:
        raise EvidenceError("CERTIFICATION_REVIEW_UNAVAILABLE") from exc


class StationRegistry:
    def __init__(self, store: EvidenceStore):
        self.store = store

    def observe(self, record_id: str, metadata: StationMetadata, *, raw_evidence_id: str) -> dict:
        raw = self.store.get(raw_evidence_id)
        if (raw["kind"] != "STATION_METADATA" or metadata.retrieved_at > self.store.clock()
                or metadata.source_payload_sha256 != digest(raw["body"]["payload"])):
            raise EvidenceError("STATION_METADATA_PROVENANCE_MISMATCH")
        event = "station:" + metadata.station
        past = history(self.store, "REGISTRY", event)
        observations = [r for r in past if r["body"]["details"].get("action") == "METADATA"]
        changed = bool(observations and observations[-1]["body"]["details"]["metadata_fingerprint"] != metadata.fingerprint)
        return self.store.audit(record_id, event_id=event, kind="REGISTRY",
                                details={"action": "METADATA", "metadata": asdict(metadata),
                                         "metadata_fingerprint": metadata.fingerprint,
                                         "material_changed": changed,
                                         "state": "QUARANTINED" if changed else "DISCOVERED",
                                         "reason": "METADATA_DRIFT" if changed else "OBSERVED_NOT_CERTIFIED"},
                                evidence_ids=(raw_evidence_id,), expected_previous_seq=past[-1]["seq"] if past else 0)

    def demote(self, record_id: str, scope: CapabilityScope, *, state: str, reason: str,
               evidence_ids: tuple[str, ...], expected_previous_seq: int | None = None,
               expected_heads: tuple[tuple[str, str, int], ...] = ()) -> dict:
        if state not in FAIL_STATES or not evidence_ids:
            raise EvidenceError("DEMOTION_REQUIRES_REASON_EVIDENCE")
        return self.store.audit(record_id, event_id="station:" + scope.station, kind="REGISTRY",
                                details={"action": "DEMOTION", "scope_key": scope.key,
                                         "scope": asdict(scope), "state": state, "reason": identity(reason)},
                                evidence_ids=evidence_ids, expected_previous_seq=expected_previous_seq,
                                expected_heads=expected_heads)

    def proof(self, record_id: str, scope: CapabilityScope, *, capability: str,
              metadata_fingerprint: str, rule_fingerprint: str, evidence_ids: tuple[str, ...],
              result: str, checker_version: str) -> dict:
        allowed = BASE_CAPABILITIES | set().union(*STRATEGY_CAPABILITIES.values()) | {
            "CANARY_EXECUTION_VERIFIED", "OUT_OF_SAMPLE_STRATEGY_VALIDATION"}
        if capability not in allowed or result not in {"PASS", "FAIL", "UNKNOWN"} or not evidence_ids:
            raise EvidenceError("CAPABILITY_PROOF_INVALID")
        sha(metadata_fingerprint); sha(rule_fingerprint)
        if capability == "CANARY_EXECUTION_VERIFIED" and result == "PASS":
            for ref in evidence_ids:
                evidence = self.store.get(ref)
                if (evidence["body"].get("evidence_class") != "PUBLIC_OBSERVED"
                        or evidence["kind"] != "LABEL"
                        or evidence["body"]["payload"].get("label_type") != "RECONCILED_LIVE_EXECUTION"):
                    raise EvidenceError("REAL_EXECUTION_EVIDENCE_REQUIRED")
        return self.store.audit(record_id, event_id="station:" + scope.station, kind="REGISTRY",
                                details={"action": "CAPABILITY_EVIDENCE", "scope": asdict(scope),
                                         "scope_key": scope.key, "capability": capability,
                                         "metadata_fingerprint": metadata_fingerprint,
                                         "rule_fingerprint": rule_fingerprint,
                                         "result": result, "checker_version": identity(checker_version)},
                                evidence_ids=evidence_ids)

    def assess(self, scope: CapabilityScope, *, stage: str, metadata_fingerprint: str,
               rule_fingerprint: str) -> dict:
        if stage not in STAGES:
            raise EvidenceError("CERTIFICATION_STAGE_INVALID")
        sha(metadata_fingerprint); sha(rule_fingerprint)
        answer = {"scope": asdict(scope), "stage": stage, "eligible": False,
                  "financial_authority": False, "activation_authorized": False,
                  "required_capabilities": sorted(BASE_CAPABILITIES | STRATEGY_CAPABILITIES[scope.strategy]),
                  "reason": "NO_REVIEWED_CERTIFICATION"}
        records = history(self.store, "REGISTRY", "station:" + scope.station)
        metadata = [r for r in records if r["body"]["details"].get("action") == "METADATA"]
        if not metadata or metadata[-1]["body"]["details"]["metadata_fingerprint"] != metadata_fingerprint:
            return dict(answer, reason="STATION_IDENTITY_MISSING_OR_CHANGED")
        barriers = [r for r in records if r["body"]["details"].get("material_changed")
                    or (r["body"]["details"].get("action") == "DEMOTION"
                        and r["body"]["details"].get("scope_key") == scope.key)]
        barrier = max((r["seq"] for r in barriers), default=0)
        now = finite(self.store.clock())
        try:
            manifest = protected_reviews()
            matches = [r for r in manifest["reviews"] if r.get("scope_key") == scope.key
                       and r.get("namespace") == self.store.namespace and r.get("stage") == stage
                       and r.get("metadata_fingerprint") == metadata_fingerprint
                       and r.get("rule_fingerprint") == rule_fingerprint]
            if len(matches) != 1:
                return answer
            review = matches[0]
            identity(review["reviewer"]); identity(review["review_id"])
            if not finite(review["approved_at"]) <= now < finite(review["expires_at"]):
                return dict(answer, reason="CERTIFICATION_REVIEW_EXPIRED_OR_FUTURE")
            if (type(review["reviewed_through_seq"]) is not int or review["reviewed_through_seq"] < barrier
                    or any(r["body"]["recorded_at"] > review["approved_at"] for r in barriers)):
                return dict(answer, reason="QUARANTINE_REQUIRES_NEW_REVIEW")
            required = set(answer["required_capabilities"])
            if stage in {"CANARY_VERIFIED", "LIVE_LIMITED"}:
                required.add("CANARY_EXECUTION_VERIFIED")
            if stage == "LIVE_LIMITED":
                required.add("OUT_OF_SAMPLE_STRATEGY_VALIDATION")
            proofs = review["capability_proofs"]
            if not isinstance(proofs, dict) or not required <= proofs.keys():
                return dict(answer, reason="REQUIRED_CAPABILITY_EVIDENCE_MISSING")
            for cap in required:
                reference = proofs[cap]
                row = self.store.get(reference["id"])
                body = row["body"].get("details", {})
                if (row["sha256"] != reference["sha256"] or row["kind"] != "REGISTRY"
                        or row["seq"] > review["reviewed_through_seq"]
                        or row["body"]["recorded_at"] > review["approved_at"]
                        or body.get("action") != "CAPABILITY_EVIDENCE" or body.get("scope_key") != scope.key
                        or body.get("capability") != cap or body.get("result") != "PASS"
                        or body.get("metadata_fingerprint") != metadata_fingerprint
                        or body.get("rule_fingerprint") != rule_fingerprint):
                    return dict(answer, reason="CAPABILITY_PROOF_BINDING_INVALID")
            # A later failure cannot be erased by replaying an earlier PASS proof.
            if any(r["body"]["details"].get("scope_key") == scope.key
                   and r["body"]["details"].get("action") == "CAPABILITY_EVIDENCE"
                   and r["body"]["details"].get("capability") in required
                   and r["body"]["details"].get("result") != "PASS"
                   and r["seq"] > review["reviewed_through_seq"] for r in records):
                return dict(answer, reason="NEW_CAPABILITY_FAILURE_REQUIRES_REVIEW")
            return dict(answer, eligible=True, reason="REVIEWED_CAPABILITIES_MATCH",
                        review_id=review["review_id"], review_manifest_sha256=digest(manifest),
                        reviewed_through_seq=review["reviewed_through_seq"],
                        approved_at=review["approved_at"], expires_at=review["expires_at"],
                        required_capabilities=sorted(required))
        except (EvidenceError, KeyError, TypeError, ValueError):
            return dict(answer, reason="CERTIFICATION_REVIEW_OR_EVIDENCE_UNAVAILABLE")
