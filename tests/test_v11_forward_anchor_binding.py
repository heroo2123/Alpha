"""Synthetic refusals only; these fixtures have no anchor authority."""

import builtins
import copy
import hashlib
import json
import os
import socket
import sqlite3
import subprocess

import pytest

from polymarket_scanner.v11.forward_anchor_binding import (
    MAX_ANCHOR_BYTES, MAX_CHILDREN, MAX_DEPENDENCY_BYTES, MAX_LEDGER_SEQ,
    MAX_LEDGERS, MAX_REQUEST_BYTES, MAX_SNAPSHOT_BYTES, Refusal,
    check_anchor_binding,
)


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _digest(char):
    return char * 64


def _fixture():
    ledger = dict(ledger_id="ledger.1", genesis_hash=_digest("a"), seq=1,
                  prefix_hash=_digest("b"), view_sha256=_digest("c"),
                  archive_sha256=_sha(b""))
    ledgers = [ledger]
    membership = _sha(_bytes([{"ledger_id": ledger["ledger_id"],
                               "genesis_hash": ledger["genesis_hash"]}]))
    frontier = dict(boot_id="boot.1", session_id="session.1",
                    custody_generation=1, journal_id="journal.1", journal_seq=1,
                    journal_head=_digest("d"), catalog_sha256=_digest("e"),
                    membership_sha256=membership, ledgers=ledgers)
    target = dict(request_id="request.1", namespace="ns", plan_id="plan",
                  scope_key="scope", event_id="event", interval_id="interval",
                  operation_id="operation", admission_id="admission",
                  capture_id="capture", child_ids=["child.1", "child.2"])
    snapshot = dict(version=1, **target, **copy.deepcopy(frontier),
                    dependency_hex=b"exact dependency bytes".hex(),
                    witness_history_sha256=_digest("f"))
    anchor = dict(version=1, anchor_id="anchor.1", issuer_id="issuer.1",
                  custodian_id="custodian.1", executable_sha256=_digest("1"),
                  policy_sha256=_digest("2"), enrollment_id="enrollment.1",
                  authentication_format="external-v1",
                  verification_root_id="root.1", request_sha256=_digest("0"),
                  snapshot_sha256=_sha(_bytes(snapshot)), **copy.deepcopy(frontier))
    request = dict(version=1, **target,
                   verifier_executable_sha256=_digest("3"),
                   verifier_policy_sha256=_digest("4"),
                   snapshot_sha256=_sha(_bytes(snapshot)))
    for name in ("anchor_id", "issuer_id", "custodian_id", "executable_sha256",
                 "policy_sha256", "enrollment_id", "authentication_format",
                 "verification_root_id", "boot_id", "session_id",
                 "custody_generation", "journal_id", "journal_seq", "journal_head",
                 "catalog_sha256", "membership_sha256"):
        request["expected_" + name] = anchor[name]
    anchor["request_sha256"] = _sha(_bytes(request))
    return anchor, request, snapshot


def _result(anchor, request, snapshot):
    return check_anchor_binding(_bytes(anchor), _bytes(request), _bytes(snapshot))


def _code(actual, expected):
    if type(actual) is not Refusal or actual.code != expected:
        raise AssertionError(f"expected {expected}, got {actual!r}")


def _reseal(anchor, request, snapshot):
    anchor["request_sha256"] = _sha(_bytes(request))
    anchor["snapshot_sha256"] = _sha(_bytes(snapshot))
    request["snapshot_sha256"] = anchor["snapshot_sha256"]
    anchor["request_sha256"] = _sha(_bytes(request))


def test_matching_synthetic_input_is_still_unavailable_and_deterministic():
    anchor, request, snapshot = _fixture()
    raw = (_bytes(anchor), _bytes(request), _bytes(snapshot))
    for _ in range(3):
        _code(check_anchor_binding(*raw), "ANCHOR_UNAVAILABLE")


@pytest.mark.parametrize("position,code", [(0, "ANCHOR_BOUND"),
                                           (1, "REQUEST_BOUND"),
                                           (2, "SNAPSHOT_BOUND")])
def test_type_empty_and_byte_caps(position, code):
    args = list(map(_bytes, _fixture()))
    for replacement in (None, b"", bytearray(args[position]),
                        b"x" * ((MAX_ANCHOR_BYTES, MAX_REQUEST_BYTES,
                                  MAX_SNAPSHOT_BYTES)[position] + 1)):
        changed = args.copy()
        changed[position] = replacement
        _code(check_anchor_binding(*changed), code)


