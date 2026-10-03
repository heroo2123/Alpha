"""Synthetic public-only tests for the pure Gate 3 clock dossier verifier.

No pytest dependency: a __main__ runner, since pytest is not installed in
this environment. Run with `python3 tests/test_v11_gate3_clock_dossier.py`
and again with `python3 -O`. These tests use `check()` (a plain
if/raise, immune to `-O` statement stripping) rather than bare `assert`, so
an optimized run exercises exactly the same invariants as a normal one.
"""

import copy
import hashlib
import json
import os
import sys
import tempfile
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.v11_gate3_clock_dossier import (  # noqa: E402
    MAX_RADIUS_US, MAX_RAW_RECORD, MAX_RECORD_OUTPUT, FIXED_AUTHORITY_FLAGS, Refusal,
    build_session, drift_envelope_ns, parse_probe_record, project_event_interval_ns,
    record_digests, to_microseconds_outward, write_session_file,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def check(condition, message=""):
    """Assertion that survives `python3 -O` (bare `assert` is stripped)."""
    if not condition:
        raise AssertionError(message)


def _bracket(before_ns, after_ns):
    return {"before_ns": before_ns, "before_result": 0, "before_errno": 0,
            "after_ns": after_ns, "after_result": 0, "after_errno": 0,
            "res_sec": 0, "res_nsec": 1, "res_result": 0, "res_errno": 0}


def _adjtimex(call_result=0, modes=0):
    return {"call_result": call_result, "errno": 0, "modes": modes, "offset": 12,
            "freq": 34, "maxerror": 56, "esterror": 78, "status": 0, "constant": 1,
            "precision": 1, "tolerance": 32768000, "time_sec": 2000000000,
            "time_usec": 123456, "tick": 10000, "ppsfreq": 0, "jitter": 0, "shift": 0,
            "stabil": 0, "jitcnt": 0, "calcnt": 0, "errcnt": 0, "stbcnt": 0, "tai": 0}


def _record(**overrides):
    value = {
        "schema": "ALPHA_V11_GATE3_CLOCK_PROBE_RECORD_V1", "status": "OK",
        "boot_id_before": "11111111-1111-4111-8111-111111111111", "boot_id_after": "11111111-1111-4111-8111-111111111111",
        "ns_time": "time:[4026531834]", "ns_pid": "pid:[4026531836]",
        # monotonic is the outermost bracket; monotonic_raw and boottime must
        # nest fully inside [m_before, m_after], as the real recorder's
        # read-close-in-reverse-order bracketing guarantees.
        "monotonic": _bracket(1_000_000_000_000, 1_000_000_010_000),
        "monotonic_raw": _bracket(1_000_000_001_000, 1_000_000_009_000),
        "boottime": _bracket(1_000_000_002_000, 1_000_000_008_000),
        "realtime": {"value_ns": 2_000_000_000_123_456_000, "result": 0, "errno": 0,
                     "res_sec": 0, "res_nsec": 1, "res_result": 0, "res_errno": 0},
        "adjtimex": _adjtimex(),
    }
    value.update(overrides)
    return value


def _raw(value):
    return json.dumps(value).encode()


# --------------------------------------------------------------------------
# parse_probe_record
# --------------------------------------------------------------------------

def test_parse_ok_record_roundtrips():
    parsed = parse_probe_record(_raw(_record()))
    check(parsed["status"] == "OK")
    check(parsed["boot_id"] == "11111111-1111-4111-8111-111111111111")
    check(parsed["m_before"] == 1_000_000_000_000)
    check(parsed["m_after"] == 1_000_000_010_000)


def test_parse_refused_record_passes_through_known_code():
    refused = {"schema": "ALPHA_V11_GATE3_CLOCK_PROBE_RECORD_V1", "status": "REFUSED",
               "code": "CLOCK_SOURCE_UNAVAILABLE", "detail_errno": 38, "partial": {}}
    parsed = parse_probe_record(_raw(refused))
    check(parsed == {"status": "REFUSED", "code": "CLOCK_SOURCE_UNAVAILABLE"})


def test_parse_refused_record_rejects_unknown_code():
    refused = {"schema": "ALPHA_V11_GATE3_CLOCK_PROBE_RECORD_V1", "status": "REFUSED",
               "code": "TOTALLY_MADE_UP", "detail_errno": 0, "partial": {}}
    try:
        parse_probe_record(_raw(refused))
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "SCHEMA")


def test_parse_rejects_wrong_schema():
    value = _record(schema="WRONG")
    try:
        parse_probe_record(_raw(value))
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "SCHEMA")


