"""Fabricated local bytes only; no provider or financial effect is exercised."""
import copy
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from polymarket_scanner.v11 import receipt_bundle_preflight as pre


H = "0x" + "a" * 64
B = "0x" + "b" * 64
A = "0x" + "c" * 40
L = "0x" + "d" * 40
TOPIC = "0x" + "e" * 64
ACCOUNT_TOPIC = "0x" + "0" * 24 + A[2:]
DATA = "0x" + format(7, "064x")


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def bundle(tmp_path):
    capture = {"captured_at_utc": "2026-10-07T00:00:00Z",
               "source": "saved-local-response", "request_id": "request-1"}
    row = {"transaction_hash": H, "account": A, "token_id": "7"}
    log = {"logIndex": "0x0", "address": L, "topics": [TOPIC, ACCOUNT_TOPIC],
           "data": DATA, "transactionHash": H, "blockHash": B, "blockNumber": "0xa",
           "transactionIndex": "0x2", "removed": False}
    files = {
        "transaction": {"capture": capture, "response": {"jsonrpc": "2.0", "id": "request-1", "result": {
            "hash": H, "blockHash": B, "chainId": "0x89", "blockNumber": "0xa",
            "transactionIndex": "0x2", "from": A}}},
        "receipt": {"capture": capture, "response": {"jsonrpc": "2.0", "id": "request-1", "result": {
            "transactionHash": H, "blockHash": B, "blockNumber": "0xa",
            "transactionIndex": "0x2", "status": "0x1", "logs": [log]}}},
        "api_row": {"capture": capture, "evidence_class": "API_OBSERVED", "row": row},
    }
    manifest = {"version": "v11_receipt_bundle_preflight_v1",
                "files": {name: {"sha256": hashlib.sha256(_bytes(value)).hexdigest(),
                                 "capture": capture} for name, value in files.items()},
                "claim": {"chain_id": 137, "block_number": 10, "block_hash": B,
                          "transaction_hash": H, "transaction_index": 2,
                          "receipt_status": "0x1", "log_index": 0, "log_address": L,
                          "log_topics": [TOPIC, ACCOUNT_TOPIC], "log_data": DATA,
                          "account": A, "token_id": "7",
                          "account_topic_index": 1, "token_word_index": 0,
                          "api_row_sha256": hashlib.sha256(_bytes(row)).hexdigest()}}
    return manifest, files


def write_bundle(tmp_path, manifest, files, *, update_hashes=True):
    paths = {name: tmp_path / (name + ".json") for name in files}
    for name, value in files.items():
        raw = _bytes(value)
        paths[name].write_bytes(raw)
        if update_hashes:
            manifest["files"][name]["sha256"] = hashlib.sha256(raw).hexdigest()
    paths["manifest"] = tmp_path / "manifest.json"
    paths["manifest"].write_bytes(_bytes(manifest))
    return paths


def run(paths):
    return pre.preflight(*(paths[name] for name in ("manifest", "transaction", "receipt", "api_row")))


def test_consistent_bytes_still_unverified_deterministic_and_read_only(tmp_path, monkeypatch, capsys):
    manifest, files = bundle(tmp_path)
    paths = write_bundle(tmp_path, manifest, files)
    before = {p: p.read_bytes() for p in paths.values()}
    def no_socket(*args, **kwargs):
        raise AssertionError("SOCKET_USED")
    monkeypatch.setattr(socket, "socket", no_socket)
    monkeypatch.setattr(socket, "create_connection", no_socket)
    expected = ("ABI_ATTESTATION_MISSING", "SOURCE_ATTESTATION_MISSING")
    assert run(paths) == run(paths) == expected
    args = []
    for name in ("manifest", "transaction", "receipt", "api_row"):
        args += ["--" + name.replace("_", "-"), str(paths[name])]
    assert pre.main(args) == 0
    assert json.loads(capsys.readouterr().out) == {"diagnostics": list(expected)}
    assert {p: p.read_bytes() for p in paths.values()} == before
    assert set(tmp_path.iterdir()) == set(paths.values())


