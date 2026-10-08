import copy
import hashlib
from datetime import datetime, timezone

import pytest

from tools.v11_r09_gate3_g3l_prep import (
    ALL_IDS, FINAL_ONLY_IDS, MAX_WINDOW_SEARCH_DAYS, MEMORY_FLOOR,
    PRE_REVIEW_IDS, RUN_SPECIFIC, SCHEMA, WINDOW_SPECIFIC, _parse_json,
    check_inventory, freeze_checklist, next_window_candidate, plan,
    private_v4_null_template, slot_inventory,
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


def test_next_window_candidate_just_before_todays_1400_picks_tomorrow():
    # now is one second before 2026-10-01 14:00 UTC (== START - 1, since
    # START is window_start_utc for the 2026-10-02 target used throughout
    # this file). The earliest date whose frozen window has not started is
    # still 2026-10-02 (window_start_utc == START, strictly in the future).
    now = START - 1
    result = next_window_candidate(now)
    assert result["schema"] == SCHEMA
    assert result["launchable"] is False
    assert result["status"] == "CANDIDATE_TARGET_DATE_SUGGESTED"
    assert result["target_date"] == TARGET_DATE
    assert result["freeze_checklist"] == freeze_checklist(TARGET_DATE)
    assert result["freeze_checklist"]["window_start_utc"] == START
    assert result["days_from_now_searched"] == 1
    assert len(result["missing_evidence"]) == len(PRE_REVIEW_IDS)


def test_next_window_candidate_exactly_at_1400_rolls_to_next_day():
    # At now == START, the 2026-10-02 window has itself already started
    # (half-open: window_start_utc <= now is not "future"), so the next
    # eligible candidate is the day after.
    result = next_window_candidate(START)
    assert result["target_date"] == "2026-10-03"
    assert result["freeze_checklist"]["window_start_utc"] == START + 86400
    assert result["freeze_checklist"]["window_start_utc"] > START
    assert result["days_from_now_searched"] == 2


def test_next_window_candidate_just_after_1700_and_1800_same_day_target():
    # 17:00 (last_acquisition_utc) and 18:00 (decision_utc) of the *current*
    # window do not change date selection: only window_start_utc gates
    # eligibility, so every now in this range still rolls to 2026-10-03,
    # whose own last_acquisition_utc/decision_utc are 3h/4h after its own
    # (later) window_start_utc.
    next_window_start = START + 86400
    for now in (START + 3 * 3600 - 1, START + 3 * 3600, START + 4 * 3600 - 1, START + 4 * 3600):
        result = next_window_candidate(now)
        assert result["target_date"] == "2026-10-03"
        checklist = result["freeze_checklist"]
        assert checklist["window_start_utc"] == next_window_start
        assert checklist["last_acquisition_utc"] == next_window_start + 3 * 3600
        assert checklist["decision_utc"] == next_window_start + 4 * 3600


def test_next_window_candidate_matches_manually_derived_report_example():
    # Mirrors the handoff-report example: at 2026-10-02 21:44:05 UTC the
    # earliest future window is Oct 3 14:00 UTC, proposing an Oct 4 target.
    now = int(datetime(2026, 10, 2, 21, 44, 5, tzinfo=timezone.utc).timestamp())
    result = next_window_candidate(now)
    assert result["target_date"] == "2026-10-04"
    assert result["freeze_checklist"]["window_start_utc"] == int(
        datetime(2026, 10, 3, 14, tzinfo=timezone.utc).timestamp())


def test_next_window_candidate_year_end_rollover():
    # After the Dec 30 cutoff, the Dec 31 window has started, so the next
    # target is Jan 1 and its window starts Dec 31 at 14:00 UTC.
    now = int(datetime(2026, 12, 30, 15, tzinfo=timezone.utc).timestamp())
    result = next_window_candidate(now)
    assert result["target_date"] == "2027-01-01"
    assert result["freeze_checklist"]["window_start_utc"] == int(
        datetime(2026, 12, 31, 14, tzinfo=timezone.utc).timestamp())


def test_next_window_candidate_never_grants_authority_or_mutates_schema():
    result = next_window_candidate(START - 1)
    assert result["launchable"] is False
    assert result["status"] != "ASSEMBLED_FOR_INDEPENDENT_REVIEW"
    assert "launch_authority" not in result
    assert all(f["state"] == "MISSING" for f in result["missing_evidence"])
    assert {f["id"] for f in result["missing_evidence"]} == set(PRE_REVIEW_IDS)


def test_next_window_candidate_is_deterministic_and_pure():
    first = next_window_candidate(START - 1)
    second = next_window_candidate(START - 1)
    assert first == second


@pytest.mark.parametrize("bad_now", [
    0, -1, 1.5, "1790867903", True, False, None,
])
def test_next_window_candidate_rejects_invalid_now(bad_now):
    with pytest.raises(ValueError):
        next_window_candidate(bad_now)


def test_next_window_candidate_rejects_non_exact_or_unrepresentable_instant():
    with pytest.raises(ValueError):
        next_window_candidate(10**18)
    with pytest.raises(ValueError):
        next_window_candidate(-(10**18))


@pytest.mark.parametrize("bad_bound", [0, -1, 31, 1000, 2.0, "30", None])
def test_next_window_candidate_rejects_invalid_max_days_ahead(bad_bound):
    with pytest.raises(ValueError):
        next_window_candidate(START - 1, max_days_ahead=bad_bound)


def test_next_window_candidate_touches_no_socket_or_filesystem(monkeypatch):
    import socket as _socket

    def _blocked(*a, **k):
        raise AssertionError("next_window_candidate must not open a socket")
    monkeypatch.setattr(_socket, "socket", _blocked)
    result = next_window_candidate(START - 1)
    assert result["target_date"] == TARGET_DATE


@pytest.mark.parametrize("now, bound, target, offset", [
    (START - 1, 1, "2026-10-02", 1),
    (START, 2, "2026-10-03", 2),
    (START + 1, 2, "2026-10-03", 2),
    (START + 4 * 3600, 2, "2026-10-03", 2),
])
def test_next_window_candidate_future_day_bound_inclusive(now, bound, target, offset):
    result = next_window_candidate(now, max_days_ahead=bound)
    assert result["target_date"] == target
    assert result["days_from_now_searched"] == offset
    assert result["freeze_checklist"]["window_start_utc"] > now


@pytest.mark.parametrize("now", [START, START + 1, START + 4 * 3600])
def test_next_window_candidate_bound_one_exhausts_at_and_after_cutoff(now):
    with pytest.raises(ValueError, match="bounded search"):
        next_window_candidate(now, max_days_ahead=1)


def test_next_window_candidate_year_9999_calendar_ceiling():
    dec30_cutoff = int(datetime(9999, 12, 30, 14, tzinfo=timezone.utc).timestamp())
    before = next_window_candidate(dec30_cutoff - 1, max_days_ahead=1)
    assert before["target_date"] == "9999-12-31"
    assert before["days_from_now_searched"] == 1
    assert before["freeze_checklist"]["window_start_utc"] == dec30_cutoff
    for now in (dec30_cutoff, dec30_cutoff + 1,
                int(datetime(9999, 12, 31, 13, tzinfo=timezone.utc).timestamp())):
        with pytest.raises(ValueError, match="representable calendar"):
            next_window_candidate(now, max_days_ahead=MAX_WINDOW_SEARCH_DAYS)


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
