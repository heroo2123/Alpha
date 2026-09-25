"""Bounded read-only comparison of original PAPER account numerical effects.

Prepared candidates and exit control results are original conditional inputs,
not recomputed strategy/admission decisions. No account command is invoked.
Legacy missing inputs and unsupported commands remain explicit unknowns.
"""
from copy import deepcopy
from dataclasses import asdict
from decimal import InvalidOperation
import time

from .account_effects import EffectInputs, EffectReplayError, VERSION as INPUT_VERSION, ref
from .basket_coordinator import BasketProposal
from .causal_replay import HistoricalView, ReplayPolicy
from .event_risk import EventContext
from .evidence import AUDIT_KINDS, KINDS, EvidenceError, canonical, digest, finite, identity, sha
from .learning_sources import learning_source_view
from .paper_coordinator import ACCOUNT_KEY, PaperCoordinator, Proposal, VERSION as ACCOUNT_VERSION
from .rules import RuleFingerprint
from .scenario_risk import Attribution

VERSION = 'alpha_v11_paper_account_effect_replay_v1'
SUPPORTED = {'COORDINATE', 'TRANSITION', 'RECOVER', 'FILL', 'TERMINAL'}
AUDIT_MAX_COMMANDS = 32


class AccountHistoricalView(HistoricalView):
    def get(self, key):
        try:
            return super().get(key)
        except EvidenceError as exc:
            # Numeric runtime branches may deliberately record invalid optional
            # telemetry or reject a candidate. Missing replay receipts/deadlines
            # must instead gate the entire comparison, never imitate that result.
            raise EffectReplayError(str(exc)) from exc


def _proposals(request, preparation):
    raw = request['proposals']
    if (type(raw) is not list or not 1 <= len(raw) <= 6 or type(preparation) is not dict
            or set(preparation) != {'prepared', 'rejected'}
            or any(type(preparation[k]) is not list or len(preparation[k]) > 6 for k in preparation)):
        raise EvidenceError('ACCOUNT_REPLAY_COMPLETE_PREPARATION_REQUIRED')
    proposals = []
    for value in raw:
        if 'desired_positions' in value:
            proposal = BasketProposal.from_dict(value)
        else:
            p = deepcopy(value)
            p.update(context=EventContext(**p['context']), rule=RuleFingerprint(**p['rule']),
                attribution=tuple(Attribution(**a) for a in p['attribution']), admission_ids=tuple(p['admission_ids']))
            proposal = Proposal(**p)
        proposals.append(proposal)
    by_id = {p.proposal_id:p for p in proposals}
    consumed = []
    for candidate in preparation['prepared']:
        p = by_id[candidate['proposal_id']]
        if (candidate['thesis_id'] != p.thesis_id or candidate['event_id'] != p.context.event_id
                or candidate['financial_authority'] is not False):
            raise EvidenceError('ACCOUNT_REPLAY_PREPARATION_REQUEST_BINDING')
        legs = candidate.get('basket_legs', [candidate])
        if (not 1 <= len(legs) <= 32 or ('basket_legs' in candidate) != isinstance(p, BasketProposal)):
            raise EvidenceError('ACCOUNT_REPLAY_PREPARATION_LEG_BOUND')
        for leg in legs:
            if (leg['valuation_id'] != p.valuation_id or leg['event_state_id'] != p.event_state_id
                    or canonical(leg['attribution']) != canonical(asdict(p)['attribution'])
                    or leg['financial_authority'] is not False or leg['status'] != 'RESERVED'
                    or leg['filled_units'] != '0' or leg['cancel_requested'] is not False):
                raise EvidenceError('ACCOUNT_REPLAY_PREPARATION_REQUEST_BINDING')
        consumed.append(p.proposal_id)
    for rejection in preparation['rejected']:
        if (set(rejection) != {'proposal_id','outcome','reason'} or rejection['outcome'] != 'REJECT'
                or not isinstance(rejection['reason'], str) or not rejection['reason']):
            raise EvidenceError('ACCOUNT_REPLAY_PREPARATION_REJECTION_REQUIRED')
        consumed.append(rejection['proposal_id'])
    if len(by_id) != len(proposals) or len(consumed) != len(by_id) or set(consumed) != set(by_id):
        raise EvidenceError('ACCOUNT_REPLAY_COMPLETE_PREPARATION_REQUIRED')
    return tuple(proposals)


