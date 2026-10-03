"""Synthetic public-only tests; these fixtures carry no admission authority."""

import copy
import hashlib
import json
import os
import subprocess
import sys
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.v11_gate3_readiness_boundaries import (
    CAPTURE, MAX_STRING, P1, SCHEMA, MAX_INT, conservative_stage_fit, validate,
)
from tools.v11_r09_gate3_g3l_prep import freeze_checklist, slot_inventory
from tools.v11_gate3_evidence_preflight_checker import (
    ClockObservation, ResourceObservation, StateLedger,
    check_evidence_preflight_package, OUTCOME_REFUSED,
)


def ref(data=b"synthetic"):
    return {"sha256": hashlib.sha256(data).hexdigest(),
            "byte_length": len(data), "bytes_hex": data.hex()}


def declaration(data=b"synthetic"):
    """Metadata-only build declaration: digest and length, no raw bytes."""
    return {"sha256": hashlib.sha256(data).hexdigest(), "byte_length": len(data)}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()


def dossier(calibration, samples):
    source = ref(b"source"); build = ref(b"build")
    calibration_ref = ref(canonical(calibration)); samples_ref = ref(canonical(samples))
    return {"host_id": "host-A", "boot_id": "boot-A",
            "recorder_source_ref": source, "recorder_build_ref": build,
            "method_version": "method-v1", "raw_calibration_ref": calibration_ref,
            "raw_samples_ref": samples_ref,
            "review": {"method_ref": ref(b"method"), "custody_ref": ref(b"custody"),
                       "report_ref": ref(b"PASS"), "terminal_ref": ref(b"PASS"),
                       "host_id": "host-A", "boot_id": "boot-A",
                       "validity_domain": "synthetic",
                       "recorder_source_sha256": source["sha256"],
                       "recorder_build_sha256": build["sha256"],
                       "raw_calibration_sha256": calibration_ref["sha256"],
                       "raw_samples_sha256": samples_ref["sha256"]},
            "calibration": calibration, "samples": samples}


def p1():
    return {
        "schema": SCHEMA, "mode": P1, "declared_build_ref": None,
        "evaluation": {"utc": "2026-10-02T09:59:00Z", "monotonic_us": 100_000_000,
                       "host_id": "host-A", "boot_id": "boot-A"},
        "p1": {"run_utc": "2026-10-02T00:00:00Z",
               "start_utc": "2026-10-02T10:00:00Z",
               "end_utc": "2026-10-02T10:01:00.000001Z",
               "campaign_id": "campaign-original", "restriction_lineage_id": "lineage-original"},
        "capture": None,
        "resources": {"selected_fs_id": "dev-1",
                      "snapshot": {"disk_total_bytes": 10_000_000_000,
                                   "disk_available_bytes": 2_214_592_512,
                                   "memory_total_bytes": 4_000_000_000,
                                   "mem_available_bytes": 671_088_640,
                                   "fs_id": "dev-1", "host_id": "host-A",
                                   "boot_id": "boot-A", "observed_utc": "2026-10-02T09:58:59Z",
                                   "monotonic_us": 40_000_000,
                                   "observation_kind": "SCENARIO"},
                      "reservation_view": {"complete": True, "scope_ref": ref(), "entries": []}},
        "clock": None,
    }


def capture():
    item = p1()
    item.update(mode=CAPTURE, p1=None, resources=None,
                capture={"target_local_date": "2026-10-03",
                         "campaign_id": "campaign-original",
                         "restriction_lineage_id": "lineage-original"})
    item["evaluation"]["utc"] = "2026-10-02T00:00:00Z"
    return item


def verdict(item, code=None):
    result = validate(item)
    assert result["execution_authority"] is False
    assert result["capture_eligibility"] is False
    assert result["qualification_credit"] == 0
    assert result["g3l"] == "NO_GO"
    assert result["clock_qualification"] is False
    if code:
        assert result["status"] == "PROPOSAL_REFUSED"
        assert result["reasons"] == [code]
        assert result["proposal"] is None
    else:
        assert result["status"] == "PROPOSAL_VALID_NOT_EXECUTABLE"
    return result


def test_nominal_minimum_width_and_strict_stage_fit():
    verdict(p1())
    for end in ("2026-10-02T10:01:00Z", "2026-10-02T10:00:59.999999Z"):
        item = p1(); item["p1"]["end_utc"] = end
        verdict(item, "WINDOW_WIDTH")
    assert not conservative_stage_fit(100, 0, 100 + 60_000_000)
    assert conservative_stage_fit(100, 0, 100 + 60_000_001)
    assert not conservative_stage_fit(100, None, 100 + 90_000_000)


