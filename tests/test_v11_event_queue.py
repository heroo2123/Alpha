from dataclasses import replace

import pytest

from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore
from polymarket_scanner.v11.event_queue import EventQueue, EventRoute, TriggerPolicy, KINDS, KEY


@pytest.fixture
def rig(tmp_path):
    tmp_path.chmod(0o700)
    now = [1000.]
    store = EvidenceStore(tmp_path/'evidence.sqlite', 'V11_PAPER', clock=lambda:now[0])
    routes = tuple(EventRoute(e, station, '2026-09-24', 'daily_high_temperature', 'a'*64,
                              (e+'-yes', e+'-no'), 2000., ('MODEL',))
                   for e, station in [('e1','KATL'), ('e2','KATL'), ('e3','KSEA')])
    policy = TriggerPolicy('fixture-1', 2, 2, 4, 16, 30., 10., 100_000, 60.,
                           tuple((k,60.) for k in sorted(KINDS)), (('KATL',30.), ('KSEA',30.)))
    return dict(store=store, now=now, routes=routes, policy=policy)


def queue(rig, **policy):
    return EventQueue(rig['store'], routes=rig['routes'], policy=replace(rig['policy'], **policy))


def capture(rig, key, kind='MODEL', *, event='e1', station='KATL', observed=None, revision=None,
            payload=None, provider=None, source_identity=None, historical=False):
    data = dict(station=station, target_date='2026-09-24', family='daily_high_temperature')
    if kind in {'BOOK','TRADE'}:
        data.update(token_id=event+'-yes', stream_healthy=True, snapshot_type='FULL',
                    rule_fingerprint='a'*64, bids=[{'price':'.1', 'size':'10'}], asks=[{'price':'.2', 'size':'10'}])
    if kind == 'PWS_OBSERVATION':
        data.update(as_of=rig['now'][0], health='HEALTHY', observation_age_seconds=[5.,10.])
    data.update(payload or {})
    return rig['store'].capture(key, event_id=event, kind=kind, provider=provider or ('ALPHA_PWS_QC' if kind=='PWS_OBSERVATION' else 'fixture'),
                  source_identity=source_identity or (data['token_id'] if kind in {'BOOK','TRADE'} else station+':'+kind),
                  revision=revision or key, observed_at=rig['now'][0] if observed is None else observed,
                  issued_at=(rig['now'][0] if observed is None else observed) if kind == 'MODEL' else None,
                  payload=data, evidence_class='HISTORICAL_AVAILABILITY_UNKNOWN' if historical else 'SYNTHETIC')


def publish(q, rig, key, kind='MODEL', **kw):
    capture(rig,key,kind,**kw)
    return q.publish('route:'+key,kind=kind,evidence_id=key)['body']['details']['result']


def result(rig, key='result', event='e1'):
    return rig['store'].audit(key, event_id=event, kind='MEASUREMENT',
                             details={'outcome':'GATED', 'reason':'SYNTHETIC_RESEARCH_RESULT'})


def test_routes_station_updates_only_to_bounded_affected_events_and_exact_book_token(rig):
    q=queue(rig)
    assert publish(q,rig,'m')['affected_events']==['e1','e2']
    assert publish(q,rig,'book','BOOK')['affected_events']==['e1']
    assert set(q.snapshot()['pending'])=={'e1','e2'}
    assert q.snapshot()['metrics']['coalesced']==1
    assert publish(q,rig,'unmapped','BOOK',payload={'token_id':'not-watched'})['reason']=='NO_AFFECTED_REGISTERED_EVENT'


def test_forecast_for_another_target_date_does_not_fan_out_to_all_station_dates(rig):
    assert publish(queue(rig),rig,'other-date',payload={'target_date':'2026-09-25'})['reason']=='NO_AFFECTED_REGISTERED_EVENT'


def test_station_level_duplicate_and_older_receipt_accounting_survive_restart(rig):
    q=queue(rig)
    publish(q,rig,'first',revision='same')
    capture(rig,'middle',revision='changed')
    assert publish(q,rig,'duplicate',revision='same')['reason']=='IDENTICAL_SOURCE_UPDATE'
    q=queue(rig)
    assert q.publish('late-middle',kind='MODEL',evidence_id='middle')['body']['details']['result']['reason']=='SOURCE_RECEIPT_ALREADY_PROCESSED'
    assert q.snapshot()['metrics']['duplicate']==1 and q.snapshot()['metrics']['out_of_order']==1


