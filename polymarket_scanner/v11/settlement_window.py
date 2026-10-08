"""Deterministic, conservative NEW-RISK settlement-window upper bound.

This module derives `time_to_settlement_seconds` for `EventRiskInputs`
(`risk_inputs.py`'s opt-in `settlement_window_policy`) from the already
reviewed, already-recorded `RuleFingerprint` payload bound by `RuleGuard`
(`rules.py`). The close instant used here is the observation window close of
the contract's local target date -- labeled below, and in every result this
module returns, as `RULE_LOCAL_TARGET_DATE_OBSERVATION_CLOSE_NOT_FINALITY`.

That close instant is a conservative UPPER BOUND on remaining
tradeable-information time, never a settlement/finality instant: the real
UMA resolution/dispute-window finality this contract eventually reaches is
always at or after it (first following-date datapoint, or 23:59 ET the next
day, whichever is first -- see `finality_and_deadline_policy` below -- both
no earlier than this module's own local-midnight close). Treating this
earlier instant as "time to settlement" can therefore only make
`event_risk.EventRiskEngine` MORE cautious (an earlier EVENT/CAUTION
transition, or `SETTLEMENT_WINDOW_UNKNOWN_OR_CLOSED` once past it), never
less. This module never touches settlement finality itself: the separate
Result-Lag finality engine (`paper_execution_health_promotion.
promote_settlement_timing`, `AUTHORIZED_SETTLEMENT_PROVIDERS`) stays
untouched and DISABLED, and nothing here claims settlement, resolution, or
payout authority, or any financial authority.

Only the one reviewed rule family this module has concretely confirmed
against `weather_only_rules.py` / `weather_only_calibration_capture.py` --
`observation_population == 'WRH_HOURLY_DATA'` and
`finality_and_deadline_policy == 'FIRST_FOLLOWING_DATE_DATAPOINT_OR_NEXT_DAY_2359_ET'`
-- is accepted; any other rule shape stays `UNKNOWN`
(`SETTLEMENT_WINDOW_RULE_FAMILY_UNSUPPORTED`) rather than guessing a close
instant for a contract this module has not verified.

`derive_time_to_observation_close` is a pure, total read. It never appends
to the store and never fabricates a number: any data problem (no rule state,
a quarantined/changed/stale rule, an unsupported rule family, a payload that
fails its own digest/zone checks) is a typed `UNKNOWN`, never an exception.
`EvidenceError` is raised only for caller-level misuse -- wrong argument
types, a malformed policy, or an `at` after the store's own current clock
(the same "never evaluate against a future timestamp" invariant
`event_risk.py`'s own `EVENT_METRICS_IN_FUTURE` and `risk_inputs.py`'s own
`RISK_MODEL_CUTOFF_IN_FUTURE` already enforce).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone as _fixed_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .evidence import EvidenceError, digest, finite
from .rules import RuleGuard


VERSION = 'alpha_v11_settlement_window_v1'
BASIS = 'RULE_LOCAL_TARGET_DATE_OBSERVATION_CLOSE_NOT_FINALITY'
ACCEPTED_OBSERVATION_POPULATION = 'WRH_HOURLY_DATA'
ACCEPTED_FINALITY_AND_DEADLINE_POLICY = 'FIRST_FOLLOWING_DATE_DATAPOINT_OR_NEXT_DAY_2359_ET'


@dataclass(frozen=True)
class SettlementWindowPolicy:
    version: str
    maximum_rule_age_seconds: float

    def __post_init__(self):
        if self.version != VERSION:
            raise EvidenceError('SETTLEMENT_WINDOW_POLICY_VERSION')
        if finite(self.maximum_rule_age_seconds, nonnegative=False) <= 0:
            raise EvidenceError('SETTLEMENT_WINDOW_POLICY_BOUND')


@dataclass(frozen=True)
class SettlementWindowResult:
    status: str
    seconds: float | None
    reason: str | None
    evidence_ids: tuple[str, ...]
    close_utc: float | None
    basis: str

    def __post_init__(self):
        if self.status not in ('DERIVED', 'UNKNOWN'):
            raise EvidenceError('SETTLEMENT_WINDOW_RESULT_STATUS')
        if self.basis != BASIS:
            raise EvidenceError('SETTLEMENT_WINDOW_RESULT_BASIS')
        if type(self.evidence_ids) is not tuple or any(type(e) is not str for e in self.evidence_ids):
            raise EvidenceError('SETTLEMENT_WINDOW_RESULT_EVIDENCE')
        if self.status == 'DERIVED':
            if self.reason is not None or self.seconds is None or self.close_utc is None:
                raise EvidenceError('SETTLEMENT_WINDOW_RESULT_SHAPE')
            finite(self.seconds, nonnegative=False)
            finite(self.close_utc)
        else:
            if self.seconds is not None or self.close_utc is not None or not self.reason:
                raise EvidenceError('SETTLEMENT_WINDOW_RESULT_SHAPE')


def _unknown(reason, evidence_ids=()):
    return SettlementWindowResult('UNKNOWN', None, reason, tuple(evidence_ids), None, BASIS)


def _standard_offset_midnight(local_midnight):
    """The zone's fixed standard (non-DST) offset, applied to this same date.

    `utcoffset() - dst()` is the standard-time component of whatever offset
    actually applies on this date; it is `0` (so this instant is identical to
    `local_midnight` itself) on any date the zone is not observing daylight
    time, and only differs on a date the zone currently observes DST. This
    never hardcodes any particular zone's numeric offset.
    """
    offset, dst = local_midnight.utcoffset(), local_midnight.dst()
    if offset is None or dst is None:
        raise EvidenceError('SETTLEMENT_WINDOW_ZONE_OFFSET_UNKNOWN')
    return local_midnight.replace(tzinfo=_fixed_timezone(offset - dst))


def derive_time_to_observation_close(store, *, event_id, rule_fingerprint, at, policy):
    """`(status, seconds, reason, evidence_ids, close_utc, basis)`; never fabricated.

    `status == 'DERIVED'` only when the exact reviewed rule family above is
    bound, fresh, and unquarantined under `RuleGuard.revalidate`, and the
    bound payload's own `target_date`/`timezone` produce a representable
    close instant. Every other path is `status == 'UNKNOWN'` with a typed
    `reason` and whatever evidence (if any) was actually inspected.
    """
    if (type(event_id) is not str or type(rule_fingerprint) is not str
            or type(at) not in (int, float) or type(policy) is not SettlementWindowPolicy):
        raise EvidenceError('SETTLEMENT_WINDOW_ARGUMENT_TYPE')
    at = finite(at)
    now = finite(store.clock())
    if at > now:
        raise EvidenceError('SETTLEMENT_WINDOW_AT_IN_FUTURE')
    review = RuleGuard(store).revalidate(event_id, rule_fingerprint, max_age_seconds=policy.maximum_rule_age_seconds)
    row = store.latest(kind='RULE_STATE', event_id=event_id)
    if not review['passed']:
        return _unknown('SETTLEMENT_WINDOW_'+review['reason'], (row['id'],) if row else ())
    if row is None:
        # RuleGuard.revalidate just passed against a RULE_STATE head that a
        # concurrent append has since removed from view; never assume content.
        return _unknown('SETTLEMENT_WINDOW_RULE_EVIDENCE_MISSING')
    details = row['body'].get('details')
    payload = details.get('preimage') if type(details) is dict else None
    if (type(payload) is not dict or digest(payload) != rule_fingerprint
            or details.get('fingerprint') != rule_fingerprint):
        return _unknown('SETTLEMENT_WINDOW_RULE_PAYLOAD_UNVERIFIED', (row['id'],))
    if (payload.get('observation_population') != ACCEPTED_OBSERVATION_POPULATION
            or payload.get('finality_and_deadline_policy') != ACCEPTED_FINALITY_AND_DEADLINE_POLICY):
        return _unknown('SETTLEMENT_WINDOW_RULE_FAMILY_UNSUPPORTED', (row['id'],))
    try:
        target = date.fromisoformat(payload['target_date'])
        tz = ZoneInfo(payload['timezone'])
    except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
        return _unknown('SETTLEMENT_WINDOW_RULE_TARGET_DATE_OR_TIMEZONE_INVALID', (row['id'],))
    try:
        civil_midnight = datetime.combine(target+timedelta(days=1), time.min, tz)
        civil_close = civil_midnight.timestamp()
        standard_close = _standard_offset_midnight(civil_midnight).timestamp()
        close = min(civil_close, standard_close)
        seconds = finite(close-at, nonnegative=False)
        close = finite(close)
    except (OverflowError, OSError, ValueError, EvidenceError):
        return _unknown('SETTLEMENT_WINDOW_CLOSE_UNREPRESENTABLE', (row['id'],))
    return SettlementWindowResult('DERIVED', seconds, None, (row['id'],), close, BASIS)