def test_parse_rejects_duplicate_keys():
    raw = b'{"schema":"ALPHA_V11_GATE3_CLOCK_PROBE_RECORD_V1","schema":"X","status":"OK"}'
    try:
        parse_probe_record(raw)
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "DUPLICATE_KEY")


def test_parse_rejects_oversized_input():
    oversized = b"x" * (MAX_RAW_RECORD + 1)
    try:
        parse_probe_record(oversized)
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "INPUT_BOUNDS")


def test_parse_rejects_missing_required_key():
    value = _record()
    del value["ns_pid"]
    try:
        parse_probe_record(_raw(value))
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "SCHEMA")


def test_parse_rejects_extra_key():
    value = _record()
    value["extra_surprise"] = 1
    try:
        parse_probe_record(_raw(value))
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "SCHEMA")


def test_parse_rejects_mismatched_boot_id():
    value = _record(boot_id_after="22222222-2222-4222-8222-222222222222")
    try:
        parse_probe_record(_raw(value))
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "CLOCK_CONTINUITY_LOST")


def test_parse_rejects_empty_boot_id():
    value = _record(boot_id_before="", boot_id_after="")
    try:
        parse_probe_record(_raw(value))
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "CLOCK_SOURCE_UNAVAILABLE")


def test_parse_rejects_nonzero_adjtimex_modes():
    value = _record(adjtimex=_adjtimex(modes=1))
    try:
        parse_probe_record(_raw(value))
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "ABI_UNSUPPORTED")


def test_parse_rejects_time_error_status():
    value = _record(adjtimex=_adjtimex(call_result=5))
    try:
        parse_probe_record(_raw(value))
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "SYNC_OR_TIMESCALE_UNQUALIFIED")


def test_parse_refuses_pending_leap_insert_and_delete_states():
    for code in (1, 2):
        value = _record(adjtimex=_adjtimex(call_result=code))
        try:
            parse_probe_record(_raw(value))
            check(False, "expected Refusal for pending leap state")
        except Refusal as error:
            check(error.code == "SYNC_OR_TIMESCALE_UNQUALIFIED")


def test_parse_refuses_leap_in_progress_and_leap_occurred_states():
    # F4 regression (independent review of 5667acb): TIME_OOP (leap second in
    # progress, call_result=3) and TIME_WAIT (leap second just occurred,
    # call_result=4) were previously accepted as OK.
    for code in (3, 4):
        value = _record(adjtimex=_adjtimex(call_result=code))
        try:
            parse_probe_record(_raw(value))
            check(False, f"expected Refusal for call_result={code}")
        except Refusal as error:
            check(error.code == "SYNC_OR_TIMESCALE_UNQUALIFIED")


def test_parse_refuses_unsynchronized_status_bit_even_with_time_ok():
    # F4 regression: a record can set STA_UNSYNC (or STA_INS/STA_DEL/
    # STA_CLOCKERR) in the status bitmask while call_result still reports
    # TIME_OK; the old parser ignored these bits entirely.
    for bit in (0x0010, 0x0020, 0x0040, 0x1000):
        adjtimex = _adjtimex()
        adjtimex["status"] = bit
        value = _record(adjtimex=adjtimex)
        try:
            parse_probe_record(_raw(value))
            check(False, f"expected Refusal for status bit {bit:#x}")
        except Refusal as error:
            check(error.code == "SYNC_OR_TIMESCALE_UNQUALIFIED")


def test_parse_rejects_adjtimex_realtime_gross_inconsistency():
    # F4 regression: adjtimex.time_sec wildly inconsistent with the bracketed
    # realtime read (here one day off) was previously accepted as OK.
    adjtimex = _adjtimex()
    adjtimex["time_sec"] = 2_000_000_000 - 86_400
    value = _record(adjtimex=adjtimex)
    try:
        parse_probe_record(_raw(value))
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "SYNC_OR_TIMESCALE_UNQUALIFIED")


def test_parse_rejects_negative_realtime():
    # F4/F10 regression: a negative realtime value was previously accepted.
    value = _record()
    value["realtime"]["value_ns"] = -5
    try:
        parse_probe_record(_raw(value))
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "CLOCK_LINKAGE")


def test_parse_rejects_negative_adjtimex_call_result():
    value = _record(adjtimex=_adjtimex(call_result=-1))
    try:
        parse_probe_record(_raw(value))
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "CLOCK_SOURCE_UNAVAILABLE")


def test_parse_rejects_backward_bracket():
    value = _record(monotonic=_bracket(2_000, 1_000))
    try:
        parse_probe_record(_raw(value))
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "CLOCK_CONTINUITY_LOST")


