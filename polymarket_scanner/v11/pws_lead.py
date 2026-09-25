"""Paired causal PWS observation-lead research, never payout or exit pricing.

Candidate bundles are immutable numeric research inputs, not promoted champions.
This sleeve archives observation-only inference and descriptive first-received
report scores. It cannot create a paper position or an execution proposal.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math

from .evidence import EvidenceError, EvidenceStore, ReleaseBinding, digest, finite, identity
from .model_artifacts import PinnedBundle, predict_with_bundle
from .probability import NEXT_OBSERVATION
from .rules import RuleFingerprint, RuleGuard
from .strategy_pipeline import _model_inputs


VERSION = 'alpha_v11_pws_observation_lead_research_v1'
REPORT_VERSION = 'alpha_v11_official_temperature_report_v1'


@dataclass(frozen=True)
class LeadPolicy:
    version: str
    horizon_seconds: float
    max_official_age_seconds: float
    max_pws_age_seconds: float
    max_model_age_seconds: float
    max_rule_age_seconds: float

    def __post_init__(self):
        identity(self.version)
        for key,value in asdict(self).items():
            if key != 'version' and not 0 < finite(value) <= 86400:
                raise EvidenceError('LEAD_HORIZON_AND_AGE_BOUND')


def _report(row, rule):
    body, p = row['body'], row['body'].get('payload', {})
    if (row['kind'] != 'OFFICIAL_OBSERVATION' or row['event_id'] != rule.payload['event_id']
            or p.get('version') != REPORT_VERSION or p.get('rule_fingerprint') != rule.sha256
            or p.get('source_role') != 'EXACT_CONTRACT_OBSERVATION'
            or any(p.get(k) != rule.payload[k] for k in
                   ('station','target_date','unit','observation_population','source_family'))
            or type(p.get('reported_whole_degree')) is not int or abs(p['reported_whole_degree']) > 250
            or body['observed_at'] is None or body['evidence_class'] == 'HISTORICAL_AVAILABILITY_UNKNOWN'):
        raise EvidenceError('LEAD_EXACT_OFFICIAL_REPORT_REQUIRED')
    return p['reported_whole_degree']


def _lineage(store, model_ids, *, event_id, cutoff):
    """Explicit bounded derivation graph; an ablation cannot hide PWS in features."""
    if type(model_ids) is not tuple or not 1 <= len(model_ids) <= 16 or len(set(model_ids)) != len(model_ids):
        raise EvidenceError('LEAD_MODEL_INPUT_BOUND')
    stack=[store.get(key) for key in model_ids]
    seen, leaves, pws = {}, {}, set()
    while stack:
        row=stack.pop()
        if row['id'] in seen:
            continue
        if len(seen) >= 256:
            raise EvidenceError('LEAD_PROVENANCE_BOUND')
        body=row['body']
        if (row['event_id'] != event_id or row['kind'] not in {'MODEL','FEATURES','OFFICIAL_OBSERVATION','PWS_OBSERVATION'}
                or body['available_at'] > cutoff or body['evidence_class'] == 'HISTORICAL_AVAILABILITY_UNKNOWN'):
            raise EvidenceError('LEAD_PROVENANCE_NOT_CAUSAL')
        seen[row['id']]=row['sha256']
        if row['kind']=='PWS_OBSERVATION':
            pws.add(row['id'])
        refs=body['payload'].get('dependencies')
        if refs is None:
            if row['id'] in model_ids or row['kind']=='FEATURES':
                raise EvidenceError('LEAD_MODEL_OR_FEATURE_DERIVATION_REQUIRED')
            if row['kind']!='PWS_OBSERVATION':
                leaves[row['id']]=row['sha256']
            continue
        if not isinstance(refs,list) or not 1 <= len(refs) <= 64 or len({r['id'] for r in refs}) != len(refs):
            raise EvidenceError('LEAD_DERIVATION_BOUND')
        for ref in refs:
            source=store.get(ref['id'])
            if (source['sha256'] != ref['sha256'] or source['seq'] >= row['seq']
                    or source['body']['available_at'] > body['available_at']):
                raise EvidenceError('LEAD_DERIVATION_BINDING')
            stack.append(source)
    return dict(records=seen, non_pws_leaves=leaves, pws_ids=sorted(pws))


def paired_inference(store, *, rule, policy, official, pws_id, model_ids, without_pws_model_ids,
                     bundle, without_pws_bundle, cutoff):
    """Shared observation-only numerics and paired lineage; no admission or write."""
    if any(b.payload['bundle']['target'] != NEXT_OBSERVATION for b in (bundle,without_pws_bundle)):
        raise EvidenceError('LEAD_OBSERVATION_BUNDLE_REQUIRED_NOT_PAYOUT_OR_EXIT')
    event_id=rule.payload['event_id']; official_id=official['id']; heads=[]
    with_graph=_lineage(store,model_ids,event_id=event_id,cutoff=cutoff)
    without_graph=_lineage(store,without_pws_model_ids,event_id=event_id,cutoff=cutoff)
    if (with_graph['pws_ids']!=[pws_id] or without_graph['pws_ids']
            or with_graph['non_pws_leaves']!=without_graph['non_pws_leaves']
            or with_graph['non_pws_leaves'].get(official_id)!=official['sha256']):
        raise EvidenceError('LEAD_PAIRED_ABLATION_NOT_SAME_NON_PWS_EVIDENCE')
    expected_context=dict(official_anchor_id=official_id,official_anchor_sha256=official['sha256'],
                          horizon_seconds=policy.horizon_seconds,window_clock='FIRST_ALPHA_RECEIPT')
    predictions=[]
    for ids, pinned in ((model_ids,bundle),(without_pws_model_ids,without_pws_bundle)):
        for key in ids:
            row=store.get(key); body=row['body']
            from .gefs_sources import current_path_heads
            heads.extend(current_path_heads(store,row))
            if body['payload'].get('observation_context')!=expected_context:
                raise EvidenceError('LEAD_MODEL_ANCHOR_OR_HORIZON_MISMATCH')
            latest=store.latest_source(kind='MODEL',event_id=event_id,provider=body['provider'],source_identity=body['source_identity'])
            if latest['id']!=key:
                raise EvidenceError('LEAD_MODEL_REVISION_SUPERSEDED')
        components=_model_inputs(store,rule,ids,cutoff,target=NEXT_OBSERVATION)
        predictions.append(predict_with_bundle(pinned,rule,components,as_of=cutoff,
                           max_source_age_seconds=policy.max_model_age_seconds).payload)
    return dict(with_pws=predictions[0], without_pws=predictions[1],
                paired_provenance=dict(with_pws=with_graph,without_pws=without_graph), source_heads=heads)


class PWSObservationLead:
    def __init__(self, store: EvidenceStore):
        self.store=store

    def observe(self, record_id: str, *, rule: RuleFingerprint, binding: ReleaseBinding, policy: LeadPolicy,
                official_id: str, pws_id: str, model_ids: tuple[str, ...], without_pws_model_ids: tuple[str, ...],
                bundle: PinnedBundle, without_pws_bundle: PinnedBundle) -> dict:
        identity(record_id,maximum=100)
        request=dict(rule=asdict(rule),binding=asdict(binding),policy=asdict(policy),official_id=official_id,pws_id=pws_id,
                     model_ids=list(model_ids),without_pws_model_ids=list(without_pws_model_ids),
                     bundle_sha256=bundle.sha256,without_pws_bundle_sha256=without_pws_bundle.sha256)
        try:
            old=self.store.get(record_id)
        except EvidenceError as exc:
            if str(exc) != 'EVIDENCE_MISSING':
                raise
        else:
            if old['kind']!='MEASUREMENT' or old['body']['details'].get('request') != request:
                raise EvidenceError('LEAD_REQUEST_ID_COLLISION')
            return old
        now=finite(self.store.clock()); event_id=rule.payload['event_id']
        if binding.rule_fingerprint!=rule.sha256 or binding.bundle_sha256!=bundle.sha256:
            raise EvidenceError('LEAD_RELEASE_BUNDLE_BINDING')
        if any(b.payload['bundle']['target'] != NEXT_OBSERVATION for b in (bundle,without_pws_bundle)):
            raise EvidenceError('LEAD_OBSERVATION_BUNDLE_REQUIRED_NOT_PAYOUT_OR_EXIT')
        heads=[]
        for kind in ('OFFICIAL_OBSERVATION','PWS_OBSERVATION','MODEL','RULE_STATE'):
            head=self.store.latest(kind=kind,event_id=event_id)
            heads.append((kind,event_id,head['seq'] if head else 0))
        rule_status=RuleGuard(self.store).revalidate(event_id,rule.sha256,max_age_seconds=policy.max_rule_age_seconds)
        if not rule_status['passed']:
            raise EvidenceError(rule_status['reason'])
        official=self.store.get(official_id); _report(official,rule)
        pws=self.store.get(pws_id); qc=pws['body'].get('payload',{})
        if (pws['kind']!='PWS_OBSERVATION' or pws['event_id']!=event_id
                or pws['body'].get('provider')!='ALPHA_PWS_QC' or qc.get('health')!='HEALTHY'
                or qc.get('station')!=rule.payload['station']
                or qc.get('official_metadata_fingerprint')!=rule.payload['metadata_fingerprint']
                or not isinstance(qc.get('observation_age_seconds'),list) or not 1<=len(qc['observation_age_seconds'])<=400):
            raise EvidenceError('LEAD_PWS_QC_CONTEXT_REQUIRED')
        for source in (official,pws):
            if self.store.latest(kind=source['kind'],event_id=event_id)['id']!=source['id']:
                raise EvidenceError('LEAD_CURRENT_OFFICIAL_AND_PWS_REQUIRED')
            if source['body']['available_at']>now or source['body']['evidence_class']=='HISTORICAL_AVAILABILITY_UNKNOWN':
                raise EvidenceError('LEAD_SOURCE_NOT_CAUSAL')
        if not 0<=now-official['body']['observed_at']<policy.max_official_age_seconds:
            raise EvidenceError('LEAD_OFFICIAL_ANCHOR_STALE')
        pws_as_of=finite(qc.get('as_of'))
        if (not 0<=now-pws_as_of<policy.max_pws_age_seconds
                or any(finite(age)+now-pws_as_of>=policy.max_pws_age_seconds for age in qc['observation_age_seconds'])):
            raise EvidenceError('LEAD_PWS_SENSOR_AGE_STALE')
        paired=paired_inference(self.store,rule=rule,policy=policy,official=official,pws_id=pws_id,
                    model_ids=model_ids,without_pws_model_ids=without_pws_model_ids,bundle=bundle,
                    without_pws_bundle=without_pws_bundle,cutoff=now)
        heads.extend(paired['source_heads'])
        unique_heads = {}
        for kind,event,seq in heads:
            if (kind,event) in unique_heads and unique_heads[kind,event] != seq:
                raise EvidenceError('LEAD_SOURCE_CHANGED_DURING_INFERENCE')
            unique_heads[kind,event] = seq
        heads = [(k,e,s) for (k,e),s in unique_heads.items()]
        window=dict(station=rule.payload['station'],population=rule.payload['observation_population'],
                    window_start=now,window_end=now+policy.horizon_seconds,clock='FIRST_ALPHA_RECEIPT')
        refs=tuple(dict.fromkeys((official_id,pws_id,*model_ids,*without_pws_model_ids)))
        details=dict(version=VERSION,request=request,strategy='PWS_OBSERVATION_LEAD',lifecycle='RESEARCH',
                     valuation_type='OBSERVATION_ONLY',target=NEXT_OBSERVATION,observation_window=window,
                     with_pws=paired['with_pws'],without_pws=paired['without_pws'],paired_provenance=paired['paired_provenance'],
                     artifact_refs=dict(with_pws=bundle.payload['bundle']['artifacts'],without_pws=without_pws_bundle.payload['bundle']['artifacts']),
                     source_heads=heads,official_anchor_observed_at=official['body']['observed_at'],
                     pws_independence_status=qc.get('independence_status','UNKNOWN'),
                     feature_ready_at=now,outcome='GATED',reason='OBSERVATION_RESEARCH_REQUIRES_SEPARATE_VALIDATED_ECONOMICS_AND_ADMISSION',
                     settlement_prediction=None,conservative_contract_payout=None,executable_exit_proceeds=None,proposal=None,
                     champion_approval='NOT_ATTESTED_BY_RESEARCH_BUNDLE_HASH',lead_advantage_verified=False,
                     calibration_status='UNCALIBRATED',financial_authority=False)
        return self.store.audit(record_id,event_id=event_id,kind='MEASUREMENT',details=details,evidence_ids=refs,
                                expected_heads=tuple(heads))

    def score_first_received_report(self, record_id: str, *, observation_id: str) -> dict:
        """Describe the first archived new report; missing collection is unknown.

        This does not certify the next published report, settlement labels,
        statistical independence, out-of-sample benefit or executable repricing.
        """
        observation=self.store.get(observation_id); d=observation['body'].get('details',{})
        if observation['kind']!='MEASUREMENT' or d.get('version')!=VERSION:
            raise EvidenceError('LEAD_OBSERVATION_RECORD_REQUIRED')
        rule=RuleFingerprint(**d['request']['rule']); now=finite(self.store.clock()); window=d['observation_window']
        rows=self.store.records(kind='OFFICIAL_OBSERVATION',event_id=observation['event_id'],after_seq=observation['seq'],limit=1000)
        if len(rows)==1000:
            raise EvidenceError('LEAD_LABEL_SCAN_BOUND')
        chosen=None; invalidated_by=None
        for row in rows:
            if row['body']['available_at']>now:
                continue
            if invalidated_by is None:
                invalidated_by=row['id']
            try:
                value=_report(row,rule)
            except EvidenceError:
                continue
            if row['body']['observed_at']<=d['official_anchor_observed_at']:
                continue  # A correction of the anchor is not the next observation.
            if not observation['body']['recorded_at']<=row['body']['available_at']<=window['window_end']:
                break
            chosen=(row,value); break
        result=dict(version=VERSION,observation_id=observation_id,target=NEXT_OBSERVATION,
                    status='UNKNOWN',reason='NO_RECEIVED_ELIGIBLE_REPORT_IN_WINDOW',first_official_update=invalidated_by,
                    target_is_true_next_published_report=False,collection_continuity_verified=False,
                    comparison_partition='DEVELOPMENT',calibration_status='SCORED_NOT_CALIBRATED',
                    lead_advantage_verified=False,settlement_label=None,executable_markout=None,trading_pnl=None,
                    financial_authority=False)
        refs=[observation_id]
        if chosen:
            row,value=chosen
            matches=[b['market_id'] for b in rule.payload['partition'] if
                     (b['lower'] is None or value>=b['lower']) and (b['upper'] is None or value<=b['upper'])]
            if len(matches)!=1:
                raise EvidenceError('LEAD_REPORTED_VALUE_NOT_IN_EXACT_PARTITION')
            scores=[]
            for prediction in (d['with_pws'],d['without_pws']):
                vector=prediction['buckets']; win=next(b['point'] for b in vector if b['market_id']==matches[0])
                scores.append(dict(brier=math.fsum((b['point']-(b['market_id']==matches[0]))**2 for b in vector),
                                   log_loss=-math.log(win) if win>0 else None,log_loss_infinite=win==0))
            result.update(status='MEASURED_FIRST_RECEIVED_REPORT',reason='ARCHIVED_RECEIPT_COMPARISON_NOT_CERTIFIED_NEXT_LABEL',
                          official_id=row['id'],official_sha256=row['sha256'],reported_whole_degree=value,
                          observed_at=row['body']['observed_at'],received_at=row['body']['received_at'],
                          provider_published_at=row['body']['published_at'],
                          receipt_lead_seconds=row['body']['received_at']-observation['body']['recorded_at'],
                          with_pws=scores[0],without_pws=scores[1],
                          brier_improvement=scores[1]['brier']-scores[0]['brier'],
                          log_loss_improvement=scores[1]['log_loss']-scores[0]['log_loss'] if all(s['log_loss'] is not None for s in scores) else None,
                          label_evidence_class=row['body']['evidence_class'])
            refs.append(row['id'])
        return self.store.audit(record_id,event_id=observation['event_id'],kind='MEASUREMENT',details=result,evidence_ids=tuple(refs))
