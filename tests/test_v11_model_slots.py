from copy import deepcopy
from types import SimpleNamespace
import os

import pytest

from polymarket_scanner.v11 import model_registry as registry
from polymarket_scanner.v11.evidence import EvidenceError, digest
from test_v11_model_artifacts import bundle
from test_v11_model_governance import authority, promote


@pytest.fixture
def slots(bundle,tmp_path_factory,monkeypatch):
    root=tmp_path_factory.mktemp('model-slots')/'scopes'
    for mode in ('V11_PAPER','V11_SHADOW'):
        (root/mode).mkdir(mode=0o700,parents=True)
    monkeypatch.setattr(registry,'SCOPED_STATE_ROOT',root)
    monkeypatch.setattr(authority,'SCOPED_STATE_ROOT',root)
    # Isolated byte/identity mechanics only. Production custody stays enforced;
    # separate negative tests exercise writable-parent and non-root rejection.
    monkeypatch.setattr(registry,'_root_custody',lambda p:p.lstat())
    monkeypatch.setattr(authority,'custody',lambda p,**kwargs:p.lstat())
    monkeypatch.setattr(registry,'ApprovedArtifactReader',lambda:bundle[0])
    states={}
    def install(scope,mode='V11_PAPER',state=None):
        state=state or promote(bundle,authority.empty_state(scope,mode))
        path=registry.state_path(scope_key=scope,mode=mode)
        path.write_text(authority.canonical({'state':state,'sha256':digest(state)}))
        path.chmod(0o600)
        states[(scope,mode)]=state
        return path
    return dict(root=root,states=states,install=install,bundle=bundle)


def root_os(monkeypatch):
    fixture_os=SimpleNamespace(**vars(os))
    fixture_os.geteuid=lambda:0
    def fstat(fd):
        values=list(os.fstat(fd)); values[4]=0
        return os.stat_result(values)
    fixture_os.fstat=fstat
    fixture_os.fchown=lambda fd,uid,gid:None
    monkeypatch.setattr(authority,'os',fixture_os)
    monkeypatch.setattr(authority,'time',SimpleNamespace(time=lambda:21.))


def test_reader_and_standalone_authority_select_identical_scope_mode_slots():
    paths=[]
    for scope,mode in [('1'*64,'V11_PAPER'),('2'*64,'V11_PAPER'),('1'*64,'V11_SHADOW')]:
        a=registry.state_path(scope_key=scope,mode=mode)
        b=authority.state_path(scope_key=scope,mode=mode)
        assert a==b and a.name==scope+'.json' and a.parent.name==mode
        paths.append(a)
    assert len(set(paths))==3


@pytest.mark.parametrize('scope,mode',[(None,'V11_PAPER'),('1'*64,None),('1'*64,'LIVE'),
                                      ('../state','V11_PAPER'),('A'*64,'V11_PAPER'),('1'*64,'../V11_PAPER')])
def test_arbitrary_paths_or_financial_modes_are_not_state_selectors(scope,mode):
    with pytest.raises(EvidenceError):
        registry.state_path(scope_key=scope,mode=mode)
    with pytest.raises(authority.AuthorityError):
        authority.state_path(scope_key=scope,mode=mode)


def test_missing_scoped_state_never_falls_back_to_valid_legacy_state(slots,monkeypatch,tmp_path):
    state=promote(slots['bundle'])
    legacy=tmp_path/'state.json'
    legacy.write_text(authority.canonical({'state':state,'sha256':digest(state)}))
    monkeypatch.setattr(registry,'STATE_PATH',legacy)
    assert registry.protected_state()['state']==state
    with pytest.raises(EvidenceError,match='PROTECTED_MODEL_STATE_UNAVAILABLE'):
        registry.ActiveModelRegistry().pin(scope_key='1'*64,mode='V11_PAPER')
    assert not registry.state_path(scope_key='1'*64,mode='V11_PAPER').exists()


def test_valid_state_copied_to_another_scope_or_mode_is_rejected(slots):
    one=slots['install']('1'*64)
    wrong_scope=registry.state_path(scope_key='2'*64,mode='V11_PAPER')
    wrong_mode=registry.state_path(scope_key='1'*64,mode='V11_SHADOW')
    for path in (wrong_scope,wrong_mode):
        path.write_bytes(one.read_bytes())
    for scope,mode in [('2'*64,'V11_PAPER'),('1'*64,'V11_SHADOW')]:
        with pytest.raises(EvidenceError,match='SLOT_IDENTITY_MISMATCH'):
            registry.ActiveModelRegistry().pin(scope_key=scope,mode=mode)


