"""Synthetic label-prefix recovery and dataset joins, never label certification."""
from copy import deepcopy

import pytest

from polymarket_scanner.v11 import pws_scoring, target_learning
from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.learning_sources import LearningSourceView, learning_source_view
from polymarket_scanner.v11.pws_lead import PWSObservationLead
from test_v11_certification_rules import setup
from test_v11_physical_inference import rig as physical_rig
from test_v11_pws_lead import rig, observe, report
from test_v11_pws_lead import rig as lead_rig
from test_v11_target_learning import observation_capture, observation_label


def score(r, key='score'):
    return PWSObservationLead(r['store']).score_first_received_report(key, observation_id='lead')


def replay(r, key='score'):
    with learning_source_view(r['store']) as source:
        return pws_scoring.replay_first_received_report(source, key)


@pytest.mark.parametrize('kind', ['ordinary', 'correction', 'proxy', 'unknown', 'late', 'negative'])
def test_original_receipt_selection_and_numerics_replay_without_writes(rig, monkeypatch, kind):
    r=rig; observe(r); r['now'][0]+=10
    if kind=='correction':
        report(r, 'correction', observed=r['store'].get('official')['body']['observed_at'])
        report(r, 'next', value=72)
    elif kind=='proxy': report(r, source_role='METAR_PROXY')
    elif kind=='late':
        r['now'][0]+=120; report(r)
    elif kind=='unknown':
        row=r['store'].get('official'); b=row['body']
        r['store'].capture('unknown', event_id=row['event_id'], kind=row['kind'], provider=b['provider'],
            source_identity=b['source_identity'], revision='unknown', payload=b['payload'],
            evidence_class='HISTORICAL_AVAILABILITY_UNKNOWN', observed_at=r['now'][0])
    else: report(r, value=69 if kind=='negative' else 72)
    original=score(r); before=r['store'].pin_read_view()
    monkeypatch.setattr(r['store'], 'audit', lambda *a,**kw:pytest.fail('replay wrote evidence'))
    proof=replay(r)
    assert proof['status']=='SCORE_REPRODUCED' and proof['score_match']
    assert proof['original_score_sha256']==proof['recomputed_score_sha256']
    assert not proof['financial_authority'] and not proof['original_prediction_recomputed']
    assert r['store'].pin_read_view()==before
    d=original['body']['details']
    if kind in {'proxy','unknown','late'}: assert d['status']=='UNKNOWN'
    if kind=='negative': assert d['brier_improvement']<0
    if kind=='correction':
        assert d['first_official_update']=='correction' and d['official_id']=='next'
        assert 'correction' in {ref['id'] for ref in original['body']['evidence']}


def test_completed_unknown_is_stable_and_new_score_id_can_observe_new_receipt(rig, monkeypatch):
    r=rig; observe(r); original=score(r)
    report(r); before=r['store'].pin_read_view()
    assert score(r)==original and r['store'].pin_read_view()==before
    assert replay(r)['score_match']
    assert score(r, 'new-score')['body']['details']['status']=='MEASURED_FIRST_RECEIVED_REPORT'
    with pytest.raises(EvidenceError, match='REQUEST_ID_COLLISION'):
        PWSObservationLead(r['store']).score_first_received_report('score', observation_id='different')


def test_start_survives_interruption_and_same_clock_report_arrival(rig, monkeypatch):
    r=rig; observe(r); original=r['store'].audit
    def fail(key, **kw):
        if key=='score': raise OSError('after start')
        return original(key, **kw)
    with monkeypatch.context() as patch:
        patch.setattr(r['store'], 'audit', fail)
        with pytest.raises(OSError, match='after start'): score(r)
    start=r['store'].get('score:start'); report(r)
    recovered=score(r)['body']['details']
    assert recovered['status']=='UNKNOWN' and recovered['scan_through_seq']==start['seq']-1
    assert recovered['scanned_report_count']==0 and replay(r)['score_match']


