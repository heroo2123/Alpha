from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest

from polymarket_scanner.v11.event_risk import EventRiskEngine
from polymarket_scanner.v11.evidence import EvidenceError, digest
from polymarket_scanner.v11.paper_runtime import TemperatureEventAdapter, PaperRuntime
from polymarket_scanner.v11.reaction_runtime import PWSLeadEventAdapter, SourceReleaseEventAdapter, PositionExitEventAdapter
from polymarket_scanner.v11.request_assembly import (SourceSelector, ScopeInputs, TargetPlan, RequestAssembler,
    EntryRequestFactory, RelativeValueRequestFactory, PWSRequestFactory, SourceReleaseRequestFactory, ExitRequestFactory)
from polymarket_scanner.v11.strategy_runtime import MultiStrategyEventAdapter, RelativeValueEventAdapter
from polymarket_scanner.v11.valuation import HOLD_RISKS, SALE_RISKS, SALE, contract_target
from test_v11_basket_coordinator import rig
from test_v11_certification_rules import setup
from test_v11_event_risk import policy as event_policy, metrics
from test_v11_model_artifacts import bundle
from test_v11_position_management import inventory
from test_v11_pws_admission import joined, coordinator
from test_v11_source_release import release_factory
from test_v11_strategy_pipeline import factory
from test_v11_paper_runtime import queue
from test_v11_reaction_runtime import exit_runtime
from test_v11_strategy_runtime import joined as relative_runtime, run_candidate
from test_v11_valuation import costs


def inputs(r, kw=None):
    raw = dict(kw or r['admission_kw']); leases = raw.pop('source_leases'); selectors = []
    for s in leases:
        b = r['store'].get(s.evidence_id)['body']
        selectors.append(SourceSelector(s.role,b['provider'],b['source_identity'],s.maximum_age_seconds))
    return ScopeInputs(**raw,sources=tuple(selectors))


def target(r, **changes):
    q = r['request']
    return replace(TargetPlan(q.market_id,q.side,q.units,q.desired_total_units,q.costs), **changes)


def assembler(r, q, kw=None, provider='fixture'):
    return RequestAssembler(q,inputs(r,kw),book_provider=provider,valuation_policy=r['request'].valuation_policy,lifetime_seconds=10.)


def state_for(r, books, source_ids, *, release=False, key='assembled-risk'):
    return EventRiskEngine(r['store']).step(key,context=r['context'],policy=event_policy(),binding=r['binding'],
        metrics=replace(metrics(r['now'][0]),special_observation=release),book_ids=tuple(books),source_ids=tuple(source_ids))


def evaluate(r, q, adapter, book=None, key='assembly'):
    q.publish(key+':update',kind='BOOK',evidence_id=book or r['request'].book_id)
    with q.work(key+':claim') as claim:
        result = adapter.evaluate(claim,key)
        q.finish(key+':done',claim_id=claim['claim_id'],result_ids=result.result_ids)
    return result


def test_current_entry_factory_drives_real_pipeline_without_prebuilt_pin_ids(factory):
    r = factory(); q = queue(r); a = assembler(r,q); f = EntryRequestFactory(a,(target(r),))
    result = evaluate(r,q,TemperatureEventAdapter(r['store'],f)); d = r['store'].get(result.result_ids[0])['body']['details']
    assert d['request']['admission_id'] != 'pin' and d['request']['model_input_ids'] == ['model2']
    assert d['outcome'] == 'REJECT' and d['reason'] == 'CONSERVATIVE_EV_NOT_ABOVE_THRESHOLD'
    assert d['prediction']['calibration_status'] == 'UNCALIBRATED' and not result.proposals
    assert coordinator(r)._head() is None


