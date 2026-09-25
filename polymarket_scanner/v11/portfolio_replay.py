"""Original basket/exit numerical inputs for conditional account-effect replay.

Only original prepared candidates are selected, including candidates later
rejected by allocation. This does not reproduce preparation, rejected proposals,
admission, strategy selection, or an original executable. No journal writes or
current model/admission calls are made here.
"""
from dataclasses import asdict
from decimal import InvalidOperation

from .account_effects import EffectReplayError, ref
from .basket_coordinator import BasketProposal
from .basket_valuation import BasketLeg, BasketPolicy, basket_details, VERSION as BASKET_VERSION
from .causal_replay import HistoricalView, historical_bundle
from .certification import CapabilityScope
from .event_risk import EventContext
from .evidence import EvidenceError, ReleaseBinding, canonical, digest, finite
from .learning_sources import source_derivation
from .model_artifacts import predict_with_bundle
from .paper_coordinator import PaperCoordinator
from .position_management import ExitRequest, _inventory, _value, payout_inputs, VERSION as EXIT_VERSION
from .probability import FINAL_EXTREME
from .rules import RuleFingerprint
from .scenario_risk import Attribution, PendingOrder, Position
from .strategy_admission import SourceLease, VERSION as ADMISSION_VERSION
from .strategy_pipeline import _model_inputs
from .valuation import CostComponent, ValuationPolicy

VERSION = 'alpha_v11_prepared_portfolio_valuation_replay_v1'


def _admission(view, proposal, binding, cutoff):
    if len(proposal.admission_ids) != 1:
        raise EvidenceError('PORTFOLIO_REPLAY_EXACT_ORIGINAL_ADMISSION_REQUIRED')
    row = view.get(proposal.admission_ids[0]); d = row['body']['details']
    original, assessment = d['request'], d['assessment']
    scope = CapabilityScope(**original['scope'])
    if (row['kind'] != 'REGISTRY' or d.get('version') != ADMISSION_VERSION
            or EventContext(**original['context']) != proposal.context
            or RuleFingerprint(**original['rule']) != proposal.rule
            or original['binding'] != asdict(binding)
            or original['stage'] not in {'PAPER','SHADOW'} or view.namespace != 'V11_PAPER'
            or proposal.attribution != (Attribution(scope.strategy, '1'),)
            or not cutoff < min(finite(assessment['valid_until']), proposal.expires_at)
            or assessment['model_bundle_sha256'] != binding.bundle_sha256
            or assessment['financial_authority'] is not False):
        raise EvidenceError('PORTFOLIO_REPLAY_ORIGINAL_ADMISSION_BINDING')
    raw = original['source_leases']
    if type(raw) is not list or not 1 <= len(raw) <= 16:
        raise EvidenceError('PORTFOLIO_REPLAY_SOURCE_LEASE_BOUND')
    leases = {s.evidence_id:s for s in (SourceLease(**v) for v in raw)}
    if len(leases) != len(raw) or not any(s.role == 'MODEL' for s in leases.values()):
        raise EvidenceError('PORTFOLIO_REPLAY_ORIGINAL_MODEL_LEASES')
    if type(assessment['heads']) is not list or len(assessment['heads']) > 64:
        raise EvidenceError('PORTFOLIO_REPLAY_ADMISSION_HEAD_BOUND')
    for kind, event, seq in assessment['heads']:
        head = view.latest(kind=kind, event_id=event)
        if type(seq) is not int or (head['seq'] if head else 0) != seq:
            raise EvidenceError('PORTFOLIO_REPLAY_ORIGINAL_ADMISSION_HEAD_CHANGED')
    bundle, model = historical_bundle(scope_key=scope.key, mode='V11_PAPER' if original['stage']=='PAPER' else 'V11_SHADOW',
        epoch=assessment['model_epoch'], state_sha256=assessment['model_state_sha256'],
        bundle_sha256=binding.bundle_sha256, at=row['body']['recorded_at'], check=view.check)
    if model['original_overlay']['size_multiplier'] != assessment['model_size_multiplier']:
        raise EvidenceError('PORTFOLIO_REPLAY_ORIGINAL_MODEL_OVERLAY')
    return original, scope, leases, bundle, model, row


def _provenance(view, leases, books, event, cutoff):
    roots = [view.get(key) for key in dict.fromkeys((*leases, *books))]
    derivation = source_derivation(view, roots, event_id=event, cutoff=cutoff)
    return dict(input_refs=[ref(row) for row in roots],
                input_evidence_classes=sorted({row['body']['evidence_class'] for row in roots}),
                source_derivation_status='ARCHIVED_REFERENCE_GRAPH_VERIFIED' if derivation else 'NO_ARCHIVED_DERIVATION_EDGES',
                source_derivation_sha256=derivation['sha256'] if derivation else None)


