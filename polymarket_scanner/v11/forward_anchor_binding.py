"""Unused offline V11 anchor binding comparator; every path returns a refusal.

No caller supplied bytes can authenticate an anchor or establish currentness.
The version here is independent of forward_protected_journal.VERSION.
"""

from dataclasses import dataclass
import hashlib
import json


VERSION = 1
MAX_ANCHOR_BYTES = 16_384
MAX_REQUEST_BYTES = 8_192
MAX_SNAPSHOT_BYTES = 32_768
MAX_LEDGERS = 16
MAX_CHILDREN = 32
MAX_DEPENDENCY_BYTES = 4_096
MAX_JOURNAL_SEQ = 128
MAX_LEDGER_SEQ = 256
MAX_GENERATION = 2**53 - 1
ZERO = "0" * 64

LEDGER_KEYS = frozenset({"ledger_id", "genesis_hash", "seq", "prefix_hash",
                         "view_sha256", "archive_sha256"})
TARGET_KEYS = frozenset({"request_id", "namespace", "plan_id", "scope_key",
                         "event_id", "interval_id", "operation_id", "admission_id",
                         "capture_id", "child_ids"})
FRONTIER_KEYS = frozenset({"boot_id", "session_id", "custody_generation",
                           "journal_id", "journal_seq", "journal_head",
                           "catalog_sha256", "membership_sha256", "ledgers"})
ANCHOR_KEYS = frozenset({"version", "anchor_id", "issuer_id", "custodian_id",
                         "executable_sha256", "policy_sha256", "enrollment_id",
                         "authentication_format", "verification_root_id",
                         "request_sha256", "snapshot_sha256"}) | FRONTIER_KEYS
REQUEST_KEYS = (frozenset({"version", "expected_anchor_id", "expected_boot_id",
                           "expected_issuer_id", "expected_custodian_id",
                           "expected_executable_sha256", "expected_policy_sha256",
                           "expected_enrollment_id", "expected_authentication_format",
                           "expected_verification_root_id",
                           "expected_session_id", "expected_custody_generation",
                           "expected_journal_id", "expected_journal_seq",
                           "expected_journal_head", "expected_catalog_sha256",
                           "expected_membership_sha256", "snapshot_sha256",
                           "verifier_executable_sha256", "verifier_policy_sha256"})
                | TARGET_KEYS)
SNAPSHOT_KEYS = frozenset({"version", "dependency_hex", "witness_history_sha256"}) | TARGET_KEYS | FRONTIER_KEYS


@dataclass(frozen=True)
class Refusal:
    code: str


class _Invalid(Exception):
    def __init__(self, code):
        self.code = code


def _need(condition, code):
    if not condition:
        raise _Invalid(code)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _need(key not in result, "DUPLICATE_KEY")
        result[key] = value
    return result


def _constant(_value):
    raise _Invalid("MALFORMED_JSON")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _decode(raw, limit, bound_code, keys):
    _need(type(raw) is bytes and 0 < len(raw) <= limit, bound_code)
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"),
                           object_pairs_hook=_pairs, parse_constant=_constant)
    except _Invalid:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise _Invalid("MALFORMED_JSON") from exc
    _need(type(value) is dict and value.keys() == keys, "SCHEMA_KEYS")
    try:
        canonical = _canonical(value)
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise _Invalid("SCHEMA_TYPE") from exc
    _need(raw == canonical, "NONCANONICAL_ENVELOPE")
    _need(type(value["version"]) is int and value["version"] == VERSION,
          "UNSUPPORTED_VERSION")
    return value


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _id(value):
    return type(value) is str and 1 <= len(value) <= 96 and value.isascii() and all(
        char in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-"
        for char in value)


def _hash(value):
    return type(value) is str and len(value) == 64 and all(
        char in "0123456789abcdef" for char in value)


def _uint(value, low, high):
    return type(value) is int and low <= value <= high


def _identity(value):
    for name in ("boot_id", "session_id", "journal_id"):
        _need(_id(value[name]), "SCHEMA_TYPE")
    _need(_uint(value["custody_generation"], 1, MAX_GENERATION), "GENERATION_INVALID")
    _need(_uint(value["journal_seq"], 1, MAX_JOURNAL_SEQ), "FRONTIER_INVALID")
    for name in ("journal_head", "catalog_sha256", "membership_sha256"):
        _need(_hash(value[name]), "SCHEMA_TYPE")


def _ledgers(value):
    _need(type(value) is list and 1 <= len(value) <= MAX_LEDGERS, "LEDGER_BOUND")
    ids = []
    members = []
    for ledger in value:
        _need(type(ledger) is dict and ledger.keys() == LEDGER_KEYS, "SCHEMA_KEYS")
        _need(_id(ledger["ledger_id"]), "SCHEMA_TYPE")
        for name in ("genesis_hash", "prefix_hash", "view_sha256", "archive_sha256"):
            _need(_hash(ledger[name]), "SCHEMA_TYPE")
        _need(_uint(ledger["seq"], 0, MAX_LEDGER_SEQ), "FRONTIER_INVALID")
        _need(ledger["seq"] != 0 or ledger["prefix_hash"] == ledger["genesis_hash"],
              "FRONTIER_INVALID")
        ids.append(ledger["ledger_id"])
        members.append({"ledger_id": ledger["ledger_id"],
                        "genesis_hash": ledger["genesis_hash"]})
    _need(ids == sorted(set(ids)), "CATALOG_MEMBERS")
    return _sha(_canonical(members))


