"""Original inputs for shared PAPER numerical effects, never replay authority.

Preparation and protected exit decisions are archived conditional inputs. Their
controls are still enforced by the runtime; consuming a transcript grants no
permission and is not independent evidence that those controls were correct.
"""
from copy import deepcopy
from dataclasses import asdict

from .event_risk import SafetyReductions
from .evidence import EvidenceError, canonical, digest, finite

VERSION = 'alpha_v11_paper_effect_inputs_v1'


class EffectReplayError(RuntimeError):
    """Missing historical inputs must escape normal candidate rejection handling."""


def ref(row):
    return dict(id=row['id'], sha256=row['sha256'], seq=row['seq']) if row else None


class EffectInputs:
    """Record runtime reads, or consume exact historical reads without commands."""
    def __init__(self, coordinator, state, *, original=None, earliest=None, latest=None):
        self.coordinator = coordinator
        self.original = deepcopy(original)
        self.earliest, self.latest = earliest, latest
        self.times, self.exits = [], []
        self.preparation = None
        self.before_sha = digest(state)

    def clock(self):
        if self.original is None:
            at = finite(self.coordinator.store.clock())
        else:
            try:
                self.coordinator.store.check()
            except EvidenceError as exc:
                raise EffectReplayError(str(exc)) from exc
            index = len(self.times)
            if index >= len(self.original['times']):
                raise EffectReplayError('ACCOUNT_REPLAY_CLOCK_INPUT_MISSING')
            at = finite(self.original['times'][index])
            if not self.earliest <= at <= self.latest or self.times and at < self.times[-1]:
                raise EffectReplayError('ACCOUNT_REPLAY_CLOCK_NONCAUSAL')
        self.times.append(at)
        return at

    def risk(self, state):
        # Preserve the runtime's clock-read position inside the risk calculation.
        return self.coordinator._risk(state, clock=self.clock)

    def exit_check(self, proposal, state):
        c = self.coordinator
        try:
            value = c.store.get(proposal.valuation_id)
        except EvidenceError as exc:
            if self.original is not None:
                raise EffectReplayError(str(exc)) from exc
            raise
        binding = dict(proposal_sha256=digest(asdict(proposal)), state_sha256=digest(state), valuation_ref=ref(value))
        if self.original is None:
            from .position_management import revalidate_exit
            reason = None
            try:
                revalidate_exit(c, proposal, value['body']['details'], state=state)
            except EvidenceError as exc:
                reason = str(exc)
            entry = dict(**binding, reason=reason)
        else:
            index = len(self.exits)
            if index >= len(self.original['exit_checks']):
                raise EffectReplayError('ACCOUNT_REPLAY_EXIT_INPUT_MISSING')
            entry = self.original['exit_checks'][index]
            if (set(entry) != set(binding) | {'reason'}
                    or any(entry[k] != v for k, v in binding.items())
                    or entry['reason'] is not None and (not isinstance(entry['reason'], str) or not entry['reason'])):
                raise EffectReplayError('ACCOUNT_REPLAY_EXIT_INPUT_BINDING')
            # Only the original conditional result is consumed. In particular,
            # this never calls protected inference or renews exit admission.
        self.exits.append(deepcopy(entry))
        if entry['reason'] is not None:
            raise EvidenceError(entry['reason'])

    def reduce_only(self, context):
        # This is a read-only historical overlay during replay. The original
        # runtime's atomic safety heads remain enforced by its account commit.
        try:
            return SafetyReductions(self.coordinator.store).view(context)['flags']['reduce_only']
        except EvidenceError as exc:
            if self.original is not None:
                raise EffectReplayError(str(exc)) from exc
            raise

    def payload(self, before, request, *, heads, receipt_seq):
        return dict(version=VERSION, before_ref=ref(before), before_state_sha256=self.before_sha,
            request_sha256=digest(request), times=self.times, exit_checks=self.exits,
            preparation=self.preparation, expected_heads=heads, receipt_seq=receipt_seq,
            control_flow_replayed=False, financial_authority=False)

    def finish(self):
        if self.original is not None:
            self.coordinator.store.check()
            if (canonical(self.times) != canonical(self.original['times'])
                    or canonical(self.exits) != canonical(self.original['exit_checks'])):
                raise EvidenceError('ACCOUNT_REPLAY_UNUSED_EFFECT_INPUTS')