def test_new_receipt_of_older_official_observation_is_a_revision_trigger(rig):
    q=queue(rig)
    publish(q,rig,'newer-sensor','OFFICIAL_OBSERVATION')
    rig['now'][0]+=1
    assert publish(q,rig,'correction','OFFICIAL_OBSERVATION',observed=990.)['outcome']=='QUEUED'
    assert all(list(x['sources'].values())[0]['evidence_id']=='correction' for x in q.snapshot()['pending'].values())


def test_unknown_historical_availability_is_not_a_current_trigger(rig):
    q=queue(rig); capture(rig,'historical',historical=True)
    with pytest.raises(EvidenceError,match='SOURCE_KIND_OR_AVAILABILITY'):
        q.publish('route',kind='MODEL',evidence_id='historical')


def test_pws_requires_qc_actual_sensor_age_and_station_specific_ttl(rig):
    q=queue(rig)
    assert publish(q,rig,'pws','PWS_OBSERVATION')['outcome']=='QUEUED'
    assert publish(q,rig,'old-sensors','PWS_OBSERVATION',payload={'observation_age_seconds':[31.]})['reason']=='PWS_FRESH_QC_REQUIRED'
    assert q.snapshot()['needs_census']['e1']=='PWS_FRESH_QC_REQUIRED'
    assert publish(q,rig,'raw','PWS_OBSERVATION',provider='MADIS_RAW')['reason']=='PWS_FRESH_QC_REQUIRED'


def test_unconfigured_pws_station_never_uses_an_implicit_ttl(rig):
    q=queue(rig,pws_station_age_seconds=(('KATL',30.),))
    capture(rig,'pws','PWS_OBSERVATION',station='KSEA',event='e3')
    with pytest.raises(EvidenceError,match='TTL_UNCONFIGURED'):
        q.publish('route',kind='PWS_OBSERVATION',evidence_id='pws')


def test_overflow_is_durable_and_does_not_evict_existing_event_silently(rig):
    q=queue(rig,max_pending_events=1)
    outcome=publish(q,rig,'m')
    assert outcome['affected_events']==['e1']
    s=q.snapshot()
    assert s['metrics']['overflow']==1 and s['needs_census']['e2']=='EVENT_QUEUE_OVERFLOW'
    assert list(s['pending'])==['e1']


def test_excess_station_fanout_is_suppressed_as_a_whole_and_counted(rig):
    q=queue(rig,max_station_fanout=1)
    assert publish(q,rig,'m')['reason']=='STATION_FANOUT_BOUND_REQUIRES_CENSUS'
    assert not q.snapshot()['pending'] and set(q.snapshot()['needs_census'])=={'e1','e2'}


def test_source_fanin_and_channel_bounds_do_not_grow_without_limit(rig):
    q=queue(rig,max_sources_per_event=1,max_source_channels=2)
    publish(q,rig,'m')
    publish(q,rig,'book','BOOK')
    assert q.snapshot()['needs_census']['e1']=='EVENT_SOURCE_FANIN_OVERFLOW'
    assert publish(q,rig,'trade','TRADE')['reason']=='SOURCE_CHANNEL_BOUND_REQUIRES_CENSUS'
    assert len(q.snapshot()['channels'])==2


def test_pending_age_is_not_extended_indefinitely_by_frequent_updates(rig):
    q=queue(rig)
    publish(q,rig,'m')
    rig['now'][0]+=20
    publish(q,rig,'m2')
    assert q.snapshot()['pending']['e1']['expires_at']==1030.
    rig['now'][0]+=11
    with q.work('work') as claim:
        assert claim['requires_full_census'] and not claim['sources']
    assert q.snapshot()['metrics']['expired']==2


def test_expired_route_is_retained_as_a_finding_without_an_evaluation_loop(rig):
    q=queue(rig); publish(q,rig,'m'); rig['now'][0]=2001.
    with q.work('work') as claim:
        assert claim is None
    assert q.snapshot()['needs_census'] and q.snapshot()['active'] is None


