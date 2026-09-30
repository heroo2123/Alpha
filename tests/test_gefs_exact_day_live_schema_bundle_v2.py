"""Deterministic v2 rebuild of the GEFS31->live-schema exact-day research
candidates. See tools/gefs_exact_day_live_schema_bundle_v2.py's module
docstring and docs/V11_R47_EXACT_DAY_LIVE_SCHEMA_REBUILD.md's final section for
background on the two v1 draft-generator provenance defects (wall-clock
``created_at``/``causal_watermark``; identical ``"ture"`` HIGH/LOW family
run-id suffix, since ``"daily_high_temperature"[-4:] == "daily_low_temperature"[-4:]``)
this generator fixes. Every test here either uses a small synthetic evidence
fixture (fast, offline, no private machine-local data required) or is gated
behind ``requires_real_evidence`` and skips cleanly when the real 541-station-day
private evidence tree is absent.
"""
from __future__ import annotations

import json
import hashlib
import sys
import os
import shutil
import subprocess
import sqlite3
import time as time_module
from datetime import datetime, timezone
from pathlib import Path

import pytest

from polymarket_scanner.v11.evidence import EvidenceError, digest
from polymarket_scanner.v11.gefs_sources import MODEL_ID as LIVE_MODEL_ID
from polymarket_scanner.v11.model_artifacts import ArtifactStore, predict_with_bundle
from polymarket_scanner.v11.offline_learning import LearningEnvelope, _Budget, _predict
from polymarket_scanner.v11.probability import FINAL_EXTREME
from polymarket_scanner.weather_only_contracts import DAILY_HIGH, DAILY_LOW
from test_v11_probability import T, component, rule
from tools import gefs_exact_day_live_schema_bundle_v2 as tool


COMMISSIONING_EVIDENCE = Path('/home/alphaadmin/AlphaV11_Commissioning/evidence')
BRAINWORK = Path('/home/alphaadmin/AlphaV11_BrainWork')
PLAN_PATH = COMMISSIONING_EVIDENCE / 'v11_brain_historical_backfill_plan_20260929.json'
CATALOG_PATH = COMMISSIONING_EVIDENCE / 'v11_historical_daily_temperature_catalog_20260929.json'
PREREGISTRATION_PATH = COMMISSIONING_EVIDENCE / 'v11_all_market_gefs_preregistration_20260929.json'
OLD_MANIFEST_PATH = COMMISSIONING_EVIDENCE / 'v11_gefs_all_market_dataset_manifest_20260929.json'
GEFS_DB_PATH = BRAINWORK / 'historical_gefs_backfill.sqlite'
V1_MANIFEST_PATH = BRAINWORK / 'exact_day_live_schema_bundles_20260930' / 'manifest.json'
REAL_EVIDENCE_PRESENT = all(p.exists() for p in
    (PLAN_PATH, CATALOG_PATH, PREREGISTRATION_PATH, OLD_MANIFEST_PATH, GEFS_DB_PATH))
requires_real_evidence = pytest.mark.skipif(not REAL_EVIDENCE_PRESENT,
    reason='real 541-station-day private GEFS evidence tree is absent')

DEPENDENCY_SHA256 = hashlib.sha256((Path(tool.__file__).resolve().parents[1] / 'requirements-dev.txt').read_bytes()).hexdigest()
RUNTIME_SHA256 = hashlib.sha256((sys.version + '|' + sys.executable).encode()).hexdigest()


@pytest.fixture(scope='module')
def committed_source_snapshot(tmp_path_factory):
    """Commit exact executing sources in a disposable repo even before code commit A.

    No verifier is mocked. Real CLI builds later bind the actual branch commit.
    """
    source = Path(tool.__file__).resolve().parents[1]
    root = tmp_path_factory.mktemp('r47-committed-source')
    for p in (source / 'polymarket_scanner').rglob('*.py'):
        target = root / p.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, target)
    (root / 'tools').mkdir()
    shutil.copyfile(tool.__file__, root / 'tools' / Path(tool.__file__).name)
    shutil.copyfile(source / 'requirements-dev.txt', root / 'requirements-dev.txt')
    def git(*args):
        return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True,
            env={**os.environ, 'GIT_AUTHOR_DATE': '2026-09-30T00:00:00Z',
                 'GIT_COMMITTER_DATE': '2026-09-30T00:00:00Z'})
    git('init', '-q')
    git('add', 'tools', 'polymarket_scanner', 'requirements-dev.txt')
    git('-c', 'user.name=R47 Test', '-c', 'user.email=r47-test@example.invalid',
        'commit', '-qm', 'Executable source snapshot')
    return root


