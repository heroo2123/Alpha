from dataclasses import asdict, replace

import pytest

from polymarket_scanner.v11 import certification as cert, model_registry
from polymarket_scanner.v11.event_risk import EventContext
from polymarket_scanner.v11.evidence import EvidenceError, ReleaseBinding, digest
from polymarket_scanner.v11.rules import RuleGuard
from polymarket_scanner.v11.strategy_admission import StrategyAdmission, SourceLease
from test_v11_certification_rules import setup, approve_fixture, observe_rule
from test_v11_model_artifacts import bundle
from test_v11_model_governance import authority, promote
from test_weather_final_gpt6_exact_replays import _event


@pytest.fixture
def admission(setup, bundle, monkeypatch):
    store, registry, scope, metadata, now = setup
    rule = observe_rule(store, RuleGuard(store), _event(station='KATL'), metadata, 'rules')
    scope = replace(scope, source_rule_family=rule.payload['source_family'], model_version='test-v1')
    updated = (store, registry, scope, metadata, now)
    manifest = approve_fixture(monkeypatch, updated, stage='PAPER', fingerprint=rule.sha256)
    state = [promote(bundle, authority.empty_state(scope.key, 'V11_PAPER'))]
    monkeypatch.setattr(model_registry, 'protected_state', lambda **kwargs: {'state': state[0], 'sha256': digest(state[0])})
    monkeypatch.setattr(model_registry, 'ApprovedArtifactReader', lambda: bundle[0])
    payload = {k: rule.payload[k] for k in ('station', 'target_date', 'family', 'unit')}
    store.capture('model', event_id=rule.payload['event_id'], kind='MODEL', provider='fixture',
                  source_identity='fixture-model', revision='run-1', observed_at=now[0]-10, issued_at=now[0]-10,
                  payload=dict(payload, rule_fingerprint=rule.sha256), evidence_class='SYNTHETIC')
    kw = dict(context=EventContext('account', metadata.city, metadata.station, rule.payload['event_id']), scope=scope,
              rule=rule, binding=ReleaseBinding('a'*40, 'b'*40, 'c'*64, bundle[3], rule.sha256), stage='PAPER',
              rule_max_age_seconds=60., source_leases=(SourceLease('model', 'MODEL', 120.),))
    return dict(store=store, registry=registry, now=now, model_state=state, bundle=bundle,
                metadata=metadata, manifest=manifest, kw=kw, monkeypatch=monkeypatch)


def verify(admission, record_id='pin', **changes):
    kw = admission['kw']
    return StrategyAdmission(admission['store']).revalidate(record_id, **{
        'context':kw['context'], 'rule':kw['rule'], 'binding':asdict(kw['binding']),
        'strategies':(kw['scope'].strategy,), **changes})


def test_pin_requires_current_scoped_review_rule_and_model_bundle(admission):
    pin = StrategyAdmission(admission['store']).pin('pin', **admission['kw'])
    result = verify(admission)
    assert result['strategy'] == 'FUTURE_FORECAST' and result['model_epoch'] == 1
    assert result['valid_until'] == 1060.
    assert ('OFFICIAL_OBSERVATION', admission['kw']['context'].event_id, 0) in [tuple(h) for h in result['heads']]
    assert not result['financial_authority'] and not pin['body']['financial_authority']


def test_missing_protected_review_cannot_be_replaced_by_local_pass_claims(admission):
    admission['monkeypatch'].setattr(cert, 'protected_reviews', lambda: {'reviews': []})
    with pytest.raises(EvidenceError, match='NO_REVIEWED_CERTIFICATION'):
        StrategyAdmission(admission['store']).pin('pin', **admission['kw'])


def test_new_official_observation_invalidates_even_an_absent_source_head(admission):
    store = admission['store']; kw = admission['kw']
    StrategyAdmission(store).pin('pin', **kw)
    store.capture('official', event_id=kw['context'].event_id, kind='OFFICIAL_OBSERVATION', provider='fixture',
                  source_identity='KATL', revision='1', observed_at=admission['now'][0], payload={'station':'KATL'},
                  evidence_class='SYNTHETIC')
    with pytest.raises(EvidenceError, match='SOURCE_CHANGED'):
        verify(admission)


def test_new_model_epoch_or_monotonic_overlay_invalidates_pin(admission):
    StrategyAdmission(admission['store']).pin('pin', **admission['kw'])
    state = admission['model_state'][0]
    admission['model_state'][0] = authority.transition(state, action='DEMOTE', expected_state_sha256=digest(state),
                                                     now=21., reason='TEST_DRIFT', size_multiplier=.5)
    with pytest.raises(EvidenceError, match='MODEL_MANUAL_REVIEW'):
        verify(admission)


def test_station_demotion_rule_drift_and_expiry_never_auto_restore(admission):
    kw = admission['kw']; store = admission['store']
    StrategyAdmission(store).pin('pin', **kw)
    admission['registry'].demote('demote', kw['scope'], state='SOURCE_UNAVAILABLE', reason='TEST', evidence_ids=('model',))
    with pytest.raises(EvidenceError, match='NEW_REVIEW'):
        verify(admission)
    admission['now'][0] += 61
    with pytest.raises(EvidenceError, match='ADMISSION_EXPIRED'):
        verify(admission)


@pytest.mark.parametrize('change', [
    {'family':'LOW'}, {'station':'OTHER'}, {'source_rule_family':'PROXY'}, {'strategy':'PWS_OBSERVATION_LEAD'},
])
def test_scope_and_source_dependencies_cannot_be_generalized(admission, change):
    kw = admission['kw']
    with pytest.raises(EvidenceError):
        StrategyAdmission(admission['store']).pin('pin', **dict(kw, scope=replace(kw['scope'], **change)))


