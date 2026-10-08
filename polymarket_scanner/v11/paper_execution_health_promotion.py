"""Strict, fail-closed promotion of genuine PAPER execution-health evidence.

`risk_inputs.py` always constructs `EventMetrics` with `adverse_fills`,
`recent_markout_per_share` and `time_to_settlement_seconds` hardcoded to
`None`; `paper_prerequisites.py` names the first two gaps
`EXECUTION_HEALTH_PROMOTION_CONTRACT_REQUIRED`. This module is that contract
for the execution-health half, plus a structurally separate, currently
unauthorized settlement-finality contract. It supplies no values today
against any live store: it only defines, and tests, the exact strict checks
a genuine future observation must pass before either field may ever leave
`None`. Wiring either promotion into `risk_inputs.py`'s live `EventMetrics`
construction is a deliberate owner decision, out of scope here.

Execution health: `paper_risk_observation.observe()` already derives
`diagnostic_adverse_fill_count` / `diagnostic_markout_collateral_per_share`
honestly from a pinned archive of genuine PAPER fills and their later BOOK
markouts, but its own result hardcodes `event_metrics_adverse_fills` and
`event_metrics_recent_markout_per_share` to `None` -- it is diagnostic-only
and documents that "its archive and policy must be independently checked
before any other use." `promote_execution_health` is that independent check:
given the exact `record_id` of an already-appended `MEASUREMENT` row holding
one such `observe()` result (no writer persists one yet), it re-verifies
scope, authority, freshness and internal shape before treating the two
diagnostic numbers as real `EventMetrics` inputs. Zero genuine recent fills
(`NO_RECONCILED_RECENT_PAPER_FILLS`, or any other non-success status) stays
`UNKNOWN`, never a fabricated `0` -- "no orders" is not evidence of "no
adverse fills." The record itself is read via `EvidenceStore.get`, which
already rejects any row whose stored body does not hash-match its own
digest, so this module only adds the scope/freshness/shape checks on top of
that -- it does not re-verify the archive-wide lineage `observe()` already
performed; it trusts the store's own append-only integrity for that.

Settlement finality (`time_to_settlement_seconds`) is a *different* and, as
of this patch, entirely unauthorized concern. It is never the same thing as
the event-risk engine's existing "new-risk research cutoff"
(`ordinary_new_risk_research_allowed` / `passive_new_risk_research_allowed`
in `event_risk.py`, or any directional-release exception in
`source_release.py`): that gate answers "may NEW research-only orders be
proposed right now", never "has this market's real-world outcome become
contractually final under UMA's own resolution/dispute-window rules."
`SettlementFinalityObservation` and `promote_settlement_timing` give that
second question its own distinct typed shape so the two can never be passed
as each other. `AUTHORIZED_SETTLEMENT_PROVIDERS` is intentionally empty: no
settlement-finality source has been reviewed or named, so every call fails
closed today regardless of what is supplied. Naming a real provider there,
after independent review, is the owner decision this module cannot make for
itself -- and even then a *separate* reviewed rule is still required to turn
a bare finality fact into the `time_to_settlement_seconds` number
`EventPolicy` expects; this module deliberately stops before inventing that
rule.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .evidence import EvidenceError, digest, finite, identity, sha
from .paper_risk_observation import VERSION as OBSERVATION_VERSION


EXECUTION_HEALTH_VERSION = 'alpha_v11_paper_execution_health_promotion_v1'
SETTLEMENT_FINALITY_VERSION = 'alpha_v11_settlement_finality_promotion_v1'

# No settlement-finality source has been independently reviewed/authorized.
# Populating this is the owner decision named in this module's docstring; it
# is intentionally empty so every observation fails closed until that lands.
AUTHORIZED_SETTLEMENT_PROVIDERS = frozenset()

# A caller may tighten this bound; this module never loosens it.
MAXIMUM_OBSERVATION_AGE_SECONDS = 24 * 60 * 60

_FIXED_NONFIELDS = {
    'settlement_finality_status': 'UNKNOWN',
    'cutoff_reason': 'REVIEWED_CUTOFF_POLICY_UNAVAILABLE',
    'new_risk_cutoff_at': None,
    'seconds_to_new_risk_cutoff': None,
}


@dataclass(frozen=True)
class ExecutionHealthPromotion:
    adverse_fills: int | None
    recent_markout_per_share: float | None
    status: str
    reason: str | None
    evidence_ids: tuple

    def __post_init__(self):
        if (self.adverse_fills is None) != (self.recent_markout_per_share is None):
            raise EvidenceError('EXECUTION_HEALTH_PARTIAL_PROMOTION_INVALID')
        if self.status == 'UNKNOWN':
            if self.adverse_fills is not None or self.reason is None:
                raise EvidenceError('EXECUTION_HEALTH_PROMOTION_STATUS_MISMATCH')
        elif self.status == 'PROMOTED':
            if self.adverse_fills is None or self.reason is not None:
                raise EvidenceError('EXECUTION_HEALTH_PROMOTION_STATUS_MISMATCH')
            if type(self.adverse_fills) is not int or self.adverse_fills < 0:
                raise EvidenceError('EXECUTION_HEALTH_ADVERSE_FILLS_INVALID')
            finite(self.recent_markout_per_share, nonnegative=False)
        else:
            raise EvidenceError('EXECUTION_HEALTH_PROMOTION_STATUS_UNKNOWN')
        if type(self.evidence_ids) is not tuple or any(type(e) is not str for e in self.evidence_ids):
            raise EvidenceError('EXECUTION_HEALTH_EVIDENCE_SHAPE_INVALID')


def _unknown(reason):
    return ExecutionHealthPromotion(None, None, 'UNKNOWN', reason, ())


def promote_execution_health(store, record_id, *, account_id, event_id, rule_fingerprint,
                              collateral_asset, policy_sha256,
                              maximum_observation_age_seconds=MAXIMUM_OBSERVATION_AGE_SECONDS):
    """Re-verify one already-appended execution-health observation row.

    `record_id` must name a `MEASUREMENT` row whose `details` is exactly the
    dict `paper_risk_observation.observe()` returns (no writer appends one
    yet). Every other input pins the exact scope this caller's claim is
    for. Returns an `ExecutionHealthPromotion`; every failure mode -- missing,
    wrong kind, cross-scope, stale, future-dated, forged/duplicated,
    malformed, or simply no genuine recent fills -- returns `UNKNOWN`, never
    a guessed number.
    """
    identity(account_id); identity(event_id); identity(collateral_asset); sha(rule_fingerprint)
    sha(policy_sha256)
    if type(maximum_observation_age_seconds) not in (int, float) or maximum_observation_age_seconds <= 0:
        raise EvidenceError('EXECUTION_HEALTH_AGE_BOUND_INVALID')
    try:
        row = store.get(record_id)
    except EvidenceError:
        return _unknown('EXECUTION_HEALTH_OBSERVATION_MISSING')
    try:
        now = finite(store.clock())
        if row['kind'] != 'MEASUREMENT' or row['event_id'] != event_id:
            return _unknown('EXECUTION_HEALTH_OBSERVATION_SCOPE_MISMATCH')
        details = row['body'].get('details')
        if type(details) is not dict or len(details) > 48:
            return _unknown('EXECUTION_HEALTH_OBSERVATION_SHAPE_UNKNOWN')
        if (details.get('version') != OBSERVATION_VERSION or details.get('namespace') != 'V11_PAPER'
                or details.get('financial_authority') is not False
                or details.get('admission_eligible') is not False
                or details.get('account_id') != account_id or details.get('event_id') != event_id
                or details.get('rule_fingerprint') != rule_fingerprint
                or details.get('collateral_asset') != collateral_asset
                or details.get('policy_sha256') != policy_sha256
                or details.get('evidence_class') != 'SYNTHETIC_PAPER_DIAGNOSTIC'):
            return _unknown('EXECUTION_HEALTH_OBSERVATION_SCOPE_OR_AUTHORITY_UNKNOWN')
        if any(details.get(key) != value for key, value in _FIXED_NONFIELDS.items()):
            return _unknown('EXECUTION_HEALTH_OBSERVATION_FOREIGN_CLAIM')
        if (details.get('event_metrics_adverse_fills') is not None
                or details.get('event_metrics_recent_markout_per_share') is not None):
            return _unknown('EXECUTION_HEALTH_OBSERVATION_ALREADY_CLAIMS_ADMISSION')
        refs = details.get('evidence_ids')
        if type(refs) is not list or not refs or len(set(refs)) != len(refs) or len(refs) > 4096:
            return _unknown('EXECUTION_HEALTH_OBSERVATION_EVIDENCE_UNKNOWN')
        for ref in refs:
            identity(ref)
        sha(details.get('frontier_tip_sha256')); sha(details.get('frontier_sha256'))
        sha(details.get('policy_config_sha256')); sha(details.get('replay_sha256'))
        observed_at = finite(details.get('observed_at'))
        valid_until = details.get('valid_until')
        if type(valid_until) not in (int, float) or valid_until < observed_at:
            return _unknown('EXECUTION_HEALTH_OBSERVATION_VALID_UNTIL_UNKNOWN')
        if observed_at > now:
            return _unknown('EXECUTION_HEALTH_OBSERVATION_IN_FUTURE')
        if now - observed_at > maximum_observation_age_seconds or now > valid_until:
            return _unknown('EXECUTION_HEALTH_OBSERVATION_STALE')
        expected_replay = _replay_sha256(details)
        if expected_replay is None or expected_replay != details.get('replay_sha256'):
            return _unknown('EXECUTION_HEALTH_OBSERVATION_FORGED_OR_DUPLICATED')
        if (details.get('execution_status') != 'OBSERVED_SYNTHETIC_DIAGNOSTIC'
                or details.get('reason') is not None):
            # Includes NO_RECONCILED_RECENT_PAPER_FILLS (zero genuine orders) and
            # every other non-success status: zero orders is UNKNOWN, not zero.
            return _unknown('EXECUTION_HEALTH_NO_RECONCILED_RECENT_PAPER_FILLS')
        fill_count = details.get('fill_count')
        if type(fill_count) is not int or fill_count < 1:
            return _unknown('EXECUTION_HEALTH_OBSERVATION_FILL_COUNT_UNKNOWN')
        adverse_fills = details.get('diagnostic_adverse_fill_count')
        if type(adverse_fills) is not int or not 0 <= adverse_fills <= fill_count:
            return _unknown('EXECUTION_HEALTH_OBSERVATION_ADVERSE_COUNT_UNKNOWN')
        raw_markout = details.get('diagnostic_markout_collateral_per_share')
        if type(raw_markout) is not str:
            # paper_risk_observation._result always stores this as str(Decimal);
            # any other type cannot be a genuine passthrough of that result.
            return _unknown('EXECUTION_HEALTH_OBSERVATION_MARKOUT_UNKNOWN')
        try:
            markout = Decimal(raw_markout)
        except (InvalidOperation, TypeError, ValueError):
            return _unknown('EXECUTION_HEALTH_OBSERVATION_MARKOUT_UNKNOWN')
        if not markout.is_finite():
            return _unknown('EXECUTION_HEALTH_OBSERVATION_MARKOUT_UNKNOWN')
        try:
            recent_markout_per_share = float(markout)
        except OverflowError:
            return _unknown('EXECUTION_HEALTH_OBSERVATION_MARKOUT_UNKNOWN')
        finite(recent_markout_per_share, nonnegative=False)
        return ExecutionHealthPromotion(adverse_fills, recent_markout_per_share, 'PROMOTED', None,
                                        tuple(refs))
    except EvidenceError as exc:
        return _unknown(str(exc))


def _replay_sha256(details):
    """Recompute `paper_risk_observation._result`'s own replay pin.

    Catches a record whose context/config fields were edited without
    recomputing this hash; it cannot by itself prove the two diagnostic
    numbers are correct, only that the store's hash-verified envelope has
    not been pieced together from a different context than it claims.
    """
    try:
        return digest([OBSERVATION_VERSION, details.get('policy_config_sha256'), details.get('observed_at'),
                       details.get('frontier_sha256'), details.get('frontier_tip_sha256'),
                       details.get('account_id'), details.get('event_id'), details.get('rule_fingerprint'),
                       details.get('collateral_asset')])
    except EvidenceError:
        return None


@dataclass(frozen=True)
class SettlementFinalityObservation:
    """The exact, distinct shape a reviewed settlement-finality source would supply.

    Never the "new-risk research cutoff" (see module docstring). No provider
    is authorized yet, so no instance of this type can be promoted today --
    see `promote_settlement_timing`.
    """
    version: str
    provider: str
    event_id: str
    market_id: str
    source_identity: str
    observed_at: float
    disputed: bool
    finalized: bool
    evidence_ids: tuple

    def __post_init__(self):
        if self.version != SETTLEMENT_FINALITY_VERSION:
            raise EvidenceError('SETTLEMENT_FINALITY_VERSION_UNKNOWN')
        identity(self.provider); identity(self.event_id); identity(self.market_id)
        identity(self.source_identity)
        finite(self.observed_at)
        if type(self.disputed) is not bool or type(self.finalized) is not bool:
            raise EvidenceError('SETTLEMENT_FINALITY_BOOLEAN_REQUIRED')
        if self.finalized and self.disputed:
            raise EvidenceError('SETTLEMENT_FINALITY_CONTRADICTORY')
        if (type(self.evidence_ids) is not tuple or not self.evidence_ids
                or any(type(e) is not str for e in self.evidence_ids)):
            raise EvidenceError('SETTLEMENT_FINALITY_EVIDENCE_REQUIRED')


def promote_settlement_timing(observation, *, event_id, now, maximum_observation_age_seconds):
    """Always `(None, reason)` today: `AUTHORIZED_SETTLEMENT_PROVIDERS` is empty.

    Still performs every structural/scope/freshness check so the only thing
    missing, once the owner names and reviews a real provider, is adding it
    to that set -- not rewriting this function. Deliberately never derives a
    `time_to_settlement_seconds` number: that mapping from "is this final"
    to "how many seconds" is itself a reviewed rule this module does not
    invent (see module docstring).
    """
    now = finite(now); identity(event_id)
    if type(maximum_observation_age_seconds) not in (int, float) or maximum_observation_age_seconds <= 0:
        raise EvidenceError('SETTLEMENT_FINALITY_AGE_BOUND_INVALID')
    if not isinstance(observation, SettlementFinalityObservation):
        return None, 'SETTLEMENT_FINALITY_OBSERVATION_REQUIRED'
    if observation.event_id != event_id:
        return None, 'SETTLEMENT_FINALITY_SCOPE_MISMATCH'
    if observation.observed_at > now:
        return None, 'SETTLEMENT_FINALITY_OBSERVATION_IN_FUTURE'
    if now - observation.observed_at > maximum_observation_age_seconds:
        return None, 'SETTLEMENT_FINALITY_OBSERVATION_STALE'
    if observation.provider not in AUTHORIZED_SETTLEMENT_PROVIDERS:
        return None, 'SETTLEMENT_FINALITY_SOURCE_NOT_AUTHORIZED'
    # AUTHORIZED_SETTLEMENT_PROVIDERS is empty, so no call reaches this line
    # today; once the owner names a reviewed provider, mapping a genuine
    # finality fact to a `time_to_settlement_seconds` number is still a
    # separate reviewed rule this module deliberately does not invent here.
    return None, 'SETTLEMENT_FINALITY_TIMING_RULE_NOT_REVIEWED'
