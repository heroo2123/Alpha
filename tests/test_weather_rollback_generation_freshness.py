from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_helper():
    path = Path(__file__).resolve().parents[1] / "deploy/weather-paper-venv-snapshot.py"
    spec = importlib.util.spec_from_file_location("weather_paper_venv_snapshot_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_archive_integrity_alone_does_not_prove_current_venv_and_tree_check_detects_drift(tmp_path):
    helper = _load_helper()
    app = tmp_path / "app"
    venv = app / ".venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin/python").write_text("python-placeholder\n", encoding="utf-8")
    package = venv / "lib/example.py"
    package.parent.mkdir(parents=True)
    package.write_text("version = 1\n", encoding="utf-8")
    archive = tmp_path / "rollback" / "previous-venv.tar"
    manifest = tmp_path / "rollback" / "previous-venv.json"

    helper.snapshot(venv, archive, manifest)
    helper.verify_archive(archive, manifest)
    helper.verify_tree(venv, manifest)

    package.write_text("version = 2\n", encoding="utf-8")
    # The immutable archive is still internally valid, but it is no longer the current
    # known-good environment. Candidate preparation therefore must call verify-tree.
    helper.verify_archive(archive, manifest)
    with pytest.raises(helper.VenvSnapshotError, match="VENV_RESTORED_TREE_MISMATCH"):
        helper.verify_tree(venv, manifest)


def test_snapshot_v2_invalidates_old_generation_until_new_tree_is_verified():
    root = Path(__file__).resolve().parents[1]
    text = (root / "deploy/snapshot-all-paper-rollback-v2.sh").read_text(encoding="utf-8")
    invalidate = text.index('rm -f "${GENERATION}"')
    base_snapshot = text.index('bash "${BASE_SNAPSHOT}"')
    first_tree_verify = text.index('"${VENV_HELPER}" verify-tree')
    publish = text.index('mv -f "${TMP_GENERATION}" "${GENERATION}"')
    assert invalidate < base_snapshot < first_tree_verify < publish
    assert text.count('"${VENV_HELPER}" verify-tree') >= 2


def test_prepare_proves_rollback_tree_matches_current_venv_before_mutation():
    root = Path(__file__).resolve().parents[1]
    text = (root / "deploy/prepare-all-paper-candidate-v2.sh").read_text(encoding="utf-8")
    archive_verify = text.index('"${TMP_HELPER}" verify')
    tree_verify = text.index('"${TMP_HELPER}" verify-tree')
    mutation_guard = text.index("PREPARE_MUTATED=0")
    checkout = text.index('checkout --detach "${RELEASE_SHA}"')
    assert archive_verify < tree_verify < mutation_guard < checkout
    assert "polymarket_scanner/weather_only_operator_state_corrective_v3.py" in text
    assert "polymarket_scanner/weather_only_operator_state_corrective_v4.py" in text