def test_parse_accepts_realistic_cross_clock_divergence():
    # F1 regression (independent review of 5667acb): CLOCK_MONOTONIC_RAW
    # legitimately drifts away from CLOCK_MONOTONIC via NTP frequency slew
    # (here ~3.456ms over one day of uptime, ~40ppm) and CLOCK_BOOTTIME
    # legitimately runs ahead of CLOCK_MONOTONIC by any suspended duration
    # (here one hour). Neither nests inside the other's bracket on a genuine
    # host. The old cross-clock numeric-containment check refused this
    # genuine record; it must now be accepted.
    value = _record(
        monotonic=_bracket(86_400_000_000_000, 86_400_000_002_000),
        monotonic_raw=_bracket(86_399_996_544_500, 86_399_996_545_500),
        boottime=_bracket(90_000_000_000_800, 90_000_000_001_200),
    )
    parsed = parse_probe_record(_raw(value))
    check(parsed["status"] == "OK")


def test_parse_still_rejects_reversed_raw_bracket_internally():
    # Each clock's own before<=after ordering is still enforced even though
    # the removed cross-clock containment check is gone.
    value = _record(monotonic_raw=_bracket(2_000, 1_000))
    try:
        parse_probe_record(_raw(value))
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "CLOCK_CONTINUITY_LOST")


def test_parse_still_rejects_reversed_boottime_bracket_internally():
    value = _record(boottime=_bracket(2_000, 1_000))
    try:
        parse_probe_record(_raw(value))
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "CLOCK_CONTINUITY_LOST")


def test_parse_rejects_nonzero_clock_result():
    value = _record()
    value["realtime"]["result"] = -1
    try:
        parse_probe_record(_raw(value))
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "CLOCK_SOURCE_UNAVAILABLE")


def test_parse_rejects_bad_resolution():
    value = _record()
    value["monotonic"]["res_sec"] = 1
    try:
        parse_probe_record(_raw(value))
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "ABI_UNSUPPORTED")


def test_parse_rejects_zero_resolution():
    # F10 regression: res_nsec == 0 (no real clock reports this) was
    # previously accepted.
    value = _record()
    value["monotonic"]["res_nsec"] = 0
    try:
        parse_probe_record(_raw(value))
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "ABI_UNSUPPORTED")


def test_parse_rejects_resolution_of_exactly_one_second():
    # F10 regression: res_nsec == 1_000_000_000 is an invalid timespec (the
    # nanosecond field must be < 1e9); the old `>` comparison accepted it.
    value = _record()
    value["monotonic"]["res_nsec"] = 1_000_000_000
    try:
        parse_probe_record(_raw(value))
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "ABI_UNSUPPORTED")


def test_parse_rejects_nonzero_errno_alongside_successful_result():
    # F10 regression: a nonzero errno paired with a reported-successful
    # result (0) is an internally inconsistent record and was previously
    # accepted.
    value = _record()
    value["monotonic"]["before_errno"] = 38
    try:
        parse_probe_record(_raw(value))
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "CLOCK_SOURCE_UNAVAILABLE")


def test_parse_accepts_int64_min_literal():
    # F10 regression: the 19-character literal cap rejected the valid
    # 20-character INT64_MIN literal ("-9223372036854775808"). Use a
    # REFUSED record's detail_errno field, which only range-checks the
    # integer and carries no other semantic cross-check.
    raw = (b'{"schema":"ALPHA_V11_GATE3_CLOCK_PROBE_RECORD_V1","status":"REFUSED",'
           b'"code":"CLOCK_SOURCE_UNAVAILABLE","detail_errno":-9223372036854775808,'
           b'"partial":{}}')
    parsed = parse_probe_record(raw)
    check(parsed == {"status": "REFUSED", "code": "CLOCK_SOURCE_UNAVAILABLE"})


def test_parse_rejects_lone_surrogate_in_string_field():
    # F10 regression: a lone surrogate escape ("\ud800") decodes to a valid
    # Python str but cannot be UTF-8 encoded; the old bounded-tree walk let
    # UnicodeEncodeError escape uncaught instead of refusing.
    value = _record(boot_id_before="\ud800", boot_id_after="\ud800")
    try:
        parse_probe_record(_raw(value))
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "INPUT_BOUNDS")


def test_parse_accepts_probes_own_output_bounds_fallback_code():
    # F9 regression: the probe's own last-resort fallback code was not in
    # the parser's known refusal-code set and was misclassified as SCHEMA.
    refused = {"schema": "ALPHA_V11_GATE3_CLOCK_PROBE_RECORD_V1", "status": "REFUSED",
               "code": "OUTPUT_BOUNDS", "detail_errno": 0, "partial": {}}
    parsed = parse_probe_record(_raw(refused))
    check(parsed == {"status": "REFUSED", "code": "OUTPUT_BOUNDS"})


