import copy
from dataclasses import replace
import importlib.util
from pathlib import Path

import pytest

from polymarket_scanner.v11 import model_registry as registry
from polymarket_scanner.v11.evidence import EvidenceError, digest
from test_v11_model_artifacts import bundle


spec=importlib.util.spec_from_file_location('v11_model_authority',
    Path(__file__).resolve().parents[1]/'host_trust/v11-model-authority/authority.py')
authority=importlib.util.module_from_spec(spec)
spec.loader.exec_module(authority)


def review(bundle, state, *, action='PROMOTE', review_id='review-1', size=1.):
    store,_,refs,key=bundle
    manifest=store.read(key)
    return {'review_id':review_id,'reviewer':'synthetic-independent-reviewer','action':action,
            'scope_key':state['scope_key'],'mode':state['mode'],'expected_epoch':state['epoch'],
            'parent_bundle_sha256':state['active_bundle_sha256'],'candidate_bundle_sha256':key,
            'approved_at':10.,'expires_at':100.,'artifact_refs':refs,'runtime_contract':manifest['runtime_contract'],
            'feature_schema_sha256':manifest['feature_schema_sha256'],'target':manifest['target'],
            'size_multiplier':size,'approval_kind':'NONFINANCIAL_MODEL_ONLY'}


def promote(bundle, state=None, **changes):
    state=state or authority.empty_state('1'*64,'V11_PAPER')
    r=review(bundle,state,**changes)
    return authority.transition(state,action='PROMOTE',expected_state_sha256=digest(state),now=20.,
                                reason='SYNTHETIC_TEST',review=r,requested_bundle=bundle[3])


def test_no_financial_mode_can_be_created():
    for mode in ('LIVE','LIVE_LIMITED','V10_CONTROL'):
        with pytest.raises(authority.AuthorityError,match='FINANCIAL_MODE_FORBIDDEN'):
            authority.empty_state('1'*64,mode)


def test_hash_alone_is_not_model_approval(bundle):
    state=authority.empty_state('1'*64,'V11_PAPER')
    with pytest.raises(authority.AuthorityError,match='EXPLICIT_MODEL_REVIEW_REQUIRED'):
        authority.transition(state,action='PROMOTE',expected_state_sha256=digest(state),now=20.,
                              reason='NO_REVIEW',requested_bundle=bundle[3])


@pytest.mark.parametrize('field,value',[('scope_key','2'*64),('mode','V11_SHADOW'),('expected_epoch',1),
    ('parent_bundle_sha256','3'*64),('expires_at',19.),('approval_kind','FINANCIAL'),('action','ROLLBACK')])
def test_review_must_bind_exact_parent_epoch_scope_mode_and_time(bundle,field,value):
    state=authority.empty_state('1'*64,'V11_PAPER')
    r=review(bundle,state)
    r[field]=value
    with pytest.raises(authority.AuthorityError,match='SCOPE_PARENT_OR_TIME'):
        authority.transition(state,action='PROMOTE',expected_state_sha256=digest(state),now=20.,
                              reason='TEST',review=r,requested_bundle=bundle[3])
    assert state['active_bundle_sha256'] is None and state['epoch']==0


def test_changed_state_cannot_be_silently_promoted(bundle):
    state=authority.empty_state('1'*64,'V11_PAPER')
    with pytest.raises(authority.AuthorityError,match='STATE_CHANGED_RECOMPUTE'):
        authority.transition(state,action='PROMOTE',expected_state_sha256='0'*64,now=20.,
                              reason='STALE_INTENT',review=review(bundle,state),requested_bundle=bundle[3])


def test_demotion_never_mutates_champion_or_automatically_restores(bundle):
    state=promote(bundle)
    reduced=authority.transition(state,action='DEMOTE',expected_state_sha256=digest(state),now=21.,
                                  reason='SOURCE_DRIFT',size_multiplier=.25)
    assert reduced['active_bundle_sha256']==state['active_bundle_sha256']
    assert reduced['overlay']=={'size_multiplier':.25,'require_manual_review':True}
    assert state['overlay']['size_multiplier']==1
    with pytest.raises(authority.AuthorityError,match='AUTOMATIC_RECOVERY_FORBIDDEN'):
        authority.transition(reduced,action='DEMOTE',expected_state_sha256=digest(reduced),now=22.,
                              reason='ATTEMPTED_REARM',size_multiplier=.5)
    restored=authority.transition(reduced,action='RESTORE_OVERLAY',expected_state_sha256=digest(reduced),now=23.,
        reason='REVIEWED_RECOVERY',review=review(bundle,reduced,action='RESTORE_OVERLAY',review_id='restore',size=.5))
    assert restored['overlay']=={'size_multiplier':.5,'require_manual_review':False}
    assert len(restored['events'])==3


