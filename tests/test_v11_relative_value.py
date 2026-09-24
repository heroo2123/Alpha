from dataclasses import replace
from decimal import Decimal

import pytest

from polymarket_scanner.v11.evidence import EvidenceError, digest
from polymarket_scanner.v11.event_risk import SafetyReductions
from polymarket_scanner.v11.relative_value import DiscoveryRequest, RelativeValueStrategies
from polymarket_scanner.v11.valuation import contract_target
from test_v11_basket_coordinator import rig
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_model_governance import authority
from test_v11_strategy_pipeline import factory
from test_v11_pws_admission import coordinator
from test_v11_valuation import costs


def request(rig, **changes):
    return DiscoveryRequest(**dict(dict(admission_id='pin',event_state_id='state2',instruments=rig['kw']['legs'],
         desired_positions=rig['proposal'].desired_positions,model_input_ids=('model2',),policy=rig['kw']['policy'],
         expires_at=rig['now'][0]+20), **changes))


def scan(rig, key='scan', req=None):
    return RelativeValueStrategies(rig['store']).evaluate(key, req or request(rig))['body']['details']


def test_full_event_discovery_finds_underround_and_preserves_rejected_individual_gaps(rig):
    d = scan(rig)
    assert d['outcome']=='CANDIDATES' and len(d['proposals'])==1
    assert len(d['opportunities'])==6  # complete set, three singles, two adjacent pairs
    assert d['artifact_refs'] and d['model_epoch']==1 and d['prediction']['calibration_status']=='UNCALIBRATED'
    assert any(i['point_underpricing_diagnostic'] for i in d['instruments'])
    assert all(r['economics']=='REJECT' for r in d['opportunities'] if 'INDIVIDUAL_POINT_PRICE_GAP' in r['diagnostics'])
    adjacent = [r for r in d['opportunities'] if 'ADJACENT_RELATIVE_VALUE' in r['diagnostics']]
    assert len(adjacent)==2 and all('adjacent_comparison' in r for r in adjacent)
    assert not d['quotes_normalized_as_probabilities'] and not d['independent_bucket_risk']
    curve=d['whole_event_curve']
    assert abs(Decimal(curve[-1]['model_cumulative_probability'])-1)<Decimal('1e-12')
    assert Decimal(curve[-1]['quoted_acquisition_cumulative_cost'])==Decimal('.6')
    assert d['quoted_curve_is_not_a_probability_distribution']
    p = RelativeValueStrategies(rig['store']).proposals('scan')
    account = coordinator(rig).coordinate('account',p)['body']['details']
    assert len(account['reserved_intent_ids'])==3 and Decimal(account['risk']['reserved_cash'])==Decimal('1.2')
    assert not rig['store'].records(kind='TRADE')


def test_complements_and_complete_no_set_share_common_outcome_model(rig):
    legs = list(rig['kw']['legs']); desired = list(rig['proposal'].desired_positions)
    for i,leg in enumerate(tuple(legs)):
        target = contract_target(rig['rule'],leg.market_id,'NO'); key = 'no-book'+str(i)
        body = rig['store'].get(leg.book_id)['body']
        rig['store'].capture(key,event_id=rig['context'].event_id,kind='BOOK',provider='fixture-no',
                 source_identity=target['token_id'],revision='1',observed_at=rig['now'][0],evidence_class='SYNTHETIC',
                 payload={**body['payload'],**target})
        legs.append(replace(leg,side='NO',book_id=key)); desired.append((target['token_id'],'2'))
    d = scan(rig,req=request(rig,instruments=tuple(legs),desired_positions=tuple(desired)))
    assert len(d['opportunities'])==13 and len(d['proposals'])==5
    assert any('EXHAUSTIVE_NO' in r['diagnostics'] and r['economics']=='CANDIDATE' for r in d['opportunities'])
    assert sum('CONDITION_COMPLEMENT' in r['diagnostics'] for r in d['opportunities'])==3
    # The common coordinator decides conflicts; discovery doesn't reserve every
    # overlapping explanation or count each as a separate profitable fill.
    p = RelativeValueStrategies(rig['store']).proposals('scan')
    d = coordinator(rig).coordinate('account',p)['body']['details']
    assert len(d['reserved_intent_ids'])==6 and Decimal(d['risk']['reserved_cash'])==Decimal('2.4')


def test_missing_sibling_instrument_never_turns_partial_set_into_complete_set(rig):
    d = scan(rig,req=request(rig,instruments=rig['kw']['legs'][:2],desired_positions=rig['proposal'].desired_positions[:2]))
    assert d['outcome']=='NO_QUALIFIED_OPPORTUNITY' and not d['proposals']
    assert not any('EXHAUSTIVE_YES' in r['diagnostics'] for r in d['opportunities'])


def test_one_unknown_cost_is_explicit_and_cannot_create_zero_fee_underround(rig):
    legs=list(rig['kw']['legs']);legs[1]=replace(legs[1],costs=costs(ACQUISITION_FEES=None))
    d=scan(rig,req=request(rig,instruments=tuple(legs)))
    whole=next(r for r in d['opportunities'] if 'EXHAUSTIVE_YES' in r['diagnostics'])
    assert whole['economics']=='GATED' and not d['proposals']
    assert any('UNKNOWN_OR_MISSING_COST' in reason for reason in whole['reasons'])


