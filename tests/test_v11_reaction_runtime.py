from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest

from polymarket_scanner.v11.event_risk import EventRiskEngine
from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.paper_runtime import PaperRuntime, RuntimePolicy
from polymarket_scanner.v11.reaction_runtime import (PWSLeadRequest, PWSLeadEventAdapter,
    SourceReleaseRequest, SourceReleaseEventAdapter, PositionExitEventAdapter)
from polymarket_scanner.v11.runtime_health import SourceNeed
from polymarket_scanner.v11.strategy_admission import StrategyAdmission
from polymarket_scanner.v11.strategy_runtime import MultiStrategyEventAdapter
from polymarket_scanner.v11.valuation import contract_target
from test_v11_basket_coordinator import rig
from test_v11_certification_rules import setup
from test_v11_event_risk import policy as event_policy, metrics
from test_v11_model_artifacts import bundle
from test_v11_position_management import inventory, request as exit_request
from test_v11_pws_admission import joined, arrival, coordinator
from test_v11_source_release import release_factory
from test_v11_strategy_pipeline import factory
from test_v11_paper_runtime import queue
from test_v11_runtime_health import monitor, ready


def health(r, monkeypatch, strategy, model='model2'):
    row = r['store'].get(model); b = row['body']; event = r['context'].event_id
    m = monitor(r, monkeypatch, scopes={event:(strategy,)}, sources=(
        SourceNeed(event, strategy, 'MODEL', b['provider'], b['source_identity'], 120.),))
    ready(r, m)
    return m


def pws_request(r):
    return PWSLeadRequest(replace(r['request'], preconfirmation_id=None), 'observation-pin',
                          ('ablation-model',), r['lead_kw']['policy'])


def release_request(r):
    return SourceReleaseRequest(replace(r['request'], source_release_id=None),
        r['pin_kw']['previous_official_id'], r['pin_kw']['current_official_id'], 'schedule')


def claimed(r, adapter, *, book=None):
    q = queue(r); q.publish('reaction-update', kind='BOOK', evidence_id=book or r['request'].book_id)
    with q.work('reaction-claim') as claim:
        assert claim and not claim['requires_full_census']
        result = adapter.evaluate(claim, 'claimed-reaction')
        completion = q.finish('reaction-done', claim_id=claim['claim_id'], result_ids=result.result_ids)
    assert completion['body']['details']['result']['outcome'] == 'RESEARCH_EVALUATED'
    return result


def test_pws_runtime_joins_observation_ablation_and_separate_payout_under_queue_claim(joined, monkeypatch):
    r = joined; store = r['store']; health(r, monkeypatch, 'PWS_OBSERVATION_LEAD', 'lead-model')
    adapter = MultiStrategyEventAdapter(store, (('pws', PWSLeadEventAdapter(store, lambda claim:(pws_request(r),))),))
    result = claimed(r, adapter); d = store.get(result.result_ids[0])['body']['details']
    assert d['outcome'] == 'REJECT' and d['reason'] == 'CONSERVATIVE_EV_NOT_ABOVE_THRESHOLD'
    assert d['executable_exit_value'] is None and not result.proposals
    pair = store.get(d['request']['preconfirmation_id'])['body']['details']['assessment']
    lead = store.get(pair['lead_id'])['body']['details']
    assert lead['target'] == 'NEXT_OFFICIAL_OBSERVATION' and lead['valuation_type'] == 'OBSERVATION_ONLY'
    assert lead['settlement_prediction'] is None and lead['executable_exit_proceeds'] is None
    assert pair['observation_bundle_sha256'] != pair['payout_bundle_sha256']
    assert d['prediction']['target'] != lead['target'] and not d['financial_authority']
    assert coordinator(r)._head() is None


