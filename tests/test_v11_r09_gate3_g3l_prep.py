import copy
import hashlib
from datetime import datetime, timezone

import pytest

from tools.v11_r09_gate3_g3l_prep import (
    ALL_IDS, FINAL_ONLY_IDS, MEMORY_FLOOR, PRE_REVIEW_IDS, RUN_SPECIFIC,
    SCHEMA, WINDOW_SPECIFIC, _parse_json, check_inventory, freeze_checklist,
    plan, private_v4_null_template, slot_inventory,
)
from tools.v11_multimodel_panel import canonical
from tools.v11_r09_gate3_launch import LaunchContractError
from tools.v11_r09_gate3_launch_v4 import GROUPS, validate_manifest_v4


RUN = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())
DISK = 3_757_068_288
MEMORY = 670_765_056
TARGET_DATE = "2026-10-02"
START = RUN + 14 * 3600
NOW = START - 1800
MEASURE = "storage.live_disk_memory_quota_measurement"


def _complete_evidence(tmp_path):
    """A structurally complete 79-identity evidence map with valid, distinct
    sealed objects and correct scopes for every identity (run-specific,
    window-specific, or unscoped)."""
    refs = []
    for name, data in (("evidence.json", b'{"test_evidence":true}'),
                        ("review.json", b'{"test_independent_review":true}')):
        (tmp_path / name).write_bytes(data)
        refs.append({"sha256": hashlib.sha256(data).hexdigest(), "byte_length": len(data),
                     "media_type": "application/json", "path": name})
    evidence = {}
    for identity in ALL_IDS:
        if identity in RUN_SPECIFIC:
            scope = f"run:{RUN}"
        elif identity in WINDOW_SPECIFIC:
            scope = f"window:{START}:{START + 10800}"
        else:
            scope = "accepted:sha256"
        evidence[identity] = {"ref": copy.deepcopy(refs[0]), "review_ref": copy.deepcopy(refs[1]),
                              "observed_utc": NOW, "scope": scope}
    return evidence


def _inventory(evidence):
    return {"schema": SCHEMA, "launchable": False, "target_date": TARGET_DATE, "evidence": evidence}


def test_full_denominator_and_prespecified_bounded_attempt_order():
    result = plan(RUN, DISK, MEMORY)
    assert result["denominator"] == 2713
    assert result["provider_denominator"] == {"GEFS": 775, "IFS": 1275, "AIFS": 663}
    assert len(result["slot_rows"]) == 2713
    attempt = result["attempt_slots"]
    assert len(attempt) > 0
    assert [result["slot_rows"][i]["slot"][0] for i in attempt[:3]] == ["GEFS", "IFS", "AIFS"]
    assert sum(row["planned_state"] == "ATTEMPT_PROPOSED" for row in result["slot_rows"]) == len(attempt)
    assert all(row["reason"] == "NOT_ATTEMPTED_BUDGET" for row in result["slot_rows"]
               if row["planned_state"] != "ATTEMPT_PROPOSED")
    resources = result["resources"]
    assert resources["requests"] == 4 * len(attempt)
    assert resources["body_reservation_bytes"] <= result["bounds"]["max_received_bytes"]
    assert resources["store_objects"] <= result["bounds"]["store_max_objects"]
    assert resources["local_storage_quota_bytes"] + result["bounds"]["additional_headroom_bytes"] <= DISK - result["bounds"]["disk_floor_bytes"]
    assert plan(RUN, DISK, MEMORY) == result


def test_resource_floor_fails_closed_without_losing_denominator():
    result = plan(RUN, 2 * 1024**3, MEMORY_FLOOR)
    assert result["attempt_slots"] == []
    assert len(result["slot_rows"]) == 2713
    assert all(row["reason"] == "NOT_ATTEMPTED_BUDGET" for row in result["slot_rows"])


def test_freeze_only_protocol_time_and_leaves_identity_unfilled():
    frozen = freeze_checklist("2026-10-02")
    assert frozen["window_start_utc"] == RUN + 14 * 3600
    assert frozen["last_acquisition_utc"] == RUN + 17 * 3600
    assert frozen["decision_utc"] == RUN + 18 * 3600
    assert frozen["station_id"] is None and frozen["events"] is None
    assert frozen["selected_run_utc"] is None
    assert len(slot_inventory(RUN)) == 2713


def test_private_v4_template_has_exact_groups_and_cannot_validate(tmp_path):
    template = private_v4_null_template("2026-10-02")
    assert set(template) == set(GROUPS)
    assert template["identity"]["launch_authority"] is False
    assert template["identity"]["pilot_id"] is None
    assert template["sources"]["IFS"]["index_evidence"] is None
    assert template["schedule"]["requests"] is None
    assert template["protocol"]["reviews"][0]["terminal"] is None
    with pytest.raises(LaunchContractError):
        validate_manifest_v4(canonical(template), repo=tmp_path,
                             object_root=tmp_path, now_utc=RUN)


