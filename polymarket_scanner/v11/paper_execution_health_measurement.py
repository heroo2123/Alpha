"""Genuine writer + discovery for the execution-health observe()->MEASUREMENT lineage.

`paper_risk_observation.observe()` is a pure, total function over an archive
prefix; `paper_execution_health_promotion.promote_execution_health()` already
independently re-verifies any already-appended row claiming to hold one such
result. Until this module, nothing in the repository ever appended one --
`promote_execution_health` had no genuine row to check (see both modules'
own docstrings, and the R08 integration map). This module is that missing
writer, plus the discovery convention a reader needs to find "the latest
such row for this exact account/event/rule/collateral/policy scope" --
nothing else.

This module never chooses production `ObservationPolicy` windows, never
wires its output into `risk_inputs.py`'s live `EventMetrics` construction,
and is not imported by any production call path. Those remain the separate,
explicit owner decisions `paper_execution_health_promotion.py`'s own
docstring already names: real policy windows are a timing-calibration
judgment, and accepting this evidence to drive live gating is a sign-off
only an owner can give. Calling `observe_and_promote_execution_health`
against a real store is safe on its own terms (it only ever appends an
honest, independently re-verifiable diagnostic, and never fabricates a
value for anything it cannot genuinely derive), but using its output to
drive live gating is not itself authorized by this module.

The writer always persists whatever `observe()` honestly returns -- a
successful diagnostic, `NO_RECONCILED_RECENT_PAPER_FILLS`, or any other
UNKNOWN reason -- never a fabricated number. Its one judgment call is
*when* it is willing to write at all: only over a real, fully-collected
archive prefix (never one truncated by `policy.complete_history_scan_bound`
or the archive-byte ceiling before genuinely reaching the store's real
tip -- see `_page_prefix`'s own postcondition, reused unmodified from the
promotion module, never reimplemented here), and always timestamped at
that prefix's own last row's `recorded_at` (never a fresh wall-clock
read). That second choice is the "conservative watermark": it makes a
rerun against an unchanged tip fully idempotent (the appended body is
byte-identical, so `EvidenceStore`'s own record-id dedup absorbs it for
free, with no spurious `RECORD_ID_CONFLICT` just because time passed), and
it means the resulting `valid_until` ages against the archive's own
evidence, never an operator's wall-clock drift relative to it.

A MEASUREMENT row is itself a new append, which would otherwise move the
store's literal tip on every single rerun -- including this writer's own
previous row, or any other kind of audit/telemetry append entirely
unrelated to execution health. `_effective_tip` closes that: the real tip
this module cares about is the latest row of a kind `observe()` actually
inspects (`RULE_STATE`, `COORDINATOR_EVENT`, `BOOK`, `TRADE`), never a
trailing `MEASUREMENT`/`RUNTIME_STATUS`/etc. row that is structurally
inert to it. Without that trim, a rerun against genuinely unchanged fills
and books would still see its own prior write as new upstream evidence and
append an unbounded, ever-growing self-referential chain of rows that
never settles.

This module never touches settlement finality. `AUTHORIZED_SETTLEMENT_PROVIDERS`
stays empty in `paper_execution_health_promotion.py`; nothing here names a
provider, derives a `time_to_settlement_seconds` number, or in any way
equates a market's scheduled end with UMA's own resolution/dispute-window
finality. Execution health and settlement timing remain structurally
separate contracts, as the promotion module's own docstring requires.
"""
from .evidence import EvidenceError, finite, identity, sha
from .event_risk import _key
from .paper_execution_health_promotion import (
    MAXIMUM_OBSERVATION_AGE_SECONDS, _page_prefix, _unknown, promote_execution_health,
)
from .paper_risk_observation import MAX_ROWS, ObservationPolicy, observe


_SCOPE_FAMILY = 'exec-health-obs'
# The only kinds `observe()` ever actually inspects (rule/account heads, fill
# proofs, book continuity) -- see `_effective_tip`'s own docstring.
_RELEVANT_KINDS = frozenset({'RULE_STATE', 'COORDINATOR_EVENT', 'BOOK', 'TRADE'})


