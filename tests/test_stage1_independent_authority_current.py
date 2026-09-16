from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = ROOT / "host_trust/weather-paper-authority-v2/authority.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_candidate_cannot_install_or_replace_host_authority_and_bootstrap_is_independently_digest_anchored():
    candidate_install = (ROOT / "deploy/install-weather-paper-host-trust.sh").read_text()
    candidate_snapshot = (ROOT / "deploy/weather-paper-host-snapshot.sh").read_text()
    candidate_recovery = (ROOT / "deploy/weather-paper-host-recovery.sh").read_text()
    bootstrap = (
        ROOT / "host_trust/weather-paper-authority-v2/bootstrap-host-authority.sh"
    ).read_text()
    for text in (candidate_install, candidate_snapshot, candidate_recovery):
        assert "REFUSED" in text
    assert 'SOURCE="${1:-}"' in bootstrap
    assert 'EXPECTED="${2:-}"' in bootstrap
    assert 'sha256sum "${SOURCE}"' in bootstrap
    assert "authority digest mismatch" in bootstrap
    assert "git show" not in bootstrap and "git cat-file" not in bootstrap
    assert "install -o root -g root -m 0555" in bootstrap
    assert "authority-anchor-v2.json" in bootstrap


def test_host_authority_self_digest_is_independent_and_candidate_mutation_is_rejected(tmp_path: Path):
    copy = tmp_path / "authority.py"
    copy.write_bytes(AUTHORITY.read_bytes())
    module = _load(copy, "stage1_authority_ok")
    digest = module.sha256_file(copy)
    anchor = tmp_path / "anchor.json"
    anchor.write_text(
        json.dumps(
            {
                "version": module.AUTHORITY_VERSION,
                "authority_sha256": digest,
            }
        )
    )
    assert module.verify_self(anchor, require_root=False)["authority_sha256"] == digest
    copy.write_text(copy.read_text() + "\n# candidate mutation\n")
    mutated = _load(copy, "stage1_authority_mutated")
    try:
        mutated.verify_self(anchor, require_root=False)
    except mutated.AuthorityError as exc:
        assert "AUTHORITY_SELF_DIGEST_MISMATCH" in str(exc)
    else:
        raise AssertionError("mutated candidate copy unexpectedly passed independent anchor")


def test_cutover_generation_binds_predecessor_candidate_database_venv_unit_and_host_identity():
    text = AUTHORITY.read_text()
    for token in (
        "generation_id",
        "predecessor_sha",
        "predecessor_tree",
        "candidate_sha",
        "candidate_tree",
        "predecessor_unit_sha256",
        "predecessor_db_sha256",
        "predecessor_db_logical_sha256",
        "predecessor_venv_archive_sha256",
        "predecessor_venv_manifest_sha256",
        "predecessor_venv_tree_sha256",
        "predecessor_enabled",
        "predecessor_active",
        "release_marker",
        "deploy_user",
        "app_dir",
        "authority_version",
        "authority_sha256",
    ):
        assert token in text
    for guard in (
        "CUTOVER_ALREADY_ACTIVE",
        "CUTOVER_CANDIDATE_ALREADY_CURRENT",
        "CUTOVER_ACTIVE_GENERATION_MISMATCH",
        "CUTOVER_PREDECESSOR_EQUALS_CANDIDATE",
        "CUTOVER_PAYLOAD_DIGEST_MISMATCH",
        "CUTOVER_DB_LOGICAL_DIGEST_MISMATCH",
    ):
        assert guard in text


def test_recovery_uses_generation_bound_payload_and_clears_wal_shm_before_exact_db_and_venv_restore():
    text = AUTHORITY.read_text()
    assert 'Path(str(db)+"-wal").unlink(missing_ok=True)' in text
    assert 'Path(str(db)+"-shm").unlink(missing_ok=True)' in text
    assert "predecessor.sqlite3" in text
    assert "predecessor-venv.tar" in text
    assert "RECOVERY_DB_LOGICAL_MISMATCH" in text
    assert "RECOVERY_VENV_TREE_MISMATCH" in text
    assert 'checkout","--detach","-f",data["predecessor_sha"]' in text.replace(" ", "")