def test_missing_report_rejects_unknown_ids_and_duplicate_keys():
    inventory = {"schema": SCHEMA, "launchable": False, "target_date": "2026-10-02",
                 "evidence": {key: None for key in ALL_IDS}}
    findings = check_inventory(inventory, target_date="2026-10-02", now_utc=RUN)
    assert len(findings) == len(PRE_REVIEW_IDS)
    assert {f["state"] for f in findings} == {"MISSING"}
    assert {f["id"] for f in findings} == set(PRE_REVIEW_IDS)
    inventory["evidence"]["invented.approval"] = None
    with pytest.raises(ValueError, match="ID set"):
        check_inventory(inventory, target_date="2026-10-02", now_utc=RUN)
    with pytest.raises(ValueError, match="duplicate key"):
        _parse_json(b'{"schema":1,"schema":2}')
    expired = check_inventory({key: value for key, value in inventory.items()
                               if key != "evidence"} | {"evidence": {key: None for key in ALL_IDS}},
                              target_date="2026-10-02", now_utc=RUN + 14 * 3600)
    assert any(f["id"] == "time.review_before_window" and f["state"] == "EXPIRED"
               for f in expired)


def test_placeholder_and_stale_current_run_evidence_are_refused(tmp_path):
    inventory = {"schema": SCHEMA, "launchable": False, "target_date": "2026-10-02",
                 "evidence": {key: None for key in ALL_IDS}}
    item_id = sorted(RUN_SPECIFIC)[0]
    template = {"sha256": "0" * 64, "byte_length": 1,
                "media_type": "application/json", "path": "placeholder.json"}
    inventory["evidence"][item_id] = {"ref": template, "review_ref": template,
                                       "observed_utc": RUN, "scope": "current run"}
    findings = check_inventory(inventory, target_date="2026-10-02", now_utc=RUN,
                               object_root=tmp_path)
    assert next(x for x in findings if x["id"] == item_id)["state"] == "INVALID"
    data = b'{"evidence":"real bytes in local test only"}'
    path = tmp_path / "sample.json"
    path.write_bytes(data)
    review_data = b'{"review":"separate local test artifact"}'
    (tmp_path / "review.json").write_bytes(review_data)
    import hashlib
    ref = {"sha256": hashlib.sha256(data).hexdigest(), "byte_length": len(data),
           "media_type": "application/json", "path": "sample.json"}
    review_ref = {"sha256": hashlib.sha256(review_data).hexdigest(),
                  "byte_length": len(review_data), "media_type": "application/json",
                  "path": "review.json"}
    inventory["evidence"][item_id] = {"ref": ref, "review_ref": review_ref,
                                       "observed_utc": RUN - 1, "scope": f"run:{RUN}"}
    findings = check_inventory(inventory, target_date="2026-10-02", now_utc=RUN,
                               object_root=tmp_path)
    assert next(x for x in findings if x["id"] == item_id)["state"] == "STALE"


def test_storage_measurement_has_one_satisfiable_scope_not_two(tmp_path):
    """F1 regression: storage.live_disk_memory_quota_measurement must be
    window-specific only, so a single correct scope string satisfies it."""
    assert MEASURE not in RUN_SPECIFIC
    assert MEASURE in WINDOW_SPECIFIC
    evidence = _complete_evidence(tmp_path)
    for key in FINAL_ONLY_IDS:
        evidence[key] = None
    findings = check_inventory(_inventory(evidence), target_date=TARGET_DATE, now_utc=NOW,
                               stage="PRE_REVIEW", object_root=tmp_path)
    assert findings == []


def test_storage_measurement_rejects_run_scope_requires_window_scope(tmp_path):
    evidence = _complete_evidence(tmp_path)
    for key in FINAL_ONLY_IDS:
        evidence[key] = None
    evidence[MEASURE]["scope"] = f"run:{RUN}"
    findings = check_inventory(_inventory(evidence), target_date=TARGET_DATE, now_utc=NOW,
                               stage="PRE_REVIEW", object_root=tmp_path)
    assert findings == [{"id": MEASURE, "state": "INVALID", "reason": "wrong window scope"}]


def test_storage_measurement_freshness_is_window_bound_not_run_bound(tmp_path):
    evidence = _complete_evidence(tmp_path)
    for key in FINAL_ONLY_IDS:
        evidence[key] = None
    # Satisfies the old (looser) run freshness bound (>= run) but predates
    # the tighter window freshness bound (>= window_start_utc - 3600):
    # the window-only contract must still flag this as STALE.
    evidence[MEASURE]["observed_utc"] = RUN + 100
    findings = check_inventory(_inventory(evidence), target_date=TARGET_DATE, now_utc=NOW,
                               stage="PRE_REVIEW", object_root=tmp_path)
    assert findings == [{"id": MEASURE, "state": "STALE",
                         "reason": "clock evidence predates window policy"}]
    # Exactly at the window freshness bound: must not be STALE.
    evidence[MEASURE]["observed_utc"] = START - 3600
    findings = check_inventory(_inventory(evidence), target_date=TARGET_DATE, now_utc=NOW,
                               stage="PRE_REVIEW", object_root=tmp_path)
    assert findings == []


