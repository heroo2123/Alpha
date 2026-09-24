import ast
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tarfile

import pytest

from polymarket_scanner.v11 import control_preservation as cp


def snapshot_module():
    path=Path(__file__).parents[1]/'tools/v11_snapshot.py'
    spec=importlib.util.spec_from_file_location('snapshot_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.fixture
def source(tmp_path):
    root=tmp_path/'source';root.mkdir();(root/'config').write_text('private-fixture-value')
    (root/'config').chmod(0o600);(root/'source.py').write_text('VALUE = 1\n')
    (root/'history').mkdir();(root/'history'/'old').write_bytes(b'old-history')
    (root/'link').symlink_to('/outside/not-followed')
    return root


def capture(source, destination, **kw):
    return cp.preserve_files({'control':source},destination,max_bytes=1024*1024,max_files=100,**kw)


def test_private_archive_preserves_hashes_modes_links_and_history_without_mutating_source(source,tmp_path):
    before={p.name:cp.digest_file(p) for p in source.iterdir() if p.is_file()}
    result=capture(source,tmp_path/'backup')
    verified=cp.verify_preservation(tmp_path/'backup',expected_manifest_sha256=result['manifest_sha256'])
    assert verified['verified'] and verified['external_manifest_hash_matched']
    assert not verified['database_restore_authority']
    assert ((tmp_path/'backup').stat().st_mode&0o777)==0o700
    assert ((tmp_path/'backup'/'physical.tar').stat().st_mode&0o777)==0o600
    with tarfile.open(tmp_path/'backup'/'physical.tar') as tar:
        assert tar.getmember('control/link').issym()
        assert tar.getmember('control/link').linkname=='/outside/not-followed'
        assert tar.getmember('control/config').mode==0o600
        assert tar.extractfile('control/history/old').read()==b'old-history'
    assert before=={p.name:cp.digest_file(p) for p in source.iterdir() if p.is_file()}


def test_existing_destination_is_preserved_including_unfinished_attempt(source,tmp_path):
    d=tmp_path/'backup';d.mkdir();(d/'unfinished').write_text('keep')
    with pytest.raises(cp.PreservationError,match='DESTINATION_EXISTS'):capture(source,d)
    assert (d/'unfinished').read_text()=='keep'


@pytest.mark.parametrize('fault',['root_symlink','parent_symlink','overlap'])
def test_unsafe_destinations_or_roots_are_rejected(source,tmp_path,fault):
    if fault=='root_symlink':
        p=tmp_path/'source-link';p.symlink_to(source,target_is_directory=True);root=p;dest=tmp_path/'backup'
    elif fault=='parent_symlink':
        p=tmp_path/'parent-link';p.symlink_to(tmp_path,target_is_directory=True);root=source;dest=p/'backup'
    else:root=source;dest=source/'backup'
    with pytest.raises(cp.PreservationError):capture(root,dest)
    assert (source/'config').exists()


def test_incomplete_copy_keeps_partial_evidence_and_never_claims_completion(source,tmp_path,monkeypatch):
    original=cp._metadata
    def changed(path):
        data=original(path)
        if path.name=='config':data['inode']+=1
        return data
    monkeypatch.setattr(cp,'_metadata',changed)
    with pytest.raises(cp.PreservationError,match='SOURCE_CHANGED'):capture(source,tmp_path/'backup')
    assert (tmp_path/'backup'/'physical.tar').exists()
    assert (tmp_path/'backup'/'INCOMPLETE.json').exists()
    assert not (tmp_path/'backup'/'COMPLETE.json').exists()
    with pytest.raises(cp.PreservationError,match='INCOMPLETE'):cp.verify_preservation(tmp_path/'backup')


def test_byte_bound_never_truncates_source_or_cleans_existing_evidence(source,tmp_path):
    with pytest.raises(cp.PreservationError,match='BYTE_BOUND'):
        cp.preserve_files({'control':source},tmp_path/'backup',max_bytes=1)
    assert (source/'config').read_text()=='private-fixture-value'
    assert (tmp_path/'backup'/'INCOMPLETE.json').exists()


def test_archive_and_external_manifest_hashes_are_checked(source,tmp_path):
    capture(source,tmp_path/'backup')
    with pytest.raises(cp.PreservationError,match='EXTERNAL_MANIFEST'):
        cp.verify_preservation(tmp_path/'backup',expected_manifest_sha256='0'*64)
    path=tmp_path/'backup'/'physical.tar'
    with path.open('r+b') as f:f.seek(700);f.write(b'corruption')
    with pytest.raises(cp.PreservationError,match='ARCHIVE_HASH'):cp.verify_preservation(tmp_path/'backup')


def test_acls_and_extended_attributes_stay_in_private_manifest(source,tmp_path):
    try:os.setxattr(source/'config','user.fixture',b'private-attribute')
    except OSError:pytest.skip('filesystem has no user xattrs')
    capture(source,tmp_path/'backup')
    rows=json.loads((tmp_path/'backup'/'manifest.json').read_text())['entries']
    row=next(r for r in rows if r['name']=='control/config')
    assert row['metadata']['xattrs_base64']['user.fixture']=='cHJpdmF0ZS1hdHRyaWJ1dGU='


def test_wal_backup_and_offline_recovery_rehearsal_include_committed_wal(tmp_path):
    source=tmp_path/'live.sqlite';db=sqlite3.connect(source)
    try:
        db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA wal_autocheckpoint=0')
        db.execute('CREATE TABLE evidence (value TEXT)');db.commit()
        db.execute('INSERT INTO evidence VALUES (?)',('committed-in-wal',));db.commit()
        db.execute('INSERT INTO evidence VALUES (?)',('uncommitted',))
        module=snapshot_module()
        m=module.snapshot_database(source,tmp_path/'snapshot',release_sha='a'*40,tree_sha='b'*40,config_sha256='c'*64)
        result=cp.rehearse_snapshot(module,tmp_path/'snapshot')
        assert result['quick_check']=='ok' and result['committed_wal_included']
        assert result['snapshot_sha256']==m['snapshot_sha256'] and not result['service_started']
        copy,_=module.open_verified_snapshot(tmp_path/'snapshot')
        try:assert [tuple(r) for r in copy.execute('SELECT value FROM evidence')]==[('committed-in-wal',)]
        finally:copy.close()
        assert db.in_transaction
    finally:db.close()


def properties():
    return dict(Type='simple',Restart='on-failure',RestartForceExitStatus='',SendSIGKILL='yes',
                MainPID='233096',ExecStop_configured=False,ExecStopPost_configured=False,
                OnSuccess='',OnFailure='',Triggers='',TriggeredBy='',Job='',WatchdogUSec='0',RuntimeMaxUSec='infinity')


def process():
    return dict(State='D (disk sleep)',SigPnd='0',ShdPnd='0',SigBlk='0',SigIgn='0000000001001000',SigCgt='0000000100000002')


def test_actual_unit_policy_cannot_be_certified_as_no_automatic_actions():
    d=cp.supervision_assessment(properties(),pids=['233096'],process_status=process())
    assert d['normal_sigterm_exit_expected_clean']
    assert d['process_in_d_state'] and d['suspension_readiness']=='NOT_READY'
    assert not d['unconditional_no_automatic_action_guarantee']
    assert {'AUTOMATIC_RESTART_POLICY_ENABLED','AUTOMATIC_FINAL_KILL_POLICY_ENABLED'}<=set(d['blockers'])


@pytest.mark.parametrize('fault',['handler','second_pid','unknown_properties'])
def test_signal_or_supervision_uncertainty_never_becomes_permission(fault):
    p=properties();s=process();ids=['233096']
    if fault=='handler':s['SigCgt']=hex(1<<14)[2:]
    elif fault=='second_pid':ids.append('another')
    else:p={}
    d=cp.supervision_assessment(p,pids=ids,process_status=s)
    assert d['suspension_readiness']=='NOT_READY' and not d['signal_sent']


def test_preservation_module_has_no_service_signal_privilege_or_restore_calls():
    tree=ast.parse(Path(cp.__file__).read_text())
    forbidden={'kill','killpg','pidfd_send_signal','system','popen','Popen','run','call','execv','execve','extractall','unlink','rmtree','remove'}
    calls={n.func.attr if isinstance(n.func,ast.Attribute) else n.func.id if isinstance(n.func,ast.Name) else ''
           for n in ast.walk(tree) if isinstance(n,ast.Call)}
    assert not calls&forbidden


def test_cli_outputs_only_redacted_hashes_and_never_source_contents(source,tmp_path,capsys):
    assert cp.main(['archive','--source-root','control='+str(source),'--destination',str(tmp_path/'backup')])==0
    raw=capsys.readouterr().out;d=json.loads(raw)
    assert 'private-fixture-value' not in raw and not d['signal_sent']
    assert cp.main(['verify','--directory',str(tmp_path/'backup'),'--expected-manifest-sha256',d['result']['manifest_sha256']])==0
    assert json.loads(capsys.readouterr().out)['result']['verified']
