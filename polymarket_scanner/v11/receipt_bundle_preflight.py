"""Offline, negative-only consistency screen for explicitly named saved bytes.

The manifest and three captures are caller-supplied. Matching hashes and fields
do not authenticate a provider, an ABI, a transaction, or an account effect.
This module is deliberately separate from the accepted inventory start path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time

from .structural_evidence import _open_regular_nofollow


_HASH = re.compile(r"0x[0-9a-f]{64}\Z")
_ADDRESS = re.compile(r"0x[0-9a-f]{40}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_WORD = re.compile(r"0x[0-9a-f]{64}\Z")
_DATA = re.compile(r"0x(?:[0-9a-f]{64}){1,8}\Z")
_UTC = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\Z")
_FILES = ("transaction", "receipt", "api_row")
_CAPTURE = {"captured_at_utc", "source", "request_id"}
_CLAIM = {"chain_id", "block_number", "block_hash", "transaction_hash",
          "transaction_index", "receipt_status", "log_index", "log_address",
          "log_topics", "log_data", "account", "token_id", "api_row_sha256",
          "account_topic_index", "token_word_index"}


class _BadInput(Exception):
    pass


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _BadInput("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _read(path: Path, limit: int, deadline: float):
    try:
        fd = _open_regular_nofollow(Path(path))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise _BadInput("NOT_REGULAR_FILE")
            if info.st_size > limit:
                raise _BadInput("BYTE_LIMIT")
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise _BadInput("BYTE_LIMIT")
        if time.monotonic() > deadline:
            raise _BadInput("TIME_LIMIT")
        value = json.loads(raw, object_pairs_hook=_unique)
        # Bound work after parsing as well; input bytes already bound parser work.
        stack = [(value, 0)]
        nodes = 0
        while stack:
            item, depth = stack.pop()
            nodes += 1
            if depth > 32 or nodes > 10_000:
                raise _BadInput("STRUCTURE_LIMIT")
            if isinstance(item, dict):
                stack.extend((v, depth + 1) for v in item.values())
            elif isinstance(item, list):
                stack.extend((v, depth + 1) for v in item)
            elif isinstance(item, float) and not math.isfinite(item):
                raise _BadInput("INVALID_NUMBER")
            if time.monotonic() > deadline:
                raise _BadInput("TIME_LIMIT")
        return value, hashlib.sha256(raw).hexdigest()
    except _BadInput:
        raise
    except (OSError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise _BadInput("INVALID_LOCAL_JSON") from exc


def _shape(value, keys):
    return type(value) is dict and set(value) == keys


def _uint(value):
    return type(value) is int and 0 <= value <= 2**64 - 1


def _hexnum(value):
    if type(value) is not str or not re.fullmatch(r"0x(?:0|[1-9a-f][0-9a-f]{0,15})", value):
        return None
    return int(value, 16)


def _capture(value):
    return (_shape(value, _CAPTURE)
            and type(value["captured_at_utc"]) is str and bool(_UTC.fullmatch(value["captured_at_utc"]))
            and all(type(value[k]) is str and 0 < len(value[k]) <= 128
                    for k in ("source", "request_id")))


def _match(fields, claim, names, findings, code):
    if any(fields.get(name) != claim.get(name) for name in names):
        findings.add(code)


def preflight(manifest: Path, transaction: Path, receipt: Path, api_row: Path) -> tuple[str, ...]:
    """Return sorted missing/inconsistent-proof codes; never return a verdict."""
    findings = {"SOURCE_ATTESTATION_MISSING", "ABI_ATTESTATION_MISSING"}
    deadline = time.monotonic() + 2.0
    try:
        m, _ = _read(manifest, 64_000, deadline)
    except _BadInput as exc:
        return tuple(sorted(findings | {"MANIFEST_" + str(exc)}))
    if not (_shape(m, {"version", "files", "claim"})
            and m["version"] == "v11_receipt_bundle_preflight_v1"
            and _shape(m["files"], set(_FILES)) and _shape(m["claim"], _CLAIM)):
        return tuple(sorted(findings | {"MANIFEST_SCHEMA_INCONSISTENT"}))
    claim = m["claim"]
    if not (all(_uint(claim[k]) for k in ("chain_id", "block_number", "transaction_index", "log_index"))
            and claim["chain_id"] > 0
            and all(type(claim[k]) is str and _HASH.fullmatch(claim[k])
                    for k in ("block_hash", "transaction_hash"))
            and type(claim["log_address"]) is str and _ADDRESS.fullmatch(claim["log_address"])
            and type(claim["account"]) is str and _ADDRESS.fullmatch(claim["account"])
            and type(claim["log_topics"]) is list and 1 <= len(claim["log_topics"]) <= 4
            and all(type(t) is str and _WORD.fullmatch(t) for t in claim["log_topics"])
            and type(claim["log_data"]) is str and _DATA.fullmatch(claim["log_data"])
            and type(claim["token_id"]) is str and re.fullmatch(r"(?:0|[1-9][0-9]{0,77})", claim["token_id"])
            and type(claim["api_row_sha256"]) is str and _SHA.fullmatch(claim["api_row_sha256"])
            and type(claim["account_topic_index"]) is int
            and 1 <= claim["account_topic_index"] < len(claim["log_topics"])
            and type(claim["token_word_index"]) is int
            and 0 <= claim["token_word_index"] < (len(claim["log_data"]) - 2) // 64):
        findings.add("CLAIM_IDENTITY_MISSING_OR_INCONSISTENT")
        return tuple(sorted(findings))
    if claim["receipt_status"] != "0x1":
        findings.add("RECEIPT_SUCCESS_MISSING")
    values = {}
    for name, path in zip(_FILES, (transaction, receipt, api_row)):
        spec = m["files"][name]
        if not (_shape(spec, {"sha256", "capture"}) and type(spec["sha256"]) is str
                and _SHA.fullmatch(spec["sha256"]) and _capture(spec["capture"])):
            findings.add(name.upper() + "_PROVENANCE_MISSING_OR_INCONSISTENT")
            continue
        try:
            value, digest = _read(path, 256_000, deadline)
        except _BadInput as exc:
            findings.add(name.upper() + "_" + str(exc))
            continue
        if digest != spec["sha256"]:
            findings.add(name.upper() + "_RAW_HASH_MISMATCH")
        if not (type(value) is dict and value.get("capture") == spec["capture"]):
            findings.add(name.upper() + "_CAPTURE_MISMATCH")
        if name != "api_row" and (type(value) is not dict
                or type(value.get("response")) is not dict
                or value["response"].get("jsonrpc") != "2.0"
                or value["response"].get("id") != spec["capture"]["request_id"]):
            findings.add(name.upper() + "_REQUEST_ID_INCONSISTENT")
        values[name] = value
    tx, rc, api = (values.get(name) for name in _FILES)
    if tx is not None:
        body = tx.get("response") if type(tx) is dict else None
        body = body.get("result") if type(body) is dict and "error" not in body else None
        if type(body) is not dict:
            findings.add("TRANSACTION_RESPONSE_MISSING")
        else:
            _match(body, {"hash": claim["transaction_hash"], "blockHash": claim["block_hash"],
                          "from": claim["account"]}, ("hash", "blockHash", "from"),
                   findings, "TRANSACTION_IDENTITY_INCONSISTENT")
            if any(_hexnum(body.get(k)) != claim[v] for k, v in
                   (("chainId", "chain_id"), ("blockNumber", "block_number"),
                    ("transactionIndex", "transaction_index"))):
                findings.add("TRANSACTION_IDENTITY_INCONSISTENT")
    if rc is not None:
        body = rc.get("response") if type(rc) is dict else None
        body = body.get("result") if type(body) is dict and "error" not in body else None
        if type(body) is not dict:
            findings.add("RECEIPT_RESPONSE_MISSING")
        else:
            _match(body, {"transactionHash": claim["transaction_hash"], "blockHash": claim["block_hash"]},
                   ("transactionHash", "blockHash"), findings, "RECEIPT_IDENTITY_INCONSISTENT")
            if any(_hexnum(body.get(k)) != claim[v] for k, v in
                   (("blockNumber", "block_number"), ("transactionIndex", "transaction_index"))):
                findings.add("RECEIPT_IDENTITY_INCONSISTENT")
            if body.get("status") != "0x1":
                findings.add("RECEIPT_SUCCESS_MISSING")
            logs = body.get("logs")
            if type(logs) is not list or len(logs) > 64:
                findings.add("LOG_SET_MISSING_OR_BOUNDED")
            else:
                indexes = [_hexnum(log.get("logIndex")) if type(log) is dict else None
                           for log in logs]
                if None in indexes or len(set(indexes)) != len(indexes):
                    findings.add("LOG_SET_DUPLICATE_OR_INVALID_INDEX")
                matching = [log for log in logs if type(log) is dict
                            and _hexnum(log.get("logIndex")) == claim["log_index"]]
                if len(matching) != 1:
                    findings.add("LOG_IDENTITY_MISSING_OR_DUPLICATE")
                else:
                    log = matching[0]
                    _match(log, {"address": claim["log_address"], "topics": claim["log_topics"],
                                 "data": claim["log_data"], "transactionHash": claim["transaction_hash"],
                                 "blockHash": claim["block_hash"], "removed": False},
                           ("address", "topics", "data", "transactionHash", "blockHash", "removed"),
                           findings, "LOG_IDENTITY_INCONSISTENT")
                    if (_hexnum(log.get("blockNumber")) != claim["block_number"]
                            or _hexnum(log.get("transactionIndex")) != claim["transaction_index"]):
                        findings.add("LOG_IDENTITY_INCONSISTENT")
    if api is not None:
        row = api.get("row") if type(api) is dict else None
        if (not _shape(api, {"capture", "evidence_class", "row"})
                or api["evidence_class"] != "API_OBSERVED" or type(row) is not dict
                or row.get("evidence_class", "API_OBSERVED") != "API_OBSERVED"):
            findings.add("API_OBSERVED_ROW_MISSING")
        else:
            canonical = json.dumps(row, sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=True, allow_nan=False).encode("ascii")
            if hashlib.sha256(canonical).hexdigest() != claim["api_row_sha256"]:
                findings.add("API_ROW_HASH_MISMATCH")
            _match(row, {"transaction_hash": claim["transaction_hash"],
                         "account": claim["account"], "token_id": claim["token_id"]},
                   ("transaction_hash", "account", "token_id"), findings,
                   "API_ROW_IDENTITY_INCONSISTENT")
    topic = claim["log_topics"][claim["account_topic_index"]]
    word = claim["log_data"][2 + 64 * claim["token_word_index"]:2 + 64 * (claim["token_word_index"] + 1)]
    if topic != "0x" + "0" * 24 + claim["account"][2:] or int(word, 16) != int(claim["token_id"]):
        findings.add("LOG_ACCOUNT_TOKEN_INCONSISTENT")
    return tuple(sorted(findings))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest",) + _FILES:
        parser.add_argument("--" + name.replace("_", "-"), required=True, type=Path)
    args = parser.parse_args(argv)
    print(json.dumps({"diagnostics": preflight(args.manifest, args.transaction,
                                                args.receipt, args.api_row)},
                     sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
