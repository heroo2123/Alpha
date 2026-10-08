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

This module never chooses production `ObservationPolicy` windows. Its only
production caller is `risk_inputs.py`'s opt-in `execution_health_policy`
(default `None`, inert); choosing real windows and accepting the evidence for
live gating remain the separate, explicit owner decisions `paper_execution_health_promotion.py`'s own
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
read). That second choice is the "conservative watermark": the observed
body depends only on archive contents, never on when the writer ran, and
the resulting `valid_until` ages against the archive's own evidence, never
an operator's wall-clock drift relative to it.

A MEASUREMENT row is itself a new append, which would otherwise move the
store's literal tip on every single rerun -- including this writer's own
previous row, or any other kind of audit/telemetry append entirely
unrelated to execution health. Two different tips are therefore used, for
two different purposes (R08-01/R08-03):

* the observation itself is always computed over the COMPLETE real literal
  prefix through the store's current tip, because that is exactly the
  prefix `promote_execution_health` independently replays (`row['seq'] - 1`)
  -- trimming anything here would make an honest row fail its own replay;
* the deterministic record id is keyed on the *effective* tip: the latest
  row of a kind `observe()` actually inspects (`RULE_STATE`,
  `COORDINATOR_EVENT`, `BOOK`, `TRADE`). A trailing `MEASUREMENT`/
  `RUNTIME_STATUS`/etc. row is structurally inert to the diagnostic, so a
  rerun with no new relevant evidence recomputes the same id and reuses the
  existing row instead of appending an unbounded self-referential chain.

An existing row at that id is reused only after it passes the promotion
module's own exact replay against its real predecessor prefix, inside the
requested scope (R08-02); anything else is a typed conflict, never success.
Locating the effective tip pages the archive under the module-wide
`MAX_ROWS`/byte ceiling only to find it; a NEW write additionally requires
the complete literal prefix to fit inside `policy.complete_history_scan_bound`.

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
    MAXIMUM_OBSERVATION_AGE_SECONDS, _page_prefix, _replay_observation, _unknown,
    promote_execution_health,
)
from .paper_risk_observation import MAX_ROWS, ObservationPolicy, observe


_SCOPE_FAMILY = 'exec-health-obs'
# The only kinds `observe()` ever actually inspects (rule/account heads, fill
# proofs, book continuity) -- see `_effective_tip`'s own docstring.
_RELEVANT_KINDS = frozenset({'RULE_STATE', 'COORDINATOR_EVENT', 'BOOK', 'TRADE'})


def _effective_tip(store):
    """The complete real literal prefix plus its effective (relevant) tip.

    `observe()` only ever selects `RULE_STATE`, `COORDINATOR_EVENT`, `BOOK`,
    and `TRADE` rows for anything (rule/account heads, fill proofs, book
    continuity) -- every other kind, including this module's own
    previously-appended `MEASUREMENT` rows, is structurally inert to its
    diagnostic. The effective tip is the last row of a relevant kind; it is
    what the record id (idempotency) is keyed on. The literal prefix is
    returned untrimmed, because the observation and its replay commit to it.

    Returns `(collected, effective_tip_row)`, or `None` if no real prefix is
    available at all (an empty store, one truncated by the `MAX_ROWS`/
    archive-byte ceiling before reaching the real tip, or one with no
    relevant row yet).
    """
    through_seq = store.pin_read_view()['through_seq']
    collected = _page_prefix(store, through_seq, MAX_ROWS)
    if collected is None:
        return None
    for item in reversed(collected):
        if item['kind'] in _RELEVANT_KINDS:
            return collected, item
    return None


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
    found = _effective_tip(store)
    if found is None:
        return None
    return _record_id(account_id=account_id, event_id=event_id, rule_fingerprint=rule_fingerprint,
                      collateral_asset=collateral_asset, policy_sha256=policy_sha256,
                      tip_sha256=found[1]['sha256'])


def _validate_arguments(*, account_id, event_id, rule_fingerprint, collateral_asset, policy):
    if type(policy) is not ObservationPolicy:
        raise EvidenceError('EXECUTION_HEALTH_MEASUREMENT_POLICY_REQUIRED')
    sha(policy.policy_sha256)
    identity(account_id); identity(event_id); identity(collateral_asset); sha(rule_fingerprint)


def _verified_existing(store, row, *, effective_tip, account_id, event_id, rule_fingerprint,
                       collateral_asset, policy):
    """True only if `row` is a genuine observation for exactly this scope.

    The deterministic id is public, so anyone able to append can preclaim it
    (R08-02). Reuse therefore requires the same exact replay the promotion
    module performs, plus that the row sits after the effective tip it is
    keyed on (so no relevant row can lie between its frontier and now).
    """
    if (row['kind'] != 'MEASUREMENT' or row['event_id'] != event_id or row['seq'] <= effective_tip['seq']
            or row['body'].get('namespace') != store.namespace):
        return False
    details = row['body'].get('details')
    if (type(details) is not dict or details.get('account_id') != account_id
            or details.get('event_id') != event_id or details.get('rule_fingerprint') != rule_fingerprint
            or details.get('collateral_asset') != collateral_asset
            or details.get('policy_sha256') != policy.policy_sha256):
        return False
    try:
        recomputed = _replay_observation(store, row, details, policy=policy, account_id=account_id,
                                         event_id=event_id, rule_fingerprint=rule_fingerprint,
                                         collateral_asset=collateral_asset,
                                         observed_at=finite(details.get('observed_at')))
    except EvidenceError:
        return False
    return recomputed is not None and recomputed == details


