"""Proves (or refuses to fabricate) live-GEFS-schema compatibility for the frozen
all-market "gefs31" research fit. See tools/gefs_schema_rebind.py's module
docstring for the full background.
"""
from dataclasses import replace
from pathlib import Path

import pytest

from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.gefs_sources import MODEL_ID as LIVE_MODEL_ID, linear_extreme
from polymarket_scanner.v11.model_artifacts import ArtifactStore, predict_with_bundle
from test_v11_gefs_sources import all_fields, gefs  # noqa: F401  (fixture import)
from test_v11_probability import T, component, rule
from tools import gefs_schema_rebind as tool


COMMISSIONING_EVIDENCE = Path('/home/alphaadmin/AlphaV11_Commissioning/evidence')
BRAINWORK = Path('/home/alphaadmin/AlphaV11_BrainWork')
RESEARCH_RESULT = COMMISSIONING_EVIDENCE / 'v11_gefs_all_market_research_result_20260929.json'
DATASET_MANIFEST = COMMISSIONING_EVIDENCE / 'v11_gefs_all_market_dataset_manifest_20260929.json'
PREREGISTRATION = COMMISSIONING_EVIDENCE / 'v11_all_market_gefs_preregistration_20260929.json'
DATASET_GZ = BRAINWORK / 'v11_gefs_all_market_dataset_20260929.json.gz'
REAL_EVIDENCE_PRESENT = all(p.exists() for p in (RESEARCH_RESULT, DATASET_MANIFEST, PREREGISTRATION, DATASET_GZ))
requires_real_evidence = pytest.mark.skipif(not REAL_EVIDENCE_PRESENT, reason='real research evidence files absent')


def test_contract_shape_is_a_pure_rename_for_both_families():
    for family in tool.FAMILIES:
        source, live = tool.prove_contract_shape_equivalence('C', family)
        assert source.model_widths == (('gefs31', 31),)
        assert live.model_widths == ((LIVE_MODEL_ID, 31),)
        assert source.unit == live.unit == 'C'
        assert source.family == live.family == family
        assert source.quantization == live.quantization
        assert set(source.mapping) == {'gefs31'} and set(live.mapping) == {LIVE_MODEL_ID}


def test_live_feature_method_constant_matches_gefs_sources(gefs):
    from polymarket_scanner.v11 import gefs_sources as source
    ids = all_fields(gefs)
    result = source.assemble_path(gefs['store'], plan=gefs['plan'], field_ids=ids, record_id='model')
    assert result['body']['payload']['coverage']['method'] == tool.LIVE_FEATURE_METHOD


def test_member_semantics_refused_when_methods_differ():
    with pytest.raises(EvidenceError, match='GEFS31_LIVE_MEMBER_SEMANTICS_METHOD_MISMATCH'):
        tool.prove_member_semantics_equivalence(
            source_feature_method=tool.SOURCE_FEATURE_METHOD_FALLBACK,
            live_feature_method=tool.LIVE_FEATURE_METHOD)


def test_member_semantics_accepted_when_methods_are_declared_identical():
    tool.prove_member_semantics_equivalence(source_feature_method='SAME_METHOD', live_feature_method='SAME_METHOD')


def test_counterexample_snapshot_max_min_disagrees_with_live_interpolation():
    """A concrete, worked disproof of "same 31 ordered member vector": for a
    local-day window whose boundary does not land on the 3-hour synoptic grid
    (the EDDM 2026-08-24 18Z case: offsets +4h/+28h, matching
    v11_gefs_all_market_dataset_20260929.json.gz's own hours=[3..30] bracket),
    GEFS_3H_SNAPSHOT_MEMBER_DAILY_MAX_OR_MIN and the live
    PIECEWISE_LINEAR_POINT_TEMPERATURE_PATH method pick different extremes.
    """
    hours = list(range(3, 31, 3))
    points = [h * 3600. for h in hours]
    # Endpoints are the historical-method maxima; the live method must not see them.
    values = {3: 100., 6: 10., 9: 10., 12: 10., 15: 10., 18: 10., 21: 10., 24: 10., 27: 10., 30: 100.}
    series = [values[h] for h in hours]
    start, end = 4 * 3600., 28 * 3600.

    snapshot_policy_max = max(series)  # GEFS_3H_SNAPSHOT_MEMBER_DAILY_MAX_OR_MIN
    live_policy_max = linear_extreme(points, series, ((start, end),), high=True)  # live gefs_sources.py

    assert snapshot_policy_max == 100.
    assert live_policy_max < 100.
    assert snapshot_policy_max != live_policy_max