def test_parse_rejects_bool_in_integer_field():
    # type(True) is bool, not int: the field-level type check must reject it
    # even though bool is an int subclass in Python.
    value = _record()
    value["monotonic"]["before_ns"] = True
    try:
        parse_probe_record(_raw(value))
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "CLOCK_LINKAGE")


def test_parse_rejects_float_in_integer_field():
    # A float fails the generic bounded-tree walk (SCHEMA) before any
    # field-specific check runs, since only exact dict/list/str/int/bool/None
    # built-ins are accepted anywhere in the tree.
    value = _record()
    text = json.dumps(value)
    text = text.replace('"before_ns": 1000000000000,', '"before_ns": 1000000000000.0,', 1)
    try:
        parse_probe_record(text.encode())
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "SCHEMA")


def test_parse_rejects_overflowing_integer():
    value = _record()
    value["monotonic"]["before_ns"] = 2**64
    try:
        parse_probe_record(_raw(value))
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "REPRESENTATION_OVERFLOW")


def test_parse_rejects_deeply_nested_hostile_input():
    nested = 1
    for _ in range(20):
        nested = [nested]
    raw = json.dumps({"schema": "ALPHA_V11_GATE3_CLOCK_PROBE_RECORD_V1", "status": "OK",
                       "hostile": nested}).encode()
    try:
        parse_probe_record(raw)
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code in ("INPUT_BOUNDS", "SCHEMA"))


# --------------------------------------------------------------------------
# record_digests: raw bytes vs. canonical parsed projection
# --------------------------------------------------------------------------

def test_record_digest_is_exact_raw_bytes_not_a_reserialization():
    value = _record()
    compact = _raw(value)
    spaced = json.dumps(value, indent=2).encode()
    parsed = parse_probe_record(compact)
    raw_sha_compact, proj_sha_compact = record_digests(compact, parsed)
    raw_sha_spaced, proj_sha_spaced = record_digests(spaced, parsed)
    check(raw_sha_compact == hashlib.sha256(compact).hexdigest())
    check(raw_sha_spaced == hashlib.sha256(spaced).hexdigest())
    check(raw_sha_compact != raw_sha_spaced)
    check(proj_sha_compact == proj_sha_spaced)


def test_record_digest_changes_with_raw_bytes():
    value = _record()
    parsed = parse_probe_record(_raw(value))
    raw1 = _raw(value)
    raw2 = _raw(value) + b" "
    sha1, _ = record_digests(raw1, parsed)
    sha2, _ = record_digests(raw2, parsed)
    check(sha1 != sha2)


# --------------------------------------------------------------------------
# Conservative interval arithmetic
# --------------------------------------------------------------------------

def test_drift_envelope_known_values():
    # rho = 1/1_000_000 (1 ppm), t = 10_000_000_000 ns -> exactly 10_000 ns scaled, + jitter.
    d = drift_envelope_ns(10_000_000_000, 1, 1_000_000, 500)
    check(d == 10_000 + 500)


def test_drift_envelope_ceils_not_truncates():
    # rho*t = 1/3 -> ceil(1/3) = 1, not 0.
    d = drift_envelope_ns(1, 1, 3, 0)
    check(d == 1)


def test_drift_envelope_rejects_unknown_rate():
    for args in [(-1, 1, 1, 0), (1, -1, 1, 0), (1, 1, 0, 0), (1, 1, 1, -1)]:
        try:
            drift_envelope_ns(*args)
            check(False, f"expected Refusal for {args}")
        except Refusal as error:
            check(error.code == "CLOCK_DRIFT_UNSUPPORTED")


def test_project_event_interval_matches_handoff_equation():
    # L0=1_000_000, U0=1_000_100, a=0, b=100, c=200, d=300 (ns), rho=0, j=5.
    lower, upper, age_upper = project_event_interval_ns(
        1_000_000, 1_000_100, 0, 100, 200, 300, 0, 1, 5)
    # age_upper = d - a = 300.
    check(age_upper == 300)
    # D(300) = ceil(0*300) + 5 = 5.
    # lower = L0 + (c-b) - D = 1_000_000 + 100 - 5 = 1_000_095.
    check(lower == 1_000_095)
    # upper = U0 + (d-a) + D = 1_000_100 + 300 + 5 = 1_000_405.
    check(upper == 1_000_405)


def test_project_event_interval_requires_event_after_calibration_bracket():
    try:
        project_event_interval_ns(0, 100, 0, 200, 100, 300, 0, 1, 0)
        check(False, "expected Refusal: c < b")
    except Refusal as error:
        check(error.code == "CLOCK_CONTINUITY_LOST")


