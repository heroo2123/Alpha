"""Offline synthetic protocol negatives; no fixture establishes host custody."""

from copy import deepcopy
import hashlib
import inspect
import json

import pytest

from polymarket_scanner.v11 import forward_protected_journal as journal
from polymarket_scanner.v11 import shadow_commission
from polymarket_scanner.v11.evidence import EvidenceError


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("ascii")


def seal(value, field):
    value[field] = digest(encoded({k: v for k, v in value.items() if k != field}))
    return value


def fixture(*, second=False):
    genesis = "a" * 64
    row_bytes = b'{"synthetic":true}'
    prefix = digest(bytes.fromhex(genesis) + (1).to_bytes(8, "big") + row_bytes)
    row = dict(seq=1, prefix_hash=prefix, kind="CAPTURE", event_id="event-A",
               body_sha256=digest(row_bytes), row_hex=row_bytes.hex())
    view = seal(dict(ledger_id="ledger-A", genesis_hash=genesis, namespace="ns-A",
                     plan_id="plan-A", event_id="event-A", seq=1, prefix_hash=prefix,
                     complete=True, rows=[row]), "view_sha256")
    views = [view]
    if second:
        sibling = seal(dict(ledger_id="ledger-B", genesis_hash=genesis, namespace="ns-A",
                            plan_id="plan-A", event_id="event-A", seq=0,
                            prefix_hash=genesis, complete=True, rows=[]), "view_sha256")
        views.append(sibling)
    catalog = seal(dict(version=1, namespace="ns-A", plan_id="plan-A", event_id="event-A",
                        member_ids=[v["ledger_id"] for v in views], views=views,
                        complete=True), "catalog_sha256")
    records = []

    def add(kind, detail, *, ledger_id="ledger-A", ledger_seq=0, interval_id="attempt-A",
            operation_id="operation-A", row_sha256=journal.ZERO):
        records.append(dict(version=1, kind=kind, financial_authority=False,
            journal_id="journal-A", seq=len(records) + 1, prev_hash=journal.ZERO,
            session_id="session-A", boot_id="boot-A", ledger_id=ledger_id,
            genesis_hash=genesis, ledger_seq=ledger_seq,
            ledger_prefix_hash=prefix if ledger_seq else genesis,
            interval_id=interval_id, operation_id=operation_id,
            row_sha256=row_sha256, dependency_sha256=journal.ZERO,
            witness_sha256=journal.ZERO, clock_lower=100.0, clock_upper=101.0,
            detail=detail))

    add("ENROLLMENT", dict(namespace="ns-A", plan_id="plan-A", event_id="event-A"))
    if second:
        add("ENROLLMENT", dict(namespace="ns-A", plan_id="plan-A", event_id="event-A"),
            ledger_id="ledger-B")
    add("OPEN", dict(admission_id="admission-A"))
    add("PREPARE", dict(target_seq=1), row_sha256=digest(row_bytes))
    prepare_seq = len(records)
    add("SEAL", dict(prepare_seq=prepare_seq), ledger_seq=1,
        row_sha256=digest(row_bytes))
    add("CHECKPOINT", dict(catalog_sha256=catalog["catalog_sha256"]), ledger_seq=1)
    add("ARCHIVE", dict(archive_sha256="b" * 64), ledger_seq=1)
    return records, catalog


def wire(records):
    previous = journal.ZERO
    lines = []
    for index, record in enumerate(records, 1):
        item = dict(record, seq=index, prev_hash=previous)
        line = encoded(item)
        lines.append(line)
        previous = digest(line)
    return b"\n".join(lines) + b"\n"


def result(records, catalog, *, anchor=None, witnesses=None):
    if witnesses is None:
        witnesses = {}
    return journal.verify_interval(wire(records), anchor, catalog, witnesses)


def refuses(actual, code):
    check(type(actual) is journal.Refusal, "NOT_TYPED_REFUSAL")
    check(actual.code == code, f"EXPECTED_{code}_GOT_{actual.code}")


def test_valid_synthetic_history_has_no_commissioned_anchor_or_coverage():
    records, catalog = fixture()
    refuses(result(records, catalog), "ANCHOR_UNAVAILABLE")
    refuses(result(records, catalog, anchor={"commissioned": True}), "ANCHOR_UNAVAILABLE")


