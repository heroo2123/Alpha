"""Pure, bounded Gate 3 readiness *proposal* validator.

This module acquires no observations and grants no execution or qualification.
All policy constants here are proposed offline boundaries, not protocol changes.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime

SCHEMA = "ALPHA_V11_GATE3_READINESS_PROPOSAL_V1"
REPORT_SCHEMA = "ALPHA_V11_GATE3_READINESS_REPORT_V1"
P1 = "P1_PREFLIGHT_PROPOSAL"
CAPTURE = "CAPTURE_PLAN_PROPOSAL"
MAX_INPUT = 1_048_576
MAX_OUTPUT = 65_536
MAX_NODES = 4_096
MAX_DEPTH = 8
MAX_ITEMS = 64
MAX_STRING = 4_096
MAX_INT = 2**63 - 1
MAX_REASONS = 64
SECOND = 1_000_000
HORIZON = 86_400 * SECOND  # proposed planning horizon
P1_MAX_WIDTH = 12_600 * SECOND
SNAPSHOT_MAX_AGE = 60 * SECOND  # proposed freshness cap
P1_DISK_RESERVATION = 64 * 1024**2
P1_MEMORY_RESERVATION = 128 * 1024**2
DISK_FLOOR = 2 * 1024**3
DISK_TARGET = 3 * 1024**3
MEMORY_FLOOR = 512 * 1024**2
CAPTURE_DENOMINATOR = 2713  # compared with public slot_inventory in tests
_TIME = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?Z\Z")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class Refusal(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _refuse(code: str):
    raise Refusal(code)


def _exact(value, kind, code="SCHEMA"):
    if type(value) is not kind:
        _refuse(code)
    return value


def _keys(value, expected, code="SCHEMA"):
    _exact(value, dict, code)
    if value.keys() != expected:
        _refuse(code)
    return value


def _integer(value, code="REPRESENTATION_OVERFLOW"):
    if type(value) is not int or not 0 <= value <= MAX_INT:
        _refuse(code)
    return value


def _identifier(value, code="SCHEMA"):
    if type(value) is not str or _ID.fullmatch(value) is None:
        _refuse(code)
    return value


def _checked_add(left, right):
    if left > MAX_INT - right:
        _refuse("REPRESENTATION_OVERFLOW")
    return left + right


def _time(value):
    if type(value) is not str:
        _refuse("TIME_SYNTAX")
    match = _TIME.fullmatch(value)
    if match is None:
        _refuse("TIME_SYNTAX")
    year, month, day, hour, minute, second = map(int, match.groups()[:6])
    fraction = match.group(7)
    if fraction is not None and fraction.endswith("0"):
        _refuse("TIME_SYNTAX")
    try:
        parsed = datetime(year, month, day, hour, minute, second)
    except ValueError:
        _refuse("TIME_SYNTAX")
    micro = int(fraction.ljust(6, "0")) if fraction else 0
    return ((parsed.toordinal() - 1) * 86_400 + hour * 3600 + minute * 60 + second) * SECOND + micro


def _date(value):
    if type(value) is not str or _DATE.fullmatch(value) is None:
        _refuse("TIME_SYNTAX")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        _refuse("TIME_SYNTAX")
    if parsed.isoformat() != value:
        _refuse("TIME_SYNTAX")
    return parsed


def _parse_int(value):
    if len(value) > 19:
        _refuse("REPRESENTATION_OVERFLOW")
    return int(value)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _refuse("DUPLICATE_KEY")
        result[key] = value
    return result


def _bounded_tree(root):
    """Iterate exact built-ins only; never call a supplied object's hooks."""
    stack = [(root, 0)]
    nodes = 0
    size = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            _refuse("INPUT_BOUNDS")
        kind = type(value)
        if kind is dict:
            if len(value) > MAX_ITEMS:
                _refuse("INPUT_BOUNDS")
            for key, item in value.items():
                if type(key) is not str:
                    _refuse("SCHEMA")
                if len(key) > MAX_STRING:
                    _refuse("INPUT_BOUNDS")
                encoded = key.encode("utf-8")
                if len(encoded) > MAX_STRING:
                    _refuse("INPUT_BOUNDS")
                size += len(encoded)
                stack.append((item, depth + 1))
        elif kind is list:
            if len(value) > MAX_ITEMS:
                _refuse("INPUT_BOUNDS")
            stack.extend((item, depth + 1) for item in value)
        elif kind is str:
            if len(value) > MAX_STRING:
                _refuse("INPUT_BOUNDS")
            encoded = value.encode("utf-8")
            if len(encoded) > MAX_STRING:
                _refuse("INPUT_BOUNDS")
            size += len(encoded)
        elif kind is int:
            if value.bit_length() > 64:
                _refuse("REPRESENTATION_OVERFLOW")
            size += 20
        elif kind is bool or value is None:
            size += 5
        else:
            _refuse("SCHEMA")
        if size > MAX_INPUT:
            _refuse("INPUT_BOUNDS")


