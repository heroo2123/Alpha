# Alpha V11 work checkpoint

Updated 2026-09-23, 20:46 UTC. Resume here. **NOT_READY_TO_FUND**.
This is an implementation checkpoint, not release or financial approval.

## Exact identities and scope

- Branch: `weather-v11-profitability-upgrade-2026-09-23`.
- Last verified implementation HEAD: `7bb382dd8a16682d34d576e59922aed86c41ee16`.
- Last verified implementation tree: `958a4d1e0518b7b9277d404414780902cc13c2c8`.
- Worktree at verification: clean; local and published implementation trees match.
- This following checkpoint commit changes documentation only. Resolve its own
  exact commit with `git log -1 --format=%H -- docs/V11_WORK_CHECKPOINT.md`, and its
  tree with `git rev-parse <that-commit>^{tree}`; no self-referential hash claim.
- Prior implementation: `cc268af500949570e25fab0b8699f58e2c9032c2`, tree
  `d018c39289e0e2331595b83aa774a9eb54bb0baa`. Prior checkpoint `7007b1df650b59e32bbe44e1b5350e719ba201f3`.
- Frozen public base: `f5f0661307a8d426a20dbf8308d18a0ee403e9b3`, tree
  `d5d2b806e273f11e2f832940f483a5f656462584`; no moving-main substitution.
- Local V11 workspace: `/workspace/scratch/38af7099c566/Alpha`.
- Authoritative input SHA-256 verified again:
  `a0e16d9bd7344c943a54a16a53c6757662363d93642f6e5cb7953cd047659b4a`.
  Full specification reread after the user's clarification. All four complete
  reference PDFs were previously read, including relevant tables/figures; their
  actual bytes were rehashed this continuation. See `V11_INPUT_MANIFEST.json`.
- The snapshot handoff supplements the FULL specification. Forensics is a
  prerequisite, not the final deliverable. Continue all remaining phases.

## Phase 0 evidence and findings

The owner completed the capture. Do not ask for it again or overwrite it.
Remote private directory: `/var/tmp/alpha-v11-control-evidence-20260923`.
Snapshot SHA-256: `3a3c1efe0e3021a609800f8c71b9d324fdb0a6e75990e6819321176cd966ed71`.
Capture: 2026-09-23T20:03:51.542021Z. Method:
`SQLITE_BACKUP_PINNED_READ_TRANSACTION`; committed WAL included; quick_check=ok.
All six files, context hashes and schema fingerprint were independently checked.
A complete off-host private archive was transferred and rehashed. Analysis used
SQLite read-only/immutable/query-only access to this copy, never a live store.

Private artifacts were saved successfully:

- `Alpha_V10_Control_Snapshot_20260923_200351.tar.gz`
- `Alpha_V10_Forensic_Baseline_20260923.md`
- `Alpha_V10_Forensics_20260923_v2.json`

Working copies are outside Git at
`/workspace/scratch/38af7099c566/v11-private-evidence/`. Public evidence index:
`docs/V11_V10_BASELINE_FORENSICS.md` (canonical required name). The old filename
now points there. No raw snapshot, financial aggregates, private PDFs or full
master specification were committed to this public repository.

The baseline includes schema, full core accounting, stored settlement bindings,
protocol/config/quarantine cohorts, future-forecast slices, concentration and
selected-sample reliability, no-fill reconciliation, exact-ID-linked delivery
funnels and compressed capture integrity. Missing fields and historical funnel
links remain unknown. Stored labels are not independently re-attested; all
inspected data is DEVELOPMENT, never an untouched holdout. Development hypotheses
are frozen; no economic/calibration/promotion thresholds were fitted.

## Open control-health finding and containment

The clean remote source remains `5bbac24759349714d4521faf9e087a14c5c0ae05`, tree
`d5d2b806e273f11e2f832940f483a5f656462584`. Earlier checks matched all 221 deployed
source/lock files and launcher/unit hashes. Captured status/release marker and
forecast signal labels match the launcher label
`2a1fe2b0d199ca87fabc1f119fae6ee4902f73da`; that label is not the source-tree identity.
Installed version metadata matched 23 runtime pins plus pip 24.0; wheel bytes and
exact historical runtime epochs are not independently attested.