@pytest.mark.parametrize("field", ("clock_lower", "clock_upper"))
@pytest.mark.parametrize("value", (10**309, -(10**309)))
def test_large_signed_integer_clocks_are_typed_refusals(field, value):
    records, catalog = fixture()
    records[1][field] = value
    refuses(result(records, catalog), "CLOCK_INVALID")


def test_serialized_prepare_and_unique_row_consumption():
    records, catalog = fixture()
    other_open = deepcopy(records[1])
    other_open.update(interval_id="attempt-B", operation_id="operation-B",
                      detail={"admission_id": "admission-B"})
    other_prepare = deepcopy(records[2])
    other_prepare.update(interval_id="attempt-B", operation_id="operation-B")
    other_seal = deepcopy(records[3])
    other_seal.update(interval_id="attempt-B", operation_id="operation-B", detail={"prepare_seq": 5})
    # Two distinct attempts reserve the same frontier before either seals it.
    concurrent = records[:3] + [other_open, other_prepare, records[3], other_seal] + records[4:]
    refuses(result(concurrent, catalog), "INTERVAL_STATE")

    # The same row cannot be claimed after the first seal, even with fresh IDs.
    later_open = deepcopy(other_open)
    later_open.update(ledger_seq=1, ledger_prefix_hash=catalog["views"][0]["prefix_hash"])
    later_prepare = deepcopy(other_prepare)
    later_prepare.update(ledger_seq=1, ledger_prefix_hash=catalog["views"][0]["prefix_hash"])
    later = records[:4] + [later_open, later_prepare, other_seal] + records[4:]
    refuses(result(later, catalog), "INTERVAL_STATE")


@pytest.mark.parametrize("new_operation", (False, True))
@pytest.mark.parametrize("abort_prepared", (False, True))
def test_abort_keeps_interval_and_operation_terminal(new_operation, abort_prepared):
    records, catalog = fixture()
    abort = deepcopy(records[1])
    abort.update(kind="ABORT", detail={"reason": "synthetic"})
    reopened = deepcopy(records[1])
    if new_operation:
        reopened["operation_id"] = "operation-B"
    cut = 3 if abort_prepared else 2
    prior = records[:cut] + [abort, reopened]
    refuses(result(prior + records[cut:], catalog), "INTERVAL_STATE")


def test_abort_requires_fresh_admission_operation_and_interval():
    records, catalog = fixture()
    abort = deepcopy(records[1])
    abort.update(kind="ABORT", detail={"reason": "synthetic"})
    fresh_open = deepcopy(records[1])
    fresh_open.update(interval_id="attempt-B", operation_id="operation-B",
                      detail={"admission_id": "admission-B"})
    fresh_prepare = deepcopy(records[2])
    fresh_prepare.update(interval_id="attempt-B", operation_id="operation-B")
    fresh_seal = deepcopy(records[3])
    fresh_seal.update(interval_id="attempt-B", operation_id="operation-B",
                      detail={"prepare_seq": 5})
    retried = records[:2] + [abort, fresh_open, fresh_prepare, fresh_seal] + records[4:]
    refuses(result(retried, catalog), "ANCHOR_UNAVAILABLE")
    for field, value in (("interval_id", "attempt-A"),
                         ("operation_id", "operation-A")):
        reused = deepcopy(retried)
        reused[3][field] = value
        refuses(result(reused, catalog), "INTERVAL_STATE")
    reused_admission = deepcopy(retried)
    reused_admission[3]["detail"]["admission_id"] = "admission-A"
    refuses(result(reused_admission, catalog), "INTERVAL_STATE")


@pytest.mark.parametrize("position,generation", ((1, 1), (2, 2), (3, 2), (1, 0)))
def test_undefined_owner_transitions_refuse_at_every_lifecycle_boundary(position, generation):
    records, catalog = fixture()
    transition = deepcopy(records[0])
    transition.update(kind="TRANSITION", detail={"generation": generation,
                                                 "reason": "synthetic"})
    records.insert(position, transition)
    refuses(result(records, catalog), "TRANSITION_CONTRACT_UNDEFINED")


