"""Bounded causal book/print features, never a maker fill or execution model."""
from dataclasses import asdict, dataclass
from decimal import Decimal, localcontext
import math

from .evidence import EvidenceError, digest, finite, identity
from .measurement import executable_depth
from .scenario_risk import number, precise
from .valuation import contract_target


VERSION = 'alpha_v11_maker_microstructure_v1'
STREAM_VERSION = 'alpha_v11_declared_book_sequence_v1'
TRADE_VERSION = 'alpha_v11_public_trade_print_v1'


@dataclass(frozen=True)
class MicrostructurePolicy:
    version: str
    collateral_asset: str
    maximum_book_age_seconds: float
    history_window_seconds: float
    maximum_gap_seconds: float
    minimum_sample_seconds: float
    depth_levels: int

    def __post_init__(self):
        identity(self.version);identity(self.collateral_asset)
        if (not 0 < finite(self.maximum_book_age_seconds) <= 120
                or not 0 < finite(self.history_window_seconds) <= 600
                or not 0 < finite(self.maximum_gap_seconds) <= self.history_window_seconds
                or not .001 <= finite(self.minimum_sample_seconds) <= self.maximum_gap_seconds
                or type(self.depth_levels) is not int or not 1 <= self.depth_levels <= 20):
            raise EvidenceError('MICROSTRUCTURE_POLICY_BOUND')


def _source(store, key, *, rule, target, kind, cutoff, policy):
    row=store.get(key);b=row['body'];p=b.get('payload',{})
    if (row['event_id']!=rule.payload['event_id'] or row['kind']!=kind
            or any(p.get(k)!=v for k,v in target.items()) or p.get('rule_fingerprint')!=rule.sha256
            or p.get('collateral_asset')!=policy.collateral_asset):
        raise EvidenceError('MICROSTRUCTURE_EXACT_SOURCE_TARGET_REQUIRED')
    if (b.get('evidence_class')=='HISTORICAL_AVAILABILITY_UNKNOWN' or b.get('observed_at') is None
            or not cutoff-policy.history_window_seconds <= b['observed_at'] <= b['received_at'] <= cutoff
            or b['available_at'] > cutoff):
        raise EvidenceError('MICROSTRUCTURE_NONCAUSAL_OR_OUTSIDE_WINDOW')
    return row


def _frame(row, policy):
    p=row['body']['payload']
    if p.get('stream_healthy') is not True:
        raise EvidenceError('MICROSTRUCTURE_BOOK_STREAM_UNHEALTHY')
    bids,asks=p.get('bids'),p.get('asks')
    if not bids or not asks:raise EvidenceError('MICROSTRUCTURE_BOOK_SIDE_MISSING')
    # Reuse strict probability/size/duplicate-price parsing. A feature does not
    # require one share of liquidity and does not turn visible depth into a fill.
    executable_depth(bids,'1',direction='SELL',fee_per_share=None)
    executable_depth(asks,'1',direction='ACQUIRE',fee_per_share=None)
    bid=sorted(((number(x['price']),number(x['size'])) for x in bids),reverse=True)
    ask=sorted((number(x['price']),number(x['size'])) for x in asks)
    bp,bq=bid[0];ap,aq=ask[0]
    if bp>=ap:raise EvidenceError('MICROSTRUCTURE_CROSSED_OR_LOCKED_BOOK')
    bdepth=sum(q for _,q in bid[:policy.depth_levels]);adepth=sum(q for _,q in ask[:policy.depth_levels])
    with localcontext() as c:
        c.prec=80
        values=dict(midpoint=(bp+ap)/2,microprice=(ap*bq+bp*aq)/(bq+aq),spread=ap-bp,
                    best_bid=bp,best_ask=ap,bid_l1_units=bq,ask_l1_units=aq,
                    bid_depth_units=bdepth,ask_depth_units=adepth,
                    l1_imbalance=(bq-aq)/(bq+aq),depth_imbalance=(bdepth-adepth)/(bdepth+adepth))
    return {k:str(v) for k,v in values.items()}


