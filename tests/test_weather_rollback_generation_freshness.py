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


def test_prepare_proves_root_custodied_rollback_tree_matches_current_venv_before_mutation():
    root = Path(__file__).resolve().parents[1]
    text = (root / "deploy/prepare-all-paper-candidate-v3.sh").read_text(encoding="utf-8")
    ownership_check = text.index('rollback artifact not root owned')
    archive_verify = text.index('"${HOST_VENV}" verify')
    tree_verify = text.index('"${HOST_VENV}" verify-tree')
    mutation_guard = text.index("PREPARE_MUTATED=0")
    checkout = text.index('checkout --detach "${RELEASE_SHA,,}"')
    assert ownership_check < archive_verify < tree_verify < mutation_guard < checkout
    assert '"${GATE}" verify-checkout' in text
    assert '"${GATE}" verify-object' in text
    assert "snapshot-generation-v4" in text
    assert "rollback-manifest-v4.json" in text
    assert "all-paper-rollback-v4-root-custody-hash-bound" in text
    assert 'HOST_RECOVERY="${LIBEXEC}/restore-rollback.sh"' in text