def test_official_arrival_between_runtime_pair_and_economics_invalidates_pws(joined, monkeypatch):
    from polymarket_scanner.v11.strategy_pipeline import TemperatureStrategies
    r = joined; store = r['store']; health(r, monkeypatch, 'PWS_OBSERVATION_LEAD', 'lead-model')
    original = TemperatureStrategies.evaluate
    def changed(engine, *args, **kwargs):
        arrival(r)
        return original(engine, *args, **kwargs)
    monkeypatch.setattr(TemperatureStrategies, 'evaluate', changed)
    result = PWSLeadEventAdapter(store, lambda claim:(pws_request(r),)).evaluate(
        {'event_id':r['context'].event_id}, 'arrival')
    d = store.get(result.result_ids[0])['body']['details']
    assert d['outcome'] == 'GATED' and not result.proposals
    assert d['reason'] == 'NEW_OFFICIAL_EVIDENCE_RECOMPUTE_THESIS' and coordinator(r)._head() is None


def test_pws_ablation_cannot_hide_the_same_pws_input(joined, monkeypatch):
    r = joined; health(r, monkeypatch, 'PWS_OBSERVATION_LEAD', 'lead-model')
    req = replace(pws_request(r), without_pws_model_ids=('lead-model',))
    result = PWSLeadEventAdapter(r['store'], lambda claim:(req,)).evaluate({'event_id':r['context'].event_id}, 'bad-ablation')
    assert r['store'].get(result.result_ids[0])['body']['details']['reason'] == 'LEAD_PAIRED_ABLATION_NOT_SAME_NON_PWS_EVIDENCE'
    assert not result.proposals


@pytest.mark.parametrize('strategy', ['SOURCE_SHOCK', 'RELEASE_OPPORTUNITY'])
def test_runtime_received_release_retains_exact_receipt_and_independent_payout_gate(release_factory, monkeypatch, strategy):
    r = release_factory(strategy); store = r['store']; health(r, monkeypatch, strategy, 'recomputed-model')
    a = SourceReleaseEventAdapter(store, lambda claim:(release_request(r),))
    result = claimed(r, MultiStrategyEventAdapter(store, (('release', a),)))
    d = store.get(result.result_ids[0])['body']['details']
    assert d['outcome'] == 'REJECT' and d['reason'] == 'CONSERVATIVE_EV_NOT_ABOVE_THRESHOLD'
    release = store.get(d['request']['source_release_id'])['body']['details']['assessment']
    assert release['current_official_id'] == 'received-release' and release['event_state'] == 'EVENT'
    assert not release['settlement_finality'] and not release['schedule']['proves_actual_release']
    assert not result.proposals and coordinator(r)._head() is None


@pytest.mark.parametrize('defect', ['schedule_only', 'pre_release_book', 'stale_model'])
def test_runtime_release_cannot_substitute_schedule_stale_model_or_old_book(release_factory, monkeypatch, defect):
    r = release_factory(**({'book_before':True} if defect == 'pre_release_book' else
                            {'stale_model':True} if defect == 'stale_model' else {}))
    health(r, monkeypatch, 'SOURCE_SHOCK', 'recomputed-model'); req = release_request(r)
    if defect == 'schedule_only': req = replace(req, current_official_id='schedule')
    result = SourceReleaseEventAdapter(r['store'], lambda claim:(req,)).evaluate({'event_id':r['context'].event_id}, defect)
    d = r['store'].get(result.result_ids[0])['body']['details']
    assert d['outcome'] == 'GATED' and not result.proposals
    assert coordinator(r)._head() is None


def test_failed_request_does_not_discard_valid_release_evaluation_in_same_claim(release_factory, monkeypatch):
    r = release_factory(); health(r, monkeypatch, 'SOURCE_SHOCK', 'recomputed-model'); req = release_request(r)
    a = SourceReleaseEventAdapter(r['store'], lambda claim:(replace(req, current_official_id='schedule'), req))
    result = claimed(r, a)
    ds = [r['store'].get(key)['body']['details'] for key in result.result_ids]
    assert [d['outcome'] for d in ds] == ['GATED', 'REJECT'] and not result.proposals