def test_release_environment_is_clean_hash_locked_and_rejects_unexpected_or_financial_packages():
    text = (ROOT / "deploy/release_environment.py").read_text()
    assert "include-system-site-packages = false" in text
    assert "--require-hashes" in text
    assert "RELEASE_ENV_UNEXPECTED_DISTRIBUTIONS" in text
    assert "RELEASE_ENV_MISSING_DISTRIBUTIONS" in text
    assert "RELEASE_ENV_FINANCIAL_DISTRIBUTION" in text
    assert "venv_tree_sha256" in text
    assert "RELEASE_ENV_LOCK_DIGEST_MISMATCH" in text
    assert "py-clob-client" in text and "eth-account" in text and "coincurve" in text
    prepare = (ROOT / "deploy/prepare-all-paper-candidate-v4.sh").read_text()
    assert '"${APP_DIR}/.venv/bin/python" -m pip install' not in prepare
    assert '.releases/${RELEASE_SHA}/environment-manifest.json' in prepare
    assert "seal-candidate-environment" in prepare


def test_independent_authority_exact_venv_snapshot_round_trip(tmp_path: Path):
    module = _load(AUTHORITY, "stage1_authority_roundtrip")
    venv = tmp_path / "oldvenv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    before = module.tree_digest(venv)
    archive = tmp_path / "oldvenv.tar"
    manifest = tmp_path / "oldvenv-manifest.json"
    archive_sha, manifest_sha = module.snapshot_venv(venv, archive, manifest)
    assert module.sha256_file(archive) == archive_sha
    assert module.sha256_file(manifest) == manifest_sha
    assert json.loads(manifest.read_text())["tree_sha256"] == before
    shutil.rmtree(venv)
    module.safe_extract_venv(archive, tmp_path, "oldvenv")
    assert module.tree_digest(venv) == before


def test_renderer_and_runtime_attester_target_v10_release_venv_with_allowlisted_environment():
    renderer = _load(ROOT / "deploy/render-all-paper-unit.py", "stage1_renderer")
    sha = "a" * 40
    generation = "b" * 64
    unit = renderer.render(
        Path("/home/test/app"),
        Path("/home/test/config"),
        "tester",
        sha,
        generation,
    )
    assert renderer.ALL_PAPER_MODULE == (
        "polymarket_scanner.weather_only_live_paper_all_signals_final_v10"
    )
    assert "ExecStart=/usr/bin/env -i " in unit
    assert (
        f"/home/test/app/.releases/{sha}/venv/bin/python -E -s -m "
        f"{renderer.ALL_PAPER_MODULE}"
    ) in unit
    assert "Environment=PYTHONNOUSERSITE=1" in unit
    unset = next(row for row in unit.splitlines() if row.startswith("UnsetEnvironment="))
    for key in (
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONUSERBASE",
        "PYTHONSTARTUP",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
    ):
        assert key in unset
    attester = (ROOT / "deploy/attest-all-paper-runtime-v2.py").read_text()
    assert 'FINAL_MODULE = "polymarket_scanner.weather_only_live_paper_all_signals_final_v10"' in attester
    assert "proc_env" in attester
    assert "ALL_PAPER_PROCESS_ENV_NOT_ALLOWLISTED" in attester
    assert "verify-candidate-environment" in attester


def test_prepare_start_preflight_and_persistence_share_explicit_generation_and_independent_authority():
    paths = (
        "deploy/prepare-all-paper-candidate-v4.sh",
        "deploy/preflight-all-paper-deployment.sh",
        "deploy/start-all-paper-candidate-v2.sh",
        "deploy/enable-all-paper-persistence.sh",
    )
    for path in paths:
        text = (ROOT / path).read_text()
        assert "/usr/local/libexec/polymarket-weather-paper-v2/authority.py" in text
        assert "verify-generation" in text
        assert "GEN" in text or "GENERATION_ID" in text
    start = (ROOT / "deploy/start-all-paper-candidate-v2.sh").read_text()
    persistence = (ROOT / "deploy/enable-all-paper-persistence.sh").read_text()
    for text in (start, persistence):
        assert "attest-all-paper-runtime-v2.py" in text
        assert "verify-all-paper-first-cycle-v2.py" in text
        assert "verify-operator-sync-complete.py" in text


def test_final_acceptance_and_renderer_are_not_stale_v9_targets():
    acceptance = (
        ROOT / "polymarket_scanner/weather_only_all_paper_deployment_acceptance_v2.py"
    ).read_text()
    renderer = (ROOT / "deploy/render-all-paper-unit.py").read_text()
    persistence = (ROOT / "deploy/enable-all-paper-persistence.sh").read_text()
    assert "FINAL_ALL_PAPER_RUNTIME_V10_VERSION" in acceptance
    assert "weather_only_live_paper_all_signals_final_v10" in renderer
    assert "PASS: V10 PAPER persistence enabled" in persistence
