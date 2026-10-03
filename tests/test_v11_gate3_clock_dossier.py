"""Synthetic public-only tests for the pure Gate 3 clock dossier verifier.

No pytest dependency: plain assertions and a __main__ runner, since pytest is
not installed in this environment. Run with `python3 tests/test_v11_gate3_clock_dossier.py`
and again with `python3 -O` (import-optimized, assertions retained because
these tests use plain `assert`, not `-O`-stripped invariants in the module
under test).
"""

import copy
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.v11_gate3_clock_dossier import (  # noqa: E402
    MAX_RADIUS_US, MAX_RAW_RECORD, MAX_RECORD_OUTPUT, Refusal,
    build_session, drift_envelope_ns, parse_probe_record, project_event_interval_ns,
    record_digests, to_microseconds_outward, write_session_file,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _bracket(before_ns, after_ns):
    return {"before_ns": before_ns, "before_result": 0, "before_errno": 0,
            "after_ns": after_ns, "after_result": 0, "after_errno": 0,
            "res_sec": 0, "res_nsec": 1, "res_result": 0, "res_errno": 0}


def _adjtimex(call_result=0, modes=0):
    return {"call_result": call_result, "errno": 0, "modes": modes, "offset": 12,
            "freq": 34, "maxerror": 56, "esterror": 78, "status": 0, "constant": 1,
            "precision": 1, "tolerance": 32768000, "time_sec": 2000000000,
            "time_usec": 123, "tick": 10000, "ppsfreq": 0, "jitter": 0, "shift": 0,
            "stabil": 0, "jitcnt": 0, "calcnt": 0, "errcnt": 0, "stbcnt": 0, "tai": 0}


def _record(**overrides):
    value = {
        "schema": "ALPHA_V11_GATE3_CLOCK_PROBE_RECORD_V1", "status": "OK",
        "boot_id_before": "fixture-boot-id-0001", "boot_id_after": "fixture-boot-id-0001",
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
    assert parsed["status"] == "OK"
    assert parsed["boot_id"] == "fixture-boot-id-0001"
    assert parsed["m_before"] == 1_000_000_000_000
    assert parsed["m_after"] == 1_000_000_010_000


def test_parse_refused_record_passes_through_known_code():
    refused = {"schema": "ALPHA_V11_GATE3_CLOCK_PROBE_RECORD_V1", "status": "REFUSED",
               "code": "CLOCK_SOURCE_UNAVAILABLE", "detail_errno": 38, "partial": {}}
    parsed = parse_probe_record(_raw(refused))
    assert parsed == {"status": "REFUSED", "code": "CLOCK_SOURCE_UNAVAILABLE"}


def test_parse_refused_record_rejects_unknown_code():
    refused = {"schema": "ALPHA_V11_GATE3_CLOCK_PROBE_RECORD_V1", "status": "REFUSED",
               "code": "TOTALLY_MADE_UP", "detail_errno": 0, "partial": {}}
    try:
        parse_probe_record(_raw(refused))
        assert False, "expected Refusal"
    except Refusal as error:
        assert error.code == "SCHEMA"


def test_parse_rejects_wrong_schema():
    value = _record(schema="WRONG")
    try:
        parse_probe_record(_raw(value))
        assert False, "expected Refusal"
    except Refusal as error:
        assert error.code == "SCHEMA"


def test_parse_rejects_duplicate_keys():
    raw = b'{"schema":"ALPHA_V11_GATE3_CLOCK_PROBE_RECORD_V1","schema":"X","status":"OK"}'
    try:
        parse_probe_record(raw)
        assert False, "expected Refusal"
    except Refusal as error:
        assert error.code == "DUPLICATE_KEY"


def test_parse_rejects_oversized_input():
    oversized = b"x" * (MAX_RAW_RECORD + 1)
    try:
        parse_probe_record(oversized)
        assert False, "expected Refusal"
    except Refusal as error:
        assert error.code == "INPUT_BOUNDS"


def test_parse_rejects_missing_required_key():
    value = _record()
    del value["ns_pid"]
    try:
        parse_probe_record(_raw(value))
        assert False, "expected Refusal"
    except Refusal as error:
        assert error.code == "SCHEMA"


def test_parse_rejects_extra_key():
    value = _record()
    value["extra_surprise"] = 1
    try:
        parse_probe_record(_raw(value))
        assert False, "expected Refusal"
    except Refusal as error:
        assert error.code == "SCHEMA"


def test_parse_rejects_mismatched_boot_id():
    value = _record(boot_id_after="different-boot-id")
    try:
        parse_probe_record(_raw(value))
        assert False, "expected Refusal"
    except Refusal as error:
        assert error.code == "CLOCK_CONTINUITY_LOST"


def test_parse_rejects_empty_boot_id():
    value = _record(boot_id_before="", boot_id_after="")
    try:
        parse_probe_record(_raw(value))
        assert False, "expected Refusal"
    except Refusal as error:
        assert error.code == "CLOCK_SOURCE_UNAVAILABLE"


def test_parse_rejects_nonzero_adjtimex_modes():
    value = _record(adjtimex=_adjtimex(modes=1))
    try:
        parse_probe_record(_raw(value))
        assert False, "expected Refusal"
    except Refusal as error:
        assert error.code == "ABI_UNSUPPORTED"


def test_parse_rejects_time_error_status():
    value = _record(adjtimex=_adjtimex(call_result=5))
    try:
        parse_probe_record(_raw(value))
        assert False, "expected Refusal"
    except Refusal as error:
        assert error.code == "SYNC_OR_TIMESCALE_UNQUALIFIED"


def test_parse_refuses_pending_leap_insert_and_delete_states():
    for code in (1, 2):
        value = _record(adjtimex=_adjtimex(call_result=code))
        try:
            parse_probe_record(_raw(value))
            assert False, "expected Refusal for pending leap state"
        except Refusal as error:
            assert error.code == "SYNC_OR_TIMESCALE_UNQUALIFIED"


def test_parse_rejects_negative_adjtimex_call_result():
    value = _record(adjtimex=_adjtimex(call_result=-1))
    try:
        parse_probe_record(_raw(value))
        assert False, "expected Refusal"
    except Refusal as error:
        assert error.code == "CLOCK_SOURCE_UNAVAILABLE"


def test_parse_rejects_backward_bracket():
    value = _record(monotonic=_bracket(2_000, 1_000))
    try:
        parse_probe_record(_raw(value))
        assert False, "expected Refusal"
    except Refusal as error:
        assert error.code == "CLOCK_CONTINUITY_LOST"


def test_parse_rejects_raw_bracket_outside_monotonic_bracket():
    value = _record(monotonic_raw=_bracket(999_999_999_999, 1_000_000_009_000))
    try:
        parse_probe_record(_raw(value))
        assert False, "expected Refusal"
    except Refusal as error:
        assert error.code == "CLOCK_CONTINUITY_LOST"


def test_parse_rejects_nonzero_clock_result():
    value = _record()
    value["realtime"]["result"] = -1
    try:
        parse_probe_record(_raw(value))
        assert False, "expected Refusal"
    except Refusal as error:
        assert error.code == "CLOCK_SOURCE_UNAVAILABLE"


def test_parse_rejects_bad_resolution():
    value = _record()
    value["monotonic"]["res_sec"] = 1
    try:
        parse_probe_record(_raw(value))
        assert False, "expected Refusal"
    except Refusal as error:
        assert error.code == "ABI_UNSUPPORTED"


def test_parse_rejects_bool_in_integer_field():
    # type(True) is bool, not int: the field-level type check must reject it
    # even though bool is an int subclass in Python.
    value = _record()
    value["monotonic"]["before_ns"] = True
    try:
        parse_probe_record(_raw(value))
        assert False, "expected Refusal"
    except Refusal as error:
        assert error.code == "CLOCK_LINKAGE"


def test_parse_rejects_float_in_integer_field():
    # A float fails the generic bounded-tree walk (SCHEMA) before any
    # field-specific check runs, since only exact dict/list/str/int/bool/None
    # built-ins are accepted anywhere in the tree.
    value = _record()
    text = json.dumps(value)
    text = text.replace('"before_ns": 1000000000000,', '"before_ns": 1000000000000.0,', 1)
    try:
        parse_probe_record(text.encode())
        assert False, "expected Refusal"
    except Refusal as error:
        assert error.code == "SCHEMA"


def test_parse_rejects_overflowing_integer():
    value = _record()
    value["monotonic"]["before_ns"] = 2**64
    try:
        parse_probe_record(_raw(value))
        assert False, "expected Refusal"
    except Refusal as error:
        assert error.code == "REPRESENTATION_OVERFLOW"


def test_parse_rejects_deeply_nested_hostile_input():
    nested = 1
    for _ in range(20):
        nested = [nested]
    raw = json.dumps({"schema": "ALPHA_V11_GATE3_CLOCK_PROBE_RECORD_V1", "status": "OK",
                       "hostile": nested}).encode()
    try:
        parse_probe_record(raw)
        assert False, "expected Refusal"
    except Refusal as error:
        assert error.code in ("INPUT_BOUNDS", "SCHEMA")


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
    assert raw_sha_compact == hashlib.sha256(compact).hexdigest()
    assert raw_sha_spaced == hashlib.sha256(spaced).hexdigest()
    assert raw_sha_compact != raw_sha_spaced
    assert proj_sha_compact == proj_sha_spaced


def test_record_digest_changes_with_raw_bytes():
    value = _record()
    parsed = parse_probe_record(_raw(value))
    raw1 = _raw(value)
    raw2 = _raw(value) + b" "
    sha1, _ = record_digests(raw1, parsed)
    sha2, _ = record_digests(raw2, parsed)
    assert sha1 != sha2


# --------------------------------------------------------------------------
# Conservative interval arithmetic
# --------------------------------------------------------------------------

def test_drift_envelope_known_values():
    # rho = 1/1_000_000 (1 ppm), t = 10_000_000_000 ns -> exactly 10_000 ns scaled, + jitter.
    d = drift_envelope_ns(10_000_000_000, 1, 1_000_000, 500)
    assert d == 10_000 + 500


def test_drift_envelope_ceils_not_truncates():
    # rho*t = 1/3 -> ceil(1/3) = 1, not 0.
    d = drift_envelope_ns(1, 1, 3, 0)
    assert d == 1


def test_drift_envelope_rejects_unknown_rate():
    for args in [(-1, 1, 1, 0), (1, -1, 1, 0), (1, 1, 0, 0), (1, 1, 1, -1)]:
        try:
            drift_envelope_ns(*args)
            assert False, f"expected Refusal for {args}"
        except Refusal as error:
            assert error.code == "CLOCK_DRIFT_UNSUPPORTED"


def test_project_event_interval_matches_handoff_equation():
    # L0=1_000_000, U0=1_000_100, a=0, b=100, c=200, d=300 (ns), rho=0, j=5.
    lower, upper, age_upper = project_event_interval_ns(
        1_000_000, 1_000_100, 0, 100, 200, 300, 0, 1, 5)
    # age_upper = d - a = 300.
    assert age_upper == 300
    # D(300) = ceil(0*300) + 5 = 5.
    # lower = L0 + (c-b) - D = 1_000_000 + 100 - 5 = 1_000_095.
    assert lower == 1_000_095
    # upper = U0 + (d-a) + D = 1_000_100 + 300 + 5 = 1_000_405.
    assert upper == 1_000_405


def test_project_event_interval_requires_event_after_calibration_bracket():
    try:
        project_event_interval_ns(0, 100, 0, 200, 100, 300, 0, 1, 0)
        assert False, "expected Refusal: c < b"
    except Refusal as error:
        assert error.code == "CLOCK_CONTINUITY_LOST"


def test_project_event_interval_requires_nonreversed_calibration():
    try:
        project_event_interval_ns(100, 0, 0, 10, 10, 20, 0, 1, 0)
        assert False, "expected Refusal: U0 < L0"
    except Refusal as error:
        assert error.code == "CLOCK_CALIBRATION"


def test_project_event_interval_rejects_reversed_bracket():
    try:
        project_event_interval_ns(0, 100, 10, 0, 10, 20, 0, 1, 0)
        assert False, "expected Refusal: b < a"
    except Refusal as error:
        assert error.code == "CLOCK_CONTINUITY_LOST"


def test_to_microseconds_outward_rounds_outward_at_tick_boundaries():
    # Exactly on a microsecond boundary: no change.
    assert to_microseconds_outward(1_000_000, 2_000_000) == (1_000, 2_000)
    # One nanosecond short of upper boundary must still ceil up.
    assert to_microseconds_outward(1_000_000, 1_999_999) == (1_000, 2_000)
    # One nanosecond past lower boundary must still floor down (never narrow).
    assert to_microseconds_outward(1_000_001, 2_000_000) == (1_000, 2_000)
    # Negative nanoseconds (pre-epoch) still floor/ceil outward correctly.
    assert to_microseconds_outward(-1_999_999, -1_000_001) == (-2_000, -1_000)


def test_to_microseconds_outward_at_one_second_and_sixty_second_bounds():
    second_ns = 1_000_000_000
    minute_ns = 60 * second_ns
    assert to_microseconds_outward(second_ns - 1, second_ns + 1) == (999_999, 1_000_001)
    assert to_microseconds_outward(minute_ns - 1, minute_ns + 1) == (59_999_999, 60_000_001)


def test_to_microseconds_outward_rejects_oversized_radius():
    try:
        to_microseconds_outward(0, (MAX_RADIUS_US + 1) * 2 * 1000)
        assert False, "expected Refusal"
    except Refusal as error:
        assert error.code == "CLOCK_EXPIRED"


def test_to_microseconds_outward_accepts_radius_at_cap():
    lower, upper = to_microseconds_outward(0, MAX_RADIUS_US * 2 * 1000)
    assert upper - lower == MAX_RADIUS_US * 2


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
                             raw_record=raw, parsed_record=parsed, calibration_ref=None,
                             method_envelope_ref=None, prior_head=None, absence_reasons=[])
    assert session["execution_authority"] is False
    assert session["provider_authority"] is False
    assert session["capture_eligibility"] is False
    assert session["clock_qualification"] is False
    assert session["qualification_credit"] == 0
    assert session["g3l"] == "NO_GO"
    assert session["status"] == "RECORDED_UNQUALIFIED"


def test_build_session_links_status_with_method_envelope_ref():
    raw, parsed = _valid_record_and_raw()
    envelope = {"sha256": hashlib.sha256(b"envelope").hexdigest(),
                "byte_length": len(b"envelope"), "bytes_hex": b"envelope".hex()}
    session = build_session(session_nonce="nonce-1", sequence=0, event_kind="SYNTHETIC",
                             raw_record=raw, parsed_record=parsed, calibration_ref=None,
                             method_envelope_ref=envelope, prior_head=None, absence_reasons=[])
    assert session["status"] == "STRUCTURALLY_LINKED_UNQUALIFIED"
    assert session["execution_authority"] is False
    assert session["clock_qualification"] is False


def test_build_session_rejects_unknown_event_kind():
    raw, parsed = _valid_record_and_raw()
    try:
        build_session(session_nonce="nonce-1", sequence=0, event_kind="DISPATCH",
                       raw_record=raw, parsed_record=parsed, calibration_ref=None,
                       method_envelope_ref=None, prior_head=None, absence_reasons=[])
        assert False, "expected Refusal"
    except Refusal as error:
        assert error.code == "SCHEMA"


def test_build_session_rejects_unknown_absence_reason():
    raw, parsed = _valid_record_and_raw()
    try:
        build_session(session_nonce="nonce-1", sequence=0, event_kind="SYNTHETIC",
                       raw_record=raw, parsed_record=parsed, calibration_ref=None,
                       method_envelope_ref=None, prior_head=None,
                       absence_reasons=["NOT_A_REAL_CODE"])
        assert False, "expected Refusal"
    except Refusal as error:
        assert error.code == "SCHEMA"


def test_build_session_digest_is_deterministic():
    raw, parsed = _valid_record_and_raw()
    kwargs = dict(session_nonce="nonce-1", sequence=0, event_kind="SYNTHETIC",
                  raw_record=raw, parsed_record=parsed, calibration_ref=None,
                  method_envelope_ref=None, prior_head=None, absence_reasons=[])
    session1 = build_session(**kwargs)
    session2 = build_session(**kwargs)
    assert session1 == session2
    assert session1["session_sha256"] == hashlib.sha256(
        json.dumps({k: v for k, v in session1.items() if k != "session_sha256"},
                   sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# --------------------------------------------------------------------------
# Bounded standalone session writer (temporary root inside the worktree only)
# --------------------------------------------------------------------------

def test_write_session_file_roundtrips_in_temp_root():
    raw, parsed = _valid_record_and_raw()
    session = build_session(session_nonce="nonce-write", sequence=0, event_kind="SYNTHETIC",
                             raw_record=raw, parsed_record=parsed, calibration_ref=None,
                             method_envelope_ref=None, prior_head=None, absence_reasons=[])
    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as root:
        path = write_session_file(root, session)
        assert os.path.dirname(path) == root
        with open(path, "rb") as handle:
            on_disk = json.loads(handle.read())
        assert on_disk["session_sha256"] == session["session_sha256"]


def test_write_session_file_refuses_duplicate_write():
    raw, parsed = _valid_record_and_raw()
    session = build_session(session_nonce="nonce-dup", sequence=0, event_kind="SYNTHETIC",
                             raw_record=raw, parsed_record=parsed, calibration_ref=None,
                             method_envelope_ref=None, prior_head=None, absence_reasons=[])
    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as root:
        write_session_file(root, session)
        try:
            write_session_file(root, session)
            assert False, "expected Refusal: no-clobber violated"
        except Refusal as error:
            assert error.code == "STORE_WRITE_FAILED"


def test_write_session_file_refuses_symlinked_target():
    raw, parsed = _valid_record_and_raw()
    session = build_session(session_nonce="nonce-sym", sequence=0, event_kind="SYNTHETIC",
                             raw_record=raw, parsed_record=parsed, calibration_ref=None,
                             method_envelope_ref=None, prior_head=None, absence_reasons=[])
    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as root:
        target = os.path.join(root, "nonce-sym.0.json")
        escape_target = os.path.join(root, "escape.json")
        os.symlink(escape_target, target)
        try:
            write_session_file(root, session)
            assert False, "expected Refusal: O_NOFOLLOW must reject the symlink"
        except Refusal as error:
            assert error.code == "STORE_WRITE_FAILED"
        assert not os.path.exists(escape_target)


def test_write_session_file_refuses_missing_root():
    raw, parsed = _valid_record_and_raw()
    session = build_session(session_nonce="nonce-missing", sequence=0, event_kind="SYNTHETIC",
                             raw_record=raw, parsed_record=parsed, calibration_ref=None,
                             method_envelope_ref=None, prior_head=None, absence_reasons=[])
    try:
        write_session_file(str(REPO_ROOT / "does-not-exist-root"), session)
        assert False, "expected Refusal"
    except Refusal as error:
        assert error.code == "STORE_WRITE_FAILED"


def test_write_session_file_revalidates_hand_built_dict():
    malicious = {"session_nonce": "../../etc", "sequence": 0}
    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT)) as root:
        try:
            write_session_file(root, malicious)
            assert False, "expected Refusal"
        except Refusal as error:
            assert error.code == "SCHEMA"


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
                       raw_record=raw, parsed_record=parsed, calibration_ref=big_ref,
                       method_envelope_ref=None, prior_head=None, absence_reasons=[])
        assert False, "expected Refusal"
    except Refusal as error:
        assert error.code == "OUTPUT_BOUNDS"


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
