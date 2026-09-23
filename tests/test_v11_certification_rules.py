import copy
from dataclasses import replace
import json

import pytest

from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore, digest
from polymarket_scanner.v11 import certification as cert
from polymarket_scanner.v11.rules import RuleGuard, fingerprint_event
from test_weather_final_gpt6_exact_replays import _event


@pytest.fixture
def setup(tmp_path):
    tmp_path.chmod(0o700)
    now = [1000.0]
    store = EvidenceStore(tmp_path/'evidence.sqlite', 'V11_PAPER', clock=lambda:now[0])
    registry = cert.StationRegistry(store)
    scope = cert.CapabilityScope('KATL','HIGH','NWS_WRH','test-model-v1','24_48_HOURS',
                                 'FUTURE_FORECAST','AUTUMN','DAY')
    payload = {'station':'KATL','latitude':33.63,'longitude':-84.44,'elevation_m':313}
    raw = store.capture('metadata-raw', event_id='station:KATL',kind='STATION_METADATA',
                        provider='fixture',source_identity='KATL',revision='1',payload=payload,
                        evidence_class='SYNTHETIC')
    metadata = cert.StationMetadata('KATL','Atlanta','US',33.63,-84.44,313,'America/New_York',
                                     'NOAA_WRH',('NOAA',),('GEFS',),digest(payload),now[0])
    registry.observe('metadata', metadata, raw_evidence_id=raw['id'])
    return store,registry,scope,metadata,now


def approve_fixture(monkeypatch, setup, *, stage='CANARY_ELIGIBLE', fingerprint='a'*64):
    store, registry, scope, metadata, now = setup
    required = cert.BASE_CAPABILITIES | cert.STRATEGY_CAPABILITIES[scope.strategy]
    proofs = {}
    last = None
    for cap in sorted(required):
        last = registry.proof('proof:'+cap,scope,capability=cap,metadata_fingerprint=metadata.fingerprint,
                              rule_fingerprint=fingerprint,evidence_ids=('metadata-raw',),result='PASS',
                              checker_version='SYNTHETIC_TEST_CHECKER')
        proofs[cap] = {'id':last['id'],'sha256':last['sha256']}
    review = {'scope_key':scope.key,'namespace':'V11_PAPER','stage':stage,
              'metadata_fingerprint':metadata.fingerprint,'rule_fingerprint':fingerprint,
              'reviewer':'synthetic-reviewer','review_id':'synthetic-review-1','approved_at':now[0],
              'expires_at':now[0]+100,'reviewed_through_seq':last['seq'],'capability_proofs':proofs}
    manifest = {'version':'alpha_v11_certification_reviews_v1','reviews':[review]}
    monkeypatch.setattr(cert,'protected_reviews',lambda:copy.deepcopy(manifest))
    return manifest


def assess(setup, stage='CANARY_ELIGIBLE', fingerprint='a'*64):
    _,registry,scope,metadata,_ = setup
    return registry.assess(scope,stage=stage,metadata_fingerprint=metadata.fingerprint,
                           rule_fingerprint=fingerprint)


def test_discovery_and_proof_claims_cannot_self_approve(setup, monkeypatch):
    monkeypatch.setattr(cert,'protected_reviews',lambda:{'reviews':[]})
    assert not assess(setup)['eligible']
    store, registry,scope,metadata,_ = setup
    registry.proof('claimed',scope,capability='IDENTITY',metadata_fingerprint=metadata.fingerprint,
                   rule_fingerprint='a'*64,evidence_ids=('metadata-raw',),result='PASS',checker_version='test')
    assert not assess(setup)['eligible']


def test_canary_eligibility_is_separate_from_real_canary_verification(setup, monkeypatch):
    approve_fixture(monkeypatch,setup)
    result = assess(setup)
    assert result['eligible'] and not result['financial_authority'] and not result['activation_authorized']
    assert 'CANARY_EXECUTION_VERIFIED' not in result['required_capabilities']
    assert not assess(setup,'CANARY_VERIFIED')['eligible']
    assert not assess(setup,'LIVE_LIMITED')['eligible']


@pytest.mark.parametrize('field,value', [('family','LOW'),('model_version','new-model'),
    ('horizon','0_6_HOURS'),('strategy','SAME_DAY_LATE_LOCK'),('season','WINTER'),('time_of_day','NIGHT')])
def test_scope_does_not_generalize(setup,monkeypatch,field,value):
    approve_fixture(monkeypatch,setup)
    store,registry,scope,metadata,now = setup
    changed = replace(scope,**{field:value})
    assert not registry.assess(changed,stage='CANARY_ELIGIBLE',metadata_fingerprint=metadata.fingerprint,
                               rule_fingerprint='a'*64)['eligible']


def test_metadata_drift_and_reversion_need_new_review(setup,monkeypatch):
    approve_fixture(monkeypatch,setup)
    store,registry,scope,metadata,now = setup
    now[0] += 1
    shifted = replace(metadata,elevation_m=314)
    registry.observe('relocation',shifted,raw_evidence_id='metadata-raw')
    assert not assess(setup)['eligible']
    now[0] += 1
    registry.observe('revert',metadata,raw_evidence_id='metadata-raw')
    assert assess(setup)['reason'] == 'QUARANTINE_REQUIRES_NEW_REVIEW'
    assert len(store.records(kind='REGISTRY',event_id='station:KATL')) >= 3


