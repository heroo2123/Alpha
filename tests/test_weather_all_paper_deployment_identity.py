"""Production replacements for obsolete V9 deployment source-string assertions.

The standalone research acceptance contract remains covered at the end of this
file; it grants no production execution authority.
"""
from pathlib import Path
import sys
import pytest
from test_host_authority_production_boundary import host, prepare, cutover, git


def _text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_final_renderer_targets_production_component_release_venvs_and_strict_loader_boundary(host):
    gid, manifest = prepare(host)
    for name, component in manifest["components"].items():
        unit = Path(host.policy["components"][name]["unit_file"]).read_text()
        assert f"{component['venv_path']}/bin/python -I -s -E -B -m polymarket_scanner.production {name} --config " in unit
        assert f"WorkingDirectory={manifest['source_path']}" in unit
        assert f"ALPHA_RELEASE_SHA={host.b}" in unit and f"ALPHA_CUTOVER_GENERATION={gid}" in unit
        assert "EnvironmentFile=" not in unit
        assert all(key in unit for key in host.m.FORBIDDEN_PROCESS_ENV)


def test_preparation_preserves_checkout_and_predecessor_until_explicit_activation(host):
    before = host.m._tree_digest(host.app / ".venv")
    gid = cutover(host)
    assert git(host.app, "rev-parse", "HEAD") == host.a
    host.m.prepare_candidate(gid, host.b, require_root=False)
    assert git(host.app, "rev-parse", "HEAD") == host.a
    assert host.m._tree_digest(host.app / ".venv") == before
    host.m.activate_checkout(gid, host.b, require_root=False)
    assert git(host.app, "rev-parse", "HEAD") == host.b


def test_actual_isolated_python_reports_sealed_module_prefix_and_interpreter(host):
    _, manifest = prepare(host)
    venv = Path(manifest["venv_path"])
    result = host.m._verify_import_environment(host.policy, Path(manifest["source_path"]), venv, require_root=False)
    assert result["isolated"] == 1 and result["user_site"] is False
    assert result["executable"] == str(venv / "bin/python") and result["prefix"] == str(venv)
    assert Path(result["base_prefix"]).resolve() == Path(sys.base_prefix).resolve()


def test_runtime_preflight_rejects_changed_installed_execution_unit(host):
    gid, _ = prepare(host)
    unit = Path(host.policy["components"]["execution"]["unit_file"])
    unit.write_text(unit.read_text().replace("-I -s -E -B -m", "-m"))
    with pytest.raises(host.m.AuthorityError, match="RUNTIME_INSTALLED_UNIT_MISMATCH"):
        host.m.verify_runtime_files(gid, host.b, require_root=False)


def test_recovery_restores_each_exact_predecessor_unit_and_marker(host):
    before = {name: Path(component["unit_file"]).read_bytes() for name, component in host.policy["components"].items()}
    gid, _ = prepare(host)
    host.m.activate_checkout(gid, host.b, require_root=False)
    host.m.recover(gid, require_root=False)
    for name, component in host.policy["components"].items():
        assert Path(component["unit_file"]).read_bytes() == before[name]
    assert Path(host.policy["release_file"]).read_text().strip() == host.a


def test_final_acceptance_v2_preserves_mature_operator_network_and_recall_stack():
    base=_text("polymarket_scanner/weather_only_all_paper_deployment_acceptance.py"); text=_text("polymarket_scanner/weather_only_all_paper_deployment_acceptance_v2.py")
    for token in ("FINAL_ALL_PAPER_RUNTIME_V5_VERSION","FINAL_ALL_PAPER_RUNTIME_V6_VERSION","FINAL_ALL_PAPER_RUNTIME_V7_VERSION","FINAL_ALL_PAPER_RUNTIME_V8_VERSION","FINAL_ALL_PAPER_RUNTIME_V9_VERSION","operator_message_sync_healthy","historical_terminal_operator_sync_complete","network_environment_absent_before_http_client_construction","global_weather_recall_complete","global_weather_recall_fresh"):
        assert token in text
    for token in ("post_receipt_future_day_provider_refetch_required","post_receipt_three_layer_thesis_revalidation_required","post_receipt_weather_before_clob_required","maker_settlement_strict_uma_finality"):
        assert token in base
