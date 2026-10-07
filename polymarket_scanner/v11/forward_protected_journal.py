"""Unused, offline V11 journal format checker. No custody or qualification authority.

The amendment does not define a commissioned anchor, a complete semantic input
contract, or a positive verdict. Consequently this module never returns Coverage.
It checks bounded synthetic bytes so a later design can review concrete refusals.
"""

from dataclasses import dataclass
import hashlib
import json
import math


VERSION = 1
MAX_JOURNAL_BYTES = 1_048_576
MAX_CATALOG_BYTES = 2_097_152
MAX_RECORD_BYTES = 8_192
MAX_RECORDS = 128
MAX_MEMBERS = 16
MAX_ROWS = 256
MAX_ROW_BYTES = 4_096
ZERO = "0" * 64
KINDS = frozenset({"ENROLLMENT", "TRANSITION", "OPEN", "PREPARE", "SEAL",
                   "ABORT", "GAP", "INVALIDATION", "CHECKPOINT", "ARCHIVE"})
BASE = frozenset({"version", "kind", "financial_authority", "journal_id", "seq",
                  "prev_hash", "session_id", "boot_id", "ledger_id", "genesis_hash",
                  "ledger_seq", "ledger_prefix_hash", "interval_id", "operation_id",
                  "row_sha256", "dependency_sha256", "witness_sha256",
                  "clock_lower", "clock_upper", "detail"})
DETAILS = {
    "ENROLLMENT": {"namespace", "plan_id", "event_id"},
    "TRANSITION": {"generation", "reason"},
    "OPEN": {"admission_id"},
    "PREPARE": {"target_seq"},
    "SEAL": {"prepare_seq"},
    "ABORT": {"reason"},
    "GAP": {"reason"},
    "INVALIDATION": {"reason"},
    "CHECKPOINT": {"catalog_sha256"},
    "ARCHIVE": {"archive_sha256"},
}
ROW_KEYS = frozenset({"seq", "prefix_hash", "kind", "event_id", "body_sha256", "row_hex"})
VIEW_KEYS = frozenset({"ledger_id", "genesis_hash", "namespace", "plan_id",
                       "event_id", "seq", "prefix_hash", "complete", "rows", "view_sha256"})
CATALOG_KEYS = frozenset({"version", "namespace", "plan_id", "event_id",
                          "member_ids", "views", "complete", "catalog_sha256"})


@dataclass(frozen=True)
class Refusal:
    code: str


@dataclass(frozen=True)
class Coverage:
    """Reserved result type; version 1 has no construction path."""

    journal_head: str
    catalog_sha256: str


class _Invalid(Exception):
    def __init__(self, code):
        self.code = code


def _need(condition, code):
    if not condition:
        raise _Invalid(code)


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        _need(key not in value, "DUPLICATE_KEY")
        value[key] = item
    return value


def _constant(_value):
    raise _Invalid("NONFINITE_NUMBER")


def _decode(data):
    try:
        return json.loads(data, object_pairs_hook=_pairs, parse_constant=_constant)
    except _Invalid:
        raise
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError) as exc:
        raise _Invalid("MALFORMED_JSON") from exc


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise _Invalid("SCHEMA_TYPE") from exc


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _hash(value):
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _id(value):
    return type(value) is str and 1 <= len(value) <= 96 and value.isascii() and all(
        c.isalnum() or c in "._:-" for c in value)


def _uint(value, maximum):
    return type(value) is int and 0 <= value <= maximum


def _keys(value, expected):
    _need(type(value) is dict and value.keys() == expected, "SCHEMA_KEYS")


def _record(raw, expected_seq, previous, identity):
    _need(len(raw) <= MAX_RECORD_BYTES, "ENVELOPE_BOUND")
    item = _decode(raw)
    _keys(item, BASE)
    _need(_canonical(item) == raw, "NONCANONICAL_ENVELOPE")
    _need(type(item["version"]) is int and item["version"] == VERSION, "UNSUPPORTED_VERSION")
    kind = item["kind"]
    _need(type(kind) is str and kind in KINDS, "SCHEMA_TYPE")
    _need(item["financial_authority"] is False, "FINANCIAL_AUTHORITY")
    _need(_uint(item["seq"], MAX_RECORDS) and item["seq"] == expected_seq, "JOURNAL_SEQUENCE")
    _need(_hash(item["prev_hash"]) and item["prev_hash"] == previous, "JOURNAL_HASH")
    for name in ("journal_id", "session_id", "boot_id", "ledger_id", "interval_id", "operation_id"):
        _need(_id(item[name]), "SCHEMA_TYPE")
    for name in ("genesis_hash", "ledger_prefix_hash", "row_sha256", "dependency_sha256", "witness_sha256"):
        _need(_hash(item[name]), "SCHEMA_TYPE")
    _need(_uint(item["ledger_seq"], MAX_ROWS), "SCHEMA_TYPE")
    for name in ("clock_lower", "clock_upper"):
        value = item[name]
        _need(type(value) in (int, float) and math.isfinite(value) and value >= 0, "CLOCK_INVALID")
    _need(item["clock_lower"] <= item["clock_upper"], "CLOCK_INVALID")
    detail = item["detail"]
    _keys(detail, DETAILS[kind])
    for key, value in detail.items():
        if key in ("generation", "target_seq", "prepare_seq"):
            _need(_uint(value, MAX_ROWS), "SCHEMA_TYPE")
        elif key.endswith("sha256"):
            _need(_hash(value), "SCHEMA_TYPE")
        else:
            _need(_id(value), "SCHEMA_TYPE")
    triple = item["journal_id"], item["session_id"], item["boot_id"]
    _need(identity is None or triple == identity, "SESSION_MISMATCH")
    return item, _sha(raw), triple