def test_new_failure_demotion_and_expiry_do_not_clear_automatically(setup,monkeypatch):
    approve_fixture(monkeypatch,setup)
    store,registry,scope,metadata,now = setup
    registry.proof('new-fail',scope,capability='SOURCE_INTEGRITY',metadata_fingerprint=metadata.fingerprint,
                   rule_fingerprint='a'*64,evidence_ids=('metadata-raw',),result='FAIL',checker_version='test')
    assert assess(setup)['reason'] == 'NEW_CAPABILITY_FAILURE_REQUIRES_REVIEW'
    registry.demote('demotion',scope,state='SOURCE_UNAVAILABLE',reason='STALE_SOURCE',evidence_ids=('metadata-raw',))
    assert not assess(setup)['eligible']
    now[0] += 101
    assert not assess(setup)['eligible']


def test_missing_or_tampered_proof_stays_gated(setup,monkeypatch):
    manifest = approve_fixture(monkeypatch,setup)
    manifest['reviews'][0]['capability_proofs']['ACCOUNTING']['sha256'] = 'b'*64
    assert assess(setup)['reason'] == 'CAPABILITY_PROOF_BINDING_INVALID'


def test_synthetic_evidence_cannot_become_canary_execution_verification(setup):
    _,registry,scope,metadata,_=setup
    with pytest.raises(EvidenceError,match='REAL_EXECUTION'):
        registry.proof('fake-live',scope,capability='CANARY_EXECUTION_VERIFIED',
            metadata_fingerprint=metadata.fingerprint,rule_fingerprint='a'*64,
            evidence_ids=('metadata-raw',),result='PASS',checker_version='test')


def test_root_review_custody_rejects_writable_parent(tmp_path,monkeypatch):
    path = tmp_path/'reviews.json'
    path.write_text(json.dumps({'version':'alpha_v11_certification_reviews_v1','reviews':[]}))
    monkeypatch.setattr(cert,'REVIEW_PATH',path)
    # /tmp (or another writable ancestor) cannot become a trusted approval path.
    with pytest.raises(EvidenceError,match='CUSTODY'):
        cert.protected_reviews()


def observe_rule(store, guard, event, metadata, label):
    f = fingerprint_event(event,station_timezone=metadata.timezone,metadata_fingerprint=metadata.fingerprint)
    raw = store.capture(label+':raw',event_id=f.payload['event_id'],kind='RULES',provider='fixture',
                         source_identity='gamma-event',revision=label,payload={'event':event},evidence_class='SYNTHETIC')
    guard.observe(label,f,raw_evidence_id=raw['id'])
    return f


def test_universal_rule_binds_tokens_metadata_and_semantics_not_volume(setup):
    store,_,_,metadata,now = setup
    event = _event(station='KATL')
    guard = RuleGuard(store)
    f = observe_rule(store,guard,event,metadata,'original')
    event['volume'] = 999
    stable = observe_rule(store,guard,event,metadata,'cosmetic')
    assert stable.sha256 == f.sha256
    assert stable.source_event_sha256 != f.source_event_sha256
    assert guard.revalidate(f.payload['event_id'],f.sha256,max_age_seconds=10)['passed']
    changed = copy.deepcopy(event)
    changed['markets'][0]['clobTokenIds'] = '["different-yes","different-no"]'
    drift = observe_rule(store,guard,changed,metadata,'token-change')
    assert drift.sha256 != f.sha256
    assert not guard.revalidate(f.payload['event_id'],drift.sha256,max_age_seconds=10)['passed']
    observe_rule(store,guard,event,metadata,'token-revert')
    assert guard.revalidate(f.payload['event_id'],f.sha256,max_age_seconds=10)['reason'] == 'RULE_DRIFT_QUARANTINED'


def test_rule_evidence_cannot_be_forged_by_matching_hash_alone(setup):
    store,_,_,metadata,_ = setup
    original = _event(station='KATL')
    f = fingerprint_event(original,station_timezone=metadata.timezone,metadata_fingerprint=metadata.fingerprint)
    changed = copy.deepcopy(original);changed['markets'][0]['clobTokenIds']='["other-yes","other-no"]'
    raw = store.capture('unbound',event_id=f.payload['event_id'],kind='RULES',provider='fixture',
                        source_identity='gamma',revision='1',payload={'event':changed},evidence_class='SYNTHETIC')
    with pytest.raises(EvidenceError,match='RAW_BINDING'):
        RuleGuard(store).observe('bad',f,raw_evidence_id=raw['id'])


def test_audit_compare_and_swap_rejects_stale_writer(setup):
    store,*_ = setup
    first = store.audit('first',event_id='event',kind='RULE_STATE',details={'state':'DRIFT'},expected_previous_seq=0)
    with pytest.raises(EvidenceError,match='STATE_CHANGED'):
        store.audit('stale',event_id='event',kind='RULE_STATE',details={'state':'CLEAR'},expected_previous_seq=0)
    assert store.get(first['id'])['body']['details']['state'] == 'DRIFT'


def test_rule_recertification_requires_protected_review_after_drift(setup,monkeypatch):
    store,registry,scope,metadata,now = setup
    guard=RuleGuard(store);event=_event(station='KATL')
    observe_rule(store,guard,event,metadata,'before')
    event['markets'][0]['clobTokenIds']='["new-yes","new-no"]'
    f=observe_rule(store,guard,event,metadata,'after')
    monkeypatch.setattr(cert,'protected_reviews',lambda:{'reviews':[]})
    with pytest.raises(EvidenceError,match='REVIEW_REQUIRED'):
        guard.recertify('unapproved',event_id=f.payload['event_id'],registry=registry,scope=scope,stage='PAPER')
    approve_fixture(monkeypatch,setup,stage='PAPER',fingerprint=f.sha256)
    guard.recertify('reviewed',event_id=f.payload['event_id'],registry=registry,scope=scope,stage='PAPER')
    assert guard.revalidate(f.payload['event_id'],f.sha256,max_age_seconds=10)['passed']
