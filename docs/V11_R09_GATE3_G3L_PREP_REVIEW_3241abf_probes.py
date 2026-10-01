"""Offline review probes for exact candidate 3241abf; synthetic local data only."""
import copy
import hashlib
import json
from pathlib import Path
import pytest
from tools import v11_r09_gate3_g3l_prep as p

RUN = 1790812800
START = RUN + 14 * 3600
NOW = START - 1800
TARGET = '2026-10-02'
MEASURE = 'storage.live_disk_memory_quota_measurement'

@pytest.fixture
def packet(tmp_path):
    refs = []
    for name, data in [('evidence.json', b'{"review_probe_evidence":true}'),
                       ('review.json', b'{"review_probe_independent_review":true}')]:
        (tmp_path / name).write_bytes(data)
        refs.append(dict(sha256=hashlib.sha256(data).hexdigest(), byte_length=len(data), media_type='application/json', path=name))
    inv = dict(schema=p.SCHEMA, launchable=False, target_date=TARGET, evidence={})
    for identity in p.ALL_IDS:
        scope = f'run:{RUN}' if identity in p.RUN_SPECIFIC else f'window:{START}:{START+10800}' if identity in p.WINDOW_SPECIFIC else 'accepted:sha256'
        inv['evidence'][identity] = dict(ref=copy.deepcopy(refs[0]), review_ref=copy.deepcopy(refs[1]), observed_utc=NOW, scope=scope)
    return inv, tmp_path

def findings(packet):
    inv, root = packet
    return p.check_inventory(inv, target_date=TARGET, now_utc=NOW, object_root=root)

@pytest.mark.parametrize('scope,reason', [(f'run:{RUN}', 'wrong window scope'), (f'window:{START}:{START+10800}', 'wrong run scope')])
def test_reproduce_unavoidable_measurement_scope_rejection(packet, scope, reason):
    packet[0]['evidence'][MEASURE]['scope'] = scope
    assert findings(packet) == [dict(id=MEASURE, state='INVALID', reason=reason)]

def test_reproduce_review_outputs_required_before_assembly(packet):
    for key in ('review.detached_g3l_report', 'review.detached_g3l_completed_terminal'):
        packet[0]['evidence'][key] = None
    got = findings(packet)
    assert {x['id'] for x in got if x['state'] == 'MISSING'} == {'review.detached_g3l_report', 'review.detached_g3l_completed_terminal'}
    assert len(got) == 3

@pytest.mark.parametrize('mutation,reason', [
    ('same_review', 'share one artifact'), ('tampered_file', 'unresolved or mismatched'),
    ('traversal', 'unresolved or mismatched'), ('symlink', 'unresolved or mismatched'),
    ('future', 'observation clock'), ('old_run', 'predates selected 00Z run'),
    ('old_window', 'predates window policy'), ('wrong_run', 'wrong run scope')])
def test_inventory_rejections(packet, mutation, reason):
    inv, root = packet
    identity = 'sources.gefs_current_run_index_object_range'
    if mutation == 'old_window': identity = 'clocks.calibration_sync_uncertainty'
    item = inv['evidence'][identity]
    if mutation == 'same_review': item['review_ref'] = copy.deepcopy(item['ref'])
    elif mutation == 'tampered_file': (root / 'evidence.json').write_bytes(b'changed')
    elif mutation == 'traversal': item['ref']['path'] = '../evidence.json'
    elif mutation == 'symlink':
        (root / 'link.json').symlink_to(root / 'evidence.json')
        item['ref']['path'] = 'link.json'
    elif mutation == 'future': item['observed_utc'] = NOW + 1
    elif mutation == 'old_run': item['observed_utc'] = RUN - 1
    elif mutation == 'old_window': item['observed_utc'] = START - 3601
    elif mutation == 'wrong_run': item['scope'] = f'run:{RUN-86400}'
    assert any(x['id'] == identity and reason in x['reason'] for x in findings(packet))

@pytest.mark.parametrize('disk,memory', [(0,0),(p.DISK_FLOOR,p.MEMORY_FLOOR), (2926313472,753352704), (3757068288,670765056), (2**40,2**40), (2**40,p.MEMORY_FLOOR+p.DECODED_RESERVE+p.HEADROOM-1), (2**40,p.MEMORY_FLOOR+p.DECODED_RESERVE+p.HEADROOM)])
def test_independent_capacity_recount(disk, memory):
    plan=p.plan(RUN,disk,memory)
    attempts=plan['attempt_slots']
    assert len(plan['slot_rows']) == len({tuple(row['slot']) for row in plan['slot_rows']}) == 2713
    body=sum(3*1024**2+2*4*1024**2+p.FIELD_LIMITS[plan['slot_rows'][i]['slot'][0]] for i in attempts)
    count=4*len(attempts)
    graph=count; aggregates=0
    while graph>1:
        graph=(graph+255)//256; aggregates+=graph
    objects=2*count+aggregates
    quota=4*64*1024**2+16*1024**2+objects*4*1024**2+64*1024**2+body
    assert plan['resources']['body_reservation_bytes']==body
    assert plan['resources']['local_storage_quota_bytes']==quota
    if attempts:
        assert quota+p.HEADROOM<=disk-p.DISK_FLOOR
        assert memory>=p.MEMORY_FLOOR+p.DECODED_RESERVE+p.HEADROOM
        assert count<=3600 and body<=1024**3 and objects<=4096
        assert 4*count+1<=10000 and 8*count+1<=32768 and 34*count+1<=131072
        assert count*30+(count-1)*2+120<=10800
    else:
        assert all(row['reason']=='NOT_ATTEMPTED_BUDGET' for row in plan['slot_rows'])

def test_committed_artifacts_reproduce():
    repo=Path('/tmp/alpha-v11-gate3-g3l-prep-20261001')
    report=json.loads((repo/'config/v11/r09_gate3_g3l_offline_prep_20261001.json').read_text())
    plan=report['capacity_plan']; snap=plan['resource_snapshot']
    assert report==p.make_report(target_date=TARGET,run_utc=RUN,free_disk=snap['free_disk_bytes'],available_memory=snap['available_memory_bytes'],observed_utc=report['snapshot_observed_utc'])
    assert json.loads((repo/'config/v11/r09_gate3_g3l_private_v4_null_template.json').read_text())==p.private_v4_null_template(TARGET)