@requires_real_evidence
def test_verify_source_evidence_against_real_files():
    evidence = tool.verify_source_evidence(research_result_path=RESEARCH_RESULT,
        dataset_manifest_path=DATASET_MANIFEST, preregistration_path=PREREGISTRATION, dataset_gz_path=DATASET_GZ)
    assert evidence['source_feature_method'] == 'GEFS_3H_SNAPSHOT_MEMBER_DAILY_MAX_OR_MIN'
    for family in tool.FAMILIES:
        assert evidence['results'][family]['bias_c'] == 0.0
        assert evidence['results'][family]['kernel_sigma_c'] == 0.5


@requires_real_evidence
def test_attempt_rebind_on_real_evidence_fails_closed_and_creates_no_bundle():
    evidence = tool.verify_source_evidence(research_result_path=RESEARCH_RESULT,
        dataset_manifest_path=DATASET_MANIFEST, preregistration_path=PREREGISTRATION, dataset_gz_path=DATASET_GZ)
    for family, fp in evidence['results'].items():
        result = tool.attempt_rebind(unit='C', family=family, bias_c=fp['bias_c'], kernel_sigma_c=fp['kernel_sigma_c'],
            source_dataset_sha256=fp['dataset_sha256'], source_parameter_sha256=fp['candidate_parameter_sha256'],
            preregistration_sha256=evidence['preregistration_sha256'], training_metrics_sha256=fp['training_metrics_sha256'],
            code_commit='a' * 40, code_tree='b' * 40, created_at=T, causal_watermark=T, seed=1,
            run_id=f'test:{family}', output_root=None, source_feature_method=evidence['source_feature_method'])
        assert result['status'] == 'NO_BUNDLE_CREATED'
        assert result['bundle_sha256'] is None
        assert result['reason'] == 'GEFS31_LIVE_MEMBER_SEMANTICS_METHOD_MISMATCH'
        assert result['no_promotion'] and result['not_host_approved'] and result['historical_research_only']
        assert not result['financial_authority'] and not result['promotion_authority']


@requires_real_evidence
def test_cli_main_on_real_evidence_writes_no_bundle_manifest_only(tmp_path, capsys):
    tmp_path.chmod(0o700)
    rc = tool.main(['--research-result', str(RESEARCH_RESULT), '--dataset-manifest', str(DATASET_MANIFEST),
        '--preregistration', str(PREREGISTRATION), '--dataset-gz', str(DATASET_GZ),
        '--output-root', str(tmp_path), '--code-commit', 'a' * 40, '--code-tree', 'b' * 40])
    assert rc == 0
    manifests = list(tmp_path.glob('v11_gefs_live_schema_rebind_manifest_*.json'))
    assert len(manifests) == 1
    import json
    manifest = json.loads(manifests[0].read_text())
    assert manifest['overall_status'] == 'NO_BUNDLE_CREATED'
    assert all(r['status'] == 'NO_BUNDLE_CREATED' for r in manifest['results'])
    assert manifest['no_promotion'] and manifest['not_host_approved'] and manifest['historical_research_only']
    assert not (tmp_path / 'objects').exists()


