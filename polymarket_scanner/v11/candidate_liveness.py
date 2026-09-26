"""Broker-owned PAPER pulse receipts. Peers never supply health or time.

Only the trusted finite publication child uses this archive adapter. An accepted
historical observation can be completed from its existing pair or refused, never
sampled again after interruption. No account, order, fill or terminal API exists.
"""
from dataclasses import asdict
from functools import partial

from .evidence import EvidenceError, digest, finite, identity, sha
from .guardian_lease import process_identity, validate_process_identity
from .liveness_protocol import VERSION, ProducerPolicy, validate_request, validate_response
from . import runtime_health


def journal_key(account_id, worker):
    return 'candidate-liveness:'+digest([identity(account_id),identity(worker)])


def record_ids(config, request_id):
    suffix=digest([config,request_id])
    return {name:'liveness-'+name+':'+suffix for name in ('accepted','completed','heartbeat','health')}


def config_digest(broker_config, health_config, policy, worker):
    sha(broker_config);sha(health_config);validate_process_identity(worker)
    if not isinstance(policy,ProducerPolicy):raise EvidenceError('LIVENESS_POLICY_REQUIRED')
    return digest(dict(broker_config=broker_config,health_config=health_config,
                       producer=asdict(policy),worker=worker))


def validate_receipt(receipt, worker, policy):
    if type(receipt) is not dict or set(receipt)!= {'stamp','challenge_stamp','challenge_sha256','peer'}:
        raise EvidenceError('LIVENESS_RECEIPT_SCHEMA')
    sha(receipt['challenge_sha256'])
    if receipt['peer']!=dict(worker,gid=policy.candidate_gid):raise EvidenceError('LIVENESS_RECEIPT_PEER')
    for name in ('stamp','challenge_stamp'):
        stamp=receipt[name]
        if type(stamp) is not dict or set(stamp)!= {'wall','monotonic','boot_id'} or stamp['boot_id']!=worker['boot_id']:
            raise EvidenceError('LIVENESS_RECEIPT_STAMP')
        finite(stamp['wall']);finite(stamp['monotonic'])
    before,after=receipt['challenge_stamp'],receipt['stamp']
    if not all(0<=after[k]-before[k]<=.5 for k in ('wall','monotonic')):
        raise EvidenceError('LIVENESS_CHALLENGE_EXPIRED')


def validate_journal(event_id, details):
    """Exact nonfinancial receipt schema, also used by safety_audit dispatch."""
    keys={'version','config_sha256','broker_config','health_config','producer','worker','account_id',
          'request','phase','response','receipt','counter','financial_authority'}
    if (type(details) is not dict or set(details)!=keys or details['version']!=VERSION
            or details['financial_authority'] is not False or details['phase'] not in {'ACCEPTED','COMPLETED'}):
        raise EvidenceError('LIVENESS_JOURNAL_SCHEMA')
    d=details;p=ProducerPolicy(**d['producer'])
    if (event_id!=journal_key(d['account_id'],p.worker)
            or d['config_sha256']!=config_digest(d['broker_config'],d['health_config'],p,d['worker'])
            or type(d['counter']) is not int or not 1<=d['counter']<=p.maximum_pulses):
        raise EvidenceError('LIVENESS_JOURNAL_CONFIG')
    validate_request(d['request'],d['config_sha256'],wire=False)
    validate_receipt(d['receipt'],d['worker'],p)
    if d['phase']=='ACCEPTED':
        if d['response'] is not None:raise EvidenceError('LIVENESS_JOURNAL_PHASE')
    else:
        validate_response(d['response'],d['request'])
        if d['response']['outcome']=='PUBLISHED':
            ids=record_ids(d['config_sha256'],d['request']['request_id']);r=d['response']['result']
            if any(r[name+'_id']!=ids[name] for name in ('accepted','heartbeat','health')):
                raise EvidenceError('LIVENESS_JOURNAL_PAIR_BINDING')


