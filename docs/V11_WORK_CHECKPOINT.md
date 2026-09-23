# Alpha V11 work checkpoint

Updated 2026-09-23, 22:40 UTC. Resume here. **NOT_READY_TO_FUND**.
This is an implementation checkpoint, not release or financial approval.

## Exact identities and scope

- Branch: `weather-v11-profitability-upgrade-2026-09-23`.
- Last verified implementation HEAD: `17215b7a7fc0af3917a082bf575b6ed31ee8b1c2`.
- Last verified implementation tree: `a8cd460f45baa80bd0c8cbcffce476a009afc689`.
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

## Phase 1/2 source and identity implementation

Published after forensics: capability-scoped station metadata/history, protected
review manifest reader (not provisioned), monotonic demotion and reviewed recovery;
universal rule preimages with archived-raw recompilation and atomic drift state;
durable source reservations/cooldowns and same-host 429 suppression; bounded
MADIS CWOP XML and AWC proxy normalization; partial-success observation cycles,
normalization health and source-ready funnels. No component grants financial
authority. Full strategy, operator, execution and guardian propagation remains
pending and is not marked complete.

A real anonymous free-public CWOP access/coverage probe completed around all 13
control settlement-station areas. All response hashes were checked after private
transfer and parsed by the new adapter. This is one geographic coverage sample,
not PWS lead/calibration/representativeness evidence or a deployed V11 shadow.
Provider publication/receipt times are absent in this XML format; observation
age at local receipt must not be labeled network latency.

Additional private artifacts saved:

- `Alpha_V11_CWOP_Access_Coverage_20260923.md`, SHA-256
  `65368c1795d5566e39e4f96aa794f666a99fb4aee4ad4bd2be003e22f1f04868`
- `Alpha_V11_CWOP_Coverage_20260923.json`, SHA-256
  `5b8074f00a382429d685d3a1d37097125a0590d8468edf2a31e1b446da0ba501`

Queries were bounded, anonymous, sequential, low-priority and resource-limited;
no accounts or messages were created. Public docs retain official source links
and implementation limits; raw weather observations stay private. The V10
resource/freshness finding remains open.

## Phase 3 / 3A probability and dataset milestone

Published `v11/probability.py` and `v11/datasets.py`, with dedicated tests and
`V11_PROBABILITY_ENGINE.md` / `V11_DATASET_PROVENANCE.md`. Coherent CDF vectors,
dependence-group budgets, separate vacuous conservative bounds, correct NO
complements, explicit observation/payout targets, exact-observation max/min and
full local-day accepted/unresolved coverage are implemented. Missing model run
age, revision risk, source fallback and calibration evidence remain explicit.

Feature schema/value bounds, receipt-bound derivation DAGs, immutable label
revisions, training-label cutoff, temporal/event/city-day partitions and all
attempt/holdout-reveal accounting are implemented. Reused confirmation is
DEVELOPMENT; V10 inspected data cannot become untouched confirmation. Stored
labels are still not independently attested. No actual fit, initial champion,
model promotion or deployment occurred. Full source/model/strategy integration
remains pending; these packages are partial in the matrix.

The continuation inspected workspace and running operations before retrying any
work. There were no unfinished project operations or pre-existing uncommitted
changes. Publication preserved working files and used a same-tree commit-ref
alignment; no reset, clean or rollback was used. Prior Git verification was
closed PASS, not repeatedly rerun over unchanged state.

## Phase 3A artifact, governance and bounded learner milestone

Published `v11/model_artifacts.py`, `v11/model_registry.py`,
`v11/offline_learning.py` and standalone
`host_trust/v11-model-authority/authority.py`. Added documentation for model
registry, continual learning and governance. The root-side helper is PREPARED,
not installed; it currently permits nonfinancial paper/shadow modes only.

Data-only five-component bundles enforce shape, numeric bounds, exact target and
feature compatibility, probability/calibration binding and complete provenance.
Immutable content-addressed objects, reviewed state epochs and decision pins are
tested. The research writer has no active-pointer interface. The separate helper
requires protected reviews and installed matching bytes; it imports no candidate
code. Atomic pointer/overlay/history publication, stale-parent rejection,
monotonic demotion, reviewed recovery and rollback are tested. Interruption
before rename retains the old epoch; uncertainty after rename requires actual
state reconciliation. No production custody or independent review is claimed.

A bounded deterministic grid learner selects only on TRAIN, records all trials,
compares the exact parent on identical causal data and reports grouped metrics,
bootstrap uncertainty, slice regressions, concentration and parent-parameter
ablation. Sparse support or resource failure preserves the parent. It returns
NO_PROMOTION pending independent label/dependence review. Only synthetic test
fits have run; no actual V10 dataset was trained or champion activated.

Dataset integration also tightened payout targets to exact market/condition/
token/side. This targeted correction preserves the original snapshot/forensics.
The V10 source/runtime/resource finding and executor containment are unchanged;
no new alpha-dev checks or host workloads were needed for this off-host milestone.

## Remaining PWS dependency milestone