def _parse_journal(journal_bytes):
    _need(type(journal_bytes) is bytes and 0 < len(journal_bytes) <= MAX_JOURNAL_BYTES,
          "JOURNAL_BOUND")
    _need(journal_bytes.endswith(b"\n"), "JOURNAL_TRUNCATED")
    lines = journal_bytes[:-1].split(b"\n")
    _need(0 < len(lines) <= MAX_RECORDS and all(lines), "JOURNAL_BOUND")
    records = []
    previous, identity = ZERO, None
    for seq, line in enumerate(lines, 1):
        item, previous, identity = _record(line, seq, previous, identity)
        records.append(item)
    return records, previous


def _view(view, namespace, plan_id, event_id):
    _keys(view, VIEW_KEYS)
    _need(all(_id(view[k]) for k in ("ledger_id", "namespace", "plan_id", "event_id")), "SCHEMA_TYPE")
    _need(all(_hash(view[k]) for k in ("genesis_hash", "prefix_hash", "view_sha256")), "SCHEMA_TYPE")
    _need(view["namespace"] == namespace and view["plan_id"] == plan_id
          and view["event_id"] == event_id, "CATALOG_SCOPE")
    _need(view["complete"] is True, "CATALOG_INCOMPLETE")
    rows = view["rows"]
    _need(type(rows) is list and len(rows) <= MAX_ROWS and type(view["seq"]) is int
          and view["seq"] == len(rows), "VIEW_BOUND")
    prefix = view["genesis_hash"]
    prefixes = [prefix]
    for index, row in enumerate(rows, 1):
        _keys(row, ROW_KEYS)
        _need(row["seq"] == index and type(row["seq"]) is int, "LEDGER_SEQUENCE")
        _need(_id(row["kind"]) and _id(row["event_id"]), "SCHEMA_TYPE")
        _need(_hash(row["body_sha256"]) and _hash(row["prefix_hash"]), "SCHEMA_TYPE")
        encoded = row["row_hex"]
        _need(type(encoded) is str and len(encoded) <= 2 * MAX_ROW_BYTES and len(encoded) % 2 == 0,
              "ROW_BOUND")
        try:
            body = bytes.fromhex(encoded)
        except ValueError as exc:
            raise _Invalid("ROW_ENCODING") from exc
        _need(body.hex() == encoded and _sha(body) == row["body_sha256"], "ROW_HASH")
        prefix = _sha(bytes.fromhex(prefix) + index.to_bytes(8, "big") + body)
        _need(row["prefix_hash"] == prefix, "LEDGER_PREFIX")
        prefixes.append(prefix)
    _need(view["prefix_hash"] == prefix, "LEDGER_PREFIX")
    unsigned = {k: v for k, v in view.items() if k != "view_sha256"}
    _need(view["view_sha256"] == _sha(_canonical(unsigned)), "VIEW_HASH")
    return prefixes