@pytest.mark.parametrize("change,code", [
    (lambda m, f: f["receipt"]["response"]["result"].update(status="0x0"), "RECEIPT_SUCCESS_MISSING"),
    (lambda m, f: f["receipt"]["response"]["result"].update(status=None), "RECEIPT_SUCCESS_MISSING"),
    (lambda m, f: f["transaction"]["response"]["result"].update(hash=B), "TRANSACTION_IDENTITY_INCONSISTENT"),
    (lambda m, f: f["receipt"]["response"]["result"].update(transactionHash=B), "RECEIPT_IDENTITY_INCONSISTENT"),
    (lambda m, f: f["api_row"]["row"].update(transaction_hash=B), "API_ROW_IDENTITY_INCONSISTENT"),
    (lambda m, f: f["api_row"]["row"].update(token_id="8"), "API_ROW_IDENTITY_INCONSISTENT"),
    (lambda m, f: f["receipt"]["response"]["result"]["logs"][0].update(data="0x" + format(8, "064x")), "LOG_IDENTITY_INCONSISTENT"),
    (lambda m, f: f["receipt"]["response"]["result"]["logs"].append(copy.deepcopy(f["receipt"]["response"]["result"]["logs"][0])), "LOG_IDENTITY_MISSING_OR_DUPLICATE"),
    (lambda m, f: m["claim"].update(token_id="8"), "LOG_ACCOUNT_TOKEN_INCONSISTENT"),
    (lambda m, f: m["claim"].update(receipt_status=None), "RECEIPT_SUCCESS_MISSING"),
    (lambda m, f: f["api_row"].update(evidence_class="SYNTHETIC_PROOF"), "API_OBSERVED_ROW_MISSING"),
    (lambda m, f: f["api_row"]["row"].update(evidence_class="CHAIN_RECEIPT"), "API_OBSERVED_ROW_MISSING"),
    (lambda m, f: f["receipt"].update(response={"error": {"code": -32000}}), "RECEIPT_RESPONSE_MISSING"),
    (lambda m, f: f["receipt"]["response"].update(id="other"), "RECEIPT_REQUEST_ID_INCONSISTENT"),
    (lambda m, f: f["receipt"]["response"]["result"]["logs"][0].update(removed=True), "LOG_IDENTITY_INCONSISTENT"),
])
def test_inconsistent_identity_and_status(tmp_path, change, code):
    manifest, files = bundle(tmp_path)
    change(manifest, files)
    paths = write_bundle(tmp_path, manifest, files)
    assert code in run(paths)
    assert "SOURCE_ATTESTATION_MISSING" in run(paths)


def test_hash_capture_and_row_binding(tmp_path):
    manifest, files = bundle(tmp_path)
    manifest["files"]["receipt"]["capture"] = dict(manifest["files"]["receipt"]["capture"], request_id="other")
    manifest["claim"]["api_row_sha256"] = "0" * 64
    paths = write_bundle(tmp_path, manifest, files)
    paths["transaction"].write_bytes(paths["transaction"].read_bytes() + b" ")
    assert {"TRANSACTION_RAW_HASH_MISMATCH", "RECEIPT_CAPTURE_MISMATCH",
            "API_ROW_HASH_MISMATCH"} <= set(run(paths))


@pytest.mark.parametrize("name,raw,code", [
    ("manifest", b'{"version":1,"version":2}', "MANIFEST_DUPLICATE_JSON_KEY"),
    ("receipt", b'{"capture":{},"capture":{}}', "RECEIPT_DUPLICATE_JSON_KEY"),
    ("api_row", b"{" * 20, "API_ROW_INVALID_LOCAL_JSON"),
])
def test_duplicate_and_malformed_json(tmp_path, name, raw, code):
    manifest, files = bundle(tmp_path)
    paths = write_bundle(tmp_path, manifest, files)
    paths[name].write_bytes(raw)
    assert code in run(paths)


def test_bounds_symlink_and_optimized_replay(tmp_path):
    manifest, files = bundle(tmp_path)
    files["receipt"]["response"]["result"]["logs"] *= 65
    paths = write_bundle(tmp_path, manifest, files)
    assert "LOG_SET_MISSING_OR_BOUNDED" in run(paths)
    paths["receipt"].write_bytes(b" " * 256_001)
    assert "RECEIPT_BYTE_LIMIT" in run(paths)
    paths["receipt"].unlink()
    paths["receipt"].symlink_to(paths["transaction"])
    assert "RECEIPT_INVALID_LOCAL_JSON" in run(paths)
    paths["receipt"].unlink()
    manifest, files = bundle(tmp_path)
    paths = write_bundle(tmp_path, manifest, files)
    cmd = [sys.executable, "-O", "-m", "polymarket_scanner.v11.receipt_bundle_preflight"]
    for name in ("manifest", "transaction", "receipt", "api_row"):
        cmd += ["--" + name.replace("_", "-"), str(paths[name])]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["diagnostics"] == list(run(paths))
    assert not result.stderr
