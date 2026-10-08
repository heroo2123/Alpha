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
scope, authority, freshness and internal shape, AND independently re-derives
the row's two diagnostic numbers by re-running `observe()` itself over the
store's own archive history -- paged from sequence 1 through this row's own
true immediate predecessor (`row['seq'] - 1` in the store's real append
order, never a self-chosen earlier cutoff -- see `_replay_observation`'s
R2-H1 fix), bounded by the caller-supplied `ObservationPolicy`'s own
`complete_history_scan_bound` (<=4096 rows) and the 32MB archive-byte
ceiling `observe()` itself enforces -- before treating the two diagnostic
numbers as real `EventMetrics` inputs. The recomputed result must equal the
row's claimed `details` EXACTLY, including `frontier_tip_sha256` itself, so
a row whose self-declared tip is not genuinely its own real immediate
predecessor can never match; any mismatch, any failure to find the claimed
tip anywhere in the real prefix, an `observed_at` outside the real tip/row
`recorded_at` bracket, or zero genuine recent fills
(`NO_RECONCILED_RECENT_PAPER_FILLS`, or any other non-success status) stays
`UNKNOWN`, never a fabricated or merely self-consistent number -- "no
orders" is not evidence of "no adverse fills," and a row that only hashes
consistently with itself is not evidence of anything a store's real history
produced. The record itself is also read via `EvidenceStore.get`, which
rejects any row whose stored body does not hash-match its own digest; this
module's lineage re-derivation is in addition to that, never a substitute
for it.

That lineage re-derivation (`_replay_observation`) only ever proves the
row's claim was genuine AS OF its own insertion point (`row['seq'] - 1`): it
says nothing about anything disqualifying that happened in the store's real
history SINCE. A row honestly recorded right after a favourable fill but
before a later adverse fill (or a later `stream_healthy: false` update)
lands would still pass that check -- and used to be PROMOTED with a stale,
now-wrong number (R3-M1). `_verify_fresh_head` closes that second gap: once
the lineage replay matches, it independently re-runs `observe()` a SECOND
time over the complete real archive through the store's REAL current tip
(not the row's own insertion point), at the real current time, and requires
that fresh result's diagnostic subset (`execution_status`, `reason`,
`fill_count`, `diagnostic_adverse_fill_count`,
`diagnostic_markout_collateral_per_share`) to still equal the row's claim.
Any drift -- a newly-landed adverse fill, a newly-unhealthy stream, anything
that changes the honest answer -- keeps the result `UNKNOWN`, never a stale
`PROMOTED`.

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
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import math

from .evidence import EvidenceError, canonical, digest, finite, identity, sha
from .paper_risk_observation import (
    MAX_ARCHIVE_BYTES, MAX_ROWS, ObservationPolicy, VERSION as OBSERVATION_VERSION, observe,
)


EXECUTION_HEALTH_VERSION = 'alpha_v11_paper_execution_health_promotion_v1'
SETTLEMENT_FINALITY_VERSION = 'alpha_v11_settlement_finality_promotion_v1'

# No settlement-finality source has been independently reviewed/authorized.
# Populating this is the owner decision named in this module's docstring; it
# is intentionally empty so every observation fails closed until that lands.
AUTHORIZED_SETTLEMENT_PROVIDERS = frozenset()

# A caller may tighten this bound; this module never loosens it (F2: a
# caller-supplied bound must be finite and no larger than this ceiling, or
# it is rejected outright rather than silently accepted).
MAXIMUM_OBSERVATION_AGE_SECONDS = 24 * 60 * 60

_FIXED_NONFIELDS = {
    'settlement_finality_status': 'UNKNOWN',
    'cutoff_reason': 'REVIEWED_CUTOFF_POLICY_UNAVAILABLE',
    'new_risk_cutoff_at': None,
    'seconds_to_new_risk_cutoff': None,
}

