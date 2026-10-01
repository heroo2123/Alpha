# Gate 3 V4 slice-two exact-commit review — 39b80fa — CHANGES_REQUIRED

Reviewer: Claude Opus 5.5 (a different model from the Sonnet writer of
`39b80fa`). Candidate: `/home/alphaadmin/AlphaV11_Gate3V4Slice2/Alpha`, branch
`r09-gate3-v4-slice2-20261001`, commit
`39b80fab8e0b5bcf97862a0cd23d06f6634f5f6e`, tree `a09af8e6`, parent
`71fc948`. Scope: slice (2), "durable shared/session ledgers with crash
matrix", checked against sections 3 to 6 of the
[transport runtime design](V11_R09_GATE3_TRANSPORT_RUNTIME_DESIGN.md). The
private master's hash was verified in place. Nothing from it is copied here.
This review is offline only. It grants no G3-L, launch, network, SHADOW or
financial authority.

## Verdict: CHANGES_REQUIRED

The journal primitive is sound on inspection. It has bounded-reader replay
(same discipline as `DurableBudget._replay`), O_NOFOLLOW/flock ownership,
directory and file identity re-checks, poison-on-write-failure, a fork guard
and a non-reentrant mutex. The generalized R2 rule (an inherited open
intent/attempt is never progressed) is correct for the paths it covers. The
35 tests pass (`/home/alphaadmin/alpha-review-test-venv`, 0.29 s).

The ledger *state machines*, however, let several holds be released that
the design requires to stay held. A ledger's job is to make those holds
durable, so they block acceptance. The
[probe script](V11_R09_GATE3_V4_SLICE2_REVIEW_39b80fa_probes.py) reproduces
all nine findings below on the exact commit (`SUMMARY 9 of 9`).

### Blocking

- **S1 — Recording a denial closes the global token (P1).** In
  `SharedLedger` a denial can only be recorded through
  `intent_closed(outcome='DENIED')`. Section 3 requires the denial to be
  recorded "immediately when headers/status make it known, before reading more
  body or releasing any accounting". Sections 4 and 5 require the single
  global token to stay held "until transport is closed and settlement
  complete", and require `INTENT_CLOSED` to bind outcome, denial history *and*
  the accounting head. A caller that follows the design therefore releases the
  token while the transport is still open. The probe then opens a second
  intent. Fix: add a separate shared `DENIAL_OBSERVED` event that is legal only
  while that request's intent is open. It blocks the control domain at once
  and keeps the token held. `INTENT_CLOSED` stays a separate, later event.
- **S2 — `AMBIGUOUS` releases the shared hold (P2).** A
  `intent_closed(outcome='AMBIGUOUS')` clears `open_intent`. After a restart
  the same control domain reopens. The design says ambiguity cannot complete
  and must hold, and that future jobs inherit a control-domain uncertainty hold
  "unless exact closure evidence exists". Fix: do not accept an ambiguous
  close at all, so the intent stays open and is inherited. Alternatively, keep
  it as a durable, non-expiring hold on the control domain and the global
  token.
- **S3 — Session `AMBIGUOUS_HELD` holds nothing (P3).** It is a terminal
  outcome that admits the next `attempt_intent`, including after a restart.
  Fix: an ambiguous outcome must never become TERMINAL-and-released. It should
  be refused, or it should durably block further attempts in the session.
- **S4 — SUCCESS without a store receipt (P4).** `terminal(outcome='SUCCESS')`
  is accepted straight from `ACCOUNTED`. Section 6 says: "ACCOUNTED without a
  store receipt proves accounting only". Fix: SUCCESS must come only from
  `WITNESSED`, and `ACCOUNTED -> terminal` must be non-success only.
  `test_session_ledger_accounted_without_witness_can_reach_terminal` already
  uses FAILED, so it stays valid.
- **S5 — Session `denial` skips closure and accounting (P5).** `denial` moves
  `OPEN`/`RESERVED`/`DISPATCHED` straight to TERMINAL. After a restart nothing
  is inherited, and a new attempt is admitted even though the denied request
  was never transport-closed or accounted and its budget reservation is still
  outstanding. Section 4 steps 4 to 6 still apply after a denial. Fix:
  - A denial should be a non-terminal annotation on a `DISPATCHED` attempt.
    The attempt then still requires `TRANSPORT_CLOSED` and `ACCOUNTED` before a
    non-success terminal.
  - A denial cannot be observed before dispatch. Pre-dispatch blocking is
    `refuse`, so drop `OPEN`/`RESERVED` from `denial`.
  - `refuse` after `BUDGET_RESERVED` remains impossible, which is correct
    (reserved bytes must not be refunded).