@pytest.mark.parametrize('missing', ['score:start', 'lead', 'correction', 'next'])
def test_missing_original_receipt_or_pin_never_gains_replay_credit(rig, monkeypatch, missing):
    r=rig; observe(r); r['now'][0]+=1
    report(r, 'correction', observed=r['store'].get('official')['body']['observed_at']); report(r)
    score(r); get=LearningSourceView.get
    def absent(self, key):
        if key==missing: raise EvidenceError('SYNTHETIC_ORIGINAL_MISSING')
        return get(self,key)
    monkeypatch.setattr(LearningSourceView, 'get', absent)
    proof=replay(r)
    assert proof['status']=='GATED' and not proof['score_match'] and not proof['comparisons']
    assert 'recomputed_score_sha256' not in proof


@pytest.mark.parametrize('field', ['scoring_as_of','scan_through_seq','scanned_report_refs_sha256','with_pws'])
def test_original_score_field_mismatch_is_detected(rig, monkeypatch, field):
    r=rig; observe(r); report(r); score(r); get=LearningSourceView.get
    def altered(self, key):
        row=get(self,key)
        if key=='score':
            row=deepcopy(row); d=row['body']['details']
            d[field]='f'*64 if field=='scanned_report_refs_sha256' else ({} if field=='with_pws' else d[field]+1)
        return row
    monkeypatch.setattr(LearningSourceView, 'get', altered)
    proof=replay(r)
    assert proof['status']=='MISMATCH' and not proof['score_match']


@pytest.mark.parametrize('field', ['request_sha256','request','evidence'])
def test_tampered_start_binding_gates(rig, monkeypatch, field):
    r=rig; observe(r); report(r); score(r); get=LearningSourceView.get
    def altered(self,key):
        row=get(self,key)
        if key=='score:start':
            row=deepcopy(row)
            if field=='evidence': row['body']['evidence']=[]
            elif field=='request': row['body']['details']['request']['observation_ref']['sha256']='f'*64
            else: row['body']['details']['request_sha256']='f'*64
        return row
    monkeypatch.setattr(LearningSourceView,'get',altered)
    proof=replay(r)
    assert proof['status']=='GATED' and not proof['score_match']


def test_legacy_score_is_retained_but_lacks_replay_pin(rig):
    r=rig; observe(r); report(r); d=deepcopy(score(r)['body']['details'])
    for key in ('score_protocol','start_ref','observation_ref','scoring_as_of','scan_through_seq',
                'scanned_report_count','scanned_report_refs_sha256'): d.pop(key)
    old=r['store'].audit('legacy',event_id=r['rule'].payload['event_id'],kind='MEASUREMENT',details=d,
        evidence_ids=('lead','next'))
    assert score(r,'legacy')==old
    proof=replay(r,'legacy')
    assert proof['status']=='GATED' and proof['reason']=='LEAD_SCORE_PIN_REQUIRED_LEGACY_UNSUPPORTED'


def test_score_computation_regression_is_mismatch(rig, monkeypatch):
    r=rig; observe(r); report(r); score(r); original=pws_scoring.first_received_report_details
    def changed(*args, **kw):
        d,refs=original(*args,**kw); d['brier_improvement']=999.; return d,refs
    monkeypatch.setattr(pws_scoring,'first_received_report_details',changed)
    proof=replay(r)
    assert proof['status']=='MISMATCH' and not proof['score_match']


@pytest.mark.parametrize('fault', ['oversized','overflow','duplicate','mass','target'])
def test_malformed_original_distribution_is_bounded_and_gated(rig, monkeypatch, fault):
    r=rig; observe(r); report(r); score(r); get=LearningSourceView.get
    def altered(self,key):
        row=get(self,key)
        if key=='lead':
            row=deepcopy(row); prediction=row['body']['details']['with_pws']; vector=prediction['buckets']
            if fault=='oversized': prediction['buckets']=vector*100
            elif fault=='overflow': vector[0]['point']=1e308
            elif fault=='duplicate': vector[0]['market_id']=vector[1]['market_id']
            elif fault=='mass':
                for b in vector: b['point']=0.
            else: prediction['target']='FINAL_DAILY_EXTREME'
        return row
    monkeypatch.setattr(LearningSourceView,'get',altered)
    proof=replay(r)
    assert proof['status']=='GATED' and not proof['score_match']
    assert proof['reason']=='LEAD_SCORE_ORIGINAL_DISTRIBUTION_REQUIRED'


def test_many_later_receipts_do_not_replace_original_scan_or_overflow_it(rig):
    r=rig; observe(r); original=score(r); proof=replay(r)
    for i in range(1000): report(r,'later-'+str(i))
    assert score(r)==original and replay(r)==proof
    with pytest.raises(EvidenceError,match='LEAD_LABEL_SCAN_BOUND'): score(r,'overflow')


