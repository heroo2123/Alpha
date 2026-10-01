# Gate 3 V4 slice 2: exact-commit review of `58a465f`

**Verdict: PASS. No P1 or P2 finding remains open.** I reviewed it as an independent, non-author reviewer. This is an offline review only. It grants no G3-L, network, SHADOW, financial, production, credential or V10 authority. Nothing was merged, and I made no changes to the candidate.

## What was reviewed
- **Commit:** `58a465fe2bbcb629d11f6e19d0c1c9969a3765b6`, tree `1336015d8761788c8b9fb58c316017e568572969`.
- **Worktree:** the detached worktree `/tmp/alpha-v11-gate3-slice2-review-58a465f`. Its status is clean before and after the review.
- **Range:** `71fc948..58a465f`. It adds two new files only: `tools/v11_r09_gate3_ledgers.py` and `tests/test_v11_r09_gate3_ledgers.py`. No existing module is touched, so no existing validation could have been weakened.
- **The final commit itself:** it only deletes the inverted `SHARED_LEDGER_DENIAL_ORDER` check (5 lines) and rewrites that one test. The steps `b92a12e→54d00c2` only add checks.
- **Merge check:** `git merge-tree --write-tree d13ed06 58a465f` is clean (tree `55313d59…`). `py_compile` and `git diff --check` are both clean.

## Tests (run by me)
- **Ledgers suite:** 52 out of 52 passed.
- **Wider Gate 3 family:** all 8 `tests/test_v11_r09_gate3_*.py` suites, 364 passed and 0 failed. The only warnings are the 2 known fork warnings.
- **Regression check:** I ran the new R1 test against `54d00c2`'s module. It fails there (1 failed, 51 passed) and passes on `58a465f`, so it really catches the old bug.

## Independent probes
My own probes are in `/tmp/slice2_review_58a465f_probes/probes.py`. There were 39 checks plus a hard-kill durability probe. 37 checks are OK, and the other 2 are the non-blocking notes below.

- **R1 is closed.** I tried in-window denials (receipt at 600, window ending at 10800) across all statuses, with and without Retry-After. In every case:
  - The denial is recorded, and it survives a hard `kill -9` and a reopen.
  - The control domain stays blocked through 10799.999, including after a restart.
  - Closing the request as FAILED or OK is refused, with `SHARED_LEDGER_CLOSE_OUTCOME_DENIAL_MISMATCH`.
  - Finite cooldowns release at exactly 10800, or later when receipt time plus Retry-After is later.
  - 401, 403, explicit denials and denials with a missing expiry never release.
  - The finiteness, schema and evidence-xor checks on denials are all still in place.
- **Holds:**
  - A denial keeps the global token held.
  - An unrelated control domain can continue only after the request is closed.
  - A crash before or after the denial is persisted leaves the request inherited and held, through two restarts.
  - On the session side, an attempt left open at any of the 7 stages is held on every progression call after a restart.
- **Boot identity:** a mismatched boot on either ledger raises `LEDGER_BOOT_ID_MISMATCH` everywhere, and `boot_id` must be passed explicitly.
- **Accounting and witness:**
  - SUCCESS requires a store receipt (`WITNESSED`).
  - A denied attempt can never end SUCCESS.
  - AMBIGUOUS outcomes are refused on both ledgers.
  - A denial cannot be recorded before dispatch.
  - The closure heads and delivered byte count are mandatory on both ledgers.
- **Overdelivery:** `ACCOUNTED` is refused, the attempt is held in `CLOSED`, and the session stays poisoned after a restart. The overdelivery is recomputed on replay. A delivery of exactly the reserved size is not flagged.
- **Rollback and tampering:** a rollback to a valid prefix and a tampered record are both detected.
- **Prior findings:** S1–S9 and R1–R4 are all closed, verified directly rather than from the author's test claims.

## Non-blocking notes (P3), for slice 3
1. **A newer denial overwrites the older record.** `_apply_denial` replaces a control domain's earlier record instead of keeping the later of the two cooldowns. Protection is only lost if the caller passes inconsistent receipt bounds or a clock that runs backwards, which section 5 already forbids. Keeping the later cooldown would be safer.
2. **A shared close can say OK after an overdelivery.** Shared `intent_closed(outcome='OK')` is accepted when delivered bytes exceed that request's own reservation. The session still halts, but the shared record is internally inconsistent. Consider refusing OK in that case.
3. **Carried over from earlier reviews:**
   - The session `expected_head` is still optional.
   - The shared root needs a reviewed boot-epoch reference before it can be used across reboots.
   - `TRANSPORT_CLOSED` has no closure monotonic sample.
   - Shared `INTENT_OPEN` lacks range, validator and denial-head fields.
   - Denial records lack origin and original clock evidence.
   - No expected root device/inode is pinned, and unknown file names are not rejected.
   - `_state()` is O(n²).
   - Replay raises a raw `KeyError` on schema-incomplete events.

GATE3_SLICE2_REVIEW_PASS
