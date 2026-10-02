"""Offline, nonfinancial InventoryTransform SHADOW observer.

The input is an explicitly named local JSON fixture with ``source`` and
``payload`` fields. This module has no runtime registration or provider path.
Its artifact is a replayable diagnostic, never an inventory or receipt ledger.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat

from .neg_risk_contract import ContractRoute, NegRiskTopologyProof, simulate_conversion
from .structural_evidence import (
    Activity, Coverage, EvidenceClass, Limits, Source, load_offline_json,
    normalize_activity, reconcile_event_window,
)


VERSION = "v11_inventory_transform_offline_shadow_v1"
MAX_ARTIFACT_BYTES = 4_000_000


class ShadowInputError(ValueError):
    pass


def _source(value: object) -> Source:
    fields = set(Source.__dataclass_fields__)
    if (not isinstance(value, dict) or not fields - {"page_number"} <= set(value)
            or not set(value) <= fields):
        raise ShadowInputError("SOURCE_SCHEMA")
    parameters = value["parameters"]
    if (not isinstance(parameters, list) or len(parameters) > 64
            or any(not isinstance(p, list) or len(p) != 2 for p in parameters)):
        raise ShadowInputError("SOURCE_PARAMETERS_BOUND")
    captured = value["captured_at_utc"]
    if captured is not None and (type(captured) is not str or len(captured) > 256):
        raise ShadowInputError("SOURCE_CAPTURE_TIME_INVALID")
    try:
        return Source(**{**value, "parameters": tuple(tuple(p) for p in parameters)})
    except (TypeError, ValueError) as exc:
        raise ShadowInputError("SOURCE_INVALID") from exc


def _jsonable(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (Coverage, EvidenceClass)):
        return value.value
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    return value


def _encoded(value: object) -> bytes:
    try:
        raw = (json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ShadowInputError("ARTIFACT_INVALID") from exc
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ShadowInputError("ARTIFACT_BYTE_LIMIT")
    return raw


def _synthetic(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"route", "topology", "selected_mask", "quantity", "available_no_units"}:
        raise ShadowInputError("SYNTHETIC_SCHEMA")
    route_value, topology_value = value["route"], value["topology"]
    route_fields = set(ContractRoute.__dataclass_fields__)
    topology_fields = set(NegRiskTopologyProof.__dataclass_fields__)
    if (not isinstance(route_value, dict)
            or not route_fields - {"verification"} <= set(route_value) or not set(route_value) <= route_fields
            or not isinstance(topology_value, dict)
            or not topology_fields - {"universe_status"} <= set(topology_value)
            or not set(topology_value) <= topology_fields):
        raise ShadowInputError("SYNTHETIC_SCHEMA")
    if any(not isinstance(topology_value[key], list) or len(topology_value[key]) > 256
           for key in ("ordered_condition_ids", "yes_token_ids", "no_token_ids")):
        raise ShadowInputError("SYNTHETIC_TOPOLOGY_BOUND")
    available = value["available_no_units"]
    if not isinstance(available, list) or len(available) > 256:
        raise ShadowInputError("SYNTHETIC_INVENTORY_BOUND")
    try:
        route = ContractRoute(**route_value)
        topology = NegRiskTopologyProof(**{**topology_value,
            "ordered_condition_ids": tuple(topology_value["ordered_condition_ids"]),
            "yes_token_ids": tuple(topology_value["yes_token_ids"]),
            "no_token_ids": tuple(topology_value["no_token_ids"])})
        result = simulate_conversion(route, topology, value["selected_mask"],
                                     value["quantity"], tuple(available))
    except (TypeError, ValueError) as exc:
        raise ShadowInputError("SYNTHETIC_INVALID") from exc
    return {"evidence_class": EvidenceClass.SYNTHETIC_PROOF.value,
            "coverage": None, "transaction_level_proof": False,
            "result": _jsonable(asdict(result))}


def observe_file(path: Path, event_slug: str, limits: Limits = Limits()) -> dict:
    """Normalize a saved API page and produce one deterministic SHADOW report."""
    try:
        return _observe_file(path, event_slug, limits)
    except RecursionError as exc:
        # A JSON document can be shallow enough to parse yet contain metadata
        # deep enough to exhaust dataclass/report serialization. Refuse it.
        raise ShadowInputError("INPUT_NESTING_BOUND") from exc


def _observe_file(path: Path, event_slug: str, limits: Limits) -> dict:
    if not isinstance(event_slug, str) or not 1 <= len(event_slug) <= 256 or any(ord(c) < 32 for c in event_slug):
        raise ShadowInputError("EVENT_SLUG_INVALID")
    try:
        loaded = load_offline_json(Path(path), limits)
    except (TypeError, ValueError, OSError) as exc:
        raise ShadowInputError("INPUT_PATH_INVALID") from exc
    except InvalidOperation as exc:
        raise ShadowInputError("INPUT_INVALID_JSON") from exc
    if loaded.payload is None:
        raise ShadowInputError("INPUT_" + (loaded.discrepancy or "UNKNOWN"))
    fixture = loaded.payload
    if not isinstance(fixture, dict) or not isinstance(fixture.get("payload"), dict):
        raise ShadowInputError("INPUT_SCHEMA")
    if fixture.get("evidence_class") != EvidenceClass.API_OBSERVED.value:
        raise ShadowInputError("API_OBSERVATION_CLASS_REQUIRED")
    source = _source(fixture.get("source"))
    batch = normalize_activity(fixture["payload"], source, limits)
    if fixture.get("coverage") is not None:
        declared = fixture["coverage"]
        if not isinstance(declared, dict) or declared.get("state") != batch.coverage.value:
            raise ShadowInputError("DECLARED_COVERAGE_MISMATCH")
    if any(row.evidence_class is not EvidenceClass.API_OBSERVED for row in batch.rows):
        raise ShadowInputError("ROW_EVIDENCE_CLASS_INVALID")
    if any(isinstance(row, Activity) and any(
            field is not None and (type(field) is not str or len(field) > 4096)
            for field in (row.side, row.token_id)) for row in batch.rows):
        raise ShadowInputError("ROW_METADATA_INVALID")
    result = reconcile_event_window(batch, event_slug)
    rows = [asdict(row) for row in batch.rows if isinstance(row, Activity) and row.event_slug == event_slug]
    synthetic = []
    if "synthetic_conversion" in fixture:
        synthetic.append(_synthetic(fixture["synthetic_conversion"]))
    report = {
        "version": VERSION, "mode": "V11_SHADOW", "financial_authority": False,
        "qualification": False, "transaction_level_proof": False,
        "source_file_sha256": loaded.raw_sha256,
        "source": _jsonable(asdict(source)),
        "event_slug": event_slug,
        "evidence_class": batch.evidence_class.value,
        "coverage": batch.coverage.value,
        "chain_status": result.chain_status,
        "rows": _jsonable(rows),
        "metrics": {
            "observed_rows": len(rows),
            "observed_purchases": result.purchases,
            "observed_conversions": sum(row["kind"] == "CONVERSION" for row in rows),
            "observed_merges": sum(row["kind"] == "MERGE" for row in rows),
            "apparent_cash_difference": str(result.apparent_cash_difference),
            "cash_price_discrepancy": str(result.cash_price_discrepancy),
        },
        "reconciliation": _jsonable(asdict(result)),
        "synthetic_proofs": synthetic,
        "evidence_class_counts": {
            EvidenceClass.API_OBSERVED.value: len(rows),
            EvidenceClass.CHAIN_RECEIPT.value: 0,
            EvidenceClass.SYNTHETIC_PROOF.value: len(synthetic),
        },
        "receipt_status": "NO_VERIFIED_RECEIPTS",
        "account_effects": [],
        "order_effects": [],
    }
    # Content identity excludes the identity itself, so replay is byte stable.
    report["observation_id"] = hashlib.sha256(_encoded(report)).hexdigest()
    _encoded(report)
    return report


def write_artifact(path: Path, report: dict) -> bool:
    """Create once, or verify an identical existing artifact. Never replace it."""
    if (not isinstance(report, dict) or report.get("version") != VERSION
            or report.get("mode") != "V11_SHADOW"
            or any(report.get(key) is not False for key in
                   ("financial_authority", "qualification", "transaction_level_proof"))
            or report.get("account_effects") != [] or report.get("order_effects") != []
            or report.get("evidence_class") != EvidenceClass.API_OBSERVED.value
            or type(report.get("coverage")) is not str
            or report["coverage"] not in {c.value for c in Coverage}
            or report.get("chain_status") != "CHAIN_UNVERIFIED"
            or report.get("receipt_status") != "NO_VERIFIED_RECEIPTS"
            or not isinstance(report.get("reconciliation"), dict)
            or report["reconciliation"].get("coverage") != report["coverage"]
            or report["reconciliation"].get("evidence_class") != EvidenceClass.API_OBSERVED.value
            or not isinstance(report.get("evidence_class_counts"), dict)
            or report["evidence_class_counts"].get(EvidenceClass.CHAIN_RECEIPT.value) != 0
            or report["reconciliation"].get("account_effects") != []
            or not isinstance(report.get("rows"), list)
            or any(not isinstance(row, dict) or row.get("evidence_class") != EvidenceClass.API_OBSERVED.value
                   for row in report["rows"])
            or report["evidence_class_counts"].get(EvidenceClass.API_OBSERVED.value) != len(report["rows"])
            or not isinstance(report.get("synthetic_proofs"), list)
            or any(not isinstance(item, dict) or item.get("evidence_class") != EvidenceClass.SYNTHETIC_PROOF.value
                   or item.get("coverage") is not None or item.get("transaction_level_proof") is not False
                   or not isinstance(item.get("result"), dict)
                   or item["result"].get("proof_class") != EvidenceClass.SYNTHETIC_PROOF.value
                   or item["result"].get("account_effects") != []
                   for item in report["synthetic_proofs"])
            or report["evidence_class_counts"].get(EvidenceClass.SYNTHETIC_PROOF.value) != len(report["synthetic_proofs"])):
        raise ShadowInputError("ARTIFACT_POLICY_REFUSED")
    expected_id = hashlib.sha256(_encoded({k: v for k, v in report.items() if k != "observation_id"})).hexdigest()
    if report.get("observation_id") != expected_id:
        raise ShadowInputError("ARTIFACT_ID_MISMATCH")
    raw = _encoded(report)
    path = Path(path)
    if not path.name or path.name in (".", ".."):
        raise ShadowInputError("OUTPUT_PATH_INVALID")
    absolute = path if path.is_absolute() else Path.cwd() / path
    parts = absolute.parts
    try:
        directory = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        raise ShadowInputError("OUTPUT_IO_REFUSED") from exc
    try:
        for component in parts[1:-1]:
            next_dir = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = next_dir
        temporary = ".inventory-shadow-" + secrets.token_hex(12)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, parts[-1], src_dir_fd=directory, dst_dir_fd=directory,
                        follow_symlinks=False)
                os.fsync(directory)
                return True
            except FileExistsError:
                existing_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                      dir_fd=directory)
                with os.fdopen(existing_fd, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise ShadowInputError("OUTPUT_NOT_REGULAR")
                    existing = stream.read(MAX_ARTIFACT_BYTES + 1)
                if existing != raw:
                    raise ShadowInputError("OUTPUT_CONFLICT")
                return False
        finally:
            os.unlink(temporary, dir_fd=directory)
    except OSError as exc:
        raise ShadowInputError("OUTPUT_IO_REFUSED") from exc
    finally:
        os.close(directory)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline InventoryTransform SHADOW observer")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--event", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = observe_file(args.input, args.event)
        write_artifact(args.output, report)
    except ShadowInputError as exc:
        parser.exit(2, f"inventory shadow refused: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