- **S6 — Boot identity is never compared (P6).** Both ledgers write `boot_id`
  into `init` but do not compare it on reopen. Section 5 refuses cross-boot
  acquisition, and section 3 binds host/boot. Fix: compare `boot_id` on reopen.
  A mismatch must refuse progression. Read-only inspection may still be
  allowed. Also make `boot_id` a required argument rather than the default
  `'synthetic-boot'`.

### Required, smaller

- **S7 — A NaN clock bypasses an active cooldown (P7).** `is_blocked`
  computes `now_utc < cooldown_until`, which is False for NaN. Fix: validate
  `now_utc` as a finite number. Also validate each denial's `window_end_utc`
  and receipt bound for ordering, for example `receipt_upper_bound_utc` must
  be finite.
- **S8 — The shared root lineage is caller-asserted (P8).**
  - The shared root's `init` binds a single manifest. A second job's manifest is
    therefore refused on the existing root, so the root is not actually
    *shared* across jobs.
  - A fresh directory with the bare boolean `genesis_reviewed=True` reopens a
    control domain that got a 403 elsewhere.
  - `expected_history_head` is optional on non-empty roots, so rolling back to
    a valid prefix (truncating at a record boundary) is undetectable. Section
    3 names this limitation.

  Fix:
  - Bind the shared root to its own root/lineage identity, not to one
    manifest. Record each job's manifest per intent.
  - Make the expected history head mandatory on every non-empty root.
  - Make genesis a digest-bound review reference rather than a bool.

  Root installation and a real external retained head stay out of scope for
  this slice. The limitation must be stated in the module, not implied away.
- **S9 — Overdelivery reaches SUCCESS (P9).** `transport_closed` accepts
  `total_delivered_bytes` greater than the attempt's own
  `max_reservation_bytes`, and the attempt can still finish SUCCESS. Section 4
  says an observed overdelivery halts. Fix: record it, and force a non-success
  outcome plus a global poison/hold.

### Schema gaps slice 3 will need (fix now, or carry as explicit preconditions)

- `TRANSPORT_CLOSED` has no closure monotonic sample. Section 5's restart-safe
  pacing ("sample monotonic time after each known transport closure, persist
  it in TRANSPORT_CLOSED ... A missing closure holds, including after restart")
  cannot be rebuilt from this ledger.
- Shared `INTENT_OPEN` lacks the range/validator and denial-head that section
  3 lists.
- Denial records lack the origin and the original clock evidence.
- Neither ledger pins an expected root device/inode passed in by the caller.
  Unknown directory names are not rejected. The session root binds no
  code/policy/schedule digest.
- `_state()` rebuilds the whole history on every append. That is O(n²) over
  up to 32,768 events, so it is slow but correct. Replay raises a raw
  `KeyError` on schema-incomplete events instead of a `LaunchContractError`.

## Evidence

- Candidate suite: `tests/test_v11_r09_gate3_ledgers.py` **35 passed**.
- Probes: all of `P1`-`P9` give `DEFECT_REPRODUCED` on `39b80fa`.
- `git merge-tree --write-tree 2668609 39b80fa` is clean (tree `6a548a90`).
  It was not merged.
- The wider Gate 3 family was not re-run. The candidate adds only a new module
  and its test file, the Sonnet record shows 372 passed, and nothing is being
  integrated.

## Next

Route the repair to Sonnet/high, on top of `39b80fa` in the same worktree.
It must address S1 to S9 with regression tests that fail on `39b80fa`. The
probe script can be turned into tests. A fresh different-model exact-commit
review of the repaired range `71fc948..<repair>` is then required before
integration. This reviewer will not self-accept code it writes, and has
written no code for this candidate. No C/J/E/A boundary was crossed.
**91/200 (45.5%), formal 1/50; NOT_READY_TO_FUND.**
