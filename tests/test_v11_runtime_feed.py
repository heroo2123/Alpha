from dataclasses import replace

import pytest

from polymarket_scanner.v11.runtime_feed import EvidenceFeed,FeedPolicy,KEY
from polymarket_scanner.v11.evidence import EvidenceError
from test_v11_event_queue import rig,queue,capture


def feed(rig,**changes):return EvidenceFeed(queue(rig),replace(FeedPolicy('fixture'),**changes))


def test_fresh_receipts_route_without_manual_update_lists_and_replay_does_not_redeliver(rig):
    capture(rig,'model');capture(rig,'book','BOOK');f=feed(rig)
    d=f.drain('one')['body']['details'];assert set(f.queue.snapshot()['pending'])=={'e1','e2'}
    assert d['state']['delivered']==2 and d['result']['remaining_sources_may_exist']
    before=f.queue.snapshot();assert f.drain('one')['body']['details']==d
    assert f.queue.snapshot()==before
    assert f.drain('two')['body']['details']['state']['delivered']==2


def test_restart_between_queue_write_and_cursor_save_reuses_delivery_id(rig,monkeypatch):
    capture(rig,'model');f=feed(rig);original=f._save
    def crash(*a,**kw):
        if kw.get('outcome')=='CURSOR_ADVANCED':raise RuntimeError('POWER_LOSS')
        return original(*a,**kw)
    monkeypatch.setattr(f,'_save',crash)
    with pytest.raises(RuntimeError,match='POWER_LOSS'):f.drain('one')
    assert f.queue.snapshot()['metrics']['received']==1
    resumed=feed(rig);d=resumed.drain('one')['body']['details']
    assert d['state']['delivered']==1 and resumed.queue.snapshot()['metrics']['received']==1


def test_raw_historical_pws_and_account_receipts_never_become_market_observations(rig):
    capture(rig,'historical',historical=True)
    capture(rig,'raw','OFFICIAL_OBSERVATION',payload={'source_time_status':'NOT_YET_NORMALIZED','response':[]})
    capture(rig,'unqc','PWS_OBSERVATION',provider='NOAA_MADIS_CWOP')
    capture(rig,'fill','TRADE',payload={'record_type':'PAPER_FILL'})
    f=feed(rig);d=f.drain('one')['body']['details'];assert d['state']['skipped']==4
    assert not f.queue.snapshot()['pending']
    assert len({o['reason'] for o in d['result']['outcomes']})==4
    assert rig['store'].get('raw')['body']['payload']['response']==[]


def schedule(rig,key,due=1002):
    return rig['store'].audit(key,event_id='release',kind='SOURCE_SCHEDULE',details=dict(
        version='alpha_v11_release_schedule_v1',station='KATL',reevaluate_at=due,
        expected_release_at=due+2,valid_until=due+20))


def test_future_release_is_retained_until_due_without_becoming_actual_observation(rig):
    schedule(rig,'release');f=feed(rig)
    d=f.drain('one')['body']['details'];assert 'release' in d['state']['pending_schedules']
    assert not f.queue.snapshot()['pending']
    rig['now'][0]=1002;d=feed(rig).drain('two')['body']['details']
    assert not d['state']['pending_schedules'] and set(f.queue.snapshot()['pending'])=={'e1','e2'}
    assert all(n['kind']=='SCHEDULED_RELEASE' for p in f.queue.snapshot()['pending'].values() for n in p['sources'].values())
    assert not rig['store'].records(kind='OFFICIAL_OBSERVATION')


def test_schedule_capacity_keeps_cursor_before_unretained_work(rig):
    schedule(rig,'one',1005);schedule(rig,'two',1006);f=feed(rig,maximum_schedules=1)
    d=f.drain('one')['body']['details']
    assert list(d['state']['pending_schedules'])==['one'] and d['state']['deferred']>0
    assert d['state']['cursors']['SOURCE_SCHEDULE']==rig['store'].get('one')['seq']
    rig['now'][0]=1005;d=f.drain('two')['body']['details']
    assert list(d['state']['pending_schedules'])==['two']


def test_bounded_rotation_cannot_starve_other_channels(rig):
    for i in range(10):capture(rig,'m'+str(i))
    capture(rig,'book','BOOK');f=feed(rig,maximum_reads=1)
    for i in range(4):d=f.drain(str(i))['body']['details']
    assert d['state']['cursors']['BOOK']==rig['store'].get('book')['seq']
    assert d['state']['cursors']['MODEL']==rig['store'].get('m0')['seq']


def test_changed_feed_policy_never_silently_restarts_existing_cursor(rig):
    capture(rig,'model');feed(rig).drain('one')
    with pytest.raises(EvidenceError,match='CONFIG_CHANGED'):feed(rig,maximum_reads=1).drain('two')


def test_queue_failure_does_not_skip_source_or_erase_other_success(rig,monkeypatch):
    capture(rig,'official','OFFICIAL_OBSERVATION');capture(rig,'model');f=feed(rig);original=f.queue.publish
    def failure(key,**kw):
        if kw['kind']=='MODEL':raise EvidenceError('SYNTHETIC_QUEUE_CONTENTION')
        return original(key,**kw)
    monkeypatch.setattr(f.queue,'publish',failure)
    d=f.drain('one')['body']['details']
    assert d['state']['cursors']['MODEL']==0 and d['state']['cursors']['OFFICIAL_OBSERVATION']>0
    assert d['state']['retry_attempts']>0
    monkeypatch.setattr(f.queue,'publish',original)
    assert f.drain('two')['body']['details']['state']['cursors']['MODEL']>0


@pytest.mark.parametrize('change',[dict(maximum_reads=0),dict(maximum_reads=129),dict(maximum_schedules=65),dict(maximum_seconds=11)])
def test_feed_policy_bounds(change):
    with pytest.raises(EvidenceError,match='POLICY_BOUND'):FeedPolicy('bad',**change)
