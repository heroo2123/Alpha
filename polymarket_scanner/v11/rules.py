"""Universal material rule binding and durable, monotonic drift quarantine.

The reviewed strict compiler remains the semantic authority. Discovery or a hash
alone never upgrades an unsupported contract. No order API exists in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..weather_only_contract_strict import compile_strict_temperature_event, strict_contract_identity
from ..weather_only_rules import compile_temperature_rule_authority
from .evidence import EvidenceError, EvidenceStore, canonical, digest, identity, sha


@dataclass(frozen=True)
class RuleFingerprint:
    # Canonical JSON prevents nested dictionaries from mutating a frozen instance.
    canonical_json: str
    sha256: str
    source_event_sha256: str

    @property
    def payload(self):
        import json
        value = json.loads(self.canonical_json)
        if digest(value) != self.sha256:
            raise EvidenceError("RULE_FINGERPRINT_INTEGRITY")
        return value


def fingerprint_event(event: dict, *, station_timezone: str,
                      metadata_fingerprint: str) -> RuleFingerprint:
    sha(metadata_fingerprint)
    try:
        ZoneInfo(station_timezone)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        raise EvidenceError("RULE_TIMEZONE_UNKNOWN") from None
    compiled = compile_strict_temperature_event(event)
    contract = strict_contract_identity(event, compiled)
    authority = compile_temperature_rule_authority(event, compiled)
    if (not authority.rule_semantics_proven or not authority.exactly_one_outcome_proven
            or compiled.target_date is None or not compiled.station_hint):
        raise EvidenceError("EXACT_RULE_SEMANTICS_REQUIRED")
    buckets = []
    seen = set()
    for b in compiled.buckets:
        for item in (b.market_id, b.condition_id, b.yes_token, b.no_token):
            identity(item)
        if b.yes_token == b.no_token or b.yes_token in seen or b.no_token in seen:
            raise EvidenceError("RULE_TOKEN_PARTITION_INVALID")
        seen.update((b.yes_token, b.no_token))
        buckets.append({"market_id": b.market_id, "condition_id": b.condition_id,
                        "question": b.question, "lower": b.lower, "upper": b.upper,
                        "unit": b.unit, "yes_token": b.yes_token, "no_token": b.no_token})
    payload = {"version": "alpha_v11_universal_rule_v1", "event_id": compiled.event_id,
               "title": compiled.title, "strict_contract": contract,
               "station": compiled.station_hint, "city": contract.get("location"),
               "target_date": compiled.target_date.isoformat(), "timezone": station_timezone,
               "unit": compiled.unit, "family": compiled.family,
               "statistic": authority.statistic, "observation_population": authority.observation_population,
               "precision_rounding": authority.precision,
               "primary_source": contract["operative_source"], "source_family": compiled.source_family,
               "fallback_policy": authority.fallback_policy, "correction_policy": authority.correction_policy,
               "finality_and_deadline_policy": authority.finality_policy,
               "no_data_outcome": authority.no_data_outcome,
               "partition": sorted(buckets, key=lambda x: x["market_id"]),
               "metadata_fingerprint": metadata_fingerprint,
               "compiler_version": compiled.compiler_version,
               "semantic_profile_version": authority.version,
               "financial_authority": False}
    for key in ("statistic", "observation_population", "precision_rounding", "primary_source",
                "fallback_policy", "correction_policy", "finality_and_deadline_policy", "no_data_outcome"):
        if not payload[key]:
            raise EvidenceError("RULE_COMPONENT_MISSING")
    return RuleFingerprint(canonical(payload), digest(payload), digest(event))


def history(store: EvidenceStore, kind: str, event_id: str) -> list[dict]:
    records, after = [], 0
    for _ in range((store.limits.max_records + 999) // 1000 + 1):
        page = store.records(kind=kind, event_id=event_id, after_seq=after, limit=1000)
        if not page:
            return records
        records.extend(page)
        after = page[-1]["seq"]
    raise EvidenceError("AUDIT_HISTORY_BOUND")


class RuleGuard:
    def __init__(self, store: EvidenceStore):
        self.store = store

    def observe(self, record_id: str, fingerprint: RuleFingerprint, *, raw_evidence_id: str) -> dict:
        payload = fingerprint.payload
        event_id = payload["event_id"]
        raw = self.store.get(raw_evidence_id)
        if raw["kind"] != "RULES" or raw["event_id"] != event_id:
            raise EvidenceError("RULE_RAW_EVIDENCE_MISMATCH")
        supplied = raw["body"]["payload"].get("event")
        if (not isinstance(supplied, dict) or digest(supplied) != fingerprint.source_event_sha256
                or fingerprint_event(supplied, station_timezone=payload["timezone"],
                                     metadata_fingerprint=payload["metadata_fingerprint"]) != fingerprint):
            raise EvidenceError("RULE_PREIMAGE_RAW_BINDING_INVALID")
        past = history(self.store, "RULE_STATE", event_id)
        last = past[-1]["body"]["details"] if past else {}
        changed = bool(last and fingerprint.sha256 != last["fingerprint"])
        quarantined = changed or last.get("quarantined", False)
        return self.store.audit(record_id, event_id=event_id, kind="RULE_STATE",
                                details={"fingerprint": fingerprint.sha256, "preimage": payload,
                                         "source_event_sha256": fingerprint.source_event_sha256,
                                         "changed": changed, "quarantined": quarantined,
                                         "state": "RULE_DRIFT" if quarantined else "SEMANTICS_OBSERVED",
                                         "cancel_managed_new_risk_requested": quarantined,
                                         "preserve_fills_and_reconciliation": True,
                                         "automatic_recertification": False}, evidence_ids=(raw_evidence_id,),
                                expected_previous_seq=past[-1]["seq"] if past else 0)

    def recertify(self, record_id: str, *, event_id: str, registry, scope, stage: str) -> dict:
        # The registry reads the fixed protected manifest itself; a caller's bool
        # or review-sequence claim is never accepted as permission.
        from .certification import StationRegistry
        if not isinstance(registry, StationRegistry) or registry.store is not self.store:
            raise EvidenceError("RECERTIFICATION_REGISTRY_MISMATCH")
        past = history(self.store, "RULE_STATE", event_id)
        if not past:
            raise EvidenceError("RULE_EVIDENCE_MISSING")
        last = past[-1]
        details = last["body"]["details"]
        review = registry.assess(scope, stage=stage,
                                 metadata_fingerprint=details["preimage"]["metadata_fingerprint"],
                                 rule_fingerprint=details["fingerprint"])
        if (not review["eligible"] or review["reviewed_through_seq"] < last["seq"]
                or review["approved_at"] < last["body"]["recorded_at"]
                or scope.station != details["preimage"]["station"]):
            raise EvidenceError("RULE_RECERTIFICATION_REVIEW_REQUIRED")
        return self.store.audit(record_id, event_id=event_id, kind="RULE_STATE",
                                details={**details, "state": "REVIEWED_RECERTIFIED", "quarantined": False,
                                         "changed": False, "cancel_managed_new_risk_requested": False,
                                         "review_id": review["review_id"],
                                         "review_manifest_sha256": review["review_manifest_sha256"]},
                                evidence_ids=(last["id"],), expected_previous_seq=last["seq"])

    def revalidate(self, event_id: str, expected: str, *, max_age_seconds: float) -> dict:
        """Data gate; current scoped certification is a separate mandatory gate."""
        sha(expected)
        from .evidence import finite
        age = finite(max_age_seconds)
        if age <= 0:
            raise EvidenceError("RULE_FRESHNESS_BOUND_INVALID")
        records = history(self.store, "RULE_STATE", event_id)
        reason = "RULE_EVIDENCE_MISSING"
        if records:
            last = records[-1]
            elapsed = finite(self.store.clock()) - last["body"]["recorded_at"]
            detail = last["body"]["details"]
            reason = ("RULE_DRIFT_QUARANTINED" if detail["quarantined"] else
                      "RULE_FINGERPRINT_CHANGED" if detail["fingerprint"] != expected else
                      "RULE_CLOCK_INVALID" if elapsed < 0 else
                      "RULE_EVIDENCE_STALE" if elapsed > age else "RULE_BINDING_MATCH")
        return {"passed": reason == "RULE_BINDING_MATCH", "reason": reason,
                "financial_authority": False, "rule_fingerprint": expected}
