"""Offline InventoryTransform SHADOW lane boundaries."""
import copy
import json
from pathlib import Path
import socket
import sys

import pytest

from polymarket_scanner.v11 import inventory_shadow as shadow
from polymarket_scanner.v11.neg_risk_contract import LEGACY_MERGE_VERSION, LEGACY_SOURCE_VERSION
from polymarket_scanner.v11.structural_evidence import Limits


FIXTURE = Path(__file__).parent / "fixtures" / "v11_inventory_transforms" / "singapore_20261003_api_observed.json"
EVENT = "highest-temperature-in-singapore-on-october-3-2026"


def fixture(tmp_path):
    value = json.loads(FIXTURE.read_text())
    path = tmp_path / "input.json"
    path.write_text(json.dumps(value))
    return path, value


def test_offline_uncertainty_metrics_idempotent_replay_and_no_effects(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("SOCKET_OR_PROVIDER_ACCESS")
    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    path, value = fixture(tmp_path)
    output = tmp_path / "shadow.json"
    first = shadow.observe_file(path, EVENT)
    assert first == shadow.observe_file(path, EVENT)
    assert first["source_file_sha256"]
    assert first["mode"] == "V11_SHADOW"
    assert first["evidence_class"] == "API_OBSERVED"
    assert first["coverage"] == "INCOMPLETE"
    assert first["chain_status"] == "CHAIN_UNVERIFIED"
    assert first["qualification"] is first["transaction_level_proof"] is first["financial_authority"] is False
    assert first["account_effects"] == first["order_effects"] == []
    assert first["metrics"]["observed_purchases"] == 11
    assert first["metrics"]["apparent_cash_difference"] == "0.007412"
    assert first["reconciliation"]["coverage"] == "INCOMPLETE"
    assert "OPENING_INVENTORY_UNKNOWN" in first["reconciliation"]["unresolved"]
    assert first["evidence_class_counts"] == {"API_OBSERVED": 13, "CHAIN_RECEIPT": 0, "SYNTHETIC_PROOF": 0}
    assert shadow.write_artifact(output, first) is True
    saved = output.read_bytes()
    assert shadow.write_artifact(output, shadow.observe_file(path, EVENT)) is False
    assert output.read_bytes() == saved
    assert json.loads(saved)["observation_id"] == first["observation_id"]
    assert value["coverage"]["state"] == "INCOMPLETE"


def test_complete_and_unknown_coverage_preserved_without_qualification(tmp_path):
    path, value = fixture(tmp_path)
    value["payload"] = {"data": [], "pagination": {"has_more": False, "next_cursor": None}}
    value["source"]["parameters"] = [["start", "0"], ["end", "10"]]
    value["source"]["window_start"] = 0
    value["source"]["window_end"] = 10
    value["coverage"]["state"] = "COMPLETE"
    path.write_text(json.dumps(value))
    complete = shadow.observe_file(path, EVENT)
    assert complete["coverage"] == complete["reconciliation"]["coverage"] == "COMPLETE"
    assert complete["transaction_level_proof"] is False
    value["payload"]["pagination"] = {"has_more": False, "next_cursor": 0}
    value["coverage"]["state"] = "UNKNOWN"
    path.write_text(json.dumps(value))
    unknown = shadow.observe_file(path, EVENT)
    assert unknown["coverage"] == unknown["reconciliation"]["coverage"] == "UNKNOWN"
    assert "PAGINATION_UNKNOWN" in unknown["reconciliation"]["discrepancies"]
    assert unknown["qualification"] is False


def test_synthetic_proof_remains_separate_from_api_and_chain(tmp_path):
    path, value = fixture(tmp_path)
    value["synthetic_conversion"] = {
        "route": {"chain_id": 137, "block_number": 1, "adapter_address": "synthetic-adapter",
                  "adapter_code_hash": "synthetic-hash", "abi_version": "synthetic-abi",
                  "collateral_id": "synthetic-usdce", "conversion_version": LEGACY_SOURCE_VERSION,
                  "merge_version": LEGACY_MERGE_VERSION, "fee_bips": 0, "verification": "SYNTHETIC_ONLY"},
        "topology": {"ordered_condition_ids": ["c0", "c1"], "yes_token_ids": ["y0", "y1"],
                     "no_token_ids": ["n0", "n1"], "collateral_id": "synthetic-usdce",
                     "rule_identity": "synthetic-rule", "universe_status": "SYNTHETIC_EXACTLY_ONE"},
        "selected_mask": 1, "quantity": 5, "available_no_units": [5, 5],
    }
    path.write_text(json.dumps(value))
    report = shadow.observe_file(path, EVENT)
    assert report["coverage"] == "INCOMPLETE"
    assert report["synthetic_proofs"][0]["evidence_class"] == "SYNTHETIC_PROOF"
    assert report["synthetic_proofs"][0]["coverage"] is None
    assert report["synthetic_proofs"][0]["result"]["proof_class"] == "SYNTHETIC_PROOF"
    assert report["evidence_class_counts"]["CHAIN_RECEIPT"] == 0
    assert report["transaction_level_proof"] is False
    value["synthetic_conversion"]["route"]["verification"] = "DEPLOYED_ATTESTED"
    path.write_text(json.dumps(value))
    with pytest.raises(shadow.ShadowInputError, match="SYNTHETIC_INVALID"):
        shadow.observe_file(path, EVENT)


def test_malformed_oversized_and_conflicting_artifacts_refused(tmp_path):
    path, value = fixture(tmp_path)
    with pytest.raises(shadow.ShadowInputError, match="INPUT_BYTE_LIMIT"):
        shadow.observe_file(path, EVENT, Limits(max_bytes=10))
    path.write_text("{" * 200)
    with pytest.raises(shadow.ShadowInputError, match="INPUT_INVALID_JSON"):
        shadow.observe_file(path, EVENT)
    value["coverage"]["state"] = "COMPLETE"
    path.write_text(json.dumps(value))
    with pytest.raises(shadow.ShadowInputError, match="DECLARED_COVERAGE_MISMATCH"):
        shadow.observe_file(path, EVENT)
    value["coverage"]["state"] = "INCOMPLETE"
    path.write_text(json.dumps(value))
    report = shadow.observe_file(path, EVENT)
    output = tmp_path / "shadow.json"
    assert shadow.write_artifact(output, report)
    altered = copy.deepcopy(report)
    altered["qualification"] = True
    with pytest.raises(shadow.ShadowInputError, match="ARTIFACT_POLICY_REFUSED"):
        shadow.write_artifact(output, altered)
    other_path, other_value = fixture(tmp_path)
    other_value["source"]["name"] = "different-local-capture"
    other_path.write_text(json.dumps(other_value))
    with pytest.raises(shadow.ShadowInputError, match="OUTPUT_CONFLICT"):
        shadow.write_artifact(output, shadow.observe_file(other_path, EVENT))
    assert json.loads(output.read_text())["qualification"] is False
    linked = tmp_path / "linked.json"
    linked.symlink_to(output)
    with pytest.raises(shadow.ShadowInputError, match="OUTPUT_IO_REFUSED"):
        shadow.write_artifact(linked, report)


def test_artifact_with_extra_key_or_non_int_count_is_rejected(tmp_path):
    path, _ = fixture(tmp_path)
    report = shadow.observe_file(path, EVENT)
    output = tmp_path / "shadow.json"
    forged = {**report, "financial_authority_granted": True}
    with pytest.raises(shadow.ShadowInputError, match="ARTIFACT_POLICY_REFUSED"):
        shadow.write_artifact(output, forged)
    assert not output.exists()
    other_forged = {**report, "qualified_strategy": "READY_TO_FUND"}
    with pytest.raises(shadow.ShadowInputError, match="ARTIFACT_POLICY_REFUSED"):
        shadow.write_artifact(output, other_forged)
    assert not output.exists()
    for planted in (True, 1.0, False):
        forged_counts = copy.deepcopy(report)
        forged_counts["evidence_class_counts"][shadow.EvidenceClass.CHAIN_RECEIPT.value] = planted
        with pytest.raises(shadow.ShadowInputError, match="ARTIFACT_POLICY_REFUSED"):
            shadow.write_artifact(output, forged_counts)
        assert not output.exists()
    assert shadow.write_artifact(output, report) is True
    assert json.loads(output.read_text())["qualification"] is False


def test_weather_shadow_not_imported_or_modified(tmp_path):
    import ast

    tree = ast.parse(Path(shadow.__file__).read_text())
    names = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    names += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    assert not any("weather" in (name or "") or "account" in (name or "")
                   or "execution" in (name or "") for name in names)
    before = {name for name in sys.modules if "weather_only_runtime" in name}
    path, _ = fixture(tmp_path)
    shadow.observe_file(path, EVENT)
    after = {name for name in sys.modules if "weather_only_runtime" in name}
    assert after == before


def test_cli_replays_identical_artifact_without_socket_or_credential_access(tmp_path, monkeypatch):
    path, _ = fixture(tmp_path)
    output = tmp_path / "artifact.json"

    def denied(*args, **kwargs):
        raise AssertionError("FORBIDDEN_AMBIENT_ACCESS")

    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(shadow.os, "getenv", denied)
    assert shadow.main(["--input", str(path), "--event", EVENT, "--output", str(output)]) == 0
    first = output.read_bytes()
    assert shadow.main(["--input", str(path), "--event", EVENT, "--output", str(output)]) == 0
    assert output.read_bytes() == first


@pytest.mark.parametrize("field", ["captured_at_utc", "side", "token_id"])
def test_cli_refuses_nested_metadata_without_output(tmp_path, field, capsys):
    path, value = fixture(tmp_path)
    nested = "metadata"
    for _ in range(600):
        nested = [nested]
    if field == "captured_at_utc":
        value["source"][field] = nested
    else:
        value["payload"]["data"][0][field] = nested
    path.write_text(json.dumps(value))
    output = tmp_path / "shadow.json"
    with pytest.raises(SystemExit) as exc:
        shadow.main(["--input", str(path), "--event", EVENT, "--output", str(output)])
    assert exc.value.code == 2
    assert "refused" in capsys.readouterr().err
    assert not output.exists()


def test_cli_refuses_directory_and_overflowing_decimal_without_output(tmp_path):
    output = tmp_path / "shadow.json"
    with pytest.raises(SystemExit) as exc:
        shadow.main(["--input", str(tmp_path), "--event", EVENT, "--output", str(output)])
    assert exc.value.code == 2
    path = tmp_path / "overflow.json"
    path.write_text('{"x":1e99999999999999999999999}')
    with pytest.raises(SystemExit) as exc:
        shadow.main(["--input", str(path), "--event", EVENT, "--output", str(output)])
    assert exc.value.code == 2
    assert not output.exists()