def test_model_run_age_unknown_cannot_use_receipt_time(admission):
    store=admission['store']; kw=admission['kw']; original=store.get('model')['body']['payload']
    store.capture('unknown-run', event_id=kw['context'].event_id, kind='MODEL', provider='fixture', source_identity='model',
                  revision='1', observed_at=admission['now'][0], payload=original, evidence_class='SYNTHETIC')
    with pytest.raises(EvidenceError, match='AGE_UNKNOWN'):
        StrategyAdmission(store).pin('pin', **dict(kw, source_leases=(SourceLease('unknown-run','MODEL',120.),)))


def pws_scope(admission):
    store=admission['store']; kw=admission['kw']; now=admission['now']; scope=replace(kw['scope'],strategy='PWS_OBSERVATION_LEAD')
    updated=(store,admission['registry'],scope,admission['metadata'],now)
    approve_fixture(admission['monkeypatch'],updated,stage='PAPER',fingerprint=kw['rule'].sha256,prefix='pws:')
    admission['model_state'][0]=promote(admission['bundle'],authority.empty_state(scope.key,'V11_PAPER'))
    store.capture('official',event_id=kw['context'].event_id,kind='OFFICIAL_OBSERVATION',provider='fixture',source_identity='KATL',
                  revision='1',observed_at=now[0]-5,payload={'station':'KATL','source_role':'PROXY_NOT_SETTLEMENT'},evidence_class='SYNTHETIC')
    qc=dict(as_of=now[0],health='HEALTHY',station='KATL',official_metadata_fingerprint=admission['metadata'].fingerprint,
             observation_age_seconds=[5.,8.],settlement_authority=False)
    store.capture('pws',event_id=kw['context'].event_id,kind='PWS_OBSERVATION',provider='ALPHA_PWS_QC',source_identity='KATL',
                  revision='1',payload=qc,evidence_class='SYNTHETIC')
    kw=dict(kw,scope=scope,source_leases=(SourceLease('model','MODEL',120.),SourceLease('official','OFFICIAL',60.),SourceLease('pws','PWS',30.)))
    admission['kw']=kw
    return kw


def test_pws_pin_is_preconfirmation_permission_data_not_settlement_or_exit_value(admission):
    kw=pws_scope(admission); gate=StrategyAdmission(admission['store'])
    result=gate.pin('pin',**kw)['body']['details']['assessment']
    assert result['strategy']=='PWS_OBSERVATION_LEAD'
    assert 'expected_payout' not in result and 'executable_exit_value' not in result
    assert not result['financial_authority']
    store=admission['store']; now=admission['now'][0]
    store.capture('confirmation',event_id=kw['context'].event_id,kind='OFFICIAL_OBSERVATION',provider='fixture',
                  source_identity='KATL',revision='2',observed_at=now,payload={'station':'KATL'},evidence_class='SYNTHETIC')
    with pytest.raises(EvidenceError,match='NEW_OFFICIAL'):
        verify(admission)


def test_pws_sensor_age_cannot_be_refreshed_by_recomputing_features(admission):
    kw=pws_scope(admission); store=admission['store']
    p=store.get('pws')['body']['payload'];p['observation_age_seconds']=[31.]
    store.capture('old-pws',event_id=kw['context'].event_id,kind='PWS_OBSERVATION',provider='ALPHA_PWS_QC',
                  source_identity='KATL',revision='2',payload=p,evidence_class='SYNTHETIC')
    leases=kw['source_leases'][:-1]+(SourceLease('old-pws','PWS',30.),)
    with pytest.raises(EvidenceError,match='FRESH_QC'):
        StrategyAdmission(store).pin('pin',**dict(kw,source_leases=leases))


def test_source_arrival_racing_pin_is_rejected_atomically(admission,monkeypatch):
    store=admission['store'];kw=admission['kw'];original=store.audit
    def racing(record_id,**args):
        if record_id=='pin':
            store.capture('arrival',event_id=kw['context'].event_id,kind='OFFICIAL_OBSERVATION',provider='fixture',
                          source_identity='KATL',revision='2',observed_at=admission['now'][0],payload={'station':'KATL'},evidence_class='SYNTHETIC')
        return original(record_id,**args)
    monkeypatch.setattr(store,'audit',racing)
    with pytest.raises(EvidenceError,match='GUARDED_STATE_CHANGED'):
        StrategyAdmission(store).pin('pin',**kw)


def test_an_older_fresh_model_revision_cannot_hide_a_new_received_run(admission):
    store=admission['store'];kw=admission['kw'];old=store.get('model')['body']
    store.capture('new-model',event_id=kw['context'].event_id,kind='MODEL',provider=old['provider'],
                  source_identity=old['source_identity'],revision='run-2',issued_at=admission['now'][0],
                  payload=old['payload'],evidence_class='SYNTHETIC')
    with pytest.raises(EvidenceError,match='CURRENT_SOURCE_REVISION'):
        StrategyAdmission(store).pin('pin',**kw)


def test_model_for_another_contract_date_is_not_an_available_forecast(admission):
    store=admission['store'];kw=admission['kw'];p=store.get('model')['body']['payload'];p['target_date']='2027-01-01'
    store.capture('wrong-date',event_id=kw['context'].event_id,kind='MODEL',provider='fixture',source_identity='other',
                  revision='run-1',issued_at=admission['now'][0],payload=p,evidence_class='SYNTHETIC')
    with pytest.raises(EvidenceError,match='FORECAST_TARGET'):
        StrategyAdmission(store).pin('pin',**dict(kw,source_leases=(SourceLease('wrong-date','MODEL',120.),)))