def exit_runtime(r, monkeypatch, *, reconcile=False):
    from polymarket_scanner.v11.paper_reconciliation import PaperReconciliation, ReconciliationPolicy
    store = r['store']; c = coordinator(r); event = r['context'].event_id
    m = health(r, monkeypatch, r['scope'].strategy); q = queue(r); selected = []
    def census(claim, prefix):
        books = []
        for bucket in r['rule'].payload['partition']:
            for side in ('YES', 'NO'):
                target = contract_target(r['rule'], bucket['market_id'], side); key = prefix+':'+str(len(books)); books.append(key)
                store.capture(key, event_id=event, kind='BOOK', provider='exit-census-fixture', source_identity=target['token_id'],
                    revision=prefix, observed_at=r['now'][0], evidence_class='SYNTHETIC', payload=dict(target,
                    rule_fingerprint=r['rule'].sha256, collateral_asset='FIXTURE_COLLATERAL', snapshot_type='FULL',
                    stream_healthy=True, bids=[dict(price='.1',size='20')], asks=[dict(price='.2',size='20')]))
        old = store.get('official2')['body']; official = prefix+':official'
        store.capture(official, event_id=event, kind='OFFICIAL_OBSERVATION', provider=old['provider'], source_identity=old['source_identity'],
            revision=prefix, observed_at=r['now'][0], payload=old['payload'], evidence_class='SYNTHETIC')
        selected[:] = [books[0], official]
        return dict(book_ids=tuple(books), source_ids=(official,), rule_state_id=store.latest(kind='RULE_STATE',event_id=event)['id'])
    def requests(claim):
        state = EventRiskEngine(store).step(claim['claim_id']+':exit-risk', context=r['context'],
            policy=event_policy(), binding=r['binding'], metrics=metrics(r['now'][0]),
            book_ids=(selected[0],), source_ids=('model2', selected[1]))
        pin = StrategyAdmission(store).pin(claim['claim_id']+':exit-pin', **r['admission_kw'])
        return (exit_request(r, admission_id=pin['id'], book_id=selected[0], event_state_id=state['id']),)
    a = MultiStrategyEventAdapter(store, (('exit', PositionExitEventAdapter(c, requests)),))
    return PaperRuntime(c, q, m, RuntimePolicy('exit-runtime-fixture'), evaluator=a, census=census,
                        worker_id='worker', generation='exit-runtime',
                        reconciliation=PaperReconciliation(c,ReconciliationPolicy('exit-receipts'),queue=q) if reconcile else None)


@pytest.mark.parametrize('complete', [False, True])
def test_runtime_rechecks_actual_inventory_and_whole_basket_before_common_exit_reservation(rig, monkeypatch, complete):
    inventory(rig, complete=complete); before = deepcopy(coordinator(rig)._state(coordinator(rig)._head())['lots'])
    rt = exit_runtime(rig, monkeypatch); row = rt.tick('exit-tick'); d = row['body']['details']; store = rig['store']
    assert d['outcome'] == 'TICK_COMPLETED', d
    completion = store.get(d['evaluation_ids'][0])['body']['details']
    result = store.get(completion['request']['result_ids'][0])['body']['details']
    assert result['outcome'] == ('HOLD_RESEARCH_ESTIMATE' if complete else 'REDUCE_RESEARCH_CANDIDATE'), result['reason']
    state = rt.coordinator._state(rt.coordinator._head())
    assert state['lots'] == before and not state['realized_entries']
    sells = [p for p in state['intents'].values() if p['direction'] == 'SELL']
    if complete: assert not sells and not d['account_batch_ids']
    else:
        assert len(sells) == 1 and sells[0]['event_queue_completion_id'] == d['evaluation_ids'][0]
        assert sells[0]['status'] == 'RESERVED' and Decimal(sells[0]['units']) == 1
        assert sells[0]['valuation_id'] == result['proposal']['valuation_id']
    head = rt.coordinator._head()
    assert rt.tick('exit-tick') == row and rt.coordinator._head() == head
    assert not d['real_orders_sent'] and not d['forward_or_live_acceptance']


