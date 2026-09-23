# Alpha V11 work checkpoint

Updated 2026-09-23. Read this before continuing. This is an implementation
checkpoint, not release approval. Verdict: **NOT_READY_TO_FUND**.

## Source identity

- Branch: `weather-v11-profitability-upgrade-2026-09-23`
- Last verified code HEAD: `f5f0661307a8d426a20dbf8308d18a0ee403e9b3`
- Last verified tree: `d5d2b806e273f11e2f832940f483a5f656462584`
- Worktree: dirty; first V11 evidence work in progress.
- Checkpoint commit: obtain with `git log -1 --format=%H -- docs/V11_WORK_CHECKPOINT.md`.
- Authoritative input: `V11_INPUT_MANIFEST.json`; all specification bytes and
  four complete reference PDFs read, with table/figure inspection.

## Progress and verified state

Phase 0 is incomplete; Phase 1 independent implementation has started.
Overall completion: **0% of implementation work packages complete** at this
initial checkpoint. The denominator and evidence status are in
`V11_REQUIREMENTS_MATRIX.md`. No partial or blocked package earns credit.
Implementation completion, technical acceptance, canary eligibility and
empirical validation are separate.

The supplied local control checkout is clean at source-equivalent commit
`5bbac24759349714d4521faf9e087a14c5c0ae05` and the expected tree. All 221
deployed application/lock files compared match. The public remote checkpoint's
CI run 35453442699 completed successfully. This is historical CI evidence,
not a new V11 test pass.

V10 remains active without changes. Executor: MASKED + INACTIVE. Production
controller: INACTIVE. Unit filesystem isolation excludes execution credentials
and financial state; no new privileges, empty capability set, protected system
and home. Clock synchronization reports healthy. Runtime status, database,
release marker and journal are unreadable by the connected development user;
do not claim their content has passed. The readable launcher still names an
older release marker; a protected-state snapshot must resolve this distinction.

One CPU, about 782 MiB available RAM, active swap use, 5.5 GiB free disk; V10
uses about 433 MiB of a 500 MiB cap. No host testing/training/shadow service was
started. Development and tests run off-host.

## Models and deployments

- V11 deployment: none; no V10 changes or Telegram consumer.
- V11 champion/challenger artifacts: none; no promotions/demotions/rollbacks.
- Latest learner dataset/watermark: none; no training run.
- Live account entitlement, credential metadata and balances: unverified.
- Financial authority: no change; no funding, wallet creation or real orders.

## Open findings and next action

1. BLOCKED: read access to control evidence; `sudo -n -l` requires a password.
   Prepare a bounded snapshot tool and one precise owner action. Do not widen
   source permissions or analyze a changing control database.
2. Phase 0 must distinguish source-tree identity from launcher's release marker,
   installed dependencies and actual healthy cycles.
3. Current official auth documentation requires Session Builder entitlement;
   EOA trading is conditional on allowlisting. Neither is account-attested.
4. Current NOAA CWOP policy allows public data, but continuous MADIS access and
   station-specific usable latency/coverage remain to be demonstrated.

Next engineering action: complete and test the read-only bounded snapshot and
causal evidence foundation; inspect V10 schema for a snapshot-only forensic
reader. Continue independent work while the protected-evidence blocker remains.
