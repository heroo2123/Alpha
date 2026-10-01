# Gate 3 V4 slice-two repair review — b92a12e — CHANGES_REQUIRED

Reviewer: Claude Opus 5.5. This is a different model from the Sonnet author
of `b92a12e`. Candidate: `/home/alphaadmin/AlphaV11_Gate3V4Slice2/Alpha`,
branch `r09-gate3-v4-slice2-20261001`, commit
`b92a12ea8e4337acee3552473415fba4a088161d`, tree `eb5ee70a`. The range is
`71fc948..b92a12e`, which is `39b80fa` plus the repair. It is checked against
sections 3 to 6 of the
[transport runtime design](V11_R09_GATE3_TRANSPORT_RUNTIME_DESIGN.md) and
against the findings of the [39b80fa review](V11_R09_GATE3_V4_SLICE2_REVIEW_39b80fa.md).
The private master's hash was verified in place. Nothing from it is copied
here. This review is offline only. It grants no G3-L, launch, network, SHADOW
or financial authority.

## Verdict: CHANGES_REQUIRED

All nine original findings are closed. The
[probe script](V11_R09_GATE3_V4_SLICE2_REVIEW_b92a12e_probes.py) ports P1-P9
to the repaired API, and all nine report `NOT_REPRODUCED` (`SUMMARY_A 0 of 9`):

| ID | Result on b92a12e |
| --- | --- |
| S1 | `denial_observed` keeps the token held (`SHARED_LEDGER_INTENT_OPEN_HELD`) |
| S2 | `AMBIGUOUS` close refused; after restart the intent is inherited and held |
| S3 | `AMBIGUOUS_HELD` refused; after restart the attempt is inherited and held |
| S4 | `SESSION_LEDGER_SUCCESS_REQUIRES_WITNESS` |
| S5 | after restart the denied attempt is inherited, and the next attempt is refused |
| S6 | both ledgers raise `LEDGER_BOOT_ID_MISMATCH` on progression |
| S7 | `SHARED_LEDGER_NOW_UTC` |
| S8 | a head is required on reopen; a second manifest is accepted on the same root; the 403 domain stays blocked; a bool genesis is refused |
| S9 | `SESSION_LEDGER_OVERDELIVERY_BLOCKS_SUCCESS`, and the session stays poisoned after restart |

The repair itself introduces two regressions, and two design requirements are
still missing. All four reproduce on the exact commit (`SUMMARY_B 4 of 4`).

### Blocking

- **R1 — The S7 ordering check rejects every in-window denial
  (regression; probe N1).** `_validate_denial` requires
  `receipt_upper_bound_utc >= window_end_utc`. Section 3 stops the control
  domain "for the whole original window". The finite cooldown is "at least
  that window end". A denial arrives during the window, so its receipt bound
  normally *precedes* the window end. The new check rejects exactly that normal
  record (`SHARED_LEDGER_DENIAL_ORDER`). The caller then has no way to record
  the denial. It can still close the intent `FAILED`, and in the probe the
  domain then reads as **not blocked**. `39b80fa` had no such check.
  `test_shared_ledger_denial_rejects_receipt_bound_before_window_end` encodes
  the wrong rule. Fix: remove the ordering check and keep the finiteness
  checks. Add a test where a denial with receipt bound 600 and window end
  10800 is recorded and blocks until at least 10800.
- **R2 — A denied session attempt can terminate SUCCESS (regression; probe
  N2).** S5 correctly made `denial` a non-terminal annotation. But `terminal`
  never checks `denial_observed`, so the path
  `denial -> TRANSPORT_CLOSED -> ACCOUNTED -> WITNESSED -> SUCCESS` is
  accepted. The module's own docstring says "(necessarily non-SUCCESS)
  terminal". In `39b80fa` a denial could never be SUCCESS. Fix: refuse
  `outcome='SUCCESS'` when `attempt['denial_observed']`. Optionally, refuse
  `object_witnessed` after a denial too, because a denied body is not a
  "successful validated body" (section 6).
- **R3 — Shared `INTENT_CLOSED` carries no closure evidence (probe N3).**
  Section 4 step 5 says to persist "shared `INTENT_CLOSED`, binding
  outcome/denial history and accounting head, before `budget.complete`". It
  also says that "Shared INTENT_CLOSED must contain a complete bounded outcome;
  it cannot merely mean the caller invoked close()". The 39b80fa review
  quoted this under S1, but the repair still records only
  `{op, request_id, outcome}`. It also accepts `outcome='OK'` after a denial
  was recorded on the same intent. Fix:
  - Require an `accounting_head` digest, `total_delivered_bytes`, and the
    shared denial-history head at close.
  - Require `outcome == 'DENIED'` exactly when a denial was observed.
  - Make the session `TRANSPORT_CLOSED` `denial_history_head` and
    `accounting_head` mandatory rather than optional, for the same reason.

### Required, smaller

- **R4 — Overdelivery still admits ACCOUNTED (probe N4).** Section 4 step 5
  says "ambiguity or stream/journal violation cannot [complete]".
  `ACCOUNTED` binds a budget completion-event hash, yet it is accepted after a
  `TRANSPORT_CLOSED` that recorded `overdelivered=True`. Fix: refuse
  `accounted` after an overdelivery. The attempt then stays held, and
  the session is already poisoned. Also recompute `overdelivered` from
  `total_delivered_bytes` and the attempt's reservation at replay, instead of
  trusting the stored flag through `event.get`.

### Non-blocking notes, carried to slice 3 as explicit preconditions

- The shared root binds the genesis `boot_id`, so after any reboot it refuses
  progression permanently. That is fail-closed and consistent with "cross-boot
  acquisition is refused". However, a root meant to be shared across jobs over
  time then needs a reviewed, append-only boot-epoch or resumption reference
  (section 3) before slice 3 can use it across reboots. State this in the module.
- `SessionLedger.expected_head` is still optional. Rolling a session root back
  to a valid prefix can therefore erase an inherited open attempt. Either make
  it mandatory on a non-empty root, as on the shared root, or state the
  limitation.
- These gaps from the 39b80fa review are unchanged:
  - `TRANSPORT_CLOSED` has no closure monotonic sample, which section 5
    pacing needs.
  - Shared `INTENT_OPEN` lacks range, validator and denial-head.
  - Denial records lack origin and original clock evidence.
  - No expected root device/inode is pinned, and unknown names are not
    rejected.
  - `_state()` is O(n²).
  - Replay raises a raw `KeyError` on schema-incomplete events.

  The closure sample can naturally be added with R3.

## Evidence

- Candidate suite `tests/test_v11_r09_gate3_ledgers.py`: **49 passed**
  (`/home/alphaadmin/alpha-review-test-venv`, 0.50 s).
- Probes: P1-P9 all `NOT_REPRODUCED`; N1-N4 all `DEFECT_REPRODUCED`.
- `git merge-tree --write-tree ffe2d0d b92a12e` is clean (tree `ebec33de`).
  It was not merged.
- The wider Gate 3 family was not re-run. The candidate touches only the new
  module and its test file, nothing is integrated, and the Sonnet record shows
  361 passed.

## Next

Route a second repair to Sonnet/high, on top of `b92a12e` in the same
worktree. It must fix R1 to R4 and add regression tests that fail on
`b92a12e`. The N1-N4 probes can be turned into tests. It must also correct
the inverted S7 ordering test. Then a fresh different-model exact-commit
review of `71fc948..<repair>` is required before integration. This reviewer
wrote no candidate code. No C/J/E/A boundary was crossed.
**91/200 (45.5%), formal 1/50; NOT_READY_TO_FUND.**