def test_archived_receipts_reach_fresh_inventory_exit_and_realized_paper_accounting(rig,monkeypatch):
    from test_v11_basket_coordinator import reserve, proof
    reserve(rig)
    proof(rig,'basket:leg:0','archived-buy','PAPER_FILL',fill_id='archived-buy',units='2',all_in_collateral='.4',direction='BUY')
    for leg in (1,2):
        proof(rig,'basket:leg:'+str(leg),'archived-terminal'+str(leg),'PAPER_TERMINAL',status='CANCELED',
            cumulative_fill_units='0',all_fills_reconciled=True,terminal_authority='SYNTHETIC_PAPER_ENGINE_FINAL')
    rt=exit_runtime(rig,monkeypatch,reconcile=True)
    assert not rt.coordinator._state(rt.coordinator._head())['lots']
    d=rt.tick('archived-entry')['body']['details'];assert d['outcome']=='TICK_COMPLETED',d
    state=rt.coordinator._state(rt.coordinator._head())
    sells=[p for p in state['intents'].values() if p['direction']=='SELL']
    assert len(sells)==1 and sells[0]['status']=='RESERVED' and sells[0]['units']=='1'
    assert sells[0]['event_queue_completion_id']==d['evaluation_ids'][0]
    proof(rig,sells[0]['proposal_id'],'archived-sell','PAPER_FILL',fill_id='archived-sell',units='1',
        all_in_collateral='.1',direction='SELL')
    after=rt.tick('archived-exit')['body']['details'];assert after['evaluation_ids']
    state=rt.coordinator._state(rt.coordinator._head())
    assert state['lots']['archived-buy']['units']=='1' and Decimal(state['cash'])==Decimal('9.7')
    assert len(state['realized_entries'])==1 and Decimal(state['realized_entries'][0]['pnl'])==Decimal('-.1')
    head=rt.coordinator._head();assert rt.tick('archived-exit')['body']['details']==after and rt.coordinator._head()==head
    assert not after['real_orders_sent'] and not after['forward_or_live_acceptance']


def test_new_queue_gap_after_runtime_exit_evaluation_prevents_inventory_reservation(rig, monkeypatch):
    inventory(rig); rt = exit_runtime(rig, monkeypatch); original = rt.coordinator.coordinate
    def race(*args, **kw):
        rt.queue.stream_gap('exit-gap', event_id=rig['context'].event_id, reason='SYNTHETIC_RACE')
        return original(*args, **kw)
    monkeypatch.setattr(rt.coordinator, 'coordinate', race)
    d = rt.tick('exit-race')['body']['details']
    assert d['account_batch_ids']
    batch = rig['store'].get(d['account_batch_ids'][0])['body']['details']
    assert not batch['reserved_intent_ids'] and batch['results'][0]['reason'] == 'EVENT_QUEUE_REQUIRES_CENSUS'
    assert all(p['direction'] == 'BUY' for p in rt.coordinator._state(rt.coordinator._head())['intents'].values())


def test_exit_adapter_cannot_evaluate_another_event_or_overdraw_held_inventory(rig, monkeypatch):
    inventory(rig); health(rig, monkeypatch, rig['scope'].strategy)
    c = coordinator(rig); req = exit_request(rig, units='3')
    a = PositionExitEventAdapter(c, lambda claim:(req,))
    for event, expected in [('foreign', 'RUNTIME_EVALUATION_EVENT_MISMATCH'),
                            (rig['context'].event_id, 'INSUFFICIENT_HELD_INVENTORY')]:
        result = a.evaluate({'event_id':event}, event)
        assert rig['store'].get(result.result_ids[0])['body']['details']['reason'] == expected
        assert not result.proposals


@pytest.mark.parametrize('adapter_type', [PWSLeadEventAdapter, SourceReleaseEventAdapter, PositionExitEventAdapter])
def test_reaction_adapters_bound_requests_before_any_evaluation(rig, adapter_type):
    owner = coordinator(rig) if adapter_type is PositionExitEventAdapter else rig['store']
    a = adapter_type(owner, lambda claim:(object(),)*7)
    with pytest.raises(EvidenceError, match='REQUEST_BOUND'):
        a.evaluate({'event_id':rig['context'].event_id}, 'overbound')
    assert coordinator(rig)._head() is None


def test_runtime_refuses_exit_evaluator_bound_to_different_coordinator(rig, monkeypatch):
    c = coordinator(rig); other = coordinator(rig); q = queue(rig)
    m = health(rig, monkeypatch, rig['scope'].strategy)
    a = MultiStrategyEventAdapter(rig['store'], (('exit',PositionExitEventAdapter(other,lambda claim:())),))
    with pytest.raises(EvidenceError, match='EVALUATOR_ACCOUNT_MISMATCH'):
        PaperRuntime(c,q,m,RuntimePolicy('mismatch'),evaluator=a)