def _effective_tip(store, scan_bound):
    """The real complete prefix `observe()` would actually see, minus any
    trailing row of a kind `observe()` never inspects.

    `observe()` only ever selects `RULE_STATE`, `COORDINATOR_EVENT`, `BOOK`,
    and `TRADE` rows for anything (rule/account heads, fill proofs, book
    continuity) -- every other kind, including this module's own
    previously-appended `MEASUREMENT` rows, is structurally inert to it.
    Treating the store's raw literal tip as "the tip" regardless of kind
    would make every rerun see its own (or any other scope's, or any other
    audit/telemetry writer's) prior append as new upstream evidence --
    producing a fresh, non-idempotent row every call, and an unbounded
    self-referential chain, even when nothing about the real underlying
    fills/books/rules changed. Trimming the trailing run of irrelevant
    kinds restores the real external tip this writer's freshness and
    idempotency are actually about.

    Returns the trimmed, still-complete prefix tuple, or `None` if no real
    prefix is available at all (an empty store, one truncated by
    `scan_bound`/the archive-byte ceiling before reaching the real tip, or
    one where every row so far is of an irrelevant kind).
    """
    through_seq = store.pin_read_view()['through_seq']
    collected = _page_prefix(store, through_seq, scan_bound)
    if collected is None:
        return None
    end = len(collected)
    while end > 0 and collected[end - 1]['kind'] not in _RELEVANT_KINDS:
        end -= 1
    return collected[:end] or None


def _record_id(*, account_id, event_id, rule_fingerprint, collateral_asset, policy_sha256, tip_sha256):
    """The one primary-key convention this module uses: scope + policy + tip.

    This is deliberately NOT the row's envelope `event_id` -- `promote_execution_health`
    hard-requires that field to equal the literal real `event_id` (so cross-event
    confusion is impossible there), and `risk_inputs.py`'s own unrelated
    `kind='MEASUREMENT'` audit row already shares that exact `(kind, event_id)`
    stream for a completely different purpose. Discovery here is therefore by
    recomputing this exact deterministic id, never by scanning "latest of
    kind MEASUREMENT for event X" (which would be genuinely ambiguous between
    the two purposes). Including `policy_sha256` -- a dedicated policy
    fingerprint -- means a changed `ObservationPolicy` always computes a
    different id, never aliasing a row genuinely written under a different
    one; including `tip_sha256` is what makes a rerun against an unchanged
    tip idempotent and a genuinely new tip produce a fresh row.
    """
    identity(account_id); identity(event_id); identity(collateral_asset)
    sha(rule_fingerprint); sha(policy_sha256); sha(tip_sha256)
    return _key(_SCOPE_FAMILY, [account_id, event_id, rule_fingerprint, collateral_asset,
                                policy_sha256, tip_sha256])


def current_execution_health_record_id(store, *, account_id, event_id, rule_fingerprint, collateral_asset,
                                       policy_sha256):
    """The exact MEASUREMENT record id a write right now would use (or already did).

    A pure read of the store's real current tip plus a deterministic hash --
    it never touches `v11_records` and never implies the row has actually
    been written. `store.get(result)` raises `EVIDENCE_MISSING` until
    `record_execution_health_observation` (or `observe_and_promote_execution_health`)
    has genuinely appended it; a caller still must not trust anything about
    the row's content without also running it through
    `promote_execution_health`. Returns `None` when the store has no real
    tip yet (nothing has ever been appended).
    """
    collected = _effective_tip(store, MAX_ROWS)
    if collected is None:
        return None
    return _record_id(account_id=account_id, event_id=event_id, rule_fingerprint=rule_fingerprint,
                      collateral_asset=collateral_asset, policy_sha256=policy_sha256,
                      tip_sha256=collected[-1]['sha256'])