def test_one_real_worker_lock_serializes_even_separate_queue_instances(rig):
    q=queue(rig); publish(q,rig,'m')
    with q.work('work1') as claim:
        with pytest.raises(EvidenceError,match='WORKER_ALREADY_RUNNING'):
            with queue(rig).work('work2'):
                pass
        with pytest.raises(EvidenceError,match='WORKER_ALREADY_RUNNING'):
            with q.work('nested'):
                pass
        result(rig)
        finished=q.finish('finish',claim_id=claim['claim_id'],result_ids=('result',))
        assert finished['body']['details']['result']['outcome']=='RESEARCH_EVALUATED'
        assert finished['body']['details']['result']['submission_ready_latency'] is None
    assert q.snapshot()['metrics']['completed']==1


def test_finishing_without_lock_or_with_foreign_result_is_refused(rig):
    q=queue(rig); publish(q,rig,'m'); result(rig,'old')
    with pytest.raises(EvidenceError,match='HELD_WORKER_LOCK'):
        q.finish('finish',claim_id='work',result_ids=('old',))
    with q.work('work'):
        result(rig,'foreign',event='e3')
        with pytest.raises(EvidenceError,match='CLAIMED_EVALUATION'):
            q.finish('finish',claim_id='work',result_ids=('foreign',))


def test_old_result_at_same_timestamp_cannot_fake_post_claim_work(rig):
    q=queue(rig); publish(q,rig,'m'); result(rig,'old')
    with q.work('work'):
        with pytest.raises(EvidenceError,match='CLAIMED_EVALUATION'):
            q.finish('finish',claim_id='work',result_ids=('old',))


def test_late_source_arrival_retains_followup_and_invalidates_work_result(rig):
    q=queue(rig); publish(q,rig,'m')
    with q.work('work'):
        publish(q,rig,'updated')
        result(rig)
        d=q.finish('finish',claim_id='work',result_ids=('result',))['body']['details']
        assert d['result']['reason']=='NEW_SOURCE_UPDATE_REQUIRES_REEVALUATION'
        assert 'e1' in d['state']['pending']


def test_exception_leaves_durable_claim_then_lock_safe_restart_requires_census(rig):
    q=queue(rig); publish(q,rig,'m')
    with pytest.raises(RuntimeError):
        with q.work('work'):
            raise RuntimeError('SIMULATED_CRASH')
    assert q.snapshot()['active']['claim_id']=='work'
    q=queue(rig)
    with q.work('restart') as claim:
        assert q.snapshot()['metrics']['abandoned']==1
        assert q.snapshot()['needs_census']['e1']=='ABANDONED_EVALUATION_REQUIRES_CENSUS'
        result(rig,event=claim['event_id'])
        q.finish('finish',claim_id='restart',result_ids=('result',))


def test_result_after_work_deadline_remains_noncurrent(rig):
    q=queue(rig); publish(q,rig,'m')
    with q.work('work'):
        rig['now'][0]+=11; result(rig)
        d=q.finish('finish',claim_id='work',result_ids=('result',))['body']['details']
        assert d['result']['reason']=='WORK_BUDGET_EXCEEDED_OR_CLOCK_REGRESSED'
        assert d['state']['needs_census']['e1']


def census(rig,q,*,claim_id='work',missing_book=False,stale=False):
    keys=[]
    for i, token in enumerate(rig['routes'][0].tokens[:1] if missing_book else rig['routes'][0].tokens):
        key='snapshot'+str(i); keys.append(key)
        capture(rig,key,'BOOK',payload={'token_id':token},observed=900. if stale else rig['now'][0])
    capture(rig,'fresh-source',observed=900. if stale else rig['now'][0])
    rig['store'].audit('rules',event_id='e1',kind='RULE_STATE',details={'fingerprint':'a'*64,'quarantined':False})
    return q.complete_census('census',claim_id=claim_id,book_ids=tuple(keys),source_ids=('fresh-source',),rule_state_id='rules')


def test_stream_gap_does_not_clear_on_reconnect_until_fresh_full_census(rig):
    q=queue(rig); q.stream_gap('gap',event_id='e1',reason='DISCONNECT')
    assert queue(rig).snapshot()['needs_census']['e1']=='STREAM_GAP:DISCONNECT'
    with q.work('work') as claim:
        assert claim['requires_full_census']
        census(rig,q)
        assert not q.snapshot()['needs_census']
        result(rig)
        assert q.finish('finish',claim_id='work',result_ids=('result',))['body']['details']['result']['outcome']=='RESEARCH_EVALUATED'