def test_lost_ack_replay_is_original_seal_only():
    records, catalog = fixture()
    original = wire(records)
    refuses(journal.verify_interval(original, None, catalog, {}), "ANCHOR_UNAVAILABLE")
    refuses(journal.verify_interval(original, None, catalog, {}), "ANCHOR_UNAVAILABLE")

    duplicate_seal = records[:4] + [deepcopy(records[3])] + records[4:]
    refuses(result(duplicate_seal, catalog), "INTERVAL_STATE")
    replay_open = deepcopy(records[1])
    replay_open.update(ledger_seq=1, ledger_prefix_hash=catalog["views"][0]["prefix_hash"])
    reopened = records[:4] + [replay_open] + records[4:]
    refuses(result(reopened, catalog), "INTERVAL_STATE")
    fresh_open = deepcopy(records[1])
    fresh_open.update(interval_id="attempt-B", operation_id="operation-B",
                      ledger_seq=1, ledger_prefix_hash=catalog["views"][0]["prefix_hash"],
                      detail={"admission_id": "admission-B"})
    refreshed = records[:4] + [fresh_open, deepcopy(records[2]), deepcopy(records[3])] + records[4:]
    for item in refreshed[5:7]:
        item.update(interval_id="attempt-B", operation_id="operation-B")
    refreshed[5].update(ledger_seq=1, ledger_prefix_hash=catalog["views"][0]["prefix_hash"])
    refreshed[6]["detail"] = {"prepare_seq": 6}
    refuses(result(refreshed, catalog), "INTERVAL_STATE")


@pytest.mark.parametrize("change,code", [
    (lambda r: r.update(version=True), "UNSUPPORTED_VERSION"),
    (lambda r: r.update(financial_authority=1), "FINANCIAL_AUTHORITY"),
    (lambda r: r.update(clock_lower=float("nan")), "NONFINITE_NUMBER"),
    (lambda r: r.update(unknown=True), "SCHEMA_KEYS"),
    (lambda r: r.update(genesis_hash="b" * 64, ledger_prefix_hash="b" * 64), "GENESIS_MISMATCH"),
    (lambda r: r.update(ledger_prefix_hash="b" * 64), "FRONTIER_MISMATCH"),
    (lambda r: r.update(ledger_id="ledger-Z"), "ENROLLMENT_MISSING"),
])
def test_strict_record_schema_and_binding(change, code):
    records, catalog = fixture()
    change(records[1] if code != "GENESIS_MISMATCH" else records[0])
    refuses(result(records, catalog), code)


def test_duplicate_key_noncanonical_oversize_and_partial_bytes():
    records, catalog = fixture()
    raw = wire(records)
    duplicate = raw.replace(b'"version":1', b'"version":1,"version":1', 1)
    refuses(journal.verify_interval(duplicate, None, catalog, {}), "DUPLICATE_KEY")
    noncanonical = raw.replace(b'"version":1', b'"version": 1', 1)
    refuses(journal.verify_interval(noncanonical, None, catalog, {}), "NONCANONICAL_ENVELOPE")
    refuses(journal.verify_interval(raw + b"x" * journal.MAX_JOURNAL_BYTES, None,
                                    catalog, {}), "JOURNAL_BOUND")
    refuses(journal.verify_interval(raw[:-1], None, catalog, {}), "JOURNAL_TRUNCATED")


@pytest.mark.parametrize("kind", sorted(journal.KINDS))
def test_each_kind_corruption_and_fork_refuse(kind):
    records, catalog = fixture()
    if kind not in {r["kind"] for r in records}:
        detail = {"TRANSITION": {"generation": 1, "reason": "synthetic"},
                  "ABORT": {"reason": "synthetic"}, "GAP": {"reason": "synthetic"},
                  "INVALIDATION": {"reason": "synthetic"}}[kind]
        extra = deepcopy(records[-1])
        extra.update(kind=kind, detail=detail)
        records.insert(-1, extra)
    index = next(i for i, r in enumerate(records) if r["kind"] == kind)
    raw = wire(records)
    lines = raw[:-1].split(b"\n")
    corrupted = lines[:]
    corrupted[index] = corrupted[index].replace(b'"kind":"', b'"kind":"X', 1)
    refuses(journal.verify_interval(b"\n".join(corrupted) + b"\n", None,
                                    catalog, {}), "SCHEMA_TYPE")
    forked = deepcopy(records)
    forked[index]["journal_id"] = "journal-fork"
    expected = "SESSION_MISMATCH" if index else "SESSION_MISMATCH"
    refuses(result(forked, catalog), expected)

    removed = lines[:index] + lines[index + 1:]
    expected = "ANCHOR_UNAVAILABLE" if index == len(lines) - 1 else "JOURNAL_SEQUENCE"
    refuses(journal.verify_interval(b"\n".join(removed) + b"\n", None,
                                    catalog, {}), expected)


