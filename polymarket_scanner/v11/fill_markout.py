"""Reconciled synthetic fills to causal depth marks, at original decision scope.

Every retained fill is reconciled before selection. Unknown execution times use
their full possible interval, so malformed/legacy details cannot select away a
loss. Horizons, directions and book evidence classes never share a score.
"""
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from decimal import Decimal
import time

from .certification import CapabilityScope
from .evidence import EvidenceError, canonical, digest, finite, identity, sha
from .fill_evidence import execution_details
from .learning_sources import learning_source_view, source_derivation
from .measurement import MARKOUT_SECONDS, executable_depth
from .microstructure import MicrostructurePolicy, _frame
from .paper_coordinator import ACCOUNT_KEY, VERSION as ACCOUNT_VERSION
from .position_attribution import intent_lineage
from .rules import RuleFingerprint
from .scenario_risk import number, precise
from .strategy_admission import VERSION as ADMISSION_VERSION


VERSION='alpha_v11_scoped_paper_fill_markout_v1'
METHOD='CITY_DAY_EVENT_INTENT_QUANTITY_WEIGHTED_FILL_V1'
SELECTION='ALL_RECONCILED_PAPER_FILLS_POSSIBLY_IN_MATURED_HORIZON_WINDOW'


@dataclass(frozen=True)
class FillMarkoutPolicy:
    version: str
    evidence_class: str
    window_seconds: float
    horizon_seconds: int
    direction: str
    tolerance_seconds: float
    fee_per_share: str | None
    minimum_intents: int
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
                or type(self.minimum_intents) is not int or not 1<=self.minimum_intents<=128
                or type(self.minimum_events) is not int or not 1<=self.minimum_events<=64
                or type(self.minimum_city_days) is not int or not 1<=self.minimum_city_days<=self.minimum_events
                or not 0<=number(self.maximum_adverse_mean_per_share)<=3):
            raise EvidenceError('FILL_MARKOUT_POLICY_BOUND_OR_METHOD')

    @property
    def sha256(self):return digest(asdict(self))


def _ref(row):return dict(id=row['id'],sha256=row['sha256'],seq=row['seq'])


def _original(view,c,state,intent,value):
    if not 1<=len(intent['admission_ids'])<=4:raise EvidenceError('FILL_MARKOUT_ADMISSIONS_BOUND')
    context=state['contexts'][intent['event_id']];rule=RuleFingerprint(**state['rules'][intent['event_id']]);result={}
    for key in intent['admission_ids']:
        row=view.get(key);d=row['body']['details'];r=d['request'];a=d['assessment'];s=CapabilityScope(**r['scope'])
        if (row['kind']!='REGISTRY' or d.get('version')!=ADMISSION_VERSION or row['event_id']!='admission:'+s.key
                or not row['seq']<value['seq'] or not row['body']['recorded_at']<=value['body']['recorded_at']<a['valid_until']
                or r['context']!=context or r['rule']!=asdict(rule) or r['binding']!=intent['binding']
                or context['account_id']!=c.policy.account_id or r['stage']!='PAPER'
                or context['station_id']!=s.station or rule.payload['station']!=s.station
                or rule.payload['source_family']!=s.source_rule_family
                or {'daily_high_temperature':'HIGH','daily_low_temperature':'LOW'}.get(rule.payload['family'])!=s.family
                or a['model_bundle_sha256']!=intent['binding']['bundle_sha256'] or s.strategy in result):
            raise EvidenceError('FILL_MARKOUT_ORIGINAL_ADMISSION_SCOPE')
        sha(a['model_state_sha256']);result[s.strategy]=(row,s,a)
    weights=intent['attribution']
    if (len({w['strategy'] for w in weights})!=len(weights) or set(result)!={w['strategy'] for w in weights}
            or sum((number(w['weight']) for w in weights),Decimal(0))!=1):
        raise EvidenceError('FILL_MARKOUT_ORIGINAL_ATTRIBUTION')
    return result,rule,context


def _details(view,c,proof,intent):
    try:return execution_details(view,proof,intent,account_id=c.policy.account_id,collateral_asset=c.policy.collateral_asset)
    except (EvidenceError,KeyError,TypeError,ValueError) as exc:
        return dict(status='UNKNOWN',reason=str(exc) if isinstance(exc,EvidenceError) else 'FILL_EXECUTION_DETAILS_MALFORMED')


def _horizon(view,proof,details,policy,through):
    if details['status']!='VALIDATED_SYNTHETIC_DETAILS':return None
    post=view.get(details['post_validation_book_ref']['id']);b=post['body'];target=details['executed_at']+policy.horizon_seconds
    return view.first_received_source(kind='BOOK',event_id=proof['event_id'],provider=b['provider'],source_identity=b['source_identity'],
        after_seq=post['seq'],through_seq=through,received_from=target,recorded_until=target+policy.tolerance_seconds)