@pytest.mark.parametrize("replacement,code", [
    (b'{"version":1,"version":1}', "DUPLICATE_KEY"),
    (b'{', "MALFORMED_JSON"),
    (b'\xff', "MALFORMED_JSON"),
    (b' {"version":1}', "SCHEMA_KEYS"),
])
def test_malformed_anchor(replacement, code):
    args = list(map(_bytes, _fixture()))
    args[0] = replacement
    _code(check_anchor_binding(*args), code)


def test_noncanonical_unknown_missing_version_and_flags():
    anchor, request, snapshot = _fixture()
    raw = _bytes(anchor)
    _code(check_anchor_binding(raw + b" ", _bytes(request), _bytes(snapshot)),
          "NONCANONICAL_ENVELOPE")
    for edit, code in (({"commissioned": True}, "SCHEMA_KEYS"),
                       ({"caller_key": "self"}, "SCHEMA_KEYS"),
                       ({"version": 2}, "UNSUPPORTED_VERSION"),
                       ({"version": True}, "UNSUPPORTED_VERSION")):
        changed = dict(anchor, **edit)
        _code(_result(changed, request, snapshot), code)
    changed = dict(anchor)
    del changed["issuer_id"]
    _code(_result(changed, request, snapshot), "SCHEMA_KEYS")
    for index in (1, 2):
        args = [anchor, request, snapshot]
        args[index] = dict(args[index], fixture_claim=True)
        _code(_result(*args), "SCHEMA_KEYS")


@pytest.mark.parametrize("name", [
    "anchor_id", "issuer_id", "custodian_id", "executable_sha256",
    "policy_sha256", "enrollment_id", "verification_root_id", "boot_id",
    "session_id", "custody_generation", "journal_id", "journal_seq",
    "journal_head", "catalog_sha256", "membership_sha256",
])
def test_each_expected_anchor_field_is_bound(name):
    anchor, request, snapshot = _fixture()
    if name == "custody_generation" or name == "journal_seq":
        anchor[name] += 1
    elif name.endswith("sha256") or name == "journal_head":
        anchor[name] = _digest("9")
    else:
        anchor[name] += ".changed"
    if name == "membership_sha256":
        _code(_result(anchor, request, snapshot), "MEMBERSHIP_MISMATCH")
    else:
        _code(_result(anchor, request, snapshot), "EXPECTED_FRONTIER_MISMATCH")


def test_authentication_format_and_bad_generation():
    anchor, request, snapshot = _fixture()
    anchor["authentication_format"] = "self-signed"
    _code(_result(anchor, request, snapshot), "AUTH_FORMAT_UNSUPPORTED")
    anchor, request, snapshot = _fixture()
    anchor["custody_generation"] = 0
    _code(_result(anchor, request, snapshot), "GENERATION_INVALID")
    anchor, request, snapshot = _fixture()
    request["expected_custody_generation"] = 2**53
    _code(_result(anchor, request, snapshot), "GENERATION_INVALID")


def test_crossed_request_and_snapshot_replay():
    anchor, request, snapshot = _fixture()
    request["request_id"] = "request.other"
    _code(_result(anchor, request, snapshot), "REQUEST_BINDING")
    _reseal(anchor, request, snapshot)
    _code(_result(anchor, request, snapshot), "TARGET_MISMATCH")
    anchor, request, snapshot = _fixture()
    snapshot["dependency_hex"] = b"changed".hex()
    _code(_result(anchor, request, snapshot), "SNAPSHOT_BINDING")
    _reseal(anchor, request, snapshot)
    _code(_result(anchor, request, snapshot), "ANCHOR_UNAVAILABLE")


@pytest.mark.parametrize("name", ["boot_id", "session_id", "custody_generation",
                                  "journal_id", "journal_seq", "journal_head",
                                  "catalog_sha256", "membership_sha256", "ledgers"])
def test_snapshot_frontier_crossing(name):
    anchor, request, snapshot = _fixture()
    if name in ("custody_generation", "journal_seq"):
        snapshot[name] += 1
    elif name == "ledgers":
        snapshot[name] = [dict(snapshot[name][0], seq=2)]
    elif name == "membership_sha256":
        snapshot[name] = _digest("9")
        _code(_result(anchor, request, snapshot), "MEMBERSHIP_MISMATCH")
        return
    elif name.endswith("sha256") or name == "journal_head":
        snapshot[name] = _digest("9")
    else:
        snapshot[name] += ".other"
    _reseal(anchor, request, snapshot)
    _code(_result(anchor, request, snapshot), "SNAPSHOT_FRONTIER_MISMATCH")


@pytest.mark.parametrize("name", ["request_id", "namespace", "plan_id", "scope_key",
                                  "event_id", "interval_id", "operation_id",
                                  "admission_id", "capture_id", "child_ids"])
def test_each_target_field_is_bound(name):
    anchor, request, snapshot = _fixture()
    snapshot[name] = ["child.other"] if name == "child_ids" else "other"
    _reseal(anchor, request, snapshot)
    _code(_result(anchor, request, snapshot), "TARGET_MISMATCH")