def test_attempt_rebind_builds_and_accepts_live_model_id_when_methods_are_provably_identical(tmp_path):
    """This is the generative half of the tool: when the source and live member
    -construction methods ARE declared identical (a hypothetical this codebase's
    real evidence does not satisfy today), the tool builds one real ArtifactStore
    bundle keyed on the actual live MODEL_ID/31-member contract.
    """
    tmp_path.chmod(0o700)
    result = tool.attempt_rebind(unit='C', family='daily_high_temperature', bias_c=0.0, kernel_sigma_c=0.5,
        source_dataset_sha256='1' * 64, source_parameter_sha256='2' * 64, preregistration_sha256='3' * 64,
        training_metrics_sha256='4' * 64, code_commit='a' * 40, code_tree='b' * 40, created_at=T + 1000, causal_watermark=T,
        seed=1, run_id='synthetic-test', output_root=tmp_path,
        source_feature_method='SAME_METHOD', live_feature_method='SAME_METHOD')
    assert result['status'] == 'BUNDLE_CREATED'
    assert result['no_promotion'] and result['not_host_approved'] and result['historical_research_only']
    assert not result['financial_authority'] and not result['promotion_authority']

    store = ArtifactStore(tmp_path / 'objects')
    pinned = store.pin(result['bundle_sha256'])
    payload = pinned.payload
    assert payload['components']['PROBABILITY']['parameters']['models'][0]['model_id'] == LIVE_MODEL_ID
    assert payload['components']['PROBABILITY']['parameters']['models'][0]['bias'] == 0.0
    assert payload['components']['PROBABILITY']['parameters']['models'][0]['kernel_sigma'] == 0.5
    assert payload['components']['PROBABILITY']['provenance']['training_status'] == 'FITTED_NOT_CALIBRATED'
    assert payload['components']['CALIBRATION']['parameters']['method'] == 'VACUOUS_BOUNDS'
    assert payload['bundle']['financial_authority'] is False

    # The bundle accepts real 31-member live-MODEL_ID components.
    r = rule('high', 'C')
    live_component = component(r, model=LIVE_MODEL_ID, members=tuple(70. + .1 * i for i in range(31)))
    prediction = predict_with_bundle(pinned, r, (live_component,), as_of=T + 110, max_source_age_seconds=120)
    assert prediction.payload['bundle_sha256'] == result['bundle_sha256']
    assert prediction.payload['model_inputs'][0]['model_id'] == LIVE_MODEL_ID