Published `v11/pws_quality.py` and `docs/V11_PWS_QUALITY.md`. Raw XML is recompiled
before normalized inputs are used. Implemented explicit causal/provider/physical/
freshness/jump/rate/gap/duplicate/neighbor checks, versioned distance/elevation
weighting, flat-sensor downweighting, receipt-preserving dedupe, full-history
metadata drift and durable relocation quarantine. Features include median/IQR,
spread, actual-endpoint trends, local observed envelopes, spatial gradient where
identified and fresh source-labeled official residuals.

23 new focused tests and the full V11 targeted suite (209 tests) pass. The broad
repository regression was not repeated for this isolated additive module; its
last complete run remains 2,522 passing at model/learning implementation
`b9de1fb2ca34d5eb0b6581bbbc9dd93c6aef4c80`. No unchanged Git checks were rerun.
No alpha-dev query or workload was added. Historical learned reliability, paired
official/PWS lead, exposure/terrain inference, ablation and complete source/event/
strategy integration remain open. PWS output is informational with trading
influence explicitly false.

## Physical nowcasting and source dependency milestone

Published `v11/metar_features.py`, `v11/nowcast_features.py` and dependency-aware
observation routing. Raw METAR bodies supply optional wind/dewpoint/cloud/weather
features with explicit units and missingness. Receipt-bound trajectories reset
across gaps; daylight requires causal, exact station/local-day forecast evidence.
PWS feature outages stay missing and cannot disable unrelated official paths.
All planned requests for a required provider must succeed; one success cannot
hide a required sibling failure. Family ablations are archived with provenance.
No feature is labeled incrementally valuable without out-of-sample evidence.

The interrupted full regression completed PASS: **2,564 passed, four existing
warnings, 63.64 s**. Focused physical/source checks: **43 passed** (17 new physical
cases and two new routing cases). No test remains running. Existing workspace
changes were preserved and published; no reset, clean, duplicate regression or
new alpha-dev workload occurred. Full inference/economic integration and actual
ablation validation remain pending. This is local code verification, not a new
fully accepted package or readiness gate.

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
| Forensic implementation regression | 2,394 passed; four existing warnings; 65.53 s |
| Latest source/identity implementation regression | **2,423 passed; four existing warnings; 65.16 s** |
| Probability / dataset focused tests | **31 / 20 passed** |
| Artifact / governance / offline learner focused tests | **19 / 21 / 8 passed** |
| Latest probability/dataset full regression | **2,473 passed; four existing warnings; 61.10 s** |
| Latest model/learning full regression | **2,522 passed; four existing warnings; 63.96 s** |
| PWS defensive QC focused tests | **23 passed** |
| Prior targeted V11 suite | **209 passed; 2.03 s** |
| Physical/source integration focused tests | **43 passed** |
| Latest physical/source full regression | **2,564 passed; four existing warnings; 63.64 s** |
| Snapshot/forensic tests | **18 passed** |
| Compileall / dependency check / diff whitespace | passed |
| Prior GitHub Actions run 35911031597 at cc268af | completed successfully |
| Probability/dataset GitHub Actions run 35924652710 at 359814e | completed successfully |
| Newer model/PWS implementation CI | not yet inspected; not claimed passed |

Tests used an isolated off-host environment installed from hash-locked dev
requirements. Four warnings are pre-existing FastAPI lifecycle deprecations.
Synthetic tests are code evidence only. No independent reviewer has reviewed V11.

Under the fixed 50-package matrix, R01 is implemented and locally verified;
**1/50 = 2%** complete. Partials/stubs/open integrations receive zero completion
credit. V10 operational health remains open under R00/R44/R46. Local code,
technical readiness, canary eligibility and empirical validation stay separate.

## Models, authority and exact continuation

- No V11 live/paper/control/challenger ledger is shared or migrated.
- Causal dataset, immutable bundle and bounded learner infrastructure implemented; only synthetic test challengers, no actual V10 fit, accepted champion or model promotion.
- Public CWOP access/coverage verified; no actual PWS lead, executable-exit or maker-fill validation.
- No live-eligible strategy/station; no funding/account creation/transfer/order.
- No executor mask change or real-money activation. No arbitrary paper waiting
  period is imposed; mandatory technical/evidence gates remain.

Next engineering action: implement durable event-risk states and target-specific
executable EV using the tested probability/model/evidence interfaces. Causal
physical feature candidates and source dependency routing are implemented;
feature value/inference validation remains pending. PWS defensive QC/spatial
features are implemented; historical reliability and economic/lead validation
remain pending. Exact-source labels, empirical calibration,
learning scheduling/OS isolation, approved initial champion and financial model
commissioning remain open; do not treat this foundation as acceptance. Continue integration of the delivered observation,
registry/rule and evidence interfaces through event/EV/coordinator/risk,
strategy migration, exits, maker, guardian, deployment and unfunded acceptance.
Reuse tested existing modules. Preserve this checkpoint and publish nonsecret
code milestones throughout.

Routine inspection/queries/analysis are authorized and remain the integrator's
work. The completed snapshot needs no further owner action. If owner-only journal
inspection or commissioning becomes necessary, prepare one precise reviewable
step; continue safe independent engineering meanwhile. Never ask for secrets in
chat or weaken access controls. Runtime acceptance and funding readiness remain
blocked; the full implementation task is still in progress.