def _link(previous, current, policy):
    a,b=previous['body'],current['body']
    left,right=a['payload'].get('book_sequence'),b['payload'].get('book_sequence')
    if not isinstance(left,dict) or not isinstance(right,dict):return 'SEQUENCE_UNAVAILABLE'
    for seq in (left,right):
        if (set(seq)!={'version','epoch','sequence','previous_sequence'} or seq['version']!=STREAM_VERSION
                or type(seq['epoch']) is not str or not 1 <= len(seq['epoch']) <= 80
                or type(seq['sequence']) is not int or not 0 <= seq['sequence'] <= 2**53
                or seq['previous_sequence'] is not None and (type(seq['previous_sequence']) is not int or
                                                          not 0 <= seq['previous_sequence'] < seq['sequence'])):
            return 'SEQUENCE_SCHEMA_UNKNOWN'
    if left['epoch']!=right['epoch']:return 'STREAM_EPOCH_CHANGED'
    if right['previous_sequence']!=left['sequence'] or right['sequence']<=left['sequence']:
        return 'BOOK_UPDATE_GAP_OR_REORDER'
    observed=b['observed_at']-a['observed_at'];received=b['received_at']-a['received_at']
    if not policy.minimum_sample_seconds <= observed <= policy.maximum_gap_seconds or not 0 <= received <= policy.maximum_gap_seconds:
        return 'SAMPLE_TIME_GAP_OR_NONADVANCING'
    return None


def _temporal(rows, frames, policy):
    suffix=[];resets=[]
    for row,frame in zip(rows,frames):
        if frame is None:
            suffix=[];resets.append(dict(book_id=row['id'],reason='UNHEALTHY_HISTORY_FRAME'));continue
        if suffix:
            reason=_link(suffix[-1][0],row,policy)
            if reason:suffix=[];resets.append(dict(book_id=row['id'],reason=reason))
        suffix.append((row,frame))
    result=dict(status='UNKNOWN',contiguous_book_ids=[r['id'] for r,_ in suffix],resets=resets,
                price_velocity_per_second=None,price_acceleration_per_second_squared=None,
                rms_midpoint_change_per_update=None,bid_visible_depth_delta=None,ask_visible_depth_delta=None,
                observed_book_update_rate=None,addition_rate=None,cancellation_rate=None,
                depth_change_cause='UNKNOWN_NO_ORDER_LIFECYCLE_EVIDENCE')
    if len(suffix)<2:return result
    velocities=[];deltas=[];durations=[]
    for (a,fa),(b,fb) in zip(suffix,suffix[1:]):
        dt=b['body']['observed_at']-a['body']['observed_at']
        delta=float(Decimal(fb['midpoint'])-Decimal(fa['midpoint']))
        velocities.append(delta/dt);deltas.append(delta);durations.append(dt)
    a,b=suffix[-2][1],suffix[-1][1]
    result.update(status='MEASURED_DECLARED_SEQUENCE',price_velocity_per_second=velocities[-1],
        rms_midpoint_change_per_update=math.sqrt(math.fsum(d*d for d in deltas)/len(deltas)),
        bid_visible_depth_delta=str(Decimal(b['bid_depth_units'])-Decimal(a['bid_depth_units'])),
        ask_visible_depth_delta=str(Decimal(b['ask_depth_units'])-Decimal(a['ask_depth_units'])),
        observed_book_update_rate=(len(suffix)-1)/math.fsum(durations))
    if len(velocities)>=2:
        result['price_acceleration_per_second_squared']=(velocities[-1]-velocities[-2])/((durations[-1]+durations[-2])/2)
    return result


def _trades(rows, policy):
    seen={};providers=set();buy=sell=unknown=Decimal(0);last=None
    for row in sorted(rows,key=lambda r:r['seq']):
        body=row['body'];p=body['payload'];providers.add(body['provider'])
        if p.get('version')!=TRADE_VERSION or p.get('record_type')!='PUBLIC_TRADE_PRINT':
            raise EvidenceError('PUBLIC_PRINT_SCHEMA_REQUIRED_NOT_ACCOUNT_FILL')
        key=(body['provider'],identity(p.get('trade_id')))
        price,size=number(p.get('price')),number(p.get('size'))
        if not 0 < price < 1 or not 0 < size <= 1_000_000:raise EvidenceError('PUBLIC_PRINT_NUMERIC_BOUND')
        signature=digest(dict(payload=p,observed_at=body['observed_at']))
        if key in seen:
            if seen[key]!=signature:raise EvidenceError('PUBLIC_PRINT_ID_REVISION_AMBIGUOUS')
            continue
        seen[key]=signature
        # Contract side (YES/NO) is part of the exact payout target. Never
        # overload it with the distinct aggressor direction (BUY/SELL).
        side=p.get('aggressor_side') if p.get('side_semantics')=='TAKER_SIDE' and p.get('taker_only_requested') is True else None
        if side=='BUY':buy+=size
        elif side=='SELL':sell+=size
        else:unknown+=size
        last=side if side in ('BUY','SELL') else None
    if len(providers)>1:raise EvidenceError('PUBLIC_PRINT_CROSS_PROVIDER_DEDUP_UNPROVEN')
    total=buy+sell+unknown
    return dict(distinct_public_prints=len(seen),buy_taker_units=str(buy),sell_taker_units=str(sell),
                unknown_aggressor_units=str(unknown),reported_units=str(total),last_reported_aggressor=last,
                signed_volume_imbalance=None if not total or unknown else str((buy-sell)/total),
                archived_prints_per_window_second=len(seen)/policy.history_window_seconds,
                coverage='SUPPLIED_ARCHIVED_SUBSET_NOT_PROVEN_COMPLETE_FEED',
                direction_authority='DECLARED_ADAPTER_TAKER_SEMANTICS',our_fill_count=None)