def test_predictions_numerically_identical_between_source_label_and_rebound_bundle(tmp_path):
    """Proves the narrow, true claim: once contract shape AND member semantics
    are equivalent, renaming the model_id label alone does not change predicted
    bucket probabilities for the identical numeric member vector.
    """
    tmp_path.chmod(0o700)
    members = tuple(70. + .3 * i for i in range(31))
    r = rule('high', 'C')

    def provenance(run_id):
        return {'code_commit': 'a' * 40, 'code_tree': 'b' * 40, 'config_sha256': 'c' * 64, 'dataset_sha256': '1' * 64,
                'causal_watermark': T, 'dependency_sha256': 'd' * 64, 'runtime_sha256': 'e' * 64, 'seed': 1,
                'created_at': T + 1000, 'parent_bundle_sha256': None, 'run_id': run_id,
                'model_family': 'GAUSSIAN_MEMBER_MIXTURE', 'model_version': 'test', 'training_metrics_sha256': '4' * 64,
                'holdout_metrics_sha256': None, 'comparison_policy_sha256': '3' * 64, 'training_status': 'FITTED_NOT_CALIBRATED'}

    # Build the "source" bundle directly on the gefs31 label -- this is exactly
    # the shape of the existing frozen all-market fit, just constructed locally.
    from polymarket_scanner.v11.forecast_features import ForecastFeatureContract
    source_contract = ForecastFeatureContract((('gefs31', 31),), 'C', 'daily_high_temperature')
    (tmp_path / 'source_gefs31').mkdir(mode=0o700)
    source_store = ArtifactStore((tmp_path / 'source_gefs31').resolve())
    source_bundle_sha256, _ = tool._build_fitted_forecast_bundle(source_store, contract=source_contract,
        bias=-1.0, kernel_sigma=0.5, provenance=provenance('source-form'))

    live_result = tool.attempt_rebind(unit='C', family='daily_high_temperature', bias_c=-1.0, kernel_sigma_c=0.5,
        source_dataset_sha256='1' * 64, source_parameter_sha256='2' * 64, preregistration_sha256='3' * 64,
        training_metrics_sha256='4' * 64, code_commit='a' * 40, code_tree='b' * 40, created_at=T + 1000, causal_watermark=T,
        seed=1, run_id='live-form', output_root=tmp_path / 'live',
        source_feature_method='SAME_METHOD', live_feature_method='SAME_METHOD')
    assert live_result['status'] == 'BUNDLE_CREATED'

    source_pinned = source_store.pin(source_bundle_sha256)
    live_pinned = ArtifactStore(tmp_path / 'live' / 'objects').pin(live_result['bundle_sha256'])

    source_prediction = predict_with_bundle(source_pinned, r, (component(r, model='gefs31', members=members, bias=0., kernel_sigma=1.),),
        as_of=T + 110, max_source_age_seconds=120)
    live_prediction = predict_with_bundle(live_pinned, r, (component(r, model=LIVE_MODEL_ID, members=members, bias=0., kernel_sigma=1.),),
        as_of=T + 110, max_source_age_seconds=120)

    source_points = [b['point'] for b in source_prediction.payload['buckets']]
    live_points = [b['point'] for b in live_prediction.payload['buckets']]
    assert source_points == live_points


@pytest.mark.parametrize('mutation,match', [
    (lambda c: replace(c, model_id='wrong-model'), 'BUNDLE_MODEL_INPUT_SET_MISMATCH'),
    (lambda c: replace(c, members=tuple(70. + i for i in range(30))), 'FORECAST_FEATURE_PARENT_CONTRACT_MISMATCH'),
])
def test_rebound_bundle_fails_closed_on_wrong_model_id_or_width(tmp_path, mutation, match):
    tmp_path.chmod(0o700)
    result = tool.attempt_rebind(unit='C', family='daily_high_temperature', bias_c=0.0, kernel_sigma_c=0.5,
        source_dataset_sha256='1' * 64, source_parameter_sha256='2' * 64, preregistration_sha256='3' * 64,
        training_metrics_sha256='4' * 64, code_commit='a' * 40, code_tree='b' * 40, created_at=T + 1000, causal_watermark=T,
        seed=1, run_id='fail-closed-test', output_root=tmp_path,
        source_feature_method='SAME_METHOD', live_feature_method='SAME_METHOD')
    pinned = ArtifactStore(tmp_path / 'objects').pin(result['bundle_sha256'])
    r = rule('high', 'C')
    bad = mutation(component(r, model=LIVE_MODEL_ID, members=tuple(70. + i for i in range(31))))
    with pytest.raises(EvidenceError, match=match):
        predict_with_bundle(pinned, r, (bad,), as_of=T + 110, max_source_age_seconds=120)