def _frontier(value):
    _identity(value)
    _need(value["membership_sha256"] == _ledgers(value["ledgers"]),
          "MEMBERSHIP_MISMATCH")


def _target(value):
    for name in TARGET_KEYS - {"child_ids"}:
        _need(_id(value[name]), "SCHEMA_TYPE")
    children = value["child_ids"]
    _need(type(children) is list and len(children) <= MAX_CHILDREN, "CHILD_BOUND")
    _need(all(_id(child) for child in children), "SCHEMA_TYPE")
    _need(children == sorted(set(children)), "CHILD_ORDER")


def _request(value):
    _target(value)
    for name in ("expected_anchor_id", "expected_issuer_id", "expected_custodian_id",
                 "expected_enrollment_id", "expected_verification_root_id",
                 "expected_boot_id", "expected_session_id", "expected_journal_id"):
        _need(_id(value[name]), "SCHEMA_TYPE")
    _need(value["expected_authentication_format"] == "external-v1",
          "AUTH_FORMAT_UNSUPPORTED")
    _need(_uint(value["expected_custody_generation"], 1, MAX_GENERATION),
          "GENERATION_INVALID")
    _need(_uint(value["expected_journal_seq"], 1, MAX_JOURNAL_SEQ),
          "FRONTIER_INVALID")
    for name in ("expected_journal_head", "expected_catalog_sha256",
                 "expected_membership_sha256", "snapshot_sha256",
                 "expected_executable_sha256", "expected_policy_sha256",
                 "verifier_executable_sha256", "verifier_policy_sha256"):
        _need(_hash(value[name]), "SCHEMA_TYPE")


def _snapshot(value):
    _target(value)
    _frontier(value)
    encoded = value["dependency_hex"]
    _need(type(encoded) is str and len(encoded) <= 2 * MAX_DEPENDENCY_BYTES
          and len(encoded) % 2 == 0, "DEPENDENCY_BOUND")
    _need(all(char in "0123456789abcdef" for char in encoded), "DEPENDENCY_ENCODING")
    _need(_hash(value["witness_history_sha256"]), "SCHEMA_TYPE")


def check_anchor_binding(anchor_bytes, request_bytes, snapshot_bytes):
    """Compare bounded synthetic envelopes; return Refusal on every path.

    Codes are checked in parse order: anchor, request, snapshot, then bindings.
    An exact match still lacks external authentication and currentness.
    """
    try:
        anchor = _decode(anchor_bytes, MAX_ANCHOR_BYTES, "ANCHOR_BOUND", ANCHOR_KEYS)
        request = _decode(request_bytes, MAX_REQUEST_BYTES, "REQUEST_BOUND", REQUEST_KEYS)
        snapshot = _decode(snapshot_bytes, MAX_SNAPSHOT_BYTES, "SNAPSHOT_BOUND", SNAPSHOT_KEYS)
        for name in ("anchor_id", "issuer_id", "custodian_id", "enrollment_id",
                     "verification_root_id"):
            _need(_id(anchor[name]), "SCHEMA_TYPE")
        _need(anchor["authentication_format"] == "external-v1", "AUTH_FORMAT_UNSUPPORTED")
        for name in ("executable_sha256", "policy_sha256", "request_sha256", "snapshot_sha256"):
            _need(_hash(anchor[name]), "SCHEMA_TYPE")
        _frontier(anchor)
        _request(request)
        _snapshot(snapshot)
        _need(anchor["request_sha256"] == _sha(request_bytes), "REQUEST_BINDING")
        _need(anchor["snapshot_sha256"] == _sha(snapshot_bytes)
              and request["snapshot_sha256"] == _sha(snapshot_bytes), "SNAPSHOT_BINDING")
        expected = {"expected_anchor_id": "anchor_id",
                    "expected_issuer_id": "issuer_id",
                    "expected_custodian_id": "custodian_id",
                    "expected_executable_sha256": "executable_sha256",
                    "expected_policy_sha256": "policy_sha256",
                    "expected_enrollment_id": "enrollment_id",
                    "expected_authentication_format": "authentication_format",
                    "expected_verification_root_id": "verification_root_id",
                    "expected_boot_id": "boot_id",
                    "expected_session_id": "session_id",
                    "expected_custody_generation": "custody_generation",
                    "expected_journal_id": "journal_id", "expected_journal_seq": "journal_seq",
                    "expected_journal_head": "journal_head",
                    "expected_catalog_sha256": "catalog_sha256",
                    "expected_membership_sha256": "membership_sha256"}
        _need(all(request[left] == anchor[right] for left, right in expected.items()),
              "EXPECTED_FRONTIER_MISMATCH")
        _need(all(anchor[name] == snapshot[name] for name in FRONTIER_KEYS),
              "SNAPSHOT_FRONTIER_MISMATCH")
        _need(all(request[name] == snapshot[name] for name in TARGET_KEYS),
              "TARGET_MISMATCH")
    except _Invalid as exc:
        return Refusal(exc.code)
    return Refusal("ANCHOR_UNAVAILABLE")