def _request(request):
    action = request['action']
    required = dict(COORDINATE={'proposals'}, TRANSITION={'intent_id','status'}, RECOVER=set(),
                    FILL={'evidence_id'}, TERMINAL={'evidence_id'})[action] | {'action'}
    if action == 'TRANSITION' and 'expected_cancel_identity' in request:
        required.add('expected_cancel_identity'); sha(request['expected_cancel_identity'])
    if set(request) != required:
        raise EvidenceError('ACCOUNT_REPLAY_REQUEST_SCHEMA')


def _inputs(view, row, before, state, request):
    d = row['body']['details']; inputs = d.get('effect_inputs')
    if inputs is None:
        raise EvidenceError('ACCOUNT_REPLAY_ORIGINAL_EFFECT_INPUTS_MISSING')
    fields = {'version','before_ref','before_state_sha256','request_sha256','times','exit_checks',
              'preparation','expected_heads','receipt_seq','control_flow_replayed','financial_authority'}
    if (type(inputs) is not dict or set(inputs) != fields or inputs['version'] != INPUT_VERSION
            or inputs['before_ref'] != ref(before) or inputs['before_state_sha256'] != digest(state)
            or inputs['request_sha256'] != digest(request) or inputs['financial_authority'] is not False
            or inputs['control_flow_replayed'] is not False):
        raise EvidenceError('ACCOUNT_REPLAY_ORIGINAL_INPUT_BINDING')
    if (type(inputs['times']) is not list or not 1 <= len(inputs['times']) <= 64
            or type(inputs['exit_checks']) is not list or len(inputs['exit_checks']) > 6
            or type(inputs['expected_heads']) is not list or len(inputs['expected_heads']) > 64
            or len(canonical(inputs).encode()) > 512*1024):
        raise EvidenceError('ACCOUNT_REPLAY_INPUT_BOUND')
    for at in inputs['times']: finite(at)
    if request['action'] != 'COORDINATE' and (inputs['preparation'] is not None or inputs['exit_checks']):
        raise EvidenceError('ACCOUNT_REPLAY_UNEXPECTED_PREPARATION')
    receipt = inputs['receipt_seq']
    if receipt is not None and (type(receipt) is not int or not 0 <= receipt < row['seq']):
        raise EvidenceError('ACCOUNT_REPLAY_RECEIPT_BOUND')
    seen = set(); guards = []
    for kind, event, seq in inputs['expected_heads']:
        if kind not in AUDIT_KINDS | KINDS or type(seq) is not int or not 0 <= seq < row['seq'] or (kind,event) in seen:
            raise EvidenceError('ACCOUNT_REPLAY_GUARDED_HEAD_BOUND')
        seen.add((kind,event)); head = view.latest(kind=kind, event_id=event)
        if (head['seq'] if head else 0) != seq:
            raise EvidenceError('ACCOUNT_REPLAY_ORIGINAL_GUARDED_HEAD_MISSING')
        if head: guards.append(ref(head))
    return inputs, guards


