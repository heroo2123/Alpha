from datetime import datetime, timezone

import pytest

from tools.v11_r09_gate3_g3l_prep import (
    ALL_IDS, MEMORY_FLOOR, RUN_SPECIFIC, SCHEMA, _parse_json,
    check_inventory, freeze_checklist, plan, private_v4_null_template,
    slot_inventory,
)
from tools.v11_multimodel_panel import canonical
from tools.v11_r09_gate3_launch import LaunchContractError
from tools.v11_r09_gate3_launch_v4 import GROUPS, validate_manifest_v4


RUN = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())
DISK = 3_757_068_288
MEMORY = 670_765_056


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
    assert len(findings) == len(ALL_IDS)
    assert {f["state"] for f in findings} == {"MISSING"}
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