def test_new_model_receipt_is_selected_and_old_risk_state_cannot_silently_cover_it(factory):
    r = factory(); q = queue(r); a = assembler(r,q); f = EntryRequestFactory(a,(target(r),))
    b = r['store'].get('model2')['body']
    r['store'].capture('model3',event_id=r['context'].event_id,kind='MODEL',provider=b['provider'],source_identity=b['source_identity'],
        revision='new',issued_at=r['now'][0],observed_at=r['now'][0],payload=b['payload'],evidence_class='SYNTHETIC')
    q.publish('update',kind='BOOK',evidence_id='book2')
    with q.work('claim') as claim:
        with pytest.raises(EvidenceError,match='EVENT_RISK_INPUTS_CHANGED_RECOMPUTE'): f(claim)
        state_for(r,('book2',),('model3','official2'))
        requests = f(claim)
        assert requests[0].model_input_ids == ('model3',)
        assert requests[0].expires_at <= r['now'][0]+10


def test_source_selector_never_falls_back_to_another_available_provider(factory):
    r = factory(); q = queue(r); original = inputs(r)
    missing = replace(original,sources=(replace(original.sources[0],provider='missing-provider'),))
    a = RequestAssembler(q,missing,book_provider='fixture',valuation_policy=r['request'].valuation_policy,lifetime_seconds=10.)
    f = EntryRequestFactory(a,(target(r),))
    available = EntryRequestFactory(assembler(r,q),(target(r),))
    adapters = (('missing',TemperatureEventAdapter(r['store'],f)),
                ('available',TemperatureEventAdapter(r['store'],available)))
    adapter = MultiStrategyEventAdapter(r['store'],adapters)
    result = evaluate(r,q,adapter)
    ds = [r['store'].get(key)['body']['details'] for key in result.result_ids]
    assert ds[0]['reason'] == 'ASSEMBLY_REQUIRED_SOURCE_UNAVAILABLE' and ds[1]['outcome'] == 'REJECT'
    assert not result.proposals


def test_assembly_requires_the_actual_held_queue_claim(factory):
    r = factory(); q = queue(r); f = EntryRequestFactory(assembler(r,q),(target(r),))
    before = r['store'].pin_read_view()
    with pytest.raises(EvidenceError,match='HELD_EVENT_CLAIM_REQUIRED'):
        f(dict(event_id=r['context'].event_id,claim_id='invented'))
    assert r['store'].pin_read_view() == before


def test_plan_change_after_adapter_binding_is_rejected(factory):
    r = factory(); q = queue(r); f = EntryRequestFactory(assembler(r,q),(target(r),)); a = TemperatureEventAdapter(r['store'],f)
    f.targets = (target(r,units='1'),)
    q.publish('update',kind='BOOK',evidence_id='book2')
    with q.work('claim') as claim:
        with pytest.raises(EvidenceError,match='REQUEST_PLAN_CHANGED_REVIEW_REQUIRED'): a.evaluate(claim,'changed')


def test_plan_binding_mutation_is_detected_without_overwriting_original_rule(factory):
    r = factory(); q = queue(r); a = assembler(r,q); original = deepcopy(r['rule'].payload)
    a.inputs = replace(a.inputs,binding=replace(a.inputs.binding,config_sha256='f'*64))
    with pytest.raises(EvidenceError,match='CONFIGURATION_CHANGED'): a.check({})
    assert r['rule'].payload == original


def test_gap_requires_new_census_before_any_request_assembly(factory):
    r = factory(); q = queue(r); f = EntryRequestFactory(assembler(r,q),(target(r),))
    q.stream_gap('gap',event_id=r['context'].event_id,reason='SYNTHETIC_GAP')
    with q.work('claim') as claim:
        with pytest.raises(EvidenceError,match='FRESH_CENSUS_REQUIRED'): f(claim)


@pytest.mark.parametrize('defect',['token','age'])
def test_current_book_selector_does_not_use_an_old_or_wrong_exact_target(factory,defect):
    r = factory(); q = queue(r); a = assembler(r,q); body = r['store'].get('book2')['body']; p = deepcopy(body['payload'])
    if defect=='token': p['token_id'] = 'wrong-token'
    else: r['now'][0] += 31
    r['store'].capture('changed-book',event_id=r['context'].event_id,kind='BOOK',provider=body['provider'],
        source_identity=body['source_identity'],revision='changed',observed_at=body['observed_at'],payload=p,evidence_class='SYNTHETIC')
    with pytest.raises(EvidenceError,match='BOOK_IDENTITY_OR_FRESHNESS'): a.book(target(r))


