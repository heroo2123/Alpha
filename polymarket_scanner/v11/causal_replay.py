"""Bounded read-only replay of retained candidate economics and account context.

Recomputation uses the shared numeric engine, not a caller-supplied evaluator.
It is not historical executable/permission attestation or renewed admission.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
from decimal import InvalidOperation
import time

from . import model_registry
from .certification import CapabilityScope
from .evidence import EvidenceError, KINDS, ReleaseBinding, canonical, digest, finite, identity, sha
from .learning_sources import learning_source_view, source_derivation
from .model_artifacts import predict_with_bundle
from .paper_coordinator import ACCOUNT_KEY, PaperCoordinator, VERSION as ACCOUNT_VERSION
from .rules import RuleFingerprint
from .strategy_admission import VERSION as ADMISSION_VERSION
from .strategy_pipeline import EntryRequest, VERSION as STRATEGY_VERSION, temperature_inputs
from .valuation import CostComponent, ValuationPolicy, settlement_entry_details

VERSION = 'alpha_v11_causal_economic_replay_v1'
SUPPORTED = {'FUTURE_FORECAST', 'SAME_DAY_LATE_LOCK', 'PWS_OBSERVATION_LEAD'}


@dataclass(frozen=True)
class ReplayPolicy:
    version: str
    maximum_seconds: float = 2.

    def __post_init__(self):
        identity(self.version)
        if not .05 <= finite(self.maximum_seconds) <= 5:
            raise EvidenceError('REPLAY_POLICY_BOUND')


def _ref(row):
    return dict(id=row['id'], sha256=row['sha256'], seq=row['seq'])


class HistoricalView:
    """No write methods, no source store access through this public interface."""
    def __init__(self, view, *, through_seq, at):
        if type(through_seq) is not int or not 0 <= through_seq <= view.snapshot_seq:
            raise EvidenceError('REPLAY_SEQUENCE_BOUND')
        self._view, self.through_seq, self.at = view, through_seq, finite(at)
        self.namespace = view.namespace
        self.derivation_cache = {}

    def clock(self): return self.at
    def check(self): self._view.check()

    def get(self, key):
        row = self._view.get(key); b = row['body']
        if (row['seq'] > self.through_seq or b['recorded_at'] > self.at or b['available_at'] > self.at
                or row['kind'] in KINDS and b.get('evidence_class') == 'HISTORICAL_AVAILABILITY_UNKNOWN'):
            raise EvidenceError('REPLAY_NONCAUSAL_OR_UNKNOWN_RECEIPT')
        return row

    def latest(self, *, kind, event_id):
        row = self._view.latest(kind=kind, event_id=event_id, through_seq=self.through_seq)
        return self.get(row['id']) if row else None

    def latest_source(self, *, kind, event_id, provider, source_identity, as_of=None):
        at = self.at if as_of is None else min(self.at, finite(as_of))
        row = self._view.latest_source(kind=kind, event_id=event_id, provider=provider,
            source_identity=source_identity, as_of=at, through_seq=self.through_seq)
        return self.get(row['id']) if row else None


def historical_bundle(*, scope_key, mode, epoch, state_sha256, bundle_sha256, at, check):
    """Verify an original state prefix in retained protected history, read-only.

    The returned numeric bundle is not an ActiveModelRegistry/DecisionModelPin and
    cannot renew an admission or restore an overlay. Missing history gates replay.
    """
    sha(scope_key); sha(state_sha256); sha(bundle_sha256); finite(at); check()
    envelope = model_registry.protected_state(scope_key=scope_key, mode=mode)
    current = envelope['state']
    if (digest(current) != envelope['sha256'] or current['scope_key'] != scope_key or current['mode'] != mode
            or mode not in {'V11_PAPER','V11_SHADOW'} or current['financial_authority'] is not False
            or type(epoch) is not int or not 1 <= epoch <= current['epoch'] <= 1000
            or len(current['events']) != current['epoch']):
        raise EvidenceError('REPLAY_MODEL_HISTORY_SCOPE')
    state = dict(version='alpha_v11_model_authority_v1', epoch=0, scope_key=scope_key, mode=mode,
        active_bundle_sha256=None, previous_bundle_sha256=None,
        overlay=dict(size_multiplier=1.0, require_manual_review=False), events=[], financial_authority=False)
    original = None; original_review = None; selected_review = None
    # Validate the complete chain, including links after the requested prefix.
    for e in current['events']:
        check(); action = e['action']; review = e['reviewed_bundle']; stamp = finite(e['at'])
        if (e['epoch'] != state['epoch']+1 or e['previous_state_sha256'] != digest(state)
                or e['previous_event_sha256'] != (digest(state['events'][-1]) if state['events'] else None)
                or e.get('financial_authority') is not False
                or state['events'] and stamp < state['events'][-1]['at']):
            raise EvidenceError('REPLAY_MODEL_HISTORY_CHAIN')
        if action in {'PROMOTE','ROLLBACK','RESTORE_OVERLAY'}:
            if (type(review) is not dict or digest(review) != e['review_sha256'] or review['review_id'] != e['review_id']
                    or review['action'] != action or review['scope_key'] != scope_key or review['mode'] != mode
                    or review['expected_epoch'] != state['epoch'] or review['parent_bundle_sha256'] != state['active_bundle_sha256']
                    or review['candidate_bundle_sha256'] != e['active_bundle_sha256']
                    or review['approval_kind'] != 'NONFINANCIAL_MODEL_ONLY'
                    or not finite(review['approved_at']) <= stamp < finite(review['expires_at'])):
                raise EvidenceError('REPLAY_MODEL_REVIEW_BINDING')
            preimage = dict(version='alpha_v11_compatible_bundle_v1', runtime_contract=review['runtime_contract'],
                target=review['target'], feature_schema_sha256=review['feature_schema_sha256'],
                artifacts=review['artifact_refs'], financial_authority=False)
            if digest(preimage) != e['active_bundle_sha256']:
                raise EvidenceError('REPLAY_MODEL_BUNDLE_PREIMAGE')
            if action in {'PROMOTE','ROLLBACK'}:
                if (e['active_bundle_sha256'] == state['active_bundle_sha256'] or e['overlay'] != state['overlay']
                        or action == 'ROLLBACK' and e['active_bundle_sha256'] != state['previous_bundle_sha256']):
                    raise EvidenceError('REPLAY_MODEL_PROMOTION_HISTORY')
                state['previous_bundle_sha256'] = state['active_bundle_sha256']
                state['active_bundle_sha256'] = e['active_bundle_sha256']
                selected_review = review
            elif (e['active_bundle_sha256'] != state['active_bundle_sha256']
                    or e['overlay'] != dict(size_multiplier=review['size_multiplier'], require_manual_review=False)):
                raise EvidenceError('REPLAY_MODEL_RESTORATION_HISTORY')
        elif action == 'DEMOTE':
            if (review is not None or e['review_id'] is not None or e['review_sha256'] is not None
                    or e['active_bundle_sha256'] != state['active_bundle_sha256']
                    or e['overlay']['require_manual_review'] is not True
                    or not 0 <= finite(e['overlay']['size_multiplier']) <= state['overlay']['size_multiplier']):
                raise EvidenceError('REPLAY_MODEL_DEMOTION_HISTORY')
        else:
            raise EvidenceError('REPLAY_MODEL_HISTORY_ACTION')
        overlay = e['overlay']
        if (set(overlay) != {'size_multiplier','require_manual_review'}
                or not 0 <= finite(overlay['size_multiplier']) <= 1 or type(overlay['require_manual_review']) is not bool):
            raise EvidenceError('REPLAY_MODEL_OVERLAY')
        state.update(epoch=e['epoch'], overlay=deepcopy(overlay)); state['events'].append(deepcopy(e))
        if e['epoch'] == epoch:
            original = deepcopy(state); original_review = selected_review
    if state != current or original is None or digest(original) != state_sha256:
        raise EvidenceError('REPLAY_ORIGINAL_MODEL_STATE_MISMATCH')
    if (original['active_bundle_sha256'] != bundle_sha256 or original['events'][-1]['at'] > at
            or original['overlay']['require_manual_review'] or original['overlay']['size_multiplier'] <= 0
            or original_review is None):
        raise EvidenceError('REPLAY_ORIGINAL_MODEL_NOT_ELIGIBLE')
    check(); bundle = model_registry.ApprovedArtifactReader().pin(bundle_sha256); check()
    if bundle.payload['bundle']['artifacts'] != original_review['artifact_refs']:
        raise EvidenceError('REPLAY_ORIGINAL_MODEL_ARTIFACTS')
    return bundle, dict(epoch=epoch, state_sha256=state_sha256, bundle_sha256=bundle.sha256,
        scope_key=scope_key, mode=mode, original_overlay=original['overlay'], retained_history_verified=True,
        current_pointer_used_for_inference=False, admission_authority=False)


def _request(raw):
    r = deepcopy(raw)
    r['valuation_policy'] = ValuationPolicy(**r['valuation_policy'])
    r['costs'] = tuple(CostComponent(**dict(c, covers=tuple(c['covers']))) for c in r['costs'])
    r['model_input_ids'] = tuple(r['model_input_ids'])
    return EntryRequest(**r)


def _account(c, view):
    row = view.latest(kind='COORDINATOR_EVENT', event_id=ACCOUNT_KEY)
    replay = PaperCoordinator(view, policy=c.policy, correlation=c.correlation, limits=c.limits)
    if row:
        d = row['body']['details']; state = d['state']
        if (d.get('version') != ACCOUNT_VERSION or d['policy_sha256'] != c.policy_sha
                or state['account_id'] != c.policy.account_id or state['execution_namespace'] != view.namespace
                or state['financial_authority'] is not False or len(state['intents']) > 512
                or len(state['lots']) > 512 or len(state['fills']) > 2048 or len(state['rules']) > 32):
            raise EvidenceError('REPLAY_ACCOUNT_POLICY_OR_BOUND')
        recorded_view = HistoricalView(view._view, through_seq=row['seq'], at=row['body']['recorded_at'])
        original = PaperCoordinator(recorded_view, policy=c.policy, correlation=c.correlation, limits=c.limits)
        risk_matches = canonical(original._risk(state)) == canonical(d.get('risk')) if 'risk' in d else None
        if risk_matches is False: raise EvidenceError('REPLAY_ACCOUNT_RISK_MISMATCH')
    else:
        # An absent ledger head does not archive the then-configured initial
        # cash/policy. Today's supplied policy cannot fill that historical gap.
        return dict(snapshot_ref=None, policy_sha256=None, recorded_risk_matches=None,
            risk_at_decision=None, status='UNKNOWN_NO_ARCHIVED_ACCOUNT_POLICY_OR_STATE',
            population='PRE_DECISION_ACCOUNT_CONTEXT', reservation_commands_replayed=False,
            economic_commands_issued=0, financial_authority=False)
    return dict(snapshot_ref=_ref(row) if row else None, policy_sha256=c.policy_sha,
        recorded_risk_matches=risk_matches, risk_at_decision=replay._risk(state), status='RECOMPUTED_ARCHIVED_CONTEXT',
        population='PRE_DECISION_ACCOUNT_CONTEXT', reservation_commands_replayed=False,
        economic_commands_issued=0, financial_authority=False)


def _pws_pair(inputs, *, request, decision, payout_request, payout_assessment, payout_model, payout_bundle):
    """Original observation pair, never a replacement for payout or admission.

    Inference uses the earlier lead boundary, not the later entry or label time.
    Only the observation champion has a protected scope; the exact archived
    without-PWS bundle is a research input and receives no champion authority.
    """
    from .pws_admission import VERSION as PAIR_VERSION, _heads
    from .pws_lead import VERSION as LEAD_VERSION, LeadPolicy, _report, paired_inference
    from .probability import NEXT_OBSERVATION
    pin = inputs.get(request.preconfirmation_id); pd = pin['body']['details']
    if pin['kind'] != 'REGISTRY' or pd.get('version') != PAIR_VERSION:
        raise EvidenceError('REPLAY_PWS_PRECONFIRMATION_REQUIRED')
    p, before = pd['request'], pd['assessment']
    if (p['payout_admission_id'] != request.admission_id
            or canonical(decision['preconfirmation']) != canonical(dict(before,
                preconfirmation_id=pin['id'], preconfirmation_sha256=pin['sha256']))
            or not pin['body']['recorded_at'] <= inputs.at < finite(before['valid_until'])):
        raise EvidenceError('REPLAY_PWS_ORIGINAL_PAIR_BINDING')
    lead = inputs.get(p['lead_id']); ld = lead['body']['details']; original = ld['request']
    observation = inputs.get(p['observation_admission_id']); od = observation['body']['details']
    if (lead['kind'] != 'MEASUREMENT' or ld.get('version') != LEAD_VERSION
            or ld['target'] != NEXT_OBSERVATION or ld['valuation_type'] != 'OBSERVATION_ONLY'
            or observation['kind'] != 'REGISTRY' or od.get('version') != ADMISSION_VERSION
            or not max(lead['seq'], observation['seq']) < pin['seq']):
        raise EvidenceError('REPLAY_PWS_ORIGINAL_OBSERVATION_REQUIRED')
    ar, a = od['request'], od['assessment']; scope = CapabilityScope(**ar['scope'])
    rule = RuleFingerprint(**original['rule']); policy = LeadPolicy(**original['policy'])
    cutoff = finite(ld['feature_ready_at'])
    if (scope.strategy != 'PWS_OBSERVATION_LEAD' or ar['rule'] != original['rule']
            or ar['rule'] != payout_request['rule'] or ar['binding'] != original['binding']
            or ar['context'] != payout_request['context'] or ar['stage'] != payout_request['stage']
            or ar['binding']['bundle_sha256'] != original['bundle_sha256']
            or a['model_bundle_sha256'] != original['bundle_sha256']
            or any(ar['scope'][k] != payout_request['scope'][k] for k in
                ('station','family','source_rule_family','strategy','season','time_of_day'))
            or any(ar['binding'][k] != payout_request['binding'][k] for k in
                ('code_commit','code_tree','config_sha256','rule_fingerprint'))
            or not cutoff <= lead['body']['recorded_at'] <= pin['body']['recorded_at']
            or not inputs.at < min(finite(a['valid_until']), cutoff+policy.horizon_seconds)
            or ld['observation_window'] != dict(station=rule.payload['station'], population=rule.payload['observation_population'],
                window_start=cutoff, window_end=cutoff+policy.horizon_seconds, clock='FIRST_ALPHA_RECEIPT')):
        raise EvidenceError('REPLAY_PWS_SEPARATE_TARGET_CONTEXT')
    for admitted in (ar, payout_request):
        leases = {x['evidence_id']:x for x in admitted['source_leases']}
        if any(leases.get(original[k], {}).get('role') != role for k, role in
               (('official_id','OFFICIAL'), ('pws_id','PWS'))):
            raise EvidenceError('REPLAY_PWS_PAIRED_SOURCE_LEASES')
    if set(original['model_ids']) != {x['evidence_id'] for x in ar['source_leases'] if x['role']=='MODEL'}:
        raise EvidenceError('REPLAY_PWS_OBSERVATION_MODEL_LEASES')
    heads = _heads([*a['heads'], *payout_assessment['heads'], *ld['source_heads']])
    for kind, event, seq in heads:
        head = inputs.latest(kind=kind, event_id=event)
        if (head['seq'] if head else 0) != seq: raise EvidenceError('REPLAY_PWS_ORIGINAL_HEAD_CHANGED')
    obs_bundle, obs_model = historical_bundle(scope_key=scope.key, mode=payout_model['mode'],
        epoch=a['model_epoch'], state_sha256=a['model_state_sha256'], bundle_sha256=original['bundle_sha256'],
        at=observation['body']['recorded_at'], check=inputs.check)
    if (obs_bundle.payload['bundle']['target'] != NEXT_OBSERVATION
            or payout_bundle.payload['bundle']['target'] != 'FINAL_CONTRACT_PAYOUT'
            or obs_model['original_overlay']['size_multiplier'] != a['model_size_multiplier']):
        raise EvidenceError('REPLAY_PWS_SEPARATE_OBSERVATION_AND_PAYOUT_MODELS')
    if (before['lead_sha256'] != lead['sha256'] or before['lead_id'] != lead['id']
            or before['observation_admission_id'] != observation['id'] or before['payout_admission_id'] != request.admission_id
            or before['context'] != payout_request['context'] or before['rule'] != payout_request['rule']
            or before['binding'] != payout_request['binding'] or before['observation_prediction_sha256'] != digest(ld['with_pws'])
            or before['observation_bundle_sha256'] != obs_bundle.sha256 or before['payout_bundle_sha256'] != payout_bundle.sha256
            or before['model_states'] != [obs_model['state_sha256'], payout_model['state_sha256']]
            or canonical(before['heads']) != canonical(heads)
            or before['model_size_multiplier'] != min(a['model_size_multiplier'], payout_assessment['model_size_multiplier'])):
        raise EvidenceError('REPLAY_PWS_ORIGINAL_MODEL_PAIR_BINDING')
    inputs.check(); without = model_registry.ApprovedArtifactReader().pin(original['without_pws_bundle_sha256']); inputs.check()
    if (ld['artifact_refs'] != dict(with_pws=obs_bundle.payload['bundle']['artifacts'],
                                  without_pws=without.payload['bundle']['artifacts'])):
        raise EvidenceError('REPLAY_PWS_ORIGINAL_ABLATION_ARTIFACTS')
    lead_inputs = HistoricalView(inputs._view, through_seq=lead['seq']-1, at=cutoff)
    for kind, event, seq in ld['source_heads']:
        head = lead_inputs.latest(kind=kind, event_id=event)
        if (head['seq'] if head else 0) != seq: raise EvidenceError('REPLAY_PWS_ORIGINAL_LEAD_HEAD_CHANGED')
    official = lead_inputs.get(original['official_id']); _report(official, rule)
    if ld['official_anchor_observed_at'] != official['body']['observed_at']:
        raise EvidenceError('REPLAY_PWS_ORIGINAL_OFFICIAL_ANCHOR')
    ids = tuple(dict.fromkeys((original['official_id'],original['pws_id'],*original['model_ids'],*original['without_pws_model_ids'])))
    roots = [lead_inputs.get(k) for k in ids]
    derivation = source_derivation(lead_inputs, roots, event_id=lead['event_id'], cutoff=cutoff)
    paired = paired_inference(lead_inputs, rule=rule, policy=policy, official=official, pws_id=original['pws_id'],
        model_ids=tuple(original['model_ids']), without_pws_model_ids=tuple(original['without_pws_model_ids']),
        bundle=obs_bundle, without_pws_bundle=without, cutoff=cutoff)
    comparisons = {k:canonical(paired[k])==canonical(ld[k]) for k in ('with_pws','without_pws','paired_provenance')}
    inputs.check()
    return dict(lead_ref=_ref(lead), preconfirmation_ref=_ref(pin), observation_admission_ref=_ref(observation),
        observation_model=obs_model, payout_bundle_sha256=payout_bundle.sha256,
        without_pws_bundle_sha256=without.sha256, without_pws_bundle_role='RESEARCH_ABLATION_NOT_CHAMPION_APPROVAL',
        comparisons=comparisons, feature_ready_at=cutoff, input_boundary_seq=lead['seq']-1,
        observation_window=ld['observation_window'], input_refs=[_ref(r) for r in roots],
        source_derivation_sha256=derivation['sha256'] if derivation else None,
        recomputed_with_pws=paired['with_pws'], recomputed_without_pws=paired['without_pws'],
        later_official_reports_used=False, label_or_outcome_replayed=False, lead_advantage_verified=False,
        observation_is_payout_or_exit=False, admission_authority=False, financial_authority=False)


def replay_temperature(c, evaluation_id, *, policy, monotonic=time.monotonic, deadline=None):
    if not isinstance(policy, ReplayPolicy) or c.store.namespace != 'V11_PAPER':
        raise EvidenceError('REPLAY_PAPER_POLICY_REQUIRED')
    identity(evaluation_id)
    result = dict(version=VERSION, policy=asdict(policy), evaluation_id=evaluation_id, status='GATED', reason=None,
        economic_match=False, comparisons={}, account=None, financial_authority=False, admission_authority=False,
        historical_executable_attested=False, full_control_flow_replayed=False,
        new_economic_commands=0, source_truth_independently_attested=False)
    try:
        stop = monotonic()+policy.maximum_seconds
        if deadline is not None: stop = min(stop, finite(deadline))
        with learning_source_view(c.store, deadline=stop, monotonic=monotonic) as source:
            row = source.get(evaluation_id); d = row['body'].get('details', {})
            if row['kind'] != 'MEASUREMENT' or d.get('version') != STRATEGY_VERSION:
                raise EvidenceError('REPLAY_TEMPERATURE_DECISION_REQUIRED')
            result.update(decision_ref=_ref(row), decision_recorded_at=row['body']['recorded_at'], original_outcome=d['outcome'], original_reason=d['reason'],
                          original_binding=d['binding'])
            if d['strategy'] not in SUPPORTED: raise EvidenceError('REPLAY_STRATEGY_JOIN_NOT_IMPLEMENTED')
            request = _request(d['request']); start = source.get(evaluation_id+':start')
            cutoff = finite(d['evaluation_started_at']); sd = start['body']['details']
            if (start['kind'] != 'MEASUREMENT' or start['event_id'] != row['event_id'] or start['seq'] >= row['seq']
                    or sd.get('version') != STRATEGY_VERSION or sd.get('stage') != 'EVALUATION_STARTED'
                    or sd.get('request') != d['request'] or sd.get('request_sha256') != digest(asdict(request))
                    or d['request_sha256'] != sd['request_sha256'] or start['body']['recorded_at'] != cutoff
                    or row['body']['recorded_at'] < cutoff):
                raise EvidenceError('REPLAY_ORIGINAL_REQUEST_OR_START')
            inputs = HistoricalView(source, through_seq=start['seq'], at=cutoff)
            admission = inputs.get(request.admission_id); ad = admission['body']['details']; ar = ad['request']; a = ad['assessment']
            scope = CapabilityScope(**ar['scope']); rule = RuleFingerprint(**ar['rule']); binding = ReleaseBinding(**ar['binding'])
            if (admission['kind'] != 'REGISTRY' or ad.get('version') != ADMISSION_VERSION
                    or admission['seq'] >= start['seq'] or ar['binding'] != d['binding'] or ar['scope'] != d['scope']
                    or scope.strategy != d['strategy'] or rule.payload['event_id'] != row['event_id']
                    or ar['context']['account_id'] != c.policy.account_id or ar['context']['event_id'] != row['event_id']
                    or ar['stage'] not in {'PAPER','SHADOW'} or not cutoff < min(finite(a['valid_until']),request.expires_at)):
                raise EvidenceError('REPLAY_ORIGINAL_ADMISSION_BINDING')
            if d.get('prediction') is None or d.get('valuation') is None:
                raise EvidenceError('REPLAY_EARLY_CONTROL_GATE_NOT_RECOMPUTED')
            for kind, event, seq in a['heads']:
                head = inputs.latest(kind=kind, event_id=event)
                if (head['seq'] if head else 0) != seq: raise EvidenceError('REPLAY_ORIGINAL_ADMISSION_HEAD_CHANGED')
            leases = {x['evidence_id']:x for x in ar['source_leases']}
            if set(request.model_input_ids) != {k for k,v in leases.items() if v['role']=='MODEL'}:
                raise EvidenceError('REPLAY_ORIGINAL_MODEL_LEASES')
            roots = [inputs.get(x) for x in dict.fromkeys((*leases,request.book_id))]
            derivation = source_derivation(inputs, roots, event_id=row['event_id'], cutoff=cutoff)
            inference_cutoff = cutoff if scope.strategy=='FUTURE_FORECAST' else finite(inputs.get(request.coverage_input_id)['body']['payload']['as_of'])
            if inference_cutoff > cutoff or inference_cutoff != d['inference_cutoff']:
                raise EvidenceError('REPLAY_ORIGINAL_INFERENCE_CUTOFF')
            if d.get('model_epoch') != a['model_epoch'] or a['model_bundle_sha256'] != binding.bundle_sha256:
                raise EvidenceError('REPLAY_ORIGINAL_MODEL_BINDING')
            bundle, model = historical_bundle(scope_key=scope.key, mode='V11_PAPER' if ar['stage']=='PAPER' else 'V11_SHADOW',
                epoch=a['model_epoch'], state_sha256=a['model_state_sha256'], bundle_sha256=binding.bundle_sha256,
                at=admission['body']['recorded_at'], check=source.check)
            if (d['artifact_refs'] != bundle.payload['bundle']['artifacts']
                    or model['original_overlay']['size_multiplier'] != a['model_size_multiplier']):
                raise EvidenceError('REPLAY_ORIGINAL_ARTIFACT_OR_OVERLAY_BINDING')
            pws_pair = _pws_pair(inputs, request=request, decision=d, payout_request=ar, payout_assessment=a,
                payout_model=model, payout_bundle=bundle) if scope.strategy=='PWS_OBSERVATION_LEAD' else None
            components, observed, coverage = temperature_inputs(inputs, rule, request, strategy=scope.strategy,
                cutoff=cutoff, inference_cutoff=inference_cutoff)
            prediction = predict_with_bundle(bundle, rule, components, as_of=inference_cutoff,
                max_source_age_seconds=min(leases[k]['maximum_age_seconds'] for k in request.model_input_ids),
                observed=observed, remaining_coverage=coverage)
            value_row = source.get(evaluation_id+':valuation'); value = value_row['body']['details']
            at = finite(value_row['body']['recorded_at'])
            if (value_row['kind'] != 'MEASUREMENT' or value_row['event_id'] != row['event_id']
                    or not start['seq'] < value_row['seq'] < row['seq'] or not cutoff <= at <= row['body']['recorded_at']
                    or canonical(value) != canonical(d['valuation']) or value['as_of'] != at):
                raise EvidenceError('REPLAY_ORIGINAL_VALUATION_BINDING')
            valuation_view = HistoricalView(source, through_seq=value_row['seq']-1, at=at)
            measured = settlement_entry_details(valuation_view, rule=rule, prediction=prediction, binding=binding,
                market_id=request.market_id, side=request.side, units=request.units, book_id=request.book_id,
                policy=request.valuation_policy, costs=request.costs)
            comparisons = dict(prediction=canonical(prediction.payload)==canonical(d['prediction']),
                valuation=canonical(measured)==canonical(value), economic_outcome=measured['outcome']==value['outcome'],
                economic_reasons=measured['reasons']==value['reasons'])
            if pws_pair is not None:
                comparisons.update({'pws_'+key:matched for key,matched in pws_pair['comparisons'].items()})
            account = _account(c, inputs); source.check()
            result.update(status='ECONOMICS_REPRODUCED' if all(comparisons.values()) else 'MISMATCH',
                reason='SHARED_NUMERIC_ENGINE_HISTORICAL_INPUTS_CONTROL_AUTHORITY_NOT_REPLAYED',
                economic_match=all(comparisons.values()), comparisons=comparisons, account=account, model=model,
                cutoff=cutoff, inference_cutoff=inference_cutoff, input_boundary_seq=start['seq'],
                valuation_at=at, valuation_ref=_ref(value_row), input_refs=[_ref(x) for x in roots],
                source_derivation_sha256=derivation['sha256'] if derivation else None,
                recomputed_prediction=prediction.payload, recomputed_valuation=measured,
                proposal_recreated=False, orders_or_fills_inferred=False)
            if pws_pair is not None: result['pws_observation'] = pws_pair
            if len(canonical(result).encode()) > 512*1024: raise EvidenceError('REPLAY_OUTPUT_BOUND')
            return result
    except (EvidenceError, KeyError, TypeError, ValueError, InvalidOperation, OSError) as exc:
        result.update(status='GATED', reason=str(exc) if isinstance(exc,EvidenceError) else 'REPLAY_MALFORMED_OR_UNAVAILABLE_EVIDENCE',
            economic_match=False, comparisons={}, account=None)
        for key in ('recomputed_prediction','recomputed_valuation','model','input_refs','pws_observation'):
            result.pop(key,None)
        return result


AUDIT_MAX_DECISIONS = 8


def fold_replay_decisions(row, aggregate, window):
    """Retain all decision counts; never call a capped sample complete coverage."""
    d = row['body'].get('details', {})
    if (row['kind'] != 'MEASUREMENT' or d.get('version') != STRATEGY_VERSION
            or d.get('stage') == 'EVALUATION_STARTED'
            or not window['start'] <= row['body']['recorded_at'] < window['end']):
        return
    cohort = aggregate.setdefault('economic_replay_selection', dict(count=0, refs=[], overflow=False))
    cohort['count'] += 1
    if len(cohort['refs']) < AUDIT_MAX_DECISIONS: cohort['refs'].append(_ref(row))
    else: cohort['overflow'] = True


def replay_audit(c, *, selection, through_seq, window, archive_complete, policy, monotonic=time.monotonic):
    """One bounded complete retained cohort, for the separate audit worker."""
    if not isinstance(policy, ReplayPolicy): raise EvidenceError('REPLAY_POLICY_REQUIRED')
    result = dict(version=VERSION, policy=asdict(policy), window=window, through_seq=through_seq,
        retained_decision_count=selection['count'], status='GATED', reason=None, rows=[],
        complete_retained_selection=False, economic_matches=0, all_economics_reproduced=False,
        full_control_flow_replayed=False, historical_executable_attested=False,
        financial_authority=False, admission_authority=False)
    try:
        if (type(through_seq) is not int or through_seq < 0
                or not finite(window['start']) < finite(window['end'])
                or type(selection['count']) is not int or selection['count'] < 0
                or not archive_complete or selection['overflow'] or len(selection['refs']) != selection['count']
                or len(selection['refs']) > AUDIT_MAX_DECISIONS):
            raise EvidenceError('REPLAY_AUDIT_INCOMPLETE_OR_OVERFLOW')
        deadline = monotonic()+policy.maximum_seconds
        seen = set()
        for ref in selection['refs']:
            if (type(ref) is not dict or set(ref) != {'id','sha256','seq'} or ref['id'] in seen
                    or type(ref['seq']) is not int or not 0 < ref['seq'] <= through_seq):
                raise EvidenceError('REPLAY_AUDIT_REFERENCE_BOUND')
            seen.add(ref['id']); sha(ref['sha256'])
            if monotonic() >= deadline: raise EvidenceError('REPLAY_AUDIT_TIME_BOUND')
            replayed = replay_temperature(c, ref['id'], policy=policy, monotonic=monotonic, deadline=deadline)
            if monotonic() >= deadline: raise EvidenceError('REPLAY_AUDIT_TIME_BOUND')
            if (replayed.get('decision_ref') != ref
                    or not window['start'] <= replayed.get('decision_recorded_at',-1) < window['end']):
                raise EvidenceError('REPLAY_AUDIT_DECISION_BINDING')
            # Compact immutable comparison evidence; do not duplicate whole model
            # vectors and account states in every daily/weekly report.
            row = {k:replayed.get(k) for k in ('decision_ref','status','reason','economic_match','comparisons',
                'original_outcome','original_reason','original_binding','model','cutoff','inference_cutoff',
                'input_boundary_seq','valuation_ref','source_derivation_sha256')}
            row.update(result_sha256=digest(replayed), account_snapshot_ref=(replayed.get('account') or {}).get('snapshot_ref'),
                account_context_status=(replayed.get('account') or {}).get('status','NOT_RECOMPUTED'),
                account_comparison_sha256=digest(replayed['account']) if replayed.get('account') else None)
            if replayed.get('pws_observation') is not None:
                pair = replayed['pws_observation']
                row['pws_observation'] = {k:pair[k] for k in ('lead_ref','preconfirmation_ref','observation_admission_ref',
                    'observation_model','payout_bundle_sha256','without_pws_bundle_sha256','without_pws_bundle_role',
                    'comparisons','feature_ready_at','input_boundary_seq','source_derivation_sha256',
                    'later_official_reports_used','label_or_outcome_replayed','lead_advantage_verified','observation_is_payout_or_exit')}
                row['pws_observation']['comparison_sha256'] = digest(pair)
            result['rows'].append(row)
        count = sum(r['economic_match'] for r in result['rows'])
        result.update(status='NO_RETAINED_DECISIONS' if not result['rows'] else 'ECONOMICS_REPRODUCED' if count==len(result['rows']) else 'PARTIAL',
            reason='RETAINED_TEMPERATURE_ECONOMICS_ONLY', complete_retained_selection=True,
            economic_matches=count, all_economics_reproduced=bool(result['rows']) and count==len(result['rows']))
        return result
    except (EvidenceError,KeyError,TypeError,ValueError) as exc:
        result.update(status='GATED',reason=str(exc) if isinstance(exc,EvidenceError) else 'REPLAY_AUDIT_MALFORMED_SELECTION',
            rows=[],complete_retained_selection=False,economic_matches=0,all_economics_reproduced=False)
        return result
