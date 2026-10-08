"""Offline, nonlaunchable G3-L inventory and conservative capacity planner.

This module has no transport, private-store writer, or launch entrypoint. A
complete inventory is only a packet for independent review; the V4 validator
and detached approval remain separate gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from tools.v11_multimodel_panel import canonical
from tools.v11_r09_gate3_launch import (
    FIELD_LIMITS, MAX_BYTES, PINNED_ADDENDUM_COMMIT, PINNED_ADDENDUM_DOC,
    PINNED_ADDENDUM_TREE, PINNED_ORIGINAL_COMMIT, PINNED_ORIGINAL_DOC,
    PINNED_ORIGINAL_TREE, SLOTS,
)
from tools.v11_r09_gate3_launch_v4 import (
    GROUPS as V4_GROUPS, SCHEMA as V4_SCHEMA,
    JOURNAL_MAX_BYTES, REPORT_RESERVE_BYTES, STORE_MAX_EVENTS,
    STORE_MAX_OBJECTS, STORE_OBJECT_MAX_BYTES, SESSION_JOURNAL_MAX_EVENTS,
    PINNED_DESIGN_COMMIT, PINNED_DESIGN_TREE, PINNED_DESIGN_DOC,
    PINNED_DESIGN_REVIEW_REPORT, PINNED_DESIGN_REVIEW_TERMINAL,
)

SCHEMA = "R09_GATE3_G3L_OFFLINE_PREP_V1"
PROVIDERS = ("GEFS", "IFS", "AIFS")
PURPOSES = ("INDEX", "OBJECT_ID", "METADATA", "FIELD")
INDEX_CAP = 3 * 1024**2
METADATA_CAP = 4 * 1024**2
DISK_FLOOR = 2 * 1024**3
MEMORY_FLOOR = 512 * 1024**2
DECODED_RESERVE = 64 * 1024**2
HEADROOM = 32 * 1024**2  # extra unclaimed local capacity, never a cap increase
MAX_REQUESTS = 3600
MAX_ELAPSED = 10800
DEADLINE = 30
START_INTERVAL = 2
PROCESSING_SECONDS = 60
FINALIZATION_SECONDS = 60
MAX_BODY_CHUNKS = 32
PLACEHOLDER = re.compile(r"(?i)(placeholder|dummy|synthetic|fixture|example|todo|tbd|unknown|pending|not[-_ ]?available|^0+$)")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")

# Exact identities to be supplied from separately reviewed real evidence.
# All entries require a sealed artifact reference and independent review record.
# "run" entries must be tied to the chosen run; "window" entries to the
# selected acquisition window. Accepted design/code evidence may be older.
REQUIRED = {
    "code": (
        "collector_commit_tree", "launch_validator_commit_tree",
        "transport_commit_tree", "decoder_commit_tree", "clock_recorder_commit_tree",
        "source_file_hashes", "dependency_build_lock", "runtime_entrypoint_review",
        "slice3_exact_commit_review", "mapping_exact_commit_review",
    ),
    "protocol": (
        "g3p_original_commit_tree_document", "g3p_original_review_terminal",
        "g3p_addendum_commit_tree_document", "g3p_addendum_review_terminal",
        "g3i_collector_review_terminal", "g3i_provider_bound_review_terminal",
        "g3i_gefs_ceiling_review_terminal", "transport_design_review_terminal",
        "g3i_composition_review_terminal",
    ),
    "cohort": (
        "station_version_coordinates_timezone", "tzdata_bytes", "pre_weather_selection_rationale",
        "high_low_event_ids", "official_rule_rounding_buckets", "settlement_source",
        "metadata_bytes_receipt", "requested_gate2_key_mapping",
    ),
    "sources": tuple(
        f"{p.lower()}_{item}" for p in PROVIDERS for item in (
            "operational_release_dossier", "release_document_retrieval",
            "licence_anonymous_access", "control_perturbed_mapping",
            "grib_identity_decoder_build", "purpose_endpoint_contracts",
            "current_run_index_object_range", "publication_attestation_or_absence_reason",
        )
    ),
    "network": (
        "exact_origin_path_purpose_allowlist", "dns_tls_build_peer_policy",
        "anonymous_no_retry_credential_policy", "restriction_domain_lineage",
        "ecmwf_503_429_expiry_resumption_review", "preflight_approval_receipts_if_used",
    ),
    "storage": (
        "private_root_owner_mode_dev_inode", "denial_root_history_head",
        "session_report_root_identities", "exclusive_lock_atomic_seal_qualification",
        "physical_persistence_review", "live_disk_memory_quota_measurement",
    ),
    "clocks": (
        "unprivileged_recorder_build_method", "calibration_sync_uncertainty",
        "host_boot_monotonic_identity", "measurement_age_policy",
    ),
    "schedule": (
        "run_candidate_readiness_evidence", "full_2713_slot_inventory_digest",
        "ordered_subset_request_paths_ranges", "shared_index_object_cache_binding",
        "purpose_reservations_and_resource_quota", "terminal_reason_precedence",
        "four_phase_receipt_schema", "observed_size_evidence_review",
    ),
    "review": (
        "private_v4_manifest_canonical_bytes", "private_v4_manifest_digest",
        "detached_g3l_report", "detached_g3l_completed_terminal",
    ),
}
RUN_SPECIFIC = {f"sources.{p.lower()}_current_run_index_object_range" for p in PROVIDERS} | {
    "cohort.metadata_bytes_receipt", "schedule.run_candidate_readiness_evidence",
}
# Live disk/memory capacity is only meaningful as close as possible to the
# acquisition window, not tied to any specific model run; it is window-scoped
# only, with the tighter window freshness bound (not the looser run bound).
WINDOW_SPECIFIC = {"clocks.calibration_sync_uncertainty", "clocks.host_boot_monotonic_identity",
                   "storage.live_disk_memory_quota_measurement"}
ALL_IDS = tuple(f"{group}.{name}" for group, names in REQUIRED.items() for name in names)
# The detached G3-L review report and its completed terminal are outputs of
# reviewing an already-assembled package, not inputs to assembling it. They
# must stay unresolved at the pre-review stage and are only required once the
# separate, later final stage validates the completed review.
FINAL_ONLY_IDS = ("review.detached_g3l_report", "review.detached_g3l_completed_terminal")
PRE_REVIEW_IDS = tuple(i for i in ALL_IDS if i not in FINAL_ONLY_IDS)
STAGES = ("PRE_REVIEW", "FINAL")


def _parse_json(raw: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate key: {key}")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs,
                       parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    if type(value) is not dict:
        raise ValueError("root must be an object")
    return value


def slot_inventory(run_utc: int) -> list[list]:
    if type(run_utc) is not int or run_utc % 86400:
        raise ValueError("run must be exact 00Z Unix seconds")
    return [[p, run_utc, member, hour] for p in PROVIDERS
            for member in range(SLOTS[p][0])
            for hour in range(0, 73, SLOTS[p][1])]


def _objects_for_requests(n: int) -> int:
    nodes, aggregates = n, 0
    while nodes > 1:
        nodes = (nodes + 255) // 256
        aggregates += nodes
    return 2 * n + aggregates


def _resources(n: int, body: int) -> dict:
    objects = _objects_for_requests(n)
    return {
        "requests": n, "body_reservation_bytes": body,
        "store_objects": objects, "store_events_upper": 4 * n + 1,
        "session_events_upper": 8 * n + 1,
        "budget_events_upper": (2 + MAX_BODY_CHUNKS) * n + 1,
        "serial_seconds_upper": n * DEADLINE + max(0, n - 1) * START_INTERVAL
        + PROCESSING_SECONDS + FINALIZATION_SECONDS,
        "local_storage_quota_bytes": 4 * JOURNAL_MAX_BYTES + REPORT_RESERVE_BYTES
        + objects * STORE_OBJECT_MAX_BYTES + DECODED_RESERVE + body,
    }


def _fits(r: dict, free_disk: int, available_memory: int) -> bool:
    return (
        r["requests"] <= MAX_REQUESTS
        and r["body_reservation_bytes"] <= MAX_BYTES
        and r["store_objects"] <= STORE_MAX_OBJECTS
        and r["store_events_upper"] <= STORE_MAX_EVENTS
        and r["session_events_upper"] <= SESSION_JOURNAL_MAX_EVENTS
        and r["budget_events_upper"] <= 131072
        and r["serial_seconds_upper"] <= MAX_ELAPSED
        and r["local_storage_quota_bytes"] + HEADROOM <= free_disk - DISK_FLOOR
        and available_memory >= MEMORY_FLOOR + DECODED_RESERVE + HEADROOM
    )


def plan(run_utc: int, free_disk: int, available_memory: int) -> dict:
    """Use one INDEX, OBJECT_ID and METADATA per field, with no cache credit.

    These are deliberately upper reservations for a possible ordered subset.
    Unknown index lengths use the full index cap. Real mappings/ranges are
    still required; this plan never supplies them.
    """
    if any(type(x) is not int or x < 0 for x in (free_disk, available_memory)):
        raise ValueError("resource measurements must be nonnegative integers")
    slots = slot_inventory(run_utc)
    # Stable round-robin across providers; no forecast value or outcome enters
    # this order. Each provider retains its native member/hour order.
    by_provider = {p: [i for i, slot in enumerate(slots) if slot[0] == p]
                   for p in PROVIDERS}
    ordered = [i for rank in range(max(map(len, by_provider.values())))
               for p in PROVIDERS for i in by_provider[p][rank:rank + 1]]
    attempt = []
    body = 0
    for index in ordered:
        provider = slots[index][0]
        next_body = body + INDEX_CAP + 2 * METADATA_CAP + FIELD_LIMITS[provider]
        if not _fits(_resources(4 * (len(attempt) + 1), next_body),
                     free_disk, available_memory):
            break
        attempt.append(index)
        body = next_body
    resources = _resources(4 * len(attempt), body)
    attempt_set = set(attempt)
    return {
        "schema": SCHEMA, "launchable": False, "capture_mode": "BOUNDED_FEASIBILITY",
        "fallback_mode": "NONE", "run_utc": run_utc,
        "slot_inventory_sha256": hashlib.sha256(canonical(slots)).hexdigest(),
        "denominator": len(slots), "provider_denominator": {
            p: sum(s[0] == p for s in slots) for p in PROVIDERS},
        "attempt_slots": attempt,
        "slot_rows": [{"slot_index": i, "slot": slot,
                       "planned_state": "ATTEMPT_PROPOSED" if i in attempt_set
                       else "NOT_ATTEMPTED_RESOURCE_CAP",
                       "reason": None if i in attempt_set else "NOT_ATTEMPTED_BUDGET"}
                      for i, slot in enumerate(slots)],
        "resources": resources,
        "bounds": {"max_requests": MAX_REQUESTS, "max_received_bytes": MAX_BYTES,
                   "max_elapsed_seconds": MAX_ELAPSED, "index_bytes": INDEX_CAP,
                   "metadata_bytes": METADATA_CAP, "field_bytes": FIELD_LIMITS,
                   "store_max_objects": STORE_MAX_OBJECTS,
                   "store_max_events": STORE_MAX_EVENTS,
                   "session_max_events": SESSION_JOURNAL_MAX_EVENTS,
                   "budget_max_events": 131072, "journal_bytes_each": JOURNAL_MAX_BYTES,
                   "journal_count": 4, "report_reserve_bytes": REPORT_RESERVE_BYTES,
                   "decoded_reserve_bytes": DECODED_RESERVE,
                   "additional_headroom_bytes": HEADROOM,
                   "disk_floor_bytes": DISK_FLOOR, "memory_floor_bytes": MEMORY_FLOOR},
        "resource_snapshot": {"free_disk_bytes": free_disk,
                              "available_memory_bytes": available_memory},
        "method": "ONE_UNSHARED_INDEX_OBJECT_ID_METADATA_AND_FIELD_PER_SLOT_FULL_CAPS",
    }


def freeze_checklist(target_date: str) -> dict:
    target = date.fromisoformat(target_date)
    if target.isoformat() != target_date:
        raise ValueError("target date must be canonical YYYY-MM-DD")
    prior = target - timedelta(days=1)
    def seconds(hour):
        return int(datetime(prior.year, prior.month, prior.day, hour,
                            tzinfo=timezone.utc).timestamp())
    return {
        "station_id": None, "station_version": None, "latitude": None,
        "longitude": None, "iana_timezone": None, "tzdata_ref": None,
        "target_local_date": target.isoformat(), "events": None,
        "event_ids": None, "rule_and_buckets_ref": None,
        "settlement_ref": None, "metadata_receipt_ref": None,
        "pre_weather_selection_ref": None, "requested_gate2_keys_ref": None,
        "local_day_start_utc": None, "local_day_end_utc": None,
        "window_start_utc": seconds(14), "last_acquisition_utc": seconds(17),
        "decision_utc": seconds(18), "review_completed_utc": None,
        "allowed_cycles": [0], "max_run_age_seconds": 86400,
        "run_selection": "LATEST_COMPLETE_READY", "fallback_mode": "NONE",
        "selected_run_utc": None, "max_uncertainty_seconds": 1,
        "selection_rule": "freeze before weather values or labels; one station and one future local day; HIGH and/or LOW share raw slots",
    }


MAX_WINDOW_SEARCH_DAYS = 30


def next_window_candidate(now_utc: int, *, max_days_ahead: int = MAX_WINDOW_SEARCH_DAYS) -> dict:
    """Suggest the earliest future target date whose frozen window has not
    already started, as of an explicit UTC ``now_utc``.

    Pure and offline: reads no clock, touches no file, and makes no
    provider/network/runtime call. This only proposes a fresh
    ``freeze_checklist`` skeleton and the existing PRE_REVIEW
    missing-identity screen for the suggested date. It never inspects,
    mutates, or re-dates any already reviewed/frozen package, never sets
    ``launchable`` or any authority flag true, and never grants a
    qualification credit. ``max_days_ahead`` counts future UTC target
    dates, from tomorrow (offset 1) through the inclusive bound. Calendar
    exhaustion raises ``ValueError`` so the call always terminates.
    """
    if type(now_utc) is not int or now_utc <= 0:
        raise ValueError("now_utc must be a positive integer Unix timestamp")
    if type(max_days_ahead) is not int or not (1 <= max_days_ahead <= MAX_WINDOW_SEARCH_DAYS):
        raise ValueError(f"max_days_ahead must be an int in [1, {MAX_WINDOW_SEARCH_DAYS}]")
    try:
        now = datetime.fromtimestamp(now_utc, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError("now_utc is not a representable UTC instant") from exc
    if int(now.timestamp()) != now_utc:
        raise ValueError("now_utc must be exact whole UTC seconds")
    anchor = now.date()
    for offset in range(1, max_days_ahead + 1):
        try:
            target = anchor + timedelta(days=offset)
        except OverflowError as exc:
            raise ValueError("no eligible candidate date found within the representable calendar") from exc
        target_date = target.isoformat()
        checklist = freeze_checklist(target_date)
        if checklist["window_start_utc"] > now_utc:
            inventory = {"schema": SCHEMA, "launchable": False,
                        "target_date": target_date, "evidence": {k: None for k in ALL_IDS}}
            return {
                "schema": SCHEMA, "launchable": False,
                "status": "CANDIDATE_TARGET_DATE_SUGGESTED",
                "now_utc": now_utc, "target_date": target_date,
                "days_from_now_searched": offset,
                "freeze_checklist": checklist,
                "missing_evidence": check_inventory(inventory, target_date=target_date,
                                                    now_utc=now_utc, stage="PRE_REVIEW"),
                "handoff": ("Suggestion only. No date or cohort is selected, frozen, "
                           "approved, or reviewed by this call; it never mutates an "
                           "existing dated package and never rolls the date of an "
                           "already-frozen window."),
            }
    raise ValueError("no eligible candidate date found within the bounded search window")


def private_v4_null_template(target_date: str) -> dict:
    """Exact V4 key skeleton with no invented evidence or approval."""
    frozen = freeze_checklist(target_date)
    empty = lambda names: {name: None for name in names.split()}
    components = ("collector", "launch_validator", "transport", "decoder", "clock_recorder")
    source_keys = ("dossier release_document licence index_evidence range_evidence "
                   "decoder_build origin path_spec publication_attestation "
                   "publication_absence_reason member_range native_hours identity_pins "
                   "effective_run_start_utc effective_run_end_utc control_domain purpose_mappings")
    mapping_keys = "origin path_spec control_domain_id mapping_evidence validator response_contract"
    sources = {}
    for provider in PROVIDERS:
        source = empty(source_keys)
        source["member_range"] = [0, SLOTS[provider][0] - 1]
        source["native_hours"] = list(range(0, 73, SLOTS[provider][1]))
        source["purpose_mappings"] = {purpose: empty(mapping_keys)
                                      for purpose in ("FIELD", "INDEX", "OBJECT_ID", "METADATA", "PROBE")}
        sources[provider] = source
    result = {
        "identity": empty("schema pilot_id purpose capture_mode created_ref financial_authority promotion_authority host_approved launch_authority mapping_scope"),
        "code": {"object_format": None,
                 "components": {name: empty("commit_oid tree_oid path sha256") for name in components},
                 "dependency_lock": None},
        "protocol": empty("original_commit_oid original_tree_oid original_document addendum_commit_oid addendum_tree_oid addendum_document reviews reviewed_design"),
        "storage": empty("root owner_uid mode directory_dev directory_inode layout exclusive_lock atomic_fsync_seal report_reserve_bytes no_reclamation"),
        "cohort": empty("station_id station_version latitude longitude timezone tzdata target_date events units rounding buckets rule settlement metadata selection requested_keys gate2_trial_keys city_day"),
        "time": empty("preregistered_utc review_completed_utc window_start_utc last_acquisition_utc decision_utc feature_seal_upper_utc decision_lower_utc expires_utc local_day_start_utc local_day_end_utc uncertainty_seconds"),
        "sources": sources,
        "runs_and_slots": empty("allowed_cycles max_run_age_seconds run_selection fallback_mode run_utc candidates slots"),
        "network": empty("origins methods path_specs purposes index_binding anonymous redirects cookies netrc ambient_proxies signed_urls retries dns_tls_policy restriction_lineage post_window_labels endpoints"),
        "limits": empty("max_requests max_received_bytes max_elapsed_seconds single_in_flight min_start_interval_seconds request_deadline_seconds max_index_bytes field_bytes min_free_disk_bytes min_available_memory_bytes headers metadata decoded report_storage"),
        "schedule": empty("slot_inventory_sha256 attempt_slots requests observed_sizes estimated_full_raw_bytes reservation_total_bytes full_denominator capture_mode processing_seconds finalization_seconds"),
        "clocks_and_receipts": {"preregistration": empty("method host_boot sync_evidence max_measurement_age_seconds"),
                                "observation_schema": empty("phases uncertainty_seconds_cap cutoff_utc")},
        "accounting": empty("terminal_precedence all_reasons journal_hash_chain no_silent_retry expired_local_only raw_partition provider_eligibility all_provider_intersection diagnostics_no_credit"),
        "runtime": {"policy": None, "schedule_digest": None,
                    "denial_root": empty("descriptor expected_history_head"),
                    "session_root": None, "report_root": None,
                    "purpose_plan": None, "journal_bounds": None,
                    "clock_policy": None,
                    "resource_bounds": empty("session_journal_max_bytes denial_journal_max_bytes store_journal_max_bytes journal_record_max_bytes session_journal_max_events denial_journal_max_events store_max_events store_object_max_bytes store_max_objects descriptor_max_bytes clock_record_max_bytes receipt_max_dependencies max_body_chunks_per_request report_reserve_bytes required_store_objects local_storage_quota_bytes")},
    }
    if set(result) != set(V4_GROUPS):
        raise AssertionError("V4 group schema drift")
    result["identity"].update(schema=V4_SCHEMA, purpose="NONFINANCIAL_RESEARCH",
                              capture_mode="BOUNDED_FEASIBILITY", financial_authority=False,
                              promotion_authority=False, host_approved=False,
                              launch_authority=False)
    result["protocol"].update(original_commit_oid=PINNED_ORIGINAL_COMMIT,
                              original_tree_oid=PINNED_ORIGINAL_TREE,
                              addendum_commit_oid=PINNED_ADDENDUM_COMMIT,
                              addendum_tree_oid=PINNED_ADDENDUM_TREE,
                              reviews=[{"name": name, "report": None, "terminal": None}
                                       for name in ("original_protocol", "collector",
                                                    "provider_bound", "gefs_ceiling",
                                                    "launch_addendum")],
                              reviewed_design={"commit_oid": PINNED_DESIGN_COMMIT,
                                               "tree_oid": PINNED_DESIGN_TREE,
                                               "document": None, "report": None,
                                               "terminal": None})
    result["cohort"].update(target_date=target_date, units="CELSIUS")
    result["time"].update(window_start_utc=frozen["window_start_utc"],
                          last_acquisition_utc=frozen["last_acquisition_utc"],
                          decision_utc=frozen["decision_utc"], uncertainty_seconds=1)
    result["runs_and_slots"].update(allowed_cycles=[0], max_run_age_seconds=86400,
                                    run_selection="LATEST_COMPLETE_READY", fallback_mode="NONE",
                                    run_utc={p: None for p in PROVIDERS},
                                    candidates=None, slots=None)
    result["network"].update(methods=["GET"], anonymous=True, redirects=False,
                             cookies=False, netrc=False, ambient_proxies=False,
                             signed_urls=False, retries=False, post_window_labels=False,
                             path_specs={p: None for p in PROVIDERS},
                             purposes=["FIELD", "INDEX", "OBJECT_ID", "METADATA", "PROBE"],
                             index_binding="ETAG_IF_RANGE")
    result["limits"].update(max_requests=MAX_REQUESTS, max_received_bytes=MAX_BYTES,
                            max_elapsed_seconds=MAX_ELAPSED, single_in_flight=True,
                            min_start_interval_seconds=START_INTERVAL,
                            request_deadline_seconds=DEADLINE, max_index_bytes=INDEX_CAP,
                            field_bytes=FIELD_LIMITS, min_free_disk_bytes=DISK_FLOOR,
                            min_available_memory_bytes=MEMORY_FLOOR, headers=4096,
                            metadata=METADATA_CAP, decoded=DECODED_RESERVE,
                            report_storage=REPORT_RESERVE_BYTES)
    result["schedule"].update(full_denominator=2713,
                              capture_mode="BOUNDED_FEASIBILITY",
                              processing_seconds=PROCESSING_SECONDS,
                              finalization_seconds=FINALIZATION_SECONDS)
    result["clocks_and_receipts"]["observation_schema"].update(
        phases=["request_start", "body_receipt", "decode_complete", "durable_seal"],
        uncertainty_seconds_cap=1)
    result["runtime"]["purpose_plan"] = {
        purpose: empty("requests reservation_bytes")
        for purpose in ("FIELD", "INDEX", "OBJECT_ID", "METADATA", "PROBE")}
    return result


def check_inventory(inventory: dict, *, target_date: str, now_utc: int,
                    stage: str = "PRE_REVIEW",
                    object_root: Path | None = None) -> list[dict]:
    """Return machine-readable findings. Never returns launch permission.

    PRE_REVIEW checks every identity except the two detached G3-L review
    outputs, which must stay unresolved: the completed review has not
    happened yet when a package is first assembled. FINAL additionally
    requires those two outputs, once the detached review is complete; it
    still never implies launch permission.
    """
    if stage not in STAGES:
        raise ValueError("stage must be PRE_REVIEW or FINAL")
    if set(inventory) != {"schema", "launchable", "target_date", "evidence"} or (
        inventory.get("schema") != SCHEMA or inventory.get("launchable") is not False
        or inventory.get("target_date") != target_date or type(inventory.get("evidence")) is not dict
    ):
        raise ValueError("prep inventory schema or target date mismatch")
    evidence = inventory["evidence"]
    if set(evidence) != set(ALL_IDS):
        raise ValueError("prep inventory evidence ID set mismatch")
    start = freeze_checklist(target_date)["window_start_utc"]
    run = start - 14 * 3600
    findings = []
    if now_utc >= start:
        findings.append({"id": "time.review_before_window", "state": "EXPIRED",
                         "reason": "review cannot finish before frozen acquisition start"})
    for item_id in ALL_IDS:
        item = evidence[item_id]
        if stage == "PRE_REVIEW" and item_id in FINAL_ONLY_IDS:
            if item is not None:
                findings.append({"id": item_id, "state": "INVALID",
                                 "reason": "detached review output supplied before the review stage"})
            continue
        if item is None:
            findings.append({"id": item_id, "state": "MISSING", "reason": "unfilled evidence"})
            continue
        if type(item) is not dict or set(item) != {"ref", "review_ref", "observed_utc", "scope"}:
            findings.append({"id": item_id, "state": "INVALID", "reason": "evidence entry schema"})
            continue
        ref, review = item["ref"], item["review_ref"]
        if any(type(x) is not dict or set(x) != {"sha256", "byte_length", "media_type", "path"}
               for x in (ref, review)):
            findings.append({"id": item_id, "state": "INVALID", "reason": "artifact reference schema"})
            continue
        if ref["sha256"] == review["sha256"]:
            findings.append({"id": item_id, "state": "INVALID",
                             "reason": "evidence and independent review share one artifact"})
            continue
        bad = False
        for label, artifact in (("ref", ref), ("review_ref", review)):
            h, size, media, path = (artifact[k] for k in
                                    ("sha256", "byte_length", "media_type", "path"))
            if (type(h) is not str or not SHA256.fullmatch(h) or PLACEHOLDER.search(h)
                or len(set(h)) < 8 or type(size) is not int or size <= 0
                or type(media) is not str or not media or PLACEHOLDER.search(media)
                or type(path) is not str or not path or PLACEHOLDER.search(path)):
                findings.append({"id": item_id, "state": "INVALID", "reason": f"{label} placeholder or malformed"})
                bad = True
                break
            if object_root is None:
                findings.append({"id": item_id, "state": "UNVERIFIED", "reason": "private object root not supplied"})
                bad = True
                break
            root = object_root.resolve()
            relative = Path(path)
            candidate = root / relative
            try:
                if (relative.is_absolute() or ".." in relative.parts
                    or any((root.joinpath(*relative.parts[:i])).is_symlink()
                           for i in range(1, len(relative.parts) + 1))
                    or not candidate.resolve().is_relative_to(root) or not candidate.is_file()):
                    raise ValueError("unsafe or missing object")
                if candidate.stat().st_size != size:
                    raise ValueError("object length mismatch")
                hashed = hashlib.sha256()
                with candidate.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        hashed.update(chunk)
                if hashed.hexdigest() != h:
                    raise ValueError("object digest or length mismatch")
            except (OSError, ValueError):
                findings.append({"id": item_id, "state": "INVALID", "reason": f"{label} unresolved or mismatched"})
                bad = True
                break
        if bad:
            continue
        observed = item["observed_utc"]
        scope = item["scope"]
        if (type(observed) is not int or observed > now_utc or observed <= 0
            or type(scope) is not str or not scope.strip() or PLACEHOLDER.search(scope)):
            findings.append({"id": item_id, "state": "INVALID", "reason": "scope or observation clock"})
        elif item_id in RUN_SPECIFIC and scope != f"run:{run}":
            findings.append({"id": item_id, "state": "INVALID", "reason": "wrong run scope"})
        elif item_id in WINDOW_SPECIFIC and scope != f"window:{start}:{start + 10800}":
            findings.append({"id": item_id, "state": "INVALID", "reason": "wrong window scope"})
        elif item_id in RUN_SPECIFIC and observed < run:
            findings.append({"id": item_id, "state": "STALE", "reason": "predates selected 00Z run"})
        elif item_id in WINDOW_SPECIFIC and observed < start - 3600:
            findings.append({"id": item_id, "state": "STALE", "reason": "clock evidence predates window policy"})
    return findings


def make_report(*, target_date: str, run_utc: int, free_disk: int,
                available_memory: int, observed_utc: int) -> dict:
    if run_utc != freeze_checklist(target_date)["window_start_utc"] - 14 * 3600:
        raise ValueError("run does not match target-date 00Z candidate")
    inventory = {"schema": SCHEMA, "launchable": False,
                 "target_date": target_date, "evidence": {k: None for k in ALL_IDS}}
    return {
        "schema": SCHEMA, "launchable": False,
        "status": "BLOCKED_MISSING_REVIEWED_EVIDENCE",
        "snapshot_observed_utc": observed_utc,
        "reviewed_public_pins": {
            "original_protocol": {"commit_oid": PINNED_ORIGINAL_COMMIT,
                                  "tree_oid": PINNED_ORIGINAL_TREE,
                                  "document_sha256": PINNED_ORIGINAL_DOC},
            "launch_addendum": {"commit_oid": PINNED_ADDENDUM_COMMIT,
                                "tree_oid": PINNED_ADDENDUM_TREE,
                                "document_sha256": PINNED_ADDENDUM_DOC},
            "transport_design": {"commit_oid": PINNED_DESIGN_COMMIT,
                                 "tree_oid": PINNED_DESIGN_TREE,
                                 "document_sha256": PINNED_DESIGN_DOC,
                                 "review_report_sha256": PINNED_DESIGN_REVIEW_REPORT,
                                 "review_terminal_sha256": PINNED_DESIGN_REVIEW_TERMINAL},
            "historical_size_evidence_sha256":
                "efefd2396c35ac672e18094d611c7a956b709f7deeabb99d1e3e2f1831211eeb",
        },
        "freeze_checklist": freeze_checklist(target_date),
        "capacity_plan": plan(run_utc, free_disk, available_memory),
        "inventory_template": inventory,
        "stage": "PRE_REVIEW",
        "missing_evidence": check_inventory(inventory, target_date=target_date,
                                             now_utc=observed_utc, stage="PRE_REVIEW"),
        "handoff": "Independent review must validate actual V4 canonical bytes with validate_manifest_v4 and a detached exact-digest G3-L terminal; this report cannot authorize capture. The two detached-review evidence identities are deliberately unresolved here: they are outputs of that later review, checked only at the separate FINAL stage.",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--free-disk-bytes", type=int, required=True)
    parser.add_argument("--available-memory-bytes", type=int, required=True)
    parser.add_argument("--observed-utc", type=int, required=True)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--object-root", type=Path)
    parser.add_argument("--stage", choices=("pre_review", "final"), default="pre_review",
                        help="pre_review: input packet only, detached review outputs must stay "
                             "unresolved. final: also requires the completed detached review "
                             "outputs. Neither stage ever grants launch permission.")
    args = parser.parse_args(argv)
    checklist = freeze_checklist(args.target_date)
    if args.observed_utc <= 0:
        parser.error("observed-utc must be positive")
    if args.inventory:
        stage = "PRE_REVIEW" if args.stage == "pre_review" else "FINAL"
        success_status = ("ASSEMBLED_FOR_INDEPENDENT_REVIEW" if stage == "PRE_REVIEW"
                          else "FINAL_REVIEWED_PACKAGE_NO_LAUNCH_AUTHORITY")
        inventory = _parse_json(args.inventory.read_bytes())
        findings = check_inventory(inventory, target_date=args.target_date,
                                   now_utc=args.observed_utc, stage=stage,
                                   object_root=args.object_root)
        result = {"schema": SCHEMA, "launchable": False, "stage": stage,
                  "status": success_status if not findings else "BLOCKED_EVIDENCE",
                  "missing_evidence": findings}
    else:
        result = make_report(target_date=args.target_date,
                             run_utc=checklist["window_start_utc"] - 14 * 3600,
                             free_disk=args.free_disk_bytes,
                             available_memory=args.available_memory_bytes,
                             observed_utc=args.observed_utc)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] in ("ASSEMBLED_FOR_INDEPENDENT_REVIEW",
                                     "FINAL_REVIEWED_PACKAGE_NO_LAUNCH_AUTHORITY") else 2


if __name__ == "__main__":
    raise SystemExit(main())
