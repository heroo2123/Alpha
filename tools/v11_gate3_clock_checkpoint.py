"""Pure supplied-byte fixture checkpoint comparison; no checkpoint issuer.

Separate custody framing replay, sharing only the reviewed raw dossier parser.
Pins are caller inputs, NOT authenticated authority or proof of freshness.
See docs/V11_GATE3_CLOCK_CHECKPOINT_HANDOFF_20261003.md.
"""
from __future__ import annotations

import hashlib
import json
import re

from tools import v11_gate3_clock_dossier as dossier

MAX_SESSIONS = 8
MAX_SAMPLES = 64
MAX_RAW = 16_384
MAX_OBJECT = 65_536
MAX_SESSION = 1_048_576
TERMINAL_RESERVE = 4_096
MAX_CHECKPOINT = 65_536
MAX_ANCHOR = 4_096
MAX_CONTEXT = 16_384
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_META = {"session_nonce", "method_id", "profile_id", "build_id", "host_id", "event_kind"}


def _flags():
    # Intentionally independent of the writer's mutable implementation state.
    return dict(execution_authority=False, provider_authority=False,
                capture_eligibility=False, clock_qualification=False,
                custody_qualification=False, qualification_credit=0,
                g3l="NO_GO", custody_status="CUSTODY_UNQUALIFIED")


def _limits():
    return dict(external_checkpoint_authenticated=False,
                anchor_freshness="UNKNOWN", rollback_protection=False,
                crash_durability="UNKNOWN_ON_REPLAY", recovery_gaps="UNKNOWN",
                diagnostic_persisted=False, evidence_incomplete=True)


class CheckpointError(ValueError):
    """Fixed bounded diagnostic, never a persisted failure receipt."""
    def __init__(self, code):
        self.code = code
        self.evidence = dict(_flags(), **_limits(), code=code)
        super().__init__(code)


def _fail(code):
    raise CheckpointError(code)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _bytes(value, cap):
    if type(value) is not bytes or not 0 < len(value) <= cap:
        _fail("INPUT_BOUNDS")


def _keys(value, keys):
    if type(value) is not dict or len(value) != len(keys) or value.keys() != keys:
        _fail("SCHEMA")


def _id(value):
    if type(value) is not str or _ID.fullmatch(value) is None:
        _fail("SCHEMA")


def _hash(value):
    if type(value) is not str or _HASH.fullmatch(value) is None:
        _fail("SCHEMA")


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        _fail("SCHEMA")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("DUPLICATE_KEY")
        result[key] = value
    return result


def _number(token):
    if len(token) > 20:
        _fail("INPUT_BOUNDS")
    return int(token)


def _no_float(token):
    _fail("SCHEMA")


def _json(raw, cap):
    _bytes(raw, cap)
    # Pre-decode depth bound, honoring JSON escapes; byte cap bounds scanning.
    depth, quoted, escaped = 0, False, False
    for char in raw:
        if quoted:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                quoted = False
        elif char == 34:
            quoted = True
        elif char in (91, 123):
            depth += 1
            if depth > 8:
                _fail("INPUT_BOUNDS")
        elif char in (93, 125):
            depth -= 1
    try:
        result = json.loads(raw.decode("ascii"), object_pairs_hook=_pairs,
                            parse_int=_number, parse_float=_no_float,
                            parse_constant=_no_float)
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, CheckpointError):
            raise
        _fail("SCHEMA")
    pending, nodes = [result], 0
    while pending:
        item = pending.pop()
        nodes += 1
        if nodes > 4096:
            _fail("INPUT_BOUNDS")
        if type(item) is dict:
            pending.extend(item.keys())
            pending.extend(item.values())
        elif type(item) is list:
            pending.extend(item)
        elif type(item) is str and len(item) > 4096:
            _fail("INPUT_BOUNDS")
    if _canonical(result) != raw:
        _fail("NONCANONICAL")
    return result


def _fixed(value):
    for key, expected in _flags().items():
        if type(value[key]) is not type(expected) or value[key] != expected:
            _fail("AUTHORITY_FIELDS")