def _basket(c, source, proposal, row):
    value = row['body']['details']; request = value['request']
    cutoff = finite(value['prediction']['as_of']); at = finite(value['as_of'])
    if (value.get('version') != BASKET_VERSION or value['request_sha256'] != digest(request)
            or request['rule'] != asdict(proposal.rule) or request['account_id'] != c.policy.account_id
            or request['binding'] != value['binding'] or not cutoff <= at <= row['body']['recorded_at']):
        raise EvidenceError('PORTFOLIO_REPLAY_ORIGINAL_BASKET_REQUEST')
    binding = ReleaseBinding(**request['binding'])
    inputs = HistoricalView(source, through_seq=row['seq']-1, at=cutoff)
    original, scope, leases, bundle, model, admission = _admission(inputs, proposal, binding, cutoff)
    if scope.strategy != request['strategy'] or value['strategy'] != scope.strategy:
        raise EvidenceError('PORTFOLIO_REPLAY_ORIGINAL_BASKET_STRATEGY')
    ids = tuple(k for k,s in leases.items() if s.role == 'MODEL')
    prediction = predict_with_bundle(bundle, proposal.rule,
        _model_inputs(inputs, proposal.rule, ids, cutoff, target=FINAL_EXTREME), as_of=cutoff,
        max_source_age_seconds=min(leases[k].maximum_age_seconds for k in ids))
    if (type(request['legs']) is not list or not 1 <= len(request['legs']) <= 32
            or type(request['positions']) is not list or type(request['pending']) is not list
            or len(request['positions'])+len(request['pending']) > 1000):
        raise EvidenceError('PORTFOLIO_REPLAY_ORIGINAL_BASKET_SIZE_BOUND')
    legs = tuple(BasketLeg(**{**leg, 'costs':tuple(CostComponent(**{**v, 'covers':tuple(v['covers'])})
                   for v in leg['costs'])}) for leg in request['legs'])
    policy = BasketPolicy(**{**request['policy'], 'valuation':ValuationPolicy(**request['policy']['valuation'])})
    def attrs(raw):
        return {**raw, 'attribution':tuple(Attribution(**a) for a in raw['attribution'])}
    view = HistoricalView(source, through_seq=row['seq']-1, at=at)
    measured = basket_details(view, row['id'], rule=proposal.rule, prediction=prediction, binding=binding,
        strategy=scope.strategy, account_id=c.policy.account_id, legs=legs, policy=policy,
        positions=tuple(Position(**attrs(p)) for p in request['positions']),
        pending=tuple(PendingOrder(**attrs(p)) for p in request['pending']),
        realized_event_pnl=request['realized_event_pnl'])
    # Books may be received after model inference, but before original valuation.
    provenance = _provenance(inputs, leases, (), proposal.context.event_id, cutoff)
    books = _provenance(view, (), tuple(l.book_id for l in legs), proposal.context.event_id, at)
    return measured, prediction.payload, dict(model=model, admission_ref=ref(admission),
        cutoff=cutoff, inference_cutoff=cutoff, input_boundary_seq=inputs.through_seq, valuation_at=at,
        **provenance, book_derivation=books,
        inventory_basis='ORIGINAL_DECLARED_BASKET_SCENARIO_INPUTS_NOT_ACCOUNT_HOLDINGS',
        inventory_inputs_sha256=digest({k:request[k] for k in ('positions','pending','realized_event_pnl')}))