def test_p1_width_horizon_and_time_edges():
    item = p1(); item["p1"]["end_utc"] = "2026-10-02T13:30:00Z"
    verdict(item)
    item["p1"]["end_utc"] = "2026-10-02T13:30:00.000001Z"
    verdict(item, "WINDOW_WIDTH")
    item = p1(); item["p1"].update(start_utc="2026-10-02T00:01:01Z",
                                      end_utc="2026-10-02T00:02:01.000001Z")
    item["evaluation"]["utc"] = "2026-10-01T00:02:01.000001Z"
    verdict(item, "FUTURE_RUN")
    item["evaluation"]["utc"] = "2026-10-02T00:00:00Z"
    item["resources"]["snapshot"]["observed_utc"] = "2026-10-01T23:59:59Z"
    verdict(item)
    item["evaluation"]["utc"] = item["p1"]["start_utc"]
    verdict(item, "TIME_ORDER")
    item = p1(); item["p1"]["end_utc"] = "2026-10-02T09:59:00Z"
    verdict(item, "TIME_ORDER")
    item = p1(); item["p1"]["run_utc"] = "2026-10-01T00:00:00Z"
    verdict(item, "RUN_SCOPE")
    for invalid in ("2026-10-02T10:00:60Z", "2026-10-02T10:00:00",
                    "2026-10-02T10:00:00+00:00", "2026-10-02T10:00:00.0000001Z",
                    "9999-12-31T24:00:00Z", "2026-02-30T10:00:00Z"):
        item = p1(); item["p1"]["start_utc"] = invalid
        verdict(item, "TIME_SYNTAX")


def test_horizon_equality_and_neighbor_with_same_day_run():
    item = p1()
    item["evaluation"]["utc"] = "2026-10-02T00:00:00Z"
    item["resources"]["snapshot"]["observed_utc"] = "2026-10-01T23:59:59Z"
    item["p1"].update(start_utc="2026-10-02T23:00:00Z",
                      end_utc="2026-10-03T00:00:00Z")
    # A 60-minute span ends exactly 24 hours after evaluation.
    verdict(item)
    item["p1"]["end_utc"] = "2026-10-03T00:00:00.000001Z"
    verdict(item, "HORIZON")


def test_capture_matches_public_schedule_and_fixed_denominator():
    item = capture(); result = verdict(item)
    frozen = freeze_checklist("2026-10-03")
    run = int(datetime(2026, 10, 2, tzinfo=timezone.utc).timestamp())
    proposal = result["proposal"]
    assert proposal["capture_start_utc"] == "2026-10-02T14:00:00Z"
    assert frozen["window_start_utc"] == run + 14 * 3600
    assert frozen["last_acquisition_utc"] == run + 17 * 3600
    assert frozen["decision_utc"] == run + 18 * 3600
    assert proposal["denominator"] == len(slot_inventory(run)) == 2713
    item["evaluation"]["utc"] = proposal["capture_start_utc"]
    verdict(item, "TIME_ORDER")
    item["evaluation"]["utc"] = "2026-10-01T23:59:59.999999Z"
    verdict(item, "FUTURE_RUN")


