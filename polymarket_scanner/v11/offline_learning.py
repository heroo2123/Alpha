"""Bounded deterministic research fits; never writes a champion pointer.

The initial learner varies one model's additive temperature bias and kernel
dispersion over a frozen finite grid. All parameter selection uses TRAIN only.
One candidate then receives identical-data champion comparison and ablation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import copy
import math
import random
import time

from .datasets import ExperimentJournal, verify_dataset
from .evidence import EvidenceError, canonical, digest, finite, identity, sha
from .model_artifacts import ArtifactStore, validate_provenance
from .probability import score_vectors


@dataclass(frozen=True)
class LearningEnvelope:
    model_id: str
    member_features: tuple[str, ...]
    lower_cut_feature: str
    upper_cut_feature: str
    bias_grid: tuple[float, ...]
    sigma_grid: tuple[float, ...]
    primary_metric: str
    minimum_train_city_days: int
    minimum_evaluation_city_days: int
    minimum_evaluation_stations: int
    required_improvement: float
    maximum_slice_regression: float
    max_wall_seconds: float
    max_kernel_evaluations: int
    bootstrap_resamples: int
    seed: int

    def __post_init__(self):
        identity(self.model_id)
        for key in (*self.member_features,self.lower_cut_feature,self.upper_cut_feature):
            identity(key)
        if (not isinstance(self.member_features,tuple) or not 1<=len(self.member_features)<=64
                or len(set(self.member_features))!=len(self.member_features)
                or self.lower_cut_feature==self.upper_cut_feature
                or {self.lower_cut_feature,self.upper_cut_feature}&set(self.member_features)):
            raise EvidenceError('LEARNER_FEATURE_MAPPING_INVALID')
        if (not isinstance(self.bias_grid,tuple) or not isinstance(self.sigma_grid,tuple)
                or not self.bias_grid or not self.sigma_grid or len(self.bias_grid)*len(self.sigma_grid)>32
                or any(abs(finite(x,nonnegative=False))>30 for x in self.bias_grid)
                or any(not .01<=finite(x)<=50 for x in self.sigma_grid)):
            raise EvidenceError('LEARNER_GRID_BOUND')
        if len(set(self.bias_grid))!=len(self.bias_grid) or len(set(self.sigma_grid))!=len(self.sigma_grid):
            raise EvidenceError('DUPLICATE_LEARNER_PARAMETER')
        if self.primary_metric not in {'brier','log_loss'}:
            raise EvidenceError('LEARNER_PRIMARY_METRIC_UNSUPPORTED')
        for v in (self.minimum_train_city_days,self.minimum_evaluation_city_days,self.minimum_evaluation_stations):
            if type(v) is not int or not 2<=v<=10_000:
                raise EvidenceError('LEARNER_EVIDENCE_POLICY_REQUIRED')
        for v in (self.required_improvement,self.maximum_slice_regression):
            if not 0<=finite(v)<=2:
                raise EvidenceError('LEARNER_METRIC_POLICY_BOUND')
        if (not 0<finite(self.max_wall_seconds)<=60 or type(self.max_kernel_evaluations) is not int
                or not 1<=self.max_kernel_evaluations<=2_000_000 or type(self.bootstrap_resamples) is not int
                or not 100<=self.bootstrap_resamples<=1000 or type(self.seed) is not int or not 0<=self.seed<2**32):
            raise EvidenceError('LEARNER_RESOURCE_BOUND')

    @property
    def sha256(self):
        return digest(asdict(self))


class _Budget:
    def __init__(self,envelope,clock):
        self.clock=clock
        self.deadline=clock()+envelope.max_wall_seconds
        self.remaining=envelope.max_kernel_evaluations

    def use(self,n=0):
        self.remaining-=n
        if self.remaining<0 or self.clock()>self.deadline:
            raise EvidenceError('LEARNER_RESOURCE_BUDGET_EXHAUSTED')


def _predict(rows,envelope,bias,sigma,budget):
    result=[]
    for p in rows:
        budget.use(2*len(envelope.member_features))
        v=p['values']
        try:
            members=[finite(v[key],nonnegative=False) for key in envelope.member_features]
            lo=v[envelope.lower_cut_feature]
            hi=v[envelope.upper_cut_feature]
        except KeyError:
            raise EvidenceError('LEARNER_REQUIRED_FEATURE_MISSING') from None
        lower=-math.inf if lo is None else finite(lo,nonnegative=False)
        upper=math.inf if hi is None else finite(hi,nonnegative=False)
        if lower>=upper or not members:
            raise EvidenceError('LEARNER_BUCKET_CUTS_INVALID')
        cdf=lambda cut:math.fsum(.5*math.erfc(-(cut-x-bias)/(sigma*math.sqrt(2))) for x in members)/len(members)
        probability=cdf(upper)-cdf(lower)
        if p['target_identity']['side']=='NO':
            probability=1-probability
        result.append(min(1.,max(0.,probability)))
    return result


def _scores(rows,probabilities):
    value=score_vectors([[1-p,p] for p in probabilities],[int(r['label_value']) for r in rows],
        event_ids=[r['event_id']+':'+r['target_identity_sha256'] for r in rows],city_days=[r['city_day'] for r in rows])
    value['n_binary_targets']=value.pop('n_events')
    value['n_events']=len({r['event_id'] for r in rows})
    value['metric_convention']='MULTICLASS_BRIER_FOR_BINARY_VECTOR'
    return value


def _metric(value,name):
    return math.inf if name=='log_loss' and value['log_loss_infinite'] else value[name]


def _comparison(rows,baseline,candidate,envelope,budget):
    if not rows:
        return {'status':'NO_EVIDENCE','passes_research_comparison':False}
    old,new=_scores(rows,baseline),_scores(rows,candidate)
    groups={}
    for i,r in enumerate(rows):
        budget.use()
        groups.setdefault(r['city_day'],[]).append(i)
    deltas=[]
    for indices in groups.values():
        budget.use()
        subset=[rows[i] for i in indices]
        a=_metric(_scores(subset,[baseline[i] for i in indices]),envelope.primary_metric)
        b=_metric(_scores(subset,[candidate[i] for i in indices]),envelope.primary_metric)
        if not math.isfinite(a) or not math.isfinite(b):
            return {'status':'NONFINITE_COMPARISON','champion':old,'challenger':new,'passes_research_comparison':False}
        deltas.append(a-b)
    rng=random.Random(envelope.seed)
    draws=[]
    for _ in range(envelope.bootstrap_resamples):
        budget.use(len(deltas))
        draws.append(math.fsum(rng.choice(deltas) for _ in deltas)/len(deltas))
    draws.sort()
    interval=[draws[int(.025*(len(draws)-1))],draws[int(.975*(len(draws)-1))]]
    leave_best=(math.fsum(deltas)-max(deltas))/(len(deltas)-1) if len(deltas)>1 else None
    slices=[]
    for field in ('station','horizon','season','strategy','selection'):
        members={}
        for i,r in enumerate(rows):
            budget.use()
            members.setdefault(r[field],[]).append(i)
        for key,indices in sorted(members.items()):
            subset=[rows[i] for i in indices]
            a=_scores(subset,[baseline[i] for i in indices])
            b=_scores(subset,[candidate[i] for i in indices])
            difference=_metric(a,envelope.primary_metric)-_metric(b,envelope.primary_metric)
            slices.append({'field':field,'value':key,'n_city_days':a['n_city_days'],
                           'improvement':difference if math.isfinite(difference) else None,
                           'within_tolerance':math.isfinite(difference) and difference>=-envelope.maximum_slice_regression})
    enough=(len(groups)>=envelope.minimum_evaluation_city_days
            and len({r['station'] for r in rows})>=envelope.minimum_evaluation_stations)
    passes=(enough and interval[0]>envelope.required_improvement and leave_best is not None
            and leave_best>envelope.required_improvement and all(x['within_tolerance'] for x in slices))
    return {'status':'COMPARED','champion':old,'challenger':new,'n_city_days':len(groups),
            'primary_metric':envelope.primary_metric,'paired_improvement_interval':interval,
            'uncertainty_method':'SEEDED_CITY_DAY_BOOTSTRAP_DEPENDENCE_NOT_CERTIFIED',
            'leave_best_city_day_out_improvement':leave_best,'slices':slices,
            'passes_research_comparison':passes,'financial_authority':False}


def run_research_fit(*, artifacts: ArtifactStore, journal: ExperimentJournal, plan_record_id: str,
                     dataset: dict, parent_bundle_sha256: str, envelope: LearningEnvelope,
                     provenance: dict, run_id: str, monotonic=time.monotonic) -> dict:
    """Fit an immutable challenger; all outcomes preserve the active champion.

    The numerical budget is bounded here. OS credential/network/CPU/RAM isolation
    is a separate required deployment gate and is not asserted by this function.
    """
    identity(run_id,maximum=100)
    validate_provenance(provenance)
    if (provenance['run_id']!=run_id or provenance['seed']!=envelope.seed
            or provenance['comparison_policy_sha256']!=envelope.sha256):
        raise EvidenceError('LEARNER_RUN_PROVENANCE_MISMATCH')
    sha(parent_bundle_sha256)
    manifest=verify_dataset(dataset)
    if manifest['plan']['comparison_policy_sha256']!=envelope.sha256:
        raise EvidenceError('FROZEN_LEARNER_POLICY_MISMATCH')
    registered=journal.store.get(plan_record_id)
    if registered['body'].get('details',{}).get('plan_sha256')!=manifest['plan_sha256']:
        raise EvidenceError('REGISTERED_LEARNER_PLAN_REQUIRED')
    if manifest['as_of']>journal.store.clock() or provenance['created_at']<manifest['as_of']:
        raise EvidenceError('LEARNER_DATASET_NOT_YET_AVAILABLE')
    parent=artifacts.pin(parent_bundle_sha256).payload
    probability=parent['components']['PROBABILITY']
    models=probability['parameters']['models']
    if (parent['bundle']['target']!='FINAL_CONTRACT_PAYOUT' or manifest['plan']['target']!='FINAL_CONTRACT_PAYOUT'
            or probability['parameters']['family']!='GAUSSIAN_MEMBER_MIXTURE'
            or len(models)!=1 or models[0]['model_id']!=envelope.model_id
            or parent['bundle']['feature_schema_sha256']!=manifest['plan']['feature_schema_sha256']):
        raise EvidenceError('LEARNER_SINGLE_MODEL_TARGET_OR_SCHEMA_UNSUPPORTED')
    features={f['name']:f for f in parent['components']['FEATURES']['parameters']['features']}
    members=envelope.member_features
    cuts=(envelope.lower_cut_feature,envelope.upper_cut_feature)
    if (set(features)!=set((*members,*cuts))
            or len({f['unit'] for f in features.values()})!=1
            or next(iter(features.values()))['unit'] not in {'C','F'}
            or any(features[key]['missing_allowed'] for key in members)
            or any(not features[key]['missing_allowed'] for key in cuts)):
        raise EvidenceError('LEARNER_COMPLETE_PARENT_FEATURE_MAPPING_REQUIRED')
    rows={part:[r['example'] for r in values] for part,values in manifest['partitions'].items()}
    if len({r['selection'] for values in rows.values() for r in values})!=1:
        raise EvidenceError('LEARNER_SELECTION_COHORT_MIXED')
    selection_id=run_id+':selection'
    journal.attempt(selection_id,plan_record_id=plan_record_id,
        candidate_sha256=digest({'parent':parent_bundle_sha256,'dataset':dataset['sha256'],'policy':envelope.sha256}),
        parent_champion_sha256=parent_bundle_sha256,hyperparameters=asdict(envelope))
    budget=_Budget(envelope,monotonic)
    try:
        if len({r['city_day'] for r in rows['TRAIN']})<envelope.minimum_train_city_days:
            result={'status':'NO_PROMOTION','reason':'INSUFFICIENT_TRAINING_GROUPS','candidate_bundle_sha256':None,
                    'parent_bundle_sha256':parent_bundle_sha256,'financial_authority':False}
        else:
            trials=[]
            for i,(bias,sigma) in enumerate((b,s) for b in sorted(envelope.bias_grid) for s in sorted(envelope.sigma_grid)):
                attempt_id=run_id+':trial:'+str(i)
                journal.attempt(attempt_id,plan_record_id=plan_record_id,
                    candidate_sha256=digest({'parent':parent_bundle_sha256,'bias':bias,'sigma':sigma}),
                    parent_champion_sha256=parent_bundle_sha256,hyperparameters={'bias':bias,'kernel_sigma':sigma})
                try:
                    metric=_scores(rows['TRAIN'],_predict(rows['TRAIN'],envelope,bias,sigma,budget))
                except Exception:
                    journal.finish(attempt_id+':finished',attempt_id=attempt_id,status='FAILED',reason='FIT_FAILED',result_sha256=None)
                    raise
                trial={'bias':bias,'kernel_sigma':sigma,'training_metrics':metric}
                trials.append(trial)
                journal.finish(attempt_id+':finished',attempt_id=attempt_id,status='NO_PROMOTION',
                               reason='TRAINING_ONLY_TRIAL',result_sha256=digest(trial))
            best=min(trials,key=lambda r:(_metric(r['training_metrics'],envelope.primary_metric),r['bias'],r['kernel_sigma']))
            p=copy.deepcopy(probability)
            p['parameters']['models'][0].update(bias=best['bias'],kernel_sigma=best['kernel_sigma'])
            p['provenance']={**provenance,'parent_bundle_sha256':parent_bundle_sha256,'dataset_sha256':dataset['sha256'],
                'causal_watermark':manifest['causal_watermark'],'training_metrics_sha256':digest(best['training_metrics']),
                'holdout_metrics_sha256':None,'comparison_policy_sha256':envelope.sha256,'training_status':'FITTED_NOT_CALIBRATED'}
            refs={**parent['bundle']['artifacts'],'PROBABILITY':artifacts.put_artifact(p)}
            calibration=copy.deepcopy(parent['components']['CALIBRATION'])
            calibration['parameters']['probability_artifact_sha256']=refs['PROBABILITY']
            calibration['provenance']=p['provenance']
            refs['CALIBRATION']=artifacts.put_artifact(calibration)
            candidate=artifacts.put_bundle(artifacts=refs,target=parent['bundle']['target'],
                                            feature_schema_sha256=parent['bundle']['feature_schema_sha256'])
            comparisons={}
            for part in ('DEVELOPMENT','CONFIRMATION'):
                if part=='CONFIRMATION' and rows[part]:
                    reveal=journal.reveal_confirmation(run_id+':confirmation',attempt_id=selection_id,dataset=dataset)
                    confirmation_role=reveal['body']['details']['evidence_role']
                baseline=_predict(rows[part],envelope,models[0]['bias'],models[0]['kernel_sigma'],budget)
                challenger=_predict(rows[part],envelope,best['bias'],best['kernel_sigma'],budget)
                comparisons[part]=_comparison(rows[part],baseline,challenger,envelope,budget)
            # No label builder currently supplies independent attestation. Keep the
            # candidate as research even when numerical comparison looks favorable.
            result={'status':'NO_PROMOTION','reason':'INDEPENDENT_LABEL_AND_DEPENDENCE_REVIEW_REQUIRED',
                    'candidate_bundle_sha256':candidate,'parent_bundle_sha256':parent_bundle_sha256,
                    'dataset_sha256':dataset['sha256'],'policy_sha256':envelope.sha256,'trials':trials,
                    'selected_parameters':{'bias':best['bias'],'kernel_sigma':best['kernel_sigma']},
                    'selection_partition':'TRAIN_ONLY','comparisons':comparisons,
                    'confirmation_role':confirmation_role if rows['CONFIRMATION'] else 'ABSENT',
                    'ablation':'PARENT_PARAMETERS_ON_IDENTICAL_CAUSAL_ROWS',
                    'calibration_status':'FITTED_NOT_CALIBRATED','financial_authority':False}
        journal.store.audit(run_id+':result',event_id=journal.event_id,kind='MODEL_EVENT',
                            details={'action':'IMMUTABLE_RESEARCH_RESULT','result':result,'sha256':digest(result)},
                            evidence_ids=(selection_id,))
        journal.finish(run_id+':finished',attempt_id=selection_id,status='NO_PROMOTION',reason=result['reason'],result_sha256=digest(result))
        return {'result':result,'sha256':digest(result)}
    except Exception:
        journal.finish(run_id+':failed',attempt_id=selection_id,status='FAILED',reason='BOUNDED_LEARNER_FAILED',result_sha256=None)
        raise