class MakerMicrostructure:
    def __init__(self,store):self.store=store

    @precise
    def evaluate(self,record_id,*,rule,market_id,side,book_ids,trade_ids,policy):
        identity(record_id,maximum=100)
        if (type(book_ids) is not tuple or not 1 <= len(book_ids) <= 32
                or type(trade_ids) is not tuple or len(trade_ids)>31
                or len(set(book_ids+trade_ids))!=len(book_ids+trade_ids)
                or not isinstance(policy,MicrostructurePolicy)):
            raise EvidenceError('MICROSTRUCTURE_INPUT_BOUND')
        target=contract_target(rule,market_id,side)
        request=dict(rule=asdict(rule),target=target,book_ids=book_ids,trade_ids=trade_ids,policy=asdict(policy))
        request_sha=digest(request);store=self.store
        try:prior=store.get(record_id)
        except EvidenceError as exc:
            if str(exc)!='EVIDENCE_MISSING':raise
        else:
            d=prior['body'].get('details',{})
            if prior['kind']!='MEASUREMENT' or d.get('version')!=VERSION or d.get('request_sha256')!=request_sha:
                raise EvidenceError('MICROSTRUCTURE_REQUEST_ID_COLLISION')
            return prior
        at=finite(store.clock());event=rule.payload['event_id'];refs=[];heads=[]
        features=temporal=flow=None;outcome='GATED';reason=None;latest_book_id=None
        try:
            for kind in ('BOOK','TRADE'):
                head=store.latest(kind=kind,event_id=event)
                heads.append((kind,event,head['seq'] if head else 0))
            rows=[_source(store,k,rule=rule,target=target,kind='BOOK',cutoff=at,policy=policy) for k in book_ids]
            refs.extend(r['id'] for r in rows);rows.sort(key=lambda r:r['seq'])
            if len({(r['body']['provider'],r['body']['source_identity']) for r in rows})!=1:
                raise EvidenceError('MICROSTRUCTURE_ONE_EXACT_BOOK_CHANNEL_REQUIRED')
            newest=rows[-1];b=newest['body'];latest_book_id=newest['id']
            latest=store.latest_source(kind='BOOK',event_id=event,provider=b['provider'],source_identity=b['source_identity'])
            if latest['id']!=newest['id']:raise EvidenceError('MICROSTRUCTURE_CURRENT_BOOK_REQUIRED')
            if at-b['observed_at']>policy.maximum_book_age_seconds or at-b['received_at']>policy.maximum_book_age_seconds:
                raise EvidenceError('MICROSTRUCTURE_BOOK_STALE')
            frames=[]
            for row in rows:
                try:frame=_frame(row,policy)
                except EvidenceError:
                    if row['id']==newest['id']:raise
                    frame=None
                frames.append(frame)
            features=dict(frames[-1],book_observation_age_seconds=at-b['observed_at'],
                          book_receipt_age_seconds=at-b['received_at'],depth_levels=policy.depth_levels)
            temporal=_temporal(rows,frames,policy)
            trades=[_source(store,k,rule=rule,target=target,kind='TRADE',cutoff=at,policy=policy) for k in trade_ids]
            refs.extend(r['id'] for r in trades);flow=_trades(trades,policy)
            outcome='MEASURED_RESEARCH_FEATURES';reason='OBSERVED_INPUTS_NOT_EXECUTION_QUALITY_VALIDATION'
        except EvidenceError as exc:
            reason=str(exc);features=temporal=flow=None;heads=[]
        return store.audit(record_id,event_id=event,kind='MEASUREMENT',details=dict(
                version=VERSION,request=request,request_sha256=request_sha,as_of=at,outcome=outcome,reason=reason,
                current_book_id=latest_book_id,features=features,temporal=temporal,public_flow=flow,
                fill_probability=None,fill_hazard=None,queue_position=None,expected_markout=None,
                adverse_move_conditional_on_fill=None,conservative_maker_ev=None,inventory_state=None,
                distance_to_fair_value=None,time_to_release_seconds=None,event_state=None,
                public_trade_through_is_our_fill=False,learned_execution_model_validated=False,
                first_reviewed_canary_does_not_require_prior_live_fills=True,
                financial_authority=False,actual_trading_pnl=None),
                evidence_ids=tuple(refs),expected_heads=tuple(heads))
