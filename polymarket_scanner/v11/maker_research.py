"""Durable non-executing quotes and sampled eligibility, never hypothetical fills.

Risk projections reuse the common PAPER account without mutating its ledger.
They cannot reserve cash, authorize orders or replace first-canary commissioning.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
from decimal import Decimal

from .evidence import EvidenceError, ReleaseBinding, canonical, digest, finite, identity
from .event_risk import EventContext, EventRiskEngine, SafetyReductions
from .measurement import MARKOUT_SECONDS, executable_depth
from .microstructure import VERSION as MICRO_VERSION, MicrostructurePolicy, _frame, _link, _source
from .paper_coordinator import ACCOUNT_KEY
from .rules import RuleFingerprint
from .scenario_risk import number, precise
from .strategy_admission import StrategyAdmission
from .valuation import contract_target


VERSION = 'alpha_v11_maker_research_v1'


@dataclass(frozen=True)
class MakerResearchPolicy:
    version: str
    maximum_quote_seconds: float
    maximum_feature_age_seconds: float
    maximum_units: str
    maximum_observing_quotes: int
    maximum_retained_quotes: int

    def __post_init__(self):
        identity(self.version)
        if (not 0<finite(self.maximum_quote_seconds)<=120
                or not 0<finite(self.maximum_feature_age_seconds)<=30
                or not 0<number(self.maximum_units)<=1_000_000
                or type(self.maximum_observing_quotes) is not int or not 1<=self.maximum_observing_quotes<=16
                or type(self.maximum_retained_quotes) is not int
                or not self.maximum_observing_quotes<=self.maximum_retained_quotes<=128):
            raise EvidenceError('MAKER_RESEARCH_POLICY_BOUND')


@dataclass(frozen=True)
class ResearchQuote:
    quote_id: str
    thesis_id: str
    context: EventContext
    rule: RuleFingerprint
    binding: ReleaseBinding
    admission_id: str
    event_state_id: str
    microstructure_id: str
    market_id: str
    side: str
    direction: str
    limit_price: str
    units: str
    unit_cost_reserve: str
    expires_at: float

    def __post_init__(self):
        for value in (self.quote_id,self.thesis_id,self.admission_id,self.event_state_id,self.microstructure_id):
            identity(value,maximum=100)
        if (self.direction not in {'BUY','SELL'} or not 0<number(self.limit_price)<1
                or not 0<number(self.units)<=1_000_000 or not 0<=number(self.unit_cost_reserve)<=1):
            raise EvidenceError('MAKER_RESEARCH_QUOTE_BOUND')
        finite(self.expires_at);contract_target(self.rule,self.market_id,self.side)
        if (self.context.event_id!=self.rule.payload['event_id'] or self.context.station_id!=self.rule.payload['station']
                or self.binding.rule_fingerprint!=self.rule.sha256):raise EvidenceError('MAKER_RULE_SCOPE')


def _restore(raw):
    kw=dict(raw)
    kw['context']=EventContext(**kw['context']);kw['binding']=ReleaseBinding(**kw['binding'])
    kw['rule']=RuleFingerprint(**kw['rule'])
    return ResearchQuote(**kw)


def _heads(values):
    result={}
    for kind,event,seq in values:
        if (kind,event) in result and result[kind,event]!=seq:raise EvidenceError('MAKER_STATE_CHANGED_RECOMPUTE')
        result[kind,event]=seq
    return tuple((kind,event,seq) for (kind,event),seq in sorted(result.items()))


class MakerResearch:
    def __init__(self,coordinator,policy):
        if not isinstance(policy,MakerResearchPolicy):raise EvidenceError('MAKER_RESEARCH_POLICY_REQUIRED')
        self.coordinator=coordinator;self.store=coordinator.store;self.policy=policy
        self.key='maker-research:'+digest([self.store.namespace,coordinator.policy.account_id])
        self.policy_sha=digest(dict(research=asdict(policy),account=coordinator.policy_sha))

    def _head(self):
        row=self.store.latest(kind='MEASUREMENT',event_id=self.key)
        if row and (row['body']['details'].get('version')!=VERSION
                    or row['body']['details'].get('policy_sha256')!=self.policy_sha):
            raise EvidenceError('MAKER_RESEARCH_POLICY_CHANGED')
        return row

    def _state(self,row):return deepcopy(row['body']['details']['quotes']) if row else {}

    def _replay(self,key,request,*,event_id=None):
        identity(key,maximum=100)
        try:row=self.store.get(key)
        except EvidenceError as exc:
            if str(exc)=='EVIDENCE_MISSING':return None
            raise
        d=row['body'].get('details',{})
        if (row['kind']!='MEASUREMENT' or row['event_id']!=(event_id or self.key)
                or d.get('version')!=VERSION or d.get('policy_sha256')!=self.policy_sha
                or canonical(d.get('request'))!=canonical(request)):
            raise EvidenceError('MAKER_RESEARCH_REPLAY_CONFLICT')
        return row

    def _commit(self,key,request,previous,quotes,*,outcome,reason,refs=(),heads=(),**details):
        audit=self.store.safety_audit if request.get('action')=='RETIRE' else self.store.audit
        return audit(key,event_id=self.key,kind='MEASUREMENT',details=dict(
            version=VERSION,policy_sha256=self.policy_sha,request=request,quotes=quotes,
            outcome=outcome,reason=reason,**details,financial_authority=False,orders_submitted=False,
            account_ledger_mutated=False,actual_trading_pnl=None,fill_probability=None,queue_position=None),
            evidence_ids=tuple(dict.fromkeys(refs)),expected_previous_seq=previous['seq'] if previous else 0,
            expected_heads=_heads(heads))

    def _feature(self,key,quote,now):
        row=self.store.get(key);d=row['body'].get('details',{});event=quote.context.event_id
        target=contract_target(quote.rule,quote.market_id,quote.side)
        if (row['kind']!='MEASUREMENT' or row['event_id']!=event or d.get('version')!=MICRO_VERSION
                or d.get('outcome')!='MEASURED_RESEARCH_FEATURES' or d['request']['target']!=target
                or d['request']['rule']!=asdict(quote.rule)
                or not d['as_of']<=now<=d['as_of']+self.policy.maximum_feature_age_seconds):
            raise EvidenceError('MAKER_CURRENT_EXACT_FEATURE_REQUIRED')
        policy=MicrostructurePolicy(**d['request']['policy'])
        if policy.collateral_asset!=self.coordinator.policy.collateral_asset:raise EvidenceError('MAKER_COLLATERAL_MISMATCH')
        head=self.store.latest(kind='BOOK',event_id=event)
        book=_source(self.store,d['current_book_id'],rule=quote.rule,target=target,kind='BOOK',cutoff=now,policy=policy)
        body=book['body'];latest=self.store.latest_source(kind='BOOK',event_id=event,
                  provider=body['provider'],source_identity=body['source_identity'])
        if (latest['id']!=book['id'] or now-body['observed_at']>policy.maximum_book_age_seconds
                or now-body['received_at']>policy.maximum_book_age_seconds):raise EvidenceError('MAKER_BOOK_NOT_CURRENT')
        computed=_frame(book,policy)
        if any(d['features'].get(k)!=v for k,v in computed.items()):raise EvidenceError('MAKER_BOOK_FEATURE_REPRODUCTION')
        price=number(quote.limit_price)
        if (quote.direction=='BUY' and price>=number(computed['best_ask'])
                or quote.direction=='SELL' and price<=number(computed['best_bid'])):
            raise EvidenceError('MAKER_QUOTE_WOULD_CROSS_POST_ONLY_BOUNDARY')
        return d,book,policy,('BOOK',event,head['seq'])

    def _admission(self,quote,now):
        c=self.coordinator;context=quote.context
        if context.account_id!=c.policy.account_id:raise EvidenceError('MAKER_ACCOUNT_MISMATCH')
        member=next((m for m in c.correlation.memberships if m.station==context.station_id),None)
        if not member or member.city!=context.city_id or member.metadata_fingerprint!=quote.rule.payload['metadata_fingerprint']:
            raise EvidenceError('MAKER_CITY_METADATA_SCOPE')
        from .runtime_health import admission_heads
        heads=list(SafetyReductions(self.store).atomic_heads(context))
        heads.extend(admission_heads(self.store, account_id=context.account_id,
                     event_id=context.event_id, strategies=('MAKER_RESEARCH',)))
        event=EventRiskEngine(self.store).revalidate(quote.event_state_id)
        event_row=self.store.get(quote.event_state_id)
        heads.append(('COORDINATOR_EVENT',event_row['event_id'],event_row['seq']))
        if event['request']['context']!=asdict(context) or event['request']['binding']!=asdict(quote.binding):
            raise EvidenceError('MAKER_EVENT_BINDING')
        if event['state'] not in {'NORMAL','CAUTION'} or any(event['safety']['flags'].values()):
            raise EvidenceError('MAKER_PASSIVE_RESEARCH_SUPPRESSED')
        admission=StrategyAdmission(self.store).revalidate(quote.admission_id,context=context,rule=quote.rule,
                      binding=asdict(quote.binding),strategies=('MAKER_RESEARCH',))
        heads.extend(admission['heads'])
        size_ceiling=min(number(self.policy.maximum_units),number(c.limits.max_position_units))*\
                    Decimal(str(event['guard']['size_multiplier']))*Decimal(str(admission['model_size_multiplier']))
        if number(quote.units)>size_ceiling:raise EvidenceError('MAKER_STATE_ADJUSTED_SIZE_BOUND')
        expiry=min(quote.expires_at,now+self.policy.maximum_quote_seconds*event['guard']['lifetime_multiplier'],
                   event['valid_until'],admission['valid_until'])
        if expiry<=now:raise EvidenceError('MAKER_QUOTE_EXPIRED')
        return event,admission,expiry,heads

    @precise
    def _project(self,account,quotes):
        """Common account risk including hypothetical adverse fills, without writes."""
        state=deepcopy(account);c=self.coordinator
        for q in quotes.values():
            if q['status']!='OBSERVING':continue
            request=_restore(q['request']);event=request.context.event_id
            if event in state['rules'] and state['rules'][event]['sha256']!=request.rule.sha256:
                raise EvidenceError('MAKER_ACCOUNT_RULE_CHANGED')
            if event in state['contexts'] and state['contexts'][event]!=asdict(request.context):
                raise EvidenceError('MAKER_ACCOUNT_CONTEXT_CHANGED')
            state['rules'][event]=asdict(request.rule);state['contexts'][event]=asdict(request.context)
            bound=number(request.limit_price)+(1 if request.direction=='BUY' else -1)*number(request.unit_cost_reserve)
            if not 0<=bound<=2:raise EvidenceError('MAKER_DECLARED_COLLATERAL_BOUND')
            if request.direction=='BUY' and bound*number(request.units)>c.policy_amount('per_intent_cash_limit'):
                raise EvidenceError('MAKER_PER_INTENT_CASH_BOUND')
            slot='maker-research:'+request.quote_id
            if slot in state['intents']:raise EvidenceError('MAKER_ACCOUNT_INTENT_ID_COLLISION')
            state['intents'][slot]=dict(proposal_id=slot,event_id=event,token_id=q['target']['token_id'],
                direction=request.direction,units=request.units,filled_units='0',unit_collateral_bound=str(bound),
                attribution=[dict(strategy='MAKER_RESEARCH',weight='1')],status='RESERVED')
        risk=c._risk(state)
        if not risk['accepted']:raise EvidenceError('MAKER_PROJECTED_COMMON_ACCOUNT_RISK')
        return dict(risk,classification='HYPOTHETICAL_QUOTE_OVERLAY_NOT_ACCOUNT_RESERVATION',
                    cost_authority='DECLARED_RESEARCH_BOUND_NOT_VERIFIED_VENUE_FEE',inventory_source='COMMON_PAPER_ACCOUNT')

    def propose(self,key,quote):
        if not isinstance(quote,ResearchQuote):raise EvidenceError('MAKER_QUOTE_REQUEST_REQUIRED')
        request=dict(action='PROPOSE',quote=asdict(quote));prior=self._replay(key,request)
        if prior:return prior
        previous=self._head();quotes=self._state(previous);now=finite(self.store.clock());refs=[];heads=[];risk=None
        try:
            if len(quotes)>=self.policy.maximum_retained_quotes:raise EvidenceError('MAKER_RETENTION_BOUND_NO_PRUNING')
            if quote.quote_id in quotes:raise EvidenceError('MAKER_QUOTE_ID_ALREADY_RECORDED')
            active=[q for q in quotes.values() if q['status']=='OBSERVING']
            if len(active)>=self.policy.maximum_observing_quotes:raise EvidenceError('MAKER_OBSERVING_QUOTE_BOUND')
            target=contract_target(quote.rule,quote.market_id,quote.side)
            if any(q['request']['thesis_id']==quote.thesis_id or q['target']['token_id']==target['token_id'] for q in active):
                raise EvidenceError('MAKER_EXISTING_THESIS_OR_TOKEN_RESEARCH_QUOTE')
            account_head=self.coordinator._head();account=self.coordinator._state(account_head)
            heads.append(('COORDINATOR_EVENT',ACCOUNT_KEY,account_head['seq'] if account_head else 0))
            event,admission,expiry,guard=self._admission(quote,now);heads.extend(guard)
            features,book,mp,guard=self._feature(quote.microstructure_id,quote,now);heads.append(guard)
            refs.extend((quote.admission_id,quote.event_state_id,quote.microstructure_id,book['id']))
            quotes[quote.quote_id]=dict(request=asdict(quote),target=target,status='OBSERVING',created_at=now,
                expires_at=expiry,origin_record_id=key,initial_book_id=book['id'],last_book_id=book['id'],
                initial_microstructure_id=quote.microstructure_id,last_microstructure_id=quote.microstructure_id,
                last_sample_at=now,sample_run_started_at=now,sampled_span_seconds=0.,gap_count=0,
                distinct_book_samples=1,book_policy=asdict(mp),source_heads=admission['heads'],
                event_state=event['state'],retired_at=None,retirement_reason=None,
                eligibility_class='SAMPLED_RESEARCH_QUOTE_NOT_EXCHANGE_SURVIVAL',fill_status='UNKNOWN_NOT_AN_ORDER')
            risk=self._project(account,quotes)
            return self._commit(key,request,previous,quotes,outcome='OBSERVING_RESEARCH_QUOTE',
                reason='BOUNDED_HYPOTHETICAL_RISK_NOT_EXECUTION_ELIGIBILITY',refs=refs,heads=heads,projected_risk=risk)
        except EvidenceError as exc:
            return self._commit(key,request,previous,self._state(previous),outcome='GATED',reason=str(exc),refs=refs)

    def revalidate_admission(self, quote_id):
        """Safety check independent of new books, telemetry jobs or queue work."""
        quotes = self._state(self._head())
        if quote_id not in quotes or quotes[quote_id]['status'] != 'OBSERVING':
            raise EvidenceError('MAKER_OBSERVING_QUOTE_REQUIRED')
        quote = quotes[quote_id]
        now = finite(self.store.clock())
        if now >= quote['expires_at']: raise EvidenceError('MAKER_QUOTE_EXPIRED')
        self._admission(_restore(quote['request']), now)

    def observe(self,key,*,quote_id,microstructure_id):
        request=dict(action='OBSERVE',quote_id=identity(quote_id),microstructure_id=identity(microstructure_id))
        prior=self._replay(key,request)
        if prior:return prior
        previous=self._head();quotes=self._state(previous)
        if quote_id not in quotes:raise EvidenceError('MAKER_QUOTE_MISSING')
        q=quotes[quote_id];quote=_restore(q['request']);now=finite(self.store.clock())
        refs=[q['origin_record_id']];heads=[];reason='SAMPLED_PASSIVE_ELIGIBILITY';gap=None;risk=None
        if q['status']!='OBSERVING':raise EvidenceError('MAKER_RETIRED_QUOTE_CANNOT_RESUME')
        try:
            if now>=q['expires_at']:raise EvidenceError('MAKER_QUOTE_EXPIRED')
            account_head=self.coordinator._head();account=self.coordinator._state(account_head)
            heads.append(('COORDINATOR_EVENT',ACCOUNT_KEY,account_head['seq'] if account_head else 0))
            event,admission,_,guard=self._admission(quote,now);heads.extend(guard)
            features,book,mp,guard=self._feature(microstructure_id,quote,now);heads.append(guard)
            refs.extend((microstructure_id,book['id']))
            if asdict(mp)!=q['book_policy']:raise EvidenceError('MAKER_BOOK_POLICY_CHANGED')
            risk=self._project(account,quotes)
            if book['id']!=q['last_book_id']:
                gap=_link(self.store.get(q['last_book_id']),book,mp)
                if now-q['last_sample_at']>mp.maximum_gap_seconds:gap='RESEARCH_SAMPLING_GAP'
                if gap:q['sample_run_started_at']=now;q['gap_count']+=1
                q['sampled_span_seconds']=now-q['sample_run_started_at']
                q['distinct_book_samples']+=1
            else:reason='NO_NEW_BOOK_OBSERVATION_NO_SURVIVAL_ADVANCEMENT'
            q.update(last_book_id=book['id'],last_microstructure_id=microstructure_id,last_sample_at=now,event_state=event['state'])
        except EvidenceError as exc:
            reason=str(exc);q.update(status='RETIRED',retired_at=now,retirement_reason=reason)
            heads=[]  # Local research withdrawal cannot create exposure or release ledger cash.
        return self._commit(key,request,previous,quotes,outcome=q['status'],reason=reason,refs=refs,heads=heads,
                quote_age_seconds=now-q['created_at'],continuity_reset=gap,projected_risk=risk,
                cancellation_status='NOT_APPLICABLE_NO_ORDER',current_inventory_unchanged=True)

    def retire(self,key,*,quote_id,reason):
        request=dict(action='RETIRE',quote_id=identity(quote_id),reason=identity(reason));prior=self._replay(key,request)
        if prior:return prior
        previous=self._head();quotes=self._state(previous)
        if quote_id not in quotes:raise EvidenceError('MAKER_QUOTE_MISSING')
        q=quotes[quote_id]
        if q['status']=='OBSERVING':q.update(status='RETIRED',retired_at=finite(self.store.clock()),retirement_reason=reason)
        return self._commit(key,request,previous,quotes,outcome='RETIRED',reason=q['retirement_reason'],
                refs=(q['origin_record_id'],),cancellation_status='NOT_APPLICABLE_NO_ORDER',current_inventory_unchanged=True)

    def context(self,key,**request):
        from .maker_context import measure_context
        return measure_context(self,key,**request)

    @precise
    def markout(self,key,*,quote_id,horizon_seconds,tolerance_seconds,fee_per_share=None):
        if horizon_seconds not in MARKOUT_SECONDS or not 0<=finite(tolerance_seconds)<=60:
            raise EvidenceError('MAKER_MARKOUT_HORIZON')
        if fee_per_share is not None and not 0<=number(fee_per_share)<=1:raise EvidenceError('MAKER_MARKOUT_FEE_BOUND')
        quote=self._state(self._head()).get(quote_id)
        if not quote:raise EvidenceError('MAKER_QUOTE_MISSING')
        q=_restore(quote['request']);event=q.context.event_id
        request=dict(action='MARKOUT',quote_id=quote_id,horizon_seconds=horizon_seconds,
                     tolerance_seconds=tolerance_seconds,fee_per_share=fee_per_share)
        prior=self._replay(key,request,event_id=event)
        if prior:return prior
        now=finite(self.store.clock());target_at=quote['created_at']+horizon_seconds
        result=dict(status='UNKNOWN',reason='HORIZON_NOT_REACHED' if now<target_at else 'NO_CAUSAL_HORIZON_BOOK',
                    markout_per_share=None,book_id=None,source_class=None,scanned_records=0)
        initial=self.store.get(quote['initial_book_id']);origin=self.store.get(quote['origin_record_id'])
        refs=[origin['id']];page=origin['seq'];chosen=None;finished=False
        if now>=target_at:
            for _ in range(10):  # At most 2,000 archived source records; no unbounded catch-up.
                rows=self.store.records(kind='BOOK',event_id=event,after_seq=page,limit=200)
                result['scanned_records']+=len(rows)
                if not rows:finished=True;break
                page=rows[-1]['seq']
                for row in rows:
                    b=row['body']
                    if b['recorded_at']>min(now,target_at+tolerance_seconds):finished=True;break
                    if (row['kind']=='BOOK' and b['provider']==initial['body']['provider']
                            and b['source_identity']==initial['body']['source_identity'] and b['received_at']>=target_at):
                        chosen=row;finished=True;break
                if finished:break
            if not finished:result['reason']='BOUNDED_SCAN_INCOMPLETE'
        if chosen:
            refs.append(chosen['id']);b=chosen['body'];result.update(book_id=chosen['id'],source_class=b['evidence_class'])
            try:
                p=b['payload']
                if (b['evidence_class']=='HISTORICAL_AVAILABILITY_UNKNOWN' or b['observed_at'] is None
                        or b['available_at']>min(now,target_at+tolerance_seconds)
                        or not target_at<=b['observed_at']<=b['received_at']<=min(now,target_at+tolerance_seconds)):
                    raise EvidenceError('HORIZON_BOOK_NOT_CAUSAL')
                if (any(p.get(k)!=v for k,v in quote['target'].items()) or p.get('rule_fingerprint')!=q.rule.sha256
                        or p.get('collateral_asset')!=self.coordinator.policy.collateral_asset):
                    raise EvidenceError('HORIZON_BOOK_EXACT_TARGET_REQUIRED')
                _frame(chosen,MicrostructurePolicy(**quote['book_policy']))
                direction='SELL' if q.direction=='BUY' else 'ACQUIRE'
                depth=executable_depth(p['bids' if q.direction=='BUY' else 'asks'],q.units,direction=direction,
                                       fee_per_share=fee_per_share)
                if not depth.full_depth:raise EvidenceError('INSUFFICIENT_HORIZON_DEPTH')
                if depth.net_value is None:raise EvidenceError('HORIZON_FEES_UNKNOWN')
                mark=depth.net_value/number(q.units)
                delta=mark-number(q.limit_price)-number(q.unit_cost_reserve) if q.direction=='BUY' else \
                      number(q.limit_price)-number(q.unit_cost_reserve)-mark
                result.update(status='MEASURED',reason='EXPLICIT_COST_DEPTH_COUNTERFACTUAL',markout_per_share=str(delta))
            except EvidenceError as exc:result['reason']=str(exc)
        return self.store.audit(key,event_id=event,kind='MEASUREMENT',details=dict(
            version=VERSION,policy_sha256=self.policy_sha,request=request,as_of=now,target_at=target_at,**result,
            measurement_class='HYPOTHETICAL_QUOTE_ENTRY_NOT_FILL_OR_TRADING_PNL',
            fee_authority='EXPLICIT_RESEARCH_ASSUMPTION_NOT_VENUE_ATTESTATION',
            inventory_unchanged=True,actual_trading_pnl=None,financial_authority=False),evidence_ids=tuple(refs))