def _replay(bundle):
    """Reconstruct exact framing without writer helpers or filesystem reads."""
    header, objects, terminal = bundle
    parsed = _json(header, TERMINAL_RESERVE)
    _keys(parsed, {"schema", "metadata"} | _flags().keys())
    _fixed(parsed)
    if parsed["schema"] != "CLOCK_CUSTODY_FIXTURE_V1":
        _fail("SCHEMA")
    metadata = parsed["metadata"]
    _keys(metadata, _META)
    for value in metadata.values():
        _id(value)
    if metadata["event_kind"] != "SYNTHETIC":
        _fail("SCHEMA")
    nonce = metadata["session_nonce"]
    head, total, hashes, failures = _sha(header), len(header), [], []
    for sequence, frame in enumerate(objects, 1):
        if len(frame) < 4:
            _fail("FRAME_TRUNCATED")
        length = int.from_bytes(frame[:4], "big")
        if length > TERMINAL_RESERVE or length > len(frame) - 4:
            _fail("FRAME_TRUNCATED")
        raw = frame[4 + length:]
        if len(raw) > MAX_RAW:
            _fail("INPUT_BOUNDS")
        try:
            projection = dossier.parse_probe_record(raw)
            # Dossier projection uses UTF-8, unlike the custody envelope.
            projection_raw = json.dumps(projection, sort_keys=True, separators=(",", ":"),
                                        ensure_ascii=False, allow_nan=False).encode("utf-8")
            projection_hash = _sha(projection_raw)
            status, reason = projection["status"], projection.get("code")
        except dossier.Refusal as error:
            projection_hash, status, reason = None, "PARSE_REFUSED", error.code
        envelope = dict(_flags(), schema="CLOCK_CUSTODY_OBJECT_V1", session_nonce=nonce,
                        sequence=sequence, prior_head=head, raw_sha256=_sha(raw),
                        raw_byte_length=len(raw), parsed_projection_sha256=projection_hash,
                        parse_status=status, parse_reason=reason)
        encoded = _canonical(envelope)
        if frame != len(encoded).to_bytes(4, "big") + encoded + raw:
            _fail("FRAME_MISMATCH")
        total += len(frame)
        if total > MAX_SESSION - TERMINAL_RESERVE:
            _fail("INPUT_BOUNDS")
        head = _sha(frame)
        hashes.append(head)
        if status != "OK":
            failures.append(dict(sequence=sequence, status=status, reason=reason))
    terminal_reason = None
    for reason in ("CALLER_FINISHED", "CAPACITY_EXHAUSTED"):
        expected = dict(_flags(), schema="CLOCK_CUSTODY_TERMINAL_V1", session_nonce=nonce,
                        count=len(objects), head=head, chain_byte_length=total, reason=reason)
        if terminal == _canonical(expected):
            terminal_reason = reason
            break
    if terminal_reason is None:
        _fail("TERMINAL_MISMATCH")
    summary = dict(session_nonce=nonce, header_sha256=_sha(header), count=len(objects),
                   object_sha256=hashes, head=head, chain_byte_length=total,
                   terminal_sha256=_sha(terminal))
    return summary, dict(session_nonce=nonce, terminal_reason=terminal_reason,
                         failures=failures)