def test_unleased_inputs_are_not_inferred_even_if_the_book_looks_profitable(rig):
    d=scan(rig,req=request(rig,model_input_ids=('model1',)))
    assert d['outcome']=='GATED' and d['reason']=='ALL_INFERENCE_MODELS_REQUIRE_ADMISSION_LEASE'
    assert d['prediction'] is None and not d['proposals']


def test_operator_halt_gates_whole_strategy_with_no_account_mutation(rig):
    SafetyReductions(rig['store']).apply('stop',scope='ACCOUNT',scope_id='account',action='NO_NEW_ORDERS',actor='fixture',reason='TEST')
    d=scan(rig)
    assert d['outcome']=='GATED' and 'STATE_CHANGED' in d['reason']
    assert coordinator(rig)._head() is None


def test_completed_replay_never_refreshes_expiry_or_repeats_scan(rig):
    req=request(rig);old=scan(rig,req=req)
    count=len(rig['store'].records(kind='MEASUREMENT',limit=1000));rig['now'][0]+=1000
    assert scan(rig,req=req)==old and len(rig['store'].records(kind='MEASUREMENT',limit=1000))==count
    with pytest.raises(EvidenceError,match='REQUEST_ID_COLLISION'):
        scan(rig,req=replace(req,maximum_proposals=1))
    with pytest.raises(EvidenceError,match='EXPIRED|SOURCE_STALE'):
        coordinator(rig)._prepare(RelativeValueStrategies(rig['store']).proposals('scan')[0],rig['now'][0])


def test_partial_scan_resume_reuses_values_and_candidate_without_refresh(rig,monkeypatch):
    store=rig['store'];old=store.audit;req=request(rig)
    def interrupt(key,**kw):
        if key=='scan':raise RuntimeError('SIMULATED_INTERRUPTION')
        return old(key,**kw)
    monkeypatch.setattr(store,'audit',interrupt)
    with pytest.raises(RuntimeError,match='SIMULATED_INTERRUPTION'):scan(rig,req=req)
    saved=store.get('scan:candidate:0');value=store.get('scan:value:0')
    rig['now'][0]+=.1;monkeypatch.setattr(store,'audit',old)
    d=scan(rig,req=req)
    assert d['outcome']=='CANDIDATES' and store.get('scan:candidate:0')==saved and store.get('scan:value:0')==value


def test_model_change_during_scan_invalidates_all_candidate_outputs(rig,monkeypatch):
    from polymarket_scanner.v11 import relative_value as module
    old=module.predict_with_bundle
    def demote(*args,**kwargs):
        p=old(*args,**kwargs);state=rig['model_state'][0]
        rig['model_state'][0]=authority.transition(state,action='DEMOTE',expected_state_sha256=digest(state),now=21.,
                                                 reason='TEST',size_multiplier=.5)
        return p
    monkeypatch.setattr(module,'predict_with_bundle',demote)
    d=scan(rig)
    assert d['outcome']=='GATED' and d['reason']=='MODEL_MANUAL_REVIEW' and not d['proposals']
    assert all(r['proposal'] is None for r in d['opportunities'])


def test_queue_completes_exact_discovery_proposals_before_common_account_admission(rig):
    from polymarket_scanner.v11.event_queue import EventQueue, EventRoute, TriggerPolicy, KINDS
    p=rig['rule'].payload;now=rig['now'][0]
    route=EventRoute(p['event_id'],p['station'],p['target_date'],p['family'],rig['rule'].sha256,
                    tuple(t for b in p['partition'] for t in (b['yes_token'],b['no_token'])),now+100,('MODEL',))
    policy=TriggerPolicy('fixture',1,1,4,16,30.,10.,100_000,60.,
                        tuple((k,60.) for k in sorted(KINDS)),((p['station'],30.),))
    queue=EventQueue(rig['store'],routes=(route,),policy=policy)
    queue.publish('enqueue',kind='MODEL',evidence_id='model2')
    with queue.work('work'):
        d=scan(rig)
        assert d['queue_result_ids']==['scan:candidate:0']
        queue.finish('done',claim_id='work',result_ids=tuple(d['queue_result_ids']))
    proposals=RelativeValueStrategies(rig['store']).proposals('scan')
    d=coordinator(rig).coordinate('account',proposals)['body']['details']
    assert len(d['reserved_intent_ids'])==3


def test_duplicate_instruments_fail_instead_of_double_counting_payout(rig):
    legs=rig['kw']['legs'];p=rig['proposal'].desired_positions
    d=scan(rig,req=request(rig,instruments=(legs[0],legs[0]),desired_positions=(p[0],p[1])))
    assert d['reason']=='DISCOVERY_DUPLICATE_TOKEN' and not d['proposals']


def test_model_probability_input_cannot_omit_a_bucket_to_renormalize_price_discrepancy(rig,monkeypatch):
    from polymarket_scanner.v11 import relative_value as module
    from polymarket_scanner.v11.probability import BucketPrediction
    from polymarket_scanner.v11.evidence import canonical
    old=module.predict_with_bundle
    def incomplete(*args,**kwargs):
        prediction=old(*args,**kwargs);p=prediction.payload;p['buckets']=p['buckets'][:-1]
        return BucketPrediction(canonical(p),digest(p))
    monkeypatch.setattr(module,'predict_with_bundle',incomplete)
    d=scan(rig)
    assert d['outcome']=='GATED' and d['reason']=='DISCOVERY_WHOLE_EVENT_VECTOR_REQUIRED'
    assert not d['proposals'] and not d['whole_event_curve']
