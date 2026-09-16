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
    helper.verify_archive(archive, manifest)
    with pytest.raises(helper.VenvSnapshotError, match="VENV_RESTORED_TREE_MISMATCH"):
        helper.verify_tree(venv, manifest)


def test_host_snapshot_invalidates_old_generation_until_source_and_venv_are_reverified():
    root = Path(__file__).resolve().parents[1]
    text = (root / "deploy/weather-paper-host-snapshot.sh").read_text(encoding="utf-8")
    invalidate = text.index('rm -f "${GENERATION}"')
    venv_snapshot = text.index('"${VENV_HELPER}" snapshot')
    archive_verify = text.index('"${VENV_HELPER}" verify', venv_snapshot)
    tree_verify = text.index('"${VENV_HELPER}" verify-tree', archive_verify)
    final_release_verify = text.rindex('"${GATE}" verify-checkout')
    publish = text.index("'all-paper-rollback-v3-host-authority' > \"${GENERATION}\"")
    assert invalidate < venv_snapshot < archive_verify < tree_verify < final_release_verify < publish
    assert "snapshot-generation-v3" in text
    assert "previous-release.sha" in text
    assert "previous-tree.sha" in text
    assert "previous-db.sha256" in text


def test_prepare_proves_host_rollback_tree_matches_current_venv_before_mutation():
    root = Path(__file__).resolve().parents[1]
    text = (root / "deploy/prepare-all-paper-candidate-v3.sh").read_text(encoding="utf-8")
    archive_verify = text.index('"${HOST_VENV}" verify')
    tree_verify = text.index('"${HOST_VENV}" verify-tree')
    mutation_guard = text.index("PREPARE_MUTATED=0")
    checkout = text.index('checkout --detach "${RELEASE_SHA,,}"')
    assert archive_verify < tree_verify < mutation_guard < checkout
    assert '"${GATE}" verify-checkout' in text
    assert '"${GATE}" verify-object' in text
    assert "snapshot-generation-v3" in text
    assert "all-paper-rollback-v3-host-authority" in text
    assert 'HOST_RECOVERY="${LIBEXEC}/restore-rollback.sh"' in text