def replay_account_command(c, command_id, *, policy, monotonic=time.monotonic, deadline=None, replay_valuations=False):
    if not isinstance(policy, ReplayPolicy) or c.store.namespace != 'V11_PAPER':
        raise EvidenceError('ACCOUNT_REPLAY_PAPER_POLICY_REQUIRED')
    if type(replay_valuations) is not bool:
        raise EvidenceError('ACCOUNT_REPLAY_VALUATION_OPTION_REQUIRED')
    identity(command_id)
    result = dict(version=VERSION, policy=asdict(policy), command_id=command_id, status='GATED', reason=None,
        effects_match=False, comparisons={}, financial_authority=False, admission_authority=False,
        full_control_flow_replayed=False, preparation_recomputed=False, historical_executable_attested=False,
        source_truth_independently_attested=False, new_economic_commands=0,
        scope='NUMERICAL_EFFECTS_CONDITIONAL_ON_ORIGINAL_PREPARATION_AND_CONTROL_INPUTS')
    try:
        stop = monotonic()+policy.maximum_seconds
        if deadline is not None: stop = min(stop, finite(deadline))
        with learning_source_view(c.store, deadline=stop, monotonic=monotonic) as source:
            row = source.get(command_id); d = row['body'].get('details', {})
            if row['kind'] != 'COORDINATOR_EVENT' or row['event_id'] != ACCOUNT_KEY:
                raise EvidenceError('ACCOUNT_REPLAY_COMMAND_REQUIRED')
            if type(d) is not dict or type(d.get('request')) is not dict:
                raise EvidenceError('ACCOUNT_REPLAY_REQUEST_SCHEMA')
            result.update(command_ref=ref(row), command_recorded_at=row['body']['recorded_at'], action=d.get('request',{}).get('action'))
            if result['action'] not in SUPPORTED:
                raise EvidenceError('ACCOUNT_REPLAY_ACTION_NOT_IMPLEMENTED')
            if d.get('version') != ACCOUNT_VERSION or d.get('policy_sha256') != c.policy_sha:
                raise EvidenceError('ACCOUNT_REPLAY_ORIGINAL_POLICY_REQUIRED')
            request = d['request']; _request(request)
            view = AccountHistoricalView(source, through_seq=row['seq']-1, at=row['body']['recorded_at'])
            historical = PaperCoordinator(view, policy=c.policy, correlation=c.correlation, limits=c.limits)
            if historical.policy_sha != d['policy_sha256']:
                raise EvidenceError('ACCOUNT_REPLAY_ORIGINAL_POLICY_REQUIRED')
            before = historical._head(); state = historical._state(before)
            if (state['account_id'] != c.policy.account_id or state['execution_namespace'] != c.store.namespace
                    or state['financial_authority'] is not False
                    or any(len(state[k]) > maximum for k, maximum in
                        [('rules',32),('intents',512),('fills',2048),('lots',512)])
                    or len(state.get('baskets',{})) > 128):
                raise EvidenceError('ACCOUNT_REPLAY_ORIGINAL_STATE_BOUND')
            inputs, guards = _inputs(view, row, before, state, request)
            effects = EffectInputs(historical, state, original=inputs,
                earliest=before['body']['recorded_at'] if before else min(inputs['times']), latest=view.at)
            refs = []
            for original in row['body']['evidence']:
                evidence = view.get(original['id'])
                if evidence['sha256'] != original['sha256']:
                    raise EvidenceError('ACCOUNT_REPLAY_ORIGINAL_EVIDENCE_HASH')
                refs.append(ref(evidence))
            action = request['action']
            if action == 'COORDINATE':
                preparation = deepcopy(inputs['preparation'])
                proposals = _proposals(request, preparation)
                # These are original prepared numeric candidates, never inferred
                # from the final ranking, post-state or reservation outcomes.
                prepared_ids = {p['proposal_id'] for p in preparation['prepared']}
                for proposal in proposals:
                    if proposal.proposal_id in prepared_ids:
                        view.get(proposal.valuation_id); view.get(proposal.event_state_id)
                if replay_valuations:
                    from .portfolio_replay import prepared_valuations
                    result['prepared_valuations'] = prepared_valuations(historical, source, command=row,
                        before_state=state, proposals=proposals, preparation=preparation)
                state, calculated = historical._coordinate_effects(state, proposals,
                    preparation['prepared'], preparation['rejected'], effects)
            elif action == 'TRANSITION':
                calculated = historical._transition_effects(state, request, effects)
            elif action == 'RECOVER':
                calculated = historical._recover_effects(state, effects)
            elif action == 'FILL':
                calculated, _ = historical._fill_effects(state, request['evidence_id'], effects)
            else:
                calculated = historical._terminal_effects(state, request['evidence_id'], effects)
            effects.finish(); source.check()
            original = {k:v for k,v in d.items() if k not in {'version','policy_sha256','request','state','effect_inputs'}}
            comparisons = dict(state=canonical(state)==canonical(d['state']), effects=canonical(calculated)==canonical(original))
            for k in ('cash','intents','lots','fills','event_realized_pnl','realized_entries','faults'):
                comparisons[k] = canonical(state[k]) == canonical(d['state'][k])
            comparisons['risk'] = canonical(calculated['risk']) == canonical(d['risk'])
            result.update(status='EFFECTS_REPRODUCED' if all(comparisons.values()) else 'MISMATCH',
                reason='SHARED_NUMERICAL_ACCOUNT_EFFECTS_ORIGINAL_CONDITIONAL_INPUTS',
                effects_match=all(comparisons.values()), comparisons=comparisons, before_ref=ref(before),
                original_policy_sha256=c.policy_sha, input_boundary_seq=view.through_seq,
                effect_inputs_sha256=digest(inputs), input_refs=refs, original_guard_refs=guards,
                original_state_sha256=digest(d['state']), recomputed_state_sha256=digest(state),
                original_effects_sha256=digest(original), recomputed_effects_sha256=digest(calculated))
            source.check()
            return result
    except (EvidenceError, EffectReplayError, KeyError, TypeError, ValueError, InvalidOperation, OSError) as exc:
        result.update(status='GATED', reason=str(exc) if isinstance(exc,(EvidenceError,EffectReplayError)) else
                      'ACCOUNT_REPLAY_MALFORMED_OR_UNAVAILABLE_EVIDENCE', effects_match=False, comparisons={})
        for key in ('before_ref','original_policy_sha256','input_boundary_seq','effect_inputs_sha256','input_refs',
                    'original_guard_refs','original_state_sha256','recomputed_state_sha256',
                    'original_effects_sha256','recomputed_effects_sha256','prepared_valuations'):
            result.pop(key, None)
        return result


