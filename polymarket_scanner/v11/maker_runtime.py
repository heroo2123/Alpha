"""Current-input maker research proposals and protected payout context.

This adapter produces event-linked research evidence only. Quotes never enter
the common account as economic proposals, reservations or fills.
"""
from base64 import urlsafe_b64encode
from copy import deepcopy
from dataclasses import asdict, dataclass

from .evidence import EvidenceError, digest, identity
from .maker_research import MakerResearch, ResearchQuote
from .microstructure import MakerMicrostructure, MicrostructurePolicy
from .paper_runtime import Evaluation, request_adapter_config, check_request_adapter
from .request_assembly import RequestAssembler, ScopeInputs, TargetPlan, _single
from .runtime_health import admission_heads
from .scenario_risk import number


VERSION='alpha_v11_maker_runtime_v1'


def _id(prefix,value):
    return prefix+urlsafe_b64encode(bytes.fromhex(digest(value))).decode().rstrip('=')


@dataclass(frozen=True)
class MakerTarget:
    market_id: str
    side: str
    direction: str
    limit_price: str
    units: str
    unit_cost_reserve: str

    def __post_init__(self):
        identity(self.market_id)
        if (self.side not in {'YES','NO'} or self.direction not in {'BUY','SELL'}
                or not 0<number(self.limit_price)<1 or not 0<number(self.units)<=1_000_000
                or not 0<=number(self.unit_cost_reserve)<=1):
            raise EvidenceError('MAKER_RUNTIME_TARGET_BOUND')


@dataclass(frozen=True)
class MakerRequest:
    quote: ResearchQuote
    model_input_ids: tuple[str, ...]
    payout_admission_id: str | None
    observed_input_id: str | None
    coverage_input_id: str | None

    def __post_init__(self):
        if (not isinstance(self.quote,ResearchQuote) or type(self.model_input_ids) is not tuple
                or not 1<=len(self.model_input_ids)<=16 or len(set(self.model_input_ids))!=len(self.model_input_ids)):
            raise EvidenceError('MAKER_RUNTIME_REQUEST_INPUT_BOUND')
        for key in (*self.model_input_ids,self.payout_admission_id,self.observed_input_id,self.coverage_input_id):
            if key is not None:identity(key)


class MakerRequestFactory:
    def __init__(self,assembler,research,targets,*,microstructure,payout_inputs=None):
        if (not isinstance(assembler,RequestAssembler) or not isinstance(research,MakerResearch)
                or research.store is not assembler.store or research.coordinator.policy.account_id!=assembler.inputs.context.account_id
                or assembler.inputs.scope.strategy!='MAKER_RESEARCH'
                or not isinstance(microstructure,MicrostructurePolicy)
                or microstructure.collateral_asset!=research.coordinator.policy.collateral_asset
                or type(targets) is not tuple or not 1<=len(targets)<=6
                or any(not isinstance(t,MakerTarget) for t in targets)
                or len({(t.market_id,t.side) for t in targets})!=len(targets)):
            raise EvidenceError('MAKER_RUNTIME_PLAN_BOUND')
        if payout_inputs is not None and (not isinstance(payout_inputs,ScopeInputs)
                or payout_inputs.scope.strategy not in {'FUTURE_FORECAST','SAME_DAY_LATE_LOCK'}
                or (payout_inputs.context,payout_inputs.rule,payout_inputs.binding,payout_inputs.stage)!=
                   (assembler.inputs.context,assembler.inputs.rule,assembler.inputs.binding,assembler.inputs.stage)):
            raise EvidenceError('MAKER_RUNTIME_PAYOUT_SCOPE')
        self.assembler,self.research,self.targets,self.microstructure=assembler,research,targets,microstructure
        self.payout_inputs=deepcopy(payout_inputs)

    @property
    def config(self):
        return digest(dict(assembler=self.assembler.config,research=self.research.policy_sha,targets=[asdict(t) for t in self.targets],
            microstructure=asdict(self.microstructure),payout_inputs=asdict(self.payout_inputs) if self.payout_inputs else None))

    def __call__(self,claim):
        a=self.assembler
        pin,leases,books,event,expiry=a.current(claim,tuple(TargetPlan(t.market_id,t.side,t.units,t.units,()) for t in self.targets))
        payout=None
        if self.payout_inputs is not None:
            payout,leases=a.pin(claim,self.payout_inputs)
            if not {s.evidence_id for s in leases if s.role!='FEATURES'}<=set(event['body']['details']['request']['source_ids']):
                raise EvidenceError('MAKER_RUNTIME_PAYOUT_RISK_INPUTS_CHANGED')
            expiry=min(expiry,payout['body']['details']['assessment']['valid_until'])
        strategies=('MAKER_RESEARCH',)+( (self.payout_inputs.scope.strategy,) if self.payout_inputs else () )
        admission_heads(a.store,account_id=a.inputs.context.account_id,event_id=a.inputs.context.event_id,strategies=strategies)
        models=tuple(s.evidence_id for s in leases if s.role=='MODEL')
        if not models:raise EvidenceError('MAKER_RUNTIME_PAYOUT_MODEL_REQUIRED')
        conditioned=any(s.role=='FEATURES' for s in leases)
        observed,coverage=(_single(leases,'OFFICIAL'),_single(leases,'FEATURES')) if conditioned else (None,None)
        result=[]
        for index,(target,book) in enumerate(zip(self.targets,books)):
            previous=a.store.previous_source(book['id']);history=(book['id'],)
            if (previous is not None and previous['body']['observed_at'] is not None
                    and a.store.clock()-previous['body']['observed_at']<=self.microstructure.history_window_seconds):
                history=(previous['id'],book['id'])
            key=_id('maker-input:',[self.config,claim['claim_id'],index])
            micro=MakerMicrostructure(a.store).evaluate(key,rule=a.inputs.rule,market_id=target.market_id,side=target.side,
                book_ids=history,trade_ids=(),policy=self.microstructure)
            quote=ResearchQuote(_id('mq:',[self.config,claim['claim_id'],index]),
                _id('mt:',[self.config,asdict(target),[(s.evidence_id,s.role) for s in leases]]),
                a.inputs.context,a.inputs.rule,a.inputs.binding,pin['id'],event['id'],micro['id'],
                target.market_id,target.side,target.direction,target.limit_price,target.units,target.unit_cost_reserve,expiry)
            result.append(MakerRequest(quote,models,payout['id'] if payout else None,observed,coverage))
        return tuple(result)