def _load(raw):
    if type(raw) is bytes:
        if not raw or len(raw) > MAX_INPUT:
            _refuse("INPUT_BOUNDS")
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                               parse_int=_parse_int,
                               parse_constant=lambda _: _refuse("JSON_SYNTAX"))
        except Refusal:
            raise
        except (ValueError, UnicodeError, RecursionError, OverflowError):
            _refuse("JSON_SYNTAX")
    elif type(raw) is dict:
        value = raw
    else:
        _refuse("SCHEMA")
    _bounded_tree(value)
    if len(_canonical(value)) > MAX_INPUT:
        _refuse("INPUT_BOUNDS")
    return value


def _reference(value, code="CLOCK_LINKAGE"):
    _keys(value, {"sha256", "byte_length", "bytes_hex"}, code)
    digest = value["sha256"]
    if type(digest) is not str or _SHA.fullmatch(digest) is None:
        _refuse(code)
    length = _integer(value["byte_length"], code)
    data = value["bytes_hex"]
    if length > MAX_INT // 2:
        _refuse("REPRESENTATION_OVERFLOW")
    if type(data) is not str or len(data) != length * 2 or len(data) % 2:
        _refuse(code)
    try:
        decoded = bytes.fromhex(data)
    except ValueError:
        _refuse(code)
    if decoded.hex() != data or hashlib.sha256(decoded).hexdigest() != digest:
        _refuse(code)
    return decoded


def _build_declaration(value, code="BUILD_BINDING"):
    """Closed, bounded digest/length-only metadata, never loaded-byte proof.

    Unlike `_reference`, this carries no `bytes_hex`, so a caller can declare
    the real SHA-256 and byte length of a source far larger than the shared
    string/aggregate ceilings. It is still only an unqualified declaration:
    the validator never hashes its own loaded bytes to check it.
    """
    _keys(value, {"sha256", "byte_length"}, code)
    digest = value["sha256"]
    if type(digest) is not str or _SHA.fullmatch(digest) is None:
        _refuse(code)
    length = _integer(value["byte_length"], code)
    return {"sha256": digest, "byte_length": length}


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _evaluation(value):
    _keys(value, {"utc", "monotonic_us", "host_id", "boot_id"})
    return (_time(value["utc"]), _integer(value["monotonic_us"]),
            _identifier(value["host_id"]), _identifier(value["boot_id"]))


def _p1(value, evaluated):
    _keys(value, {"run_utc", "start_utc", "end_utc", "campaign_id", "restriction_lineage_id"})
    _identifier(value["campaign_id"])
    _identifier(value["restriction_lineage_id"])
    run, start, end = (_time(value[key]) for key in ("run_utc", "start_utc", "end_utc"))
    if not evaluated < start < end:
        _refuse("TIME_ORDER")
    width = end - start
    if not 60 * SECOND < width <= P1_MAX_WIDTH:
        _refuse("WINDOW_WIDTH")
    if end - evaluated > HORIZON:
        _refuse("HORIZON")
    if run % (86_400 * SECOND) or run // (86_400 * SECOND) != start // (86_400 * SECOND):
        _refuse("RUN_SCOPE")
    if run > evaluated:
        _refuse("FUTURE_RUN")
    return {"run_utc": value["run_utc"], "start_utc": value["start_utc"],
            "end_utc": value["end_utc"], "campaign_id": value["campaign_id"],
            "restriction_lineage_id": value["restriction_lineage_id"]}