def test_context_and_resource_floors():
    item = p1(); del item["evaluation"]["boot_id"]
    verdict(item, "SCHEMA")
    item = p1(); item["resources"]["snapshot"]["boot_id"] = "other"
    verdict(item, "RESOURCE_CONTEXT")
    item = p1(); item["resources"]["snapshot"]["monotonic_us"] = 39_999_999
    verdict(item, "RESOURCE_AGE")
    item = p1(); item["resources"]["snapshot"]["monotonic_us"] = 100_000_001
    verdict(item, "RESOURCE_AGE")
    item = p1(); item["resources"]["snapshot"]["observed_utc"] = "2026-10-02T10:00:00Z"
    verdict(item, "RESOURCE_CONTEXT")
    item = p1(); item["resources"]["snapshot"]["disk_available_bytes"] -= 1
    verdict(item, "RESOURCE_FLOOR")
    item = p1(); item["resources"]["snapshot"]["mem_available_bytes"] -= 1
    verdict(item, "RESOURCE_FLOOR")
    item = p1(); item["resources"]["snapshot"]["disk_available_bytes"] += 1
    assert verdict(item)["resources"]["disk_target_shortfall"] is True
    item = p1(); item["resources"]["snapshot"]["disk_available_bytes"] = 3_288_334_336
    assert verdict(item)["resources"]["disk_target_shortfall"] is False
    item = p1(); item["resources"]["snapshot"]["disk_available_bytes"] = MAX_INT
    verdict(item, "RESOURCE_INCONSISTENCY")
    item = p1(); item["resources"]["snapshot"]["disk_total_bytes"] = MAX_INT
    item["resources"]["snapshot"]["disk_available_bytes"] = MAX_INT
    verdict(item)
    item["resources"]["snapshot"]["disk_total_bytes"] = MAX_INT + 1
    verdict(item, "REPRESENTATION_OVERFLOW")
    item = p1(); item["resources"]["snapshot"]["disk_total_bytes"] = True
    verdict(item, "REPRESENTATION_OVERFLOW")
    item = p1(); item["resources"]["snapshot"]["fs_id"] = "other"
    verdict(item, "RESOURCE_INCONSISTENCY")


def test_reservation_accounting_and_incomplete_view():
    item = p1(); entry = {"id": "r1", "consumer_id": "consumer-1",
                          "state": "MATERIALIZED_BEFORE_SNAPSHOT",
                          "disk_bytes": 1000, "memory_bytes": 1000,
                          "covered_worst_case": True}
    item["resources"]["reservation_view"]["entries"] = [entry]
    assert verdict(item)["resources"]["post_disk_bytes"] == 2_147_483_648
    entry["state"] = "OUTSTANDING_NOT_IN_SNAPSHOT"
    entry["covered_worst_case"] = False
    verdict(item, "RESOURCE_FLOOR")
    item = p1(); item["resources"]["reservation_view"]["entries"] = [entry, copy.deepcopy(entry)]
    verdict(item, "RESERVATION_VIEW_UNQUALIFIED")
    item = p1(); item["resources"]["reservation_view"]["entries"] = [dict(entry, id="")]
    verdict(item, "RESERVATION_VIEW_UNQUALIFIED")
    item = p1(); item["resources"]["reservation_view"]["entries"] = [dict(entry, state="CRASH_UNKNOWN")]
    verdict(item, "RESERVATION_VIEW_UNQUALIFIED")
    item = p1(); item["resources"]["reservation_view"]["complete"] = False
    verdict(item, "RESERVATION_VIEW_UNQUALIFIED")
    item = p1(); item["resources"]["reservation_view"]["scope_ref"] = ref(b"")
    verdict(item, "RESERVATION_VIEW_UNQUALIFIED")
    item = p1(); item["resources"]["reservation_view"]["entries"] = [dict(entry, disk_bytes=MAX_INT)]
    verdict(item, "REPRESENTATION_OVERFLOW")