def test_count_encoding_and_frontier_caps():
    anchor, request, snapshot = _fixture()
    request["child_ids"] = [f"child.{i:02}" for i in range(MAX_CHILDREN + 1)]
    _code(_result(anchor, request, snapshot), "CHILD_BOUND")
    anchor, request, snapshot = _fixture()
    request["child_ids"] = ["z", "a"]
    _code(_result(anchor, request, snapshot), "CHILD_ORDER")
    anchor, request, snapshot = _fixture()
    snapshot["dependency_hex"] = "aa" * (MAX_DEPENDENCY_BYTES + 1)
    _code(_result(anchor, request, snapshot), "DEPENDENCY_BOUND")
    anchor, request, snapshot = _fixture()
    snapshot["dependency_hex"] = "AA"
    _code(_result(anchor, request, snapshot), "DEPENDENCY_ENCODING")
    anchor, request, snapshot = _fixture()
    anchor["journal_seq"] = 129
    _code(_result(anchor, request, snapshot), "FRONTIER_INVALID")
    anchor, request, snapshot = _fixture()
    anchor["ledgers"][0]["seq"] = MAX_LEDGER_SEQ + 1
    _code(_result(anchor, request, snapshot), "FRONTIER_INVALID")
    anchor, request, snapshot = _fixture()
    anchor["ledgers"][0]["seq"] = 0
    _code(_result(anchor, request, snapshot), "FRONTIER_INVALID")
    anchor, request, snapshot = _fixture()
    anchor["ledgers"] = [dict(anchor["ledgers"][0], ledger_id=f"ledger.{i:02}")
                         for i in range(MAX_LEDGERS + 1)]
    _code(_result(anchor, request, snapshot), "LEDGER_BOUND")
    anchor, request, snapshot = _fixture()
    anchor["ledgers"] = anchor["ledgers"] * 2
    _code(_result(anchor, request, snapshot), "CATALOG_MEMBERS")


def test_maximum_counts_and_dependency_bytes_are_bounded_synthetic_inputs():
    anchor, request, snapshot = _fixture()
    request["child_ids"] = [f"child.{i:02}" for i in range(MAX_CHILDREN)]
    snapshot["child_ids"] = request["child_ids"].copy()
    snapshot["dependency_hex"] = "ab" * MAX_DEPENDENCY_BYTES
    ledgers = [dict(anchor["ledgers"][0], ledger_id=f"ledger.{i:02}")
               for i in range(MAX_LEDGERS)]
    members = [{"ledger_id": item["ledger_id"], "genesis_hash": item["genesis_hash"]}
               for item in ledgers]
    membership = _sha(_bytes(members))
    anchor["ledgers"] = copy.deepcopy(ledgers)
    snapshot["ledgers"] = copy.deepcopy(ledgers)
    anchor["membership_sha256"] = membership
    snapshot["membership_sha256"] = membership
    request["expected_membership_sha256"] = membership
    _reseal(anchor, request, snapshot)
    _code(_result(anchor, request, snapshot), "ANCHOR_UNAVAILABLE")


def test_nested_duplicate_and_alternate_encoding_refuse():
    anchor, request, snapshot = _fixture()
    raw = _bytes(anchor)
    raw = raw.replace(b'"seq":1', b'"seq":1,"seq":1', 1)
    _code(check_anchor_binding(raw, _bytes(request), _bytes(snapshot)),
          "DUPLICATE_KEY")
    raw = _bytes(anchor).replace(b'"anchor_id":"anchor.1"',
                                 b'"anchor_id":"anchor.\\u0031"')
    _code(check_anchor_binding(raw, _bytes(request), _bytes(snapshot)),
          "NONCANONICAL_ENVELOPE")
    request["version"] = 2
    _code(_result(anchor, request, snapshot), "UNSUPPORTED_VERSION")
    request["version"] = 1
    snapshot["version"] = 2
    _code(_result(anchor, request, snapshot), "UNSUPPORTED_VERSION")


def test_no_socket_database_process_or_account_side_effect(monkeypatch):
    raw = tuple(map(_bytes, _fixture()))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("unexpected external effect")

    with monkeypatch.context() as patch:
        patch.setattr(socket, "socket", forbidden)
        patch.setattr(socket, "create_connection", forbidden)
        patch.setattr(sqlite3, "connect", forbidden)
        patch.setattr(subprocess, "Popen", forbidden)
        patch.setattr(os, "system", forbidden)
        patch.setattr(os, "open", forbidden)
        patch.setattr(builtins, "open", forbidden)
        _code(check_anchor_binding(*raw), "ANCHOR_UNAVAILABLE")
