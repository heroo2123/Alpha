"""Resumable public catalog capture and strict semantic census.

Enumeration, semantic support, station certification, current books and strategy
eligibility are distinct. No discovery result grants any of the latter gates.
"""
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import fcntl
import os
import re
import sqlite3
import time

from ..weather_only_contract_strict import compile_strict_temperature_event
from ..weather_only_contracts import weather_family
from ..weather_only_discovery import WeatherOnlyDiscovery
from .certification import StationMetadata
from .collection import SourceRequest
from .evidence import EvidenceError, canonical, digest, finite, identity
from .rules import RuleGuard, fingerprint_event
from .runtime_health import KEY as HEALTH_KEY


VERSION = 'alpha_v11_market_discovery_v1'
KEY = 'v11-market-discovery'
ENDPOINT = 'https://gamma-api.polymarket.com/events/keyset'


@dataclass(frozen=True)
class DiscoveryPolicy:
    version: str
    page_size: int = 50
    maximum_pages: int = 1000
    maximum_event_hits: int = 100_000
    maximum_events_per_step: int = 8
    maximum_processing_seconds: float = 2.
    maximum_scan_seconds: float = 14400.
    maximum_page_age_seconds: float = 300.
    maximum_metadata_age_seconds: float = 86400.
    maximum_page_failures: int = 3

    def __post_init__(self):
        identity(self.version)
        for name, upper in (('page_size',100),('maximum_pages',1000),('maximum_event_hits',100_000),
                            ('maximum_events_per_step',32),('maximum_page_failures',5)):
            if type(getattr(self,name)) is not int or not 1 <= getattr(self,name) <= upper:
                raise EvidenceError('DISCOVERY_POLICY_COUNT_BOUND')
        for name, upper in (('maximum_processing_seconds',10),('maximum_scan_seconds',86400),
                            ('maximum_page_age_seconds',3600),('maximum_metadata_age_seconds',86400)):
            if not 0 < finite(getattr(self,name)) <= upper:
                raise EvidenceError('DISCOVERY_POLICY_TIME_BOUND')


def catalog_request(cursor, *, page_size, revision):
    params = [('closed','false'),('limit',str(page_size))]
    if cursor is not None:
        identity(cursor, maximum=512); params.append(('after_cursor',cursor))
    return SourceRequest('GAMMA_DISCOVERY',ENDPOINT,'v11-discovery-catalog','RULES',
                         'keyset:'+digest(params),revision,tuple(params))