def test_clock_reference_identity_still_never_qualifies():
    item = p1(); source = ref(b"source"); build = ref(b"build")
    anchor = {"id": "cal-1", "host_id": "host-A", "boot_id": "boot-A",
              "monotonic_us": 0, "utc_us": 1_000_000, "uncertainty_us": 1_000_000}
    samples = [{"event_kind": "dispatch", "sequence": 1,
                "monotonic_us": 60_000_000, "utc_us": 1_000_000,
                "uncertainty_us": 1_000_000,
                "calibration_id": "cal-1", "calibration_monotonic_us": 0}]
    calibration = ref(canonical(anchor)); samples_ref = ref(canonical(samples))
    item["clock"] = {"host_id": "host-A", "boot_id": "boot-A",
                     "recorder_source_ref": source, "recorder_build_ref": build,
                     "method_version": "method-v1", "raw_calibration_ref": calibration,
                     "raw_samples_ref": samples_ref,
                     "review": {"method_ref": ref(b"method"), "custody_ref": ref(b"custody"),
                                "report_ref": ref(b"PASS"), "terminal_ref": ref(b"PASS"),
                                "host_id": "host-A", "boot_id": "boot-A",
                                "validity_domain": "synthetic",
                                "recorder_source_sha256": source["sha256"],
                                "recorder_build_sha256": build["sha256"],
                                "raw_calibration_sha256": calibration["sha256"],
                                "raw_samples_sha256": samples_ref["sha256"]},
                     "calibration": anchor, "samples": samples}
    assert verdict(item)["clock_linkage"] == "STRUCTURALLY_LINKED_UNQUALIFIED"
    changed = copy.deepcopy(item); changed["clock"]["samples"][0]["utc_us"] += 1
    verdict(changed, "CLOCK_LINKAGE")
    changed = copy.deepcopy(item); changed["clock"]["boot_id"] = "boot-B"
    verdict(changed, "CLOCK_CONTEXT")
    changed = copy.deepcopy(item); changed["clock"]["samples"][0]["uncertainty_us"] += 1
    changed["clock"]["raw_samples_ref"] = ref(canonical(changed["clock"]["samples"]))
    changed["clock"]["review"]["raw_samples_sha256"] = changed["clock"]["raw_samples_ref"]["sha256"]
    verdict(changed, "CLOCK_CALIBRATION")
    changed = copy.deepcopy(item); changed["clock"]["samples"][0]["monotonic_us"] += 1
    changed["clock"]["raw_samples_ref"] = ref(canonical(changed["clock"]["samples"]))
    changed["clock"]["review"]["raw_samples_sha256"] = changed["clock"]["raw_samples_ref"]["sha256"]
    verdict(changed, "CLOCK_CALIBRATION")
    changed = copy.deepcopy(item); changed["clock"]["samples"][0]["calibration_id"] = "copy"
    changed["clock"]["raw_samples_ref"] = ref(canonical(changed["clock"]["samples"]))
    changed["clock"]["review"]["raw_samples_sha256"] = changed["clock"]["raw_samples_ref"]["sha256"]
    verdict(changed, "CLOCK_REBASE")
    changed = copy.deepcopy(item)
    changed["clock"]["samples"][0]["monotonic_us"] = 1_000_000
    changed["clock"]["samples"].append(dict(changed["clock"]["samples"][0],
        sequence=2, monotonic_us=2_000_000, utc_us=1_000_000_000))
    changed["clock"]["raw_samples_ref"] = ref(canonical(changed["clock"]["samples"]))
    changed["clock"]["review"]["raw_samples_sha256"] = changed["clock"]["raw_samples_ref"]["sha256"]
    assert verdict(changed)["clock_linkage"] == "STRUCTURALLY_LINKED_UNQUALIFIED"
    assert "CLOCK_DRIFT_MODEL_UNSUPPORTED" in verdict(changed)["blockers"]
    changed["clock"]["samples"][1]["monotonic_us"] = 500_000
    changed["clock"]["raw_samples_ref"] = ref(canonical(changed["clock"]["samples"]))
    changed["clock"]["review"]["raw_samples_sha256"] = changed["clock"]["raw_samples_ref"]["sha256"]
    verdict(changed, "CLOCK_MONOTONIC")
    changed["clock"]["samples"][1]["monotonic_us"] = 2_000_000
    changed["clock"]["samples"][1]["utc_us"] = 0
    changed["clock"]["samples"][0]["utc_us"] = 5_000_000
    changed["clock"]["raw_samples_ref"] = ref(canonical(changed["clock"]["samples"]))
    changed["clock"]["review"]["raw_samples_sha256"] = changed["clock"]["raw_samples_ref"]["sha256"]
    verdict(changed, "CLOCK_STEP")
    item["clock"]["raw_samples_ref"]["bytes_hex"] = b"tampered".hex()
    verdict(item, "CLOCK_LINKAGE")
    item = p1(); item["clock"] = {"source": "PASS"}
    verdict(item, "CLOCK_LINKAGE")