@pytest.fixture(autouse=True)
def use_committed_source_snapshot(committed_source_snapshot, monkeypatch):
    monkeypatch.setattr(tool, 'REPO_ROOT', committed_source_snapshot)


# ---------------------------------------------------------------------------
# Synthetic fixture: exercises the exact same evidence-verification / dataset /
# fit / bundle pipeline as a real run, on one hand-built station-day, so tests
# run fast and never depend on private machine-local data.
# ---------------------------------------------------------------------------

def _self_hashed(body: dict) -> dict:
    value = dict(body)
    value['artifact_sha256'] = digest(body)
    return value


def _build_synthetic_db(path: Path, station_day: str, hours, member_count: int = 31) -> None:
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute('CREATE TABLE messages (message_key TEXT, run_date TEXT, cycle INT, hour INT, member INT, '
                 'status TEXT, attempts INT, idx_sha256 TEXT, grib_sha256 TEXT, bytes INT, error TEXT)')
    conn.execute('CREATE TABLE station_days (station_day TEXT, station TEXT, target_date TEXT, split TEXT, '
                 'expected_values INT, status TEXT)')
    conn.execute('CREATE TABLE point_values (message_key TEXT, station_day TEXT, member INT, hour INT, '
                 'value_k REAL, distance_km REAL, nearest_lat REAL, nearest_lon REAL)')
    for member in range(member_count):
        for hour in hours:
            value_k = 290.0 + member * 0.05 + hour * 0.02
            conn.execute('INSERT INTO point_values VALUES (?,?,?,?,?,?,?,?)',
                        (f'msg-{member}-{hour}', station_day, member, hour, value_k, 1.0, 40.0, -73.0))
    conn.commit()
    conn.close()