@precise
def _cohort(view,c,scope,bundle_sha256,policy,as_of,through):
    account=view.latest(kind='COORDINATOR_EVENT',event_id=ACCOUNT_KEY,through_seq=through)
    if account is None:return None
    d=account['body']['details'];state=d['state']
    if (d.get('version')!=ACCOUNT_VERSION or d.get('policy_sha256')!=c.policy_sha
            or state.get('execution_namespace')!=view.namespace or state.get('account_id')!=c.policy.account_id
            or state.get('financial_authority') is not False or account['body']['recorded_at']>as_of
            or state['faults'] or len(state['fills'])>2048 or len(state['intents'])>512):
        raise EvidenceError('FILL_MARKOUT_ACCOUNT_IDENTITY_RECONCILIATION_OR_BOUND')
    refs=[];totals=defaultdict(Decimal);events=set();cache={};start=as_of-policy.window_seconds
    for key,hash_ in sorted(state['fills'].items()):
        view.check();proof=view.by_hash(kind='TRADE',sha256=hash_);b=proof['body'];p=b['payload']
        intent=state['intents'][p['intent_id']];value=view.get(intent['valuation_id']);vd=value['body']['details']
        if (proof['seq']>=account['seq'] or b.get('evidence_class')!='SYNTHETIC' or p.get('record_type')!='PAPER_FILL'
                or p.get('execution_namespace')!=view.namespace or p.get('account_id')!=c.policy.account_id
                or p.get('fill_id')!=key or p.get('intent_id')!=intent['proposal_id'] or p.get('token_id')!=intent['token_id']
                or proof['event_id']!=intent['event_id'] or p.get('direction')!=intent['direction'] or number(p['units'])<=0
                or value['kind']!='MEASUREMENT' or value['event_id']!=intent['event_id'] or value['seq']>=proof['seq']
                or vd.get('binding')!=intent['binding'] or not value['body']['recorded_at']<=b['received_at']<=b['available_at']<=account['body']['recorded_at']):
            raise EvidenceError('FILL_MARKOUT_RECONCILED_PROOF_REQUIRED')
        number(p['all_in_collateral']);totals[intent['proposal_id']]+=number(p['units'])
        if intent['proposal_id'] not in cache:cache[intent['proposal_id']]=_original(view,c,state,intent,value)
        original,_,_=cache[intent['proposal_id']]
        if (intent['direction']!=policy.direction or intent['binding']['bundle_sha256']!=bundle_sha256
                or not any(s==scope for _,s,_ in original.values())):continue
        details=_details(view,c,proof,intent);target=None
        if details['status']=='VALIDATED_SYNTHETIC_DETAILS':
            target=details['executed_at']+policy.horizon_seconds
            if not start<=target<as_of or target+policy.tolerance_seconds>as_of:continue
        else:
            # Execution cannot precede valuation or follow proof receipt. Retain
            # every unknown whose possible horizon overlaps the matured window.
            low=value['body']['recorded_at']+policy.horizon_seconds;high=b['received_at']+policy.horizon_seconds
            if high<start or low>=as_of or low+policy.tolerance_seconds>as_of:continue
        mark=_horizon(view,proof,details,policy,through);events.add(intent['event_id'])
        refs.append(dict(fill_id=key,proof_ref=_ref(proof),intent_id=intent['proposal_id'],target_at=target,
            details_status=details['status'],details_reason=details['reason'],book_ref=_ref(mark) if mark else None))
    if any(totals[key]!=number(intent['filled_units']) for key,intent in state['intents'].items()):
        raise EvidenceError('FILL_MARKOUT_RECONCILED_QUANTITY_MISMATCH')
    if len(events)>64 or len(refs)>512:raise EvidenceError('FILL_MARKOUT_COHORT_BOUND')
    heads=[['COORDINATOR_EVENT',ACCOUNT_KEY,account['seq']]]
    for event in sorted(events):
        head=view.latest(kind='BOOK',event_id=event,through_seq=through);heads.append(['BOOK',event,head['seq'] if head else 0])
    return dict(account_ref=_ref(account),fills=refs,history_sha256=digest(refs),input_heads=heads,source_through_seq=through)


def _request(c,scope,bundle_sha256,policy,as_of):
    if not isinstance(policy,FillMarkoutPolicy) or not isinstance(scope,CapabilityScope) or c.store.namespace!='V11_PAPER':
        raise EvidenceError('FILL_MARKOUT_PAPER_SCOPE_POLICY_REQUIRED')
    sha(bundle_sha256);as_of=finite(as_of)
    if as_of>c.store.clock():raise EvidenceError('FILL_MARKOUT_FUTURE_CUTOFF')
    return as_of


