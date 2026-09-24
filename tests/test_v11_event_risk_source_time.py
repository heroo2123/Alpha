from dataclasses import replace

import pytest

from polymarket_scanner.v11.event_risk import EventRiskEngine
from polymarket_scanner.v11.evidence import EvidenceError
from test_v11_event_risk import rig, captures, policy, metrics, CONTEXT, BINDING


def qc_step(store, now, suffix, *, as_of=None, ages=(2.,3.), provider='ALPHA_PWS_QC', health='HEALTHY'):
    books, sources = captures(store,now,suffix)
    row = store.capture('qc'+suffix,event_id='event',kind='PWS_OBSERVATION',provider=provider,
        source_identity='station',revision=suffix,evidence_class='SYNTHETIC',payload=dict(
        station='station',health=health,as_of=now[0] if as_of is None else as_of,observation_age_seconds=list(ages)))
    return EventRiskEngine(store).step('qc-risk'+suffix,context=CONTEXT,policy=policy(),binding=BINDING,
        metrics=metrics(now[0]),book_ids=books,source_ids=(*sources,row['id']))['body']['details']


def test_qc_uses_oldest_sensor_time_without_rewriting_raw_observed_at(rig):
    store, now = rig; d = qc_step(store,now,'first')
    assert store.get('qcfirst')['body']['observed_at'] is None
    assert d['state'] == 'RECOVERY' and 'SOURCE_STALE_OR_UNKNOWN' not in d['reasons']
    assert min(d['stability']['watermarks'].values()) == now[0]-3
    assert d['valid_until'] <= now[0]-3+policy().max_source_age_seconds


@pytest.mark.parametrize('changes', [dict(ages=(1.,121.)),dict(ages=()),dict(ages=(-1.,)),
    dict(as_of=1001.),dict(provider='RAW_PWS'),dict(health='DEGRADED'),dict(ages=(True,))])
def test_bad_or_stale_qc_never_becomes_fresh_event_evidence(rig,changes):
    store, now = rig; d = qc_step(store,now,'bad',**changes)
    assert d['state'] == 'EVENT' and 'SOURCE_STALE_OR_UNKNOWN' in d['reasons']
    assert not d['ordinary_new_risk_research_allowed']


def test_recomputed_qc_does_not_advance_unchanged_sensor_recovery(rig):
    store, now = rig; first = qc_step(store,now,'first')
    now[0] += 10
    second = qc_step(store,now,'again',ages=(12.,13.))
    assert second['stability']['count'] == first['stability']['count'] == 1
    assert second['state'] == 'RECOVERY'


@pytest.mark.parametrize('issued,expected', [(990.,'RECOVERY'),(None,'EVENT'),(800.,'EVENT'),(1001.,'EVENT')])
def test_model_freshness_uses_issue_time_even_if_receipt_or_observed_time_is_new(rig,issued,expected):
    store, now = rig; books, sources = captures(store,now,'model')
    if issued is not None and issued > now[0]:
        with pytest.raises(EvidenceError,match='SOURCE_TIME_IN_FUTURE'):
            store.capture('model',event_id='event',kind='MODEL',provider='fixture',source_identity='model',
                revision='one',issued_at=issued,observed_at=now[0],payload={},evidence_class='SYNTHETIC')
        return
    row = store.capture('model',event_id='event',kind='MODEL',provider='fixture',source_identity='model',
        revision='one',issued_at=issued,observed_at=now[0],payload={},evidence_class='SYNTHETIC')
    result = EventRiskEngine(store).step('model-risk',context=CONTEXT,policy=policy(),binding=BINDING,
        metrics=metrics(now[0]),book_ids=books,source_ids=(*sources,row['id']))['body']['details']
    assert result['state'] == expected
    if expected == 'EVENT': assert 'SOURCE_STALE_OR_UNKNOWN' in result['reasons']