def test_release_factory_resolves_exact_immediate_predecessor_and_keeps_schedule_optional(release_factory):
    r = release_factory(); q = queue(r)
    # No lease or previously fabricated release pin is handed to the adapter.
    from polymarket_scanner.v11.strategy_admission import SourceLease
    kw = dict(r['admission_kw'],scope=r['scope'],source_leases=(SourceLease('recomputed-model','MODEL',120.),
        SourceLease('received-release','OFFICIAL',120.),SourceLease('release-coverage','FEATURES',120.)))
    f = SourceReleaseRequestFactory(EntryRequestFactory(assembler(r,q,kw),(target(r),)))
    result = evaluate(r,q,SourceReleaseEventAdapter(r['store'],f)); d = r['store'].get(result.result_ids[0])['body']['details']
    assert d['outcome'] == 'REJECT', d['reason']
    release = r['store'].get(d['request']['source_release_id'])['body']['details']['assessment']
    assert release['previous_official_id'] == 'previous-exact' and release['current_official_id'] == 'received-release'
    assert release['schedule'] is None and not release['settlement_finality']


def test_previous_source_uses_receipt_sequence_and_ignores_other_channels(factory):
    r = factory(); s = r['store']; b = s.get('official2')['body']; event = r['context'].event_id
    s.capture('other-provider',event_id=event,kind='OFFICIAL_OBSERVATION',provider='another',source_identity='KATL',
              revision='other',observed_at=r['now'][0],payload=b['payload'],evidence_class='SYNTHETIC')
    s.capture('late-revision',event_id=event,kind='OFFICIAL_OBSERVATION',provider=b['provider'],source_identity=b['source_identity'],
              revision='late',observed_at=b['observed_at']-60,payload=b['payload'],evidence_class='SYNTHETIC')
    assert s.previous_source('late-revision')['id'] == 'official2'
    assert s.previous_source('official0') is None
    with pytest.raises(EvidenceError,match='CAPTURE_KIND_INVALID'): s.previous_source('state2')


def test_pws_factory_resolves_separate_scopes_and_causal_ablation_inputs(joined):
    r = joined; q = queue(r); a = assembler(r,q,r['payout_kw'])
    state_for(r,('book2',),('model2','anchor','pws'))
    f = PWSRequestFactory(EntryRequestFactory(a,(target(r),)),observation_inputs=inputs(r,r['observation_kw']),
        without_pws_sources=(SourceSelector('MODEL','fixture','ablation-model',120.),),lead_policy=r['lead_kw']['policy'])
    result = evaluate(r,q,PWSLeadEventAdapter(r['store'],f)); d = r['store'].get(result.result_ids[0])['body']['details']
    assert d['outcome'] == 'REJECT',d['reason']
    assert not result.proposals and d['executable_exit_value'] is None
    pair = r['store'].get(d['request']['preconfirmation_id'])['body']['details']['assessment']
    assert pair['observation_admission_id'] != pair['payout_admission_id']
    assert pair['observation_bundle_sha256'] != pair['payout_bundle_sha256']