def new_bundle(bundle):
    store,values,refs,key=bundle
    values=copy.deepcopy(values)
    values['PROBABILITY']['parameters']['models'][0]['bias']=1
    refs={**refs,'PROBABILITY':store.put_artifact(values['PROBABILITY'])}
    values['CALIBRATION']['parameters']['probability_artifact_sha256']=refs['PROBABILITY']
    refs['CALIBRATION']=store.put_artifact(values['CALIBRATION'])
    new=store.put_bundle(artifacts=refs,target='FINAL_CONTRACT_PAYOUT',
                         feature_schema_sha256=values['FEATURES']['feature_schema_sha256'])
    return store,values,refs,new


def test_promotion_and_rollback_preserve_overlay_and_past_model_identity(bundle):
    first=promote(bundle)
    reduced=authority.transition(first,action='DEMOTE',expected_state_sha256=digest(first),now=20.,
                                  reason='DATA_DRIFT',size_multiplier=.5)
    newer=new_bundle(bundle)
    second=promote(newer,reduced,review_id='review-2')
    assert second['previous_bundle_sha256']==bundle[3]
    assert second['overlay']==reduced['overlay']
    rolled=authority.transition(second,action='ROLLBACK',expected_state_sha256=digest(second),now=21.,
        reason='REVIEWED_ROLLBACK',review=review(bundle,second,action='ROLLBACK',review_id='review-3'),
        requested_bundle=bundle[3])
    assert rolled['active_bundle_sha256']==bundle[3]
    assert rolled['overlay']==reduced['overlay']
    assert first['events'][0]['active_bundle_sha256']==bundle[3]
    assert not rolled['financial_authority']


def test_wrong_reviewed_preimage_cannot_pin_a_different_component(bundle):
    state=authority.empty_state('1'*64,'V11_PAPER')
    r=review(bundle,state)
    r['artifact_refs']={**r['artifact_refs'],'EXECUTION_COST':'f'*64}
    with pytest.raises(authority.AuthorityError,match='PREIMAGE_MISMATCH'):
        authority.transition(state,action='PROMOTE',expected_state_sha256=digest(state),now=20.,reason='TEST',
                              review=r,requested_bundle=bundle[3])


def test_history_corruption_is_not_silently_repaired(bundle):
    state=promote(bundle)
    state['events'][0]['active_bundle_sha256']='f'*64
    with pytest.raises(authority.AuthorityError,match='CURRENT_HISTORY_MISMATCH'):
        authority.validate_state(state)


def test_missing_owner_commissioned_state_and_writable_parent_fail(tmp_path,monkeypatch):
    monkeypatch.setattr(registry,'STATE_PATH',tmp_path/'state.json')
    with pytest.raises(EvidenceError): registry.protected_state()
    with pytest.raises(authority.AuthorityError,match='PARENT_CUSTODY'):
        authority.custody(tmp_path/'state.json')


def test_runtime_pins_one_epoch_and_revalidates_after_change(bundle,monkeypatch):
    state=[promote(bundle)]
    monkeypatch.setattr(registry,'protected_state',lambda **kwargs:{'state':state[0],'sha256':digest(state[0])})
    monkeypatch.setattr(registry,'ApprovedArtifactReader',lambda:bundle[0])
    active=registry.ActiveModelRegistry()
    pinned=active.pin(scope_key='1'*64,mode='V11_PAPER')
    assert active.revalidate(pinned)['passed']
    assert not active.revalidate(replace(pinned,size_multiplier=2.))['passed']
    state[0]=authority.transition(state[0],action='DEMOTE',expected_state_sha256=digest(state[0]),now=21.,
                                  reason='STALE_SOURCE',size_multiplier=0.)
    assert active.revalidate(pinned)['reason']=='MODEL_EPOCH_CHANGED_RECOMPUTE'
    newpin=active.pin(scope_key='1'*64,mode='V11_PAPER')
    assert active.revalidate(newpin)['reason']=='MODEL_MANUAL_REVIEW'
    assert pinned.bundle.sha256==newpin.bundle.sha256
    with pytest.raises(EvidenceError,match='SCOPE_OR_LEDGER_MISMATCH'):
        active.pin(scope_key='1'*64,mode='V11_SHADOW')