def record_execution_health_observation(store, *, account_id, event_id, rule_fingerprint, collateral_asset,
                                        policy):
    """Append one genuine `observe()` result over the store's real current tip.

    Returns the appended `MEASUREMENT` row, or `None` if no real, fully
    collected archive prefix is currently available right now (an empty
    store, or one truncated by `policy.complete_history_scan_bound`/the
    archive-byte ceiling before reaching the real tip) -- never a write
    built on a partial or merely-plausible prefix. Raises `EvidenceError`
    only for a caller-level argument problem (bad scope identity, wrong
    policy type) or a genuine storage-layer conflict (an existing row under
    the same deterministic id with different content -- see module
    docstring on why that should not happen from this writer's own
    ordinary reruns).
    """
    if type(policy) is not ObservationPolicy:
        raise EvidenceError('EXECUTION_HEALTH_MEASUREMENT_POLICY_REQUIRED')
    sha(policy.policy_sha256)
    identity(account_id); identity(event_id); identity(collateral_asset); sha(rule_fingerprint)
    scan_bound = policy.complete_history_scan_bound
    if type(scan_bound) is not int or not 1 <= scan_bound <= MAX_ROWS:
        return None
    collected = _effective_tip(store, scan_bound)
    if collected is None:
        return None
    tip_sha256 = collected[-1]['sha256']
    record_id = _record_id(account_id=account_id, event_id=event_id, rule_fingerprint=rule_fingerprint,
                           collateral_asset=collateral_asset, policy_sha256=policy.policy_sha256,
                           tip_sha256=tip_sha256)
    try:
        # Check-then-write, exactly the `_existing()` idiom `event_risk.py`
        # already uses for its own CAS-guarded audit rows: a genuine rerun
        # against an unchanged effective tip must short-circuit here, never
        # reach `store.audit` again. `store.audit`'s own envelope
        # `recorded_at`/`available_at` always reflects a fresh `store.clock()`
        # read, which real wall-clock drift between calls would otherwise
        # make differ from the first call's envelope -- a genuine
        # `RECORD_ID_CONFLICT` for a would-be rerun that changed nothing
        # about the real diagnostic itself.
        return store.get(record_id)
    except EvidenceError as exc:
        if str(exc) != 'EVIDENCE_MISSING':
            raise
    at = finite(collected[-1]['body']['recorded_at'])
    result = observe(collected, tip_sha256=tip_sha256, at=at, policy=policy, account_id=account_id,
                     event_id=event_id, rule_fingerprint=rule_fingerprint, collateral_asset=collateral_asset)
    # The envelope `event_id` is the literal real event -- required exactly
    # as-is by `promote_execution_health`'s own scope check (see `_record_id`'s
    # docstring on why this is deliberately a different value from the id above).
    return store.audit(record_id, event_id=event_id, kind='MEASUREMENT', details=result,
                       evidence_ids=tuple(dict.fromkeys(result['evidence_ids'])))


def observe_and_promote_execution_health(store, *, account_id, event_id, rule_fingerprint, collateral_asset,
                                         policy, maximum_observation_age_seconds=MAXIMUM_OBSERVATION_AGE_SECONDS):
    """Write a fresh observation, then immediately independently re-verify it.

    This is the single call a future caller would make; see module
    docstring for why a fresh write-then-promote on every call is the safe
    minimum here, not a separately scheduled job. Always returns an
    `ExecutionHealthPromotion` -- a write-side problem (no complete prefix
    available right now) is reported the same way any other UNKNOWN reason
    already is, never raised past this function for an ordinary data
    reason. Caller-level argument problems and genuine storage conflicts
    still raise, exactly as `record_execution_health_observation` does.
    """
    row = record_execution_health_observation(store, account_id=account_id, event_id=event_id,
                                               rule_fingerprint=rule_fingerprint, collateral_asset=collateral_asset,
                                               policy=policy)
    if row is None:
        return _unknown('EXECUTION_HEALTH_MEASUREMENT_SCAN_INCOMPLETE')
    return promote_execution_health(store, row['id'], account_id=account_id, event_id=event_id,
                                    rule_fingerprint=rule_fingerprint, collateral_asset=collateral_asset,
                                    policy=policy, maximum_observation_age_seconds=maximum_observation_age_seconds)
