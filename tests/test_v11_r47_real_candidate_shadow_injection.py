"""R47 real-candidate isolated shadow-injection evidence.

Exercises the two genuine, immutable, real-data research candidate bundles
recorded in docs/V11_R47_REAL_CANDIDATE_SHADOW_EVIDENCE.md through the real
prediction/decision machinery via an explicit ISOLATED_RESEARCH_INJECTION.
Both bundles are NO_PROMOTION / FITTED_NOT_CALIBRATED research artifacts
(v11_real_data_fit_result_20260929.json); nothing here writes protected
model-authority state, approves a champion, or creates any TRADE/order/
account record. Tests that need the private, machine-local object store skip
cleanly when it is absent: it is read-only external evidence, never a Git
artifact (see ArtifactStore's own docstring: "There is deliberately no active
pointer.").
"""
import ast
from dataclasses import replace
from pathlib import Path

import pytest

from polymarket_scanner.v11 import (basket_coordinator, drift_runtime, gefs_sources, maker_context,
    position_management, pws_admission, reaction_runtime, relative_value, risk_inputs, source_release,
    strategy_admission, strategy_pipeline)
from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.forecast_features import ForecastFeatureContract
from polymarket_scanner.v11.model_artifacts import ArtifactStore
from polymarket_scanner.v11.probability import FINAL_EXTREME
from polymarket_scanner.weather_only_contracts import DAILY_HIGH, DAILY_LOW
from test_v11_model_artifacts import make_artifacts
from test_v11_probability import component, rule, T
from tools import r47_isolated_shadow_injection as harness


MODEL_ID = 'gefs31'
HIGH_ROOT = Path('/home/alphaadmin/AlphaV11_BrainWork/real_fit_20260929/high/objects')
HIGH_BUNDLE = 'de92cf905b5fa62bfea16bd0f2d7c2983ede00ff3f6a61820b4b25268fd17c46'
HIGH_PARENT = '7da9be823091885383fdeeee739c468ade30fa7c338a7daff05368329be877d2'
LOW_ROOT = Path('/home/alphaadmin/AlphaV11_BrainWork/real_fit_20260929/low/objects')
LOW_BUNDLE = 'f09730a5fd1ced4c581c425619e987b239f4ed34d1d9b5d1e2485f0dfe05a5a9'
LOW_PARENT = '8480555dfb4d4e3b085772c373ac37f2ec24ebed716894c566692a8aad4e96f3'

CANDIDATES = [
    pytest.param(HIGH_ROOT, HIGH_BUNDLE, DAILY_HIGH, 'high', id='high'),
    pytest.param(LOW_ROOT, LOW_BUNDLE, DAILY_LOW, 'low', id='low'),
]

requires_real_evidence = pytest.mark.skipif(
    not (HIGH_ROOT.is_dir() and LOW_ROOT.is_dir()),
    reason='real_fit_20260929 private object store is machine-local external evidence, not a Git artifact')


def members(base=70.0):
    return tuple(base + 0.1 * i for i in range(31))


@requires_real_evidence
@pytest.mark.parametrize('root,bundle_sha256,family,word', CANDIDATES)
def test_isolated_injection_validates_exact_real_candidate_hashes(root, bundle_sha256, family, word):
    injection = harness.inject_isolated_research_candidate(private_root=root, bundle_sha256=bundle_sha256)
    assert injection.bundle.sha256 == bundle_sha256
    assert injection.target == 'FINAL_CONTRACT_PAYOUT'
    assert injection.status == 'ISOLATED_RESEARCH_INJECTION'
    assert injection.mode == 'V11_SHADOW'
    assert injection.host_approved is False
    assert injection.promotion_authority is False
    assert injection.financial_authority is False
    payload = injection.bundle.payload
    assert payload['bundle']['financial_authority'] is False
    assert payload['components']['PROBABILITY']['parameters']['models'][0]['model_id'] == MODEL_ID
    assert len(payload['components']['FEATURES']['parameters']['features']) == 31 + 2
    assert payload['components']['CALIBRATION']['parameters']['status'] == 'UNCALIBRATED'
    assert payload['components']['PROBABILITY']['provenance']['training_status'] == 'FITTED_NOT_CALIBRATED'