def test_approved_reader_exposes_no_pointer_or_artifact_writer():
    reader=object.__new__(registry.ApprovedArtifactReader)
    with pytest.raises(EvidenceError,match='CANNOT_WRITE'):
        reader._write({})


def test_root_publisher_does_not_import_candidate_code_or_use_network():
    import ast
    source=Path(authority.__file__).read_text()
    imports=[]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node,ast.Import): imports.extend(n.name for n in node.names)
        if isinstance(node,ast.ImportFrom): imports.append(node.module)
    assert all(not str(name).startswith(('polymarket_scanner','http','requests','socket','pickle','subprocess')) for name in imports)


@pytest.mark.parametrize('failure_stage',['BEFORE_RENAME','AFTER_RENAME',None])
def test_atomic_publisher_interruption_has_one_reconcilable_epoch(bundle,tmp_path,monkeypatch,failure_stage):
    # Filesystem crash mechanics only; custody is separately tested above.
    # This synthetic test does not install or approve any production manifest.
    import os
    from types import SimpleNamespace
    state=promote(bundle)
    path=tmp_path/'state.json'
    path.write_text(authority.canonical({'state':state,'sha256':digest(state)}))
    path.chmod(0o600)
    monkeypatch.setattr(authority,'STATE',path)
    monkeypatch.setattr(authority,'custody',lambda p,**kwargs:p.lstat())
    # Scope the privileged-OS fixture to this imported authority module. The
    # crash mechanics run on ordinary CI-owned files without changing owners
    # or requiring sudo. Production custody and ownership calls stay intact.
    fixture_os=SimpleNamespace(**vars(os))
    ownership_calls=[]
    def fixture_fstat(fd):
        values=list(os.fstat(fd))
        values[4]=0  # Synthetic root owner; device/inode/mode remain actual.
        return os.stat_result(values)
    def fixture_fchown(fd,uid,gid):
        assert uid==0
        ownership_calls.append((fd,uid,gid))
    fixture_os.geteuid=lambda:0
    fixture_os.fstat=fixture_fstat
    fixture_os.fchown=fixture_fchown
    monkeypatch.setattr(authority,'os',fixture_os)
    real_replace=authority.os.replace
    replaced=[False]
    def replace_file(a,b):
        if failure_stage=='BEFORE_RENAME':
            raise OSError('synthetic interrupted write')
        real_replace(a,b)
        replaced[0]=True
    real_fsync=authority.os.fsync
    def fsync(fd):
        if failure_stage=='AFTER_RENAME' and replaced[0]:
            raise OSError('synthetic uncertain durability')
        real_fsync(fd)
    monkeypatch.setattr(authority.os,'replace',replace_file)
    monkeypatch.setattr(authority.os,'fsync',fsync)
    monkeypatch.setattr(authority,'time',SimpleNamespace(time=lambda:21.))
    def publish():
        return authority.publish(action='DEMOTE',expected_state_sha256=digest(state),reason='SOURCE_LOST',size_multiplier=0.)
    if failure_stage:
        with pytest.raises(OSError): publish()
    else:
        assert publish()['epoch']==2
    envelope=authority.read_protected(path)
    authority.validate_state(envelope['state'])
    assert digest(envelope['state'])==envelope['sha256']
    assert envelope['state']['epoch']==(1 if failure_stage=='BEFORE_RENAME' else 2)
    assert envelope['state']['active_bundle_sha256']==state['active_bundle_sha256']
    assert not list(tmp_path.glob('model-state-*'))
    assert len(ownership_calls)==1


def test_publisher_lock_custody_remains_enforced(tmp_path,monkeypatch):
    import os
    import stat
    from types import SimpleNamespace
    fixture_os=SimpleNamespace(**vars(os))
    fixture_os.geteuid=lambda:0
    fixture_os.fstat=lambda fd:SimpleNamespace(st_mode=stat.S_IFREG|0o600,st_uid=1000)
    monkeypatch.setattr(authority,'os',fixture_os)
    monkeypatch.setattr(authority,'STATE',tmp_path/'state.json')
    monkeypatch.setattr(authority,'custody',lambda p,**kwargs:p.lstat())
    with pytest.raises(authority.AuthorityError,match='MODEL_AUTHORITY_LOCK_CUSTODY'):
        authority.publish(action='DEMOTE',expected_state_sha256='0'*64,reason='SYNTHETIC_TEST',size_multiplier=0.)
    assert not (tmp_path/'state.json').exists()