def test_sequence_hash_and_catalog_adversaries():
    records, catalog = fixture()
    raw = wire(records)
    lines = raw[:-1].split(b"\n")
    refuses(journal.verify_interval(b"\n".join(lines[1:]) + b"\n", None,
                                    catalog, {}), "JOURNAL_SEQUENCE")
    swapped = lines[:]
    swapped[1], swapped[2] = swapped[2], swapped[1]
    refuses(journal.verify_interval(b"\n".join(swapped) + b"\n", None,
                                    catalog, {}), "JOURNAL_SEQUENCE")
    bad = deepcopy(catalog)
    bad["views"][0]["rows"][0]["row_hex"] = b"forged".hex()
    seal(bad["views"][0], "view_sha256")
    seal(bad, "catalog_sha256")
    refuses(result(records, bad), "ROW_HASH")
    records2, full = fixture(second=True)
    missing = deepcopy(full)
    missing["views"].pop()
    missing["member_ids"].pop()
    seal(missing, "catalog_sha256")
    refuses(result(records2, missing), "CATALOG_MEMBERS")
    sibling = deepcopy(full)
    sibling["views"][1]["complete"] = False
    seal(sibling["views"][1], "view_sha256")
    seal(sibling, "catalog_sha256")
    refuses(result(records2, sibling), "CATALOG_INCOMPLETE")


def test_frontier_checkpoint_and_cross_ledger_conflict():
    records, catalog = fixture()
    wrong = deepcopy(records)
    wrong[-1]["ledger_seq"] = 0
    wrong[-1]["ledger_prefix_hash"] = wrong[-1]["genesis_hash"]
    refuses(result(wrong, catalog), "FRONTIER_MISMATCH")
    wrong = deepcopy(records)
    wrong[-2]["detail"]["catalog_sha256"] = "c" * 64
    refuses(result(wrong, catalog), "CATALOG_CHECKPOINT")

    records, catalog = fixture(second=True)
    body = b'{"synthetic":"sibling-conflict"}'
    sibling = catalog["views"][1]
    prefix = digest(bytes.fromhex(sibling["genesis_hash"]) + (1).to_bytes(8, "big") + body)
    sibling["rows"].append(dict(seq=1, prefix_hash=prefix, kind="RAW_RECEIPT",
                                event_id="event-A", body_sha256=digest(body), row_hex=body.hex()))
    sibling["seq"] = 1
    sibling["prefix_hash"] = prefix
    seal(sibling, "view_sha256")
    seal(catalog, "catalog_sha256")
    for record in records:
        if record["kind"] == "CHECKPOINT":
            record["detail"]["catalog_sha256"] = catalog["catalog_sha256"]
    extra = deepcopy(records[-1])
    extra.update(kind="CHECKPOINT", ledger_id="ledger-B", ledger_seq=1,
                 ledger_prefix_hash=prefix,
                 detail={"catalog_sha256": catalog["catalog_sha256"]})
    records.append(extra)
    refuses(result(records, catalog), "CROSS_LEDGER_EVENT_HISTORY")


@pytest.mark.parametrize("kind,code", [
    ("GAP", "JOURNAL_GAP"), ("INVALIDATION", "INTERVAL_INVALIDATED")])
def test_explicit_negative_history(kind, code):
    records, catalog = fixture()
    extra = deepcopy(records[-1])
    extra.update(kind=kind, detail={"reason": "synthetic"})
    records.insert(-1, extra)
    refuses(result(records, catalog), code)


def test_witness_contract_and_gate_remain_closed():
    records, catalog = fixture()
    refuses(result(records, catalog, witnesses={"unreviewed": "claim"}),
            "WITNESS_CONTRACT_UNDEFINED")
    source = inspect.getsource(shadow_commission._require_protected_interval)
    check("raise EvidenceError('FORWARD_PROTECTED_INTERVAL_UNPROVEN')" in source,
          "PROTECTED_GATE_SOURCE_CHANGED")
    try:
        shadow_commission._require_protected_interval(None, None, ())
    except EvidenceError as exc:
        check(str(exc) == "FORWARD_PROTECTED_INTERVAL_UNPROVEN", "PROTECTED_GATE_CODE_CHANGED")
    else:
        raise AssertionError("PROTECTED_GATE_OPENED")