@requires_real_evidence
@pytest.mark.parametrize('root,bundle_sha256,family,word', CANDIDATES)
def test_isolated_injection_parent_bundle_matches_recorded_provenance(root, bundle_sha256, family, word):
    injection = harness.inject_isolated_research_candidate(private_root=root, bundle_sha256=bundle_sha256)
    expected_parent = HIGH_PARENT if family == DAILY_HIGH else LOW_PARENT
    payload = injection.bundle.payload
    assert payload['components']['PROBABILITY']['provenance']['parent_bundle_sha256'] == expected_parent


@requires_real_evidence
@pytest.mark.parametrize('root,bundle_sha256,family,word', CANDIDATES)
def test_isolated_injection_exact_31_member_gefs31_contract_matches_bundle_schema(root, bundle_sha256, family, word):
    injection = harness.inject_isolated_research_candidate(private_root=root, bundle_sha256=bundle_sha256)
    contract = ForecastFeatureContract(((MODEL_ID, 31),), 'F', family)
    assert contract.schema.sha256 == injection.bundle.payload['bundle']['feature_schema_sha256']


@requires_real_evidence
@pytest.mark.parametrize('root,bundle_sha256,family,word', CANDIDATES)
def test_isolated_injection_exercises_future_forecast_prediction_path_with_real_31_member_gefs_input(
        root, bundle_sha256, family, word):
    injection = harness.inject_isolated_research_candidate(private_root=root, bundle_sha256=bundle_sha256)
    r = rule(word, 'F')
    assert r.payload['family'] == family
    c = component(r, model=MODEL_ID, group='NCEP_GEFS', members=members(), target=FINAL_EXTREME)
    result = harness.run_isolated_shadow_prediction(injection, r, (c,), as_of=T + 110, max_source_age_seconds=120.)
    assert result['status'] == 'ISOLATED_RESEARCH_INJECTION'
    assert result['mode'] == 'V11_SHADOW'
    assert result['host_approved'] is False
    assert result['promotion_authority'] is False
    assert result['financial_authority'] is False
    assert result['prediction']['bundle_sha256'] == bundle_sha256
    assert result['prediction']['calibration_status'] == 'UNCALIBRATED'
    assert result['prediction']['model_inputs'][0]['model_id'] == MODEL_ID
    assert len(result['prediction']['model_inputs'][0]['members']) == 31


@requires_real_evidence
def test_isolated_injection_fails_closed_on_wrong_root_schema_family_or_member_count(tmp_path):
    with pytest.raises(OSError):
        harness.inject_isolated_research_candidate(private_root=LOW_ROOT, bundle_sha256=HIGH_BUNDLE)
    tmp_path.chmod(0o755)
    with pytest.raises(EvidenceError, match='PRIVATE_ARTIFACT_DIRECTORY_REQUIRED'):
        harness.inject_isolated_research_candidate(private_root=tmp_path, bundle_sha256=HIGH_BUNDLE)
    injection = harness.inject_isolated_research_candidate(private_root=HIGH_ROOT, bundle_sha256=HIGH_BUNDLE)
    r = rule('high', 'F')
    with pytest.raises(EvidenceError, match='BUNDLE_MODEL_INPUT_SET_MISMATCH'):
        wrong_model = component(r, model='not-gefs31', group='NCEP_GEFS', members=members(), target=FINAL_EXTREME)
        harness.run_isolated_shadow_prediction(injection, r, (wrong_model,), as_of=T + 110, max_source_age_seconds=120.)
    with pytest.raises(EvidenceError, match='FORECAST_FEATURE_PARENT_CONTRACT_MISMATCH'):
        short = component(r, model=MODEL_ID, group='NCEP_GEFS', members=members()[:-1], target=FINAL_EXTREME)
        harness.run_isolated_shadow_prediction(injection, r, (short,), as_of=T + 110, max_source_age_seconds=120.)
    with pytest.raises(EvidenceError, match='FORECAST_FEATURE_PARENT_CONTRACT_MISMATCH'):
        wrong_family_rule = rule('low', 'F')
        mismatched = component(wrong_family_rule, model=MODEL_ID, group='NCEP_GEFS', members=members(), target=FINAL_EXTREME)
        harness.run_isolated_shadow_prediction(injection, wrong_family_rule, (mismatched,), as_of=T + 110, max_source_age_seconds=120.)


def _synthetic_bundle(tmp_path):
    tmp_path.chmod(0o700)
    store = ArtifactStore(tmp_path)
    values = make_artifacts()
    refs = {kind: store.put_artifact(value) for kind, value in values.items()}
    key = store.put_bundle(artifacts=refs, target='FINAL_CONTRACT_PAYOUT',
                           feature_schema_sha256=values['FEATURES']['feature_schema_sha256'])
    return key


