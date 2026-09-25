"""Paired runtime integration and actual sibling publication; synthetic PAPER only."""
from dataclasses import asdict
import json
import subprocess
import sys
import time

from polymarket_scanner.v11 import runtime_health as health, paper_runtime, paper_guardian, guardian_lease
from test_v11_paper_coordinator import rig, coordinator, proposal
from test_v11_paper_runtime import assembled
from test_v11_paper_guardian import ENV, ROOT, state
from guardian_custody_namespace import _readline


def test_candidate_runtime_uses_bound_atomic_heartbeat_sample(rig,monkeypatch):
    base=assembled(rig,monkeypatch,census=False)
    runtime=paper_runtime.PaperRuntime(base.coordinator,base.queue,base.health,base.policy,
        evaluator=base.evaluator,worker_id='worker',generation='paired-runtime')
    runtime.tick('paired')
    view=health.read_health_snapshot(rig['store']);sample=view['row']['body']['details']
    heartbeat=view['workers']['worker']
    assert sample['request']==dict(action='SAMPLE',heartbeat_key=heartbeat['id'],worker='worker',generation='paired-runtime')
    assert sample['workers'][0]['record_id']==heartbeat['id']
    assert sample['request']['heartbeat_key'].endswith(':heartbeat')


PUBLISHER = '''
import json,sys,time
from polymarket_scanner.v11.evidence import EvidenceStore
from polymarket_scanner.v11.runtime_health import RuntimeHealth,HealthPolicy,SourceNeed
from polymarket_scanner.v11.paper_guardian import process_limits
process_limits()
x=json.loads(sys.argv[1]);p=x['policy'];p['workers']=tuple(p['workers'])
s=EvidenceStore(x['path'],'V11_PAPER',clock=lambda:x['wall']+time.monotonic()-x['mono'])
m=RuntimeHealth(s,HealthPolicy(**p),account_id='account',scopes={k:tuple(v) for k,v in x['scopes'].items()},
 sources=tuple(SourceNeed(**v) for v in x['sources']),
 sync_probe=lambda:dict(synchronized=True,mechanism='SYNTHETIC_OFF_HOST_FIXTURE',offset_seconds=None))
for i in range(2):
 m.publish('paired-sample:'+str(i),heartbeat_key='paired-heartbeat:'+str(i),worker='candidate',generation='same-process')
 time.sleep(.03)
print('READY',flush=True)
assert sys.stdin.readline()=='GO\\n'
for i in range(2,42):
 m.publish('paired-sample:'+str(i),heartbeat_key='paired-heartbeat:'+str(i),worker='candidate',generation='same-process')
 time.sleep(.01)
print('DONE',flush=True)
assert sys.stdin.readline()=='EXIT\\n'
'''


def test_actual_sibling_publication_does_not_cancel_healthy_resting_intent(rig):
    c=coordinator(rig);c.coordinate('reserve',(proposal(rig,units='2'),))
    wall=rig['now'][0];mono=time.monotonic();rig['store'].clock=lambda:wall+time.monotonic()-mono
    p=health.HealthPolicy('synthetic-coherent-sibling',10.,10.,1.,2,.02,('candidate',))
    sources=(health.SourceNeed(rig['context'].event_id,'fixture','OFFICIAL_OBSERVATION','fixture',rig['context'].station_id,60.),)
    monitor=health.RuntimeHealth(c.store,p,account_id='account',scopes={rig['context'].event_id:('fixture',)},
        sources=sources,sync_probe=lambda:dict(synchronized=True,mechanism='SYNTHETIC_OFF_HOST_FIXTURE'))
    payload=dict(path=str(c.store.path),wall=wall,mono=mono,policy=asdict(p),sources=[asdict(s) for s in sources],scopes=monitor.scopes)
    process=subprocess.Popen([sys.executable,'-s','-E','-c',PUBLISHER,json.dumps(payload)],cwd=ROOT,env=ENV,
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,close_fds=True,start_new_session=True)
    try:
        assert _readline(process.stdout,time.monotonic()+5)==b'READY'
        guardian=paper_guardian.PaperGuardian(c,policy=guardian_lease.GuardianPolicy('coherent-sibling'),
            health_config=monitor.config,worker=guardian_lease.process_identity(process.pid))
        process.stdin.write(b'GO\n');process.stdin.flush()
        outcomes=[]
        for _ in range(20):
            details=guardian._cycle()['body']['details'];outcomes.append(details['status'])
            assert details['status']=='READY' or details['reasons']==['GUARDIAN_HEALTH_PUBLICATION_CHANGED']
            assert not state(c)['intents']['proposal']['cancel_requested']
        assert _readline(process.stdout,time.monotonic()+5)==b'DONE'
        assert guardian._cycle()['body']['details']['status']=='READY'
        assert state(c)['intents']['proposal']['status']=='RESERVED'
        process.stdin.write(b'EXIT\n');process.stdin.close();process.stdin=None
        out,err=process.communicate(timeout=5)
        assert process.returncode==0 and not out and not err
    finally:
        if process.poll() is None:process.kill()
        process.communicate(timeout=5)
