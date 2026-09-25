"""Original-scope maker counterfactual quality; no fill or payout inference.

One declared direction/horizon and evidence class per policy. Every matured
retained quote in the window is included, including unknown observations.
"""
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from decimal import Decimal
import time

from .certification import CapabilityScope
from .evidence import EvidenceError, canonical, digest, finite, identity, sha
from .learning_sources import learning_source_view, source_derivation
from .maker_research import VERSION as MAKER_VERSION, _restore
from .measurement import MARKOUT_SECONDS, executable_depth
from .microstructure import MicrostructurePolicy, _frame
from .scenario_risk import number, precise
from .strategy_admission import VERSION as ADMISSION_VERSION
from .valuation import contract_target


VERSION='alpha_v11_scoped_maker_markout_drift_v1'
METHOD='CITY_DAY_EVENT_QUOTE_DEPTH_COUNTERFACTUAL_V1'
SELECTION='ALL_RETAINED_MATURED_MAKER_QUOTES_IN_WINDOW_FOR_SCOPE'


@dataclass(frozen=True)
class MarkoutDriftPolicy:
    version: str
    evidence_class: str
    window_seconds: float
    horizon_seconds: int
    direction: str
    tolerance_seconds: float
    fee_per_share: str | None
    minimum_quotes: int
    minimum_events: int
    minimum_city_days: int
    maximum_adverse_mean_per_share: str
    metric_method: str

    def __post_init__(self):
        identity(self.version)
        if (self.evidence_class not in {'PUBLIC_OBSERVED','SYNTHETIC'} or self.metric_method!=METHOD
                or not 1<=finite(self.window_seconds)<=366*86400
                or type(self.horizon_seconds) is not int or self.horizon_seconds not in MARKOUT_SECONDS
                or self.direction not in {'BUY','SELL'} or not 0<=finite(self.tolerance_seconds)<=60
                or self.fee_per_share is not None and not 0<=number(self.fee_per_share)<=1
                or type(self.minimum_quotes) is not int or not 1<=self.minimum_quotes<=128
                or type(self.minimum_events) is not int or not 1<=self.minimum_events<=64
                or type(self.minimum_city_days) is not int or not 1<=self.minimum_city_days<=self.minimum_events
                or not 0<=number(self.maximum_adverse_mean_per_share)<=3):
            raise EvidenceError('DRIFT_MARKOUT_POLICY_BOUND_OR_METHOD')

    @property
    def sha256(self):return digest(asdict(self))


def _ref(row):return dict(id=row['id'],sha256=row['sha256'],seq=row['seq'])


def _optional(view,key):
    try:return view.get(key)
    except EvidenceError as exc:
        if str(exc)!='EVIDENCE_MISSING':raise
        return None


def _original(view,telemetry,key,q):
    quote=_restore(q['request']);origin=view.get(q['origin_record_id']);d=origin['body'].get('details',{})
    original=d.get('quotes',{}).get(key,{})
    if (origin['kind']!='MEASUREMENT' or origin['event_id']!=telemetry.research.key
            or d.get('version')!=MAKER_VERSION or d.get('policy_sha256')!=telemetry.research.policy_sha
            or d.get('outcome')!='OBSERVING_RESEARCH_QUOTE'
            or d.get('request')!=dict(action='PROPOSE',quote=asdict(quote))
            or any(original.get(k)!=q.get(k) for k in ('request','target','created_at','expires_at',
                'origin_record_id','initial_book_id','initial_microstructure_id','book_policy'))
            or quote.quote_id!=key or q['created_at']!=origin['body']['recorded_at']
            or q['target']!=contract_target(quote.rule,quote.market_id,quote.side)):
        raise EvidenceError('DRIFT_MARKOUT_ORIGINAL_QUOTE_REQUIRED')
    admission=view.get(quote.admission_id);a=admission['body'].get('details',{})
    request=a.get('request',{});assessment=a.get('assessment',{});scope=CapabilityScope(**request['scope'])
    mode='V11_PAPER' if request.get('stage')=='PAPER' else 'V11_SHADOW' if request.get('stage')=='SHADOW' else None
    if (admission['kind']!='REGISTRY' or a.get('version')!=ADMISSION_VERSION
            or admission['event_id']!='admission:'+scope.key or scope.strategy!='MAKER_RESEARCH'
            or request.get('context')!=asdict(quote.context) or request.get('rule')!=asdict(quote.rule)
            or request.get('binding')!=asdict(quote.binding) or mode!=view.namespace
            or quote.context.account_id!=telemetry.research.coordinator.policy.account_id
            or quote.context.event_id not in telemetry.event_ids
            or assessment.get('model_bundle_sha256')!=quote.binding.bundle_sha256
            or admission['seq']>=origin['seq'] or not admission['body']['recorded_at']<=q['created_at']<assessment['valid_until']
            or dict(id=admission['id'],sha256=admission['sha256']) not in origin['body']['evidence']
            or scope.station!=quote.rule.payload['station']
            or scope.source_rule_family!=quote.rule.payload['source_family']
            or scope.family!={'daily_high_temperature':'HIGH','daily_low_temperature':'LOW'}.get(quote.rule.payload['family'])):
        raise EvidenceError('DRIFT_MARKOUT_ADMISSION_BINDING')
    sha(assessment['model_state_sha256'])
    return quote,origin,admission,scope,assessment