def test_project_event_interval_requires_nonreversed_calibration():
    try:
        project_event_interval_ns(100, 0, 0, 10, 10, 20, 0, 1, 0)
        check(False, "expected Refusal: U0 < L0")
    except Refusal as error:
        check(error.code == "CLOCK_CALIBRATION")


def test_project_event_interval_rejects_reversed_bracket():
    try:
        project_event_interval_ns(0, 100, 10, 0, 10, 20, 0, 1, 0)
        check(False, "expected Refusal: b < a")
    except Refusal as error:
        check(error.code == "CLOCK_CONTINUITY_LOST")


def test_to_microseconds_outward_rounds_outward_at_tick_boundaries():
    # Exactly on a microsecond boundary: no change.
    check(to_microseconds_outward(1_000_000, 2_000_000) == (1_000, 2_000))
    # One nanosecond short of upper boundary must still ceil up.
    check(to_microseconds_outward(1_000_000, 1_999_999) == (1_000, 2_000))
    # One nanosecond past lower boundary must still floor down (never narrow).
    check(to_microseconds_outward(1_000_001, 2_000_000) == (1_000, 2_000))
    # Negative nanoseconds (pre-epoch) still floor/ceil outward correctly.
    check(to_microseconds_outward(-1_999_999, -1_000_001) == (-2_000, -1_000))


def test_to_microseconds_outward_at_one_second_and_sixty_second_bounds():
    second_ns = 1_000_000_000
    minute_ns = 60 * second_ns
    check(to_microseconds_outward(second_ns - 1, second_ns + 1) == (999_999, 1_000_001))
    check(to_microseconds_outward(minute_ns - 1, minute_ns + 1) == (59_999_999, 60_000_001))


def test_to_microseconds_outward_rejects_oversized_radius():
    try:
        to_microseconds_outward(0, (MAX_RADIUS_US + 1) * 2 * 1000)
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "CLOCK_EXPIRED")


def test_to_microseconds_outward_accepts_radius_at_cap():
    lower, upper = to_microseconds_outward(0, MAX_RADIUS_US * 2 * 1000)
    check(upper - lower == MAX_RADIUS_US * 2)


# --------------------------------------------------------------------------
# Fixed false authority flags
# --------------------------------------------------------------------------

def _valid_record_and_raw():
    value = _record()
    raw = _raw(value)
    return raw, parse_probe_record(raw)


def test_build_session_fixes_authority_flags_false():
    raw, parsed = _valid_record_and_raw()
    session = build_session(session_nonce="nonce-1", sequence=0, event_kind="SYNTHETIC",
                             raw_record=raw, calibration_ref=None,
                             method_envelope_ref=None, prior_head=None, absence_reasons=[])
    check(session["execution_authority"] is False)
    check(session["provider_authority"] is False)
    check(session["capture_eligibility"] is False)
    check(session["clock_qualification"] is False)
    check(session["qualification_credit"] == 0)
    check(session["g3l"] == "NO_GO")
    check(session["status"] == "RECORDED_UNQUALIFIED")


def test_fixed_authority_flags_cannot_be_mutated():
    # F2 regression (independent review of 5667acb, repro B3): the module's
    # fixed authority flags were a plain mutable dict; mutating it changed
    # every later session's execution_authority to True.
    try:
        FIXED_AUTHORITY_FLAGS["execution_authority"] = True
        check(False, "expected TypeError: FIXED_AUTHORITY_FLAGS must be immutable")
    except TypeError:
        pass
    check(FIXED_AUTHORITY_FLAGS["execution_authority"] is False)


def test_build_session_binds_parsed_projection_to_raw_bytes():
    # F3 regression (repro B1): build_session used to accept a caller-
    # supplied `parsed_record` independent of `raw_record`, so garbage or
    # REFUSED raw bytes could be paired with an unrelated OK projection.
    # The parsed projection is now always derived from raw_record itself.
    raw, parsed = _valid_record_and_raw()
    session = build_session(session_nonce="nonce-bind", sequence=0, event_kind="SYNTHETIC",
                             raw_record=raw, calibration_ref=None,
                             method_envelope_ref=None, prior_head=None, absence_reasons=[])
    _, expected_projection_sha256 = record_digests(raw, parsed)
    check(session["parsed_projection_sha256"] == expected_projection_sha256)
    try:
        build_session(session_nonce="nonce-bind", sequence=0, event_kind="SYNTHETIC",  # noqa: E501
                       raw_record=raw, calibration_ref=None, method_envelope_ref=None,
                       prior_head=None, absence_reasons=[], parsed_record=parsed)
        check(False, "expected TypeError: parsed_record is no longer accepted")
    except TypeError:
        pass


