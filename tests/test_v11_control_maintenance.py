import ast
import hashlib
import json
from pathlib import Path

import pytest

from polymarket_scanner.v11 import control_maintenance as prep


@pytest.fixture
def roots(tmp_path):
    root=tmp_path/'state';root.mkdir();(root/'weather.sqlite-wal').write_bytes(b'private-wal-fixture')
    (root/'config').write_bytes(b'fixture-secret');(root/'config').chmod(0o600)
    (root/'link').symlink_to('/not-followed')
    return {'control':root}


def test_inventory_retains_private_metadata_without_copying_or_opening_database(roots,tmp_path,monkeypatch):
    original=prep.os.open;opened=[]
    def tracked(path,*args,**kw):opened.append(str(path));return original(path,*args,**kw)
    monkeypatch.setattr(prep.os,'open',tracked)
    root=roots['control'];before=(root/'weather.sqlite-wal').read_bytes()
    result=prep.inventory(roots,tmp_path/'inventory',hash_paths=(root/'config',))
    body=json.loads((tmp_path/'inventory'/'inventory.json').read_text())
    assert not result['backup_complete'] and not result['database_opened']
    assert not any(p.endswith('weather.sqlite-wal') for p in opened)
    assert (root/'weather.sqlite-wal').read_bytes()==before
    config=next(r for r in body['entries'] if r['path'].endswith('/config'))
    assert config['mode']==0o600 and config['sha256']==hashlib.sha256(b'fixture-secret').hexdigest()
    assert 'fixture-secret' not in (tmp_path/'inventory'/'inventory.json').read_text()
    assert (tmp_path/'inventory').stat().st_mode&0o777==0o700
    assert (tmp_path/'inventory'/'inventory.json').stat().st_mode&0o777==0o600
    assert next(r for r in body['entries'] if r['kind']=='SYMLINK')['target']=='/not-followed'


def test_existing_and_partial_attempts_are_never_overwritten(roots,tmp_path):
    dest=tmp_path/'inventory'
    with pytest.raises(prep.PreparationError,match='ENTRY_BOUND'):prep.inventory(roots,dest,max_entries=1)
    assert (dest/'INCOMPLETE.json').exists()
    before=(dest/'INCOMPLETE.json').read_bytes()
    with pytest.raises(prep.PreparationError,match='DESTINATION_EXISTS'):prep.inventory(roots,dest)
    assert (dest/'INCOMPLETE.json').read_bytes()==before


def test_symlink_root_and_source_destination_overlap_are_refused(roots,tmp_path):
    link=tmp_path/'rootlink';link.symlink_to(roots['control'])
    with pytest.raises(prep.PreparationError,match='SYMLINK'):prep.inventory({'root':link},tmp_path/'out')
    with pytest.raises(prep.PreparationError,match='OVERLAP'):prep.inventory(roots,roots['control']/'out')


def test_changed_config_during_inventory_fails_without_completion(roots,tmp_path,monkeypatch):
    original=prep.os.open
    def changed(path,*args,**kw):
        if str(path).endswith('/config'):Path(path).write_bytes(b'changed-longer')
        return original(path,*args,**kw)
    monkeypatch.setattr(prep.os,'open',changed)
    with pytest.raises(prep.PreparationError,match='(CONFIG|SOURCE)_CHANGED'):
        prep.inventory(roots,tmp_path/'inventory',hash_paths=(roots['control']/'config',))
    assert not (tmp_path/'inventory'/'COMPLETE.json').exists()


def test_requested_config_digest_cannot_be_silently_missing(roots,tmp_path):
    outside=tmp_path/'other';outside.write_bytes(b'other')
    with pytest.raises(prep.PreparationError,match='NOT_IN_SOURCE'):
        prep.inventory(roots,tmp_path/'inventory',hash_paths=(outside,))


def test_proposal_is_runtime_only_with_start_guard_and_no_resource_relaxation():
    d=prep.proposal()
    assert d['dropin_path'].startswith('/run/') and d['marker_path'].startswith('/run/')
    assert d['dropin_sha256']==hashlib.sha256(d['dropin_text'].encode()).hexdigest()
    assert 'Restart=no\nRestartForceExitStatus=\nSendSIGKILL=no' in d['dropin_text']
    assert 'ConditionPathExists=!'+d['marker_path'] in d['dropin_text']
    assert all(x not in d['dropin_text'] for x in ('Memory','OOMPolicy','KillMode','Timeout','Exec'))
    assert not d['reboot_persistence'] and not d['kernel_oom_prevented'] and not d['service_mutated']


def test_default_cli_is_a_read_only_proposal_and_unprivileged_inventory_fails(capsys,monkeypatch):
    assert prep.main([])==0
    assert json.loads(capsys.readouterr().out)['status']=='PREPARED_NOT_INSTALLED_NOT_APPROVED'
    monkeypatch.setattr(prep.os,'geteuid',lambda:1000)
    assert prep.main(['--owner-inventory-only'])==2
    assert json.loads(capsys.readouterr().out)['error']=='OWNER_PROTECTED_READ_ACCESS_REQUIRED'


def test_preparation_contains_no_service_signal_process_or_restore_api():
    tree=ast.parse(Path(prep.__file__).read_text())
    forbidden={'kill','killpg','pidfd_send_signal','system','popen','Popen','run','call','execv','execve','extractall','unlink','rmtree','remove'}
    calls={n.func.attr if isinstance(n.func,ast.Attribute) else n.func.id if isinstance(n.func,ast.Name) else ''
           for n in ast.walk(tree) if isinstance(n,ast.Call)}
    assert not calls&forbidden