@pytest.mark.parametrize('change,error',[({'missing_book':True},'ALL_EVENT_TOKENS'),({'stale':True},'NEW_FULL_BOOK')])
def test_incomplete_or_stale_resync_cannot_clear_gap(rig,change,error):
    q=queue(rig); q.stream_gap('gap',event_id='e1',reason='DISCONNECT')
    with q.work('work'):
        with pytest.raises(EvidenceError,match=error):
            census(rig,q,**change)
    assert q.snapshot()['needs_census']


def test_newer_gap_racing_census_commit_is_not_cleared(rig,monkeypatch):
    q=queue(rig); q.stream_gap('gap',event_id='e1',reason='DISCONNECT')
    with q.work('work'):
        original=rig['store'].audit
        def race(record_id,**kw):
            if record_id=='census':
                monkeypatch.setattr(rig['store'],'audit',original)
                q.stream_gap('second-gap',event_id='e1',reason='ANOTHER_DISCONNECT')
            return original(record_id,**kw)
        monkeypatch.setattr(rig['store'],'audit',race)
        with pytest.raises(EvidenceError,match='STATE_CHANGED'):
            census(rig,q)
    assert q.snapshot()['needs_census']['e1']=='STREAM_GAP:ANOTHER_DISCONNECT'


def test_census_cannot_replace_required_model_with_an_unrelated_source_kind(rig):
    q=queue(rig); q.stream_gap('gap',event_id='e1',reason='DISCONNECT')
    with q.work('work'):
        capture(rig,'s1','BOOK'); capture(rig,'s2','BOOK',payload={'token_id':'e1-no'})
        capture(rig,'official','OFFICIAL_OBSERVATION')
        rig['store'].audit('rules',event_id='e1',kind='RULE_STATE',details={'fingerprint':'a'*64,'quarantined':False})
        with pytest.raises(EvidenceError,match='SOURCE_COVERAGE_MISSING'):
            q.complete_census('census',claim_id='work',book_ids=('s1','s2'),source_ids=('official',),rule_state_id='rules')


def test_scheduled_release_triggers_caution_work_without_claiming_observation_arrival(rig):
    q=queue(rig)
    rig['store'].audit('schedule',event_id='station:KATL',kind='SOURCE_SCHEDULE',details={
        'version':'alpha_v11_release_schedule_v1','station':'KATL','reevaluate_at':1010.,'expected_release_at':1020.,'valid_until':1040.})
    with pytest.raises(EvidenceError,match='NOT_DUE'):
        q.publish('route',kind='SCHEDULED_RELEASE',evidence_id='schedule')
    rig['now'][0]=1010.
    q.publish('route',kind='SCHEDULED_RELEASE',evidence_id='schedule')
    with q.work('work') as claim:
        assert claim['sources'][0]['kind']=='SCHEDULED_RELEASE'
        assert rig['store'].get('schedule')['kind']=='SOURCE_SCHEDULE'


def test_policy_change_and_command_collision_never_silently_discard_pending_work(rig):
    q=queue(rig); publish(q,rig,'m')
    with pytest.raises(EvidenceError,match='CONFIGURATION_CHANGED'):
        queue(rig,max_pending_events=3).snapshot()
    with pytest.raises(EvidenceError,match='COMMAND_ID_COLLISION'):
        q.stream_gap('route:m',event_id='e1',reason='OTHER')
    assert len(q.snapshot()['pending'])==2


def test_concurrent_enqueue_cas_preserves_first_writer(rig,monkeypatch):
    q=queue(rig); capture(rig,'one'); capture(rig,'two')
    original=rig['store'].audit
    def race(record_id,**kw):
        if record_id=='one-op':
            monkeypatch.setattr(rig['store'],'audit',original)
            q.publish('two-op',kind='MODEL',evidence_id='two')
        return original(record_id,**kw)
    monkeypatch.setattr(rig['store'],'audit',race)
    with pytest.raises(EvidenceError,match='STATE_CHANGED'):
        q.publish('one-op',kind='MODEL',evidence_id='one')
    assert q.snapshot()['metrics']['received']==1
    assert all(next(iter(p['sources'].values()))['evidence_id']=='two' for p in q.snapshot()['pending'].values())