def _open_db(db_path: Path):
    conn = sqlite3.connect('file:' + str(db_path) + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    return conn


def _synthetic_evidence(tmp_path: Path):
    station, target_date, tz = 'TESTC', '2026-08-15', 'America/New_York'
    station_day = station + '|' + target_date
    hours = list(range(3, 31, 3))  # +3h..+30h; local-day boundary (+4h/+28h) is off the 3h grid

    catalog = _self_hashed({'events': [
        {'station': station, 'target_date': target_date, 'family': DAILY_HIGH, 'complete_final_vector': True,
         'source_family': 'NWS_WRH_TIMESERIES', 'event_id': 'evt-high-1', 'unit': 'C',
         'buckets': [{'market_id': 'm1', 'condition_id': 'c1', 'yes_token': 't1',
                      'lower': 10.0, 'upper': 15.0, 'yes_payout': 1.0}]},
        {'station': station, 'target_date': target_date, 'family': DAILY_LOW, 'complete_final_vector': True,
         'source_family': 'NWS_WRH_TIMESERIES', 'event_id': 'evt-low-1', 'unit': 'C',
         'buckets': [{'market_id': 'm2', 'condition_id': 'c2', 'yes_token': 't2',
                      'lower': 0.0, 'upper': 5.0, 'yes_payout': 0.0}]},
    ]})
    plan = _self_hashed({'catalog_sha256': catalog['artifact_sha256'], 'station_days': [
        {'station': station, 'target_date': target_date, 'timezone': tz,
         'run_utc': target_date + 'T00:00:00+00:00', 'forecast_hours': hours, 'split': 'TRAIN'},
    ]})
    preregistration = _self_hashed({
        'created_utc': '2026-08-01T00:00:00.500000+00:00',
        'brain_plan_sha256': plan['artifact_sha256'], 'catalog_sha256': catalog['artifact_sha256'],
        'search': {'bias_grid_c': [0.0], 'sigma_grid_c': [0.5], 'primary_metric': 'brier',
                   'minimum_train_city_days': 2, 'minimum_evaluation_city_days': 2, 'minimum_evaluation_stations': 2,
                   'required_improvement': 0.0, 'maximum_slice_regression': 0.0, 'max_wall_seconds': 5.0,
                   'max_kernel_evaluations': 100000, 'bootstrap_resamples': 100, 'seed': 1},
        'baseline': {'bias_c': 0.0, 'kernel_sigma_c': 0.5},
    })

    db_path = tmp_path / 'gefs.sqlite'
    _build_synthetic_db(db_path, station_day, hours)
    conn = _open_db(db_path)
    db_content_sha256 = tool.database_content_sha256(conn)
    conn.close()

    old_manifest = {'created_utc': '2026-08-16T00:00:00+00:00', 'plan_sha256': plan['artifact_sha256'], 'catalog_sha256': catalog['artifact_sha256'],
                     'gefs_database_content_sha256': db_content_sha256}
    return plan, catalog, preregistration, old_manifest, db_path


def _run(plan, catalog, preregistration, old_manifest, db_path, output_root):
    db = _open_db(db_path)
    try:
        return tool.generate(plan=plan, catalog=catalog, preregistration=preregistration,
            old_dataset_manifest=old_manifest, db=db, output_root=output_root,
            code_commit=tool._git(['rev-parse', 'HEAD']), code_tree=tool._git(['rev-parse', 'HEAD^{tree}']), dependency_sha256=DEPENDENCY_SHA256,
            runtime_sha256=RUNTIME_SHA256, dataset_manifest_sha256=digest(old_manifest))
    finally:
        db.close()


def _generate(tmp_path: Path, name: str = 'v2'):
    plan, catalog, preregistration, old_manifest, db_path = _synthetic_evidence(tmp_path)
    manifest = _run(plan, catalog, preregistration, old_manifest, db_path, tmp_path / name)
    return manifest, tmp_path / name


# ---------------------------------------------------------------------------
# Fixes documented in docs/V11_R47_EXACT_DAY_LIVE_SCHEMA_REBUILD.md
# ---------------------------------------------------------------------------

def test_v1_defect_reproduced_family_name_suffix_collision():
    """Documents exactly why the v1 generator's ``family_name[-4:]`` run-id
    suffix was unsafe: both family names share the same last four characters.
    """
    assert DAILY_HIGH[-4:] == DAILY_LOW[-4:] == 'ture'


def test_high_and_low_run_identities_never_collide(tmp_path):
    manifest, _ = _generate(tmp_path)
    by_family_unit = {(b['family'], b['unit']): b for b in manifest['bundles']}
    run_ids = []
    for unit in ('C', 'F'):
        high, low = by_family_unit[(DAILY_HIGH, unit)], by_family_unit[(DAILY_LOW, unit)]
        assert high['parent_run_id'] != low['parent_run_id']
        assert high['fit_run_id'] != low['fit_run_id']
        assert 'HIGH' in high['parent_run_id'] and 'HIGH' in high['fit_run_id']
        assert 'LOW' in low['parent_run_id'] and 'LOW' in low['fit_run_id']
        run_ids += [high['parent_run_id'], high['fit_run_id'], low['parent_run_id'], low['fit_run_id']]
    assert len(run_ids) == len(set(run_ids)) == 8


def test_created_at_uses_commit_lineage_and_watermark_uses_completed_dataset(tmp_path):
    plan, catalog, preregistration, old_manifest, db_path = _synthetic_evidence(tmp_path)
    expected = float(tool._git(['show', '-s', '--format=%ct', 'HEAD']))
    watermark = datetime.fromisoformat(old_manifest['created_utc']).timestamp()
    assert expected >= watermark > datetime.fromisoformat(preregistration['created_utc']).timestamp()

    manifest_a = _run(plan, catalog, preregistration, old_manifest, db_path, tmp_path / 'run_a')
    time_module.sleep(1.1)
    manifest_b = _run(plan, catalog, preregistration, old_manifest, db_path, tmp_path / 'run_b')

    assert manifest_a['created_utc'] == manifest_b['created_utc']
    assert manifest_a['created_utc'] == datetime.fromtimestamp(expected, tz=timezone.utc).isoformat()
    assert manifest_a['provenance_timestamp_source'] == 'VERIFIED_CODE_COMMIT_COMMITTER_TIME'
    store = ArtifactStore(tmp_path / 'run_a' / 'objects')
    for b in manifest_a['bundles']:
        prov = store.pin(b['candidate_bundle_sha256']).payload['components']['PROBABILITY']['provenance']
        assert prov['created_at'] == expected
        assert prov['causal_watermark'] == watermark
        assert prov['dataset_sha256'] == digest(manifest_a['dataset_binding'])
    assert manifest_a['source_dataset_manifest_snapshot'] == old_manifest
    assert manifest_a['source_dataset_manifest_sha256'] == digest(old_manifest)
    assert manifest_a['historical_availability_established'] is False
    assert manifest_a['forward_proof_established'] is False


def test_repeat_generation_is_byte_identical(tmp_path):
    plan, catalog, preregistration, old_manifest, db_path = _synthetic_evidence(tmp_path)

    manifest_a = _run(plan, catalog, preregistration, old_manifest, db_path, tmp_path / 'run_a')
    time_module.sleep(1.1)
    manifest_b = _run(plan, catalog, preregistration, old_manifest, db_path, tmp_path / 'run_b')

    assert manifest_a == manifest_b
    assert manifest_a['artifact_sha256'] == manifest_b['artifact_sha256']
    objects_a = sorted(p.name for p in (tmp_path / 'run_a' / 'objects').iterdir())
    objects_b = sorted(p.name for p in (tmp_path / 'run_b' / 'objects').iterdir())
    assert objects_a == objects_b
    for name in objects_a:
        assert ((tmp_path / 'run_a' / 'objects' / name).read_bytes()
                == (tmp_path / 'run_b' / 'objects' / name).read_bytes())
    manifest_bytes_a = (tmp_path / 'run_a' / 'manifest.json').read_bytes()
    manifest_bytes_b = (tmp_path / 'run_b' / 'manifest.json').read_bytes()
    assert manifest_bytes_a == manifest_bytes_b


def test_manifest_never_claims_forward_shadow_promotion_or_host_approval(tmp_path):
    manifest, _ = _generate(tmp_path)
    assert manifest['historical_confirmation_is_forward_holdout'] is False
    assert manifest['status'] == 'NO_PROMOTION'
    assert manifest['calibration_status'] == 'FITTED_NOT_CALIBRATED'
    assert manifest['financial_authority'] is False
    assert manifest['promotion_authority'] is False
    assert manifest['order_authority'] is False
    assert manifest['host_approved'] is False
    assert any('not a forward untouched holdout' in s for s in manifest['limitations'])
    assert manifest['supersedes']['draft_generator_sha256'] == tool.V1_GENERATOR_SHA256


def test_generate_fails_closed_on_tampered_plan_self_hash(tmp_path):
    plan, catalog, preregistration, old_manifest, db_path = _synthetic_evidence(tmp_path)
    tampered_plan = dict(plan)
    tampered_plan['station_days'] = []  # mutated without updating artifact_sha256
    with pytest.raises(EvidenceError, match='V2_SOURCE_SELF_HASH_MISMATCH'):
        _run(tampered_plan, catalog, preregistration, old_manifest, db_path, tmp_path / 'tampered')


def test_generate_fails_closed_on_tampered_gefs_db_content_hash(tmp_path):
    plan, catalog, preregistration, old_manifest, db_path = _synthetic_evidence(tmp_path)
    tampered_manifest = dict(old_manifest)
    tampered_manifest['gefs_database_content_sha256'] = 'f' * 64
    with pytest.raises(EvidenceError, match='V2_GEFS_DB_CONTENT_HASH_MISMATCH'):
        _run(plan, catalog, preregistration, tampered_manifest, db_path, tmp_path / 'tampered')


def test_generate_fails_closed_on_broken_preregistration_link(tmp_path):
    plan, catalog, preregistration, old_manifest, db_path = _synthetic_evidence(tmp_path)
    tampered = _self_hashed({**{k: v for k, v in preregistration.items() if k != 'artifact_sha256'},
                             'catalog_sha256': 'f' * 64})
    with pytest.raises(EvidenceError, match='V2_PREREGISTRATION_LINK_MISMATCH'):
        _run(plan, catalog, tampered, old_manifest, db_path, tmp_path / 'tampered')


def test_output_root_forbidden_for_protected_paths(tmp_path):
    plan, catalog, preregistration, old_manifest, db_path = _synthetic_evidence(tmp_path)
    with pytest.raises(EvidenceError, match='V2_OUTPUT_ROOT_FORBIDDEN'):
        _run(plan, catalog, preregistration, old_manifest, db_path, Path('/var/lib/alpha-v11/model-authority/v2'))


# ---------------------------------------------------------------------------
# C/F affine invariance
# ---------------------------------------------------------------------------

def _predict_one(members, lower_cut, upper_cut, bias, sigma):
    features = tuple(f'm{i}' for i in range(len(members)))
    env = LearningEnvelope('SYN', features, 'lower_cut', 'upper_cut', (0.0,), (0.5,), 'brier',
                           2, 2, 2, 0.0, 0.0, 5.0, 100000, 100, 1)
    budget = _Budget(env, time_module.monotonic)
    row = {'values': {**dict(zip(features, members)), 'lower_cut': lower_cut, 'upper_cut': upper_cut},
           'target_identity': {'side': 'YES'}}
    return _predict([row], env, bias, sigma, budget)[0]


def test_cf_affine_invariance_holds_for_the_selected_fit(tmp_path):
    manifest, _ = _generate(tmp_path)
    members_c = [10.0, 12.5, 9.3, 11.1, 8.7]
    lower_c, upper_c = 9.0, 11.0
    members_f = [m * 9 / 5 + 32 for m in members_c]
    lower_f, upper_f = lower_c * 9 / 5 + 32, upper_c * 9 / 5 + 32
    for family in (DAILY_HIGH, DAILY_LOW):
        bias_c = manifest['fit'][family]['selected_parameters']['bias_c']
        sigma_c = manifest['fit'][family]['selected_parameters']['kernel_sigma_c']
        p_c = _predict_one(members_c, lower_c, upper_c, bias_c, sigma_c)
        p_f = _predict_one(members_f, lower_f, upper_f, bias_c * 1.8, sigma_c * 1.8)
        assert p_c == pytest.approx(p_f, abs=1e-9)

        bundle_c = next(b for b in manifest['bundles'] if b['family'] == family and b['unit'] == 'C')
        bundle_f = next(b for b in manifest['bundles'] if b['family'] == family and b['unit'] == 'F')
        assert bundle_f['bias'] == pytest.approx(bundle_c['bias'] * 1.8)
        assert bundle_f['kernel_sigma'] == pytest.approx(bundle_c['kernel_sigma'] * 1.8)


# ---------------------------------------------------------------------------
# predict_with_bundle end-to-end, including fail-closed schema/model/unit/family
# ---------------------------------------------------------------------------

def _members(base=10.0):
    return tuple(base + 0.01 * i for i in range(31))


def test_predict_with_bundle_exercises_high_candidate_with_31_member_live_gefs_input(tmp_path):
    manifest, output_root = _generate(tmp_path)
    high_c = next(b for b in manifest['bundles'] if b['family'] == DAILY_HIGH and b['unit'] == 'C')
    pinned = ArtifactStore(output_root / 'objects').pin(high_c['candidate_bundle_sha256'])
    r = rule('high', 'C')
    c = component(r, model=LIVE_MODEL_ID, group='NCEP_GEFS', members=_members(), target=FINAL_EXTREME)
    result = predict_with_bundle(pinned, r, (c,), as_of=T + 110, max_source_age_seconds=120.)
    payload = result.payload
    assert payload['bundle_sha256'] == high_c['candidate_bundle_sha256']
    assert payload['calibration_status'] == 'UNCALIBRATED'
    assert payload['model_inputs'][0]['model_id'] == LIVE_MODEL_ID
    assert len(payload['model_inputs'][0]['members']) == 31


def test_predict_with_bundle_fails_closed_on_wrong_model_unit_family_or_member_count(tmp_path):
    manifest, output_root = _generate(tmp_path)
    high_c = next(b for b in manifest['bundles'] if b['family'] == DAILY_HIGH and b['unit'] == 'C')
    pinned = ArtifactStore(output_root / 'objects').pin(high_c['candidate_bundle_sha256'])
    r = rule('high', 'C')
    members = _members()

    with pytest.raises(EvidenceError, match='BUNDLE_MODEL_INPUT_SET_MISMATCH'):
        wrong_model = component(r, model='NOT_' + LIVE_MODEL_ID, group='NCEP_GEFS', members=members, target=FINAL_EXTREME)
        predict_with_bundle(pinned, r, (wrong_model,), as_of=T + 110, max_source_age_seconds=120.)

    with pytest.raises(EvidenceError, match='FORECAST_FEATURE_PARENT_CONTRACT_MISMATCH'):
        short = component(r, model=LIVE_MODEL_ID, group='NCEP_GEFS', members=members[:-1], target=FINAL_EXTREME)
        predict_with_bundle(pinned, r, (short,), as_of=T + 110, max_source_age_seconds=120.)

    with pytest.raises(EvidenceError, match='FORECAST_FEATURE_PARENT_CONTRACT_MISMATCH'):
        wrong_unit_rule = rule('high', 'F')
        mismatched = component(wrong_unit_rule, model=LIVE_MODEL_ID, group='NCEP_GEFS', members=members, target=FINAL_EXTREME)
        predict_with_bundle(pinned, wrong_unit_rule, (mismatched,), as_of=T + 110, max_source_age_seconds=120.)

    with pytest.raises(EvidenceError, match='FORECAST_FEATURE_PARENT_CONTRACT_MISMATCH'):
        wrong_family_rule = rule('low', 'C')
        mismatched = component(wrong_family_rule, model=LIVE_MODEL_ID, group='NCEP_GEFS', members=members, target=FINAL_EXTREME)
        predict_with_bundle(pinned, wrong_family_rule, (mismatched,), as_of=T + 110, max_source_age_seconds=120.)


# ---------------------------------------------------------------------------
# Real 541-station-day evidence (skips cleanly if the private tree is absent)
# ---------------------------------------------------------------------------

@requires_real_evidence
def test_real_evidence_dataset_digest_matches_previously_published_value():
    plan = json.loads(PLAN_PATH.read_bytes())
    catalog = json.loads(CATALOG_PATH.read_bytes())
    preregistration = json.loads(PREREGISTRATION_PATH.read_bytes())
    old_manifest = json.loads(OLD_MANIFEST_PATH.read_bytes())
    db = _open_db(GEFS_DB_PATH)
    try:
        evidence = tool.verify_source_evidence(plan=plan, catalog=catalog, preregistration=preregistration,
            old_dataset_manifest=old_manifest, dataset_manifest_sha256=digest(old_manifest), db=db)
        rows, correction = tool.build_corrected_dataset(plan, catalog, db)
    finally:
        db.close()
    dataset_sha256 = tool.dataset_digest(rows, plan_sha256=plan['artifact_sha256'],
        catalog_sha256=catalog['artifact_sha256'], gefs_db_content_sha256=evidence['gefs_database_content_sha256'])
    # Published in docs/V11_R47_EXACT_DAY_LIVE_SCHEMA_REBUILD.md as the v1 draft's
    # corrected-dataset hash; the dataset-construction math itself is unchanged by
    # this v2 generator (only run_id/timestamp provenance wrapping changed), so an
    # independent recomputation must reproduce it exactly.
    assert dataset_sha256 == '649fd39acab88dd2508c34668f37228b173b8d5902c93f59f2b75f50bbf990a9'
    assert len(correction[DAILY_HIGH]) == len(correction[DAILY_LOW]) == 16771


@requires_real_evidence
def test_real_evidence_end_to_end_generation_selects_documented_parameters_and_never_reuses_v1_hashes(tmp_path):
    plan = json.loads(PLAN_PATH.read_bytes())
    catalog = json.loads(CATALOG_PATH.read_bytes())
    preregistration = json.loads(PREREGISTRATION_PATH.read_bytes())
    old_manifest = json.loads(OLD_MANIFEST_PATH.read_bytes())
    manifest = _run(plan, catalog, preregistration, old_manifest, GEFS_DB_PATH, tmp_path / 'real_v2')

    for family in (DAILY_HIGH, DAILY_LOW):
        assert manifest['fit'][family]['selected_parameters'] == {'bias_c': 0.0, 'kernel_sigma_c': 0.5}
    by_family_unit = {(b['family'], b['unit']): b for b in manifest['bundles']}
    assert by_family_unit[(DAILY_HIGH, 'C')]['parent_run_id'] != by_family_unit[(DAILY_LOW, 'C')]['parent_run_id']
    assert by_family_unit[(DAILY_HIGH, 'C')]['fit_run_id'] != by_family_unit[(DAILY_LOW, 'C')]['fit_run_id']

    if V1_MANIFEST_PATH.exists():
        v1_manifest = json.loads(V1_MANIFEST_PATH.read_bytes())
        v1_hashes = {b['candidate_bundle_sha256'] for b in v1_manifest['bundles']}
        v2_hashes = {b['candidate_bundle_sha256'] for b in manifest['bundles']}
        assert v1_hashes.isdisjoint(v2_hashes)


def test_rejects_parent_commit_without_generator(monkeypatch):
    monkeypatch.setattr(tool, 'REPO_ROOT', Path(tool.__file__).resolve().parents[1])
    with pytest.raises(EvidenceError, match='V2_CODE_GENERATOR_NOT_COMMITTED'):
        tool.verify_code_identity('7bb4f27715a5cbbd6ed1be8d66b029e2db0e06bd',
                                  '0d7a33ccd7b1c3c7b5fac28efddc1b01d8129fde')


def test_rejects_wrong_tree():
    with pytest.raises(EvidenceError, match='V2_CODE_TREE_MISMATCH'):
        tool.verify_code_identity(tool._git(['rev-parse', 'HEAD']), 'b' * 40)


@pytest.mark.parametrize('relative', ['tools/gefs_exact_day_live_schema_bundle_v2.py',
                                      'polymarket_scanner/v11/gefs_sources.py'])
def test_rejects_modified_executable_bytes(tmp_path, monkeypatch, relative):
    root = tmp_path / 'changed'
    shutil.copytree(tool.REPO_ROOT, root)
    monkeypatch.setattr(tool, 'REPO_ROOT', root)
    with (root / relative).open('a') as f:
        f.write('\n# adversarial source change\n')
    with pytest.raises(EvidenceError, match='V2_CODE_BYTES_MISMATCH'):
        tool.verify_code_identity(tool._git(['rev-parse', 'HEAD']), tool._git(['rev-parse', 'HEAD^{tree}']))


def test_rejects_changed_dataset_completion_snapshot(tmp_path):
    plan, catalog, prereg, manifest, db_path = _synthetic_evidence(tmp_path)
    original_digest = digest(manifest)
    manifest['created_utc'] = '2026-08-17T00:00:00+00:00'
    with _open_db(db_path) as db:
        with pytest.raises(EvidenceError, match='V2_DATASET_MANIFEST_SNAPSHOT_MISMATCH'):
            tool.verify_source_evidence(plan=plan, catalog=catalog, preregistration=prereg,
                old_dataset_manifest=manifest, dataset_manifest_sha256=original_digest, db=db)


def test_rejects_lineage_before_dataset_completion_without_writing_output(tmp_path):
    plan, catalog, prereg, manifest, db_path = _synthetic_evidence(tmp_path)
    manifest['created_utc'] = '2099-01-01T00:00:00+00:00'
    with pytest.raises(EvidenceError, match='V2_CODE_LINEAGE_PREDATES_COMPLETED_DATASET'):
        _run(plan, catalog, prereg, manifest, db_path, tmp_path / 'forbidden')
    assert not (tmp_path / 'forbidden').exists()


def test_rejects_ambiguous_dataset_time(tmp_path):
    plan, catalog, prereg, manifest, db_path = _synthetic_evidence(tmp_path)
    manifest['created_utc'] = '2026-08-16T00:00:00'
    with pytest.raises(EvidenceError, match='V2_DATASET_COMPLETION_TIMESTAMP_INVALID'):
        _run(plan, catalog, prereg, manifest, db_path, tmp_path / 'ambiguous')


def test_preserves_existing_output(tmp_path):
    manifest, output = _generate(tmp_path)
    before = (output / 'manifest.json').read_bytes()
    plan, catalog, prereg, old_manifest, db_path = _synthetic_evidence(tmp_path)
    with pytest.raises(EvidenceError, match='V2_OUTPUT_ROOT_MUST_NOT_EXIST'):
        _run(plan, catalog, prereg, old_manifest, db_path, output)
    assert (output / 'manifest.json').read_bytes() == before