def _capture(value, evaluated):
    _keys(value, {"target_local_date", "campaign_id", "restriction_lineage_id"})
    _identifier(value["campaign_id"])
    _identifier(value["restriction_lineage_id"])
    target = _date(value["target_local_date"])
    if target.toordinal() == 1:
        _refuse("TIME_SYNTAX")
    run = (target.toordinal() - 2) * 86_400 * SECOND
    start = run + 14 * 3600 * SECOND
    end = run + 17 * 3600 * SECOND
    if not evaluated < start:
        _refuse("TIME_ORDER")
    if end - evaluated > HORIZON:
        _refuse("HORIZON")
    if run > evaluated:
        _refuse("FUTURE_RUN")
    prior = date.fromordinal(target.toordinal() - 1).isoformat()
    return {"target_local_date": value["target_local_date"],
            "run_utc": prior + "T00:00:00Z", "capture_start_utc": prior + "T14:00:00Z",
            "capture_end_utc": prior + "T17:00:00Z", "decision_utc": prior + "T18:00:00Z",
            "denominator": CAPTURE_DENOMINATOR,
            "campaign_id": value["campaign_id"],
            "restriction_lineage_id": value["restriction_lineage_id"]}


def _resources(value, evaluation):
    _keys(value, {"selected_fs_id", "snapshot", "reservation_view"}, "RESOURCE_INCONSISTENCY")
    fs = _identifier(value["selected_fs_id"], "RESOURCE_INCONSISTENCY")
    snapshot = _keys(value["snapshot"], {"disk_total_bytes", "disk_available_bytes",
        "memory_total_bytes", "mem_available_bytes", "fs_id", "host_id", "boot_id",
        "observed_utc", "monotonic_us", "observation_kind"}, "RESOURCE_INCONSISTENCY")
    disk_total, disk_free, memory_total, memory_free = (_integer(snapshot[k]) for k in
        ("disk_total_bytes", "disk_available_bytes", "memory_total_bytes", "mem_available_bytes"))
    if disk_free > disk_total or memory_free > memory_total or snapshot["fs_id"] != fs:
        _refuse("RESOURCE_INCONSISTENCY")
    if snapshot["observation_kind"] not in ("SCENARIO", "LOCAL_DECLARED") or type(snapshot["observation_kind"]) is not str:
        _refuse("RESOURCE_INCONSISTENCY")
    if _time(snapshot["observed_utc"]) > evaluation[0]:
        _refuse("RESOURCE_CONTEXT")
    if (_identifier(snapshot["host_id"]) != evaluation[2] or
            _identifier(snapshot["boot_id"]) != evaluation[3]):
        _refuse("RESOURCE_CONTEXT")
    age = evaluation[1] - _integer(snapshot["monotonic_us"])
    if not 0 <= age <= SNAPSHOT_MAX_AGE:
        _refuse("RESOURCE_AGE")
    view = _keys(value["reservation_view"], {"complete", "scope_ref", "entries"},
                 "RESERVATION_VIEW_UNQUALIFIED")
    if view["complete"] is not True:
        _refuse("RESERVATION_VIEW_UNQUALIFIED")
    if not _reference(view["scope_ref"], "RESERVATION_VIEW_UNQUALIFIED"):
        _refuse("RESERVATION_VIEW_UNQUALIFIED")
    entries = _exact(view["entries"], list, "RESERVATION_VIEW_UNQUALIFIED")
    ids = set()
    outstanding_disk = outstanding_memory = 0
    for entry in entries:
        _keys(entry, {"id", "consumer_id", "state", "disk_bytes", "memory_bytes",
                      "covered_worst_case"}, "RESERVATION_VIEW_UNQUALIFIED")
        identity = _identifier(entry["id"], "RESERVATION_VIEW_UNQUALIFIED")
        _identifier(entry["consumer_id"], "RESERVATION_VIEW_UNQUALIFIED")
        if identity in ids:
            _refuse("RESERVATION_VIEW_UNQUALIFIED")
        ids.add(identity)
        disk = _integer(entry["disk_bytes"])
        memory = _integer(entry["memory_bytes"])
        state = entry["state"]
        if state == "OUTSTANDING_NOT_IN_SNAPSHOT" and entry["covered_worst_case"] is False:
            outstanding_disk = _checked_add(outstanding_disk, disk)
            outstanding_memory = _checked_add(outstanding_memory, memory)
        elif state == "MATERIALIZED_BEFORE_SNAPSHOT" and entry["covered_worst_case"] is True:
            pass
        else:
            _refuse("RESERVATION_VIEW_UNQUALIFIED")
    debit_disk = _checked_add(outstanding_disk, P1_DISK_RESERVATION)
    debit_memory = _checked_add(outstanding_memory, P1_MEMORY_RESERVATION)
    if disk_free < debit_disk or memory_free < debit_memory:
        _refuse("RESOURCE_INCONSISTENCY")
    post_disk = disk_free - debit_disk
    post_memory = memory_free - debit_memory
    if post_disk < DISK_FLOOR or post_memory < MEMORY_FLOOR:
        _refuse("RESOURCE_FLOOR")
    return {"post_disk_bytes": post_disk, "post_memory_bytes": post_memory,
            "disk_target_shortfall": post_disk < DISK_TARGET,
            "snapshot_age_us": age, "resource_qualification": False}