def _cohort(view,telemetry,scope,bundle_sha256,policy,as_of,through):
    head=view.latest(kind='MEASUREMENT',event_id=telemetry.research.key,through_seq=through)
    if head is None:return None
    d=head['body']['details'];quotes=d['quotes']
    if (d.get('version')!=MAKER_VERSION or d.get('policy_sha256')!=telemetry.research.policy_sha
            or head['body']['recorded_at']>as_of or len(quotes)>telemetry.research.policy.maximum_retained_quotes):
        raise EvidenceError('DRIFT_MARKOUT_QUOTE_HEAD_OR_BOUND')
    refs=[];events=set();start=as_of-policy.window_seconds
    for key,q in sorted(quotes.items()):
        view.check();target=finite(q['created_at'])+policy.horizon_seconds
        if not start<=target<as_of or target+policy.tolerance_seconds>as_of:continue
        quote,origin,admission,original_scope,assessment=_original(view,telemetry,key,q)
        if origin['seq']>head['seq'] or origin['body']['recorded_at']>head['body']['recorded_at']:
            raise EvidenceError('DRIFT_MARKOUT_ORIGIN_AFTER_SNAPSHOT')
        if (original_scope!=scope or quote.binding.bundle_sha256!=bundle_sha256 or quote.direction!=policy.direction):continue
        events.add(quote.context.event_id)
        mark=_optional(view,telemetry._mark_key(key,q,policy.horizon_seconds))
        if mark is not None and (mark['seq']>through or mark['body']['recorded_at']>as_of):mark=None
        refs.append(dict(quote_id=key,origin_ref=_ref(origin),markout_ref=_ref(mark) if mark else None))
    if len(events)>64 or len(refs)>128:raise EvidenceError('DRIFT_MARKOUT_COHORT_BOUND')
    heads=[('MEASUREMENT',telemetry.research.key,head['seq'])]
    for event in sorted(events):
        row=view.latest(kind='MEASUREMENT',event_id=event,through_seq=through)
        heads.append(('MEASUREMENT',event,row['seq'] if row else 0))
    return dict(quote_head_ref=_ref(head),quotes=refs,history_sha256=digest(refs),
        telemetry_config_sha256=telemetry.config,input_heads=[list(h) for h in heads],source_through_seq=through)


def snapshot_cohort(telemetry, *, scope, bundle_sha256, policy, as_of, deadline=None, monotonic=time.monotonic):
    if not isinstance(policy,MarkoutDriftPolicy) or not isinstance(scope,CapabilityScope) or scope.strategy!='MAKER_RESEARCH':
        raise EvidenceError('DRIFT_MARKOUT_SCOPE_POLICY_REQUIRED')
    sha(bundle_sha256);as_of=finite(as_of)
    if (telemetry.store.namespace not in {'V11_PAPER','V11_SHADOW'} or as_of>telemetry.store.clock()
            or policy.tolerance_seconds!=telemetry.policy.tolerance_seconds
            or policy.fee_per_share!=telemetry.policy.fee_per_share):
        raise EvidenceError('DRIFT_MARKOUT_TELEMETRY_POLICY_OR_CUTOFF')
    with learning_source_view(telemetry.store,deadline=monotonic()+2. if deadline is None else deadline,monotonic=monotonic) as view:
        return _cohort(view,telemetry,scope,bundle_sha256,policy,as_of,view.snapshot_seq)