def test_isolated_injection_cannot_be_constructed_or_replaced_into_claiming_authority(tmp_path):
    key = _synthetic_bundle(tmp_path)
    injection = harness.inject_isolated_research_candidate(private_root=tmp_path, bundle_sha256=key)
    assert injection.financial_authority is False and injection.host_approved is False and injection.promotion_authority is False
    for changes in ({'financial_authority': True}, {'host_approved': True}, {'promotion_authority': True},
                     {'mode': 'V11_PAPER'}, {'status': 'APPROVED_CHAMPION'}, {'target': 'NEXT_OFFICIAL_OBSERVATION'}):
        with pytest.raises(EvidenceError):
            replace(injection, **changes)


def test_isolated_injection_requires_an_actual_pinned_bundle():
    with pytest.raises(EvidenceError, match='ISOLATED_INJECTION_PINNED_BUNDLE_REQUIRED'):
        harness.IsolatedResearchInjection(object(), 'FINAL_CONTRACT_PAYOUT')


def test_run_isolated_shadow_prediction_rejects_non_injection_argument():
    with pytest.raises(EvidenceError, match='ISOLATED_INJECTION_CANNOT_CLAIM_AUTHORITY'):
        harness.run_isolated_shadow_prediction(object(), None, (), as_of=0., max_source_age_seconds=1.)


def test_live_gefs_source_model_id_does_not_match_real_fit_candidate_model_id():
    """LIVE_INPUT_FEATURE_SCHEMA_COMPATIBILITY_CHECK, static code evidence only.

    The live GEFS collector (`gefs_sources.assemble_path`) always captures
    exactly 31 members in the rule's own unit -- both already compatible with
    this candidate's contract -- but always labels them with
    `gefs_sources.MODEL_ID`, never with this candidate's fitted `model_id`
    ('gefs31'). `predict_with_bundle`'s own `BUNDLE_MODEL_INPUT_SET_MISMATCH`
    check means a natural, unmodified live capture cannot presently satisfy
    this bundle's input-set requirement without a `model_id` remap that does
    not exist in committed code today. This records a real remaining gap; it
    does not close LIVE_INPUT_FEATURE_SCHEMA_COMPATIBILITY_CHECK.
    """
    assert gefs_sources.MODEL_ID == 'NOAA_GEFS_0P50_LINEAR_DAY_V1'
    assert gefs_sources.MODEL_ID != MODEL_ID


@requires_real_evidence
def test_natural_live_shaped_gefs_input_fails_bundle_model_input_set_mismatch_not_calibration():
    injection = harness.inject_isolated_research_candidate(private_root=HIGH_ROOT, bundle_sha256=HIGH_BUNDLE)
    r = rule('high', 'F')
    live_shaped = component(r, model=gefs_sources.MODEL_ID, group='NCEP_GEFS', members=members(), target=FINAL_EXTREME)
    with pytest.raises(EvidenceError, match='BUNDLE_MODEL_INPUT_SET_MISMATCH'):
        harness.run_isolated_shadow_prediction(injection, r, (live_shaped,), as_of=T + 110, max_source_age_seconds=120.)


_FORBIDDEN_IMPORT_ROOTS = ('production', 'host_trust', 'requests', 'http', 'urllib', 'socket', 'subprocess',
                           'pickle', 'ctypes')
_FORBIDDEN_SUBMODULES = ('polymarket_scanner.v11.model_registry', 'polymarket_scanner.v11.certification')


def _imported_names(module):
    source = Path(module.__file__).read_text()
    names = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.extend(n.name for n in node.names)
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
    return names


def test_isolated_shadow_injection_never_imports_production_host_authority_or_champion_registry():
    names = _imported_names(harness)
    roots = {n.split('.')[0] for n in names}
    assert not roots & set(_FORBIDDEN_IMPORT_ROOTS)
    assert not any(n in _FORBIDDEN_SUBMODULES for n in names)


_DECISION_SITE_MODULES = [strategy_pipeline, position_management, relative_value, basket_coordinator,
    pws_admission, source_release, maker_context, reaction_runtime, drift_runtime, risk_inputs, strategy_admission]


@pytest.mark.parametrize('module', _DECISION_SITE_MODULES)
def test_no_production_decision_site_imports_the_isolated_research_harness(module):
    names = _imported_names(module)
    assert not any('r47_isolated_shadow_injection' in str(n) for n in names)
