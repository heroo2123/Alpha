import ast
import hashlib
import json
import os
from pathlib import Path

import pytest

from polymarket_scanner.v11 import control_inventory_verify as verify, control_maintenance as capture


@pytest.fixture
def inventory(tmp_path):
    source = tmp_path/'state'; source.mkdir()
    for name in ('weather-paper.sqlite', 'weather-paper.sqlite-wal', 'weather-paper.sqlite-shm'):
        (source/name).write_bytes(b'private-fixture-not-opened-by-verifier')
    config = source/'config'; config.write_bytes(b'synthetic-secret'); config.chmod(0o600)
    (source/'external-link').symlink_to('/private-not-followed')
    roots = {'runtime_state':str(source)}; dest = tmp_path/'inventory'
    result = capture.inventory(roots, dest, hash_paths=(str(config),))
    return dict(directory=dest, expected_sha256=result['inventory_sha256'], roots=roots,
                hash_paths={str(config)}, expected_uid=os.geteuid(), expected_gid=os.getegid(), memory_available=1024**3)


def rewrite(rig, change):
    p = rig['directory']/'inventory.json'; body = json.loads(p.read_text()); change(body)
    raw = json.dumps(body).encode(); p.write_bytes(raw); key = hashlib.sha256(raw).hexdigest()
    marker = rig['directory']/'COMPLETE.json'; d = json.loads(marker.read_text()); d['inventory_sha256'] = key
    marker.write_text(json.dumps(d)); rig['expected_sha256'] = key


def test_saved_inventory_hash_schema_and_redacted_coverage_without_any_live_source_read(inventory, monkeypatch):
    original = verify.os.open; opened = []
    def record(path, flags, **kw):
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
        opened.append(str(path)); return original(path, flags, **kw)
    monkeypatch.setattr(verify.os, 'open', record)
    d = verify.verify(**inventory)
    assert d['status'] == 'INVENTORY_VERIFIED_ONLY' and d['external_owner_hash_matched'] and d['schema_verified']
    assert all(d['recorded_database_members'].values()) and d['roots']['runtime_state']['symlinks'] == 1
    assert d['entries'] == 6 and d['recorded_size_within_physical_copier_bound']
    assert all(d[k] is False for k in verify.FLAGS)
    assert not d['live_sources_read'] and not d['database_integrity_verified'] and d['suspension_readiness'] == 'NOT_READY'
    assert opened == [str(inventory['directory']), 'inventory.json', 'COMPLETE.json']
    encoded = json.dumps(d)
    assert 'synthetic-secret' not in encoded and '/private-not-followed' not in encoded
    assert 'external-link' not in encoded and 'xattrs_base64' not in encoded


def test_owner_hash_cannot_be_replaced_with_completion_markers_own_claim(inventory):
    original = inventory['expected_sha256']
    rewrite(inventory, lambda b:b.update(regular_bytes=0)); inventory['expected_sha256'] = original
    with pytest.raises(verify.VerificationError, match='OWNER_INVENTORY_HASH_MISMATCH'):
        verify.verify(**inventory)


@pytest.mark.parametrize('fault', ['partial', 'file_symlink', 'file_permissions', 'dir_permissions', 'owner', 'marker'])
def test_incomplete_or_unprotected_inventory_is_not_verified(inventory, fault):
    dest = inventory['directory']
    if fault == 'partial': (dest/'INCOMPLETE.json').write_text('{}')
    elif fault == 'file_symlink':
        f = dest/'inventory.json'; target = dest.parent/'other'; f.rename(target); f.symlink_to(target)
    elif fault == 'file_permissions': (dest/'inventory.json').chmod(0o644)
    elif fault == 'dir_permissions': dest.chmod(0o755)
    elif fault == 'owner': inventory['expected_uid'] += 1
    else: (dest/'COMPLETE.json').write_text('{}')
    with pytest.raises((verify.VerificationError, OSError)):
        verify.verify(**inventory)


@pytest.mark.parametrize('fault', ['scope', 'duplicate', 'totals', 'flags', 'config_hash', 'root', 'attrs', 'timestamp'])
def test_authenticated_bytes_still_require_consistent_inventory_schema(inventory, fault):
    def change(body):
        if fault == 'scope': body['entries'][1]['path'] = '/not-in-inventoried-root'
        elif fault == 'duplicate': body['entries'][1] = dict(body['entries'][0])
        elif fault == 'totals': body['regular_bytes'] += 1
        elif fault == 'flags': body['backup_complete'] = True
        elif fault == 'config_hash':
            next(r for r in body['entries'] if 'sha256' in r).pop('sha256')
        elif fault == 'root': body['entries'] = [r for r in body['entries'] if r['kind'] != 'DIRECTORY']
        elif fault == 'attrs': body['entries'][0]['xattrs_base64'] = {'user.fixture':'invalid-b64!'}
        else: body['created_at'] = {'untrusted':'not-a-timestamp'}
    rewrite(inventory, change)
    with pytest.raises((verify.VerificationError, ValueError)):
        verify.verify(**inventory)


def test_oversized_parse_is_honest_hash_only_and_does_not_claim_coverage(inventory, monkeypatch):
    monkeypatch.setattr(verify, 'MAX_JSON_BYTES', 1)
    d = verify.verify(**inventory)
    assert d['external_owner_hash_matched'] and not d['schema_verified']
    assert d['status'] == 'INVENTORY_HASH_VERIFIED_SCHEMA_UNCHECKED' and 'roots' not in d


def test_insufficient_memory_fails_before_parsing(inventory):
    inventory['memory_available'] = 1
    with pytest.raises(verify.VerificationError, match='MEMORY_HEADROOM'):
        verify.verify(**inventory)


def test_duplicates_in_completion_json_are_not_accepted(inventory):
    (inventory['directory']/'COMPLETE.json').write_text('{"status":1,"status":2}')
    with pytest.raises(verify.VerificationError, match='DUPLICATE_JSON_KEY'):
        verify.verify(**inventory)


def test_prepared_cli_is_read_only_root_required_and_pins_exact_owner_handoff(monkeypatch, capsys):
    assert verify.ROOTS == capture.ROOTS and verify.HASH_PATHS == capture.HASH_SMALL_FILES
    assert verify.DESTINATION == capture.INVENTORY_DESTINATION
    assert verify.EXPECTED_SHA256 == 'b161426cff5b39b262e72a6e8142982dd29fa8a0bf29c9965232edf1ff364bd3'
    monkeypatch.setattr(verify.os, 'geteuid', lambda:1000)
    assert verify.main(['--owner-verify-inventory-only']) == 2
    assert json.loads(capsys.readouterr().out)['error'] == 'OWNER_PROTECTED_READ_ACCESS_REQUIRED'
    tree = ast.parse(Path(verify.__file__).read_text())
    forbidden = {'kill','killpg','system','popen','Popen','run','call','chmod','chown','unlink','mkdir','rename','replace','write','write_bytes','write_text'}
    calls = {n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id if isinstance(n.func, ast.Name) else ''
             for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert not calls & forbidden