# The exact key set `paper_risk_observation._result` always produces (F6):
# an unknown extra key can never be a genuine passthrough of that result.
_EXPECTED_DETAIL_KEYS = frozenset({
    'version', 'namespace', 'financial_authority', 'evidence_class', 'admission_eligible',
    'event_metrics_adverse_fills', 'event_metrics_recent_markout_per_share',
    'new_risk_cutoff_at', 'seconds_to_new_risk_cutoff', 'settlement_finality_status',
    'cutoff_reason', 'execution_status', 'reason', 'observed_at', 'account_id', 'event_id',
    'rule_fingerprint', 'collateral_asset', 'policy_sha256', 'policy_config_sha256',
    'frontier_tip_sha256', 'frontier_sha256', 'fill_count', 'diagnostic_adverse_fill_count',
    'diagnostic_markout_collateral_per_share', 'evidence_ids', 'valid_until', 'replay_sha256',
})


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
                              collateral_asset, policy,
                              maximum_observation_age_seconds=MAXIMUM_OBSERVATION_AGE_SECONDS):
    """Re-verify one already-appended execution-health observation row.

    `record_id` must name a `MEASUREMENT` row whose `details` is exactly the
    dict `paper_risk_observation.observe()` returns (no writer appends one
    yet). Every other input pins the exact scope this caller's claim is
    for. `policy` must be the exact `paper_risk_observation.ObservationPolicy`
    the caller believes produced this row -- it is used both to pin the
    row's self-declared `policy_sha256`/`policy_config_sha256` and, if every
    cheaper check passes, to independently re-run `observe()` over the
    store's own archive history (see module docstring). Returns an
    `ExecutionHealthPromotion`; every failure mode -- missing, wrong kind,
    cross-scope, stale, future-dated, malformed, a lineage that cannot be
    found or replayed to an exact match, the real store history having since
    moved against the claim (R3-M1; see `_verify_fresh_head`), or simply no
    genuine recent fills -- returns `UNKNOWN`, never a guessed number.
    """
    identity(account_id); identity(event_id); identity(collateral_asset); sha(rule_fingerprint)
    # R2-L3: exact type, not isinstance -- a subclass overriding reason()
    # would still carry the same asdict() digest, so only an exact-type
    # match keeps the policy binding below meaningful.
    if type(policy) is not ObservationPolicy:
        raise EvidenceError('EXECUTION_HEALTH_POLICY_REQUIRED')
    sha(policy.policy_sha256)
    # F2: a caller-supplied bound must be finite and never above this
    # module's own ceiling; NaN, inf, and absurdly large values are caller
    # bugs, not data this module should silently tolerate or loosen for.
    if type(maximum_observation_age_seconds) not in (int, float):
        raise EvidenceError('EXECUTION_HEALTH_AGE_BOUND_INVALID')
    try:
        # R2-L4: a Python int far too large to convert to float (e.g.
        # 10**400) makes math.isfinite raise OverflowError instead of
        # returning False. That is exactly as unusable here as NaN/inf, and
        # must fail through this module's own typed error, not an uncaught
        # OverflowError.
        age_bound_is_finite = math.isfinite(maximum_observation_age_seconds)
    except OverflowError:
        age_bound_is_finite = False
    if (not age_bound_is_finite
            or not 0 < maximum_observation_age_seconds <= MAXIMUM_OBSERVATION_AGE_SECONDS):
        raise EvidenceError('EXECUTION_HEALTH_AGE_BOUND_INVALID')
    try:
        row = store.get(record_id)
    except EvidenceError:
        return _unknown('EXECUTION_HEALTH_OBSERVATION_MISSING')
    try:
        now = finite(store.clock())
        # F3: the row's self-declared `details['namespace']` is attacker
        # controlled; only the store's own namespace and the envelope
        # `body['namespace']` that `EvidenceStore._append` itself stamps are
        # real.
        if (row['kind'] != 'MEASUREMENT' or row['event_id'] != event_id
                or store.namespace != 'V11_PAPER' or row['body'].get('namespace') != 'V11_PAPER'):
            return _unknown('EXECUTION_HEALTH_OBSERVATION_SCOPE_MISMATCH')
        details = row['body'].get('details')
        # F6: an unknown extra key can never be a genuine passthrough of
        # paper_risk_observation._result's own fixed shape.
        if type(details) is not dict or set(details) != _EXPECTED_DETAIL_KEYS:
            return _unknown('EXECUTION_HEALTH_OBSERVATION_SHAPE_UNKNOWN')
        if (details.get('version') != OBSERVATION_VERSION or details.get('namespace') != 'V11_PAPER'
                or details.get('financial_authority') is not False
                or details.get('admission_eligible') is not False
                or details.get('account_id') != account_id or details.get('event_id') != event_id
                or details.get('rule_fingerprint') != rule_fingerprint
                or details.get('collateral_asset') != collateral_asset
                or details.get('policy_sha256') != policy.policy_sha256
                or details.get('evidence_class') != 'SYNTHETIC_PAPER_DIAGNOSTIC'):
            return _unknown('EXECUTION_HEALTH_OBSERVATION_SCOPE_OR_AUTHORITY_UNKNOWN')
        if any(details.get(key) != value for key, value in _FIXED_NONFIELDS.items()):
            return _unknown('EXECUTION_HEALTH_OBSERVATION_FOREIGN_CLAIM')
        if (details.get('event_metrics_adverse_fills') is not None
                or details.get('event_metrics_recent_markout_per_share') is not None):
            return _unknown('EXECUTION_HEALTH_OBSERVATION_ALREADY_CLAIMS_ADMISSION')
        refs = details.get('evidence_ids')
        # F4/F5: validate every element's type BEFORE any set() use (a
        # non-hashable element, e.g. a nested list, must never reach set()
        # and raise an uncaught TypeError), and no longer reject duplicates
        # by themselves -- genuine multi-fill observe() output can legally
        # repeat an anchor/horizon-book id; only the F1 replay-equality
        # check below is this module's forgery defense.
        if (type(refs) is not list or not refs or len(refs) > MAX_ROWS
                or any(type(ref) is not str for ref in refs)):
            return _unknown('EXECUTION_HEALTH_OBSERVATION_EVIDENCE_UNKNOWN')
        for ref in refs:
            identity(ref)
        sha(details.get('frontier_tip_sha256')); sha(details.get('frontier_sha256'))
        sha(details.get('policy_config_sha256')); sha(details.get('replay_sha256'))
        # The caller's policy object must be the exact one this row's own
        # config hash commits to, not merely one whose bare `policy_sha256`
        # matches.
        if digest(asdict(policy)) != details.get('policy_config_sha256'):
            return _unknown('EXECUTION_HEALTH_OBSERVATION_POLICY_CONFIG_MISMATCH')
        observed_at = finite(details.get('observed_at'))
        valid_until = details.get('valid_until')
        if type(valid_until) not in (int, float) or valid_until < observed_at:
            return _unknown('EXECUTION_HEALTH_OBSERVATION_VALID_UNTIL_UNKNOWN')
        if observed_at > now:
            return _unknown('EXECUTION_HEALTH_OBSERVATION_IN_FUTURE')
        if now - observed_at > maximum_observation_age_seconds or now > valid_until:
            return _unknown('EXECUTION_HEALTH_OBSERVATION_STALE')
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
        if not markout.is_finite() or str(markout) != raw_markout:
            # F6: str(Decimal(x)) == x is the exact canonical form
            # paper_risk_observation's own `_result` always produces;
            # underscore digit grouping, surrounding whitespace, or any
            # other non-canonical spelling cannot be a genuine passthrough.
            return _unknown('EXECUTION_HEALTH_OBSERVATION_MARKOUT_UNKNOWN')
        try:
            recent_markout_per_share = float(markout)
        except OverflowError:
            return _unknown('EXECUTION_HEALTH_OBSERVATION_MARKOUT_UNKNOWN')
        if markout != 0 and recent_markout_per_share == 0.0:
            # F6: a nonzero Decimal that silently underflows to float 0.0
            # (e.g. '1E-400') is exactly as unusable here as inf/nan.
            return _unknown('EXECUTION_HEALTH_OBSERVATION_MARKOUT_UNKNOWN')
        finite(recent_markout_per_share, nonnegative=False)
        # F1: the only real forgery defense. Independently re-run observe()
        # over the store's own archive history, from sequence 1 up to this
        # row's self-declared frontier tip, and require an EXACT match
        # against every field this row claims. A row that only hashes
        # consistently with itself (the prior design's `replay_sha256`
        # check) proves nothing: it never recomputes the two diagnostic
        # numbers from anything outside the row itself.
        recomputed = _replay_observation(store, row, details, policy=policy,
                                         account_id=account_id, event_id=event_id,
                                         rule_fingerprint=rule_fingerprint,
                                         collateral_asset=collateral_asset, observed_at=observed_at)
        if recomputed is None:
            return _unknown('EXECUTION_HEALTH_OBSERVATION_LINEAGE_UNKNOWN')
        if recomputed != details:
            return _unknown('EXECUTION_HEALTH_OBSERVATION_REPLAY_MISMATCH')
        # R3-M1: the replay above only proves the claim was genuine as of the
        # row's own insertion point. Independently check whether the store's
        # real history has moved against it since.
        drift_reason = _verify_fresh_head(store, row, details, policy=policy, account_id=account_id,
                                          event_id=event_id, rule_fingerprint=rule_fingerprint,
                                          collateral_asset=collateral_asset, now=now)
        if drift_reason is not None:
            return _unknown(drift_reason)
        return ExecutionHealthPromotion(adverse_fills, recent_markout_per_share, 'PROMOTED', None,
                                        tuple(refs))
    except EvidenceError as exc:
        return _unknown(str(exc))