def compare_checkpoint(checkpoint_bytes, anchor_bytes, receipt_context_bytes, sessions):
    """Compare supplied complete fixture bundles to a separately supplied pin.

    sessions is an exact tuple of (header bytes, object-bytes tuple, terminal
    bytes) tuples. No default/fallback anchor, issuer, disk access or trust store.
    A matching pin proves byte agreement only, even if callers label it external.
    """
    if anchor_bytes is None:
        _fail("ANCHOR_REQUIRED")
    _bytes(anchor_bytes, MAX_ANCHOR)
    _bytes(checkpoint_bytes, MAX_CHECKPOINT)
    _bytes(receipt_context_bytes, MAX_CONTEXT)
    if type(sessions) is not tuple or not 1 <= len(sessions) <= MAX_SESSIONS:
        _fail("INPUT_BOUNDS")
    # Validate all cardinalities/types/sizes before hashing or parsing a bundle.
    for bundle in sessions:
        if type(bundle) is not tuple or len(bundle) != 3:
            _fail("INPUT_BOUNDS")
        header, objects, terminal = bundle
        _bytes(header, TERMINAL_RESERVE)
        _bytes(terminal, TERMINAL_RESERVE)
        if type(objects) is not tuple or len(objects) > MAX_SAMPLES:
            _fail("INPUT_BOUNDS")
        size = len(header) + len(terminal)
        for frame in objects:
            _bytes(frame, MAX_OBJECT)
            size += len(frame)
            if size > MAX_SESSION:
                _fail("INPUT_BOUNDS")
    anchor = _json(anchor_bytes, MAX_ANCHOR)
    _keys(anchor, {"schema", "scope_id", "custodian_id", "generation",
                   "checkpoint_sha256", "checkpoint_byte_length"})
    if anchor["schema"] != "CLOCK_CHECKPOINT_FIXTURE_PIN_V1":
        _fail("SCHEMA")
    for key in ("scope_id", "custodian_id"):
        _id(anchor[key])
    _integer(anchor["generation"], 1, (1 << 63) - 1)
    _integer(anchor["checkpoint_byte_length"], 1, MAX_CHECKPOINT)
    _hash(anchor["checkpoint_sha256"])
    if (len(checkpoint_bytes) != anchor["checkpoint_byte_length"] or
            _sha(checkpoint_bytes) != anchor["checkpoint_sha256"]):
        _fail("ANCHOR_MISMATCH")
    checkpoint = _json(checkpoint_bytes, MAX_CHECKPOINT)
    _keys(checkpoint, {"schema", "scope_id", "custodian_id", "generation",
                       "receipt_context_sha256", "receipt_context_byte_length",
                       "sessions"} | _flags().keys())
    _fixed(checkpoint)
    if checkpoint["schema"] != "CLOCK_CHECKPOINT_FIXTURE_V1":
        _fail("SCHEMA")
    _integer(checkpoint["generation"], 1, (1 << 63) - 1)
    for key in ("scope_id", "custodian_id", "generation"):
        if checkpoint[key] != anchor[key]:
            _fail("ANCHOR_SCOPE_OR_GENERATION")
    _integer(checkpoint["receipt_context_byte_length"], 1, MAX_CONTEXT)
    _hash(checkpoint["receipt_context_sha256"])
    if (len(receipt_context_bytes) != checkpoint["receipt_context_byte_length"] or
            _sha(receipt_context_bytes) != checkpoint["receipt_context_sha256"]):
        _fail("RECEIPT_CONTEXT_MISMATCH")
    entries = checkpoint["sessions"]
    if type(entries) is not list or len(entries) != len(sessions):
        _fail("SESSION_SET_MISMATCH")
    summaries, diagnostics, seen = [], [], set()
    for bundle in sessions:
        summary, diagnostic = _replay(bundle)
        nonce = summary["session_nonce"]
        if nonce in seen:
            _fail("DUPLICATE_SESSION")
        seen.add(nonce)
        summaries.append(summary)
        diagnostics.append(diagnostic)
    # Canonical-byte comparison closes nested schemas and prevents bool == int.
    if _canonical(entries) != _canonical(summaries):
        _fail("CHECKPOINT_SESSION_MISMATCH")
    return dict(_flags(), **_limits(), status="SUPPLIED_CHECKPOINT_MATCH_UNQUALIFIED",
                checkpoint_sha256=_sha(checkpoint_bytes), anchor_sha256=_sha(anchor_bytes),
                receipt_context_sha256=_sha(receipt_context_bytes),
                comparison_scope="EXACT_CLOSED_SYNTHETIC_SESSION_SET",
                sessions=summaries, session_diagnostics=diagnostics)