def test_rebound_bundle_fails_closed_on_wrong_unit_or_family(tmp_path):
    tmp_path.chmod(0o700)
    result = tool.attempt_rebind(unit='C', family='daily_high_temperature', bias_c=0.0, kernel_sigma_c=0.5,
        source_dataset_sha256='1' * 64, source_parameter_sha256='2' * 64, preregistration_sha256='3' * 64,
        training_metrics_sha256='4' * 64, code_commit='a' * 40, code_tree='b' * 40, created_at=T + 1000, causal_watermark=T,
        seed=1, run_id='fail-closed-unit-family', output_root=tmp_path,
        source_feature_method='SAME_METHOD', live_feature_method='SAME_METHOD')
    pinned = ArtifactStore(tmp_path / 'objects').pin(result['bundle_sha256'])
    members = tuple(70. + i for i in range(31))
    wrong_unit_rule = rule('high', 'F')
    with pytest.raises(EvidenceError, match='PARENT_CONTRACT_MISMATCH'):
        predict_with_bundle(pinned, wrong_unit_rule, (component(wrong_unit_rule, model=LIVE_MODEL_ID, members=members),),
            as_of=T + 110, max_source_age_seconds=120)
    wrong_family_rule = rule('low', 'C')
    with pytest.raises(EvidenceError, match='PARENT_CONTRACT_MISMATCH'):
        predict_with_bundle(pinned, wrong_family_rule, (component(wrong_family_rule, model=LIVE_MODEL_ID, members=members),),
            as_of=T + 110, max_source_age_seconds=120)


def test_member_order_within_the_31_vector_does_not_affect_predictions(tmp_path):
    """The mixture CDF (probability.py ForecastComponent.cdf) sums over members
    unordered, so permuting the 31-member vector cannot itself be a compatibility
    hazard -- only the width, unit, family and model_id label are. Documented
    here instead of asserting a fabricated "wrong order fails closed" case.
    """
    tmp_path.chmod(0o700)
    result = tool.attempt_rebind(unit='C', family='daily_high_temperature', bias_c=0.0, kernel_sigma_c=0.5,
        source_dataset_sha256='1' * 64, source_parameter_sha256='2' * 64, preregistration_sha256='3' * 64,
        training_metrics_sha256='4' * 64, code_commit='a' * 40, code_tree='b' * 40, created_at=T + 1000, causal_watermark=T,
        seed=1, run_id='order-invariance-test', output_root=tmp_path,
        source_feature_method='SAME_METHOD', live_feature_method='SAME_METHOD')
    pinned = ArtifactStore(tmp_path / 'objects').pin(result['bundle_sha256'])
    r = rule('high', 'C')
    members = tuple(70. + .37 * i for i in range(31))
    forward = component(r, model=LIVE_MODEL_ID, members=members)
    reversed_order = component(r, model=LIVE_MODEL_ID, members=tuple(reversed(members)))
    p1 = predict_with_bundle(pinned, r, (forward,), as_of=T + 110, max_source_age_seconds=120)
    p2 = predict_with_bundle(pinned, r, (reversed_order,), as_of=T + 110, max_source_age_seconds=120)
    assert [b['point'] for b in p1.payload['buckets']] == [b['point'] for b in p2.payload['buckets']]


def test_output_root_rejects_protected_state_paths():
    for bad in ('/var/lib/alpha-v11/model-authority', '/etc/alpha-v11', '/home/alphaadmin/host_trust/x'):
        with pytest.raises(EvidenceError, match='REBIND_OUTPUT_ROOT_FORBIDDEN'):
            tool._forbid_protected_output_root(Path(bad))


def test_tool_module_has_no_financial_order_or_protected_state_surface():
    """The module docstring is allowed to describe, in prose, the forbidden
    surfaces this tool refuses (host_trust, /var/lib, wallets, credentials,
    approvals) -- that prose IS the safety contract. This test instead scans
    only the executable code (module docstring stripped) for any actual usage.
    """
    source = Path(tool.__file__).read_text()
    module_docstring_end = source.index('"""', source.index('"""') + 3) + 3
    code = source[module_docstring_end:]
    always_forbidden = ('wallet', 'private_key', 'mnemonic', 'credential', 'order_api',
                         'execution_namespace', 'settlement_authority=True', 'financial_authority=True')
    hits = [word for word in always_forbidden if word in code]
    assert hits == []
    import_lines = [line.strip() for line in code.splitlines() if line.strip().startswith(('import ', 'from '))]
    assert not any('host_trust' in line for line in import_lines)
    assert 'financial_authority\': False' in code or "financial_authority': False" in code