def record_execution_health_observation(store, *, account_id, event_id, rule_fingerprint, collateral_asset,
                                        policy):
    """Append (or genuinely reuse) one `observe()` result over the real current tip.

    Returns the `MEASUREMENT` row, or `None` if no real, fully collected
    archive prefix is currently available right now (an empty store, or one
    truncated by `policy.complete_history_scan_bound`/the archive-byte
    ceiling before reaching the real tip) -- never a write built on a
    partial or merely-plausible prefix. Raises `EvidenceError` for a
    caller-level argument problem (bad scope identity, wrong policy type),
    for `EXECUTION_HEALTH_MEASUREMENT_RECORD_CONFLICT` (a row already holds
    this deterministic id but is not a verified observation for this exact
    scope), or for a storage-layer failure such as `AUDIT_CLOCK_REGRESSION`.
    """
    _validate_arguments(account_id=account_id, event_id=event_id, rule_fingerprint=rule_fingerprint,
                        collateral_asset=collateral_asset, policy=policy)
    scan_bound = policy.complete_history_scan_bound
    if type(scan_bound) is not int or not 1 <= scan_bound <= MAX_ROWS:
        return None
    found = _effective_tip(store)
    if found is None:
        return None
    collected, effective_tip = found
    record_id = _record_id(account_id=account_id, event_id=event_id, rule_fingerprint=rule_fingerprint,
                           collateral_asset=collateral_asset, policy_sha256=policy.policy_sha256,
                           tip_sha256=effective_tip['sha256'])
    try:
        # Check-then-write, exactly the `_existing()` idiom `event_risk.py`
        # already uses for its own CAS-guarded audit rows: a genuine rerun
        # with no new relevant evidence must short-circuit here, never reach
        # `store.audit` again (whose envelope `recorded_at` would differ).
        existing = store.get(record_id)
    except EvidenceError as exc:
        if str(exc) != 'EVIDENCE_MISSING':
            raise
    else:
        if not _verified_existing(store, existing, effective_tip=effective_tip, account_id=account_id,
                                  event_id=event_id, rule_fingerprint=rule_fingerprint,
                                  collateral_asset=collateral_asset, policy=policy):
            raise EvidenceError('EXECUTION_HEALTH_MEASUREMENT_RECORD_CONFLICT')
        return existing
    # A new write must observe the complete literal prefix within the
    # policy's own scan bound -- never a truncated one.
    if len(collected) > scan_bound:
        return None
    tip = collected[-1]
    at = finite(tip['body']['recorded_at'])
    result = observe(collected, tip_sha256=tip['sha256'], at=at, policy=policy, account_id=account_id,
                     event_id=event_id, rule_fingerprint=rule_fingerprint, collateral_asset=collateral_asset)
    # The envelope `event_id` is the literal real event -- required exactly
    # as-is by `promote_execution_health`'s own scope check (see `_record_id`'s
    # docstring on why this is deliberately a different value from the id above).
    return store.audit(record_id, event_id=event_id, kind='MEASUREMENT', details=result,
                       evidence_ids=tuple(dict.fromkeys(result['evidence_ids'])))


def observe_and_promote_execution_health(store, *, account_id, event_id, rule_fingerprint, collateral_asset,
                                         policy, maximum_observation_age_seconds=MAXIMUM_OBSERVATION_AGE_SECONDS):
    """`observe_and_promote_execution_health_record` without the record id."""
    return observe_and_promote_execution_health_record(
        store, account_id=account_id, event_id=event_id, rule_fingerprint=rule_fingerprint,
        collateral_asset=collateral_asset, policy=policy,
        maximum_observation_age_seconds=maximum_observation_age_seconds)[0]


def observe_and_promote_execution_health_record(store, *, account_id, event_id, rule_fingerprint,
                                                collateral_asset, policy,
                                                maximum_observation_age_seconds=MAXIMUM_OBSERVATION_AGE_SECONDS):
    """Write (or reuse) an observation, then immediately independently re-verify it.

    Returns `(promotion, record_id)`: `record_id` is the id of the exact row
    that was written/reused and verified (None when no row exists). Callers
    must cite this id rather than re-deriving it with
    `current_execution_health_record_id`, whose effective tip may already
    have advanced past the row that was promoted.

    This is the single call a future caller would make. Always returns an
    `ExecutionHealthPromotion` for any data or storage reason: no complete
    prefix (`EXECUTION_HEALTH_MEASUREMENT_SCAN_INCOMPLETE`), a preclaimed or
    foreign row at the deterministic id, or a write-side storage failure
    (R08-04) are all typed UNKNOWN. Only caller-level argument problems
    raise, checked before anything touches the store.
    """
    _validate_arguments(account_id=account_id, event_id=event_id, rule_fingerprint=rule_fingerprint,
                        collateral_asset=collateral_asset, policy=policy)
    try:
        row = record_execution_health_observation(store, account_id=account_id, event_id=event_id,
                                                   rule_fingerprint=rule_fingerprint,
                                                   collateral_asset=collateral_asset, policy=policy)
    except EvidenceError as exc:
        return _unknown(str(exc)), None
    if row is None:
        return _unknown('EXECUTION_HEALTH_MEASUREMENT_SCAN_INCOMPLETE'), None
    return promote_execution_health(store, row['id'], account_id=account_id, event_id=event_id,
                                    rule_fingerprint=rule_fingerprint, collateral_asset=collateral_asset,
                                    policy=policy, maximum_observation_age_seconds=maximum_observation_age_seconds), row['id']