class CandidateLiveness:
    def __init__(self, broker, policy, health):
        if not isinstance(health,runtime_health.RuntimeHealth) or not isinstance(policy,ProducerPolicy):
            raise EvidenceError('LIVENESS_TYPED_CONFIGURATION_REQUIRED')
        if (health.store.path!=broker.store.path or health.store.namespace!='V11_PAPER'
                or health.account_id!=broker.c.policy.account_id or health.config!=broker.g.health_config
                or policy.worker not in health.policy.workers):
            raise EvidenceError('LIVENESS_HEALTH_BINDING')
        # Recompute the exact launch configuration instead of trusting a copied hash.
        expected=digest(dict(policy=asdict(health.policy),account_id=health.account_id,
            scopes=health.scopes,sources=[asdict(s) for s in health.sources]))
        if expected!=health.config:raise EvidenceError('LIVENESS_HEALTH_CONFIGURATION_CHANGED')
        worker=dict(broker.g.worker)
        if broker.policy.identity_mode=='DISTINCT_PRINCIPALS' and worker['uid'] in {
                broker.policy.broker_uid,broker.policy.guardian_uid}:
            raise EvidenceError('LIVENESS_SEPARATE_PRINCIPAL_REQUIRED')
        self.broker,self.store,self.health,self.policy=broker,broker.store,health,policy
        self.worker=worker;self.config=config_digest(broker.config,health.config,policy,worker)
        self.key=journal_key(health.account_id,policy.worker)

    def _get(self,key):
        try:return self.store.get(key)
        except EvidenceError as exc:
            if str(exc)!='EVIDENCE_MISSING':raise
            return None

    def _head(self):
        row=self.store.latest(kind='RUNTIME_STATUS',event_id=self.key)
        if row:
            validate_journal(self.key,row['body']['details'])
            if row['body']['details']['config_sha256']!=self.config:
                raise EvidenceError('LIVENESS_CONFIG_CHANGED_REVIEW_REQUIRED')
        return row

    def _save(self,details,evidence_ids=()):
        validate_journal(self.key,details);head=self._head()
        ids=record_ids(self.config,details['request']['request_id'])
        return self.store.safety_audit(ids[details['phase'].lower()],kind='RUNTIME_STATUS',event_id=self.key,
            details=details,evidence_ids=evidence_ids,expected_previous_seq=head['seq'] if head else 0)

    def _complete(self,accepted,sample):
        d=accepted['body']['details'];ids=record_ids(self.config,d['request']['request_id'])
        evidence=[accepted['id']]
        if sample is None:
            outcome='REFUSED';result=dict(reason='INTERRUPTED_OBSERVATION')
        else:
            heartbeat=self.store.get(ids['heartbeat']);outcome='PUBLISHED'
            result=dict(accepted_id=accepted['id'],accepted_sha256=accepted['sha256'],
                heartbeat_id=heartbeat['id'],heartbeat_sha256=heartbeat['sha256'],
                health_id=sample['id'],health_sha256=sample['sha256'])
            evidence.extend((heartbeat['id'],sample['id']))
        response=dict(d['request'],outcome=outcome,result=result,financial_authority=False)
        self._save(dict(d,phase='COMPLETED',response=response),tuple(evidence))
        return response

    def recover(self):
        """Read existing original pair only; never probe or renew an old pulse."""
        head=self._head()
        if head and head['body']['details']['phase']=='ACCEPTED':
            d=head['body']['details'];ids=record_ids(self.config,d['request']['request_id'])
            sample=self.health._publication_replay(ids['health'],ids['heartbeat'],
                self.policy.worker,self.policy.generation,observation_id=head['id'])
            return self._complete(head,sample)

    def publish(self,request,receipt):
        """Trusted kernel-authenticated fresh request, executed off the guardian loop."""
        validate_request(request,self.config,wire=False)
        validate_receipt(receipt,self.worker,self.policy)
        start,observed=receipt['challenge_stamp'],receipt['stamp']
        if abs((observed['wall']-start['wall'])-(observed['monotonic']-start['monotonic']))>self.health.policy.maximum_wall_step_seconds:
            raise EvidenceError('LIVENESS_RECEIPT_CLOCK_STEP')
        if process_identity(self.worker['pid'])!=self.worker:raise EvidenceError('LIVENESS_WORKER_CHANGED')
        self.recover()
        ids=record_ids(self.config,request['request_id']);old=self._get(ids['accepted'])
        if old:
            if old['body']['details']['request']!=request:raise EvidenceError('LIVENESS_REQUEST_ID_CONFLICT')
            return self.store.get(ids['completed'])['body']['details']['response']
        now=runtime_health.host_stamp(self.store);stamp=receipt['stamp']
        if (now['boot_id']!=stamp['boot_id'] or any(not 0<=now[k]-stamp[k]<=self.policy.publication_timeout_seconds
                                                  for k in ('wall','monotonic'))):
            raise EvidenceError('LIVENESS_RECEIPT_TOO_OLD')
        head=self._head();previous=head['body']['details'] if head else None
        count=previous['counter']+1 if previous else 1
        if count>self.policy.maximum_pulses:raise EvidenceError('LIVENESS_PUBLICATION_LIMIT')
        if previous:
            previous_stamp=previous['receipt']['stamp']
            if (previous_stamp['boot_id']!=stamp['boot_id']
                    or stamp['monotonic']-previous_stamp['monotonic']<self.policy.minimum_interval_seconds
                    or stamp['wall']<previous_stamp['wall']):
                raise EvidenceError('LIVENESS_PUBLICATION_RATE')
        details=dict(version=VERSION,config_sha256=self.config,broker_config=self.broker.config,
            health_config=self.health.config,producer=asdict(self.policy),worker=self.worker,
            account_id=self.health.account_id,request=request,phase='ACCEPTED',response=None,
            receipt=receipt,counter=count,financial_authority=False)
        accepted=self._save(details)
        if process_identity(self.worker['pid'])!=self.worker:raise EvidenceError('LIVENESS_WORKER_CHANGED')
        sample=self.health.publish_observation(ids['health'],heartbeat_key=ids['heartbeat'],
            worker=self.policy.worker,generation=self.policy.generation,observation_id=accepted['id'])
        return self._complete(accepted,sample)


def health_configuration(health):
    return dict(policy=asdict(health.policy),account_id=health.account_id,scopes=health.scopes,
                sources=[asdict(s) for s in health.sources])


def restore_health(store,raw):
    if type(raw) is not dict or set(raw)!= {'policy','account_id','scopes','sources'}:
        raise EvidenceError('LIVENESS_HEALTH_LAUNCH_SCHEMA')
    p=dict(raw['policy']);p['workers']=tuple(p['workers'])
    return runtime_health.RuntimeHealth(store,runtime_health.HealthPolicy(**p),account_id=raw['account_id'],
        scopes={k:tuple(v) for k,v in raw['scopes'].items()},
        sources=tuple(runtime_health.SourceNeed(**v) for v in raw['sources']),
        sync_probe=partial(runtime_health.local_sync_status,parent_death=True))