def _replay_observation(store, row, details, *, policy, account_id, event_id, rule_fingerprint,
                        collateral_asset, observed_at):
    """Independently re-derive this row's claimed `observe()` result.

    R2-H1 fix: this used to page from sequence 1 only up to the row's own
    self-declared `frontier_tip_sha256`, stopping (breaking out of the scan)
    the instant that sha256 was seen anywhere in history. A row could
    therefore self-declare an arbitrarily old real tip and a freshly-dated
    `observed_at`, and the replay would silently never see anything genuine
    that happened between that chosen tip and the row's own real insertion
    point (a later adverse fill, a later `stream_healthy: false` book
    update, ...) -- the recomputed numbers could still exactly match the
    row's claim while being blind to real, disqualifying history.

    The fix: always page the *entire* real history from sequence 1 through
    `row['seq'] - 1` -- this row's own true immediate predecessor in the
    store's real append order, never a self-chosen earlier cutoff -- and
    run `observe()` over that complete set, using the *real* final row's own
    sha256 as the tip (not the row's self-declared one). The claimed
    `frontier_tip_sha256`/`frontier_sha256`/diagnostic numbers are still
    required to equal this recomputed result EXACTLY by the caller's
    dict-equality check; a row whose self-declared tip is not genuinely its
    own real immediate predecessor can now never match, because the
    recomputed `frontier_tip_sha256` is always the real one. The scan is
    still bounded by the same `complete_history_scan_bound` (<=4096 rows)
    and 32MB archive-byte ceiling `observe()` itself enforces.

    This also binds `observed_at`: it must fall between the real tip row's
    own `recorded_at` and this row's own `recorded_at` (its real append
    time), so a row can no longer self-declare an `observed_at` later than
    its own real insertion to extend its own apparent freshness window.

    Returns `None` (never raises) if the scan bound is invalid, the row's
    self-declared tip never genuinely appears anywhere in the real prefix,
    the real prefix is too short/incomplete to reach `row['seq'] - 1`, or
    `observed_at` falls outside the real tip/row recorded_at bracket.
    """
    scan_bound = policy.complete_history_scan_bound
    if type(scan_bound) is not int or not 1 <= scan_bound <= MAX_ROWS:
        return None
    through_seq = row['seq'] - 1
    collected = _page_prefix(store, through_seq, scan_bound)
    if collected is None:
        return None
    # The claimed tip must genuinely exist somewhere in real history (R3-L1:
    # `_page_prefix` already refuses a prefix truncated early by a
    # scan-bound/byte-bound cutoff, so a non-`None` `collected` here is
    # always complete through `through_seq`).
    tip_target = details.get('frontier_tip_sha256')
    if not any(item['sha256'] == tip_target for item in collected):
        return None
    tip_row = collected[-1]
    tip_recorded_at = finite(tip_row['body']['recorded_at'])
    row_recorded_at = finite(row['body']['recorded_at'])
    if not tip_recorded_at <= observed_at <= row_recorded_at:
        return None
    return observe(collected, tip_sha256=tip_row['sha256'], at=observed_at, policy=policy,
                   account_id=account_id, event_id=event_id,
                   rule_fingerprint=rule_fingerprint, collateral_asset=collateral_asset)


