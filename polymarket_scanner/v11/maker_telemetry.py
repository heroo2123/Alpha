"""Bounded updates for retained research quotes; never fills or trading P&L.

No collector, quote creation, order transport or account mutation. Markout
windows close before publication, so early missing books cannot freeze a later
causal observation as UNKNOWN. The existing engine selects the first eligible
book, not the best later price. Retired quote history is preserved.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
import fcntl
import os
import time

from .evidence import EvidenceError, digest, finite, identity
from .maker_research import MakerResearch, _restore
from .measurement import MARKOUT_SECONDS
from .microstructure import MakerMicrostructure, MicrostructurePolicy
from .runtime_health import RuntimeHealth, KEY as HEALTH_KEY
from .scenario_risk import number


VERSION = 'alpha_v11_maker_telemetry_v1'


@dataclass(frozen=True)
class MakerTelemetryPolicy:
    version: str
    maximum_actions: int = 4
    maximum_seconds: float = 1.
    tolerance_seconds: float = 2.
    fee_per_share: str | None = None

    def __post_init__(self):
        identity(self.version)
        if (type(self.maximum_actions) is not int or not 1 <= self.maximum_actions <= 16
                or not 0 < finite(self.maximum_seconds) <= 2
                or not 0 <= finite(self.tolerance_seconds) <= 60
                or self.fee_per_share is not None and not 0 <= number(self.fee_per_share) <= 1):
            raise EvidenceError('MAKER_TELEMETRY_POLICY_BOUND')


class MakerTelemetryWorker:
    def __init__(self,research,health,policy,*,event_ids):
        if (not isinstance(research,MakerResearch) or not isinstance(health,RuntimeHealth)
                or not isinstance(policy,MakerTelemetryPolicy) or research.store is not health.store
                or research.coordinator.policy.account_id != health.account_id
                or type(event_ids) is not tuple or not 1 <= len(event_ids) <= 16
                or len(set(event_ids)) != len(event_ids)
                or any(e not in health.scopes or 'MAKER_RESEARCH' not in health.scopes[e] for e in event_ids)):
            raise EvidenceError('MAKER_TELEMETRY_COMPONENT_SCOPE')
        self.research,self.health,self.store,self.policy = research,health,research.store,policy
        self.event_ids=event_ids
        self.config=digest(dict(research=research.policy_sha,health=health.config,policy=asdict(policy),events=event_ids))
        self.key='maker-telemetry:'+digest([self.store.namespace,health.account_id])

    def _get(self,key):
        try:return self.store.get(key)
        except EvidenceError as exc:
            if str(exc)!='EVIDENCE_MISSING':raise
        return None

    def _head(self):
        row=self.store.latest(kind='RUNTIME_STATUS',event_id=self.key)
        if row and row['body']['details'].get('config_sha256')!=self.config:
            raise EvidenceError('MAKER_TELEMETRY_CONFIGURATION_CHANGED_REVIEW_REQUIRED')
        return row

    def _save(self,key,state,**details):
        head=self._head()
        return self.store.audit(key,event_id=self.key,kind='RUNTIME_STATUS',details=dict(
            version=VERSION,config_sha256=self.config,state=state,**details,
            financial_authority=False,orders_sent=False,account_ledger_mutated=False,
            measurement_class='RESEARCH_OBSERVATIONS_NOT_FILL_OR_TRADING_PNL'),
            expected_previous_seq=head['seq'] if head else 0)

    def _progress(self,step_id,state,**details):
        head=self._head()
        return self._save('maker-progress:'+digest([step_id,head['seq'] if head else 0]),state,**details)

    def _clock(self):
        row=self.store.latest(kind='RUNTIME_STATUS',event_id=HEALTH_KEY)
        if (row is None or row['body']['details'].get('config_sha256')!=self.health.config
                or row['body']['details']['clock_reasons']
                or not 0 <= self.store.clock()-row['body']['recorded_at'] < self.health.policy.maximum_sample_age_seconds):
            raise EvidenceError('MAKER_TELEMETRY_CLOCK_UNVERIFIED')

    def _mark_key(self,quote_id,q,horizon):
        return 'maker-markout:'+digest([self.config,quote_id,q['origin_record_id'],horizon])

    def _select(self,state):
        quotes=self.research._state(self.research._head());now=finite(self.store.clock())
        if len(quotes)>self.research.policy.maximum_retained_quotes:
            raise EvidenceError('MAKER_TELEMETRY_RETENTION_BOUND')
        keys=sorted(quotes)
        keys=[k for k in keys if k>state['last_quote']]+[k for k in keys if k<=state['last_quote']]
        for key in keys:
            q=quotes[key];request=_restore(q['request'])
            if request.context.event_id not in self.event_ids:continue
            base=dict(quote_id=key,origin_record_id=q['origin_record_id'])
            observation=None
            if q['status']=='OBSERVING':
                try:
                    if now>=q['expires_at']:raise EvidenceError('MAKER_QUOTE_EXPIRED')
                    self.research._admission(request,now)
                except EvidenceError as exc:
                    return dict(base,action='RETIRE',reason=str(exc))
                initial=self.store.get(q['initial_book_id'])['body']
                book=self.store.latest_source(kind='BOOK',event_id=request.context.event_id,
                    provider=initial['provider'],source_identity=initial['source_identity'])
                if book is None:return dict(base,action='RETIRE',reason='MAKER_BOOK_UNAVAILABLE')
                if book['id']!=q['last_book_id']:
                    observation=dict(base,action='OBSERVE',book_ids=[q['last_book_id'],book['id']])
                else:
                    try:self.research._feature(q['last_microstructure_id'],request,now)
                    except EvidenceError as exc:return dict(base,action='RETIRE',reason=str(exc))
            for horizon in MARKOUT_SECONDS:
                if now>=q['created_at']+horizon+self.policy.tolerance_seconds:
                    mark=self._mark_key(key,q,horizon)
                    if self._get(mark) is None:return dict(base,action='MARKOUT',horizon=horizon,mark_id=mark)
            # Each quote has only five markout windows. Service due windows
            # before fresh sampling so a continuous book stream cannot starve
            # them; bounded round-robin rotation still services other quotes.
            if observation is not None:return observation
        return None

    def _execute(self,command):
        q=self.research._state(self.research._head()).get(command['quote_id'])
        if q is None or q['origin_record_id']!=command['origin_record_id']:
            raise EvidenceError('MAKER_TELEMETRY_QUOTE_IDENTITY_CHANGED')
        key=command['id'];action=command['action']
        # A child committed before an interruption is returned unchanged. Do not
        # re-observe at a newer clock tick or replace the chosen horizon book.
        saved=self._get(key if action!='MARKOUT' else command['mark_id'])
        if saved is not None:return saved
        if action=='RETIRE':return self.research.retire(key,quote_id=command['quote_id'],reason=command['reason'])
        if action=='MARKOUT':
            return self.research.markout(command['mark_id'],quote_id=command['quote_id'],horizon_seconds=command['horizon'],
                tolerance_seconds=self.policy.tolerance_seconds,fee_per_share=self.policy.fee_per_share)
        if action!='OBSERVE':raise EvidenceError('MAKER_TELEMETRY_ACTION_INVALID')
        if q['status']!='OBSERVING':
            return self.research.retire(key,quote_id=command['quote_id'],reason='ALREADY_RETIRED_DURING_TELEMETRY')
        request=_restore(q['request'])
        features=MakerMicrostructure(self.store).evaluate('maker-features:'+digest(key),rule=request.rule,
            market_id=request.market_id,side=request.side,book_ids=tuple(command['book_ids']),trade_ids=(),
            policy=MicrostructurePolicy(**q['book_policy']))
        return self.research.observe(key,quote_id=command['quote_id'],microstructure_id=features['id'])

    def step(self,step_id):
        identity(step_id,maximum=100);final='maker-telemetry-step:'+digest(step_id)
        saved=self._get(final)
        if saved is not None:
            if saved['body']['details'].get('config_sha256')!=self.config:raise EvidenceError('MAKER_TELEMETRY_REPLAY_CONFIG')
            return saved
        fd=os.open(self.store.path.with_name(self.store.path.name+'.maker-telemetry.lock'),
            os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise EvidenceError('MAKER_TELEMETRY_ALREADY_RUNNING') from None
            saved=self._get(final)
            if saved is not None:
                if saved['body']['details'].get('config_sha256')!=self.config:raise EvidenceError('MAKER_TELEMETRY_REPLAY_CONFIG')
                return saved
            self._clock();head=self._head()
            state=deepcopy(head['body']['details']['state']) if head else dict(last_quote='',pending=None,sequence=0)
            start=time.monotonic();results=[]
            while len(results)<self.policy.maximum_actions and time.monotonic()-start<self.policy.maximum_seconds:
                self._clock()
                if state['pending'] is None:
                    action=self._select(state)
                    if action is None:break
                    state['sequence']+=1
                    state['pending']=dict(action,id='maker-action:'+digest([self.config,state['sequence'],action]))
                    self._progress(step_id,state,outcome='ACTION_RESERVED')
                row=self._execute(state['pending'])
                results.append(dict(action=state['pending']['action'],quote_id=state['pending']['quote_id'],record_id=row['id']))
                state['last_quote']=state['pending']['quote_id'];state['pending']=None
                self._progress(step_id,state,outcome='ACTION_COMPLETED',result=results[-1])
            elapsed=time.monotonic()-start
            return self._save(final,state,outcome='BOUNDED_MAKER_TELEMETRY',results=results,
                elapsed_seconds=elapsed,cooperative_budget_overrun_seconds=max(0,elapsed-self.policy.maximum_seconds),
                horizons=list(MARKOUT_SECONDS),publication_waits_for_closed_tolerance_window=True,
                configured_fee_is_declared_hypothetical_cost_not_verified_venue_fee=True,
                public_trade_flow='NOT_COLLECTED_BY_THIS_WORKER',new_quote_creation=False)
        finally:os.close(fd)
