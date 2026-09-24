"""Read-only learning assembly and original normalized-source lineage."""
import sqlite3

import pytest

from polymarket_scanner.v11.datasets import DatasetPlan, build_dataset
from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore, ReleaseBinding, digest
from polymarket_scanner.v11.forecast_features import ForecastFeatureContract, build_initial_forecast_bundle
from polymarket_scanner.v11.forecast_learning import run_forecast_fit
from polymarket_scanner.v11.learning_capture import capture_forecast_vector, labeled_examples
from polymarket_scanner.v11.learning_sources import learning_source_view, source_derivation
from polymarket_scanner.v11.model_artifacts import ArtifactStore, predict_with_bundle
from polymarket_scanner.v11.probability import FINAL_EXTREME
from polymarket_scanner.v11.strategy_pipeline import _model_inputs
from test_v11_forecast_learning import setup_job
from test_v11_gefs_sources import gefs, all_fields, source
from test_v11_model_artifacts import make_artifacts, provenance


def small_archive(tmp_path):
    tmp_path.chmod(0o700);now=[1.]
    store=EvidenceStore(tmp_path/'sources.sqlite','V11_PAPER',clock=lambda:now[0])
    raw=store.capture('raw',event_id='event',kind='MODEL',provider='fixture',source_identity='raw',revision='1',
        payload={'response':'original'},evidence_class='SYNTHETIC')
    now[0]=2.
    model=store.capture('model',event_id='event',kind='MODEL',provider='fixture',source_identity='model',revision='1',
        payload={'raw_evidence_id':'raw','raw_evidence_sha256':raw['sha256']},evidence_class='SYNTHETIC')
    return store,now,model


def test_pinned_read_only_view_includes_committed_wal_but_not_later_appends(tmp_path):
    store,now,model=small_archive(tmp_path)
    with learning_source_view(store) as view:
        assert view.get('model')==model and view.snapshot_seq==model['seq']
        now[0]=3
        store.capture('later',event_id='event',kind='MODEL',provider='fixture',source_identity='raw',revision='2',
            payload={'response':'later'},evidence_class='SYNTHETIC')
        with pytest.raises(EvidenceError,match='EVIDENCE_MISSING'):view.get('later')
        with pytest.raises(sqlite3.OperationalError,match='readonly'):
            view._db.execute('CREATE TABLE forbidden(value TEXT)')
        row=view.get('raw');row['body']['payload']['response']='caller change'
        assert view.get('raw')['body']['payload']['response']=='original'
    assert store.get('later')['body']['payload']['response']=='later'


def test_learning_job_never_uses_source_connection_write_pragmas(tmp_path,monkeypatch):
    cfg,_,_,_=setup_job(tmp_path);source_store=cfg['source_store'];before=source_store.pin_read_view()
    with monkeypatch.context() as patch:
        patch.setattr(source_store,'_connect',lambda:(_ for _ in ()).throw(AssertionError('source write connection')))
        result=run_forecast_fit(**cfg)
    assert result['result']['status']=='NO_PROMOTION'
    assert source_store.pin_read_view()==before


def test_raw_derivation_keeps_original_receipts_after_a_later_revision(tmp_path):
    store,now,model=small_archive(tmp_path)
    with learning_source_view(store) as view:
        original=source_derivation(view,[model],event_id='event',cutoff=2.)
    now[0]=3
    store.capture('revision',event_id='event',kind='MODEL',provider='fixture',source_identity='raw',revision='2',
        payload={'response':'corrected'},evidence_class='SYNTHETIC')
    with learning_source_view(store) as view:
        assert source_derivation(view,[view.get('model')],event_id='event',cutoff=2.)==original
    assert {n['id'] for n in original['manifest']['nodes']}=={'model','raw'}
    assert next(n for n in original['manifest']['nodes'] if n['id']=='raw')['received_at']==1.
    assert original['sha256']==digest(original['manifest'])


@pytest.mark.parametrize('fault',['hash','missing_hash','missing_normalized_reference','duplicate','unknown_fields',
                                'cross_event','historical','label','late_child'])
def test_bad_or_noncausal_derived_sources_cannot_enter_dataset_provenance(tmp_path,fault):
    store,now,model=small_archive(tmp_path);p=dict(model['body']['payload']);now[0]=3
    if fault=='hash':p['raw_evidence_sha256']='f'*64
    if fault=='missing_hash':del p['raw_evidence_sha256']
    if fault=='missing_normalized_reference':p={'version':'alpha_v11_gefs_field_v1'}
    if fault in {'duplicate','unknown_fields'}:
        ref=dict(id='raw',sha256=store.get('raw')['sha256'],source_identity='raw')
        p=dict(version='alpha_v11_gefs_linear_day_v1' if fault=='duplicate' else 'unknown',field_references=[ref,ref])
    if fault in {'cross_event','historical','label','late_child'}:
        child=store.capture('bad-raw',event_id='other' if fault=='cross_event' else 'event',
            kind='LABEL' if fault=='label' else 'MODEL',provider='fixture',source_identity='raw',revision='bad',
            payload={},evidence_class='HISTORICAL_AVAILABILITY_UNKNOWN' if fault=='historical' else 'SYNTHETIC')
        p=dict(raw_evidence_id=child['id'],raw_evidence_sha256=child['sha256'])
    changed=store.capture('changed',event_id='event',kind='MODEL',provider='fixture',source_identity='model',
                          revision='2',payload=p,evidence_class='SYNTHETIC')
    with learning_source_view(store) as view:
        with pytest.raises(EvidenceError):
            source_derivation(view,[changed],event_id='event',cutoff=2. if fault=='late_child' else 3.)