def _catalog(catalog, records):
    _keys(catalog, CATALOG_KEYS)
    _need(type(catalog["version"]) is int and catalog["version"] == VERSION, "UNSUPPORTED_VERSION")
    _need(catalog["complete"] is True, "CATALOG_INCOMPLETE")
    _need(all(_id(catalog[k]) for k in ("namespace", "plan_id", "event_id")), "SCHEMA_TYPE")
    members, views = catalog["member_ids"], catalog["views"]
    _need(type(members) is list and type(views) is list and 1 <= len(members) <= MAX_MEMBERS
          and len(members) == len(views), "CATALOG_BOUND")
    _need(all(_id(v) for v in members) and members == sorted(set(members)), "CATALOG_MEMBERS")
    _need([v.get("ledger_id") if type(v) is dict else None for v in views] == members,
          "CATALOG_MEMBERS")
    _need(_hash(catalog["catalog_sha256"]), "SCHEMA_TYPE")
    enrollments = {}
    for record in records:
        if record["kind"] == "ENROLLMENT":
            ledger_id = record["ledger_id"]
            _need(ledger_id not in enrollments and record["ledger_seq"] == 0
                  and record["ledger_prefix_hash"] == record["genesis_hash"], "ENROLLMENT_INVALID")
            _need(record["detail"] == {k: catalog[k] for k in ("namespace", "plan_id", "event_id")},
                  "CATALOG_SCOPE")
            enrollments[ledger_id] = record
        else:
            _need(record["ledger_id"] in enrollments, "ENROLLMENT_MISSING")
    _need(sorted(enrollments) == members, "CATALOG_MEMBERS")
    by_id = {}
    for view in views:
        prefixes = _view(view, catalog["namespace"], catalog["plan_id"], catalog["event_id"])
        _need(view["genesis_hash"] == enrollments[view["ledger_id"]]["genesis_hash"], "GENESIS_MISMATCH")
        by_id[view["ledger_id"]] = (view, prefixes)
    _need(len(_canonical(catalog)) <= MAX_CATALOG_BYTES, "CATALOG_BOUND")
    unsigned = {k: v for k, v in catalog.items() if k != "catalog_sha256"}
    _need(_sha(_canonical(unsigned)) == catalog["catalog_sha256"], "CATALOG_HASH")
    for record in records:
        if record["kind"] == "CHECKPOINT":
            _need(record["detail"]["catalog_sha256"] == catalog["catalog_sha256"],
                  "CATALOG_CHECKPOINT")
    highwater = {member: 0 for member in members}
    opened = set()
    prepared = {}
    archived = set()
    for record in records:
        ledger_id = record["ledger_id"]
        kind = record["kind"]
        _need(ledger_id not in archived, "ARCHIVE_TERMINAL")
        view, prefixes = by_id[record["ledger_id"]]
        _need(record["genesis_hash"] == view["genesis_hash"], "GENESIS_MISMATCH")
        seq = record["ledger_seq"]
        _need(type(seq) is int and seq <= view["seq"] and seq >= highwater[record["ledger_id"]],
              "FRONTIER_MISMATCH")
        _need(record["ledger_prefix_hash"] == prefixes[seq], "FRONTIER_MISMATCH")
        key = ledger_id, record["interval_id"], record["operation_id"]
        if kind == "OPEN":
            _need(key not in opened, "INTERVAL_STATE")
            opened.add(key)
        elif kind == "PREPARE":
            _need(key in opened and key not in prepared
                  and record["detail"]["target_seq"] == seq + 1, "INTERVAL_STATE")
            prepared[key] = record
        elif kind == "SEAL":
            prior = prepared.pop(key, None)
            _need(prior is not None and record["detail"]["prepare_seq"] == prior["seq"]
                  and seq == prior["detail"]["target_seq"]
                  and record["row_sha256"] == prior["row_sha256"]
                  and view["rows"][seq - 1]["body_sha256"] == record["row_sha256"],
                  "INTERVAL_STATE")
        elif kind == "ABORT":
            opened.discard(key)
            prepared.pop(key, None)
        elif kind == "ARCHIVE":
            _need(not any(k[0] == ledger_id for k in prepared), "INTERVAL_STATE")
            archived.add(ledger_id)
        highwater[record["ledger_id"]] = seq
    _need(not prepared, "JOURNAL_TRUNCATED")
    _need(all(highwater[k] == by_id[k][0]["seq"] for k in members), "FRONTIER_MISMATCH")
    event_members = set()
    for view in views:
        for row in view["rows"]:
            if row["kind"] == "INVALIDATION":
                raise _Invalid("INTERVAL_INVALIDATED")
            if row["event_id"] == catalog["event_id"]:
                event_members.add(view["ledger_id"])
    _need(len(event_members) <= 1, "CROSS_LEDGER_EVENT_HISTORY")
    _need(not any(record["kind"] == "GAP" for record in records), "JOURNAL_GAP")
    _need(not any(record["kind"] == "INVALIDATION" for record in records),
          "INTERVAL_INVALIDATED")


def verify_interval(journal_bytes, anchor, ledger_view_catalog, witnesses):
    """Return a typed refusal. Anchor and witness authority remain undefined.

    Structural errors take precedence. A syntactically complete synthetic history
    still returns ANCHOR_UNAVAILABLE, irrespective of caller supplied objects.
    """
    try:
        records, _head = _parse_journal(journal_bytes)
        _need(type(ledger_view_catalog) is dict, "CATALOG_BOUND")
        _catalog(ledger_view_catalog, records)
        _need(type(witnesses) is dict and witnesses == {}, "WITNESS_CONTRACT_UNDEFINED")
    except _Invalid as exc:
        return Refusal(exc.code)
    return Refusal("ANCHOR_UNAVAILABLE")