def test_state_byte_bound_fails_without_partial_queue_mutation(rig):
    q=queue(rig,max_state_bytes=100)
    with pytest.raises(EvidenceError,match='STATE_BYTES_BOUND'):
        publish(q,rig,'m')
    assert rig['store'].latest(kind='RUNTIME_STATUS',event_id=KEY) is None


def test_scheduled_plan_can_be_recorded_well_before_due_without_becoming_stale_observation(rig):
    q=queue(rig)
    rig['store'].audit('schedule',event_id='station:KATL',kind='SOURCE_SCHEDULE',details={
        'version':'alpha_v11_release_schedule_v1','station':'KATL','reevaluate_at':1500.,'expected_release_at':1510.,'valid_until':1540.})
    rig['now'][0]=1500.
    assert q.publish('route',kind='SCHEDULED_RELEASE',evidence_id='schedule')['body']['details']['result']['outcome']=='QUEUED'
    with q.work('work') as claim:
        assert claim['source_to_work_seconds']==0.
    assert rig['store'].get('schedule')['body']['recorded_at']==1000.


def test_archived_source_arrival_without_queue_notification_stales_work_result(rig):
    q=queue(rig); publish(q,rig,'m')
    with q.work('work'):
        capture(rig,'unnotified')
        result(rig)
        done=q.finish('finish',claim_id='work',result_ids=('result',))
        assert done['body']['details']['result']['reason']=='ARCHIVED_SOURCE_CHANGED_DURING_EVALUATION'


def test_source_arrival_racing_final_commit_is_guarded_atomically(rig,monkeypatch):
    q=queue(rig); publish(q,rig,'m')
    with q.work('work'):
        result(rig)
        original=rig['store'].audit
        def race(record_id,**kw):
            if record_id=='finish':
                capture(rig,'racing-source')
            return original(record_id,**kw)
        monkeypatch.setattr(rig['store'],'audit',race)
        with pytest.raises(EvidenceError,match='AUDIT_GUARDED_STATE_CHANGED'):
            q.finish('finish',claim_id='work',result_ids=('result',))
        assert q.snapshot()['active']['claim_id']=='work'


def test_source_arrival_racing_census_commit_cannot_clear_gap(rig,monkeypatch):
    q=queue(rig); q.stream_gap('gap',event_id='e1',reason='DISCONNECT')
    with q.work('work'):
        original=rig['store'].audit
        def race(record_id,**kw):
            if record_id=='census':
                capture(rig,'racing-source')
            return original(record_id,**kw)
        monkeypatch.setattr(rig['store'],'audit',race)
        with pytest.raises(EvidenceError,match='AUDIT_GUARDED_STATE_CHANGED'):
            census(rig,q)
        assert q.snapshot()['needs_census']['e1']=='STREAM_GAP:DISCONNECT'


def test_result_must_be_computed_after_census_not_merely_after_claim(rig):
    q=queue(rig); q.stream_gap('gap',event_id='e1',reason='DISCONNECT')
    with q.work('work'):
        result(rig,'pre-census')
        census(rig,q)
        with pytest.raises(EvidenceError,match='CLAIMED_EVALUATION'):
            q.finish('finish',claim_id='work',result_ids=('pre-census',))
        result(rig,'post-census')
        assert q.finish('finish',claim_id='work',result_ids=('post-census',))['body']['details']['result']['outcome']=='RESEARCH_EVALUATED'


def test_repeated_identical_gap_during_claim_has_a_new_loss_generation(rig):
    q=queue(rig);q.stream_gap('before',event_id='e1',reason='DISCONNECT')
    with q.work('work'):
        q.stream_gap('during',event_id='e1',reason='DISCONNECT')
        with pytest.raises(EvidenceError,match='LOSS_AFTER_CLAIM'):
            census(rig,q)
    assert q.snapshot()['needs_census']['e1']=='STREAM_GAP:DISCONNECT'


def test_gap_on_unrelated_event_during_claim_does_not_invalidate_healthy_event_census(rig):
    q=queue(rig);q.stream_gap('before',event_id='e1',reason='DISCONNECT')
    with q.work('work'):
        q.stream_gap('during',event_id='e3',reason='DISCONNECT')
        census(rig,q)
    assert 'e1' not in q.snapshot()['needs_census'] and 'e3' in q.snapshot()['needs_census']