def test_build_session_refuses_garbage_raw_record():
    # F3 regression: garbage raw bytes must refuse session construction
    # entirely rather than silently pairing with a caller's own projection.
    try:
        build_session(session_nonce="nonce-garbage", sequence=0, event_kind="SYNTHETIC",
                      raw_record=b"not json at all", calibration_ref=None,
                      method_envelope_ref=None, prior_head=None, absence_reasons=[])
        check(False, "expected Refusal")
    except Refusal:
        pass


def test_build_session_copies_calibration_ref_not_aliased():
    # F3 regression (repro B4): calibration_ref was stored by reference, so
    # mutating the caller's dict after the call changed the in-memory
    # session's calibration_ref while session_sha256 stayed stale.
    raw, _ = _valid_record_and_raw()
    blob = b"cal"
    ref = {"sha256": hashlib.sha256(blob).hexdigest(), "byte_length": len(blob),
           "bytes_hex": blob.hex()}
    session = build_session(session_nonce="nonce-alias", sequence=0, event_kind="SYNTHETIC",
                             raw_record=raw, calibration_ref=ref,
                             method_envelope_ref=None, prior_head=None, absence_reasons=[])
    ref["bytes_hex"] = "deadbeef"
    ref["byte_length"] = 4
    check(session["calibration_ref"]["bytes_hex"] == blob.hex())
    check(session["calibration_ref"]["byte_length"] == 3)


def test_write_session_file_refuses_tampered_authority_flags():
    # F2 regression (repro B5): write_session_file only re-validated the
    # nonce and sequence, so a hand-tampered session with True authority
    # flags and status=READY was written to disk verbatim.
    raw, _ = _valid_record_and_raw()
    session = build_session(session_nonce="nonce-tamper", sequence=0, event_kind="SYNTHETIC",
                             raw_record=raw, calibration_ref=None,
                             method_envelope_ref=None, prior_head=None, absence_reasons=[])
    session["execution_authority"] = True
    session["g3l"] = "GO"
    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as root:
        try:
            write_session_file(root, session)
            check(False, "expected Refusal")
        except Refusal as error:
            check(error.code == "SCHEMA")
        check(os.listdir(root) == [])


def test_write_session_file_refuses_hand_built_dict_with_true_authority():
    # F2 regression (repro B5): a minimal hand-built dict, never produced by
    # build_session, claiming provider_authority=True.
    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as root:
        try:
            write_session_file(root, {"session_nonce": "h", "sequence": 1,
                                       "provider_authority": True})
            check(False, "expected Refusal")
        except Refusal as error:
            check(error.code == "SCHEMA")


def test_write_session_file_refuses_list_session():
    # F10 regression: passing a list instead of a dict raised an uncaught
    # AttributeError instead of a Refusal.
    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as root:
        try:
            write_session_file(root, [1])
            check(False, "expected Refusal")
        except Refusal as error:
            check(error.code == "SCHEMA")


def test_build_session_links_status_with_method_envelope_ref():
    raw, parsed = _valid_record_and_raw()
    envelope = {"sha256": hashlib.sha256(b"envelope").hexdigest(),
                "byte_length": len(b"envelope"), "bytes_hex": b"envelope".hex()}
    session = build_session(session_nonce="nonce-1", sequence=0, event_kind="SYNTHETIC",
                             raw_record=raw, calibration_ref=None,
                             method_envelope_ref=envelope, prior_head=None, absence_reasons=[])
    check(session["status"] == "STRUCTURALLY_LINKED_UNQUALIFIED")
    check(session["execution_authority"] is False)
    check(session["clock_qualification"] is False)


def test_build_session_rejects_unknown_event_kind():
    raw, parsed = _valid_record_and_raw()
    try:
        build_session(session_nonce="nonce-1", sequence=0, event_kind="DISPATCH",
                       raw_record=raw, calibration_ref=None,
                       method_envelope_ref=None, prior_head=None, absence_reasons=[])
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "SCHEMA")


def test_build_session_rejects_unknown_absence_reason():
    raw, parsed = _valid_record_and_raw()
    try:
        build_session(session_nonce="nonce-1", sequence=0, event_kind="SYNTHETIC",
                       raw_record=raw, calibration_ref=None,
                       method_envelope_ref=None, prior_head=None,
                       absence_reasons=["NOT_A_REAL_CODE"])
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "SCHEMA")


def test_build_session_digest_is_deterministic():
    raw, parsed = _valid_record_and_raw()
    kwargs = dict(session_nonce="nonce-1", sequence=0, event_kind="SYNTHETIC",
                  raw_record=raw, calibration_ref=None,
                  method_envelope_ref=None, prior_head=None, absence_reasons=[])
    session1 = build_session(**kwargs)
    session2 = build_session(**kwargs)
    check(session1 == session2)
    check(session1["session_sha256"] == hashlib.sha256(
        json.dumps({k: v for k, v in session1.items() if k != "session_sha256"},
                   sort_keys=True, separators=(",", ":")).encode()).hexdigest())