def test_json_bounds_duplicate_keys_hostile_types_and_replay(monkeypatch):
    item = p1(); first = verdict(item)
    assert first == validate(json.dumps(item).encode())
    item["p1"]["campaign_id"] = "changed"
    assert first["proposal"]["campaign_id"] == "campaign-original"
    assert validate(b'{}')["reasons"] == ["SCHEMA"]
    assert validate(b'')["reasons"] == ["INPUT_BOUNDS"]
    assert validate(b'{"a":1,"a":2}')["reasons"] == ["DUPLICATE_KEY"]
    assert validate(b'0' * 1_048_577)["reasons"] == ["INPUT_BOUNDS"]
    assert validate({"x": [["x" * 289] * 60 for _ in range(60)]})["reasons"] == ["INPUT_BOUNDS"]
    assert validate(b'{"n":' + b'9' * 10_000 + b'}')["reasons"] == ["REPRESENTATION_OVERFLOW"]
    assert validate({"x": [None] * 65})["reasons"] == ["INPUT_BOUNDS"]
    assert validate({"x": [[[[[[[[[0]]]]]]]]]})["reasons"] == ["INPUT_BOUNDS"]
    class Hostile(dict):
        def __iter__(self): raise AssertionError("hook invoked")
    assert validate(Hostile())["reasons"] == ["SCHEMA"]
    class SubInt(int): pass
    item = p1(); item["resources"]["snapshot"]["disk_total_bytes"] = SubInt(10)
    assert validate(item)["reasons"] == ["SCHEMA"]
    item = p1(); item["declared_build_ref"] = declaration(b"forged source")
    assert verdict(item)["declared_build_ref"]["sha256"] == declaration(b"forged source")["sha256"]
    assert "BUILD_UNATTESTED" in verdict(item)["blockers"]
    item["declared_build_ref"] = None
    assert "BUILD_UNATTESTED" in verdict(item)["blockers"]
    import builtins, io, os, socket, subprocess
    def forbidden(*args, **kwargs): raise AssertionError("I/O attempted")
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(io, "open", forbidden)
    monkeypatch.setattr(os, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert verdict(p1()) == validate(p1())


def test_digest_changes_without_resetting_campaign_lineage():
    item = p1()
    original = verdict(item)["proposal"]
    item["p1"]["end_utc"] = "2026-10-02T10:01:00.000002Z"
    changed = verdict(item)["proposal"]
    assert changed["proposal_sha256"] != original["proposal_sha256"]
    assert changed["campaign_id"] == original["campaign_id"]
    assert changed["restriction_lineage_id"] == original["restriction_lineage_id"]


def test_frozen_checker_still_refuses_shifted_date_without_private_fixture():
    package = {"run_utc": "2026-10-03T00:00:00Z",
               "window": {"automatic_roll_forward": False,
                          "dispatch_not_before_utc": "2026-10-03T10:00:00Z",
                          "expires_utc": "2026-10-03T13:30:00Z"}}
    result = check_evidence_preflight_package(
        package_raw=json.dumps(package).encode(),
        restrictions_raw=b"{}", binding_raw=b"{}", protocol_raw=b"synthetic",
        clock=ClockObservation("2026-10-03T10:05:00Z", 0.3, 10.0, True),
        resources=ResourceObservation(3_300_000_000, 600_000_000, 67_108_864),
        ledger=StateLedger())
    assert result.outcome == OUTCOME_REFUSED
    assert "CHANGED_FIELD:package.run_utc" in result.refusal_reasons
    assert "CHANGED_FIELD:window.dispatch_not_before_utc" in result.refusal_reasons


def test_optimized_runtime_contract_uses_no_assert_side_effects():
    result = validate(p1())
    if result["status"] != "PROPOSAL_VALID_NOT_EXECUTABLE":
        raise AssertionError(result)
    if (result["execution_authority"] is not False or
            result["capture_eligibility"] is not False or
            result["qualification_credit"] != 0 or result["g3l"] != "NO_GO"):
        raise AssertionError(result)
    item = p1(); item["p1"]["end_utc"] = item["p1"]["start_utc"]
    refused = validate(item)
    if refused["status"] != "PROPOSAL_REFUSED" or refused["reasons"] != ["TIME_ORDER"]:
        raise AssertionError(refused)


def test_oversized_builtin_string_value_and_key_refuse_before_large_allocation():
    item = p1(); item["p1"]["campaign_id"] = "y" * (16 * 1024 * 1024)
    tracemalloc.start()
    try:
        result = validate(item)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert result["reasons"] == ["INPUT_BOUNDS"]
    assert peak < 1_048_576
    payload = {"k" * (16 * 1024 * 1024): 1}
    tracemalloc.start()
    try:
        result = validate(payload)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert result["reasons"] == ["INPUT_BOUNDS"]
    assert peak < 1_048_576


def test_multibyte_string_refuses_on_encoded_byte_ceiling_not_character_count():
    item = p1()
    value = "é" * 2200  # 2200 code points, 4400 UTF-8 bytes
    assert len(value) <= MAX_STRING
    item["p1"]["campaign_id"] = value
    assert validate(item)["reasons"] == ["INPUT_BOUNDS"]


def test_allocation_sensitive_regression_no_memoryerror_under_resource_limit():
    script = (
        "import resource\n"
        "from tools.v11_gate3_readiness_boundaries import validate\n"
        "resource.setrlimit(resource.RLIMIT_AS, (96 * 1024**2, 96 * 1024**2))\n"
        "raw = {'x': 'x' * (48 * 1024**2)}\n"
        "try:\n"
        "    print(validate(raw)['reasons'])\n"
        "except MemoryError:\n"
        "    print('MemoryError escapes validate')\n"
    )
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(repo_root))
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script], cwd=str(repo_root),
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "['INPUT_BOUNDS']"