**V10 cycle health is NOT passed:** the captured successful-status timestamp is
materially stale. Read-only cgroup inspection found severe memory pressure above
MemoryHigh, with no OOM kill. Active/running and NRestarts=0 do not prove fresh
cycles. The full cause remains unproven. Protected journals remain inaccessible
through the connected development user. No V10 service, source, limit, permission
or control metadata was modified. Do not restart it for development convenience.

At the latest read-only check, `alpha-weather-execution.service` is MASKED/INACTIVE
and `alpha-weather-controller.service` INACTIVE; host NTP reports synchronized.
Captured flags grant no financial/order authority. Account-specific entitlement,
balances and execution-credential attestation remain unverified.

Host: one CPU; prior available RAM approximately 0.8 GB and free disk approximately
5.3 GiB, with host swap use. The V10 cgroup pressure finding blocks adding another
workload without demonstrated isolation/headroom. All analysis and tests ran
OFF-HOST. No V11 deployment, training or financial process started.

## Implementation and verification

Prior delivered foundation is retained: private append-only evidence namespaces,
causal timestamps/revision replay, bound decision explanations/funnels, bounded
anonymous collection with independent source durability, executable-depth
counterfactual markouts, consistent snapshots and the initial forensic reader.

New implementation: `v11/forensic_detail.py` plus extensions to `v11/forensics.py`:
quarantine override, release/config cohort separation, stored-label identity,
source/member/calendar-lead slices, concentration/realized drawdown, calibration
reliability bins, schema inventory, linked/unlinked signal/decision funnels,
bounded compressed-capture verification and explicitly limited runtime context.

| Check | Result |
|---|---|
| Original pinned baseline | 2,336 passed; four existing warnings |
| Prior implementation regression | 2,389 passed; four existing warnings |
| New full regression | **2,394 passed; four existing warnings; 65.53 s** |
| Targeted V11 foundation tests | **58 passed** |
| Snapshot/forensic tests | **18 passed** |
| Compileall / dependency check / diff whitespace | passed |
| Prior GitHub Actions run 35911031597 at cc268af | completed successfully |
| New implementation CI | not yet inspected; not claimed passed |

Tests used an isolated off-host environment installed from hash-locked dev
requirements. Four warnings are pre-existing FastAPI lifecycle deprecations.
Synthetic tests are code evidence only. No independent reviewer has reviewed V11.

Under the fixed 50-package matrix, R01 is implemented and locally verified;
**1/50 = 2%** complete. Partials/stubs/open integrations receive zero completion
credit. V10 operational health remains open under R00/R44/R46. Local code,
technical readiness, canary eligibility and empirical validation stay separate.

## Models, authority and exact continuation

- No V11 live/paper/control/challenger ledger is shared or migrated.
- No initial champion, challenger, causal training manifest or model promotion.
- No actual PWS lead, executable-exit or maker-fill validation.
- No live-eligible strategy/station; no funding/account creation/transfer/order.
- No executor mask change or real-money activation. No arbitrary paper waiting
  period is imposed; mandatory technical/evidence gates remain.

Next engineering action: integrate durable evidence/decision/funnel/replay paths
with source normalization/scheduling, capability-scoped station certification and
universal rule fingerprints. Then follow the full dependency order through
coherent valuation/calibration, controlled learning, event/EV/coordinator/risk,
strategy migration, exits, maker, guardian, deployment and unfunded acceptance.
Reuse tested existing modules. Preserve this checkpoint and publish nonsecret
code milestones throughout.

Routine inspection/queries/analysis are authorized and remain the integrator's
work. The completed snapshot needs no further owner action. If owner-only journal
inspection or commissioning becomes necessary, prepare one precise reviewable
step; continue safe independent engineering meanwhile. Never ask for secrets in
chat or weaken access controls. Runtime acceptance and funding readiness remain
blocked; the full implementation task is still in progress.