def test_pre_review_stage_accepts_fully_resolved_complete_inventory(tmp_path):
    """F1 + F2: a structurally complete, fully correct synthetic pre-review
    packet (all 77 pre-review identities filled, the 2 review outputs
    explicitly unresolved) passes with zero findings."""
    evidence = _complete_evidence(tmp_path)
    for key in FINAL_ONLY_IDS:
        evidence[key] = None
    findings = check_inventory(_inventory(evidence), target_date=TARGET_DATE, now_utc=NOW,
                               stage="PRE_REVIEW", object_root=tmp_path)
    assert findings == []


def test_pre_review_stage_rejects_premature_review_outputs(tmp_path):
    """F2: the two detached review outputs must not exist before the review
    they describe has happened; supplying them early at the pre-review stage
    is rejected, not silently accepted."""
    evidence = _complete_evidence(tmp_path)
    findings = check_inventory(_inventory(evidence), target_date=TARGET_DATE, now_utc=NOW,
                               stage="PRE_REVIEW", object_root=tmp_path)
    assert {f["id"] for f in findings} == set(FINAL_ONLY_IDS)
    assert all(f["state"] == "INVALID" for f in findings)


def test_final_stage_requires_review_outputs_and_still_never_launches(tmp_path):
    """F2: the final stage requires the completed detached review report and
    terminal, and even a fully resolved final packet never implies launch
    authority."""
    evidence = _complete_evidence(tmp_path)
    inventory = _inventory(evidence)
    findings = check_inventory(inventory, target_date=TARGET_DATE, now_utc=NOW,
                               stage="FINAL", object_root=tmp_path)
    assert findings == []
    assert inventory["launchable"] is False


def test_final_stage_flags_missing_review_outputs_as_missing_not_skipped(tmp_path):
    evidence = _complete_evidence(tmp_path)
    for key in FINAL_ONLY_IDS:
        evidence[key] = None
    findings = check_inventory(_inventory(evidence), target_date=TARGET_DATE, now_utc=NOW,
                               stage="FINAL", object_root=tmp_path)
    assert {f["id"] for f in findings} == set(FINAL_ONLY_IDS)
    assert all(f["state"] == "MISSING" for f in findings)


def test_invalid_stage_rejected(tmp_path):
    evidence = _complete_evidence(tmp_path)
    with pytest.raises(ValueError, match="stage"):
        check_inventory(_inventory(evidence), target_date=TARGET_DATE, now_utc=NOW,
                        stage="LAUNCH", object_root=tmp_path)


def test_committed_report_matches_fixed_generator_and_denominator_unchanged():
    import json
    from pathlib import Path

    from tools.v11_r09_gate3_g3l_prep import make_report

    report = json.loads(Path("config/v11/r09_gate3_g3l_offline_prep_20261001.json").read_text())
    snap = report["capacity_plan"]["resource_snapshot"]
    regenerated = make_report(target_date="2026-10-02", run_utc=report["capacity_plan"]["run_utc"],
                              free_disk=snap["free_disk_bytes"],
                              available_memory=snap["available_memory_bytes"],
                              observed_utc=report["snapshot_observed_utc"])
    assert report == regenerated
    assert report["capacity_plan"]["denominator"] == 2713
    assert report["capacity_plan"]["provider_denominator"] == {"GEFS": 775, "IFS": 1275, "AIFS": 663}
    assert len(report["missing_evidence"]) == len(PRE_REVIEW_IDS) == 77
    assert {f["id"] for f in report["missing_evidence"]}.isdisjoint(FINAL_ONLY_IDS)
    assert report["stage"] == "PRE_REVIEW"
    assert report["launchable"] is False


def test_cli_pre_review_then_final_stage_never_sets_launchable_true(tmp_path, capsys):
    import json as _json

    from tools.v11_r09_gate3_g3l_prep import main

    evidence = _complete_evidence(tmp_path)
    for key in FINAL_ONLY_IDS:
        evidence[key] = None
    inv_path = tmp_path / "inventory.json"
    inv_path.write_text(_json.dumps(_inventory(evidence)))
    common = ["--target-date", TARGET_DATE, "--free-disk-bytes", "0",
              "--available-memory-bytes", "0", "--observed-utc", str(NOW),
              "--inventory", str(inv_path), "--object-root", str(tmp_path)]

    code = main(common + ["--stage", "pre_review"])
    out = _json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["status"] == "ASSEMBLED_FOR_INDEPENDENT_REVIEW"
    assert out["launchable"] is False

    code = main(common + ["--stage", "final"])
    out = _json.loads(capsys.readouterr().out)
    assert code == 2
    assert out["status"] == "BLOCKED_EVIDENCE"
    assert {f["id"] for f in out["missing_evidence"]} == set(FINAL_ONLY_IDS)
    assert out["launchable"] is False

    for key in FINAL_ONLY_IDS:
        evidence[key] = copy.deepcopy(evidence["review.private_v4_manifest_digest"])
    inv_path.write_text(_json.dumps(_inventory(evidence)))
    code = main(common + ["--stage", "final"])
    out = _json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["status"] == "FINAL_REVIEWED_PACKAGE_NO_LAUNCH_AUTHORITY"
    assert out["launchable"] is False