def _page_prefix(store, through_seq, scan_bound):
    """Page the complete real history `1..through_seq`, never truncating early.

    Shared by `_replay_observation` (bound at a row's own `seq - 1`) and
    `_verify_fresh_head` (R3-M1; bound at the store's real current tip):
    both need the identical "collect everything in real append order, never
    stop early at a self-chosen or merely convenient point" behaviour the
    R2-H1 fix established for this module. Returns the complete tuple of
    rows in order, or `None` if the scan-bound/byte-bound ceiling truncates
    before genuinely reaching `through_seq` (R3-L1).
    """
    collected = []
    total_bytes = 0
    after = 0
    while after < through_seq and len(collected) < scan_bound and total_bytes <= MAX_ARCHIVE_BYTES:
        page = store.page_through(after_seq=after, through_seq=through_seq, limit=64)
        if not page:
            break
        for item in page:
            collected.append(item)
            total_bytes += len(canonical(item['body']).encode())
            after = item['seq']
            if len(collected) >= scan_bound or total_bytes > MAX_ARCHIVE_BYTES:
                break
    if not collected or collected[-1]['seq'] != through_seq:
        return None
    return tuple(collected)


def _verify_fresh_head(store, row, details, *, policy, account_id, event_id, rule_fingerprint,
                       collateral_asset, now):
    """R3-M1 fix: refuse a claim whose diagnostic numbers have gone stale.

    `_replay_observation` only ever proves a row's claim was a genuine
    replay of history up to the row's own insertion point
    (`row['seq'] - 1`). It is silent about anything disqualifying that has
    happened in the store's real history SINCE: a MEASUREMENT row honestly
    recorded right after a favourable fill, but before a later adverse fill
    or a later `stream_healthy: false` update lands, still passes that
    check -- its own claim was 100% genuine as of its own insertion point.

    This closes that second gap by independently re-running `observe()` a
    SECOND time, now over the complete real archive through the store's
    real current tip (`store.pin_read_view()`, never a self-chosen earlier
    cutoff), at the real current time (`now`), and requiring the diagnostic
    subset of that fresh result (`execution_status`, `reason`, `fill_count`,
    `diagnostic_adverse_fill_count`, `diagnostic_markout_collateral_per_share`)
    to still equal what the row claims. `frontier_tip_sha256`/
    `frontier_sha256` are deliberately excluded from this comparison: the
    fresh window always covers strictly more real history than the row's own
    claim ever could (it necessarily includes the row's own MEASUREMENT slot,
    a kind `observe()` itself ignores, plus everything appended since), so
    those two fields can never genuinely match the row's claim here and are
    not a sign of forgery the way they are in `_replay_observation`'s own
    check.

    Returns `None` when there is nothing new to check (the row's own
    insertion point already IS the real current head) or the fresh recompute
    still agrees with the claim. Otherwise returns the reason to refuse
    promotion -- never a stale, now-wrong `PROMOTED`.
    """
    view = store.pin_read_view()
    through_seq = view['through_seq']
    if through_seq <= row['seq']:
        return None
    collected = _page_prefix(store, through_seq, policy.complete_history_scan_bound)
    if collected is None:
        return 'EXECUTION_HEALTH_OBSERVATION_HEAD_SCAN_INCOMPLETE'
    fresh = observe(collected, tip_sha256=view['tip_sha256'], at=now, policy=policy,
                    account_id=account_id, event_id=event_id, rule_fingerprint=rule_fingerprint,
                    collateral_asset=collateral_asset)
    drift_fields = ('execution_status', 'reason', 'fill_count', 'diagnostic_adverse_fill_count',
                    'diagnostic_markout_collateral_per_share')
    if any(fresh.get(key) != details.get(key) for key in drift_fields):
        return 'EXECUTION_HEALTH_OBSERVATION_HEAD_DRIFT'
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