def test_backward_step_refuses_against_original_calibration_anchor_directly():
    anchor = {"id": "cal-1", "host_id": "host-A", "boot_id": "boot-A",
              "monotonic_us": 0, "utc_us": 1_000_000_000, "uncertainty_us": 0}
    samples = [{"event_kind": "dispatch", "sequence": 1, "monotonic_us": 1_000_000,
                "utc_us": 0, "uncertainty_us": 0, "calibration_id": "cal-1",
                "calibration_monotonic_us": 0}]
    item = p1(); item["clock"] = dossier(anchor, samples)
    verdict(item, "CLOCK_STEP")
    # The same refusal must not depend on the caller duplicating the anchor
    # as an explicit sequence-0 sample.
    with_anchor_sample = [dict(samples[0], sequence=0, monotonic_us=0,
                                utc_us=1_000_000_000), samples[0]]
    item = p1(); item["clock"] = dossier(anchor, with_anchor_sample)
    verdict(item, "CLOCK_STEP")


def test_calibration_anchor_interval_overlap_boundary():
    anchor = {"id": "cal-1", "host_id": "host-A", "boot_id": "boot-A",
              "monotonic_us": 0, "utc_us": 10_000_000, "uncertainty_us": 0}
    touching = [{"event_kind": "dispatch", "sequence": 1, "monotonic_us": 1_000_000,
                 "utc_us": 10_000_000, "uncertainty_us": 0, "calibration_id": "cal-1",
                 "calibration_monotonic_us": 0}]
    item = p1(); item["clock"] = dossier(anchor, touching)
    verdict(item)
    gapped = [dict(touching[0], utc_us=touching[0]["utc_us"] - 1)]
    item = p1(); item["clock"] = dossier(anchor, gapped)
    verdict(item, "CLOCK_STEP")


def test_declared_build_ref_represents_actual_current_source_bytes():
    source_path = Path(__file__).resolve().parents[1] / "tools" / "v11_gate3_readiness_boundaries.py"
    source_bytes = source_path.read_bytes()
    assert len(source_bytes) > 2048
    item = p1(); item["declared_build_ref"] = declaration(source_bytes)
    result = verdict(item)
    assert result["declared_build_ref"] == {
        "sha256": hashlib.sha256(source_bytes).hexdigest(),
        "byte_length": len(source_bytes)}
    assert "BUILD_UNATTESTED" in result["blockers"]


def test_declared_build_ref_rejects_legacy_bytes_hex_field():
    item = p1(); item["declared_build_ref"] = ref(b"legacy reference shape")
    verdict(item, "BUILD_BINDING")


def test_declared_build_ref_forged_metadata_stays_unattested_and_bounds_shape():
    item = p1(); item["declared_build_ref"] = {"sha256": "0" * 64, "byte_length": 999_999_999}
    result = verdict(item)
    assert result["declared_build_ref"] == {"sha256": "0" * 64, "byte_length": 999_999_999}
    assert "BUILD_UNATTESTED" in result["blockers"]
    item = p1(); item["declared_build_ref"] = {"sha256": "F" * 64, "byte_length": 1}
    verdict(item, "BUILD_BINDING")
    item = p1(); item["declared_build_ref"] = {"sha256": "0" * 63, "byte_length": 1}
    verdict(item, "BUILD_BINDING")
    item = p1(); item["declared_build_ref"] = {"sha256": "0" * 64, "byte_length": -1}
    verdict(item, "BUILD_BINDING")
    item = p1(); item["declared_build_ref"] = {"sha256": "0" * 64, "byte_length": MAX_INT + 1}
    verdict(item, "BUILD_BINDING")
    item = p1(); item["declared_build_ref"] = None
    assert "BUILD_UNATTESTED" in verdict(item)["blockers"]