def test_view_deadline_prevents_unbounded_reads(tmp_path):
    store,_,_=small_archive(tmp_path);now=[0.]
    with learning_source_view(store,monotonic=lambda:now[0]) as view:
        now[0]=11.
        with pytest.raises(EvidenceError,match='TIME_BOUND'):view.get('model')


def test_view_has_a_real_decoded_byte_budget(tmp_path):
    store,now,_=small_archive(tmp_path)
    for i in range(10):
        store.capture('large-'+str(i),event_id='event',kind='MODEL',provider='fixture',source_identity='large',
            revision=str(i),payload={'response':'x'*(900*1024)},evidence_class='SYNTHETIC')
    with learning_source_view(store) as view:
        with pytest.raises(EvidenceError,match='READ_BOUND'):
            for i in range(10):view.get('large-'+str(i))


def test_complete_gefs_raw_path_reaches_learning_dataset_without_truncating_at_256(gefs):
    r=gefs;store=r['store'];ids=all_fields(r)
    model=source.assemble_path(store,plan=r['plan'],field_ids=ids,record_id='model')
    rule=r['plan'].rule;contract=ForecastFeatureContract(((source.MODEL_ID,31),),rule.payload['unit'],rule.payload['family'])
    directory=store.path.parent/'objects';directory.mkdir(mode=0o700);artifacts=ArtifactStore(directory)
    params=make_artifacts()['PROBABILITY']['parameters'];params['models'][0]['model_id']=source.MODEL_ID
    parent=build_initial_forecast_bundle(artifacts,contract=contract,probability_parameters=params,
                                          provenance=provenance(),quality_modifiers={})
    components=_model_inputs(store,rule,('model',),r['now'][0],target=FINAL_EXTREME)
    prediction=predict_with_bundle(artifacts.pin(parent),rule,components,as_of=r['now'][0],max_source_age_seconds=86400.)
    capture=capture_forecast_vector(store,'capture',context=r['context'],rule=rule,
        binding=ReleaseBinding('a'*40,'b'*40,'c'*64,parent,rule.sha256),prediction=prediction,
        pinned_bundle=artifacts.pin(parent),model_input_ids=('model',),expires_at=r['now'][0]+20)
    r['now'][0]+=2*86400;labels={}
    for i,row in enumerate(capture['body']['details']['rows']):
        target=row['target_identity'];key='label-'+str(i);labels[target['market_id']]=key
        store.capture(key,event_id=rule.payload['event_id'],kind='LABEL',provider='TEST_ONLY',source_identity=target['token_id'],
            revision='test',evidence_class='SYNTHETIC',payload=dict(
                context=dict(station=rule.payload['station'],city=r['context'].city_id,local_date=rule.payload['target_date'],
                             target='FINAL_CONTRACT_PAYOUT',rule_fingerprint=rule.sha256),
                target_identity=target,label_version='test',decision_target='FINAL_CONTRACT_PAYOUT',knowable_at=r['now'][0],
                value=int(i==1),evidence_type='SYNTHETIC'))
    before=store.pin_read_view()
    with learning_source_view(store) as view:
        examples=labeled_examples(view,'capture',label_ids=labels,city=r['context'].city_id,horizon='DAY_AHEAD',season='AUTUMN')
    graph=examples[0].payload['source_derivation']['manifest']
    assert graph['node_count']==1+2*len(ids)==621 and graph['edge_count']==2*len(ids)
    raw_nodes=[n for n in graph['nodes'] if n['id'].startswith('raw-')]
    assert len(raw_nodes)==310 and all(n['published_at'] is None for n in raw_nodes)
    assert all(n['received_at']==model['body']['received_at'] for n in raw_nodes)
    assert all(e.payload['source_derivation']==examples[0].payload['source_derivation'] for e in examples)
    cutoff=r['now'][0]+1
    plan=DatasetPlan('gefs-research','FINAL_CONTRACT_PAYOUT',contract.schema.sha256,0.,cutoff,cutoff+1,cutoff+2,'f'*64)
    dataset=build_dataset(examples,plan,as_of=r['now'][0])
    assert dataset['manifest']['counts']['TRAIN']['city_days']==1
    assert store.pin_read_view()==before and store.limits.max_evidence_per_decision==64