def _clock(value, evaluation):
    if value is None:
        return "CLOCK_UNQUALIFIED"
    dossier = _keys(value, {"host_id", "boot_id", "recorder_source_ref",
        "recorder_build_ref", "method_version", "raw_calibration_ref", "raw_samples_ref",
        "review", "calibration", "samples"}, "CLOCK_LINKAGE")
    if (_identifier(dossier["host_id"], "CLOCK_LINKAGE") != evaluation[2] or
            _identifier(dossier["boot_id"], "CLOCK_LINKAGE") != evaluation[3]):
        _refuse("CLOCK_CONTEXT")
    _identifier(dossier["method_version"], "CLOCK_LINKAGE")
    for key in ("recorder_source_ref", "recorder_build_ref", "raw_calibration_ref", "raw_samples_ref"):
        _reference(dossier[key])
    calibration = _keys(dossier["calibration"], {"id", "host_id", "boot_id",
        "monotonic_us", "utc_us", "uncertainty_us"}, "CLOCK_LINKAGE")
    for key in ("id", "host_id", "boot_id"):
        _identifier(calibration[key], "CLOCK_LINKAGE")
    if calibration["host_id"] != evaluation[2] or calibration["boot_id"] != evaluation[3]:
        _refuse("CLOCK_CONTEXT")
    for key in ("monotonic_us", "utc_us", "uncertainty_us"):
        _integer(calibration[key], "CLOCK_LINKAGE")
    if calibration["uncertainty_us"] > SECOND:
        _refuse("CLOCK_CALIBRATION")
    if _reference(dossier["raw_calibration_ref"]) != _canonical(calibration):
        _refuse("CLOCK_LINKAGE")
    review = _keys(dossier["review"], {"method_ref", "custody_ref", "report_ref",
        "terminal_ref", "host_id", "boot_id", "validity_domain",
        "recorder_source_sha256", "recorder_build_sha256", "raw_calibration_sha256",
        "raw_samples_sha256"}, "CLOCK_LINKAGE")
    for key in ("method_ref", "custody_ref", "report_ref", "terminal_ref"):
        _reference(review[key])
    for key in ("host_id", "boot_id", "validity_domain"):
        _identifier(review[key], "CLOCK_LINKAGE")
    if review["host_id"] != evaluation[2] or review["boot_id"] != evaluation[3]:
        _refuse("CLOCK_CONTEXT")
    for key, source in (("recorder_source_sha256", "recorder_source_ref"),
                        ("recorder_build_sha256", "recorder_build_ref"),
                        ("raw_calibration_sha256", "raw_calibration_ref"),
                        ("raw_samples_sha256", "raw_samples_ref")):
        if review[key] != dossier[source]["sha256"]:
            _refuse("CLOCK_LINKAGE")
    samples = _exact(dossier["samples"], list, "CLOCK_LINKAGE")
    if not samples:
        _refuse("CLOCK_LINKAGE")
    if _reference(dossier["raw_samples_ref"]) != _canonical(samples):
        _refuse("CLOCK_LINKAGE")
    previous_sequence = -1
    previous_mono = -1
    # Retain the strongest (maximum) prior interval lower bound, seeded from
    # the original anchor, across the whole sequence. Overwriting it with
    # only the immediately preceding sample would let an intervening
    # overlapping sample silently discard an earlier, tighter constraint.
    lower_bound_floor = calibration["utc_us"] - calibration["uncertainty_us"]
    for sample in samples:
        _keys(sample, {"event_kind", "sequence", "monotonic_us", "utc_us",
                       "uncertainty_us", "calibration_id", "calibration_monotonic_us"},
              "CLOCK_LINKAGE")
        _identifier(sample["event_kind"], "CLOCK_LINKAGE")
        _identifier(sample["calibration_id"], "CLOCK_LINKAGE")
        sequence = _integer(sample["sequence"], "CLOCK_LINKAGE")
        if sequence > MAX_NODES:
            _refuse("CLOCK_LINKAGE")
        mono = _integer(sample["monotonic_us"], "CLOCK_LINKAGE")
        utc = _integer(sample["utc_us"], "CLOCK_LINKAGE")
        uncertainty = _integer(sample["uncertainty_us"], "CLOCK_LINKAGE")
        calibration_mono = _integer(sample["calibration_monotonic_us"], "CLOCK_LINKAGE")
        if sequence <= previous_sequence or mono <= previous_mono:
            _refuse("CLOCK_MONOTONIC")
        if mono > evaluation[1]:
            _refuse("CLOCK_CONTEXT")
        if uncertainty > SECOND or not 0 <= mono - calibration_mono <= 60 * SECOND:
            _refuse("CLOCK_CALIBRATION")
        if (sample["calibration_id"] != calibration["id"] or
                calibration_mono != calibration["monotonic_us"]):
            _refuse("CLOCK_REBASE")
        # A wholly backwards UTC interval, including one entirely before the
        # retained original anchor or any earlier retained sample, is a step
        # under every nonnegative monotonic drift envelope. No forward-drift
        # envelope is assumed.
        if utc + uncertainty < lower_bound_floor:
            _refuse("CLOCK_STEP")
        lower_bound_floor = max(lower_bound_floor, utc - uncertainty)
        previous_sequence, previous_mono = sequence, mono
    return "STRUCTURALLY_LINKED_UNQUALIFIED"