def test_demotion_of_one_slot_does_not_demote_or_refresh_another(slots):
    slots['install']('1'*64); slots['install']('2'*64); slots['install']('1'*64,'V11_SHADOW')
    active=registry.ActiveModelRegistry()
    first=active.pin(scope_key='1'*64,mode='V11_PAPER')
    second=active.pin(scope_key='2'*64,mode='V11_PAPER')
    shadow=active.pin(scope_key='1'*64,mode='V11_SHADOW')
    state=slots['states'][('1'*64,'V11_PAPER')]
    demoted=authority.transition(state,action='DEMOTE',expected_state_sha256=digest(state),now=21.,reason='TEST_DRIFT',size_multiplier=.5)
    slots['install']('1'*64,state=demoted)
    assert not active.revalidate(first)['passed']
    assert active.revalidate(second)['passed'] and active.revalidate(shadow)['passed']
    assert second.bundle.sha256==first.bundle.sha256==shadow.bundle.sha256


def test_different_observation_and_payout_champions_can_coexist_as_nonfinancial_states(slots):
    bundle=slots['bundle']; objects,values,refs,key=bundle
    values=deepcopy(values)
    for artifact in values.values():
        artifact['target']='NEXT_OFFICIAL_OBSERVATION'
    values['CALIBRATION']['parameters']['probability_artifact_sha256']=digest(values['PROBABILITY'])
    refs={k:objects.put_artifact(v) for k,v in values.items()}
    observed_key=objects.put_bundle(artifacts=refs,target='NEXT_OFFICIAL_OBSERVATION',
                                   feature_schema_sha256=values['FEATURES']['feature_schema_sha256'])
    obs_bundle=(objects,values,refs,observed_key)
    slots['install']('1'*64)
    observed=promote(obs_bundle,authority.empty_state('2'*64,'V11_PAPER'))
    slots['install']('2'*64,state=observed)
    active=registry.ActiveModelRegistry()
    assert active.pin(scope_key='1'*64,mode='V11_PAPER').bundle.payload['bundle']['target']=='FINAL_CONTRACT_PAYOUT'
    assert active.pin(scope_key='2'*64,mode='V11_PAPER').bundle.payload['bundle']['target']=='NEXT_OFFICIAL_OBSERVATION'
    assert not observed['financial_authority']


def test_root_publisher_mutates_only_selected_custodied_slot(slots,monkeypatch):
    first=slots['install']('1'*64); second=slots['install']('2'*64); shadow=slots['install']('1'*64,'V11_SHADOW')
    preserved={p:p.read_bytes() for p in (second,shadow)}
    root_os(monkeypatch)
    state=slots['states'][('1'*64,'V11_PAPER')]
    result=authority.publish(action='DEMOTE',expected_state_sha256=digest(state),reason='TEST_DRIFT',size_multiplier=.5,
                             scope_key='1'*64,mode='V11_PAPER')
    assert result['epoch']==2 and not result['financial_authority']
    assert all(p.read_bytes()==raw for p,raw in preserved.items())
    assert registry.protected_state(scope_key='1'*64,mode='V11_PAPER')['state']['overlay']['size_multiplier']==.5
    assert not list(first.parent.glob('model-state-*'))


def test_publisher_does_not_initialize_missing_scope_or_relabel_wrong_state(slots,monkeypatch):
    root_os(monkeypatch)
    with pytest.raises(FileNotFoundError):
        authority.publish(action='DEMOTE',expected_state_sha256='0'*64,reason='TEST',size_multiplier=0.,scope_key='1'*64,mode='V11_PAPER')
    one=slots['install']('1'*64)
    wrong=registry.state_path(scope_key='2'*64,mode='V11_PAPER'); wrong.write_bytes(one.read_bytes())
    original=wrong.read_bytes()
    with pytest.raises(authority.AuthorityError,match='SLOT_IDENTITY_MISMATCH'):
        authority.publish(action='DEMOTE',expected_state_sha256=digest(slots['states'][('1'*64,'V11_PAPER')]),
                          reason='TEST',size_multiplier=0.,scope_key='2'*64,mode='V11_PAPER')
    assert wrong.read_bytes()==original


def test_scoped_path_still_requires_protected_parent_custody(tmp_path,monkeypatch):
    monkeypatch.setattr(registry,'SCOPED_STATE_ROOT',tmp_path)
    path=registry.state_path(scope_key='1'*64,mode='V11_PAPER'); path.parent.mkdir(); path.write_text('{}')
    with pytest.raises(EvidenceError,match='CUSTODY'):
        registry.protected_state(scope_key='1'*64,mode='V11_PAPER')
    with pytest.raises(authority.AuthorityError,match='PARENT_CUSTODY'):
        authority.custody(path)