# --------------------------------------------------------------------------
# Bounded standalone session writer (temporary root inside the worktree only)
# --------------------------------------------------------------------------

def test_write_session_file_roundtrips_in_temp_root():
    raw, parsed = _valid_record_and_raw()
    session = build_session(session_nonce="nonce-write", sequence=0, event_kind="SYNTHETIC",
                             raw_record=raw, calibration_ref=None,
                             method_envelope_ref=None, prior_head=None, absence_reasons=[])
    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as root:
        path = write_session_file(root, session)
        check(os.path.dirname(path) == root)
        with open(path, "rb") as handle:
            on_disk = json.loads(handle.read())
        check(on_disk["session_sha256"] == session["session_sha256"])


def test_write_session_file_refuses_duplicate_write():
    raw, parsed = _valid_record_and_raw()
    session = build_session(session_nonce="nonce-dup", sequence=0, event_kind="SYNTHETIC",
                             raw_record=raw, calibration_ref=None,
                             method_envelope_ref=None, prior_head=None, absence_reasons=[])
    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as root:
        write_session_file(root, session)
        try:
            write_session_file(root, session)
            check(False, "expected Refusal: no-clobber violated")
        except Refusal as error:
            check(error.code == "STORE_WRITE_FAILED")


def test_write_session_file_refuses_symlinked_target():
    raw, parsed = _valid_record_and_raw()
    session = build_session(session_nonce="nonce-sym", sequence=0, event_kind="SYNTHETIC",
                             raw_record=raw, calibration_ref=None,
                             method_envelope_ref=None, prior_head=None, absence_reasons=[])
    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as root:
        target = os.path.join(root, "nonce-sym.0.json")
        escape_target = os.path.join(root, "escape.json")
        os.symlink(escape_target, target)
        try:
            write_session_file(root, session)
            check(False, "expected Refusal: O_NOFOLLOW must reject the symlink")
        except Refusal as error:
            check(error.code == "STORE_WRITE_FAILED")
        check(not os.path.exists(escape_target))


def test_write_session_file_refuses_missing_root():
    raw, parsed = _valid_record_and_raw()
    session = build_session(session_nonce="nonce-missing", sequence=0, event_kind="SYNTHETIC",
                             raw_record=raw, calibration_ref=None,
                             method_envelope_ref=None, prior_head=None, absence_reasons=[])
    try:
        write_session_file(str(REPO_ROOT / "does-not-exist-root"), session)
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "STORE_WRITE_FAILED")


def test_write_session_file_revalidates_hand_built_dict():
    malicious = {"session_nonce": "../../etc", "sequence": 0}
    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as root:
        try:
            write_session_file(root, malicious)
            check(False, "expected Refusal")
        except Refusal as error:
            check(error.code == "SCHEMA")


def _must_refuse(call, code):
    try:
        call()
    except Refusal as error:
        check(error.code == code, (error.code, code))
    else:
        check(False, f"expected {code} refusal")


def test_boot_id_requires_canonical_uuid():
    for bad in ("x", "11111111-1111-4111-8111-11111111111G",
                "11111111111141118111111111111111",
                "11111111-1111-4111-8111-111111111111\n"):
        _must_refuse(lambda bad=bad: parse_probe_record(_raw(_record(
            boot_id_before=bad, boot_id_after=bad))), "CLOCK_SOURCE_UNAVAILABLE")


def test_adjtimex_fractional_modes_and_ordering_hold():
    nano = _adjtimex()
    nano["status"] = 0x2000
    nano["time_usec"] = 123456000
    check(parse_probe_record(_raw(_record(adjtimex=nano)))["status"] == "OK")
    for fraction, status, code in ((1_000_000, 0, "ABI_UNSUPPORTED"),
                                   (1_000_000_000, 0x2000, "ABI_UNSUPPORTED"),
                                   (-1, 0, "ABI_UNSUPPORTED"),
                                   (2**63 - 1, 0, "ABI_UNSUPPORTED")):
        adj = _adjtimex()
        adj["time_usec"] = fraction
        adj["status"] = status
        _must_refuse(lambda adj=adj: parse_probe_record(_raw(_record(adjtimex=adj))), code)
    for offset in (-59_876_544_000, 1, 456_000, 60_123_456_000):
        record = _record()
        record["realtime"]["value_ns"] += offset
        _must_refuse(lambda record=record: parse_probe_record(_raw(record)),
                     "SYNC_OR_TIMESCALE_UNQUALIFIED")


