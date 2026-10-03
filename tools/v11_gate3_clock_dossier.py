"""Pure, bounded Gate 3 passive clock dossier verifier and session writer.

This module acquires no observations itself. It only parses explicitly
supplied immutable bytes (the native `v11_gate3_clock_probe` one-shot output,
or a synthetic fixture shaped like it), checks closed schemas, hash/length
binding and conservative interval arithmetic, and writes a bounded standalone
session record to a caller-supplied root. It never dereferences a path/URL
found inside supplied bytes, never accepts a trust key/list from a dossier,
and never sets any execution, provider or clock-qualification authority: every
report fixes `execution_authority=false`, `provider_authority=false`,
`capture_eligibility=false`, `clock_qualification=false`,
`qualification_credit=0`, `g3l=NO_GO`.

A complete session is `RECORDED_UNQUALIFIED` (no method-envelope reference
supplied) or `STRUCTURALLY_LINKED_UNQUALIFIED` (a method-envelope reference is
bound), never READY. Neither status asserts clock accuracy, host identity,
custody or review acceptance; a future independent admission consumer must
resolve those against separately accepted records.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat

SCHEMA_RECORD = "ALPHA_V11_GATE3_CLOCK_PROBE_RECORD_V1"
SCHEMA_SESSION = "ALPHA_V11_GATE3_CLOCK_SESSION_V1"

MAX_RAW_RECORD = 16_384
MAX_SAMPLES = 64
MAX_RECORD_OUTPUT = 65_536
MAX_SESSION_TOTAL = 1_048_576
MAX_NODES = 4_096
MAX_DEPTH = 8
MAX_STRING = 4_096
MAX_INT = 2**63 - 1
MAX_RADIUS_US = 1_000_000

CLOCK_NAMES = ("monotonic", "monotonic_raw", "boottime")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")

REFUSAL_CODES = (
    "CLOCK_SOURCE_UNAVAILABLE", "ABI_UNSUPPORTED", "CLOCK_REFERENCE_UNQUALIFIED",
    "SYNC_OR_TIMESCALE_UNQUALIFIED", "CLOCK_CONTINUITY_LOST", "CLOCK_DRIFT_UNSUPPORTED",
    "CLOCK_EXPIRED", "WINDOW_FIT_REFUSED", "BUILD_OR_ORIGIN_UNQUALIFIED",
    "CUSTODY_UNQUALIFIED", "STORE_WRITE_FAILED",
)


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
    if type(value) is not int or not -MAX_INT - 1 <= value <= MAX_INT:
        _refuse(code)
    return value


def _nonneg_integer(value, code="REPRESENTATION_OVERFLOW"):
    if type(value) is not int or not 0 <= value <= MAX_INT:
        _refuse(code)
    return value


def _identifier(value, code="SCHEMA"):
    if type(value) is not str or _ID.fullmatch(value) is None:
        _refuse(code)
    return value


def _bounded_string(value, code="INPUT_BOUNDS"):
    if type(value) is not str:
        _refuse("SCHEMA")
    if len(value.encode("utf-8")) > MAX_STRING:
        _refuse(code)
    return value


def _checked_add(left, right, code="REPRESENTATION_OVERFLOW"):
    result = left + right
    if not -MAX_INT - 1 <= result <= MAX_INT:
        _refuse(code)
    return result


def _checked_sub(left, right, code="REPRESENTATION_OVERFLOW"):
    result = left - right
    if not -MAX_INT - 1 <= result <= MAX_INT:
        _refuse(code)
    return result


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _refuse("DUPLICATE_KEY")
        result[key] = value
    return result


def _parse_int(value):
    if len(value) > 19:
        _refuse("REPRESENTATION_OVERFLOW")
    return int(value)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False).encode("utf-8")


def _bounded_tree(root, max_bytes):
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
            if len(value) > MAX_SAMPLES:
                _refuse("INPUT_BOUNDS")
            for key, item in value.items():
                if type(key) is not str or len(key.encode("utf-8")) > MAX_STRING:
                    _refuse("INPUT_BOUNDS")
                size += len(key.encode("utf-8"))
                stack.append((item, depth + 1))
        elif kind is list:
            if len(value) > MAX_SAMPLES:
                _refuse("INPUT_BOUNDS")
            stack.extend((item, depth + 1) for item in value)
        elif kind is str:
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
        if size > max_bytes:
            _refuse("INPUT_BOUNDS")


def _load_json(raw, max_bytes):
    if type(raw) is not bytes:
        _refuse("SCHEMA")
    if not raw or len(raw) > max_bytes:
        _refuse("INPUT_BOUNDS")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                            parse_int=_parse_int,
                            parse_constant=lambda _: _refuse("JSON_SYNTAX"))
    except Refusal:
        raise
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        _refuse("JSON_SYNTAX")
    _bounded_tree(value, max_bytes)
    return value


def _reference(value, code="CLOCK_LINKAGE"):
    """Explicit SHA-256 + length + raw bytes; never a path/URL dereference."""
    _keys(value, {"sha256", "byte_length", "bytes_hex"}, code)
    digest = value["sha256"]
    if type(digest) is not str or _SHA.fullmatch(digest) is None:
        _refuse(code)
    length = _nonneg_integer(value["byte_length"], code)
    data = value["bytes_hex"]
    if length > MAX_INT // 2 or type(data) is not str or len(data) != length * 2 or len(data) % 2:
        _refuse(code)
    try:
        decoded = bytes.fromhex(data)
    except ValueError:
        _refuse(code)
    if decoded.hex() != data or hashlib.sha256(decoded).hexdigest() != digest:
        _refuse(code)
    return decoded


def _optional_reference(value, code="CLOCK_LINKAGE"):
    if value is None:
        return None
    return _reference(value, code)


# --------------------------------------------------------------------------
# Native probe record parsing (one bounded observation per invocation)
# --------------------------------------------------------------------------

def _clock_bracket(value, code="CLOCK_LINKAGE"):
    _keys(value, {"before_ns", "before_result", "before_errno",
                  "after_ns", "after_result", "after_errno",
                  "res_sec", "res_nsec", "res_result", "res_errno"}, code)
    before = _nonneg_integer(value["before_ns"], code)
    after = _nonneg_integer(value["after_ns"], code)
    before_result = _integer(value["before_result"], code)
    after_result = _integer(value["after_result"], code)
    res_result = _integer(value["res_result"], code)
    if before_result != 0 or after_result != 0 or res_result != 0:
        _refuse("CLOCK_SOURCE_UNAVAILABLE")
    if after < before:
        _refuse("CLOCK_CONTINUITY_LOST")
    res_sec = _nonneg_integer(value["res_sec"], code)
    res_nsec = _nonneg_integer(value["res_nsec"], code)
    if res_sec != 0 or res_nsec > 1_000_000_000:
        _refuse("ABI_UNSUPPORTED")
    return {"before_ns": before, "after_ns": after,
            "before_errno": _integer(value["before_errno"], code),
            "after_errno": _integer(value["after_errno"], code),
            "res_sec": res_sec, "res_nsec": res_nsec,
            "res_errno": _integer(value["res_errno"], code)}


def _realtime_read(value, code="CLOCK_LINKAGE"):
    _keys(value, {"value_ns", "result", "errno",
                  "res_sec", "res_nsec", "res_result", "res_errno"}, code)
    result = _integer(value["result"], code)
    res_result = _integer(value["res_result"], code)
    if result != 0 or res_result != 0:
        _refuse("CLOCK_SOURCE_UNAVAILABLE")
    value_ns = _integer(value["value_ns"], code)
    res_sec = _nonneg_integer(value["res_sec"], code)
    res_nsec = _nonneg_integer(value["res_nsec"], code)
    if res_sec != 0 or res_nsec > 1_000_000_000:
        _refuse("ABI_UNSUPPORTED")
    return {"value_ns": value_ns, "errno": _integer(value["errno"], code),
            "res_sec": res_sec, "res_nsec": res_nsec,
            "res_errno": _integer(value["res_errno"], code)}


_ADJTIMEX_FIELDS = ("call_result", "errno", "modes", "offset", "freq", "maxerror",
                    "esterror", "status", "constant", "precision", "tolerance",
                    "time_sec", "time_usec", "tick", "ppsfreq", "jitter", "shift",
                    "stabil", "jitcnt", "calcnt", "errcnt", "stbcnt", "tai")
# TIME_OK=0 .. TIME_ERROR=5 per the pinned glibc/kernel adjtimex ABI profile.
_ADJTIMEX_OK_RESULTS = frozenset({0, 1, 2, 3, 4})


def _adjtimex(value, code="CLOCK_LINKAGE"):
    _keys(value, set(_ADJTIMEX_FIELDS), code)
    parsed = {key: _integer(value[key], code) for key in _ADJTIMEX_FIELDS}
    if parsed["modes"] != 0:
        _refuse("ABI_UNSUPPORTED")
    if parsed["call_result"] not in _ADJTIMEX_OK_RESULTS:
        if parsed["call_result"] == 5:
            _refuse("SYNC_OR_TIMESCALE_UNQUALIFIED")
        _refuse("CLOCK_SOURCE_UNAVAILABLE")
    if parsed["call_result"] in (1, 2):
        _refuse("SYNC_OR_TIMESCALE_UNQUALIFIED")
    return parsed


def parse_probe_record(raw: bytes) -> dict:
    """Parse one bounded native-recorder output record.

    Returns a dict with keys `status` ("OK" or "REFUSED"). On "OK" the dict
    also carries the fully parsed, bounds-checked fields. This never asserts
    clock accuracy, sync state or host identity; it only checks the closed
    schema, bounds and internally-consistent ordering of a single bracket.
    """
    value = _load_json(raw, MAX_RAW_RECORD)
    _exact(value, dict, "SCHEMA")
    if value.get("schema") != SCHEMA_RECORD:
        _refuse("SCHEMA")
    status = value.get("status")
    if status == "REFUSED":
        _keys(value, {"schema", "status", "code", "detail_errno", "partial"})
        code = value["code"]
        if type(code) is not str or code not in REFUSAL_CODES:
            _refuse("SCHEMA")
        _integer(value["detail_errno"])
        _exact(value["partial"], dict, "SCHEMA")
        return {"status": "REFUSED", "code": code}
    if status != "OK":
        _refuse("SCHEMA")
    _keys(value, {"schema", "status", "boot_id_before", "boot_id_after",
                  "ns_time", "ns_pid", "monotonic", "monotonic_raw", "boottime",
                  "realtime", "adjtimex"})
    boot_before = _bounded_string(value["boot_id_before"], "CLOCK_LINKAGE")
    boot_after = _bounded_string(value["boot_id_after"], "CLOCK_LINKAGE")
    if not boot_before or not boot_after:
        _refuse("CLOCK_SOURCE_UNAVAILABLE")
    if boot_before != boot_after:
        _refuse("CLOCK_CONTINUITY_LOST")
    ns_time = _bounded_string(value["ns_time"], "CLOCK_LINKAGE")
    ns_pid = _bounded_string(value["ns_pid"], "CLOCK_LINKAGE")
    if not ns_time or not ns_pid:
        _refuse("CLOCK_SOURCE_UNAVAILABLE")
    clocks = {name: _clock_bracket(value[name]) for name in CLOCK_NAMES}
    realtime = _realtime_read(value["realtime"])
    adjtimex = _adjtimex(value["adjtimex"])
    m_before = clocks["monotonic"]["before_ns"]
    m_after = clocks["monotonic"]["after_ns"]
    for name in ("monotonic_raw", "boottime"):
        if not (m_before <= clocks[name]["before_ns"] <= clocks[name]["after_ns"] <= m_after):
            _refuse("CLOCK_CONTINUITY_LOST")
    return {"status": "OK", "boot_id": boot_before, "ns_time": ns_time, "ns_pid": ns_pid,
            "monotonic": clocks["monotonic"], "monotonic_raw": clocks["monotonic_raw"],
            "boottime": clocks["boottime"], "realtime": realtime, "adjtimex": adjtimex,
            "m_before": m_before, "m_after": m_after}


def record_digests(raw: bytes, parsed: dict) -> tuple:
    """Hash raw bytes and the canonical parsed projection separately.

    Never substitutes a reserialization of `raw` for the original bytes: the
    raw digest is always computed over exactly the supplied `raw` bytes.
    """
    if type(raw) is not bytes:
        _refuse("SCHEMA")
    raw_sha256 = hashlib.sha256(raw).hexdigest()
    projection_sha256 = hashlib.sha256(_canonical(parsed)).hexdigest()
    return raw_sha256, projection_sha256


# --------------------------------------------------------------------------
# Conservative interval arithmetic (Section 3 of the design handoff)
# --------------------------------------------------------------------------

def _ceil_div(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        _refuse("CLOCK_DRIFT_UNSUPPORTED")
    if numerator < 0:
        _refuse("CLOCK_DRIFT_UNSUPPORTED")
    return -(-numerator // denominator)


def drift_envelope_ns(elapsed_ns: int, rho_num: int, rho_den: int, jitter_ns: int) -> int:
    """D(t) = ceil(rho*t) + j with an explicit nonnegative rational rate bound.

    Unknown drift is unknown uncertainty, never zero: callers with no reviewed
    drift model must not call this and must instead refuse with
    `CLOCK_DRIFT_UNSUPPORTED`.
    """
    if type(elapsed_ns) is not int or elapsed_ns < 0:
        _refuse("CLOCK_DRIFT_UNSUPPORTED")
    if type(rho_num) is not int or type(rho_den) is not int or rho_num < 0 or rho_den <= 0:
        _refuse("CLOCK_DRIFT_UNSUPPORTED")
    if type(jitter_ns) is not int or jitter_ns < 0:
        _refuse("CLOCK_DRIFT_UNSUPPORTED")
    scaled = _ceil_div(rho_num * elapsed_ns, rho_den)
    return _checked_add(scaled, jitter_ns, "CLOCK_DRIFT_UNSUPPORTED")


def project_event_interval_ns(l0_ns: int, u0_ns: int, a_ns: int, b_ns: int,
                               c_ns: int, d_ns: int, rho_num: int, rho_den: int,
                               jitter_ns: int) -> tuple:
    """age_upper = d - a; P(event) = [L0+(c-b)-D(d-a), U0+(d-a)+D(d-a)].

    All inputs are nanoseconds on the same host/boot/clock domain. Requires
    `c >= b` (the event bracket starts no earlier than the calibration
    bracket ends) and `b >= a`, `d >= c`, `u0_ns >= l0_ns`. Returns the
    interval in nanoseconds; callers convert to microseconds with outward
    rounding (floor lower, ceil upper) themselves, since this function never
    narrows by rounding inward.
    """
    for value in (l0_ns, u0_ns, a_ns, b_ns, c_ns, d_ns):
        _integer(value, "CLOCK_DRIFT_UNSUPPORTED")
    if not (a_ns <= b_ns):
        _refuse("CLOCK_CONTINUITY_LOST")
    if not (c_ns >= b_ns):
        _refuse("CLOCK_CONTINUITY_LOST")
    if not (d_ns >= c_ns):
        _refuse("CLOCK_CONTINUITY_LOST")
    if not (u0_ns >= l0_ns):
        _refuse("CLOCK_CALIBRATION")
    age_upper = _checked_sub(d_ns, a_ns)
    drift = drift_envelope_ns(age_upper, rho_num, rho_den, jitter_ns)
    lower = _checked_sub(_checked_add(l0_ns, _checked_sub(c_ns, b_ns)), drift)
    delta = _checked_sub(d_ns, a_ns)
    upper = _checked_add(_checked_add(u0_ns, delta), drift)
    if upper < lower:
        _refuse("CLOCK_CALIBRATION")
    return lower, upper, age_upper


def to_microseconds_outward(lower_ns: int, upper_ns: int) -> tuple:
    """Floor the lower bound and ceil the upper bound; refuse an oversized radius.

    Python's `//` already floors toward negative infinity for both operand
    signs, so the lower bound is `lower_ns // 1000` directly; the upper bound
    uses `-(-upper_ns // 1000)` to get the matching ceiling without a
    sign-dependent branch.
    """
    _integer(lower_ns, "CLOCK_DRIFT_UNSUPPORTED")
    _integer(upper_ns, "CLOCK_DRIFT_UNSUPPORTED")
    if upper_ns < lower_ns:
        _refuse("CLOCK_CALIBRATION")
    lower_us = lower_ns // 1000
    upper_us = -(-upper_ns // 1000)
    radius = (upper_us - lower_us + 1) // 2
    if radius > MAX_RADIUS_US:
        _refuse("CLOCK_EXPIRED")
    return lower_us, upper_us


# --------------------------------------------------------------------------
# Bounded standalone session (fixed false authority flags)
# --------------------------------------------------------------------------

FIXED_AUTHORITY_FLAGS = {
    "execution_authority": False,
    "provider_authority": False,
    "capture_eligibility": False,
    "clock_qualification": False,
    "qualification_credit": 0,
    "g3l": "NO_GO",
}


def build_session(*, session_nonce: str, sequence: int, event_kind: str,
                   raw_record: bytes, parsed_record: dict,
                   calibration_ref: dict | None, method_envelope_ref: dict | None,
                   prior_head: str | None, absence_reasons: list) -> dict:
    """Assemble one closed-schema session record. Never qualifies anything."""
    _identifier(session_nonce, "SCHEMA")
    _nonneg_integer(sequence, "SCHEMA")
    if event_kind not in ("LOCAL_OBSERVATION", "SYNTHETIC"):
        _refuse("SCHEMA")
    if type(raw_record) is not bytes or len(raw_record) > MAX_RAW_RECORD:
        _refuse("INPUT_BOUNDS")
    _exact(absence_reasons, list, "SCHEMA")
    if len(absence_reasons) > MAX_SAMPLES:
        _refuse("INPUT_BOUNDS")
    for reason in absence_reasons:
        if type(reason) is not str or reason not in REFUSAL_CODES:
            _refuse("SCHEMA")
    if prior_head is not None and (type(prior_head) is not str or _SHA.fullmatch(prior_head) is None):
        _refuse("SCHEMA")
    _optional_reference(calibration_ref)
    envelope_bytes = _optional_reference(method_envelope_ref)
    raw_sha256, projection_sha256 = record_digests(raw_record, parsed_record)
    status = "STRUCTURALLY_LINKED_UNQUALIFIED" if envelope_bytes is not None else "RECORDED_UNQUALIFIED"
    session = {
        "schema": SCHEMA_SESSION,
        "status": status,
        "session_nonce": session_nonce,
        "sequence": sequence,
        "event_kind": event_kind,
        "prior_head": prior_head,
        "raw_record_sha256": raw_sha256,
        "raw_record_byte_length": len(raw_record),
        "parsed_projection_sha256": projection_sha256,
        "calibration_ref": calibration_ref,
        "method_envelope_ref": method_envelope_ref,
        "absence_reasons": absence_reasons[:MAX_SAMPLES],
    }
    session.update(FIXED_AUTHORITY_FLAGS)
    encoded = _canonical(session)
    if len(encoded) > MAX_RECORD_OUTPUT:
        _refuse("OUTPUT_BOUNDS")
    session["session_sha256"] = hashlib.sha256(encoded).hexdigest()
    return session


def _no_follow_open(path: str, flags: int, mode: int = 0o600):
    try:
        return os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
    except OSError:
        _refuse("STORE_WRITE_FAILED")


def write_session_file(root: str, session: dict) -> str:
    """Write one bounded session record under `root` with exclusive creation.

    For synthetic tests only: callers must pass a temporary root inside the
    worktree. This performs no custody chain (external checkpoints, fsync
    directory binding, uid/mode verification) -- real durable retention needs
    a separately authorized private root and custody review that this
    diagnostic writer does not implement.
    """
    encoded = _canonical(session)
    if len(encoded) > MAX_RECORD_OUTPUT:
        _refuse("OUTPUT_BOUNDS")
    # Re-validate the two fields used to build the filename independently of
    # whatever already ran inside build_session: this is a disk-write path
    # and must not trust a hand-built caller dict to keep path components safe.
    nonce = _identifier(session.get("session_nonce"), "SCHEMA")
    sequence = _nonneg_integer(session.get("sequence"), "SCHEMA")
    try:
        root_stat = os.stat(root)
    except OSError:
        _refuse("STORE_WRITE_FAILED")
    if not stat.S_ISDIR(root_stat.st_mode):
        _refuse("STORE_WRITE_FAILED")
    name = f"{nonce}.{sequence}.json"
    path = os.path.join(root, name)
    fd = _no_follow_open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        written = 0
        while written < len(encoded):
            try:
                written += os.write(fd, encoded[written:])
            except OSError as error:
                if error.errno == errno.EINTR:
                    continue
                _refuse("STORE_WRITE_FAILED")
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


def session_total_bytes(session_paths: list) -> int:
    total = 0
    for path in session_paths:
        try:
            total = _checked_add(total, os.stat(path).st_size, "STORE_WRITE_FAILED")
        except OSError:
            _refuse("STORE_WRITE_FAILED")
        if total > MAX_SESSION_TOTAL:
            _refuse("INPUT_BOUNDS")
    return total
