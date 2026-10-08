"""Offline historical six-record seed profile; no publication or authority grant.

The catalogue is fixed here. The caller separately pins the complete sealed
snapshot digest; supplying a different snapshot never changes the catalogue.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date
import hashlib
import math
from pathlib import Path
import re
import sqlite3

from .daily_seed_plan import (AUDIT_KEYS, CAPTURE_KEYS, COLUMNS, NAMESPACE,
                              SeedPlan, SeedPlanError, _memory_db,
                              _row, _same_path, _sealed_bytes, _validate_row,
                              _validate_schema, verify_existing_daily)
from .evidence import digest

PROFILE_ID = "alpha_v11_historical_six_20261004_v1"
HISTORICAL_EVENT = "1118070"
HISTORICAL_DAY = date(2026, 10, 4)
CATALOGUE = (
    (1, "decision-shadow:gamma:1118070:full", "RULES", HISTORICAL_EVENT,
     "7d0fd0cbb4ecfd0441d933e4874e78f869aaaa5a53a937a0a73a7c06bbf5450b"),
    (2, "decision-shadow:gamma:1118070:event", "RULES", HISTORICAL_EVENT,
     "99bbd9b015b9948eda87ab2d3ad93140552352c2164ae2796e9e1829e5a4db1d"),
    (3, "decision-shadow:station:KATL:raw", "STATION_METADATA", "station:KATL",
     "22afbefdadc847cad902fa9650c362d43bed778dd63756f503fd1a7eba950559"),
    (4, "decision-shadow:station:KATL:metadata", "REGISTRY", "station:KATL",
     "d76fa7bb11492c9f83b83b68bb2616f32f3e6751ebb273e5178a39f5b0233050"),
    (5, "decision-shadow:rule:1118070", "RULE_STATE", HISTORICAL_EVENT,
     "8a672602d24004eb4d557a5b176553fdceef0c06056f8d93a607198ac558379c"),
    (6, "decision-shadow:technical-readiness:0dd809ea4b42", "MEASUREMENT", "station:KATL",
     "bc8092d00688f9127719927ebed4ff5f4512b939d77ae9b558859d2e5221662c"),
)
CATALOGUE_SHA256 = digest({"profile_id": PROFILE_ID, "catalogue": CATALOGUE})
HEX = re.compile(r"[0-9a-f]{64}\Z")
METADATA_KEYS = frozenset({"station", "city", "country", "latitude", "longitude",
                            "elevation_m", "timezone", "settlement_source",
                            "observation_providers", "forecast_providers",
                            "source_payload_sha256", "retrieved_at"})
RULE_KEYS = frozenset({"version", "fingerprint", "preimage", "source_event_sha256",
                       "source_received_at", "source_receipt_seq", "changed", "quarantined",
                       "state", "cancel_managed_new_risk_requested",
                       "preserve_fills_and_reconciliation", "automatic_recertification"})
PREIMAGE_KEYS = frozenset({"version", "event_id", "title", "strict_contract", "station",
                           "city", "target_date", "timezone", "unit", "family", "statistic",
                           "observation_population", "precision_rounding", "primary_source",
                           "source_family", "fallback_policy", "correction_policy",
                           "finality_and_deadline_policy", "no_data_outcome", "partition",
                           "metadata_fingerprint", "compiler_version", "semantic_profile_version",
                           "financial_authority"})
STRICT_CONTRACT_KEYS = frozenset({"version", "event_id", "family", "location",
                                  "operative_rules", "operative_source", "questions",
                                  "sha256", "station", "target_date", "unit"})
PARTITION_KEYS = frozenset({"market_id", "condition_id", "question", "lower", "upper",
                            "unit", "yes_token", "no_token"})
READINESS_KEYS = frozenset({"kind", "claim_not_protected_approval", "financial_authority",
                             "live_authority", "real_orders", "release_git_sha", "release_tree_sha",
                             "commissioning_census", "prior_book_semantics_tests",
                             "prior_exact_katl_book_replay", "runtime_candidate_shadow_adjacent_tests",
                             "runtime_health_nested_transaction_fix", "source_view_regression_tests"})
EDGES = {2: (1,), 4: (3,), 5: (2,), 6: (4, 5, 2)}


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise SeedPlanError("HISTORICAL_SIX_" + reason)


def _finite(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _all_finite(value: object) -> bool:
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is dict:
        return all(_all_finite(v) for v in value.values())
    if type(value) is list:
        return all(_all_finite(v) for v in value)
    return True


def _capture(body: dict, kind: str) -> dict:
    _need(set(body) == CAPTURE_KEYS and body["source_kind"] == kind,
          "CAPTURE_ENVELOPE")
    _need(body["evidence_class"] == "PUBLIC_OBSERVED" and
          all(type(body[k]) is str and body[k] for k in
              ("provider", "source_identity", "revision")), "CAPTURE_SOURCE")
    _need(all(body[k] is None or _finite(body[k]) for k in
              ("observed_at", "issued_at", "published_at")) and
          _finite(body["received_at"]) and
          body["received_at"] <= body["available_at"] and
          body["available_at"] == body["recorded_at"], "CAPTURE_TIME")
    _need(type(body["payload"]) is dict, "CAPTURE_PAYLOAD")
    return body["payload"]


def _audit(body: dict, keys: frozenset) -> tuple[dict, list]:
    _need(set(body) == AUDIT_KEYS and type(body["details"]) is dict and
          set(body["details"]) == keys and type(body["evidence"]) is list,
          "AUDIT_ENVELOPE")
    return body["details"], body["evidence"]


def _validate_six(rows: list, bodies: list[dict]) -> None:
    for index, (row, body, pin) in enumerate(zip(rows, bodies, CATALOGUE), 1):
        _need((row["seq"], row["record_id"], row["kind"], row["event_id"],
               row["body_sha256"]) == pin, "CATALOGUE")
        _need(body.get("namespace") == NAMESPACE and
              body.get("financial_authority") is False and
              (body.get("record_id"), body.get("kind"), body.get("event_id")) ==
              (pin[1], pin[2], pin[3]), "BODY_IDENTITY")
        _need(_all_finite(body), "NONFINITE")
        _need(body["available_at"] == row["available_at"] and
              body["recorded_at"] == row["recorded_at"] and
              body["available_at"] == body["recorded_at"], "ROW_TIME")
    full, extracted, station, metadata, rule, readiness = bodies
    full_payload = _capture(full, "RULES")
    event_payload = _capture(extracted, "RULES")
    station_payload = _capture(station, "STATION_METADATA")
    _need(set(full_payload) == {"endpoint", "http_status", "request_params", "response"}
          and type(full_payload["endpoint"]) is str
          and type(full_payload["http_status"]) is int
          and type(full_payload["request_params"]) is dict
          and set(full_payload["request_params"]) == {"id"}
          and full_payload["request_params"]["id"] == HISTORICAL_EVENT
          and type(full_payload["response"]) is list
          and len(full_payload["response"]) == 1, "FULL_WRAPPER")
    _need(set(event_payload) == {"event", "source_capture_id", "source_capture_sha256"}
          and type(event_payload["event"]) is dict
          and event_payload["event"] == full_payload["response"][0]
          and event_payload["source_capture_id"] == CATALOGUE[0][1]
          and event_payload["source_capture_sha256"] == CATALOGUE[0][4], "EVENT_CAPTURE")
    _need(type(station_payload) is dict and
          set(station_payload) == {"@context", "geometry", "id", "properties", "type"},
          "STATION_PROVIDER")
    md, _ = _audit(metadata, frozenset({"action", "metadata", "metadata_fingerprint",
                                         "material_changed", "state", "reason"}))
    material = md["metadata"]
    _need(md["action"] == "METADATA" and md["material_changed"] is False and
          md["state"] == "DISCOVERED" and md["reason"] == "OBSERVED_NOT_CERTIFIED" and
          type(material) is dict and set(material) == METADATA_KEYS and
          material["station"] == "KATL" and
          all(type(material[k]) is str and material[k] for k in
              ("city", "timezone", "settlement_source")) and
          (material["country"] is None or type(material["country"]) is str) and
          all(_finite(material[k]) for k in ("latitude", "longitude", "retrieved_at")) and
          (material["elevation_m"] is None or _finite(material["elevation_m"])) and
          all(type(material[k]) is list and all(type(v) is str for v in material[k])
              for k in ("observation_providers", "forecast_providers")) and
          material["source_payload_sha256"] == digest(station_payload) and
          material["retrieved_at"] == station["received_at"], "METADATA_PAYLOAD")
    fingerprint_material = dict(material)
    fingerprint_material.pop("source_payload_sha256")
    fingerprint_material.pop("retrieved_at")
    for key in ("observation_providers", "forecast_providers"):
        fingerprint_material[key] = sorted(set(fingerprint_material[key]))
    _need(md["metadata_fingerprint"] == digest(fingerprint_material),
          "METADATA_FINGERPRINT")
    rd, _ = _audit(rule, RULE_KEYS)
    preimage = rd["preimage"]
    _need(type(preimage) is dict and set(preimage) == PREIMAGE_KEYS and
          preimage["event_id"] == HISTORICAL_EVENT and
          preimage["target_date"] == HISTORICAL_DAY.isoformat() and
          preimage["station"] == "KATL" and
          preimage["metadata_fingerprint"] == md["metadata_fingerprint"] and
          preimage["financial_authority"] is False and
          type(preimage["strict_contract"]) is dict and
          set(preimage["strict_contract"]) == STRICT_CONTRACT_KEYS and
          preimage["strict_contract"].get("event_id") == HISTORICAL_EVENT and
          preimage["strict_contract"].get("target_date") == HISTORICAL_DAY.isoformat() and
          type(preimage["partition"]) is list and
          all(type(part) is dict and set(part) == PARTITION_KEYS for part in
              preimage["partition"]), "RULE_PREIMAGE")
    _need(rd["fingerprint"] == digest(preimage) and
          rd["source_event_sha256"] == digest(event_payload["event"]) and
          type(rd["source_receipt_seq"]) is int and rd["source_receipt_seq"] == 2 and
          rd["source_received_at"] == extracted["received_at"] and
          rd["changed"] is False and rd["quarantined"] is False and
          rd["cancel_managed_new_risk_requested"] is False and
          rd["automatic_recertification"] is False and
          rd["preserve_fills_and_reconciliation"] is True and
          rd["state"] == "SEMANTICS_OBSERVED", "RULE_BINDING")
    readiness_details, _ = _audit(readiness, READINESS_KEYS)
    _need(readiness_details["kind"] == "SHADOW_TECHNICAL_READINESS_CLAIM" and
          readiness_details["claim_not_protected_approval"] is True and
          all(readiness_details[k] is False for k in
              ("financial_authority", "live_authority", "real_orders")) and
          readiness_details["release_git_sha"] ==
          "0dd809ea4b42cce9026225e390856509d0b2041c" and
          readiness_details["runtime_health_nested_transaction_fix"] is True and
          type(readiness_details["commissioning_census"]) is dict and
          set(readiness_details["commissioning_census"]) ==
          {"books", "errors", "gefs_fields", "outcome"} and
          all(type(readiness_details["commissioning_census"][k]) is int for k in
              ("books", "errors", "gefs_fields")) and
          type(readiness_details["commissioning_census"]["outcome"]) is str and
          all(type(readiness_details[k]) is dict and
              set(readiness_details[k]) == {"passed", "failed"} and
              all(type(v) is int for v in readiness_details[k].values()) for k in
              ("prior_book_semantics_tests", "prior_exact_katl_book_replay",
               "runtime_candidate_shadow_adjacent_tests", "source_view_regression_tests")),
          "READINESS_SCOPE")
    for index, expected in EDGES.items():
        if index == 2:  # Capture lineage is carried by the reviewed payload pair.
            continue
        refs = bodies[index - 1]["evidence"]
        _need(len(refs) == len(expected), "EDGE_COUNT")
        for ref, predecessor in zip(refs, expected):
            _need(type(ref) is dict and set(ref) == {"id", "sha256"} and
                  ref["id"] == CATALOGUE[predecessor - 1][1] and
                  ref["sha256"] == CATALOGUE[predecessor - 1][4] and
                  predecessor < index and
                  bodies[predecessor - 1]["available_at"] <= bodies[index - 1]["available_at"],
                  "EDGE_BINDING")
    _need(full["received_at"] <= extracted["received_at"] and
          extracted["received_at"] <= rule["recorded_at"] and
          station["received_at"] <= metadata["recorded_at"], "CAUSAL_TIME")


def plan_historical_six_seed(snapshot: Path, expected_source_sha256: str, *,
                             target_date: str, target_event_id: str,
                             target_generation: str) -> SeedPlan:
    """Return historical rows and a canonical manifest from one sealed image."""
    _need(type(expected_source_sha256) is str and HEX.fullmatch(expected_source_sha256)
          is not None, "SOURCE_PIN_REQUIRED")
    try:
        target_day = date.fromisoformat(target_date)
    except (TypeError, ValueError) as exc:
        raise SeedPlanError("HISTORICAL_SIX_TARGET_DATE") from exc
    _need(target_day.isoformat() == target_date and target_day > HISTORICAL_DAY and
          type(target_event_id) is str and bool(target_event_id) and
          target_event_id != HISTORICAL_EVENT and
          type(target_generation) is str and bool(target_generation) and
          len(target_event_id) <= 256 and len(target_generation) <= 256,
          "TARGET_CONTEXT")
    snapshot = Path(snapshot)
    source_bytes, identity = _sealed_bytes(snapshot)
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    _need(source_sha == expected_source_sha256, "SOURCE_IDENTITY")
    try:
        with closing(_memory_db(source_bytes)) as db:
            _validate_schema(db)
            _need(db.execute("PRAGMA quick_check").fetchone()[0] == "ok", "SOURCE_INTEGRITY")
            source_rows = [_row(db, pin[1]) for pin in CATALOGUE]
            bodies = [_validate_row(row) for row in source_rows]
            _validate_six(source_rows, bodies)
            rows = tuple(tuple(row[field] for field in COLUMNS) for row in source_rows)
    except sqlite3.DatabaseError as exc:
        raise SeedPlanError("HISTORICAL_SIX_SQLITE_INVALID") from exc
    _same_path(snapshot, identity)
    mapping = [dict(source_seq=pin[0], local_seq=pin[0], record_id=pin[1],
                    kind=pin[2], event_id=pin[3], body_sha256=pin[4]) for pin in CATALOGUE]
    manifest = dict(version="alpha_v11_daily_seed_plan_v2", profile_id=PROFILE_ID,
                    profile_catalogue_sha256=CATALOGUE_SHA256, namespace=NAMESPACE,
                    historical_role="HISTORICAL_EVIDENCE_ONLY",
                    barrier_reconciliation="UNPERFORMED", runtime_admission=False,
                    source_snapshot_sha256=source_sha, target_date=target_date,
                    target_event_id=target_event_id, target_generation=target_generation,
                    mapping=mapping, graph_edges={str(k): list(v) for k, v in EDGES.items()},
                    rule_receipt_seq=2, financial_authority=False)
    return SeedPlan(rows, manifest, digest(manifest))


def verify_existing_historical_six_daily(path: Path, plan: SeedPlan,
                                         expected_manifest_sha256: str) -> int:
    """Verify a sealed daily image, including its exact six-row genesis."""
    manifest = plan.manifest
    expected_mapping = [dict(source_seq=pin[0], local_seq=pin[0], record_id=pin[1],
                             kind=pin[2], event_id=pin[3], body_sha256=pin[4])
                        for pin in CATALOGUE]
    _need(type(manifest) is dict and
          set(manifest) == {"version", "profile_id", "profile_catalogue_sha256",
                            "namespace", "historical_role", "barrier_reconciliation",
                            "runtime_admission", "source_snapshot_sha256", "target_date",
                            "target_event_id", "target_generation", "mapping", "graph_edges",
                            "rule_receipt_seq", "financial_authority"} and
          manifest["version"] == "alpha_v11_daily_seed_plan_v2" and
          manifest["profile_id"] == PROFILE_ID and
          manifest["profile_catalogue_sha256"] == CATALOGUE_SHA256 and
          manifest["historical_role"] == "HISTORICAL_EVIDENCE_ONLY" and
          manifest["barrier_reconciliation"] == "UNPERFORMED" and
          manifest["runtime_admission"] is False and
          manifest["mapping"] == expected_mapping and
          manifest["graph_edges"] == {str(k): list(v) for k, v in EDGES.items()} and
          manifest["rule_receipt_seq"] == 2 and
          type(manifest["source_snapshot_sha256"]) is str and
          HEX.fullmatch(manifest["source_snapshot_sha256"]) is not None and
          type(manifest["target_event_id"]) is str and
          manifest["target_event_id"] != HISTORICAL_EVENT and
          type(manifest["target_generation"]) is str and
          bool(manifest["target_generation"]) and
          type(manifest["target_date"]) is str and
          bool(manifest["target_date"]) and
          len(plan.rows) == 6 and
          all((row[0], row[1], row[2], row[3], row[7]) == pin
              for row, pin in zip(plan.rows, CATALOGUE)), "PLAN_PROFILE")
    return verify_existing_daily(path, plan, expected_manifest_sha256)