def test_assembled_exit_runs_periodic_census_through_common_account_without_prebuilt_requests(rig,monkeypatch):
    inventory(rig); rt = exit_runtime(rig,monkeypatch); original = rt.census
    a = assembler(rig,rt.queue,provider='exit-census-fixture')
    f = ExitRequestFactory(a,(target(rig,units='1'),),hold_costs=costs(HOLD_RISKS),
                          sale_costs=costs(SALE_RISKS,SALE),reason='NET_SALE_EXCEEDS_HOLD')
    def census(claim,prefix):
        cover = original(claim,prefix)
        state_for(rig,(cover['book_ids'][0],),('model2',*cover['source_ids']),key=prefix+':risk')
        return cover
    adapted = PaperRuntime(rt.coordinator,rt.queue,rt.health,rt.policy,
        evaluator=MultiStrategyEventAdapter(rig['store'],(('exit',PositionExitEventAdapter(rt.coordinator,f)),)),
        census=census,worker_id='worker',generation='assembled-exit')
    d = adapted.tick('assembled-exit')['body']['details']
    assert d['outcome'] == 'TICK_COMPLETED' and len(d['account_batch_ids']) == 1,d
    batch = rig['store'].get(d['account_batch_ids'][0])['body']['details']
    assert len(batch['reserved_intent_ids']) == 1,batch['results']
    state = adapted.coordinator._state(adapted.coordinator._head())
    assert len(state['lots']) == 1 and Decimal(state['lots']['fill-0']['units']) == 2
    assert not state['realized_entries']
    changed = ExitRequestFactory(a,(target(rig,units='2'),),hold_costs=costs(HOLD_RISKS),
                                sale_costs=costs(SALE_RISKS,SALE),reason='NET_SALE_EXCEEDS_HOLD')
    resumed = PaperRuntime(rt.coordinator,rt.queue,rt.health,rt.policy,
        evaluator=MultiStrategyEventAdapter(rig['store'],(('exit',PositionExitEventAdapter(rt.coordinator,changed)),)),
        census=census,worker_id='worker',generation='restarted')
    with pytest.raises(EvidenceError,match='RUNTIME_REPLAY_CONFIG_CONFLICT'): resumed.tick('assembled-exit')


def test_public_mock_candidate_uses_current_relative_value_factory_and_common_account(rig,monkeypatch):
    from polymarket_scanner.v11.book_inputs import PROVIDER
    rt = relative_runtime(rig,monkeypatch); a = assembler(rig,rt.queue,provider=PROVIDER)
    targets = tuple(TargetPlan(l.market_id,l.side,l.units,l.units,l.costs) for l in rig['kw']['legs'])
    f = RelativeValueRequestFactory(a,targets,basket_policy=rig['kw']['policy'])
    class MeasuredRiskFixture:
        config = digest(dict(assembly=f.config,risk='SYNTHETIC_OFF_HOST_FIXTURE'))
        def __call__(self,claim):
            books = tuple(a.book(t)['id'] for t in targets)
            official = rig['store'].latest_source(kind='OFFICIAL_OBSERVATION',event_id=rig['context'].event_id,
                provider='NOAA_AWC',source_identity=rig['context'].station_id)
            state_for(rig,books,('model2',official['id']),key=claim['claim_id']+':risk')
            return f(claim)
    adapted = PaperRuntime(rt.coordinator,rt.queue,rt.health,rt.policy,
        evaluator=MultiStrategyEventAdapter(rig['store'],(('relative',RelativeValueEventAdapter(rig['store'],MeasuredRiskFixture())),)),
        worker_id='worker',generation='assembled-relative')
    run_candidate(rig,adapted)
    state = adapted.coordinator._state(adapted.coordinator._head())
    assert len(state['intents']) == 3 and len(state['baskets']) == 1
    assert Decimal(adapted.coordinator.snapshot()['reserved_cash']) == Decimal('1.2')
    assert not rig['store'].records(kind='TRADE') and not state['financial_authority']


@pytest.mark.parametrize('defect',['stage','route','targets'])
def test_assembly_plan_bounds_cannot_grant_authority_or_invent_a_route(factory,defect):
    r = factory(); q = queue(r)
    with pytest.raises(EvidenceError):
        if defect=='stage': replace(inputs(r),stage='LIVE')
        elif defect=='route':
            bad = replace(inputs(r),binding=replace(r['binding'],rule_fingerprint='f'*64))
            RequestAssembler(q,bad,book_provider='fixture',valuation_policy=r['request'].valuation_policy,lifetime_seconds=10.)
        else: EntryRequestFactory(assembler(r,q),(target(r),)*7)