class MakerEventAdapter:
    def __init__(self,research,requests_for_event):
        if not isinstance(research,MakerResearch):raise EvidenceError('MAKER_RUNTIME_RESEARCH_REQUIRED')
        if getattr(requests_for_event,'research',research) is not research:raise EvidenceError('MAKER_RUNTIME_RESEARCH_SCOPE')
        self.research,self.coordinator,self.store=research,research.coordinator,research.store
        self.requests_for_event=requests_for_event
        self.config=request_adapter_config(type(self).__name__,requests_for_event)

    def evaluate(self,claim,prefix):
        check_request_adapter(self);requests=self.requests_for_event(claim)
        if (type(requests) is not tuple or not 1<=len(requests)<=6
                or any(not isinstance(r,MakerRequest) for r in requests)):
            raise EvidenceError('MAKER_RUNTIME_REQUEST_BOUND')
        outputs=[]
        for index,request in enumerate(requests):
            key=_id('maker-run:',[prefix,index]);request_sha=digest(asdict(request))
            try:saved=self.store.get(key)
            except EvidenceError as exc:
                if str(exc)!='EVIDENCE_MISSING':raise
            else:
                if saved['event_id']!=claim['event_id'] or saved['body']['details'].get('request_sha256')!=request_sha:
                    raise EvidenceError('MAKER_RUNTIME_REPLAY_CONFLICT')
                outputs.append(saved['id']);continue
            refs=[];context=None;quote=None;outcome='GATED'
            try:
                if request.quote.context.event_id!=claim['event_id']:raise EvidenceError('MAKER_RUNTIME_EVENT_MISMATCH')
                quote=self.research.propose(key+':quote',request.quote);refs.append(quote['id'])
                outcome,reason=(quote['body']['details'][f] for f in ('outcome','reason'))
                if outcome=='OBSERVING_RESEARCH_QUOTE':
                    context=self.research.context(key+':context',quote_id=request.quote.quote_id,
                        model_input_ids=request.model_input_ids,payout_admission_id=request.payout_admission_id,
                        observed_input_id=request.observed_input_id,coverage_input_id=request.coverage_input_id)
                    refs.append(context['id'])
            except EvidenceError as exc:outcome,reason='GATED',str(exc)
            row=self.store.audit(key,event_id=claim['event_id'],kind='RUNTIME_STATUS',details=dict(
                version=VERSION,request_sha256=request_sha,quote_id=request.quote.quote_id,outcome=outcome,reason=reason,
                quote_record_id=quote['id'] if quote else None,context_record_id=context['id'] if context else None,
                context_outcome=context['body']['details']['outcome'] if context else 'NOT_EVALUATED',
                financial_authority=False,account_proposal=False,orders_sent=False,account_ledger_mutated=False),evidence_ids=tuple(refs))
            outputs.append(row['id'])
        return Evaluation(tuple(outputs))
