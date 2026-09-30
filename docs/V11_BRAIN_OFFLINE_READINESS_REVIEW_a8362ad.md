# Independent Brain offline readiness review — a8362ad

**CHANGES_REQUIRED. Candidate remains unmerged.**

On 2026-09-30, Astra/high independently reviewed Sol/high's exact commit
`a8362ad907063afaa5f2e97f26c2d7a133d8a934`, tree
`625ef610b0c1e66ddf18b21f071f3cb39ec1f7d9`, in the clean isolated
`/home/alphaadmin/AlphaV11_BrainReadiness/Alpha` worktree. The reviewer made
no candidate edits.

## P2 findings

1. Station/local-date aliases with different caller-supplied `city_day` values
   bypass the split and city-day weighting checks in
   `tools/v11_brain_readiness.py`. The same station/date/HIGH target is accepted
   in DEVELOPMENT and HISTORICAL_CONFIRMATION and counted as two city-days.
   Bind `(station, local_date)` to one canonical city-day and split.
2. Execution cost and markout diagnostics check candidate-bundle and event
   membership separately. A LOW bundle's costs or markout request can be
   attached to a HIGH observation and produce numeric diagnostics. Require
   each execution event to match that observation's exact candidate bundle.
3. Markout diagnostics do not check the actual upstream
   `request.namespace`. A `V11_LIVE` markout with an otherwise matched HIGH
   bundle is accepted. Require `V11_PAPER`.

The handoff's future "exact-day feature adapter" wording is a P3 mismatch to
the adjudicated R09 native sampled-trajectory contract and should be corrected.

## Evidence and scope

The candidate's focused suite passed **8 tests**. Five adversarial executions
reproduced the P2 defects; two positive controls passed. The reviewer also
verified the retained R47 manifest's canonical digest, corrected dataset
digest and four candidate identities. Synthetic-only scoring and withholding
of forward calibration, real multi-model learner admission, promotion and
financial authority remain intact.

An isolated offline repair worker is active in the same clean Brain worktree;
it must add negative regressions, commit a new candidate and obtain a fresh
different-model exact-commit review. No forward-evidence acceptance, SHADOW
qualification, real IFS/AIFS admission, merge or C/J/E/A credit follows from
this review. **91/200 (45.5%), formal 1/50; NOT_READY_TO_FUND**.
