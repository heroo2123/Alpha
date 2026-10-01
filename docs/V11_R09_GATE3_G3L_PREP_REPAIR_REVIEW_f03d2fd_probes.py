"""Independent offline repair probes; actual candidate files remain read-only."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest
from tools import v11_r09_gate3_g3l_prep as p
from tools.v11_multimodel_panel import canonical
from tools.v11_r09_gate3_launch import LaunchContractError
from tools.v11_r09_gate3_launch_v4 import validate_manifest_v4

RUN=1790812800
START=RUN+14*3600
NOW=START-1800
TARGET='2026-10-02'
REPO=Path('/tmp/alpha-v11-gate3-g3l-prep-20261001')
MEASURE='storage.live_disk_memory_quota_measurement'

@pytest.fixture
def packet(tmp_path):
    inv={'schema':p.SCHEMA,'launchable':False,'target_date':TARGET,'evidence':{}}
    for n, identity in enumerate(p.ALL_IDS):
        refs=[]
        for role in ('evidence','review'):
            data=json.dumps({'identity':identity,'role':role}).encode()
            name=f'{n}-{role}.json'
            (tmp_path/name).write_bytes(data)
            refs.append(dict(sha256=hashlib.sha256(data).hexdigest(),byte_length=len(data),media_type='application/json',path=name))
        scope=f'run:{RUN}' if identity in p.RUN_SPECIFIC else f'window:{START}:{START+10800}' if identity in p.WINDOW_SPECIFIC else 'accepted:sha256'
        inv['evidence'][identity]=dict(ref=refs[0],review_ref=refs[1],scope=scope,observed_utc=NOW)
    return inv,tmp_path

def check(packet, stage='FINAL', now=NOW, root=True):
    inv,obj=packet
    return p.check_inventory(inv,target_date=TARGET,now_utc=now,stage=stage,object_root=obj if root else None)

def pre(packet):
    for identity in p.FINAL_ONLY_IDS: packet[0]['evidence'][identity]=None
    return packet

@pytest.mark.parametrize('stage',['PRE_REVIEW','FINAL'])
def test_complete_unique_artifact_packet(packet,stage):
    if stage=='PRE_REVIEW': pre(packet)
    assert check(packet,stage)==[]
    assert len(p.PRE_REVIEW_IDS)==77 and len(p.ALL_IDS)==79
    assert not p.RUN_SPECIFIC & p.WINDOW_SPECIFIC

@pytest.mark.parametrize('stage',['PRE_REVIEW','FINAL'])
def test_every_required_identity_missing_and_ref_invalid(packet,stage):
    if stage=='PRE_REVIEW': pre(packet)
    ids=p.PRE_REVIEW_IDS if stage=='PRE_REVIEW' else p.ALL_IDS
    inv,_=packet
    for identity in ids:
        original=copy.deepcopy(inv['evidence'][identity])
        inv['evidence'][identity]=None
        assert check(packet,stage)==[dict(id=identity,state='MISSING',reason='unfilled evidence')]
        for role in ('ref','review_ref'):
            inv['evidence'][identity]=copy.deepcopy(original)
            inv['evidence'][identity][role]['sha256']='a1b2c3d4'*8
            result=check(packet,stage)
            assert len(result)==1 and result[0]['id']==identity and result[0]['state']=='INVALID'
        inv['evidence'][identity]=original

@pytest.mark.parametrize('stage',['PRE_REVIEW','FINAL'])
@pytest.mark.parametrize('mutation',['equal','length','missing','absolute','traversal','symlink','parent_symlink','malformed','placeholder','future','bool_time','empty_scope'])
def test_bad_refs_and_metadata(packet,stage,mutation):
    if stage=='PRE_REVIEW': pre(packet)
    inv,root=packet
    identity=MEASURE
    item=inv['evidence'][identity]
    ref=item['ref']
    if mutation=='equal': item['review_ref']=copy.deepcopy(ref)
    elif mutation=='length': ref['byte_length']+=1
    elif mutation=='missing': ref['path']='absent.json'
    elif mutation=='absolute': ref['path']=str(root/ref['path'])
    elif mutation=='traversal': ref['path']='../'+ref['path']
    elif mutation=='symlink':
        (root/'link').symlink_to(root/ref['path']); ref['path']='link'
    elif mutation=='parent_symlink':
        (root/'dirlink').symlink_to(root,target_is_directory=True); ref['path']='dirlink/'+ref['path']
    elif mutation=='malformed': ref['byte_length']=True
    elif mutation=='placeholder': ref['media_type']='pending'
    elif mutation=='future': item['observed_utc']=NOW+1
    elif mutation=='bool_time': item['observed_utc']=True
    elif mutation=='empty_scope': item['scope']=' '
    result=check(packet,stage)
    assert len(result)==1 and result[0]['id']==identity and result[0]['state']=='INVALID'

@pytest.mark.parametrize('stage',['PRE_REVIEW','FINAL'])
def test_scoped_identity_boundaries(packet,stage):
    if stage=='PRE_REVIEW': pre(packet)
    for identity in p.RUN_SPECIFIC | p.WINDOW_SPECIFIC:
        item=packet[0]['evidence'][identity]; original=copy.deepcopy(item)
        lower=RUN if identity in p.RUN_SPECIFIC else START-3600
        item['observed_utc']=lower
        assert check(packet,stage)==[]
        item['observed_utc']=lower-1
        result=check(packet,stage)
        assert len(result)==1 and result[0]['id']==identity and result[0]['state']=='STALE'
        item['observed_utc']=NOW; item['scope']='run:1'
        assert check(packet,stage)[0]['state']=='INVALID'
        packet[0]['evidence'][identity]=original
    assert check(packet,stage,now=START-1)==[]
    assert check(packet,stage,now=START)==[dict(id='time.review_before_window',state='EXPIRED',reason='review cannot finish before frozen acquisition start')]

@pytest.mark.parametrize('stage',['PRE_REVIEW','FINAL'])
def test_no_root_cannot_pass(packet,stage):
    if stage=='PRE_REVIEW': pre(packet)
    assert len(check(packet,stage,root=False))==(77 if stage=='PRE_REVIEW' else 79)
    assert {f['state'] for f in check(packet,stage,root=False)}=={'UNVERIFIED'}

def test_premature_outputs_and_final_requires_both(packet):
    assert {f['id'] for f in check(packet,'PRE_REVIEW')}==set(p.FINAL_ONLY_IDS)
    for identity in p.FINAL_ONLY_IDS:
        original=packet[0]['evidence'][identity]
        packet[0]['evidence'][identity]=None
        assert check(packet)==[dict(id=identity,state='MISSING',reason='unfilled evidence')]
        packet[0]['evidence'][identity]=original

@pytest.mark.parametrize('stage',['PRE_REVIEW','FINAL'])
def test_cli_complete_missing_invalid_never_launch(packet,stage,capsys):
    if stage=='PRE_REVIEW': pre(packet)
    inv,root=packet; path=root/'packet.json'
    args=['--target-date',TARGET,'--free-disk-bytes','0','--available-memory-bytes','0','--observed-utc',str(NOW),'--inventory',str(path),'--object-root',str(root),'--stage',stage.lower()]
    for case in ('complete','missing','invalid'):
        changed=copy.deepcopy(inv)
        if case=='missing': changed['evidence'][MEASURE]=None
        if case=='invalid': changed['evidence'][MEASURE]['ref']['byte_length']+=1
        path.write_text(json.dumps(changed))
        code=p.main(args); output=json.loads(capsys.readouterr().out)
        assert code==(0 if case=='complete' else 2)
        assert output['launchable'] is False and output['stage']==stage
        if case=='complete': assert output['status']==('ASSEMBLED_FOR_INDEPENDENT_REVIEW' if stage=='PRE_REVIEW' else 'FINAL_REVIEWED_PACKAGE_NO_LAUNCH_AUTHORITY')
        else: assert output['status']=='BLOCKED_EVIDENCE'

def test_closed_schema_stage_and_launch_flag(packet):
    inv,_=packet
    for stage in ('LAUNCH','final','',None):
        with pytest.raises(ValueError): check(packet,stage)
    for launch in (True,1,0,None,'false'):
        inv['launchable']=launch
        with pytest.raises(ValueError): check(packet)
    inv['launchable']=False
    del inv['evidence'][p.FINAL_ONLY_IDS[0]]
    with pytest.raises(ValueError): check(packet,'PRE_REVIEW')
    with pytest.raises(ValueError): p._parse_json(b'{"launchable":false,"launchable":true}')

def test_exact_report_template_reproduction_and_v4_refusal(tmp_path):
    raw=(REPO/'config/v11/r09_gate3_g3l_offline_prep_20261001.json').read_bytes()
    report=json.loads(raw); plan=report['capacity_plan']; snap=plan['resource_snapshot']
    actual=p.make_report(target_date=TARGET,run_utc=RUN,free_disk=snap['free_disk_bytes'],available_memory=snap['available_memory_bytes'],observed_utc=report['snapshot_observed_utc'])
    assert report==actual
    assert raw==canonical(actual)+b'\n'
    assert len(actual['missing_evidence'])==77 and all(v is None for v in actual['inventory_template']['evidence'].values())
    raw=(REPO/'config/v11/r09_gate3_g3l_private_v4_null_template.json').read_bytes()
    template=p.private_v4_null_template(TARGET)
    assert json.loads(raw)==template
    assert raw==(json.dumps(template,sort_keys=True,indent=2)+'\n').encode()
    with pytest.raises(LaunchContractError): validate_manifest_v4(canonical(template),repo=REPO,object_root=tmp_path,now_utc=NOW)

# Retain independent resource recounts from the original review; defect
# reproductions are deliberately replaced by successful repaired-path probes.
_spec=importlib.util.spec_from_file_location('original_review','/tmp/alpha-v11-g3l-prep-review-3241abf-probes.py')
_old=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_old)
test_independent_capacity_recount=_old.test_independent_capacity_recount
