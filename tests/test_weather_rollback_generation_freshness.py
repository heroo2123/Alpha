from __future__ import annotations

import importlib.util
from pathlib import Path
import pytest


def _load_helper():
    path=Path(__file__).resolve().parents[1]/"deploy/weather-paper-venv-snapshot.py"
    spec=importlib.util.spec_from_file_location("weather_paper_venv_snapshot_test",path); assert spec and spec.loader
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


def test_archive_integrity_alone_does_not_prove_current_venv_and_tree_check_detects_drift(tmp_path):
    helper=_load_helper(); app=tmp_path/"app"; venv=app/".venv"; (venv/"bin").mkdir(parents=True); (venv/"bin/python").write_text("python-placeholder\n")
    package=venv/"lib/example.py"; package.parent.mkdir(parents=True); package.write_text("version = 1\n")
    archive=tmp_path/"rollback/previous-venv.tar"; manifest=tmp_path/"rollback/previous-venv.json"
    helper.snapshot(venv,archive,manifest); helper.verify_archive(archive,manifest); helper.verify_tree(venv,manifest)
    package.write_text("version = 2\n"); helper.verify_archive(archive,manifest)
    with pytest.raises(helper.VenvSnapshotError,match="VENV_RESTORED_TREE_MISMATCH"): helper.verify_tree(venv,manifest)


def test_host_snapshot_publishes_generation_specific_immutable_predecessor_before_cutover():
    root=Path(__file__).resolve().parents[1]; text=(root/"deploy/weather-paper-host-snapshot.sh").read_text()
    assert 'GENERATIONS="${ROLLBACK_DIR}/generations"' in text
    assert '[[ "${PREDECESSOR_SHA}" != "${CANDIDATE_SHA}" ]]' in text
    assert 'cutover generation for candidate already exists' in text
    assert 'GEN="${GENERATIONS}/${GENERATION_ID}"' in text
    assert 'generation.json' in text and 'weather-paper-cutover-generation-v1' in text
    assert 'snapshot-generation-v4' not in text and 'previous-release.sha' not in text
    for field in ('predecessor_sha','predecessor_tree','candidate_sha','candidate_tree','predecessor_unit_digest','predecessor_db_digest','predecessor_venv_digest','venv_manifest_digest','active','enabled','release_marker','deploy_user','app_dir','creation_timestamp'):
        assert field in text


def test_prepare_weather_candidate_snapshots_before_mutation_and_start_recovery_is_generation_exact():
    root=Path(__file__).resolve().parents[1]
    prepare=(root/"deploy/prepare-weather-paper-candidate.sh").read_text(); start=(root/"deploy/start-weather-paper-candidate.sh").read_text(); recovery=(root/"deploy/weather-paper-host-recovery.sh").read_text()
    assert prepare.index('"${HOST_SNAPSHOT}" --candidate-sha') < prepare.index('checkout --detach "${RELEASE_SHA}"')
    assert 'verify-generation --generation-id "${EXPECTED_GENERATION}" --sha "${EXPECTED_SHA}"' in start
    assert '"${HOST_RECOVERY}" --generation-id "${EXPECTED_GENERATION}"' in start
    assert 'GEN="${ROLLBACK_DIR}/generations/${GENERATION_ID}"' in recovery
    assert 'checkout --detach "${PREDECESSOR_SHA}"' in recovery


def test_prepare_proves_root_custodied_rollback_tree_matches_current_venv_before_mutation():
    root=Path(__file__).resolve().parents[1]
    text=(root/"deploy/prepare-all-paper-candidate-v3.sh").read_text()
    ownership_check=text.index('rollback artifact not root owned'); archive_verify=text.index('"${HOST_VENV}" verify'); tree_verify=text.index('"${HOST_VENV}" verify-tree'); mutation_guard=text.index("PREPARE_MUTATED=0"); checkout=text.index('checkout --detach "${RELEASE_SHA,,}"')
    assert ownership_check < archive_verify < tree_verify < mutation_guard < checkout
    assert '"${GATE}" verify-checkout' in text and '"${GATE}" verify-object' in text and "snapshot-generation-v4" in text and "rollback-manifest-v4.json" in text and "all-paper-rollback-v4-root-custody-hash-bound" in text and 'HOST_RECOVERY="${LIBEXEC}/restore-rollback.sh"' in text