def fold_account_commands(row, aggregate, window):
    """Count every account command, including unsupported and legacy records."""
    if (row['kind'] != 'COORDINATOR_EVENT' or row['event_id'] != ACCOUNT_KEY
            or not window['start'] <= row['body']['recorded_at'] < window['end']):
        return
    cohort = aggregate.setdefault('account_replay_selection', dict(count=0, refs=[], overflow=False))
    cohort['count'] += 1
    if len(cohort['refs']) < AUDIT_MAX_COMMANDS: cohort['refs'].append(ref(row))
    else: cohort['overflow'] = True


def account_replay_audit(c, *, selection, through_seq, window, archive_complete, policy, monotonic=time.monotonic,
                         replay_valuations=False):
    """Complete retained cohort or an explicit gate, never a favorable prefix."""
    if not isinstance(policy, ReplayPolicy): raise EvidenceError('ACCOUNT_REPLAY_POLICY_REQUIRED')
    if type(replay_valuations) is not bool: raise EvidenceError('ACCOUNT_REPLAY_VALUATION_OPTION_REQUIRED')
    result = dict(version=VERSION, policy=asdict(policy), window=window, through_seq=through_seq,
        retained_command_count=selection.get('count'), status='GATED', reason=None, rows=[],
        complete_retained_selection=False, effect_matches=0, all_effects_reproduced=False,
        full_control_flow_replayed=False, preparation_recomputed=False, historical_executable_attested=False,
        financial_authority=False, admission_authority=False, new_economic_commands=0)
    if replay_valuations:
        result['prepared_valuation_coverage'] = dict(complete=False, prepared_count=None, economic_matches=0,
            rejected_preparation_count=None, all_prepared_valuations_reproduced=False,
            scope='ORIGINAL_PREPARED_CANDIDATES_ONLY_FULL_PREPARATION_AND_CONTROLS_UNVERIFIED')
    try:
        if (type(through_seq) is not int or through_seq < 0
                or not finite(window['start']) < finite(window['end'])
                or type(selection['count']) is not int or selection['count'] < 0
                or not archive_complete or selection['overflow'] or len(selection['refs']) != selection['count']
                or len(selection['refs']) > AUDIT_MAX_COMMANDS):
            raise EvidenceError('ACCOUNT_REPLAY_AUDIT_INCOMPLETE_OR_OVERFLOW')
        deadline = monotonic()+policy.maximum_seconds
        seen = set()
        for original in selection['refs']:
            if (type(original) is not dict or set(original) != {'id','sha256','seq'} or original['id'] in seen
                    or type(original['seq']) is not int or not 0 < original['seq'] <= through_seq):
                raise EvidenceError('ACCOUNT_REPLAY_AUDIT_REFERENCE_BOUND')
            seen.add(original['id']); sha(original['sha256'])
            if monotonic() >= deadline: raise EvidenceError('ACCOUNT_REPLAY_AUDIT_TIME_BOUND')
            replayed = replay_account_command(c, original['id'], policy=policy, monotonic=monotonic, deadline=deadline,
                                               replay_valuations=replay_valuations)
            if monotonic() >= deadline: raise EvidenceError('ACCOUNT_REPLAY_AUDIT_TIME_BOUND')
            if (replayed.get('command_ref') != original
                    or not window['start'] <= replayed.get('command_recorded_at',-1) < window['end']):
                raise EvidenceError('ACCOUNT_REPLAY_AUDIT_COMMAND_BINDING')
            row = {k:replayed.get(k) for k in ('command_ref','action','status','reason','effects_match','comparisons',
                'before_ref','original_policy_sha256','input_boundary_seq','effect_inputs_sha256',
                'original_state_sha256','recomputed_state_sha256','original_effects_sha256','recomputed_effects_sha256')}
            row['result_sha256'] = digest(replayed)
            if replay_valuations: row['prepared_valuations'] = replayed.get('prepared_valuations')
            result['rows'].append(row)
        count = sum(r['effects_match'] for r in result['rows'])
        result.update(status='NO_RETAINED_COMMANDS' if not result['rows'] else 'EFFECTS_REPRODUCED' if count==len(result['rows']) else 'PARTIAL',
            reason='RETAINED_CONDITIONAL_NUMERICAL_EFFECTS_ONLY', complete_retained_selection=True,
            effect_matches=count, all_effects_reproduced=bool(result['rows']) and count==len(result['rows']))
        if replay_valuations:
            # Unknown/legacy commands could hide preparation. Never credit a
            # positive subset as full coverage when the original cohort is open.
            complete = all(r['status'] != 'GATED' and (r['action'] != 'COORDINATE'
                or r['prepared_valuations'] is not None and r['prepared_valuations']['complete_prepared_selection'])
                for r in result['rows'])
            values = [r['prepared_valuations'] for r in result['rows'] if r['prepared_valuations'] is not None]
            total = sum(v['prepared_count'] for v in values)
            matched = sum(v['economic_matches'] for v in values)
            result['prepared_valuation_coverage'].update(complete=complete, prepared_count=total if complete else None,
                economic_matches=matched, rejected_preparation_count=sum(v['rejected_preparation_count'] for v in values) if complete else None,
                all_prepared_valuations_reproduced=complete and total > 0 and matched==total)
        return result
    except (EvidenceError, KeyError, TypeError, ValueError) as exc:
        result.update(status='GATED',reason=str(exc) if isinstance(exc,EvidenceError) else 'ACCOUNT_REPLAY_AUDIT_MALFORMED_SELECTION',
            rows=[],complete_retained_selection=False,effect_matches=0,all_effects_reproduced=False)
        return result