class MarketDiscovery:
    def __init__(self, scheduled, health, policy):
        if not isinstance(policy,DiscoveryPolicy) or scheduled.store is not health.store:
            raise EvidenceError('DISCOVERY_COMPONENT_SCOPE')
        self.scheduled,self.health,self.store,self.policy=scheduled,health,health.store,policy
        self.config=digest(dict(policy=asdict(policy),health=health.config,namespace=self.store.namespace))

    def _get(self,key):
        try:return self.store.get(key)
        except EvidenceError as exc:
            if str(exc)!='EVIDENCE_MISSING':raise
        return None

    def _head(self):
        row=self.store.latest(kind='RUNTIME_STATUS',event_id=KEY)
        if row and row['body']['details'].get('config_sha256')!=self.config:
            raise EvidenceError('DISCOVERY_CONFIG_CHANGED_REVIEW_REQUIRED')
        return row

    def _save(self,key,state,**result):
        if len(canonical(state).encode())>256*1024:raise EvidenceError('DISCOVERY_STATE_BYTES_BOUND')
        head=self._head()
        return self.store.safety_audit(key,event_id=KEY,kind='RUNTIME_STATUS',details=dict(
            version=VERSION,config_sha256=self.config,state=state,**result,
            financial_authority=False,route_registration_performed=False,automatic_recertification=False,
            semantic_family_approval_performed=False,forward_acceptance=False),expected_previous_seq=head['seq'] if head else 0)

    def start(self,scan_id):
        with self._locked():
            return self._start(scan_id)

    @contextmanager
    def _locked(self):
        fd=os.open(self.store.path.with_name(self.store.path.name+'.discovery.lock'),os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise EvidenceError('DISCOVERY_WORKER_ALREADY_RUNNING') from None
            yield
        finally:os.close(fd)

    def _start(self,scan_id):
        identity(scan_id,maximum=80);key='discovery-start:'+digest(scan_id)
        prior=self._get(key)
        if prior:
            if prior['body']['details'].get('config_sha256')!=self.config:raise EvidenceError('DISCOVERY_REPLAY_CONFIG')
            return prior
        head=self._head()
        if head and head['body']['details']['state']['phase'] not in {'COMPLETE','INCOMPLETE'}:
            raise EvidenceError('DISCOVERY_ACTIVE_SCAN_MUST_BE_PRESERVED')
        state=dict(scan_id=scan_id,phase='COLLECTING',started_at=finite(self.store.clock()),finished_at=None,
            cursor=None,seen_cursor_hashes=[],page_id=None,page_position=0,next_cursor=None,pages=0,
            pending_cycle=None,attempt=0,page_failures=0,hits=0,unique_events=0,weather_looking=0,
            strict_supported=0,unsupported=0,duplicates=0,duplicate_revisions=0,reason_counts={},family_counts={},
            template_counts={},counter_overflow=0,first_receipt_at=None,last_receipt_at=None,errors=[])
        return self._save(key,state,outcome='SCAN_STARTED')

    def _progress(self,command,state,**result):
        head=self._head()
        return self._save('discovery-progress:'+digest([command,head['seq'] if head else 0]),state,**result)

    def _sample(self,command,stage):
        head=self.store.latest(kind='RUNTIME_STATUS',event_id=HEALTH_KEY)
        return self.health.sample('discovery-clock:'+digest([command,stage,head['seq'] if head else 0]))

    @staticmethod
    def _counter(state,name,key):
        counter=state[name]
        if key in counter or len(counter)<64:counter[key]=counter.get(key,0)+1
        else:state['counter_overflow']+=1

    def _metadata(self,station):
        # Indexed station-local lookup; do not load all certification history.
        try:
            with self.store._connect() as db:
                deadline=time.monotonic()+.25
                db.set_progress_handler(lambda: int(time.monotonic()>=deadline),1000)
                row=db.execute("SELECT record_id FROM v11_records WHERE kind='REGISTRY' AND event_id=? "
                    "AND json_extract(body,'$.details.action')='METADATA' ORDER BY seq DESC LIMIT 1",('station:'+station,)).fetchone()
        except sqlite3.OperationalError:
            raise EvidenceError('DISCOVERY_METADATA_READ_UNAVAILABLE') from None
        if row is None:raise EvidenceError('DISCOVERY_STATION_METADATA_UNAVAILABLE')
        record=self.store.get(row['record_id']);d=record['body']['details'];value=dict(d['metadata'])
        for name in ('observation_providers','forecast_providers'):value[name]=tuple(value[name])
        metadata=StationMetadata(**value)
        if (metadata.station!=station or metadata.fingerprint!=d['metadata_fingerprint']
                or not 0 <= self.store.clock()-metadata.retrieved_at < self.policy.maximum_metadata_age_seconds):
            raise EvidenceError('DISCOVERY_STATION_METADATA_NOT_CURRENT')
        refs=record['body'].get('evidence',[])
        if len(refs)!=1:raise EvidenceError('DISCOVERY_METADATA_RAW_PROOF_REQUIRED')
        raw=self.store.get(refs[0]['id'])
        if (raw['sha256']!=refs[0]['sha256'] or raw['kind']!='STATION_METADATA'
                or digest(raw['body']['payload'])!=metadata.source_payload_sha256):
            raise EvidenceError('DISCOVERY_METADATA_RAW_PROOF_REQUIRED')
        return metadata,record

    def _event(self,scan_id,page,index):
        event=page['body']['payload']['response']['events'][index]
        event_id=identity(event.get('id'),maximum=128)
        key='discovery-event:'+digest([scan_id,page['id'],index]);prior=self._get(key)
        first_key='discovery-first:'+digest([scan_id,event_id])
        if prior:
            d=prior['body']['details']
            if not d['duplicate'] and self._get(first_key) is None:
                self.store.audit(first_key,event_id=KEY,kind='MEASUREMENT',details=d,evidence_ids=(key,))
            return prior
        first=self._get(first_key);now=finite(self.store.clock());event_sha=digest(event)
        duplicate=first is not None;revised=duplicate and first['body']['details']['event_sha256']!=event_sha
        raw_id=key+':raw';raw=self._get(raw_id)
        if raw is None:
            body=dict(provider='GAMMA_DISCOVERY_EVENT',source_identity=event_id,revision=page['id'],
                payload=dict(event=event,discovery_page_id=page['id'],discovery_page_sha256=page['sha256'],page_index=index),
                observed_at=None,issued_at=None,published_at=None,received_at=page['body']['received_at'],
                evidence_class=page['body']['evidence_class'],source_kind='RULES')
            raw=self.store._append(raw_id,'RULES',event_id,body,now,now)
        weather=WeatherOnlyDiscovery._weather_looking_candidate(event)
        classification,reason=WeatherOnlyDiscovery._semantic_classification(event) if weather else ('NOT_WEATHER','NOT_WEATHER')
        family=weather_family(event) if weather else 'NOT_WEATHER'
        guard=self.store.latest(kind='RULE_STATE',event_id=event_id)
        guard_id=None;metadata_id=None;rule_sha=None;binding_reason='UNSUPPORTED_OR_NOT_WEATHER'
        rule_key=key+':guard'
        if classification=='SUPPORTED':
            try:
                if (event.get('active') is not True or event.get('closed') is not False
                        or event.get('archived',False) is not False):
                    raise EvidenceError('DISCOVERY_ACTIVE_OPEN_STATE_UNVERIFIED')
                compiled=compile_strict_temperature_event(event)
                metadata,record=self._metadata(compiled.station_hint);metadata_id=record['id']
                rule=fingerprint_event(event,station_timezone=metadata.timezone,metadata_fingerprint=metadata.fingerprint)
                observed=self._get(rule_key) or RuleGuard(self.store).observe(rule_key,rule,raw_evidence_id=raw_id)
                guard_id=observed['id'];rule_sha=rule.sha256
                binding_reason='RULE_QUARANTINED' if observed['body']['details']['quarantined'] else 'OBSERVED_RULE_NOT_STRATEGY_CERTIFICATION'
            except EvidenceError as exc:
                binding_reason=str(exc)
        confirmed_closed = event.get('closed') is True or event.get('archived') is True or event.get('active') is False
        if guard_id is None and guard and (classification!='SUPPORTED' or confirmed_closed):
            try:
                rejected=self._get(rule_key) or RuleGuard(self.store).invalidate(rule_key,event_id=event_id,raw_evidence_id=raw_id,
                    reason=binding_reason if classification=='SUPPORTED' else reason)
                guard_id=rejected['id'] if rejected else None
            except EvidenceError as exc:
                binding_reason=str(exc)
        if weather:
            for stage,state,why in (('DISCOVERED','PASS','WEATHER_LOOKING_NOT_CERTIFIED'),
                ('SEMANTICALLY_SUPPORTED','PASS' if classification=='SUPPORTED' else 'GATED',reason)):
                funnel_key=key+':'+stage
                if self._get(funnel_key) is None:
                    self.store.funnel(funnel_key,event_id=event_id,strategy='SEMANTIC_CENSUS',stage=stage,state=state,reason=why,cycle_id=scan_id)
        # Clustering is a review aid only; it never rewrites the strict grammar.
        text=(str(event.get('description') or '')+' '+' '.join(str(x.get('description') or '') for x in event.get('markets',[])[:4]))[:8192]
        template=digest(dict(family=family,classification=classification,reason=reason,
                             normalized_text=re.sub(r'\d+','#',re.sub(r'\s+',' ',text.lower())))) if weather else None
        details=dict(version=VERSION,scan_id=scan_id,event_id=event_id,event_sha256=event_sha,page_id=page['id'],
            raw_event_id=raw_id,weather_looking=weather,classification=classification,reason=reason,family=family,
            template_sha256=template,duplicate=duplicate,duplicate_revision=bool(revised),metadata_record_id=metadata_id,
            rule_state_id=guard_id,rule_fingerprint=rule_sha,binding_reason=binding_reason,
            strategy_eligible=False,financial_authority=False,denominator='FIRST_RECEIPT_PER_EVENT_WITHIN_SCAN')
        result=self.store.audit(key,event_id=event_id,kind='MEASUREMENT',details=details,
            evidence_ids=tuple(x for x in (raw_id,guard_id,metadata_id) if x))
        if not duplicate:self.store.audit(first_key,event_id=KEY,kind='MEASUREMENT',details=details,evidence_ids=(key,))
        return result

    def _page(self,record_id,state):
        row=self.store.get(record_id);b=row['body'];p=b['payload']
        expected=catalog_request(state['cursor'],page_size=self.policy.page_size,revision='unused')
        response=p.get('response')
        if (row['kind']!='RULES' or row['event_id']!='v11-discovery-catalog' or b['provider']!='GAMMA_DISCOVERY'
                or b['source_identity']!=expected.source_identity or p.get('endpoint')!=ENDPOINT
                or p.get('request_params')!=dict(expected.params) or p.get('http_status')!=200
                or b['evidence_class'] not in {'PUBLIC_OBSERVED','SYNTHETIC'} or type(response) is not dict
                or set(response)-{'events','next_cursor'}):
            raise EvidenceError('DISCOVERY_PAGE_ENVELOPE_OR_SCOPE')
        if not 0 <= self.store.clock()-b['received_at'] < self.policy.maximum_page_age_seconds:
            raise EvidenceError('DISCOVERY_PAGE_RECEIPT_STALE')
        events=response.get('events');cursor=response.get('next_cursor')
        if (type(events) is not list or len(events)>self.policy.page_size
                or any(type(e) is not dict or type(e.get('markets',[])) is not list
                       or len(e.get('markets',[]))>64 or any(type(m) is not dict for m in e.get('markets',[])) for e in events)):
            raise EvidenceError('DISCOVERY_PAGE_EVENT_BOUND_OR_SCHEMA')
        for event in events:identity(event.get('id'),maximum=128)
        if cursor is not None:
            identity(cursor,maximum=512)
            if not events or cursor==state['cursor'] or digest(cursor) in state['seen_cursor_hashes']:
                raise EvidenceError('DISCOVERY_CURSOR_REPEATED_OR_EMPTY_PAGE')
        return row

    async def step(self,command_id):
        identity(command_id,maximum=80);final='discovery-step:'+digest(command_id)
        prior=self._get(final)
        if prior:
            if prior['body']['details'].get('config_sha256')!=self.config:raise EvidenceError('DISCOVERY_REPLAY_CONFIG')
            return prior
        fd=os.open(self.store.path.with_name(self.store.path.name+'.discovery.lock'),os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise EvidenceError('DISCOVERY_WORKER_ALREADY_RUNNING') from None
            head=self._head()
            if head is None:raise EvidenceError('DISCOVERY_SCAN_NOT_STARTED')
            state=deepcopy(head['body']['details']['state'])
            health=self._sample(command_id,'before')
            if health['body']['details']['clock_reasons']:
                return self._save(final,state,outcome='DEFERRED_CLOCK_UNHEALTHY')
            if state['phase'] in {'COMPLETE','INCOMPLETE'}:
                return self._save(final,state,outcome='SCAN_TERMINAL',summary=self.summary(state))
            if not 0 <= self.store.clock()-state['started_at'] < self.policy.maximum_scan_seconds:
                state['phase']='INCOMPLETE';state['errors'].append('SCAN_TIME_BOUND')
                return self._save(final,state,outcome='SCAN_TIME_BOUND',summary=self.summary(state))
            if state['page_id'] is None:
                if state['pages']>=self.policy.maximum_pages:
                    state['phase']='INCOMPLETE';state['errors'].append('PAGE_BOUND')
                    return self._save(final,state,outcome='PAGE_BOUND',summary=self.summary(state))
                if state['pending_cycle']:
                    raw=self._get(state['pending_cycle']+':0:capture')
                    if raw is None:
                        state['pending_cycle']=None;state['page_failures']+=1
                        if state['page_failures']>=self.policy.maximum_page_failures:state['phase']='INCOMPLETE'
                        return self._save(final,state,outcome='INTERRUPTED_REQUEST_NOT_REPEATED',summary=self.summary(state))
                else:
                    state['attempt']+=1;state['pending_cycle']='discovery-get:'+digest([state['scan_id'],state['pages'],state['attempt']])
                    self._progress(command_id,state,outcome='REQUEST_RESERVED')
                    request=catalog_request(state['cursor'],page_size=self.policy.page_size,revision=state['pending_cycle'])
                    collected=await self.scheduled.cycle(state['pending_cycle'],(request,))
                    raw=self._get(state['pending_cycle']+':0:capture')
                    if raw is None:
                        state['pending_cycle']=None
                        if collected['sources']:state['page_failures']+=1
                        if state['page_failures']>=self.policy.maximum_page_failures:state['phase']='INCOMPLETE'
                        return self._save(final,state,outcome='PAGE_UNAVAILABLE_OR_COOLDOWN',summary=self.summary(state))
                try:page=self._page(raw['id'],state)
                except EvidenceError as exc:
                    state['phase']='INCOMPLETE';state['errors'].append(str(exc))
                    return self._save(final,state,outcome='PAGE_REJECTED',summary=self.summary(state))
                state.update(page_id=page['id'],page_position=0,next_cursor=page['body']['payload']['response'].get('next_cursor'),
                    pending_cycle=None,page_failures=0,pages=state['pages']+1,last_receipt_at=page['body']['received_at'])
                if state['first_receipt_at'] is None:state['first_receipt_at']=state['last_receipt_at']
                if state['next_cursor'] is not None:state['seen_cursor_hashes'].append(digest(state['next_cursor']))
                self._progress(command_id,state,outcome='RAW_PAGE_PERSISTED')
            page=self.store.get(state['page_id']);events=page['body']['payload']['response']['events']
            health=self._sample(command_id,'after')
            if health['body']['details']['clock_reasons']:
                return self._save(final,state,outcome='DEFERRED_CLOCK_CHANGED',summary=self.summary(state))
            if not 0 <= self.store.clock()-page['body']['received_at'] < self.policy.maximum_page_age_seconds:
                state['phase']='INCOMPLETE';state['errors'].append('PAGE_EXPIRED_DURING_PROCESSING')
                return self._save(final,state,outcome='PAGE_EXPIRED',summary=self.summary(state))
            deadline=time.monotonic()+self.policy.maximum_processing_seconds;processed=[]
            for _ in range(self.policy.maximum_events_per_step):
                if state['page_position']>=len(events) or time.monotonic()>=deadline:break
                if state['hits']>=self.policy.maximum_event_hits:
                    state['phase']='INCOMPLETE';state['errors'].append('EVENT_HIT_BOUND');break
                event=self._event(state['scan_id'],page,state['page_position']);d=event['body']['details'];processed.append(event['id'])
                state['hits']+=1;state['page_position']+=1
                if d['duplicate']:
                    state['duplicates']+=1;state['duplicate_revisions']+=int(d['duplicate_revision'])
                else:
                    state['unique_events']+=1
                    if d['weather_looking']:
                        state['weather_looking']+=1;state['strict_supported']+=int(d['classification']=='SUPPORTED')
                        state['unsupported']+=int(d['classification']!='SUPPORTED')
                        self._counter(state,'family_counts',d['family']);self._counter(state,'template_counts',d['template_sha256'])
                        if d['classification']!='SUPPORTED':self._counter(state,'reason_counts',d['reason'])
                self._progress(command_id,state,outcome='EVENT_ARCHIVED')
            if state['phase']!='INCOMPLETE' and state['page_position']==len(events):
                state['cursor']=state['next_cursor'];state['page_id']=None;state['page_position']=0
                if state['cursor'] is None:state['phase']='COMPLETE';state['finished_at']=finite(self.store.clock())
            return self._save(final,state,outcome='BOUNDED_DISCOVERY_STEP',processed_event_ids=processed,summary=self.summary(state))
        finally:os.close(fd)

    def summary(self,state=None):
        if state is None:
            head=self._head()
            if head is None:return dict(catalog_traversal_complete=False,current_universe_verified=False)
            state=head['body']['details']['state']
        return dict(catalog_traversal_complete=state['phase']=='COMPLETE',current_universe_verified=False,
            atomic_catalog_snapshot=False,phase=state['phase'],scan_id=state['scan_id'],pages=state['pages'],
            event_hits=state['hits'],distinct_events=state['unique_events'],weather_looking_events=state['weather_looking'],
            strict_supported_events=state['strict_supported'],unsupported_events=state['unsupported'],
            unsupported_reason_counts=state['reason_counts'],semantic_family_counts=state['family_counts'],
            template_counts=state['template_counts'],counter_overflow=state['counter_overflow'],
            duplicate_hits=state['duplicates'],duplicate_revisions=state['duplicate_revisions'],
            first_receipt_at=state['first_receipt_at'],last_receipt_at=state['last_receipt_at'],finished_at=state['finished_at'],
            coverage_age_seconds=None if state['first_receipt_at'] is None else self.store.clock()-state['first_receipt_at'],
            semantic_coverage_complete=state['phase']=='COMPLETE' and state['weather_looking']>0 and not state['unsupported'] and not state['duplicate_revisions'] and not state['counter_overflow'],
            strategy_eligible=False,financial_authority=False)