def test_zero_winner_probability_retains_infinite_log_loss_flag(rig):
    r=rig; observe(r); row=r['store'].get('lead'); d=deepcopy(row['body']['details'])
    # An explicit synthetic extreme vector checks scoring math, not inference.
    for prediction in (d['with_pws'],d['without_pws']):
        for i,b in enumerate(prediction['buckets']): b['point']=1. if i==0 else 0.
    r['store'].audit('extreme-lead',event_id=row['event_id'],kind='MEASUREMENT',details=d)
    report(r,value=72)
    result=PWSObservationLead(r['store']).score_first_received_report('extreme-score',observation_id='extreme-lead')
    measured=result['body']['details']
    assert measured['with_pws']['log_loss_infinite'] and measured['with_pws']['log_loss'] is None
    assert measured['log_loss_improvement'] is None and replay(r,'extreme-score')['score_match']


def test_final_deadline_failure_discards_completed_score_match(rig, monkeypatch):
    r=rig; observe(r); report(r); score(r); old=pws_scoring.canonical
    elapsed=[0.]
    def spent(value):
        if isinstance(value,dict) and value.get('status')=='SCORE_REPRODUCED': elapsed[0]=2.
        return old(value)
    monkeypatch.setattr(pws_scoring,'canonical',spent)
    with learning_source_view(r['store'],deadline=1.,monotonic=lambda:elapsed[0]) as source:
        proof=pws_scoring.replay_first_received_report(source,'score')
    assert proof['status']=='GATED' and not proof['score_match'] and not proof['comparisons']
    assert 'recomputed_score_sha256' not in proof


def test_dataset_requires_numerical_score_replay_with_original_label_cutoff(physical_rig, setup, monkeypatch):
    r=physical_rig; pair,context=observation_capture(r,setup); label=observation_label(r,pair,context)
    kw=dict(label_ids=label['id'],city=context.city_id,horizon='NEXT_120S',season='AUTUMN')
    before=r['store'].pin_read_view()
    examples=target_learning.labeled_target_examples(r['store'],pair['id'],**kw)
    assert len(examples)==2 and r['store'].pin_read_view()==before
    provenance=examples[0].payload['label_receipt_provenance']
    assert provenance==examples[1].payload['label_receipt_provenance']
    assert provenance['scoring_as_of']>examples[0].payload['prediction_at']
    assert provenance['score_replay_sha256'] and not provenance['collection_continuity_verified']
    original=pws_scoring.first_received_report_details
    def changed(*args,**kw):
        d,refs=original(*args,**kw); d['brier_improvement']=999.; return d,refs
    monkeypatch.setattr(pws_scoring,'first_received_report_details',changed)
    with pytest.raises(EvidenceError,match='RECEIPT_SCORE_REPLAY_REQUIRED'):
        target_learning.labeled_target_examples(r['store'],pair['id'],**kw)


def test_legacy_score_cannot_enter_observation_dataset(physical_rig, setup):
    r=physical_rig; pair,context=observation_capture(r,setup); label=observation_label(r,pair,context)
    original=r['store'].get('receipt-score'); d=deepcopy(original['body']['details']); d.pop('score_protocol')
    legacy=r['store'].audit('legacy',event_id=original['event_id'],kind='MEASUREMENT',details=d,
        evidence_ids=tuple(ref['id'] for ref in original['body']['evidence']))
    payload=deepcopy(label['body']['payload'])
    payload.update(receipt_score_id=legacy['id'],receipt_score_sha256=legacy['sha256'])
    later=r['store'].capture('legacy-label',event_id=label['event_id'],kind='LABEL',provider='TEST_ONLY',
        source_identity='legacy-next',revision='1',payload=payload,evidence_class='SYNTHETIC')
    with pytest.raises(EvidenceError,match='RECEIPT_SCORE_REPLAY_REQUIRED:LEAD_SCORE_PIN_REQUIRED_LEGACY_UNSUPPORTED'):
        target_learning.labeled_target_examples(r['store'],pair['id'],label_ids=later['id'],
            city=context.city_id,horizon='NEXT_120S',season='AUTUMN')