@precise
def measure_markout_window(telemetry, *, scope, bundle_sha256, policy, cohort, as_of, monotonic=time.monotonic):
    # Reconstruct the complete selection at its original receipt boundary. Later
    # markouts cannot fill holes or change an interrupted measurement's cohort.
    if not isinstance(policy,MarkoutDriftPolicy) or not isinstance(scope,CapabilityScope) or scope.strategy!='MAKER_RESEARCH':
        raise EvidenceError('DRIFT_MARKOUT_SCOPE_POLICY_REQUIRED')
    sha(bundle_sha256);as_of=finite(as_of)
    if (telemetry.store.namespace not in {'V11_PAPER','V11_SHADOW'} or as_of>telemetry.store.clock() or policy.tolerance_seconds!=telemetry.policy.tolerance_seconds
            or policy.fee_per_share!=telemetry.policy.fee_per_share):
        raise EvidenceError('DRIFT_MARKOUT_TELEMETRY_POLICY_OR_CUTOFF')
    rows=[];event_rules={}
    with learning_source_view(telemetry.store,deadline=monotonic()+2.,monotonic=monotonic) as view:
        if _cohort(view,telemetry,scope,bundle_sha256,policy,as_of,cohort['source_through_seq'])!=cohort:
            raise EvidenceError('DRIFT_MARKOUT_PINNED_COHORT_CHANGED')
        quotes=view.get(cohort['quote_head_ref']['id'])['body']['details']['quotes']
        for ref in cohort['quotes']:
            view.check();q=quotes[ref['quote_id']]
            quote,origin,admission,_,assessment=_original(view,telemetry,ref['quote_id'],q)
            event=quote.context.event_id
            if event in event_rules and event_rules[event]!=quote.rule.sha256:raise EvidenceError('DRIFT_EVENT_RULE_CHANGED')
            event_rules[event]=quote.rule.sha256
            initial=view.get(q['initial_book_id']);ib=initial['body']
            if (initial['kind']!='BOOK' or initial['event_id']!=event or initial['seq']>=origin['seq']
                    or ib['available_at']>q['created_at'] or ib['evidence_class']!=policy.evidence_class
                    or dict(id=initial['id'],sha256=initial['sha256']) not in origin['body']['evidence']):
                raise EvidenceError('DRIFT_MARKOUT_INITIAL_BOOK_PROVENANCE')
            sources=[initial]
            for source_ref in assessment['source_refs']:
                source=view.get(source_ref['id']);b=source['body']
                if (source['sha256']!=source_ref['sha256'] or source['seq']>=admission['seq']
                        or b['available_at']>admission['body']['recorded_at'] or b['evidence_class']!=policy.evidence_class):
                    raise EvidenceError('DRIFT_MARKOUT_SOURCE_CLASS_OR_PROVENANCE')
                sources.append(source)
            row=dict(quote_id=ref['quote_id'],event_id=event,city_day=quote.context.city_id+':'+quote.rule.payload['target_date'],
                origin_ref=ref['origin_ref'],admission_ref=_ref(admission),model_state_sha256=assessment['model_state_sha256'],
                markout_ref=ref['markout_ref'],status='UNKNOWN',reason='HORIZON_MEASUREMENT_NOT_PUBLISHED',markout_per_share=None)
            if ref['markout_ref'] is not None:
                mark=view.get(ref['markout_ref']['id']);m=mark['body']['details'];target=q['created_at']+policy.horizon_seconds
                expected=dict(action='MARKOUT',quote_id=ref['quote_id'],horizon_seconds=policy.horizon_seconds,
                    tolerance_seconds=policy.tolerance_seconds,fee_per_share=policy.fee_per_share)
                if (mark['kind']!='MEASUREMENT' or mark['event_id']!=event or m.get('version')!=MAKER_VERSION
                        or m.get('policy_sha256')!=telemetry.research.policy_sha or m.get('request')!=expected
                        or m.get('target_at')!=target or not target+policy.tolerance_seconds<=m['as_of']<=as_of
                        or m['as_of']!=mark['body']['recorded_at'] or origin['seq']>=mark['seq']
                        or m.get('measurement_class')!='HYPOTHETICAL_QUOTE_ENTRY_NOT_FILL_OR_TRADING_PNL'
                        or m.get('financial_authority') is not False or m.get('actual_trading_pnl') is not None
                        or dict(id=origin['id'],sha256=origin['sha256']) not in mark['body']['evidence']
                        or m.get('status') not in {'MEASURED','UNKNOWN'}):
                    raise EvidenceError('DRIFT_MARKOUT_MEASUREMENT_BINDING')
                row.update(status=m['status'],reason=m['reason'],markout_per_share=m['markout_per_share'])
                if m['status']=='MEASURED':
                    book=view.first_received_source(kind='BOOK',event_id=event,provider=ib['provider'],source_identity=ib['source_identity'],
                        after_seq=origin['seq'],through_seq=mark['seq']-1,received_from=target,recorded_until=target+policy.tolerance_seconds)
                    if book is None or book['id']!=m['book_id']:raise EvidenceError('DRIFT_MARKOUT_FIRST_RECEIPT_REQUIRED')
                    b=book['body'];p=b['payload']
                    if (b['evidence_class']!=policy.evidence_class or m['source_class']!=policy.evidence_class
                            or b['observed_at'] is None or not target<=b['observed_at']<=b['received_at']<=target+policy.tolerance_seconds
                            or b['available_at']>target+policy.tolerance_seconds
                            or any(p.get(k)!=v for k,v in q['target'].items()) or p.get('rule_fingerprint')!=quote.rule.sha256
                            or p.get('collateral_asset')!=telemetry.research.coordinator.policy.collateral_asset
                            or dict(id=book['id'],sha256=book['sha256']) not in mark['body']['evidence']):
                        raise EvidenceError('DRIFT_MARKOUT_HORIZON_BOOK_BINDING')
                    _frame(book,MicrostructurePolicy(**q['book_policy']))
                    depth=executable_depth(p['bids' if quote.direction=='BUY' else 'asks'],quote.units,
                        direction='SELL' if quote.direction=='BUY' else 'ACQUIRE',fee_per_share=policy.fee_per_share)
                    if not depth.full_depth or depth.net_value is None:raise EvidenceError('DRIFT_MARKOUT_EXPLICIT_DEPTH_COST_REQUIRED')
                    price=depth.net_value/number(quote.units)
                    value=price-number(quote.limit_price)-number(quote.unit_cost_reserve) if quote.direction=='BUY' else \
                        number(quote.limit_price)-number(quote.unit_cost_reserve)-price
                    if number(m['markout_per_share'],signed=True)!=value:raise EvidenceError('DRIFT_MARKOUT_VALUE_REPRODUCTION')
                    row['book_ref']=_ref(book)
                    sources.append(book)
                elif m['markout_per_share'] is not None:raise EvidenceError('DRIFT_MARKOUT_UNKNOWN_VALUE')
            derivation=source_derivation(view,sources,event_id=event,cutoff=as_of)
            row['source_derivation_sha256']=derivation['sha256'] if derivation else None
            rows.append(row)
        view.check()
    groups=defaultdict(lambda:defaultdict(list));unknown=Counter();signs=Counter()
    for row in rows:
        if row['status']!='MEASURED':unknown[row['reason']]+=1;continue
        value=number(row['markout_per_share'],signed=True);groups[row['city_day']][row['event_id']].append(value)
        signs['negative' if value<0 else 'positive' if value>0 else 'zero']+=1
    average=lambda xs:sum(xs,Decimal(0))/len(xs)
    mean=average([average([average(v) for v in events.values()]) for events in groups.values()]) if groups else None
    n=sum(signs.values());events=len({r['event_id'] for r in rows});city_days=len({r['city_day'] for r in rows})
    sufficient=n>=policy.minimum_quotes and events>=policy.minimum_events and city_days>=policy.minimum_city_days
    breaches=['ADVERSE_COUNTERFACTUAL_MEAN_ABOVE_DECLARED_MAXIMUM'] if mean is not None and -mean>number(policy.maximum_adverse_mean_per_share) else []
    outcome='INCOMPLETE_HORIZON_COHORT' if unknown else 'INSUFFICIENT_COHORT' if not sufficient else 'DEGRADATION_CANDIDATE' if breaches else 'NO_DECLARED_BREACH'
    request=dict(scope=asdict(scope),bundle_sha256=bundle_sha256,policy=asdict(policy),as_of=as_of,
        account_id=telemetry.research.coordinator.policy.account_id,namespace=telemetry.store.namespace,cohort=cohort)
    result=dict(version=VERSION,request=request,request_sha256=digest(request),selection=SELECTION,
        window=dict(start_inclusive=as_of-policy.window_seconds,end_exclusive=as_of,axis='HORIZON_TARGET_TIME_CLOSED_TOLERANCE_ONLY'),
        rows=rows,input_heads=cohort['input_heads'],cohort_sufficient=sufficient and not unknown,outcome=outcome,threshold_breaches=breaches,
        scores=dict(n_quotes=len(rows),n_measured=n,n_events=events,n_city_days=city_days,unknown_reasons=dict(unknown),
            mean_counterfactual_per_share=str(mean) if mean is not None else None,sign_counts=dict(signs),
            negative_quote_fraction=str(Decimal(signs['negative'])/n) if n else None,confidence_interval=None,
            weighting=METHOD,independence_validated=False,observed_subset_only=bool(unknown)),
        evidence_class=policy.evidence_class,measurement_class='HYPOTHETICAL_QUOTE_ENTRY_NOT_FILL_OR_TRADING_PNL',
        retained_quote_window_coverage_verified=True,global_universe_coverage_verified=False,
        fee_authority='EXPLICIT_RESEARCH_ASSUMPTION_NOT_VENUE_ATTESTATION',actual_trading_pnl=None,net_ev_capture=None,
        predeclared_policy_review_verified=False,calibration_acceptance=False,demotion_applied=False,financial_authority=False)
    if len(canonical(result).encode())>512*1024:raise EvidenceError('DRIFT_RESULT_BYTES_BOUND')
    return result