def snapshot_fill_cohort(c,*,scope,bundle_sha256,policy,as_of,deadline=None,monotonic=time.monotonic):
    as_of=_request(c,scope,bundle_sha256,policy,as_of)
    with learning_source_view(c.store,deadline=monotonic()+2. if deadline is None else deadline,monotonic=monotonic) as view:
        return _cohort(view,c,scope,bundle_sha256,policy,as_of,view.snapshot_seq)


@precise
def measure_fill_window(c,*,scope,bundle_sha256,policy,cohort,as_of,monotonic=time.monotonic):
    from .performance import PerformanceLab
    as_of=_request(c,scope,bundle_sha256,policy,as_of);rows=[];start=as_of-policy.window_seconds
    with learning_source_view(c.store,deadline=monotonic()+2.,monotonic=monotonic) as view:
        if cohort is None or _cohort(view,c,scope,bundle_sha256,policy,as_of,cohort['source_through_seq'])!=cohort:
            raise EvidenceError('FILL_MARKOUT_PINNED_COHORT_CHANGED')
        account=view.get(cohort['account_ref']['id']);state=account['body']['details']['state']
        report=PerformanceLab(c).build(start=start,end=as_of,account_row=account)
        if report['faults'] or not report['reconciliation']['complete']:raise EvidenceError('FILL_MARKOUT_ACCOUNT_RECONCILIATION_REQUIRED')
        for ref in cohort['fills']:
            view.check();proof=view.get(ref['proof_ref']['id']);p=proof['body']['payload'];intent=state['intents'][ref['intent_id']]
            value=view.get(intent['valuation_id']);original,rule,context=_original(view,c,state,intent,value)
            admission,_,assessment=original[scope.strategy];details=_details(view,c,proof,intent)
            row=dict(ref,event_id=intent['event_id'],city_day=context['city_id']+':'+rule.payload['target_date'],
                admission_ref=_ref(admission),model_state_sha256=assessment['model_state_sha256'],valuation_ref=_ref(value),
                decision=intent_lineage(view,intent),attribution_basis='ORIGINAL_FILL_DECISION_NOT_ADDITIONAL_REALIZED_PNL',
                units=p['units'],all_in_collateral=p['all_in_collateral'],execution_evidence=details,
                status='UNKNOWN',reason=details['reason'],markout_per_share=None,price_markout_per_share=None)
            if details['status']=='VALIDATED_SYNTHETIC_DETAILS':
                sources=[view.get(details[k]['id']) for k in ('signal_book_ref','post_validation_book_ref')]
                try:
                    for s in assessment['source_refs']:
                        source=view.get(s['id']);b=source['body']
                        if (source['sha256']!=s['sha256'] or source['seq']>=admission['seq'] or b['available_at']>admission['body']['recorded_at']):
                            raise EvidenceError('FILL_MARKOUT_ORIGINAL_SOURCE_PROVENANCE')
                        sources.append(source)
                    if any(s['body']['evidence_class']!=policy.evidence_class for s in sources):
                        raise EvidenceError('FILL_MARKOUT_SOURCE_CLASS_MISMATCH')
                    if ref['book_ref'] is None:raise EvidenceError('FILL_MARKOUT_HORIZON_BOOK_MISSING')
                    book=view.get(ref['book_ref']['id']);b=book['body'];bp=b['payload'];target=ref['target_at']
                    if (b['evidence_class']!=policy.evidence_class or b['observed_at'] is None
                            or not target<=b['observed_at']<=b['received_at']<=b['available_at']<=b['recorded_at']<=target+policy.tolerance_seconds
                            or any(bp.get(k)!=v for k,v in intent['target'].items()) or bp.get('rule_fingerprint')!=rule.sha256
                            or bp.get('collateral_asset')!=c.policy.collateral_asset):
                        raise EvidenceError('FILL_MARKOUT_HORIZON_BOOK_BINDING')
                    frame=MicrostructurePolicy('FILL_DEPTH_ONLY',c.policy.collateral_asset,120.,600.,60.,.001,20)
                    for source in sources[:2]+[book]:_frame(source,frame)
                    depth=executable_depth(bp['bids' if policy.direction=='BUY' else 'asks'],p['units'],
                        direction='SELL' if policy.direction=='BUY' else 'ACQUIRE',fee_per_share=policy.fee_per_share)
                    if not depth.full_depth or depth.net_value is None:raise EvidenceError('FILL_MARKOUT_EXPLICIT_DEPTH_AND_COST_REQUIRED')
                    qty=number(p['units']);basis=number(p['all_in_collateral'])/qty;sign=1 if policy.direction=='BUY' else -1
                    mark=(depth.net_value/qty-basis)*sign
                    row.update(status='MEASURED',reason='CAUSAL_DEPTH_AFTER_EXPLICIT_PAPER_FILL',markout_per_share=str(mark),
                        price_markout_per_share=str((depth.gross_value/qty-number(details['price_per_share']))*sign))
                    sources.append(book)
                    derivation=source_derivation(view,sources,event_id=intent['event_id'],cutoff=as_of)
                    row['source_derivation_sha256']=derivation['sha256'] if derivation else None
                except (EvidenceError,KeyError,TypeError,ValueError) as exc:
                    row.update(status='UNKNOWN',reason=str(exc) if isinstance(exc,EvidenceError) else 'FILL_MARKOUT_MALFORMED_SOURCE',
                        markout_per_share=None,price_markout_per_share=None)
            rows.append(row)
        view.check()
    groups=defaultdict(lambda:defaultdict(lambda:defaultdict(list)));unknown=Counter()
    for row in rows:
        if row['status']!='MEASURED':unknown[row['reason']]+=1;continue
        # This value was computed above from validated bounded inputs. A
        # repeating per-share quotient is not an external ledger amount and
        # must not be rejected by the ledger's 48-place input restriction.
        groups[row['city_day']][row['event_id']][row['intent_id']].append((Decimal(row['markout_per_share']),number(row['units'])))
    average=lambda xs:sum(xs,Decimal(0))/len(xs)
    intent_mean=lambda xs:sum((v*q for v,q in xs),Decimal(0))/sum((q for _,q in xs),Decimal(0))
    mean=average([average([average([intent_mean(xs) for xs in intents.values()]) for intents in events.values()]) for events in groups.values()]) if groups else None
    n=sum(len(intents) for events in groups.values() for intents in events.values());ne=sum(len(events) for events in groups.values());nc=len(groups)
    signs=Counter('negative' if intent_mean(xs)<0 else 'positive' if intent_mean(xs)>0 else 'zero'
        for events in groups.values() for intents in events.values() for xs in intents.values())
    sufficient=n>=policy.minimum_intents and ne>=policy.minimum_events and nc>=policy.minimum_city_days and not unknown
    breaches=['ADVERSE_PAPER_FILL_MARKOUT_ABOVE_DECLARED_MAXIMUM'] if mean is not None and -mean>number(policy.maximum_adverse_mean_per_share) else []
    outcome='INCOMPLETE_HORIZON_COHORT' if unknown else 'INSUFFICIENT_COHORT' if not sufficient else 'DEGRADATION_CANDIDATE' if breaches else 'NO_DECLARED_BREACH'
    request=dict(scope=asdict(scope),bundle_sha256=bundle_sha256,policy=asdict(policy),cohort=cohort,as_of=as_of,
        account_id=c.policy.account_id,namespace=c.store.namespace)
    result=dict(version=VERSION,request=request,request_sha256=digest(request),selection=SELECTION,
        window=dict(start_inclusive=start,end_exclusive=as_of,axis='HORIZON_TARGET_TIME_WITH_CONSERVATIVE_UNKNOWN_INTERVALS'),
        rows=rows,input_heads=cohort['input_heads'],cohort_sufficient=sufficient,outcome=outcome,threshold_breaches=breaches,
        scores=dict(n_fills=len(rows),n_measured=sum(r['status']=='MEASURED' for r in rows),n_intents=n,n_events=ne,n_city_days=nc,
            mean_fill_markout_per_share=str(mean) if mean is not None else None,unknown_reasons=dict(unknown),
            intent_sign_counts=dict(signs),negative_intent_fraction=str(Decimal(signs['negative'])/n) if n else None,
            adverse_selection_rate_empirical=None,
            weighting=METHOD,independence_validated=False,observed_subset_only=bool(unknown),confidence_interval=None),
        evidence_class=policy.evidence_class,execution_class='SYNTHETIC_PAPER_FILL',
        measurement_class='PAPER_FILL_TO_HYPOTHETICAL_DEPTH_NOT_REALIZED_PNL',
        reconciled_fill_selection_verified=True,execution_timing_coverage_verified=all(r['target_at'] is not None for r in rows),
        global_universe_coverage_verified=False,venue_execution_attested=False,
        horizon_fee_authority='EXPLICIT_RESEARCH_ASSUMPTION_NOT_VENUE_ATTESTATION',
        actual_trading_pnl=None,net_ev_capture=None,predeclared_policy_review_verified=False,
        calibration_acceptance=False,demotion_applied=False,financial_authority=False)
    if len(canonical(result).encode())>512*1024:raise EvidenceError('DRIFT_RESULT_BYTES_BOUND')
    return result