def _exit(c, source, proposal, row, before_state):
    value = row['body']['details']; saved = value['position_management']
    request = ExitRequest.from_dict(saved['request'])
    if (saved.get('version') != EXIT_VERSION or saved['request_sha256'] != digest(asdict(request))
            or not row['id'].endswith(':valuation') or proposal.admission_ids != (request.admission_id,)
            or proposal.event_state_id != request.event_state_id
            or proposal.preconfirmation_id != request.preconfirmation_id or proposal.source_release_id != request.source_release_id
            or proposal.expires_at > request.expires_at):
        raise EvidenceError('PORTFOLIO_REPLAY_ORIGINAL_EXIT_REQUEST')
    start = source.get(row['id'][:-len(':valuation')]+':start'); sd = start['body']['details']
    cutoff = finite(start['body']['recorded_at'])
    if (start['kind'] != 'MEASUREMENT' or start['event_id'] != row['event_id'] or start['seq'] >= row['seq']
            or sd.get('version') != EXIT_VERSION or sd['request_sha256'] != saved['request_sha256']
            or cutoff != value['as_of'] or cutoff > row['body']['recorded_at']):
        raise EvidenceError('PORTFOLIO_REPLAY_ORIGINAL_EXIT_START')
    inputs = HistoricalView(source, through_seq=start['seq'], at=cutoff)
    binding = ReleaseBinding(**value['binding'])
    original, scope, leases, bundle, model, admission = _admission(inputs, proposal, binding, cutoff)
    historical = PaperCoordinator(inputs, policy=c.policy, correlation=c.correlation, limits=c.limits)
    head = historical._head()
    if head is None or head['id'] != sd['account_head_id'] or historical.policy_sha != saved['account_policy_sha256']:
        raise EvidenceError('PORTFOLIO_REPLAY_ORIGINAL_EXIT_ACCOUNT')
    state = historical._state(head)
    if (head['body']['details']['policy_sha256'] != historical.policy_sha
            or _inventory(historical, state, proposal.rule)[1] != saved['inventory_sha256']
            or _inventory(historical, before_state, proposal.rule)[1] != saved['inventory_sha256']):
        raise EvidenceError('PORTFOLIO_REPLAY_ORIGINAL_EXIT_INVENTORY')
    components, observed, coverage, inference_at, age = payout_inputs(inputs, request, original, proposal.rule, cutoff)
    prediction = predict_with_bundle(bundle, proposal.rule, components, as_of=inference_at,
        max_source_age_seconds=age, observed=observed, remaining_coverage=coverage)
    measured = _value(historical, state, request, proposal.rule, binding, prediction, cutoff)
    provenance = _provenance(inputs, leases, (request.book_id,), proposal.context.event_id, cutoff)
    return measured, prediction.payload, dict(model=model, admission_ref=ref(admission), start_ref=ref(start),
        cutoff=cutoff, inference_cutoff=inference_at, input_boundary_seq=inputs.through_seq, valuation_at=cutoff,
        **provenance, inventory_basis='ORIGINAL_PRE_EVALUATION_ACCOUNT_SNAPSHOT_LOTS_ALSO_BOUND_TO_PREPARATION',
        inventory_ref=ref(head), inventory_inputs_sha256=saved['inventory_sha256'])


def prepared_valuations(c, source, *, command, before_state, proposals, preparation):
    """Same source snapshot/deadline as the enclosing account command replay."""
    by_id = {p.proposal_id:p for p in proposals}; rows = []
    for prepared in preparation['prepared']:
        source.check(); proposal = by_id[prepared['proposal_id']]
        result = dict(proposal_id=proposal.proposal_id, proposal_sha256=digest(asdict(proposal)),
            valuation_id=proposal.valuation_id, status='GATED', reason=None, economic_match=False, comparisons={})
        try:
            row = source.get(proposal.valuation_id); value = row['body']['details']
            if (row['kind'] != 'MEASUREMENT' or row['event_id'] != proposal.context.event_id
                    or proposal.context.account_id != c.policy.account_id or row['seq'] >= command['seq']
                    or row['body']['recorded_at'] > command['body']['recorded_at']):
                raise EvidenceError('PORTFOLIO_REPLAY_ORIGINAL_VALUATION_BINDING')
            if isinstance(proposal, BasketProposal):
                measured, prediction, metadata = _basket(c, source, proposal, row)
                original_prediction = value['prediction']
            elif prepared['direction'] == 'SELL':
                measured, prediction, metadata = _exit(c, source, proposal, row, before_state)
                original_prediction = value['model']['prediction']
            else:
                raise EvidenceError('PORTFOLIO_REPLAY_PREPARED_VALUATION_NOT_IMPLEMENTED')
            comparisons = dict(prediction=canonical(prediction)==canonical(original_prediction),
                valuation=canonical(measured)==canonical(value), outcome=measured['outcome']==value['outcome'],
                reasons=canonical(measured['reasons'])==canonical(value['reasons']))
            result.update(status='ECONOMICS_REPRODUCED' if all(comparisons.values()) else 'MISMATCH',
                reason='SHARED_NUMERICAL_VALUATION_ORIGINAL_EVIDENCE', economic_match=all(comparisons.values()),
                comparisons=comparisons, valuation_ref=ref(row), original_valuation_sha256=digest(value),
                recomputed_valuation_sha256=digest(measured), original_prediction_sha256=digest(original_prediction),
                recomputed_prediction_sha256=digest(prediction), **metadata)
        except (EvidenceError, EffectReplayError, KeyError, TypeError, ValueError, InvalidOperation, OSError) as exc:
            result['reason'] = str(exc) if isinstance(exc,(EvidenceError,EffectReplayError)) else 'PORTFOLIO_REPLAY_MALFORMED_OR_UNAVAILABLE_EVIDENCE'
        rows.append(result); source.check()
    result = dict(version=VERSION, prepared_count=len(rows), rejected_preparation_count=len(preparation['rejected']),
        complete_prepared_selection=True, economic_matches=sum(row['economic_match'] for row in rows), rows=rows,
        all_prepared_valuations_reproduced=bool(rows) and all(row['economic_match'] for row in rows),
        financial_authority=False, admission_authority=False, full_preparation_replayed=False,
        source_truth_independently_attested=False, historical_executable_attested=False, new_economic_commands=0)
    if len(canonical(result).encode()) > 256*1024:
        raise EvidenceError('PORTFOLIO_REPLAY_OUTPUT_BOUND')
    return result