def conservative_stage_fit(dispatch_upper_us, drift_margin_us, end_us):
    """Illustrative strict fit only; no caller can assert a qualified margin."""
    if any(type(v) is not int or v < 0 or v > MAX_INT for v in
           (dispatch_upper_us, drift_margin_us, end_us)):
        return False
    return dispatch_upper_us + 60 * SECOND + drift_margin_us < end_us


def _report(status, reasons, blockers=None, proposal=None, resources=None, clock=None,
            build=None):
    if proposal is not None:
        proposal = dict(proposal)
        proposal["proposal_sha256"] = hashlib.sha256(_canonical(proposal)).hexdigest()
    result = {"schema": REPORT_SCHEMA, "status": status,
              "reasons": reasons[:MAX_REASONS], "blockers": blockers or [],
              "execution_authority": False, "capture_eligibility": False,
              "qualification_credit": 0, "g3l": "NO_GO", "clock_qualification": False,
              "declared_build_ref": build, "proposal": proposal,
              "resources": resources, "clock_linkage": clock}
    if len(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()) > MAX_OUTPUT:
        return {"schema": REPORT_SCHEMA, "status": "PROPOSAL_REFUSED",
                "reasons": ["OUTPUT_BOUNDS"], "blockers": [],
                "execution_authority": False, "capture_eligibility": False,
                "qualification_credit": 0, "g3l": "NO_GO", "clock_qualification": False,
                "declared_build_ref": None, "proposal": None, "resources": None,
                "clock_linkage": None}
    return result


def validate(raw):
    """Return a detached, deterministic proposal report for exact JSON/built-ins."""
    try:
        request = _load(raw)
        _keys(request, {"schema", "mode", "declared_build_ref", "evaluation",
                        "p1", "capture", "resources", "clock"})
        if request["schema"] != SCHEMA:
            _refuse("SCHEMA")
        mode = request["mode"]
        if type(mode) is not str or mode not in (P1, CAPTURE):
            _refuse("SCHEMA")
        evaluation = _evaluation(request["evaluation"])
        build = request["declared_build_ref"]
        if build is not None:
            build = _build_declaration(build, "BUILD_BINDING")
        if mode == P1:
            if request["capture"] is not None:
                _refuse("SCHEMA")
            proposal = _p1(request["p1"], evaluation[0])
            resources = _resources(request["resources"], evaluation)
        else:
            if request["p1"] is not None or request["resources"] is not None:
                _refuse("SCHEMA")
            proposal = _capture(request["capture"], evaluation[0])
            resources = None
        clock = _clock(request["clock"], evaluation)
        blockers = ["BUILD_UNATTESTED", "CLOCK_UNQUALIFIED", "INDEPENDENT_REVIEW_REQUIRED",
                    "PROVIDER_AND_EXECUTION_PREREQUISITES_UNRESOLVED"]
        if request["clock"] is not None:
            blockers.append("CLOCK_DRIFT_MODEL_UNSUPPORTED")
        if mode == P1:
            blockers.append("RESOURCE_UNQUALIFIED")
        return _report("PROPOSAL_VALID_NOT_EXECUTABLE", [], blockers,
                       proposal, resources, clock, build)
    except Refusal as error:
        return _report("PROPOSAL_REFUSED", [error.code])
    except (ValueError, OverflowError, RecursionError, UnicodeError):
        return _report("PROPOSAL_REFUSED", ["SCHEMA"])