def test_adjtimex_success_with_errno_refuses():
    adj = _adjtimex()
    adj["errno"] = 38
    _must_refuse(lambda: parse_probe_record(_raw(_record(adjtimex=adj))),
                 "CLOCK_SOURCE_UNAVAILABLE")


def test_refusal_partial_schema_is_closed_and_typed():
    base = {"schema": "ALPHA_V11_GATE3_CLOCK_PROBE_RECORD_V1", "status": "REFUSED",
            "code": "CLOCK_SOURCE_UNAVAILABLE", "detail_errno": 38, "partial": {}}
    for partial in ({"invented_success": True},
                    {"monotonic": {"after_result": 0}},
                    {"realtime": {"result": 0}},
                    {"boot_id_before": "x"},
                    {"adjtimex": {"call_result": 0}},
                    {"ns_time": []}):
        record = dict(base, partial=partial)
        _must_refuse(lambda record=record: parse_probe_record(_raw(record)),
                     "CLOCK_SOURCE_UNAVAILABLE" if "boot_id_before" in partial else "SCHEMA")
    good = dict(base, partial={"monotonic": _bracket(10, 20)})
    check(parse_probe_record(_raw(good))["status"] == "REFUSED")


def test_session_strings_require_exact_builtin_type():
    class Spoof(str):
        def __eq__(self, other):
            return True
        __hash__ = str.__hash__

    raw, _ = _valid_record_and_raw()
    kwargs = dict(session_nonce="type-spoof", sequence=0, event_kind="SYNTHETIC",
                  raw_record=raw, calibration_ref=None, method_envelope_ref=None,
                  prior_head=None, absence_reasons=[])
    _must_refuse(lambda: build_session(**dict(kwargs, event_kind=Spoof("DISPATCH"))), "SCHEMA")
    for field, value in (("schema", Spoof("WRONG")),
                         ("event_kind", Spoof("DISPATCH")),
                         ("status", Spoof("READY"))):
        session = build_session(**kwargs)
        session[field] = value
        session["session_sha256"] = hashlib.sha256(json.dumps(
            {key: item for key, item in session.items() if key != "session_sha256"},
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as root:
            _must_refuse(lambda: write_session_file(root, session), "SCHEMA")
            check(os.listdir(root) == [])


def test_zero_progress_write_cleans_temp_and_allows_retry():
    raw, _ = _valid_record_and_raw()
    session = build_session(session_nonce="zero-progress", sequence=0,
                            event_kind="SYNTHETIC", raw_record=raw,
                            calibration_ref=None, method_envelope_ref=None,
                            prior_head=None, absence_reasons=[])
    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as root:
        real_write = os.write
        calls = 0

        def partial_then_zero(fd, data):
            nonlocal calls
            calls += 1
            return real_write(fd, data[:7]) if calls == 1 else 0

        with patch("tools.v11_gate3_clock_dossier.os.write", side_effect=partial_then_zero):
            _must_refuse(lambda: write_session_file(root, session), "STORE_WRITE_FAILED")
        check(calls == 2)
        check(os.listdir(root) == [])
        check(os.path.isfile(write_session_file(root, session)))


# --------------------------------------------------------------------------
# Bounds/separation: oversized output refuses before any write
# --------------------------------------------------------------------------

def test_build_session_refuses_oversized_calibration_reference():
    # calibration_ref/method_envelope_ref carry full bytes_hex, not just a
    # digest, so a caller-supplied blob can push the session past
    # MAX_RECORD_OUTPUT even though raw_record itself stays small.
    raw, parsed = _valid_record_and_raw()
    big = b"x" * 40_000
    big_ref = {"sha256": hashlib.sha256(big).hexdigest(), "byte_length": len(big),
               "bytes_hex": big.hex()}
    try:
        build_session(session_nonce="nonce-big", sequence=0, event_kind="SYNTHETIC",
                       raw_record=raw, calibration_ref=big_ref,
                       method_envelope_ref=None, prior_head=None, absence_reasons=[])
        check(False, "expected Refusal")
    except Refusal as error:
        check(error.code == "OUTPUT_BOUNDS")


def _all_tests():
    module = sys.modules[__name__]
    return [getattr(module, name) for name in dir(module) if name.startswith("test_")]


if __name__ == "__main__":
    failures = []
    tests = _all_tests()
    for test in tests:
        try:
            test()
        except Exception as error:  # noqa: BLE001 - report every failure, then exit nonzero
            failures.append((test.__name__, repr(error)))
    print(f"{len(tests) - len(failures)} passed, {len(failures)} failed, {len(tests)} total")
    for name, error in failures:
        print(f"FAIL {name}: {error}")
    sys.exit(1 if failures else 0)
