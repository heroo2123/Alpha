# Alpha V11 work checkpoint

Updated 2026-09-25, after receipt-cost and causal price-comparison audit integration. Resume here. **NOT_READY_TO_FUND**.
This is an implementation checkpoint, not release or financial approval.

## Latest verified checkpoint — receipt costs and candidate audits

Published implementation **79a7b1e98388c34a9c817fc567cef5867ea59e0e**, tree
**1852925219ce2ab0453b97a226fd0bbae44dc1f1**, passed the locked full regression:
**4046 passed, four existing FastAPI warnings, 347.89 s, exit 0**, session
**49643**. All **845 tracked inputs unchanged**, reverified before this report.
Wrapper 348.766 s; user 208.85164 s; system 129.962257 s; peak RSS 165020 KiB.
Affected suite **245 passed in 35.93 s, exit 0**, session **73652**, on the same
unchanged tree. Final new suite **26 passed in 6.99 s**, session **99815**, exit 0.
No failed run in this unit. No full test remains running; its lock is free.

Exact metadata, shared complete manifest and captured outputs:
**docs/V11_EXECUTION_COST_REGRESSION_EVIDENCE.md**. Local result:
`/workspace/scratch/38af7099c566/v11-test-evidence/execution-cost-full-20260925-01.json`
and corresponding `.log`; input digest
`9e4829135de9a295c010e5ddeb6ddabea38a50fb1e67ca5f7bda7e811f073164`.
This final report changes documentation only. Resolve the latest reporting save
with `git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md`.

Completed: exact reconciled synthetic cost population, explicit optional execution
details, original signal/post-validation full marginal depth, correct BUY/SELL
shortfall signs and additive decomposition, legacy/malformed unknowns, pinned
execution windows, and candidate scheduled audit integration/recovery. Costs stay
inside existing all-in ledger amounts, with zero additional P&L adjustment. No
venue execution, empirical impact, matched EV, finality or deployment is inferred.

**82/200, approximately 41%**, formal **1/50 (2%)**, unchanged. Existing C/J credits
cover this work; no actual-evidence or acceptance gate closed. **NOT_READY_TO_FUND**.
V10 unchanged; maintenance DEFERRED. No host/service, wallet, permissions, orders
or financial authority changed. No owner-only action blocks the next coding step.

Exact next implementation action: add a bounded read-only historical model/evidence
context for deterministic replay, then join the existing strategy evaluator and
common-account PAPER path. Code inspection found EvidenceStore.replay still takes
a caller-supplied evaluator, while TemperatureStrategies and StrategyAdmission read
the current protected model epoch. Replay must bind the original model/receipt
boundary and compare original decisions/account effects after later revisions or
promotions, without substituting current heads or changing active approval gates.
Missing historical inputs must be explicit gates. Inspect those seams before any
refactor; do not repeat collection or original economic commands. Full engine,
PWS pre-confirmation and challenger-same-input replay remain open in R04.

The six readiness milestones/active-hour ranges below remain current. All have
LOW confidence and exclude external waiting and owner-dependent actions. Actual
source/history/labels/calibration, independent reviews and approved isolated host/
unfunded access block readiness; they do not block this safe off-host replay work.

## Historical implementation checkpoint — reconciled execution costs and candidate audits

Recovered clean published/local **495124429647627f35bf2e0b66883b35c23be825**,
tree **2a7e6b768fecd1aaa323b3320a19da55e4d3500a**. All 754 non-document inputs
still matched the saved 4020-pass regression; no operation was running and the
full-regression lock was free. The original master SHA-256 was reverified. Newer
work and all V10 evidence were preserved.

`execution_costs.py` now joins every retained reconciled PAPER fill to validated
optional execution details and the exact original signal/post-validation books.
It reports receipt-declared fees/other costs separately from gross price shortfall,
uses adverse-positive BUY/SELL signs, and decomposes signal-to-post price movement
from post-to-fill shortfall. Partial fills consume cumulative depth per intent and
exact book; earlier out-of-window fills still consume that depth. Unknown execution
timing preserves every possible overlapping member and gates partial-fill ordering.
Stale, unhealthy, crossed, shallow or invalid-lineage books remain UNKNOWN without
discarding independently validated costs. Groups never pool directions/classes.

PerformanceLab and the typed candidate's scheduled AuditWorker share an explicit
optional ExecutionCostPolicy. Default report/config/assembly identities remain
unchanged. Account snapshots, immutable receipt references and replay preserve the
original population; later fills cannot rewrite published reports. Cost totals
cover execution-time windows, separately from realized-P&L cohorts. All costs are
already in the common ledger: no cash/P&L adjustment or allocation of joint basket
EV occurs. These synthetic comparisons do not attest venue execution, impact or
matched realized EV. Bounds gate whole results instead of emitting a good prefix.
The publishing worker adds at most a declared two-second cooperative read budget;
independent cancellation/host guarantees remain open.

Final focused verification: **26 passed in 6.99 s, exit 0**, session **99815**;
initial 22 passed in 3.15 s, session 29001. No failed run in this unit. Cases include
archived detailed fill -> bounded candidate receipt delivery -> common account ->
scheduled daily audit, pinned replay/crash recovery, quantity/depth conservation,
BUY/SELL/fractional quantities, malformed/legacy evidence and policy changes.
Affected and full saved-tree regression are the next verification gates. This
implementation is not covered by the earlier 4020-pass result. Resolve its save
with `git log -1 --format='%H %T' -- polymarket_scanner/v11/execution_costs.py`.

**82/200, approximately 41%**, formal **1/50 (2%)**, unchanged: this extends
already-credited R05/R40/R41 integrations and earns no new named milestone.
**NOT_READY_TO_FUND**; V10 unchanged and maintenance DEFERRED. No host, wallet,
order, service, permission or financial authority changed. No owner-only action
blocks continued off-host implementation. Actual source/calibration evidence,
independent safety/review and isolated host/unfunded acceptance remain blockers
to readiness, not to the next coding action.

Exact next action: run the affected reporting/candidate/reconciliation checks,
then one locked full regression on this saved tree and preserve its complete input
manifest/output. Next implementation: connect pinned causal replay to the actual
strategy/common-account PAPER candidate, comparing original decisions and account
effects without latest-evidence substitution or external execution. Inspect the
existing EvidenceStore replay and candidate seams first; do not rerun collection
or duplicate original economic commands. The six readiness milestones/hours below
remain current, excluding external waiting and owner-dependent actions.

## Historical verified checkpoint — archived receipts, fresh inventory and exits

Saved/published implementation **5bfd38f0caa1f891738df459826fd1a7d6a4e203**, tree
**5b29ee49eba4ed46639205b4d3cc0916f6f97789**, passed the locked full regression:
**4020 passed, four existing FastAPI warnings, 276.83 seconds, exit 0**, session
**42473**. All **843 tracked inputs unchanged**, reverified before this report.
Wrapper elapsed 277.584 s; user 200.572825 s; system 67.346589 s; peak RSS 163248 KiB.
No failed/excluded test in this run; no full regression remains active and the lock
is free. This report changes documentation only. Resolve its latest reporting
commit/tree with `git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md`.

Exact full/affected manifests and captured outputs:
**docs/V11_RECONCILIATION_REGRESSION_EVIDENCE.md**. Local full result:
`/workspace/scratch/38af7099c566/v11-test-evidence/reconciliation-full-20260925-01.json`
and corresponding `.log`; input digest
`9474ee2793f46420a0b7a28ee83b2bbe806efd0934bba07af89e30b33d80e060`.
Final event/exit regression **91 passed in 9.84 s**, session **46830**, exit 0;
focused **40 passed in 6.33 s**, session **70198**, exit 0. Preceding receipt tree
**031779b3596c08964b63e370e5d6caae8bc91cc6** passed **328 / 39.18 s**, with all
842 inputs unchanged. Development assertion corrections are preserved below.

Completed: bounded archived explicit PAPER fill/terminal delivery; durable pending
proofs and cursor recovery; atomic receipt/journal/account admission and submission
guards; configured startup gating; malformed/foreign/public separation; proof-based
terminal release; health-loss cancellation; fresh event generations and census after
account changes; existing whole-event exit/common SELL reservation; explicit SELL
reconciliation and realized PAPER attribution; candidate monitoring and daily audits.
Crash replay preserves cash, reservations and lots across both account and queue
commits. Unconfigured legacy identities and existing proof/source/exit checks remain.
Public prints and maker quotes are never inferred to be fills. Changes since the
recovered 95f37a1 reporting checkpoint are confined to V11 code/tests/docs.

**82/200, approximately 41%**, formal **1/50 (2%)**, unchanged. Existing integration
credits apply; no actual source/calibration, independent guardian/review, host or
unfunded acceptance milestone closed. **NOT_READY_TO_FUND**. V10 unchanged;
maintenance DEFERRED. No host/service, permissions, wallet, orders or financial
authority changed. No owner-only action blocks the next off-host work.

Exact next implementation action: join validated execution details from reconciled
receipts to PerformanceLab cost/slippage reporting and daily audits, with exact
original signal/post-validation book and fill-quantity pairing. Keep legacy,
incomplete or unmatched cost/price evidence UNKNOWN; do not invent matched EV,
settlement/finality, calibrated provider truth or actual venue execution.
The six remaining readiness milestones/active-hour estimates below remain current,
all LOW confidence, excluding external waiting and owner-dependent actions.

## Historical integration checkpoint — receipt-driven fresh inventory and exits

The preceding receipt implementation is saved/published as
**031779b3596c08964b63e370e5d6caae8bc91cc6**, tree
**12d687b57e9c5fded1d59b942fc2c2edbe28b8c1**. Its affected regression passed
**328 tests in 39.18 s, exit 0**, session **33408**, all **842 inputs unchanged**;
wrapper 39.490 s, peak RSS 64180 KiB. The exact manifest/output is now recorded in
`docs/V11_RECONCILIATION_REGRESSION_EVIDENCE.md`.

Continued the remaining account-to-event join: the candidate receipt worker now
shares its exact EventQueue. Successful account reconciliation requests fresh
census/evaluation without creating a market trade or artificial source notice.
Existing source-loss findings survive; census generations advance and old or
in-flight evaluations cannot admit against pre-fill inventory. Receipt-derived
account and event command identities survive crashes before/after queue delivery.
Only after both joins succeed may that receipt advance out of the pending journal.
Absent/expired registered routes remain explicitly reported. Optional unconfigured
runtime/assembly behavior and protected-source/exit gates remain unchanged.

Final focused result: **40 passed in 6.33 s, exit 0**, session **70198**, including
archived BUY fill -> fresh source census -> existing whole-event exit decision ->
common-account SELL reservation -> archived SELL receipt -> retained lot and
realized PAPER P&L -> fresh reevaluation, with replay preserving the account.
No manual record_fill/reconcile_terminal call is used in that integrated case.
A development run had 2 failed / 37 passed in 5.26 s: an incorrect test census
method name and an incorrect assertion that fresh census books could not trigger
the ordinary source feed. Assertions corrected; production source/census gates
were not weakened. The full saved-tree regression is the next verification gate.

Resolve this integration save with `git log -1 --format='%H %T' --
polymarket_scanner/v11/event_queue.py`. The historical 3981-pass run is not a
verification claim for these new changes. No full test remains running at this save.

**82/200, approximately 41%**, formal **1/50 (2%)**, unchanged: existing C/J
integration scope, no new actual-evidence or acceptance milestone. V10 remains
unchanged and maintenance DEFERRED. **NOT_READY_TO_FUND**. The six remaining
milestones/hours below cover the full engineering scope; all LOW confidence,
external waiting and owner actions excluded. No owner-only action blocks safe
off-host work.

Exact next action: verify affected event/exit behavior, then run one locked full
regression on this saved tree and preserve the exact manifest/output. The next
implementation after verification is to join validated receipt execution details
to PerformanceLab cost/slippage reporting and daily audits; preserve UNKNOWN for
legacy or unmatched causal price/cost evidence, without inventing matched EV,
finality, provider calibration or venue execution acceptance.

## Historical implementation checkpoint — archived PAPER receipts through candidate reconciliation

Recovered published/local **95f37a182d02401355dabfcc66ecd5dad0283486**, tree
**32bcd474d94d23208af6cd35fae502bd4c20249d**, with a clean workspace, free regression
lock, no running operation, and all 752 non-document implementation inputs matching
the prior 3981-pass run. The authoritative master hash was reverified. Newer work,
V10 evidence and deferred maintenance were preserved. No owner action was needed.

Implemented `paper_reconciliation.py`: explicit PAPER_FILL/PAPER_TERMINAL archive
receipts now reach the existing common-account proof APIs through bounded scans,
a durable cursor/pending journal, one nonblocking worker lock and deterministic
receipt commands. Candidate priority ticks reconcile before new admissions and
before cancellation consumes the account snapshot. Both configured startup and
activated account journals fail closed; other same-account coordinators must obey
activated progress. Admission/submission atomically pin account, journal and the
receipt frontier. A concurrent receipt or activation invalidates the admission.
Unrelated public prints do not change that frontier.

Malformed, unknown, mismatched or incomplete terminal proofs remain visible and
pending; later valid receipts still process within bounds. Only explicit valid
foreign identities are classified as foreign. Pending-capacity exhaustion leaves
the unretained receipt behind the cursor. Cumulative quantity and terminal authority
remain the existing coordinator checks; ambiguity retains reservations. Crashes
after an account commit replay the same economic command. Clock regression does
not rewrite timestamps or expand safety-write authority. Health loss and worker
errors retain cancellation service. A quarter of the tick budget bounds this
cooperative receipt slice; independent guardian and deployment gates remain open.

The public source feed uses matching account-envelope classification, including
malformed markers, so these cannot become market prints or stop later feed progress.
Audits separately expose journal outcomes, delivery-attempt outcomes and pending
count (attempts are not unique fills). Legacy unconfigured runtime/plan identities
remain unchanged. No network, order, wallet, financial or deployment authority added.

Targeted result: **34 passed in 5.12 s, exit 0** (session **73168**), including the
finite candidate archive -> account -> five-horizon monitoring -> reviewed scoped
cancellation -> terminal reconciliation -> daily audit path. Other cases cover
startup, receipt/account/journal races, crash replay, pending capacity, malformed
and foreign evidence, health loss, failed worker, nonblocking/symlink lock and
clock regression. Earlier development runs: 2 failed / 18 passed in 1.31 s due to
an incorrect test terminal field name, then 22 passed in 4.33 s; 1 failed / 27 passed
in 4.62 s due to string formatting in a numeric assertion, then 29 passed in 4.84 s.
Both fixture assertions corrected without changing accounting/proof requirements.
Affected and full regression on this new implementation have not yet run.

Save identity: resolve this implementation commit/tree with
`git log -1 --format='%H %T' -- polymarket_scanner/v11/paper_reconciliation.py`.
Do not treat the historical 3981-pass result below as verification of this new tree.

**82/200, approximately 41%**, formal **1/50 (2%)**, unchanged. This closes more of
existing credited reconciliation/runtime/monitoring integrations, not a new E/A
milestone. **NOT_READY_TO_FUND**. V10 unchanged, maintenance DEFERRED. The six active
work estimates below remain LOW confidence; external waiting/owner actions excluded.

Exact next action: run the affected account/runtime/feed/audit/candidate tests,
then one locked full regression on the saved unchanged tree and preserve its
manifest/output. Next implementation after verification: deliver reconciled account
changes into bounded event reevaluation, preserving current-source/census and exit
inventory gates so new fills trigger existing eligible exit/strategy decisions.
Actual-source/model/calibration, matched EV/finality/residual and operational
acceptance remain open; no owner action blocks this off-host integration.

## Historical verified checkpoint — reconciled fill quality and candidate safety

Saved/published implementation **33d927314075539de465ea90ae677d13fece0fe6**, tree
**32e337563da1b4ad9af86f338e36574ed5447a99**, passed the locked full regression:
**3981 passed, four existing FastAPI warnings, 277.46 seconds, exit 0**, session **59890**. All **840 tracked
inputs unchanged**, checked again before this report. Wrapper elapsed 278.689 s; user 197.504531 s; system 71.520638 s; peak RSS 165656 KiB.
No failed/excluded test in this run; no full regression remains active.

Manifest/output and prior run/correction/targeted evidence are durable in
**docs/V11_FILL_REGRESSION_EVIDENCE.md**. Local result:
`/workspace/scratch/38af7099c566/v11-test-evidence/fill-full-20260925-02.json` and corresponding `.log`; input digest
`c62b6e508c77d20b7c1a2b238601bd1c39dd67c3684b0027cf76de44850b0eeb`. The first 3980-pass run and subsequent fractional
cost defect/fix are preserved. Final targeted **59 / 10.17 s**, affected **338 /
57.18 s**, added single-leg **1 / 0.42 s**. This checkpoint updates docs only;
all implementation inputs remain as tested. Resolve this reporting commit/tree
with `git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md`.

Completed: additive synthetic PAPER timing/price/cost validation; reconciled
original single-leg/basket/exit fills to five causal depth horizons; unknown-
preserving window selection and partial-fill weighting; reviewed automatic
monitoring, candidate scoped cancellation and durable audits. Legacy hashes,
all-in accounting, inventory and original strategy/model/EV attribution remain.
A fractional per-share metric bug is corrected without relaxing ledger bounds.

**82/200, approximately 41%**, formal **1/50 (2%)**, unchanged. Existing integration
credits apply; actual-source/calibration, independent, host and unfunded acceptance
are still open. **NOT_READY_TO_FUND**. V10 unchanged; maintenance DEFERRED.
No owner action blocks the next off-host implementation. The six remaining
readiness milestones and active-hour ranges below remain current; all LOW
confidence, excluding external waiting and owner-dependent actions.

Exact next implementation action: connect archived, explicitly classified
PAPER_FILL/PAPER_TERMINAL receipts to bounded, resumable common-account
reconciliation inside the finite candidate. Consume known fills before new-risk
admission, guard receipt/account progress against races, preserve idempotency and
ambiguous reservations, and retain malformed/foreign evidence explicitly. Public
prints and maker quotes cannot become fills. Current metrics consume already
reconciled proofs; archive-to-ledger delivery is not yet integrated. Matched
EV/finality/residual, actual model/source and operational gates remain required.

## Historical precision correction checkpoint — superseded by verification above

The initial fill integration at **3fa1663c64755c5d793e2c7a5025ae346d99b808**, tree
**8e84b3bdda18d4f4fe40b36f6ff4a2e672b716b4**, passed **3980 tests / four existing
FastAPI warnings / 267.20 s / exit 0**, session **23310**, all **839 tracked inputs
unchanged**. The complete manifest/output is now in
**docs/V11_FILL_REGRESSION_EVIDENCE.md**. That run is completed, not still active.

A newly added fractional-fill case then exposed RISK_DECIMAL_REPRESENTATION in
metric aggregation (one failure / 0.50 s). Calculated repeating per-share Decimal
values now bypass only the external ledger input parser; no ledger bounds,
price/cost validation, cash accounting or financial authority changed. All **59
new targeted cases pass / 10.17 s / exit 0** after the correction. A single full
regression on the changed saved tree is next; the prior full result cannot certify
this correction. Preserve both full manifests and do not retry an active run.

Progress **82/200, approximately 41%**, formal **1/50 (2%)**, unchanged. No extra
credit for this correctness fix. **NOT_READY_TO_FUND**; V10 unchanged and deferred.
After full verification/evidence save, the exact next implementation remains the
bounded archived PAPER fill/terminal reconciliation path described below. No
owner-only action blocks that work. The six readiness milestones/hours still apply.

## Latest integration checkpoint — reconciled PAPER fill markouts

Recovered **6d237ef47c537f17c77db38002508fb75d989506**, tree
**ba4341d494005b5c8f22c247ec10c70661ced57f**, local and published branch equal,
clean, no unfinished operation. All 748 non-doc inputs still matched the prior
3922-pass full-regression manifest. Preserved all newer work and prior evidence.

Implemented additive synthetic PAPER execution_details validation and a read-only
PerformanceLab fill-to-depth join. Original account/fill/admission/model scope,
signal and post-validation books, explicit raw price/fees/other costs, all-in
conservation, original single-leg/basket/exit decision and receipt chronology are
verified. Bad optional metadata cannot prevent cash/unit reconciliation; old
proofs and replayed results keep their hashes. All retained fill quantities are
reconciled; uncertain timing uses the whole possible interval and never selects
away an unknown. First causal horizon depth, explicit future cost and normalized
source provenance drive separate 1/5/30/120/600-second BUY/SELL measurements.
Partial fills are quantity weighted within intents; no extra joint basket EV,
realized P&L, independent sample, empirical adverse-selection or live-fill credit.

The existing reviewed DriftWorker and typed candidate automatically consume these
cohorts, guard account/book heads, recover immutable measurements/reductions,
disable only the reviewed original scope and cancel its opening intentions while
preserving inventory/cash. Daily/weekly audits retain separate bounded fill-markout
summaries. No live route, model mutation or automatic restoration is added.

Verification: **338 affected tests passed in 57.18 s, exit 0** (wrapper 57.459 s),
including **57 new targeted cases** that also passed alone in **8.23 s**. An added
single-leg fixture then passed **1 test / 0.42 s**; there are **58 new cases** total.
The initial run had **30 passes / two test setup failures** (wrong audit helper
signature and candidate fixture constructor), corrected before the passing runs.
Affected files and logs are recorded at
`/workspace/scratch/38af7099c566/v11-test-evidence/fill-affected-20260925-01.json`
and `.log`. Full regression for this changed integration is next; the previous
3922-pass result does not certify the changed tree. Resolve this checkpoint's
commit/tree using `git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md`.

**82/200, approximately 41%**, formal **1/50 (2%)**, unchanged. R05/R35/R40/R42
already hold the applicable C/J credits. No new actual-source, independent,
deployment or unfunded acceptance milestone has closed. **NOT_READY_TO_FUND**.
V10 remains unchanged; maintenance DEFERRED. No owner action blocks off-host work.
The six remaining milestones/hours below remain current, with external waiting
and owner actions separate. The wider unresolved scope still supports LOW
confidence ranges; no percentage-to-time conversion is used.

After one integrated full regression and durable evidence save, exact next action:
connect archived, explicitly classified PAPER_FILL/PAPER_TERMINAL receipts to a
bounded, resumable common-account reconciliation path in the finite candidate.
The current fill measurement consumes already reconciled proofs; EvidenceFeed
correctly excludes account receipts from public-trade events but no candidate
worker currently reconciles them. Preserve proof identities/idempotency, retain
ambiguous risk, process known fills before admitting new exposure, keep public
prints/maker quotes distinct and never invent fills. Subsequent target-matched EV,
finality/residual, actual source/model and host/independent gates remain open.

## Latest verified checkpoint — maker-markout candidate integration

Saved/published implementation **444c71fdd4bf300b08e399de7598d38e9c3419dd**, tree
**70f716f8a02f83a2f140edef81792c17b7bc5a37**, passed the locked full regression:
**3922 passed, four existing FastAPI warnings, 264.11 seconds, exit 0**, session
**45388**. All **833 tracked inputs** remained unchanged and matched again before
this report. Wrapper elapsed 264.826 s; user 195.229953 s; system 60.022824 s; peak RSS 163804 KiB.
No failed run or exclusion occurred. No regression remains active.

The full manifest/output and 276-pass affected / 47-pass final targeted evidence
are durable in **docs/V11_MARKOUT_REGRESSION_EVIDENCE.md**. Original local records:
`/workspace/scratch/38af7099c566/v11-test-evidence/markout-full-20260925-01.json`
and `.log`; input digest
`06ca24ce74f5492616890e5867e58e7b64ef0e2033a4e38a936d2cebfd7b679a`.
The prior calibration/P&L implementation passed 3875 / four warnings / 252.03 s,
with its separate durable report preserved. This checkpoint changes docs only;
no unchanged full-suite rerun is needed. **docs/V11_MARKOUT.md** now records the
implemented integration, evidence distinctions and remaining execution join.

**82/200, approximately 41%**, formal **1/50 (2%)**, unchanged. Existing C/J credits
cover this integration; actual-source/calibration, independent, identity/guardian/
host and unfunded operational acceptance remain open. **NOT_READY_TO_FUND**.
V10 unchanged; maintenance DEFERRED. No owner action blocks the next off-host code.
The six remaining milestones/active-hour ranges below remain current and exclude
external waiting and owner-dependent actions; all retain LOW confidence.

Exact next implementation action: validate an additive synthetic PAPER fill timing/
cost evidence contract, preserving old record hashes and unknown unsupported fields;
then connect reconciled fills and original single-leg/basket/exit valuations to
horizon-specific depth marks in PerformanceLab and monitoring. Existing fill proofs
have all-in collateral, not separately identified fill price/fees/slippage; source
observed_at is not automatically attested exchange fill time. Keep partial fills,
original strategy/model scope and joint basket EV conserved. Do not infer matched
EV capture or source residuals from maker quote counterfactuals or unrelated P&L.
Resolve this reporting commit/tree with
`git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md` and preserve newer work.

## Latest implementation — horizon-specific maker quality through candidate safety/audits

Recovered verified calibration/P&L reporting checkpoint
**b85ce68115a5fffbd6befe1a256ca5eeabafb982**, tree
**66f8e7a8c5aecbf1fac4cb388c4f634c3f96bae7**, before extending this integration.
Prior implementation **95b00abc** passed 3875 / four existing warnings / 252.03 s;
all 830 inputs matched. Existing completed records, histories and policy identities
were preserved; no V10 or host operation was performed.

MarkoutDriftPolicy now declares an exact horizon (1/5/30/120/600 s), BUY/SELL direction,
evidence class, cost/tolerance assumptions, cohort minimums and mean-loss threshold.
Bounded read-only snapshots include EVERY matured retained quote for the original
MAKER_RESEARCH scope/bundle and half-open horizon-target window. The original quote,
admission, rule, model state and source class/derivations are verified. Every measured
value is reproduced from the first received horizon book, exact token/collateral,
full depth and explicit cost. Unknown fee/depth/book/unpublished members are retained;
any incomplete cohort gates reduction instead of selecting only observed winners or
losers. Means weight city-days equally, then events, then quotes. Sign/fraction counts
are descriptive counterfactuals, never fills, payout accuracy, actual adverse-selection
rates, P&L or net-EV capture. Retained-window coverage is not universe coverage.

The existing DriftWorker automatically queues new immutable markout cohorts and
shares round-robin scope scheduling with realized-paper monitoring; busy short horizons
cannot starve other eligible scopes. Pending requests, exact replay, original receipt
cutoffs, measurement/review/reduction recovery and no automatic restoration are retained.
A protected pre-window review and the original current model epoch are required before
DISABLED can be recorded. Pinned quote/measurement heads are checked and guarded atomically;
a concurrent change gates old evidence and permits a fresh snapshot retry. The typed
candidate shares the existing telemetry/research/account instances and demonstrates
measurement -> reviewed reduction -> quote retirement -> daily audit. Reports add
bounded per-scope/horizon summaries without averaging across horizons or rewriting old
reports; summary overflow makes semantic coverage incomplete. Old quality policy/scorer
identities and optional-free candidate configurations are unchanged.

Verification: **276 passed / 49.78 s / exit 0**, session 80647; final **47 new cases
passed / 7.98 s / exit 0**, session 66772 after the explicit zero-deadline check and
raw-derivation case. Earlier 117 / 23.03 s, 30 / 4.21 s and 43 / 8.49 s also passed;
no failed run occurred. Post-run source hashes/tool results:
`/workspace/scratch/38af7099c566/v11-test-evidence/markout-targeted-20260925-01.json`.
These targeted runs are not prehashed locked runs. A single locked full exact-tree
regression is next because shared worker/source-view/candidate/reporting code changed.

**82/200, approximately 41%**, formal **1/50 (2%)**, unchanged. R05/R35/R42 already
hold applicable integration credits; this adds no actual/independent/host/unfunded
acceptance. **NOT_READY_TO_FUND**. V10 unchanged and maintenance DEFERRED. No owner
step blocks this verification or the next off-host implementation.

Exact next action: complete the locked full regression on this saved tree and preserve
its manifest/output. Then connect the existing reconciled synthetic PAPER fill proofs
to horizon-aligned executable-depth markouts and original entry/exit attribution in
PerformanceLab and monitoring. Keep fill-based measurements separate from maker quote
counterfactuals, unknown venue fees and unmatched payout/EV/residual evidence. Actual
source/calibration/host acceptance remains open. The six readiness milestones and
active-hour ranges below remain current; external/owner waiting is excluded. Resolve
this checkpoint's own commit/tree with
`git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md`; preserve newer work.

## Latest verification — calibration and automatic realized-paper drift

Recovered the interrupted publication rather than repeating it: local and remote
both equal **95b00abc42fd8233f29a28d6c7d164fad1e59c38**, tree
**d6a52c9ca7f1e89f57fa98a7e638ce4d15197b2c**, with a clean workspace and no pending
operation. The locked full regression completed: **3875 passed, four existing
FastAPI warnings, 252.03 seconds, exit 0**, session **73588**. All **830 tracked
inputs** remained unchanged and matched again before this documentation update.
Wrapper elapsed 252.703 s; user 186.78011 s; system 56.612016 s; peak RSS 164412 KiB.
Manifest/output and the 117-pass calibration / 202-pass realized-paper targeted
results are durable in **docs/V11_QUALITY_REGRESSION_EVIDENCE.md**. Original evidence:
`/workspace/scratch/38af7099c566/v11-test-evidence/quality-full-20260925-01.json`
and `.log`; input digest
`3d121681c1471c14e4c6e5f5737221db721d94a3c9716ecfd98dfaa265e906eb`.
No test failed or was excluded. No full regression remains running.

**82/200, approximately 41%**, formal **1/50 (2%)**, unchanged. These are already
credited integrations; actual source, independent, host and unfunded acceptance
remain open. **NOT_READY_TO_FUND**. V10 unchanged; maintenance DEFERRED. This report
changes documentation only, so do not rerun the unchanged suite for this checkpoint.

Exact next action: connect horizon-specific maker counterfactual markout measurements
to original quote/admission/model scope, reviewed monitoring and finite candidate/
audit reporting. Preserve missing-evidence gates and each horizon; do not infer fills,
net-EV capture, calibration or residual truth. No owner action blocks this off-host
implementation. The six remaining milestones and hour ranges below remain current;
external/owner waiting is separate. Resolve this checkpoint's own commit/tree with
`git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md` and preserve newer work.

## Latest implementation — automatic scoped realized-paper quality monitoring

The calibration milestone was saved/published as
**cd2049cbf972c2a9bbca634cdd271755d6f2c55d**, tree
**a1d38d307eb6b84baff393bf84e5e0e9fecb830e**, before this implementation continued.
Existing history, policies and completed operations were retained unchanged.

PerformanceLab now measures one original station/strategy/horizon/model scope from
an immutable common-account snapshot and half-open realization window. Retained BUY
and SELL fill proofs, source receipt ordering, entry valuation/admission/bundle,
exact rule/context and strategy allocations are checked. Partial exits conserve
basis/proceeds and entry P&L; multiple fragments do not inflate event/city-day counts.
Known other scopes are excluded explicitly. Unknown lineage, chronology, faults,
missing proofs and offsetting per-event reconciliation gaps gate the cohort. The
bounded read-only view has a two-second deadline and result limits; no account/source
mutation or silent truncation is used. No mark-to-market, live capital, matched EV,
settlement or source-residual truth is inferred from realized-only P&L.

RealizedDriftPolicy requires explicit decimal collateral loss/drawdown thresholds,
cohort minimums and the fixed PAPER metric definition. A distinct protected review
must approve this exact policy and all-account-window selection before the window.
DriftWorker automatically queues one immutable snapshot when retained realized
history changes, so the typed finite candidate actually runs this monitor without
manual cohort submission. It preserves pending work, avoids repeating unchanged
history, fairly services configured scopes and permits prediction-quality plus P&L
policies for the same scope. Account movement before action gates the saved result;
its exact account version is guarded atomically at the station reduction. Account-race
recovery measures a new snapshot instead of rewriting history. A reviewed loss breach
records DISABLED; existing reducing exits and remaining inventory are preserved.
No model parameter/pointer/approval is written and no successful metric restores a
scope. Existing cancellation/reconciliation and daily/weekly drift audits are reused.
The paper learner and protected authority remain separate.

Verification: **202 passed / 35.32 s / exit 0**, session 44113, including **30 new
realized-drift cases**. The earlier unchanged-behavior checks passed 63 / 6.64 s and
initial P&L integration passed 29 / 7.73 s; no failed run occurred. Coverage includes
actual synthetic PAPER fill/exit mechanics, automatic candidate scheduling, concurrent
account CAS, exact recovery, preserved reducing exits, original-scope attribution,
partial cohorts, policy/target gates and pinned audits. Post-run hashes/tool results
(not a prehashed locked run):
`/workspace/scratch/38af7099c566/v11-test-evidence/realized-targeted-20260925-01.json`.
The prior calibration extension passed 117 / 8.85 s (14 new cases). A single full
exact-tree regression is next, justified by the shared scorer/source/account-report/
worker integration; latest completed full remains 3831 / 245.54 s before these changes.

**82/200, approximately 41%**, formal **1/50 (2%)**, unchanged. R40/R42 C/J already
credit the applicable account and lifecycle integration. Real performance/calibration,
independent review, isolated host/guardian and unfunded acceptance stay open. V10
unchanged and maintenance DEFERRED. **NOT_READY_TO_FUND**. No owner action blocks
this verification or the remaining independent off-host implementation.

Exact next action: run one locked full regression on this saved tree and durably
record results/hashes. Then close the remaining horizon-specific maker-counterfactual
markout-to-monitoring/audit join using original quote/admission/payout-model identities;
keep counterfactuals separate from fill execution and never infer net-EV capture or
source residuals from unmatched evidence. The six readiness milestones below remain
current; active hours exclude external waiting and owner actions. Resolve this
checkpoint's own commit/tree with `git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md`.

## Latest implementation — grouped calibration error and reviewed withdrawal

Recovered clean/published **3f5771b7062935c04b77cd9a43715862170e4961**, tree
**7862d3749f63f1a65ece480ad9303a008892e7a7**. All 744 non-document inputs matched
the saved 3831-pass full run; no unfinished process or locked regression was present.
The authoritative attached specification hash matched. No unchanged suite was rerun.

`score_vectors` now offers the explicit optional method
CITY_DAY_EVENT_SNAPSHOT_BUCKET_EQUAL_WIDTH_10_V1. It gives equal weight to city-days,
then events, repeated snapshots and each vector's buckets, using ten fixed bins.
The scalar is bin-weighted absolute probability/frequency discrepancy. Empty bins,
p=1, exact boundaries and differing partition sizes are explicit; no confidence
interval, sample independence or calibrated-model acceptance is claimed. Omitting
the method preserves the original serialized scores and offline learner behavior.

An additive CalibrationDriftPolicy requires the method and numerical threshold
explicitly; original DriftPolicy fields/digests, protected reviews and completed
records remain unchanged. The new policy's identity reaches the existing candidate
worker, predeclared review, original-model check, scoped safety reduction, PAPER
withdrawal, terminal reconciliation and pinned audits. An old review cannot approve
this added threshold. A measured pass never restores authority.

Verification: **117 passed / 8.85 s / exit 0**, session 36407, including **14 new
cases** and existing probability, drift, worker and offline learner checks. No failed
run occurred. Tool result/post-run hashes (not a locked prehashed full run):
`/workspace/scratch/38af7099c566/v11-test-evidence/calibration-targeted-20260925-01.json`.
Prior full verification is 3831 / 245.54 s and predates this additive extension.

**82/200, approximately 41%**, formal **1/50 (2%)**, unchanged. This extends existing
quality/lifecycle integration; actual-label calibration and independent/host/unfunded
acceptance remain open. **NOT_READY_TO_FUND**. V10 unchanged and maintenance DEFERRED.

Exact next implementation: connect PerformanceLab and original entry attribution to
an immutable account-window drift measurement for realized PAPER P&L and realized-only
drawdown, then reuse the protected review and finite candidate safety path. Preserve
account/model/scope provenance, unknown-lineage gates, partial-exit conservation and
replay; do not relabel realized-only loss as mark-to-market loss or infer net-EV capture.
A pinned account change before reduction must gate the old measurement. No owner-only
action blocks this off-host step. The six readiness milestones and active-hour ranges
below remain current; external waiting/owner actions remain separate. Resolve this
checkpoint commit/tree with `git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md`.

## Latest verification — scoped drift candidate and lifecycle integration

Published implementation **4bbb8bee10cebd1cef0ec942f90233c460e5515b**, tree
**30ca456a2eb9d4f3e74afd65c0099315d400d3da**, passed the locked full suite:
**3831 passed, four existing FastAPI deprecation warnings, 245.54 seconds, exit 0**.
All **827 tracked inputs** were unchanged and rechecked before this report. Wrapper
elapsed 246.224 s; user 180.206336 s; system 56.734231 s; peak RSS 160344 KiB.
Session **7667** has completed; no test or publication operation remains active.

The input manifest, exact full output, targeted results and earlier fixture failures
are now durably recorded in **docs/V11_DRIFT_REGRESSION_EVIDENCE.md**. Original local
records remain at `/workspace/scratch/38af7099c566/v11-test-evidence/drift-full-20260925-01.json`
and `.log`; input digest
`b51ba1288bf1c6cc63ecac3e23b48af7442c2b4176429391d242c48646c3cfdc`.
Affected verification: 121 passed / 22.01 s for measurement/capture; 156 passed /
29.77 s for worker/candidate/withdrawal/audits, then two final review/recovery checks /
0.60 s. The full run includes all 62 new cases and final bound/metric declarations.
This reporting checkpoint changes no source/test behavior; no unchanged full suite
needs rerunning just because these documents were updated.

Fixed score **82/200, approximately 41%**, formal **1/50 (2%)**, unchanged. R42 C/J
already covered lifecycle integration; the wider tested slice earns no duplicate
unit. Actual meaningful degradation/calibration, approved policies and independent
labels, isolated guardian/host, account entitlement and unfunded acceptance remain
open. **NOT_READY_TO_FUND**. V10 was unchanged and maintenance remains DEFERRED.
No owner-only action blocks the next off-host coding step.

Exact next implementation: finish the scalar calibration-error metric in the existing
grouped score_vectors output, with an explicit weighting/bin definition and reviewed
threshold policy that preserves already-saved policy identities. Then connect required
profitability drift from the existing PerformanceLab/position-attribution records using
original entry model/scope and a pinned account window. Do not treat realized-only
drawdown as executable mark-to-market loss, aggregate unmatched markout horizons, infer
net-EV capture from unrelated P&L, or invent finality/source-residual labels. Missing
metric evidence stays gated while independent eligible work continues. Existing
PerformanceLab explicitly reports these missing joins; maker research markouts are
counterfactuals with distinct horizons and cannot be silently pooled with fills.

The six remaining milestones/active-hour ranges below remain current (no percentage-
to-hours conversion). External waiting and owner actions are excluded and listed
separately. Resolve this reporting checkpoint's own commit/tree with
`git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md`; preserve any newer work.

## Latest implementation — reviewed drift through paper withdrawal and audits

Saved measurement checkpoint **5730dd604d183569c01e9886b332d20f49531096**, tree
**ab52d8ef3a1ac03c56c13d1acb2b4bdb57f3f51c**, was published/aligned cleanly before
this integration continued. Existing work and historical evidence were preserved.

The typed candidate now includes an optional DriftWorker sharing the existing
paper account, scope routes, cooperative safety ticks and audit archive. It accepts
one explicit pending cohort (maximum 64 captures, 16 configured scopes), preserving
its exact request and cutoff. Read-only measurement remains bounded to two seconds,
128 captures / 2048 buckets; worker action has the tighter 64-event label-head CAS
bound. Missing, malformed, insufficient or unsupported evidence finishes as a
visible gate so independent jobs can continue. Idle jobs do not invent cohorts.

Automatic action requires an exact root-protected read-only review at
`/etc/alpha-v11/approvals/drift-policies.json`, with numerical policy identity,
account/namespace, explicit-cohort selection, safety-reduction-only authority,
approval before the measurement window, finite freshness/expiry and the original
current protected model epoch. Changed labels, reviews or epochs gate the action.
The worker persists the original measurement and review, then atomically guards
station and label heads when recording the existing CALIBRATION_DEGRADED barrier.
Interruptions recover the exact action once; later reviewed station recovery never
causes the old action to run again. No PASS automatically restores authority. No
parameter, protected pointer, approval file, financial transport or learner is added
to the runtime. A learner still has no protected publisher interface.

The existing lifecycle path then invalidates original admissions, requests PAPER
cancellation, retains cash until terminal reconciliation, and reports both the drift
outcome and cancellation evidence in pinned daily/weekly audits. The report layout
identity changes explicitly; incompatible old jobs stay preserved and require review.
Synthetic thresholds and labels prove mechanics only, with no statistical, real-input,
independent label/calibration, host or guardian acceptance claim. Scalar calibration
error, markout/EV/PnL/drawdown/residual-bias reducers remain unsupported explicitly.

Verification: **156 passed / 29.77 s / exit 0**, session 44183, then **2 passed /
0.60 s** for reviewed restoration/review-replacement boundaries. **35 new worker
cases** join the 27 earlier measurement cases. The first worker run had 30 passes /
three fixture failures / 4.15 s: two unserialized binding calls and label timestamps
beyond intent expiry. Corrected fixtures preserve the existing expiry/API gates;
intermediate diagnostic results (2 pass/1 fail in 0.88 s; 1 fail in 0.64 s) remain
recorded. Final plan bound/unsupported-metric naming followed targeted verification;
one full exact-tree regression is next. Post-run hashes and tool results:
`/workspace/scratch/38af7099c566/v11-test-evidence/drift-runtime-targeted-20260925-01.json`.
Latest completed full run is still **3769 / 231.74 s**, before this extension.

**82/200, approximately 41%**, formal **1/50 (2%)**, unchanged; R42 C/J was already
credited. Actual quality/degradation, independently reviewed policies/labels, approved
host and unfunded acceptance remain open. **NOT_READY_TO_FUND**. V10 untouched and
DEFERRED. No owner action blocks the next off-host verification.

Exact next action: run one locked full regression of this saved integrated tree,
including existing security/replay/account/model/source tests; record unchanged
inputs and results. Then finish the remaining required quality/profitability drift
metrics from the existing prediction and attribution evidence, preserving exact
scope, horizon and target semantics. Do not manufacture actual labels or threshold
reviews. The six readiness milestones below remain current, with active-hour ranges
separate from external waiting/owner actions. Resolve this checkpoint's own saved
commit/tree with `git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md`.

## Latest implementation — original-admission scoped rolling measurement

Recovered clean/published **3c9d5cce1416f96e09cc21b49fd4537c52f450b1**, tree
**443ef03b7a9a36fc959a70765a2c143af61f306a**, with no active operation. All 740
non-document files matched the prior locked full-run inputs; the unchanged full
suite was not rerun. The authoritative master-specification hash was rechecked.

Forecast and conditioned payout captures now optionally bind the original checked
strategy admission; actual TemperatureStrategies passes that identity. Existing
unscoped records and completed replay identities remain unchanged and cannot gain a
retroactive scope. The bounded read-only rolling measurement uses explicit exact
labels, rejects superseded revisions at the cutoff, verifies original station,
strategy, model bundle, horizon, season, source class and full event vectors, then
reports event/city-day weighted Brier, log-loss, reliability and cohort sufficiency.
Explicit policies have no invented default thresholds. Measurement writes no source,
model or account state, makes no calibration/label attestation, and records missing
universe coverage and unsupported markout/EV/PnL/source-bias metrics honestly.

**121 passed / 22.01 s / exit 0**, session 29060, including 27 new drift cases.
A prior affected run had 93 passes / one replay-fixture failure / 18.71 s: its replay
omitted the new explicit admission identity. The fixture was corrected without
weakening replay conflict checks; drift/target checks then passed 47 / 5.01 s.
Tool-result and post-run hashes (not a prehashed full run):
`/workspace/scratch/38af7099c566/v11-test-evidence/drift-measurement-targeted-20260925-01.json`.
Latest full regression remains the prior **3769 / 231.74 s**, below; it predates this
measurement extension. The matrix's stale full-run wording is corrected here.

**82/200, approximately 41%**, formal **1/50 (2%)**, unchanged. R42 already has C/J;
this adds no actual-source, independent or acceptance milestone. **NOT_READY_TO_FUND**.
V10 unchanged and DEFERRED; no owner action blocks the next off-host implementation.

Exact next action: connect this measurement to a durable bounded candidate worker,
protected predeclared safety-only policy review and idempotent StationRegistry
reduction; demonstrate degraded captures -> paper withdrawal -> reconciliation/audit.
No learner may write approvals/model pointers or restore authority. A missing review,
new protected model epoch, insufficient cohort or unsupported target must remain gated.
After the integrated milestone run the affected checks, then one locked full regression.
The six remaining readiness milestones below remain current; their active hours exclude
external waiting and owner actions. Resolve this checkpoint's commit/tree with
`git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md`.

## Latest verification — protected lifecycle integration

Published implementation **7dd8a4621de5fb1637eb1442d2f04a34e195ec26**, tree
**fd35f8c0c2f6af2f9a6976ac985be0028e527548**, passed the locked full regression:
**3769 passed, four existing FastAPI deprecation warnings, 231.74 seconds, exit 0**.
All **823 tracked inputs** remained unchanged and were rechecked before this report.
Wrapper elapsed 232.391 s; user 169.365129 s; system 53.898211 s; peak RSS 161284 KiB.
Session **55992** has completed. No full regression or publication remains running.
Evidence: `/workspace/scratch/38af7099c566/v11-test-evidence/lifecycle-full-20260925-01.json`
and `.log`; input digest
`70e2cff06499ab80e8ffbfdb72bad91b229f8e66b2bd40ab8ef9a93d8f5c3ce9`.
The prior affected run was **267 passed / 35.43 s**, including 24 new cases. This
full run also covers the preceding explicit conditioned-learning extension; it
replaces no historical record and was not repeated against an unchanged tree.

Current fixed score remains **82/200, approximately 41%**, formal **1/50 (2%)**.
R42 J was credited to the implementation below; verification adds no new unit.
No actual drift/calibration, isolated guardian/host, independent or unfunded
acceptance is inferred. **NOT_READY_TO_FUND**. V10 is unchanged and maintenance
remains DEFERRED; no owner maintenance/verifier action is requested.

Exact next implementation: bind existing complete-vector forecast/conditioned
capture and explicit exact-label joins to a bounded rolling Brier/log-loss and
reliability measurement, grouped by event/city-day, under a frozen declared
station/strategy/model policy. Bind each cohort to its original scoped admission
and model bundle; connect eligible degradation to the existing StationRegistry
safety demotion and the now-tested withdrawal path. Missing labels, insufficient
coverage, unreviewed thresholds or unsupported target families must remain explicit
gates; do not invent a statistical threshold or label attestation. The already
built `score_vectors`, `labeled_examples` / `labeled_target_examples`, read-only
learning-source view, StationRegistry and finite audit/worker interfaces are the
reuse points. Preserve reviewed recovery, no model parameter mutation and no
learner access to protected publishers or real cancellation credentials.

The six remaining milestones and active-hour ranges below remain current; external
waiting and owner actions remain separate. No owner-only action blocks the next
off-host code step. Actual source/label/calibration evidence, accepted initial
champion/independent review, account entitlement, approved isolated host and unfunded
operational acceptance remain genuine later blockers. Resolve this reporting
checkpoint's commit/tree with `git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md`.

## Latest implementation — protected lifecycle withdrawal and audit integration

Recovered actual clean/published **4c3a7a1f52cc670a625612c22473f18af9148668**,
tree **c154fc8740d51caaa6f229e5f045f2d7405031e3**; no unfinished operation or newer
work was present. Prior conditioned-learning 87-test result was retained, not rerun
as a recovery step. The full master specification and all approval boundaries apply.

The finite PAPER runtime now sweeps existing managed opening/passive intents through
their original StrategyAdmission checks, including the separately protected PWS
observation/payout pair. Model overlay/epoch changes, station demotion, capability
failure, metadata drift, lost review, stale source and expired admission cause a
recorded withdrawal request. No model pointer, frozen parameter or review is written.
The same common-account cancellation bridge binds the exact account snapshot and
immutable intent signature; requests retain reservations, accept late fills and wait
for explicit terminal reconciliation. Reducing SELL intents are preserved. Healthy
checks do not renew pins or restore authority; reviewed recovery cannot resurrect
old canceled intents. Existing broader admission-source invalidation stays intact.

Dedicated rotating intake uses the existing maximum_updates and maximum_cancel_plans
bounds ahead of optional event work. One tracked plan per retained intent (account
hard bound 512), separately from the existing 32 general trigger plans, prevents
pending reconciliation/busy trigger streams from consuming its intake slots. Plan
registration is saved before dispatch. Maker admission now revalidates without a new
book or telemetry job. Daily/weekly pinned audits record failure reasons, cancellation
audit counts and maker retirements; these are not unique fills or exchange cancels.
Runtime safety policy and report-layout identities change explicitly: an existing
incompatible runtime/report job requires review, with old state retained unchanged.

Verification: **267 passed / 35.43 s / exit 0**, session 37406, including **24 new
lifecycle integration cases**. Actual source/model/certification and basket/common-
account code is exercised with explicit synthetic reviews/prices. Coverage includes
both PWS model slots, late fills, terminal inventory preservation, recovery without
resurrection, interrupted registration, bounded rotation, expiry/authority checks,
clock regression without capture-clock repair, immutable intent mismatch and audits.
The first focused attempt exposed a missing admission-check telemetry allowlist:
13 failed / 4 passed; the scoped nonauthorizing telemetry guard was added without
relaxing ordinary evidence or account mutation checks. Next 17 passed / 5.54 s;
final affected run includes the additional clock/audit/identity checks. Earlier
unchanged cancellation/runtime checks: 50 passed / 2.79 s. Final tool transcript
and post-run hashes are saved in
`/workspace/scratch/38af7099c566/v11-test-evidence/lifecycle-targeted-20260925-01.json`.
This is a recorded tool result, not a prehashed locked run. A full regression is
next because shared evidence, runtime, maker and reporting behavior changed.

**R42 earns J** for the demonstrated protected model/station/strategy failure →
existing PAPER cancellation/reconciliation → monitoring/audit integration. Total
**82/200, approximately 41%**, formal **1/50 (2%)**. Actual statistically meaningful
rolling drift/lead/calibration evidence and independent guardian/host acceptance
remain unearned. R37 receives no credit for cooperative cancellation. Six remaining
readiness milestones below retain their active-hour ranges and exclude external
waiting; the completed lifecycle withdrawal join is removed from remaining work.

Next: save this exact implementation and run one locked full regression; then
continue explicit evidence-bound rolling degradation integration under predeclared
scope/policy and reviewed recovery. Genuine blockers remain actual source/label and
calibration evidence, independent initial champion/semantic/security review, owner
account entitlement and approved isolated host/unfunded acceptance. No owner action
is needed for the next off-host work. V10 is unchanged; maintenance DEFERRED. No
service, permission, deployment, funding, transfer or real-order action occurred.
**NOT_READY_TO_FUND**. Resolve saved commit/tree using
`git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md`.

## Latest implementation — explicit same-day conditioned research

Recovered/published reporting checkpoint **bb2be4133470e14bcc28e52a9476106ad092c76b**,
tree **a7759fc20b3cc35c2892ddc28a0473442f307450**, clean worktree, no remaining
operation. This implementation extends the existing offline fit/job/finite worker;
it does not install a trainer on the candidate path or create an active model.

`ConditionedLearningEnvelope` has a distinct, frozen policy digest for exact accepted
revision plus complete remaining-day coverage. Original LearningEnvelope fields,
serialization, hashes and unconditioned meaning are unchanged. The new path checks
the rule, feature/member/unit/family contract, exact observation/coverage/model
references, original prediction cutoff and complete local-day interval partition.
Only the existing single Gaussian-member family is supported. All parameter trials
and parent comparison apply the same high/low condition as numerical inference;
TRAIN alone selects bias/dispersion. The existing read-only job and separate worker
accept the conditioned capture only under that policy. Mixed/incorrect target
modes remain gated; physical coefficients and observation-distribution fitting
remain unsupported rather than silently using the unconditioned learner.

Exact completed jobs replay saved results, parents/source archives remain unchanged,
and new city-day, backoff, daily-budget, journal, holdout-reuse and no-promotion
boundaries are retained. Capture-to-example derivations now include the original
conditioning rule; prior source captures/decisions are never rewritten. New paired
observation summaries explicitly leave statistical independent-sample count unknown
and retain a paired-target count of one; dataset event/city-day grouping is unchanged.

Verification: **87 passed / 15.31 s / exit 0**, session 96386, including **18 new
conditioned-learning cases**. Tests demonstrate C/F and high/low numerical parity,
train-only selection, exact source/coverage faults, target-mode separation, immutable
parent/source preservation, completed replay and finite-worker evidence backoff.
Result transcript metadata and post-run hashes:
`/workspace/scratch/38af7099c566/v11-test-evidence/conditioned-learning-targeted-20260925-01.json`.
This is transparently a saved tool result with post-run hashes, not a prehashed
locked run. No test was repeated merely to create that record. The latest full
regression remains **3727 passed / 260.57 s** at `61a5cc84`, covering the preceding
shared archive/candidate/capture integration. It predates this bounded offline
extension; no full 3745-test result is claimed. Future broad release acceptance
remains required; the affected tests are sufficient for this off-host work unit.

Fixed estimate **81/200, approximately 41%**, formal **1/50 (2%)**, unchanged.
R14/R15 already hold the relevant engineering integrations. Actual exact-label and
calibration evidence, accepted initial champion, independent safety/auth, host
isolation and operational/unfunded acceptance remain open. The six remaining
milestones/ranges below retain their estimates; they now exclude these completed
capture and same-day Gaussian fit joins, not the larger missing evidence/learning
scope. No estimate is derived from elapsed effort or percentage.

Next concrete implementation: connect existing drift/degradation evidence to the
protected model/station/strategy demotion and paper cancellation paths (R42),
using explicit bounded policy inputs and retained reviewed recovery. Inspect
`v11/model_registry.py`, `host_trust/v11-model-authority/authority.py`,
`v11/certification.py` and `v11/paper_cancellation.py` before adding any join.
Unsupported optional physical/observation learners stay gated while required safety
integration proceeds. No owner action is needed for that off-host preparation.
Actual source/calibration and isolated host/identity acceptance remain external
dependencies; do not reopen deferred V10 inventory/maintenance without a concrete
required dependency. V10 and executor remain unchanged. No deployment, service,
permission, funding, transfer or real-order action occurred. **NOT_READY_TO_FUND**.
Resolve this milestone's commit/tree with
`git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md`.

## Full verification — conditioned and observation capture

Implementation **61a5cc84b09d17a4918cf69aa05a30f08aaabaeb**, tree
**1eafb00f217e6016c4ecde45477febd70d136245**, passed the locked full regression:
**3727 passed, four existing FastAPI deprecation warnings, 260.57 seconds, exit 0**.
All 821 tracked inputs remained unchanged. Wrapper 261.171 s; user 167.172302 s,
system 85.439772 s; peak RSS 164544 KiB. Evidence:
`/workspace/scratch/38af7099c566/v11-test-evidence/target-learning-full-20260925-01.json`
and `.log`. Input digest
`a6a60b90193c62d0f1e77ed6d216b9dfca7841009df3523fac30baea675c9d5c`.
No full regression remains running. No source collection or host action occurred.

Fixed estimate **81/200, approximately 41%**, formal **1/50 (2%)**, unchanged.
The six remaining readiness milestones/ranges below still apply. Next implementation
is explicit same-day-conditioned Gaussian research fitting through the existing
read-only job and finite learner worker, with a new frozen policy identity and
numerical inference parity. Existing unconditioned histories and policies stay
unchanged. Physical/observation fitting and all real-label/calibration, independent,
host and operational gates remain open. V10 is unchanged; maintenance DEFERRED.
This reporting entry changes no tested code. Resolve its eventual commit/tree
with `git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md`.

## Latest implementation — conditioned and paired observation learning capture

Recovered reporting HEAD **13070495008a312200d57ac00e032f04752b7e8f**, tree
**d7315789a3b9eab3b182168e40d9822e0d58d872**, with publication/fetch/alignment
complete and no unfinished work. The master hash remains unchanged. This work
adds `v11/target_learning.py` and joins it to the existing temperature and PWS
candidate paths before economic filtering. All exact payout buckets retain the
accepted observation revision, remaining-day intervals, original inference cutoff,
immutable parent contract and reproduced prediction. Paired observation captures
retain one first-Alpha-receipt target/window/anchor and separately archived ablation
inputs. A received-report target is not a certified next-publication label.

An integration failure exposed learning records advancing the live FEATURES head.
The additive LEARNING_FEATURES record kind now keeps research snapshots out of
operational feature leases and preserves existing head/CAS/admission safeguards;
no existing records or database schema were rewritten. Both scheduled observation
and separately protected payout captures reach the candidate while entry economics,
event suppression, cancellation and common-account gates remain intact.

The read-only dataset path expands physical/remaining models, PWS QC and normalized
AWC/MADIS/GEFS to original receipt hashes and times. Ambiguous graphs, missing or
changed references, future/cross-event/label inputs and synthetic-to-public evidence
substitution gate. Observation labels require an exact retained first-received-report
score and source binding; paired examples count as one event/city-day. Known labels
cannot become new by backdated knowability plus later archival. Conditioning and
prediction time survive dataset assembly. The unconditioned grid learner explicitly
rejects these new conditioned examples; physical/observation fitting remains open.
No code creates labels, source truth, calibrated confidence, approvals or orders.

Verification: initial changed integration **63 passed / two failed / 6.99 s**
exposed the live feature-head issue plus an obsolete not-implemented assertion;
both were corrected. Expanded run **98 passed / two fixture-call failures / 13.18 s**
then **154 passed / 26.41 s / exit 0** after correcting test arguments/provenance.
Final source/label-provenance verification **41 passed / 4.82 s / exit 0**.
There are **26 new cases** (20 target capture, six derivation checks). A single
locked file-backed full regression is next because archive/dataset/runtime joins
changed; the earlier 3701-pass run is not attributed to this implementation.

Fixed estimate **81/200, approximately 41%**, formal **1/50 (2%)**, unchanged:
R14/R15/R27 already hold their integration credits. Actual source labels, calibration,
independent acceptance and isolated deployment remain unearned. The six remaining
milestones/ranges below retain their scope and estimates; target-specific capture
is now implemented for these supported paths, with broader targets and fitting
still unfinished. Off-host preflight: 4813762560 / 8589934592 memory bytes,
eight CPU quota equivalents, 26937540 KiB disk available, zero high/max/OOM events.
This is not alpha-dev headroom or deployment evidence.

Next: publish this exact implementation and run one recorded full regression,
then extend the existing bounded offline fit to reproduce same-day conditioning
for the already supported Gaussian member family, with explicit target-mode
separation. Keep physical coefficient/observation learners gated until their own
contracts and checks exist. No owner action is needed. V10 remains unchanged,
maintenance DEFERRED, all host/resource/control findings preserved. No deployment,
service action, funding, transfer or real order occurred. **NOT_READY_TO_FUND**.
Resolve this checkpoint's commit/tree with
`git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md`.

## Recovered verification — bounded preparation

Published/local HEAD **2073e53383be0eae85571be8195b81d8f45c4448**, tree
**6cd23e57f8ef1765fc8f3767549438f87a330c52**. One clean worktree, no remaining
test/publication operation, and the connected GitHub branch agrees. The full
regression completed after the prior chat update: **3701 passed, four existing
FastAPI deprecation warnings, 221.56 seconds, exit 0**. All 819 recorded inputs
independently rehash unchanged. The runner recorded the pre-publication local
commit e396c069095e4a2f17bf1ad6133e1d1cdd6dcf7a; its tested tree is exactly the
published tree above. This evidence was recovered, not rerun.

Evidence: `/workspace/scratch/38af7099c566/v11-test-evidence/preparation-full-20260925-01.json`
and `.log`; wrapper 222.214 s, user 161.623836 s, system 51.757070 s,
peak RSS 161920 KiB. Input digest
`9168f6738cb1f951923f510f54e5cbaac5e5aa69e3510dc81519ce46b886ba9f`.

Fixed estimate **81/200, approximately 41%**, formal **1/50 (2%)**, unchanged.
The six remaining milestones/ranges below remain applicable. Next implementation:
target-specific conditioned payout and paired next-observation capture, preserving
exact conditioning, observation horizons and receipt-level provenance before
economic filtering. Actual labels/calibration, independent safety and host/release
acceptance stay open. No owner action is needed for this off-host work. V10 stays
unchanged and its maintenance DEFERRED. Resolve this reporting checkpoint's own
commit/tree with `git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md`.

## Latest implementation — bounded current-input preparation

Recovered reporting checkpoint **a84c3049e2eda4d629b7ed168d433ef600081dda**, tree
**f48d6766dda393e4b51f76686476da51d4da3a02**. New code adds typed remaining-day and
physical/paired-ablation preparation plans to the finite candidate. Each turn
selects bounded current archived sources or performs one durable stage; clock
health, source revisions, optional-source absence, expiry, input identity and
attempt budgets remain enforced. No HTTP, fitting, approvals or financial
transport are added. Existing archived newer GEFS runs can be adopted with the
original plan policy and complete field binding. Partial outputs survive restart
without timestamp renewal; an invalid plan rotates to other independent work.

The integration corrected two concrete joins: normalized AWC physical features
now verify their original raw response/receipt, and observation/payout plans share
the same immutable physical feature record so one target cannot supersede the
other. PWS ablation retains the identical non-PWS evidence and inference cutoff.
The demonstrated synthetic scheduled path reaches protected observation and
payout scopes, derived risk, conservative economics and the common candidate.
It retains the event entry-suppression gate and rejects nonpositive conservative
EV; no executable exit, trade, filled inventory or financial authority is inferred.

Verification: final affected suite **245 passed / 56.98 s / exit 0**, including
**24 new preparation cases**. File-backed result/log:
`/workspace/scratch/38af7099c566/v11-test-evidence/preparation-targeted-20260925-01.json`
and `.log`; wrapper 57.246 s, peak RSS 76068 KiB, all 819 tracked/untracked inputs
unchanged. Input digest `8b4ad4d0e294f38981595c8d144d2ff9eed0c826d1aabcd81e86a15e84520783`.
An earlier interrupted affected run lost its terminal handle and had no retained
final result; it is not counted. Initial targeted failures exposed the telemetry
allowlist and shared-feature join defects, both corrected before this final run.
The latest completed full regression is still the recovered **3677-pass** run at
`47c3b999`; do not attribute it to these new changes. Run one locked file-backed
full regression after saving this implementation, without editing its inputs.

Fixed estimate **81/200, approximately 41%**, formal **1/50 (2%)**, unchanged:
R09/R11/R13/R26/R33 already hold the applicable integration credits. No actual
provider, model calibration, protected review, isolation or acceptance is earned.
The six remaining milestones below retain their active-hour ranges; the source
milestone now excludes the completed bounded preparation join. Next independent
implementation: target-specific learning capture with exact conditioning and
receipt-level provenance, before economic filtering. Unsupported targets must
remain explicitly gated; do not invent labels or widen learner/model authority.
No owner action is needed for that off-host implementation. Resolve this commit
and tree with `git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md`.
V10 remains unchanged and maintenance DEFERRED. **NOT_READY_TO_FUND**.

## Recovered full verification — physical/PWS and remaining paths

Recovered implementation **47c3b999d0e08468da0941075d0bf8c1ce32b6f4**, tree
**6db94c2af4a746381d1c2adf34690ac1f0f914b2**. Local and GitHub branch refs agree;
one clean worktree, no running test/publication process. The master specification
bytes again match the recorded SHA-256. Newer completed evidence was found before
retrying the next action in the older checkpoint: **3677 passed, four existing
FastAPI deprecation warnings, 201.69 seconds, exit 0**. No duplicate run was made.

Evidence: `/workspace/scratch/38af7099c566/v11-test-evidence/physical-remaining-full-20260925-01.json`
and `.log`; wrapper 202.353 s, user 149.776305 s, system 44.445409 s, peak RSS
160776 KiB. All 817 tracked inputs were unchanged during the run and independently
rehash identically on recovery. Input digest
`3db83f973ab654f5cef4f0b07217c4442aae9e3cbd29c97b90777be80fcbdd87`.
This reporting checkpoint changes documentation only; resolve its own identity
with `git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md`.

Estimate **81/200, approximately 41%**, formal **1/50 (2%)**, unchanged. The six
remaining active-work ranges below are retained; no operational or independent
acceptance is earned by the recovered regression. Next implementation: bounded
current-input preparation scheduling in the finite candidate, including durable
recovery and unchanged source/event/cancellation gates. Actual source/label and
calibration evidence, independent guardian/auth/host and readiness acceptance
remain open. No owner-only action is required for this next off-host task.
V10 stays unchanged, maintenance DEFERRED, with all recorded control-health,
inventory verification, backup and host-isolation findings preserved.

## Latest saved implementation — physical/PWS inference

Previous milestone published and aligned as **1bdd091e6679e122174cd21a0f7202f31377e340**,
tree **8a44c1398ffcbd153a4bb2f64c972b98b1e7c1fe**, session 92040 exit 0, clean
workspace. Resolve this checkpoint's exact HEAD/tree with the existing
`git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md` command.

`v11/physical_inference.py` connects the existing AWC physical decoder and MADIS
QC features to a declared member/feature/unit/family/target contract and frozen
numeric inference parameters. Only immutable PROBABILITY artifact coefficients
apply standardized feature adjustments; the archive retains unchanged base
members and original run/receipt identity. Coefficient/scale/term/effective-bias
bounds fail closed. Material missing features require a strictly wider kernel;
missing PWS retains healthy official features, while PWS-lead eligibility still
requires healthy QC. Bounds remain vacuous and probabilities UNCALIBRATED.
No meteorological coefficient or calibrated status is hard-coded into production.
The existing grid learner rejects the new family; fitting and OOS acceptance of
physical coefficients remain required work.

Features now retain metadata, causal expiry and distinct ablation stream identity.
A PWS ablation removes its entire dependency and values while keeping the same
non-PWS evidence and observation bundle. Current raw/feature/model sources and
PWS metadata revisions participate in atomic guards. New context-less/expired or
mismatched feature records cannot enter the declared family. Unchanged legacy
predictions retain their prior serialized field set (no new null field).

The demonstrated synthetic path is raw MADIS + AWC -> QC/physical features ->
paired next-official observation prediction -> separately protected observation
and payout pins -> same-day revision/remaining-path inference -> conservative
settlement economics. Economics rejects entry; there is no payout inferred from
crossing and no assumed executable exit. The same-day derived risk/maker paths
retain their source and condition guards. Automated scheduling of these new
preparations and fitting/datasets for the additional targets remain unfinished.

Tests: unchanged-family regression **113 passed / 2.75 s**; initial new physical
suite **18 passed / 1.75 s** after correcting a reused synthetic review ID. The
expanded affected suite returned **307 passed, one failed / 55.10 s**. Its outage
case exposed an unnecessary healthy-QC lineage requirement when every PWS value
was missing. Corrected that fallback: source-arrival guards remain, and material
PWS values still require healthy QC/metadata. Final new suite **26 passed / 2.06 s,
exit 0**, session 27509. No actual source, label, independent review or strategy
eligibility is claimed. A single combined full regression is the next gate;
latest completed full remains 3610 passes at `6347e704` and is not attributed to
this changed tree.

R13 earns its named J milestone for the demonstrated physical-source -> protected
inference integration. Fixed estimate **81/200, approximately 41%**; formal
**1/50 (2%)**. C/J are limited engineering substeps; no E/A is earned. The six
remaining active-work ranges below are retained, with the completed bounded
source/feature joins removed from the listed implementation tasks. Actual-source,
calibration, wider runtime scheduling, independent safety/auth/isolation and
unfunded acceptance remain open. R01 remains the sole formally complete package.

Next concrete action: publish the exact tree and run one locked full regression
because shared inference, source guards and dataset feature archival changed.
After that, connect bounded current-input scheduling for these preparations to
the finite candidate, with durable replay and unchanged event/cancellation gates;
then complete target-specific learning/capture and empirical acceptance where
inputs are available. Do not duplicate a running regression or edit its inputs.
V10 maintenance stays DEFERRED; no V10 service/executor change, host workload,
deployment or financial action occurred. Its stale-cycle/memory-pressure finding,
alpha-dev resource/isolation and backup/recovery gates remain open. Inventory
SHA-256 remains OWNER-REPORTED / INDEPENDENT VERIFICATION PENDING.

## Latest saved implementation — archived remaining-day paths

Recovered published/local **86fe65dd6cb5c59843ed7ef91342f6207d7063fd**, tree
**4ca9b0374d581945a6a563bb9f23e7d176db3b24**, clean workspace and no outstanding
operation. GitHub read-only ref agreed. Master specification SHA-256 matched
`a0e16d9bd7344c943a54a16a53c6757662363d93642f6e5cb7953cd047659b4a`.
This checkpoint's exact published HEAD/tree resolves with
`git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md` on the existing branch.

Implemented `v11/remaining_forecast.py`: explicit exact-population interval
coverage and an immutable full GEFS path produce unresolved high/low members and
the FEATURES coverage record consumed by the existing protected same-day engine.
It retains every elapsed gap, 23/25-hour local days, all 31 members, original run
and oldest constituent receipt. Reprocessing does not renew source time. A proxy
or collection of point reports cannot establish accepted interval coverage. The
actual exact-source adapter/coverage certification remains unavailable; the new
contract is not independent attestation. Interpolation, revision and calibration
uncertainty remain explicit. No next-observation probability becomes payout or
executable sale proceeds.

Current parent/field and official revisions are rechecked in health/admission.
The same-day pipeline, derived risk and maker paths verify that the coverage
record describes the same observation and interval partition as the derived
members. Appends use atomic source guards; completed replay is historical and an
interrupted MODEL/FEATURES pair can resume without replacing or refreshing the
saved model. Shared linear-path extrema logic is reused by whole-day forecasts.

The end-to-end test exposed an existing duplicate-guard defect in strategy
admission for derived source channels. Identical guards now merge; differing
versions fail closed. No permission, freshness threshold or production risk
policy was relaxed. Synthetic metadata/GRIB and risk-age fixture defects were
also corrected. The final related run was **211 passed, one failed / 56.73 s**;
the failure was the integration fixture's 120-second source policy rejecting a
16-hour model run. The targeted correction and shared-guard checks then passed
**3 passed, 21 deselected / 1.95 s, exit 0**. All **24 new cases** have passed
across these runs. A combined full regression is required after the next shared
inference milestone; no new full-suite pass is claimed yet. Latest full remains
3610 passes at `6347e704`, predating this change and the learner worker.

The preserved venv had a missing `bin/python3` launcher after environment
recovery; initial test invocation exited 127 without running tests. Restored only
that missing symlink to the recorded Python 3.12.14 runtime; existing packages
were retained (pytest 8.3.3, httpx 0.27.2, pydantic 2.9.2). Off-host preflight:
4655427584 bytes memory used / 8589934592 limit, eight CPU quota equivalents,
27020976 KiB disk free, memory high/max/OOM counters zero. This is not alpha-dev
resource or deployment evidence.

Estimate **80/200, approximately 40% (unchanged)**; formal **1/50 (2%)**. R11/R26
already hold their named integration credits; no E/A is earned. The six remaining
active-work ranges below remain unchanged; the bounded observed-interval/GEFS
join is implemented, while source acceptance, broader paths and physical/PWS
inference are still part of the first milestone. No external waiting is counted.

Next concrete action: connect causal physical/PWS feature values to a declared
immutable inference contract and paired next-observation model, including missing
source fallback and exact-family ablation. Reuse protected observation/payout
scopes; do not hard-code meteorology as calibrated probability. Actual labels,
calibration, independent guardian/auth/host and acceptance gates stay open.
V10 and executor remain unchanged; maintenance DEFERRED, no deployment or funding.
Inventory hash remains OWNER-REPORTED / INDEPENDENT VERIFICATION PENDING, and all
recorded stale-control/memory-pressure/resource/backup findings remain open.

## Latest saved implementation — finite learner worker

Recovered the completed full-regression reporting checkpoint
**43f020b03c3fff3514a57a2737f51045d6ab77f9**, tree
**043860ffd3c72514191e59300df27b92fdd35cce**. Publication/fetch/alignment passed,
session 87862 exit 0, clean worktree. This milestone adds the separate
`v11/learning_worker.py`, its tests and an optional eight-event research fixture.
Resolve this checkpoint's exact published HEAD/tree with
`git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md` on branch
`weather-v11-profitability-upgrade-2026-09-23`.

`ForecastLearningWorker.step` joins exact complete-label cohorts to the existing
read-only capture/dataset/fit path. Its fixed policy requires new resolved city-day
groups, an attempt interval and a daily attempt budget; old groups, more bucket
rows, elapsed time and a new command ID cannot invent new evidence. Trigger basis
is NEW_TO_THIS_PROGRAM_COHORT, not a claim that every label arrived since the last
fit or that global universe coverage is complete. Counts are preliminary identity/
availability checks; the full causal dataset checks still run before training.

A nonblocking private lock allows one worker per research database. A durable
reservation precedes fitting. Complete results recover by exact request/dataset/
parent/result/artifact identity without a second fit. Known failures retain
backoff and the parent. Uncertain interruptions remain explicitly gated for
review; a changed policy or reused run ID cannot silently adopt another result.
Attempt history is bounded, source archives are read-only, and confirmation reuse
still becomes DEVELOPMENT evidence. The worker is not connected to the candidate
decision/safety loop and exposes no model-pointer, order, service or credential
interface. Actual OS isolation remains unverified. Worker scheduling limits are
not strategy-eligibility thresholds or a post-completion funding waiting period.

Tests: initial worker/fit **37 passed / 9.71 s**; final worker/source/dataset/fit/
capture/artifact integration **125 passed / 18.51 s, exit 0**, session 32777,
including **17 new worker cases**. No assertion failure occurred. The latest full
regression remains **3610 passed / 288.26 s** at the exact implementation below.
It predates this separate worker; it is not reported as a full 3627-test run.
Existing production/candidate source was unchanged by the worker addition, so
the targeted integration checks address the new joins without repeating the
unchanged full suite. Full original release/security/independent acceptance is
still outstanding. No test, fit, collection or publication operation should be
duplicated on resume; inspect actual state first.

Estimate **80/200, approximately 40% (unchanged)**; formal **1/50 (2%)**. No new
formal package or E/A credit is claimed. The six readiness milestones/ranges below
remain applicable; the learning scheduling substep is now implemented, while
actual labels, accepted initial artifacts, isolated process/host custody and
learning acceptance keep that larger milestone open. No calendar delay is implied.

Next concrete implementation: connect accepted observation-prefix/remaining-path
inputs and PWS/physical feature contracts to the existing protected same-day and
observation-lead factories, preserving the separate next-observation, final-payout
and executable-exit targets. Inspect the current factories/conditioning artifacts
before adding adapters; reuse the established capture/lineage and scoped bundle
mechanisms. Actual exact-source labels/finality, calibrated artifacts, source
access, independent guardian/auth/host and acceptance remain genuine dependencies.
Continue off-host; do not reopen optional V10 maintenance or invent approval.

V10 remains unchanged. Its recorded stale successful cycles and severe memory
pressure are unresolved, as are alpha-dev resources/isolation and backup/recovery.
Maintenance stays DEFERRED; inventory SHA-256
`b161426cff5b39b262e72a6e8142982dd29fa8a0bf29c9965232edf1ff364bd3` is
OWNER-REPORTED / INDEPENDENT VERIFICATION PENDING. No V10 operation, guard,
systemd/executor change, deployment, funding, transfer or real order occurred.
No owner maintenance command is requested. **NOT_READY_TO_FUND**.

## Latest full verification — forecast learning integration

Fully verified implementation **6347e704876137f2c3e8d3b7a3365a05efd3bc32**,
tree **268bd4b4aa870ddb62be199795a351bae702f263**, existing branch unchanged.
Publication/fetch/tree alignment passed, session 83133 exit 0, clean workspace.
Full regression **PASS: 3610 passed, four existing FastAPI deprecation warnings,
288.26 s, exit 0**. Wrapper 289.076 s; user 209.019457 s; system 71.921874 s;
peak RSS **162508 KiB**. All **811 tracked files remained unchanged**, input digest
`96f665759c393c7881bc9cbd204c691a970a06ad2a2a3da63e0a117e90954ed2`.
Exclusive session 64934 finished; no full regression remains running. Evidence:
`/workspace/scratch/38af7099c566/v11-test-evidence/forecast-contract-full-20260924-01.json`
and `.log`; runner `run_forecast_contract_full.py`. Do not duplicate this passed
run without a relevant change or overwrite its evidence.

Off-host preflight: memory current 6873120768 bytes / 21474836480-byte limit,
eight CPU quota equivalents, 25101852 KiB disk free; high/max/OOM counters zero.
No V10 host check/workload, source probe, service or financial action occurred.
Estimate stays **80/200, approximately 40%**; formal **1/50 (2%)**. The six remaining
readiness milestones/ranges below remain applicable and unchanged. Full testing
does not earn operational or independent acceptance.

Next code action: integrate the prepared bounded learner worker with the verified
source-to-fit function, new-resolved-cohort thresholds, backoff/daily budgets,
single-worker locking and durable interrupted-attempt handling. Its work stays
outside the candidate decision process; actual OS isolation and source/label/
champion acceptance remain open.

## Latest implementation — read-only learning source closure

Previous milestone published as **49cd76f74bc058e96130e1a79f0b147f49e0b33a**,
tree **b9d4221db850b73c9e900248f1079d5935fc36a1**. GitHub/local equality and
fetch/alignment passed, session 81634 exit 0; clean worktree at that milestone.

Implemented `v11/learning_sources.py` and joined dataset construction and the
forecast research job. One SQLite read transaction opens with `mode=ro` and
`query_only=ON`, includes committed WAL, and pins the source sequence. It performs
no journal-mode/checkpoint/schema/source mutation. Reads are bounded to 8192
unique records, 8 MiB and ten seconds. Caller changes to returned dictionaries
cannot change the view; later source appends are not substituted into it.

Examples now expand normalized forecast/observation raw references and complete
GEFS field graphs with exact hashes, original issue/observation/receipt/availability
times, strict event/provider/kind/sequence ordering, evidence class and dependencies.
Missing, malformed, historical-unknown, future or cross-event children gate.
An additional offline derivation budget is 1024 records/2048 edges/512 KiB metadata/
two seconds; the existing 256-node feature DAG and 64-input runtime decision limits
are unchanged. The job also caps the serialized dataset at 16 MiB. Larger jobs
remain gated, not evidence of deployment capacity.

Verification: earlier affected dataset/fit **42 passed / 5.05 s**; **15 new cases
passed / 4.37 s**; related source/dataset/fit/protected-inference suite **168 passed
/ 31.28 s, exit 0**, session 60960. A complete synthetic 310-field GEFS path retains
**621 original derivation records** in every event-bucket example and builds a
causal dataset without truncation. Read-only source connection and committed-WAL
snapshot behavior, query rejection, actual byte/deadline limits and immutable
revisions are tested. No actual source data, labels, fills or independent review
is claimed. Two unused test imports were removed afterward; full verification
below is the next gate.

Estimate **80/200, approximately 40% (unchanged)**; formal **1/50 (2%)**. R14/R15
already hold their named integration credits. The remaining six milestones and
active-work ranges below are retained; this closes a bounded provenance substep,
not the larger real-evidence/acceptance milestone.

Next: publish this exact implementation, then one locked full regression because
shared dataset/probability/pipeline paths changed. After that, continue bounded
learner triggering/backoff and durable job recovery using this source-to-fit path;
do not introduce training into the candidate decision process. OS isolation and
actual initial champion/calibration/label acceptance remain separate open gates.
V10 maintenance stays DEFERRED, V10 unchanged, and no deployment or financial
authority is authorized. All recorded host/resource and owner-inventory findings
remain open. No owner maintenance action is needed for this off-host work.

## Latest continuation — forecast capture to immutable challenger

Recovered reporting HEAD **e91479b4ba187358fcb66800f01a21318a72d426**, tree
**c2754be193d2548631374297528233e8ba9ce67b**, on the existing branch
`weather-v11-profitability-upgrade-2026-09-23`. Clean workspace, one worktree,
no unfinished test/publication operation; no reset or duplicate work. The private
master bytes again match the authoritative SHA-256. No V10 operation occurred.
Resolve this checkpoint's eventual exact commit/tree with
`git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md`.

Implemented `v11/forecast_features.py` and `v11/forecast_learning.py`; integrated
the existing capture, immutable bundle, protected forecast pipeline and bounded
learner. A declared contract fixes model IDs/member counts, units, daily family
and model quantization before constructing an INITIAL_NO_FIT research bundle.
No existing parent is adapted or activated. Capture v2 requires the exact parent
FEATURES contract/parameters; completed v1 records retain original identities.
Declared-contract inference refuses changed widths/units/families. The learner
requires all declared member/cut columns, without nullable-member substitution.

The explicit offline job reads complete event vectors and exact supplied labels,
builds the registered temporal dataset, retains capture/example hashes and the
dataset recipe in a separate CHALLENGER journal, and invokes the existing bounded
fit. The immutable challenger reproduces its reported probabilities through the
same inference function. Completed requests replay; interrupted fits require
review rather than implicit duplicate trials. No labels are fabricated by code,
no training runs on the decision path, and no model pointer or authority changes.

Verification: initial related **44 passed / 4.11 s**; new integration cases
**22 passed / 5.62 s**; final related **145 passed / 29.35 s, exit 0**, session
85456, including **29 newly added cases**. All examples and labels in these tests
are SYNTHETIC. The last full regression remains **3566 passed / 283.92 s** on
the older implementation below; it has not been rerun for these changes yet.
Off-host preflight: 20 GiB memory limit, 9652477952 bytes current use, eight CPU
quota equivalents, 22482692 KiB disk free, no high/max/OOM events. This does not
prove alpha-dev headroom or production isolation.

R15 earns its named integration J: **80/200, approximately 40% (unchanged after
rounding)**. Formal full completion remains **1/50 (2%)**. Current source/label
truth, calibration, other learning targets, OS separation, independently accepted
champion and operational acceptance remain open. The initial champion does not
need a newly winning challenger once all original readiness gates actually pass.

Next concrete implementation: read-only bounded dataset assembly and full forecast
derivation provenance, including original GEFS constituent receipts. Existing
`build_example` follows nested FEATURES but stops at MODEL records; a normalized
forecast currently omits its constituent raw derivation from the example manifest.
Resolve this causal evidence gap without treating reconstructed publication time
as actual receipt evidence or raising runtime decision limits.

V10 remains unchanged; maintenance is DEFERRED. Stale successful cycles and severe
memory pressure remain recorded findings. Host resources/isolation, backup/recovery
and inventory verification remain open. Inventory hash remains OWNER-REPORTED /
INDEPENDENT VERIFICATION PENDING. Actual NOAA access remains unverified after the
recorded proxy/client failure; no unchanged probe was repeated or bypass used.
No deployment, executor, funding, money movement or real-order action occurred.

## Remaining path to READY_TO_FUND — active work estimate

These six milestones cover the remaining original scope, not a replacement plan.
Ranges estimate hands-on implementation, integration, analysis and verification
from the current code/matrix; they exclude external waiting and owner actions.
They are not computed from the completion percentage. Confidence is LOW because
actual source semantics, independent review and operational access remain unknown.
The ranges must be revised for a concrete discovered change, not elapsed time.
Funded canary measurements/activation are outside this unfunded finish line and
still require separate budget and live approval. No arbitrary paper wait applies.

| Remaining milestone | Work remaining and proof of completion | Active hours | Confidence | External/owner dependency |
|---|---|---:|---|---|
| Source, weather and label closure | Complete remaining provider/target adapters and scheduling, physical/lead fitting, exact labels and calibration/fallback; explicit-interval/GEFS and physical/PWS inference now have bounded candidate preparation scheduling. Prove causal source/target identity, coverage, lead/ablation and required OOS quality on actual evidence. R06–R13, R25–R28, R31. | 45–90 | LOW | Working authorized provider access; exact source/version history and sufficient evidence; independent semantic/calibration review. |
| Evidence and controlled learning | Close complete replay/provenance, remaining proof-delivery/selection/report joins, operational learner scheduling/isolation, remaining target families, rolling degradation evidence and accepted initial-bundle/learning governance; conditioned capture/fitting, scoped Brier/log-loss/reliability/calibration error and automatic entry-attributed realized-paper P&L/drawdown now reach reviewed safety reduction and audits; horizon-specific maker counterfactuals also reach scoped monitoring/retirement/audits; reconciled synthetic fill-based markouts now reach the same monitoring/audit path; bounded archived PAPER fill/terminal delivery now reaches the common account and audits; receipt-driven fresh census/exit reevaluation now also joins the candidate; validated execution-window cost/causal price reporting now also reaches the candidate audit worker; matched EV/residual, actual execution-cost calibration and operational evidence remain open. Prove deterministic dataset-to-artifact and rollback/reuse/failure behavior with required real evidence. R02–R05, R14–R17, R40–R42, R47. | 30–60 | LOW | Upstream exact labels; independent initial champion and governance acceptance; isolated learning environment. |
| Strategy, portfolio and execution integration | Finish missing relative/structural/exit/redemption, correlation, costs and maker/reward paths. Prove full common-account scenario/reservation/reconciliation and strategy eligibility across required failure cases. R18–R24, R29–R30, R32–R36. | 35–70 | LOW | Reviewed mappings/parameters and actual source/execution evidence; funded fill learning remains later and separately authorized. |
| Independent safety, identity and host | Finish independent cancel-only guardian and protected command/auth routing; prepare and verify isolated deployment/recovery configuration. Prove custody, permissions, resource budgets and authenticated safety behavior. R37–R39, R43–R44. | 30–60 | LOW | Owner account entitlement/access, approved isolated host and deployment action; alpha-dev resource/isolation currently unpassed. V10 maintenance stays deferred absent an exact dependency. |
| Regression and unfunded acceptance | Run complete integration/fault/security acceptance and permitted unfunded account/execution checks; resolve findings. Proof is the original acceptance matrix with independent review and reproducible exact-tree results. R45, R48. | 25–50 | LOW | Independent reviewers and permitted existing-account access; no wallet/account creation or financial activation is implied. |
| Operational comparison and release | Verify authorized isolated paper/shadow runtime, empirical V10/V11 comparison and strategy-specific release gates; document stale forward-control gap and exact release/rollback identity. Prove every unfunded readiness gate before READY_TO_FUND. R00, R46, R49. | 15–30 | LOW | Separate deployment approval, actual current-input/clock evidence, host gate and independent acceptance. External evidence collection time is not included or assigned a fixed calendar wait. |

## Saved continuation handoff — latest full verification

Latest implementation **f1752a8157c85ce1e975f64cd80b11e5a6318780**, tree
**581fa1011fe62ec0e135a59be2d1719b7e871cd9**, branch
`weather-v11-profitability-upgrade-2026-09-23`. Public/local tree equality and
fetch/alignment passed (session 37775 exit 0), clean workspace. This subsequent
reporting-only checkpoint changes no source or tests; resolve its own exact
commit/tree with `git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md`.
No prior or unfinished work was discarded, reset, cleaned or rolled back.

Final full regression **PASS: 3566 passed, four existing FastAPI deprecation
warnings, 283.92 s, exit 0**. Wrapper 284.571 s; user 202.628285 s; system 73.054890 s;
peak RSS **157996 KiB**. All **806 tracked files** remained unchanged; input-map
digest `f828fec9bf9da2d2a8ec6eb4c93e283f91bfa5e2dfc4ef215137f1eab25c7128`.
Exclusive run session 13111 completed. Retained local result/log:
`/workspace/scratch/38af7099c566/v11-test-evidence/model-census-full-20260924-01.json`
and `.log`, runner `run_model_census_full.py`. Do not overwrite/reuse that run name
or repeat the passed regression without a relevant change/finding. No test or
source collection operation remains running at this handoff.

Off-host headroom before the full run: 21474836480-byte cgroup memory limit,
6927261696 bytes current use, eight CPU quota equivalents, 25656700928 bytes disk
free; high/max/OOM counters zero. These checks apply only to this off-host test
workspace. They do not establish alpha-dev capacity or production isolation.

Completed and published this continuation: complete run-bound GEFS source/path
integration; forecast-vector causal dataset capture; bounded six-hour run rollover;
fresh multi-step forecast census; bounded consistent source views; shared safety
scheduling and adoption of an exact completed census model. Learning-specific
related run: **114 passed / 22.55 s**. Rollover: **112 passed / 38.26 s**. Final
census/source/queue/safety related run: **241 passed / 78.97 s**. Earlier full
source/learning/rollover regression: **3545 passed / 243.99 s**; the later full run
above covers the subsequent archive/queue integration. All network/clock examples
are synthetic/mock tests, not live data, real fills or independent review.

Estimated full original engineering scope: **79/200, approximately 40%**. Formal
fully completed requirements: **1/50 (2%)**. The fixed calculation remains in
`docs/V11_ENGINEERING_PROGRESS.md`; no extra completion credit comes from tests,
elapsed effort or this handoff. Remaining implementation, actual evidence,
verified deployment and unfunded acceptance remain in that denominator.

Next concrete code action: align declared forecast-member feature schemas with
the immutable parent bundle, then connect the captured causal dataset to the
existing bounded learner. Inspection confirms that the learner requires exact
parent/dataset schema identity; capture alone does not establish that compatibility.
Retain the parent, original prediction hashes, target distinctions and NO_PROMOTION
on missing independent labels/evidence. Actual exact-label production, calibrated
artifacts and an accepted initial champion remain unverified. Continue all other
full-master provider, conditioned/PWS, execution, independent guardian, operational
comparison and unfunded acceptance requirements; this is not a forensic-only or
component-only final deliverable.

Open operational findings: actual NOAA bytes/packing/latency remain unverified
after the recorded HTTP proxy/client failure; no repeated probe or bypass occurred.
Forecast collection capacity is also unverified: per-field rate limits and epoch
expiry can gate larger event sets; the maximum supported plan count is not proof
that such a deployment meets latency/headroom requirements. No source/clock probe,
real model training, deployment or commissioning acceptance is claimed.

V10 remains unchanged by this work. Its stale successful cycles and memory pressure,
alpha-dev resource/isolation, backup/recovery and inventory-verification findings
remain open. Maintenance is DEFERRED. Inventory is OWNER-REPORTED / INDEPENDENT
VERIFICATION PENDING. No owner maintenance action is requested. No guard, systemd,
signal, stop/restart, executor, deployment, funding, transfer or real-order action
occurred. **NOT_READY_TO_FUND**; any eventual budget/live activation needs separate
approval after the original readiness and strategy-eligibility gates actually pass.

## Latest implementation milestone — fresh MODEL census

Continued from published reporting HEAD **e21bc82b65a8dac613f54ec87a30309ddc4346b6**,
tree **e0c0367c0065a60b9deb95a6a4c1a43428491897** (alignment session 44873 exit 0).
Implemented `v11/model_census.py` and joined the typed candidate, GEFS worker,
census worker, event queue and paper safety scheduler. A bounded collection epoch
precedes every raw model field; full coverage is completed under the ordinary
short claim with newly collected books/observations. New losses, expiry, source
changes and rule drift retain their gates. Partial state survives interruption.
No long event claim, second collection owner, receipt renewal or model authority
is introduced. Same-run replacement requires all new fields and retains history.
The auxiliary worker adopts an exact current completed census model without
refetching or attempting a duplicate aggregate. Details: `docs/V11_MODEL_CENSUS.md`.

One full mocked path reached the existing two-second assembly limit because of
repeated member queries. A bounded consistent `EvidenceStore.source_batch` now
removes those repeated scans: 8 MiB / 1000 decoded rows / two seconds, with the
original aggregate-head publication guard. Decision/CAS limits remain unchanged.
No schema migration or host/service mutation occurred.

Final related suite: **241 passed in 78.97 s, exit 0**, session 86764. This includes
**21 new cases**, now **1230** above baseline 2336. Earlier shared checks: 106 passed
in 21.15 s. New cases first produced 7 passes / 3 failures from an overlong request
ID; the ID was shortened without changing its limit. Next run: 34 passed / two
failures in 49.45 s (assembly time bound and a fixture's pre-existing coordinator
records assertion). After bounded reads and the fixture correction: 76 passed
in 62.54 s. The final 241-pass run also covers auxiliary completed-model adoption.
No assertion failure remains. A new locked full regression is required because
the archive and queue paths changed after the previously verified tree below.

Supplementary estimate stays **79/200, approximately 40%**; formal completion stays
**1/50 (2%)**. R09/R11/R33 already hold their named integration credits. This work
does not establish actual provider access/packing, calibration, independent
review, OS guardian isolation, deployed comparison or unfunded acceptance.

Next: full regression on the saved census tree, then source-aware learning
contracts and the exact-label/calibration/initial-champion integration. Preserve
the remaining other-provider, observed-prefix/remaining-path, PWS lead, execution,
guardian and full-master requirements. Actual source validation remains blocked
by the previously recorded off-host HTTP proxy/client compatibility finding;
no failed network call was repeated or restriction bypassed. Independent code
work does not require an owner maintenance command.

V10 remains unchanged. Its recorded stale-cycle/memory-pressure and alpha-dev
resource/isolation findings remain open. Maintenance is DEFERRED; owner inventory
hash remains OWNER-REPORTED / INDEPENDENT VERIFICATION PENDING. No deployment,
funding, money movement, executor or real-money authority change occurred.

## Last full regression, before the fresh MODEL census changes

Latest fully verified implementation: **d073d34a82c3d8d38602a6936e158986e6460654**,
tree **e596aee1e66b258d01d38a10947d9cb75abad208**. GitHub/local tree equality
and fetch/alignment passed (session 29784, exit 0), clean worktree. This later
reporting checkpoint changes documentation only.

Full combined regression **PASS: 3545 passed, four existing FastAPI warnings,
243.99 s, exit 0**. Wrapper elapsed 244.680 s; user 170.027036 s; system 65.531045 s;
peak RSS 157768 KiB. All **802 tracked files** remained unchanged; input-map digest
`1d37bcf938f606b4d9e6bc1ba0891674d289035c3ea48c60229d2348189a171b`.
Exclusive session 59899 completed. Evidence outside Git:
`/workspace/scratch/38af7099c566/v11-test-evidence/gefs-learning-full-20260924-01.json`
and `.log`, runner `run_gefs_learning_full.py`. Do not overwrite/reuse this name
or duplicate the completed run without a relevant source change or finding.

Off-host resource gate before this run: 20 GiB cgroup limit, 6869114880 bytes
current use, eight CPU quota equivalents, 25707712512 bytes disk free; high/max/
OOM counters zero. This is not alpha-dev headroom evidence. No V10 workload or
service action occurred. Estimate **79/200, approximately 40%**, formal **1/50
(2%)**, unchanged. Next implementation: fresh multi-step forecast census.

## Current priority override — V11 runtime integration

Owner update September 24: V10 maintenance/suspension preparation is DEFERRED.
Do not request or execute the staged inventory verifier, add maintenance helpers
or tests, install guards, mutate systemd, signal, stop or restart V10. Preserve
all prepared work and evidence. Inventory SHA-256
b161426cff5b39b262e72a6e8142982dd29fa8a0bf29c9965232edf1ff364bd3 remains
OWNER-REPORTED / INDEPENDENT VERIFICATION PENDING. Backup, recovery, resource,
isolation and suspension gates remain open, not completed by deferral.
Reopen only for a concrete otherwise-blocked required integration/deployment
dependency or new urgent safety/data-loss evidence, with the minimum owner action.
Historical owner-action instructions below are superseded by this priority.

At the priority override, recovered branch HEAD 769fb19c702cc91d33a19ab362a575ac86a2b0a2, tree
5129ea064d614c91665389e2258470f45a2c61a2, clean worktree and no running local
operation. Master bytes match the authoritative hash. All work remains off-host;
runtime scheduling, causal source inputs, heartbeat/clock gates and cancellation
integration subsequently advanced as recorded below. V10 health/resource findings remain
open. No funding, deployment, financial activation or new host workload authorized.

## Current continuation — forecast-run rollover

Published learning implementation **943d704dd269db274f37ca34075e2578ca829a49**,
tree **4062b72a48e3d9538bde0ab8e088860abb6710e7**; GitHub/local equality and
fetch/alignment passed (session 89243, exit 0), clean worktree. Continued from
that state without replacing prior work or touching V10.

Implemented `v11/gefs_schedule.py`, optional runtime rollover and its typed
candidate policy. Request selection uses an explicit bounded lag, exact six-hour
initializations and full local-day coverage; scheduled time is not publication
proof. Partial fields and earlier models remain archived. New runs receive
distinct input/path identities. Interrupted prior operations reconcile before
advancement, and provider cooldowns remain shared. Ended days/stale plans gate.
The unchanged fixed-run default remains available. The runtime policy cannot be
changed silently on recovery. See `docs/V11_GEFS_SOURCE.md`.

Initial focused run: **70 passed / 2 failed in 30.35 s**. One failure found that
a newer run selection could hide an invalid recovered initialization; recovered
state is now validated independently. The other was a fixture expecting a field
fetch during a deliberate separate rollover step; the candidate collection test
now starts with its current scheduled seed. Final source/schedule/candidate/clock
integration: **112 passed in 38.26 s, exit 0** (session 49339), including **18 new
cases**, now **1209** above baseline 2336. No open assertion failure remains.

Estimate remains **79/200, approximately 40%**; formal **1/50 (2%)**. R09/R11
already have source integration credits. This expands them without earning
actual provider, empirical, independent or deployment acceptance.

Next verification: a uniquely named locked full regression covering the new
shared GEFS, candidate and learning changes. Do not edit tracked files during
that run or claim the prior 3442-pass result covers the new implementation.
Next code dependency: multi-step forecast census with fresh-response and
loss-generation fences. Current census intentionally refuses required MODEL;
do not clear it by substituting old raw data or extending an event work deadline.
Then continue remaining exact-label/calibration/learning, provider, guardian and
full-master acceptance work. No new owner command is needed for off-host work.

V10 unchanged; stale cycles/memory pressure, alpha-dev resource/isolation and
preservation/recovery gates remain open. Maintenance remains DEFERRED. Inventory
is OWNER-REPORTED / INDEPENDENT VERIFICATION PENDING. No financial or deployment
authority is granted by this checkpoint.

## Previous continuation — forecast-vector learning capture

Recovered saved implementation **6681d68b7d21357c83e702f048dadf71e77223aa**,
tree **f3668f94a4b75d017078b9e8ea05e86611369e1a**, on the unchanged V11 branch.
The three unfinished learning files were preserved. Recorded test session 39954
was no longer accessible; no pytest/full-run/publication process remained. Its
final result was unavailable, so one bounded replacement run was justified.
No reset, clean, rollback, duplicated live operation or V10 access occurred.

Implemented `v11/learning_capture.py` and joined `strategy_pipeline.py` to the
existing causal dataset path. Every YES bucket of each evaluated unconditioned
forecast is captured before entry economics, with exact source/bundle/rule
identity, actual feature time, immutable replay and original expiry. Explicit
complete labels join through existing cutoff and provenance checks. No label,
training, promotion, fill or account authority is created. Conditioned and other
learning targets remain explicitly unimplemented. Details and limitations:
`docs/V11_LEARNING_CAPTURE.md`.

Final related verification: **114 passed in 22.55 s, exit 0** (session 25406).
This includes **17 new tests**, now **1191** above baseline 2336. The prior
interrupted attempt had 64 passes and one fixture assertion failure about
pre-existing coordinator events; the corrected fixture asserts no new events.
No assertion failure remains. Full regression covering GEFS plus the new shared
pipeline changes is due at the next shared integration milestone; the prior
3442-pass full run does not cover them.

Supplementary estimate **79/200, approximately 40%**, earns R14 J for the
demonstrated protected prediction -> exact feature/decision capture -> labeled
causal dataset integration. Formal completion remains **1/50 (2%)**; no empirical,
deployment or independent acceptance is implied. Other source/learning targets
and all full-master requirements remain in scope.

Next concrete implementation: multi-step forecast census recovery and bounded run
rollover, retaining original source receipts and partial-run history. Continue
exact labels/calibration/learning and remaining independent/operational acceptance
afterward. No owner action blocks independent development. V10 remains unchanged;
stale-cycle/memory-pressure, alpha-dev isolation/resource, backup/recovery and
deferred inventory verification gates remain open. No deployment or funding.

This checkpoint's saved commit/tree can be resolved without a self-referential
hash using `git log -1 --format='%H %T' -- docs/V11_WORK_CHECKPOINT.md`.

## Previous continuation — run-bound source integration

Recovered and independently matched local/public reporting HEAD
`f39de24df5970dae39989d3ad818842e4b71e7e9`, tree
`0865293fbc2c896c4e58a2d72741a8a2bc635539`. Branch is unchanged, worktree was
clean and single, no project operation was running, and the regression lock was
idle. The authoritative master hash matched. No reset, cleanup, rollback,
duplicate test or V10 action occurred. Current source changes are new work.

Implemented `grib_fields.py`, `gefs_sources.py` and `gefs_runtime.py`:
bounded native-format byte decoding; exact request/run/member/grid identity;
all-member local-day path coverage; raw receipt/hash preservation; one-file
scheduled collection with crash recovery; candidate/source-health/admission joins.
Simple/IEEE packing only; unknown formats gate. The distinct piecewise-linear
forecast model explicitly does not know intrastep extremes or calibrated error.
It feeds the immutable probability bundle interface with vacuous bounds and no
activation. A changed constituent revokes its current source eligibility.
See `docs/V11_GEFS_SOURCE.md` for bounds, limits and primary references.

**242 related tests passed in 46.97 s, exit 0**, including **68 new cases**.
Initial checks: 61 passed / 4.88 s. Expanded checks: 31 passed plus one fixture
lookup error; corrected candidate case passed / 1.75 s. One wider command named
a nonexistent test file and exited 4 with no tests; the corrected wider command
passed above. No assertion failure remains. Full regression of the shared
collector/candidate/health changes is still due; the prior 3442-pass result below
does not cover these changes. The related session 55085 completed.

Estimated full-scope completion remains approximately **39%**. Supplementary
evidence numerator is now **78/200**: the demonstrated R11 source-to-protected-
inference join earns J, with no new E/A acceptance. Formal completion stays
**1/50 (2%)**. No actual source, calibration, independent or deployed acceptance
was inferred from tests.

The bounded off-host NOAA data probe failed before HTTP: the pinned HTTP client
rejects the configured socks5h proxy scheme. Private evidence SHA-256:
`2e9eb774c838f790f03a481bb469253ef715214fe9911584bda425d86d17fc1f`.
No proxy/access restriction was bypassed and no actual field received. The
configured package-index lookup returned no ecCodes distribution; none was
installed. Actual source/packing parity and model availability remain unverified.
Plans currently pin a run; automatic rollover and multi-step forecast census
remain pending. No owner action is requested for these independent code tasks.

Next concrete implementation: connect archived forecast predictions to the
causal learning dataset, then complete run rollover/census, calibration/learning,
independent guardian, other providers and all remaining master acceptance gates.
Broader regression is required at the next shared integration milestone. Preserve
unfinished work and resume any recorded operation before retrying it.
V10 stale-cycle/memory-pressure and host-resource/isolation findings remain open.
Maintenance remains DEFERRED; inventory is OWNER-REPORTED / INDEPENDENT
VERIFICATION PENDING. No deployment, service, executor or financial action.

## Previous continuation milestone

Latest verified implementation: **84068f641840574a6fd82f73e53a2a0ea14e944e**,
tree **671fe620c0c05a47639167265b947d6da9c609e8**. Public/local tree equality
and fetch/alignment passed (session 2093, exit 0), with a clean workspace.
This later reporting checkpoint changes documentation only. No newer work was
reset, removed or overwritten. V10 and its prepared maintenance work were untouched.

Two integrations were completed this continuation:

- Bounded PWS raw archive → QC → candidate scheduling → event routing and
  health/admission metadata guards, plus required fresh-census collection.
- Archived GEFS response → exact request/grid/day/member normalization → finite
  candidate worker. Original receipts remain intact; exact run initialization
  and publication stay UNKNOWN. The normal inference and source-health gates
  reject unverified run age. No new forecast HTTP endpoint or access authority
  was added. Unverified auxiliary normalization cannot create an unrelated
  whole-event census failure. Actual run-bound forecast ingestion remains open.

Final full regression: **3442 passed, four existing FastAPI deprecation warnings,
229.86 s, exit 0**. Wrapper elapsed 230.552 s, user 158.778876 s, system 64.060153 s,
peak RSS 157204 KiB. All **791 tracked files** remained byte-identical during
the run; input-map digest
`30d375cb139e668a6bb4d7a8472a7787163c692158946551426832a6f4520af0`.
The exclusive run finished (session 99634); no test operation remains running.
Durable off-repo evidence:
`/workspace/scratch/38af7099c566/v11-test-evidence/sources-runtime-full-20260924-01.json`
and matching `.log`; runner `run_sources_full.py`. Do not reuse/overwrite that
run name or repeat the regression without a relevant change/new finding.

There are **43 new tests** in this continuation (21 PWS, 22 forecast), 1106 above
the 2336-test baseline. Forecast related checks: 44 passed in 1.16 s; wider
integration 145 passed in 20.51 s; final focused checks 23 passed in 2.53 s.
All are off-host synthetic/mock checks. They are not live/forward evidence,
profitability, independent review or deployed acceptance.

Off-host resource check before regression: cgroup limit 21474836480 bytes,
current use 6825902080 bytes, eight CPU-quota equivalents, disk free 25745367040
bytes; cgroup high/max/OOM counters zero. This is not alpha-dev capacity evidence.
Its stale-cycle, memory-pressure, resource/isolation and recovery findings remain
open; optional maintenance remains deferred, and no owner action is requested.

**Estimated full-scope engineering completion: approximately 39%, unchanged
after forecast work/full regression. Fully completed requirements: 1/50 (2%).**
The fixed supplementary calculation remains **77/200** in
`docs/V11_ENGINEERING_PROGRESS.md`. R10's demonstrated runtime join earned one
unit; the forecast work does not close its actual provenance/access dependency
or earn a second already-credited R09 integration unit. Formal acceptance has
not changed. No funding-dependent or real-money step has been performed.

Next unfinished implementation: a permitted, bounded, **run-bound forecast
source/decoder** with original initialization/availability and full-day grid
coverage, then its model-input/census integration. The existing seamless
response cannot supply that proof; a separate metadata timestamp cannot be
substituted. Continue exact-label/conditioned-inference, calibration/learning,
remaining independent guardian/execution/acceptance work under the full master.
Missing actual inputs and owner commissioning remain explicit gates, not
completion claims. Do not reopen V10 maintenance for unrelated development.

### PWS integration and recovery detail

Recovered local/public HEAD `7bd4b3b51b5abe28efa3eecff051553b2adaa0d7`, tree
`35db479b309bdc0f341c1adc4f7edbdb56ac01e7`, clean single worktree. Saved full
regression exit 0 and idle regression lock verified; no duplicate run started.
Authoritative master bytes matched again. No V10 access or maintenance was needed.

Published PWS implementation `cbe5796d9c54b462138126ad99c539a4073f4ede`, tree
`9a3a6651fdc5cf84bec7e87e5e936adbd94892d7`; public/local tree equality and
fetch/alignment passed (session 16935, exit 0), clean workspace. New
`v11/pws_runtime.py` joins existing anonymous MADIS observation collection to a
bounded, resumable defensive-QC job, health/admission metadata guards and event
routing. Required PWS census now collects fresh raw input and computes bounded
historical QC; reprocessing old receipts cannot clear a data gap. Optional PWS
does not become an unrelated census dependency. No source/model/financial
authority is created, and no fill or account balance is fabricated.

Verification: **255 related tests passed in 32.63 s**, followed by **4 focused
candidate PWS/QC tests passed in 2.58 s** after the final shared-policy guard.
Earlier focused verification was 51 passed in 0.46 s and 104 passed in 3.93 s.
Three new census tests initially failed because their fixture constructed a
RuleFingerprint incorrectly; they now use the actual compiler. All five census
tests passed in 0.99 s and are included in the 255 related passes. This was a
fixture error, not a passed inaccessible check. There are **21 new tests** in
this milestone. Full regression below predates these source changes and has not
been redundantly rerun; a broader run is due at the next integrated milestone.

**Estimated full-scope engineering completion: approximately 39%. Formal completed
requirements: 1/50 (2%).** Supplementary method and denominator are fixed in
`docs/V11_ENGINEERING_PROGRESS.md`: 200 named evidence milestones, four per
original package. Recovery baseline 76/200 (approximately 38%); newly demonstrated
R10 runtime integration adds one unit, now 77/200. No partial package has become
formally accepted. Real source/label/calibration evidence, independent review,
verified isolation/deployment and unfunded readiness remain in the denominator.
Tests, elapsed time and maintenance preparation do not automatically earn units.

At the PWS checkpoint, the next implementation was the forecast raw-source/
run-provenance adapter and candidate integration; the subsequent milestone above
records what was implemented and what remains blocked/unverified. Continue the
full master requirements, including learned/conditioned inference, independent
guardian, acceptance and commissioning. Do not reopen deferred V10 work without
a concrete necessary dependency. No test operation remains running.

### Previous maker milestone

The typed candidate now connects current maker proposal factories, protected
payout context, research observations, safety retirement and due counterfactual
markouts. Maker outputs never become economic account proposals or fills.
Focused new factory/adapter checks: **9 passed in 3.19 s**. Full unchanged-input
regression: **3399 passed, four existing FastAPI warnings, 208.79 s; exit 0**.
Wrapper 209.426 s, user 143.670744 s, system 58.675137 s, peak RSS 159120 KiB.
The exclusive regression run completed; no test operation remains running.
Fully completed packages remain **1/50**; formal, empirical and independent
acceptance remain open. Next: raw forecast/PWS-QC source and model-provenance
integration. No V10 maintenance, deployment or funding is requested.

## Exact identities and scope

- Branch: `weather-v11-profitability-upgrade-2026-09-23`.
- Last verified implementation HEAD: `84068f641840574a6fd82f73e53a2a0ea14e944e`.
- Last verified implementation tree: `671fe620c0c05a47639167265b947d6da9c609e8`.
- Previous PWS implementation: `cbe5796d9c54b462138126ad99c539a4073f4ede`,
  tree `9a3a6651fdc5cf84bec7e87e5e936adbd94892d7`; reporting checkpoint
  `fb0040dae733f4bdc3e94e852cb52cd670c2b105`.
- Previous maker proposal/context implementation: `306a7ece6ade71e90d436f7c99730d18a8597d3c`,
  tree `56977f5bee32381fac7e954abe83cc1c1901020f`. Public/local
  tree equality passed; fetch/alignment exited 0 (session 70687), with a clean
  worktree. No unfinished work was discarded. This later documentation checkpoint
  records that exact tested implementation without changing source/tests.
- A later commit containing this checkpoint may include the newer work below. Resolve its own
  exact commit with `git log -1 --format=%H -- docs/V11_WORK_CHECKPOINT.md`, and its
  tree with `git rev-parse <that-commit>^{tree}`; no self-referential hash claim.
- Prior maker quote implementation: `f0e0abc3345f63d170c29b5ba5d5d74c91238477`,
  tree `52cbef6c6d9372beca2c60ed32675ecdc9298023`; its recovery checkpoint was
  `e5d7afe4123a3cd46eb473ea4e6de3469b507662`.
- Preservation-preparation checkpoint: `2dc11b341f0885ea2af534c39a3873e7b2483006`,
  tree `52000e11dcbdacb5b07df1fd8fcc0fea7e2bc029`.
- Prior maker-feature implementation: `a3a0a05b3ea2ecdc55190c711a75d6d7cd990922`,
  tree `053189306c136d11f32943ec62f0807c827a582c`; its recovery checkpoint was
  `53cae2da33e97c993c76ab976d2f079a97bf6ae1`.
- Earlier full regression: **3,293 passed, four existing warnings, 212.37 s** on
  the candidate-runner/shared-runtime implementation recorded below. Its predecessor full run was
  2,894 passing at `f69e318d04b8771f1de3074928ae63d3951cebec`.
- Prior implementation: `dd1e85706eb0a26c9bb8aef1317cb635a791b920`, tree
  `8bf30057a39e8690d457e531b781b953a2476878`. Prior recovered checkpoint
  `6a602a7fe7f8f36aa238140f89b15b3e071423fa`. Health/plan checkpoint
  `a64636dd819f8a0bd1b10f66ccaf563a6215fc2e`.
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
through the connected development user. The completed owner probe below returned
no journal metadata in its two-hour window and confirmed stale successful cycles. No V10 service, source, limit, permission
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

## Phase 4 event-risk and executable-economics milestone

Published `v11/event_risk.py` and `v11/valuation.py`. Event states bind scoped
source/book evidence, metrics, policy and release. Recovery requires distinct
advancing observations, healthy market/data samples and a configured span; elapsed
time or repeated receipt cannot suffice. Durable operator reductions never
self-restore. CAS state transitions, current-head/operator checks and earliest
source/metric expiries preserve stale-state rejection. Cancellation remains only
REQUESTED, with no claimed inventory change.

Valuation binds exact contract/token/side, rule, collateral and bundle. Depth walk,
fees and reserves have explicit units/horizons and nonoverlapping risk coverage.
Unknown/missing costs, insufficient depth and oversize requests gate economics.
The existing BUY fee policy is reused with receipt/target/limit/post-only checks;
no SELL fee rule is guessed. Hold-versus-sale keeps sunk costs out of the prospective
choice and in hypothetical lifetime P&L. Next-observation probabilities cannot be
payout/exit prices. Current vacuous bounds still cannot qualify settlement entry;
validated repricing and exact fee/source authority remain unfinished.

Verification: 28 event + 26 valuation tests; 101 event/valuation/existing-fee checks
passed. The full regression passed **2,618 tests, four existing warnings, 68.67 s**.
The first focused run found a duplicate-keyword error in the test helper, corrected
before the passing runs. A lost session publishing helper was restored; that failed
attempt made no Git mutation. Publication then completed with matching local/public
trees and preserved files. No test remains running. No alpha-dev action occurred.
Protected operator routing, full metric derivation, coordinator/guardian execution,
actual cancel/exit reconciliation and empirical strategy validation remain open.

## Phase 5 scenario and correlated-risk milestone

Published `v11/scenario_risk.py`: exact YES/NO resolving outcomes, held/all-in cost
basis, adverse optional fills for unresolved remainders, incremental risk,
concentration and partitioned attribution. Pending complete sets keep legging
risk; selling a hedge is not automatically a risk reduction. Versioned station
metadata binds city/region/weather/source/model groups. Different cities get no
independence credit; profitable hypothetical outcomes do not offset other losses.

15 synthetic tests passed. Targeted checks caught the legacy strict-contract
empty city label; the final implementation requires the exact station metadata
fingerprint for mapping rather than guessing a city. The prior 2,618-test broad
regression was not repeated for this isolated additive module. No unfinished test,
alpha-dev action or deployment. Actual dependence mapping/protected review and
account/strategy integration remain pending. No complete-package credit added.

## Phase 5 account coordination milestone

Published `v11/paper_coordinator.py` and `v11/allocation.py`. One nonfinancial
account journal ranks proposals before allocation, nets desired-position/conflicting
exposure, binds exact city metadata and valuation/event pins, and checks common
cash/inventory/scenario/correlation limits. Atomic account CAS also guards operator
and event heads, including absent operator scopes, in the same transaction.
Fixed policy identity, retained cash and reduction-only sizing cannot grow caps.

Restart preserves ambiguous submissions. Expiry/timeout/cancel request cannot
release reservations. Explicit synthetic paper fills atomically preserve cash,
lots, all-in basis and unfilled risk. Duplicate fill identities do not create P&L;
fee overruns fault future admission. Partial sales preserve basis rounding residue.
Only complete exact paper terminal reconciliation releases the remainder. No
network/order adapter, live/control ledger or real account mutation exists here.

20 new coordinator/allocation tests and 91 combined checks passed. A canonical
request comparison fixed tuple/list JSON replay portability during targeted tests.
Full regression: **2,653 passed, four existing warnings, 70.49 s**. Current vacuous
settlement estimates still do not qualify. Accepted downstream fixtures are
explicitly synthetic account-mechanics tests, not profitable-strategy evidence.
Scoped strategy/certification/model-authority integration, external live exposure,
settlement/redemption, retention and guardian/executor commissioning remain open.

## CI portability correction

GitHub Actions run `35931387025` at `77a0755` failed three model-publisher crash
fixtures on both Python 3.11 and 3.12 ordinary runners; 2,615 other tests passed.
Decoded logs show `MODEL_AUTHORITY_LOCK_CUSTODY`: the fixture mocked UID/custody
but missed lock ownership and fchown. Runtime hash/isolation jobs passed.

Published correction `d4902a26aef5964e437f46d605a93db153d4f3ef` scopes a synthetic
OS fixture to the test module and adds a negative lock-custody test. Production
permissions/authority code are unchanged. All 22 governance tests pass locally.
A local unprivileged-process launch was blocked before execution because this
container has zero effective capabilities and NoNewPrivs despite UID 0. It is not
claimed passed; the new GitHub runner result must be checked. See
`docs/V11_CI_FINDINGS.md`. The broad regression above preceded this test-only fix;
it was not repeated over unchanged production code. No tests remain running.

## Scoped strategy admission milestone

Published `v11/strategy_admission.py` and integrated mandatory per-strategy pins
into the paper coordinator before reservation and submission-state transition.
Protected capability review, station/rule scope, metadata, model version/bundle/
epoch, manual-review/size overlay and source leases now join at one admission
boundary. Missing protected commissioning remains gated. The model/review reader
is re-read; no writer or financial mode is introduced.

Known model issue time and exact forecast target are required. Sensor/observation
ages remain causal; fresh receipt or feature recomputation cannot refresh old PWS.
Older received source versions are refused. Source heads (including an absent
official head) are pinned before reads and atomically guarded. A new official
observation invalidates pre-confirmation, and new PWS data requires QC recompute.
Every attributed strategy needs its own matching admission, and model reductions
also constrain size. Existing reservations survive a later admission demotion.

15 admission tests plus one added coordinator case passed with dependencies:
**109 targeted checks, 2.49 s**. Full regression: **2,670 passed, four existing
warnings, 67.31 s**. Two fixture proof-ID collisions were corrected with explicit
scope prefixes; a rejection-reason check was refined to preserve the specific
new-official reason. No tests remain running. Source/strategy factories, actual
champions/labels and all runtime acceptance remain unfinished; these pins do not
manufacture payout, PWS lead, finality or executable exit evidence.

The CI portability finding is now CLOSED: ordinary Python 3.11/3.12 runners passed
run `35933848981` at `d4902a2`; coordinator run `35933951235` at `8437079` also
completed successfully. The blocked local UID probe remains explicitly unpassed.
No protected host permission was weakened, no V10 action or alpha-dev workload
occurred, and no independent review is claimed.

## Phase 6 forecast and same-day milestone

Published `75a1b24a22dc622a90d53bdc959c115db92df7f4`, tree
`556dc6d2a59a0350ad8dd42ef8df09a02744e891`. Added
`v11/strategy_pipeline.py`, 27 dedicated synthetic checks and
`docs/V11_TEMPERATURE_STRATEGIES.md`. Evaluation reconstructs input members and
identities from leased archive records, applies the protected frozen bundle,
checks local contract-day routing, values exact executable depth/costs and
produces common coordinator proposal data only if qualified. Same-day conditioning
binds the exact revision/population and complete unresolved-day/model coverage.
Its original inference cutoff survives later archival/evaluation. Stable request
IDs retain partial valuations and completed evidence across interruption; they
never refresh authority. New source arrivals, event/operator suppression and
model demotion remain effective.

Full off-host regression: 2,697 passed, four existing warnings, 72.93 seconds.
Compileall and diff whitespace checks passed. Earlier scoped-admission GitHub run
35935233616 completed successfully. No live/source-normalizer commissioning,
empirical calibration, actual champion or independent acceptance is claimed.
Raw-to-inference adapters and measured scope-regime assignment remain pending;
all current settlement candidates are rejected/gated by conservative economics.
No alpha-dev access, workloads or V10 changes were needed for this milestone.

## Phase 2/4 bounded event-routing milestone

Published `bb10f5556008ef1ce6a808f33905d4811c607de1`, tree
`6718d36ee137c9f289c12daff2e3b5f6ca5d6c57`. Added `v11/event_queue.py`,
32 queue tests, one joined temperature-strategy test and `V11_EVENT_QUEUE.md`.
Durable routing covers book/trade/official/QC-PWS/model/scheduled-release inputs,
station/date/token mapping, bounded fan-out/queues/channels/bytes/age, dedupe and
received corrections, explicit drops and one process-serialized worker. Crashed
claims, reconnects and lost coverage require a full census. Clearing that state
requires newly archived full books for every token, required fresh sources and a
current rule; atomic heads protect census/completion against arrivals and gaps.
The output must have a sequence after its claim/census, not merely an equal
clock timestamp. Late/unnotified arrivals invalidate old results. A scheduled
release window is not an official observation. Queue work is nonfinancial.

124 focused integration checks passed; full regression passed 2,730 tests with
four existing warnings in 75.35 seconds. Compileall/diff checks passed. Strategy
CI run 35936624713 completed successfully. A same-timestamp stale-result defect
found during development was corrected with sequence fencing and retested.
No alpha-dev workload or change occurred. Websocket protocol integration,
periodic-census scheduling, protected route reconfiguration and queue-fault
propagation to final admission/guardian remain unfinished. Worker result timeouts
do not substitute for OS resource isolation.

## Phase 4/5 queue-to-paper admission milestone

Published `31dcd290d6cd27221d391bf504712b847d942d53`, tree
`fdd681a6986d76b5fb7577620b7052a832fae56c`. Event queue completion now binds the
exact evaluated valuation and a bounded validity window. Once a queue exists,
its current result is mandatory for paper reservation and submission-state
transition. Pending work, lost coverage, expired completion and changed raw
sources suppress admission. Queue and capture heads join account transaction
CAS, including queue absence so a racing new queue/fault cannot be ignored.
Submission suppression preserves all cash/inventory reservations; cancellation
requests and reconciliation remain available. No external order is sent.

Nine new boundary tests plus existing dependencies passed 105 focused checks.
Full off-host regression: 2,739 passed, four existing warnings, 67.08 seconds.
Queue implementation CI run 35937992616 passed. Explicit downstream synthetic
positive-economics/admission fixtures do not represent actual strategy eligibility.
No financial authority, deployment, host workload or independent review occurred.
Periodic scheduling, source adapters, protected commissioning and live guardian
integration remain open. Next: PWS observation-lead research evaluation, with
next-observation/crossing predictions kept separate from payout and executable exit.

## Phase 6 PWS observation-lead research milestone

Published `2328f20b27287e8f869e54df291879cb02004c34`, tree
`5cabc89a2a91370b79031a61f7918dce0cdd10ca`. Added `v11/pws_lead.py`,
18 tests and `V11_PWS_LEAD.md`. Paired immutable next-observation inference binds
an official anchor, declared receipt horizon, fresh QC PWS and model revisions.
Bounded hash-bound provenance requires identical non-PWS leaves; hidden PWS in
the ablation is refused. Atomic source heads invalidate racing pre-confirmation
work. First-received-report scores distinguish anchor corrections, missing/late
reports and harmful/beneficial paired differences. They explicitly do not attest
true next-published labels, source continuity, independent lead advantage,
settlement, executable exit or P&L. All outputs are GATED/RESEARCH with no proposal.

119 focused checks passed in 2.80 seconds, including 18 new tests. No unchanged
full suite rerun was needed for this isolated new research module: the most recent
full pass is 2,739, with 18 additional focused checks passing. Compilation and
whitespace checks passed. Queue-admission CI run 35938568101 passed. No actual
lead model was trained/promoted, no eligible paper entry created and no V10 work
or change performed. Exact adapters, independently supported labels, calibrated
lead models and common paper economics integration remain unfinished.

## Separately scoped model authority milestone

Published `c0d97df2a0718ae87b8c36cf3d8ae4c5869974a7`, tree
`44718b030c4d4cf7f19eaf9257e15aeec39acefe`. Protected state selection now uses
exact scope and nonfinancial PAPER/SHADOW mode. Distinct next-observation and
payout champions can coexist under separate reviews. Missing state never falls
back to the legacy singleton. Both reader and standalone publisher reject wrong
slot identities; existing custody, atomic transitions and reductions remain.
No initialization, migration, provisioning or financial mode was introduced.

14 new slot tests and 97 related checks passed in 3.17 seconds. Full off-host
regression passed **2,771 tests, four existing warnings, 68.88 seconds**. A fixture
initially mixed state directories into the immutable artifact directory; it was
corrected without weakening production checks. Compileall and diff checks passed.
PWS research CI run 35939317722 passed. Resume inspection confirmed the saved
HEAD, preserved all six unfinished files and found no running project operations.
No duplicate full test run or alpha-dev action occurred. Git publication passed
with identical local/public trees and a clean worktree. No independent review,
actual model approval or complete-package acceptance is claimed.

## Phase 6 PWS common-economics milestone

Published `d0f7c737268ca45a62738dd289d61c6d42ee57fd`, tree
`74c9ee8d4884ea061c98e81090a4d7b95b15b1d9`. Added `v11/pws_admission.py`
and integrated paired pre-confirmation pins into the temperature strategy and
common paper coordinator. Distinct approved observation/payout targets, matching
source/context/release identity, stricter source expiry and both model epochs
revalidate before reservation and paper submission-state transition. Payout uses
exact remaining-day conditioning; observation probability is not payout or exit.
New exact-book revisions and racing receipts require new economics. Common
netting/risk/reservations remain in force, with no special PWS risk allocation.

16 new joined tests passed; related checks passed 121 tests in 8.32 seconds.
The initial focused run found a duplicate-keyword error in a test helper and it
was corrected. The final full suite passed **2,787 tests, four existing warnings,
82.56 seconds**. Compilation and whitespace checks passed. Scoped-registry CI
run 35940278264 passed. The PWS model/capability fixtures substitute synthetic
reviews; positive-EV account fixtures are explicitly downstream mechanics, not
actual calibration or strategy eligibility. Actual current bounds still reject.

One publication attempt stopped before Git mutation because the session helper
was missing. It was restored and saved outside Git for continuity. Publication
then completed with matching local/public trees and preserved files. No reset,
clean, alpha-dev workload, deployment, funding or mask change occurred. No tests
remain running. Raw adapters, actual reviewed champions, lead/label evidence and
runtime/independent acceptance remain open. Next: source-shock/release reaction
through common gates, distinguishing scheduled notices from received observations
and revisions; implement exact-source/CLOB checks for any directional EVENT path.

## Phase 6 received-source reaction milestone

Published `8ccadd546e25a39a3a252d03d6f80f8824e377c1`, tree
`586d78383f63c9b282ce8cc47e5619614535ecf6`. Added `v11/source_release.py`,
RELEASE_OPPORTUNITY capability scope and both source-release sleeves in the
shared temperature evaluator/coordinator. Exact received observations and
same-observation revisions remain distinct. Schedules are provenance only.
Each payout component must incorporate the received official evidence; exact
books and event metrics must follow receipt. Current source/book/epoch heads
revalidate and atomically guard paper reservation/submission-state changes.

The directional EVENT data path requires scoped review and healthy inputs;
stronger EVENT size, EV, liquidity and lifetime limits remain effective. Operator
reductions, non-release health/risk faults and adverse execution suppress it.
Passive new-risk permission remains false. Reservations survive failed checks.
Actual current conservative payout bounds still reject all economic entries.
Positive-EV fixtures exercise downstream mechanics only, not strategy acceptance.

21 new release tests and 138 related checks passed in 12.54 seconds. An initial
size-limit fixture used five units below the actual reduced ceiling of twenty;
it was corrected to twenty-one without changing runtime limits. Full off-host
regression: **2,808 passed, four existing warnings, 85.26 seconds**. Compilation
and whitespace checks passed. PWS integration CI run 35941229234 passed. Local
resource observation showed approximately 21 GB available RAM and 23 GB free
disk; this is off-host headroom and does not clear the alpha-dev resource gate.

No tests remain running; publication has matching local/public trees and a clean
worktree. No V10/runtime/permission/financial change occurred. Exact adapters,
forecast-only releases, independent event/market/PWS corroboration metrics,
calibration, runtime and independent review remain open. Next: cross-temperature
relative-value and structural basket valuation with shared outcome/partial-leg
risk, followed by common multi-leg account integration and remaining phases.

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
| Physical/source full regression | **2,564 passed; four existing warnings; 63.64 s** |
| Event/valuation/existing-fee focused checks | **101 passed** |
| Exact scenario / correlation focused checks | **15 passed** |
| Event/EV full regression | **2,618 passed; four existing warnings; 68.67 s** |
| Account/scenario/event/evidence integration | **91 passed; 1.59 s** |
| Coordinator full regression | **2,653 passed; four existing warnings; 70.49 s** |
| Scoped admission integration | **109 passed; 2.49 s** |
| Latest scoped-admission full regression | **2,670 passed; four existing warnings; 67.31 s** |
| Governance portability correction | **22 passed locally; ordinary GitHub runners passed run 35933848981** |
| Snapshot/forensic tests | **18 passed** |
| Compileall / dependency check / diff whitespace | passed |
| Prior GitHub Actions run 35911031597 at cc268af | completed successfully |
| Probability/dataset GitHub Actions run 35924652710 at 359814e | completed successfully |
| Event/EV CI run 35931387025 | Historical FAILED fixture finding; corrected and closed by run 35933848981 |
| Coordinator CI run 35933951235 at 8437079 | completed successfully |
| Admission implementation CI 35935233616 at a2dd287 | completed successfully |
| Forecast/same-day strategy checks | **27 passed; 120 related checks passed** |
| Latest forecast/same-day full regression | **2,697 passed; four existing warnings; 72.93 s** |
| Forecast/same-day CI 35936624713 at 75a1b24 | completed successfully |
| Bounded event routing integration | **124 passed; 4.57 s** |
| Latest bounded-routing full regression | **2,730 passed; four existing warnings; 75.35 s** |
| Event-queue CI 35937992616 at bb10f55 | completed successfully |
| Queue-to-account admission integration | **105 passed; 4.70 s** |
| Latest queue-admission full regression | **2,739 passed; four existing warnings; 67.08 s** |
| Queue-admission CI 35938568101 at 31dcd29 | completed successfully |
| PWS observation-lead research | **18 new checks passed; 119 related checks passed in 2.80 s** |
| PWS research CI 35939317722 at 2328f20 | completed successfully |
| Scope/mode model slots | **14 new tests; 97 related checks passed, 3.17 s** |
| Scope-slot full regression | **2,771 passed; four existing warnings; 68.88 s** |
| Scope-slot CI 35940278264 at c0d97df | completed successfully |
| PWS paired admission/economics | **16 new checks; 121 related checks passed, 8.32 s** |
| PWS integration full regression | **2,787 passed; four existing warnings; 82.56 s** |
| PWS integration CI 35941229234 at d0f7c73 | completed successfully |
| Received-source release integration | **21 new checks; 138 related checks passed, 12.54 s** |
| Source-release full off-host regression | **2,808 passed; four existing warnings; 85.26 s** |
| Basket common-account integration | **24 new tests; 125 related checks passed, 17.72 s** |
| Latest full off-host regression | **2,851 passed; four existing warnings; 125.91 s** |
| Subsequent standalone read-only control probe | **7 tests passed, 0.07 s** |

Tests used an isolated off-host environment installed from hash-locked dev
requirements. Four warnings are pre-existing FastAPI lifecycle deprecations.
Synthetic tests are code evidence only. No independent reviewer has reviewed V11.

Under the fixed 50-package matrix, R01 is implemented and locally verified;
**1/50 = 2%** complete. Partials/stubs/open integrations receive zero completion
credit. V10 operational health remains open under R00/R44/R46. Local code,
technical readiness, canary eligibility and empirical validation stay separate.

## Recovered overnight work and joint basket valuation

Recovered local and published HEAD `ba290c071af7f578c5a9f72bbe9a1f5bd4e0c4db`
(tree `7aee3e259891866edbb1fcb24768fc06703d05e9`) without reset or cleanup.
The interrupted `basket_valuation.py` was preserved and completed; there were no
running project operations to duplicate. Source-release CI run `35942062158` at
`8ccadd546e25a39a3a252d03d6f80f8824e377c1` completed successfully. Specification
bytes again match the authoritative hash. No additional alpha-dev workload ran.

The old off-host virtual environment had a broken interpreter link after runtime
replacement. It was preserved. Hash-locked dependencies were installed in
`/workspace/scratch/38af7099c566/alpha-v11-venv-20260924` using Python 3.12.14;
`pip check` passed. This is an off-host environment repair, not a V10 change.

Published joint basket valuation with exact whole-event probabilities, executable
per-leg depth/costs, complement/exhaustive payout floors, and separate adverse
partial-fill scenarios. Every leg must pass; unknown fees or a superseded book
cannot disappear in aggregation. Full-fill payout floors are conditional, never
spendable cash, realized P&L or locked executable profit. Admission remains gated
until common-account multi-leg integration.

Verification: **19 new basket tests; 91 related checks passed in 1.60 s**. The last
full regression remains **2,808 passed, four existing warnings, 85.26 s** at the
prior source-release implementation. It was not duplicated for an additive module.
No tests remain running. R29/R30 are PARTIAL; completion remains **1/50 = 2%**.
No independent review or empirical/runtime acceptance is claimed.

## Atomic basket account and bounded control-health milestones

Published basket integration `3e84ca3822b5925dc30c502d18eec5dd68e5f56e`, tree
`aff30d127c98de32e194df86299b46caf6f8ece8`. Joint basket and single-token proposals
now share ranking, cash, scenario limits and the account CAS. All legs reserve
atomically. Protected bundle/input reproduction, scoped review, source/book/event
revalidation and aggregate limit-price EV are mandatory. Fills, cancel requests,
terminal reconciliation and restart ambiguity remain leg-specific; unfilled hedge
risk and inventory basis persist. Fully filled baskets never become invented
payout, realized P&L or redeemed cash. 24 new tests / 125 related checks passed;
full regression **2,851 passed, four existing warnings, 125.91 s**. No tests remain
running. This is local code verification, not full package or strategy acceptance.

The user's bounded V10 operational assessment was carried out with read-only
samples on September 24 at 09:12:44 and 09:14:17 UTC. Memory remained 454,397,952
bytes against MemoryHigh 419,430,400 bytes. The high-event counter increased by
3,672; full memory PSI avg60 was approximately 75.4–75.9%. V10 was active with zero
restarts, but its process was in D state. Host headroom does not pass the isolation
requirement. Executor remains MASKED/INACTIVE and controller INACTIVE.

Current status/database/WAL/SHM reads and journals remain inaccessible to the
development user. Current fresh successful cycles and useful forward-control
evidence are UNVERIFIED. Snapshot history is preserved; it is not a current health
pass. A bounded redacted probe is prepared, with seven passing tests. The connected
tool rejected the privileged invocation as **Command not allowed**; no workaround
was attempted. One owner-only read action, exact prepared path/hash/command and
preservation boundaries are in `docs/V11_CONTROL_HEALTH_ASSESSMENT.md`.

Recommendation: obtain that read-only evidence, leave V10 unchanged, continue
independent off-host implementation. No otherwise-ready V11 deployment is waiting
only for V10's resources. No suspension, restart, duplicate snapshot capture,
permission/resource weakening or V11 host workload was performed. The existing
unit's 25-second stop timeout and SendSIGKILL=yes make an ordinary stop unsuitable
under the no-force-kill constraint without a separately reviewed plan. No such
stop is authorized. R00 remains PARTIAL; fixed package completion stays 1/50.

## Whole-event discovery milestone

Published `v11/relative_value.py`, its tests and `docs/V11_RELATIVE_VALUE.md`.
Discovery reuses protected model/source pins and exact joint valuation for
individual price gaps, adjacent pairs, exhaustive YES/NO sets and complements.
Quotes are not normalized as probabilities and point-price gaps are not calibrated
alpha. Partial data, missing fees and incomplete vectors retain explicit gates.
Bounded candidates have exact event-queue result IDs and enter the same paper
account. Interrupted evaluations reuse saved values/candidates without refreshing
inference or expiry. No account mutation occurs in discovery itself.

**12 new tests and 92 related checks passed in 22.85 s.** The incomplete-vector
handling was corrected to a durable GATED result during focused verification.
Latest full regression remains 2,851 passing tests at the shared-account change;
the additive probe/discovery modules have their own passing checks. Basket account
CI run `35980156386` completed successfully at `3e84ca3822b5925dc30c502d18eec5dd68e5f56e`.
No test remains running. Fixed full-package completion remains **1/50 = 2%**.
That milestone preceded the owner health probe. The completed owner read and
current operational recommendation are recorded below; host pressure and all
deployment/financial safeguards remain explicit. Off-host work continues independently.

## Owner health evidence and bounded suspension proposal

The owner completed the protected read-only probe at 10:31:16 UTC. Its reported
latest success is September 22 at 23:41:47.652562 UTC, age 125,369 seconds (34 h
49 m); status bytes match the preserved snapshot. WAL mtime is September 23 at
00:05:39 UTC. The privileged two-hour journal metadata query succeeded with no
records. This is OWNER_REPORTED evidence, not an independently accessed current
private file. The prior owner action is COMPLETE; do not request or retry it.
Fresh persisted successful-cycle health is FAIL. Historical true health flags do
not pass current health. The complete root cause remains unproven.

Read-only service properties at 10:33 UTC still show 454,397,952 bytes charged,
executor MASKED/INACTIVE and controller INACTIVE. No V10 file, service, permission,
limit or mask was changed. No new backup, stop, signal, restart or V11 host workload
ran. The existing snapshot and completed forensic baseline remain intact.

Recommendation now: seek explicit approval for preservation-first bounded
SIGTERM-only temporary suspension as detailed in V11_CONTROL_SUSPENSION_PLAN.md.
No approval is present. The plan rejects ordinary stop's possible SIGKILL
escalation, requires a new consistent committed-WAL backup plus complete private
preservation, preserves originals, records the existing health gap separately
from suspension, and requires separate recovery approval. Expected memory relief
is conditional on actual exit; no otherwise-ready V11 release is blocked solely
by V10. V11 resource/isolation and all other deployment gates remain open.

This documentation milestone supersedes earlier pending-owner-read statements.
Recovered HEAD before edits: 6a602a7fe7f8f36aa238140f89b15b3e071423fa, tree
82629c14d54fe309201cc3e85b83176ae3931bb4. Worktree was clean and no project
operations were running. Existing checks were not duplicated. Off-host strategy
and active-position implementation continues independently. Completion: 1/50.

## Active-exit and accounting milestone

Off-host changes implement inventory-bound whole-event exits and lot-level
entry/exit/P&L attribution. New files: v11/position_management.py,
v11/position_attribution.py, tests/test_v11_position_management.py and
V11_ACTIVE_EXITS.md. The common paper coordinator now rejects bare single-token
exit comparisons without protected model/input and joint inventory reproduction.
Reservation, submission, partial fills, FIFO basis and exact queue output remain
inside the common account. Legacy lot history is explicitly unknown.

24 new tests and 78 related exit/account/basket/queue tests passed in 16.73 s.
An earlier focused run found only a decimal-string test expectation (.4 versus
0.4), corrected to numeric comparison. The expanded tests also verify interrupted
resume and queue evaluation before completion. Full regression completed PASS: **2,894 passed, four existing FastAPI
deprecation warnings, 146.00 seconds**. No test remains running. No duplicate
regression was launched. No V10 action or workload occurred. The initial focused
run was 39 pass / one decimal-string assertion failure; after correction the
related suite passed 141 checks, then the expanded exit/account/basket/queue
suite passed 78 checks before the single full regression.

Exact-finality dependencies were reviewed in V11_FINALITY_DEPENDENCIES.md. The
existing bounded WRH polling bracket cannot establish exact publication/revision
state, and HOURLY population cannot stand in for ALL_TIMES. RESULT_LAG remains
GATED with R31 OPEN. R32 remains PARTIAL pending continuous runtime, capital
rotation, emergency permission integration and external execution acceptance.

## Preparation-only authorization and verification

The owner explicitly approved preservation preparation ONLY and withheld SIGTERM,
stop/restart, service mutation, deletion/reset, executor changes and V11 deployment.
At 11:06 UTC, read-only inspection still found the same D-state main process,
single cgroup PID, six threads and 454,397,952 bytes memory; executor MASKED/INACTIVE.
Source HEAD/tree remain the frozen local V10 identity and the source is clean.
No remote task was running before inspection; no prior operations were duplicated.
Recovered V11 checkpoint bd7e9925205869f886d0679ebfe24ea6b63d2186 was clean.
The authoritative specification hash still matches. No old draft was substituted.

New preservation-only module includes bounded private physical archives, metadata/
ACL/xattr/link retention, external manifest/member integrity checks, incomplete
attempt preservation and read-only SQLite recovery checks. It has no service,
signal, privilege or restore API. 16 new tests / 41 related tests passed in 0.68 s.
The existing actual off-host snapshot passed expected SHA-256 and quick_check;
this targeted recovery check did not recapture or restart the forensic baseline.
Full regression remains 2,894 passed at f69e318; no unchanged broad run was repeated.

Prepared scripts were staged and hash verified, not executed, under
/home/alphaadmin/alpha-v11-preservation-prep-20260924 outside V10. Their exact hashes,
new proposed destination, preservation/recovery steps, evidence-loss accounting
and checklist are in V11_CONTROL_SUSPENSION_PLAN.md. No new live snapshot, private
configuration read, archive or journal export ran. Protected source access remains
owner-only; the previously rejected privilege route was not retried or bypassed.

SUSPENSION_NOT_READY: the unchanged unit retains Restart=on-failure (15 s),
SendSIGKILL=yes, FinalKillSignal=9, stop timeout 25 s and OOMPolicy=stop. Normal
SIGTERM is expected clean, but no unconditional no-restart/no-kill guarantee is
available. Watchdog/runtime timeout, hooks, triggers and pending jobs were absent
at inspection. The earlier proposed signal command is withdrawn from execution
readiness. No signal or automatic recovery is armed. Await new explicit approval
before any service action, without treating approval as missing technical proof.
Continue independent maker implementation off-host. Fixed completion: 1/50 = 2%.

## Bounded maker feature milestone

Published v11/microstructure.py, tests/test_v11_microstructure.py and
V11_MAKER_MICROSTRUCTURE.md. Exact-target causal book/print features include L1/
selected-depth imbalance, midpoint/microprice/spread, declared-sequence velocity/
acceleration and sampled variation, visible depth deltas and archived public
flow. Gaps, reconnects and unknown sequences reset temporal features. Source-head
CAS rejects racing updates; historical replay cannot refresh admission authority.
Public prints never become our fills, queue priority or trading P&L. Unlearned
execution probabilities/EV and unbound account/weather contexts stay UNKNOWN.

The first focused run returned 109 passed / two failures caused by a collision
between contract side and public aggressor direction. They are now separate
fields, with YES/NO and legacy-collision regression coverage. Corrected result:
**27 new / 113 related tests passed in 1.82 seconds**. No failure remains open.
Full regression remains 2,894 pass / four existing warnings at f69e318; it was
not repeated for unchanged components. Total distinct new tests across recorded
runs: 601 (558 before preparation, 16 preservation, 27 microstructure).

R35 is PARTIAL, not an empirically accepted execution feature/model package.
Quote survival, protected fair/inventory/event/release context, provider/runtime
integration, reviewed baseline and empirical validation remain pending. Prior
real maker fills are not required for the first separately approved bounded
canary, but no canary is authorized now. Fixed full-package completion remains
1/50 = 2%. Current work and publication operations completed; no V10 workload,
service action, new backup, real order or financial activation occurred.

## Models, authority and exact continuation

### Inventory-only authorization and access result (September 24)

Recovered checkpoint e5d7afe4123a3cd46eb473ea4e6de3469b507662, tree
0e295065443d36984b1d483ef25f1e89889c7940, with a clean worktree and no active
remote session. The owner explicitly authorized only the staged metadata inventory
command. Helper hash 50a5067f9d58405ae62c9ef84ecfdefd69fb3cfbf77f8b1008a12aa9e34cd6b9
matched again, mode 0400, no helper symlink, and the exact inventory destination
did not exist. The exact approved sudo command was attempted once through Remote
Desktop Commander and returned `Command not allowed` / `INVALID_ARGUMENT`.
The helper did not execute; no inventory hash or current backup was generated.
A following session list was empty. There was no password-waiting process,
privilege workaround, guard installation, manager reload, signal or service action.
Owner-only access remains the preservation blocker, independent of off-host work.
The precise remaining owner action and unchanged suspension gates are recorded
in V11_CONTROL_SUSPENSION_PLAN.md. Do not mistake authorization for tool access
or metadata inventory for a verified preservation set.

### Prior continuation: reversible maintenance and maker research

Recovered 53cae2da33e97c993c76ab976d2f079a97bf6ae1, tree
cbb409b292f9d63f417ae8e7b0f6d3c7744f4980, on the same development branch with a
clean worktree and no active project operation. No reset, clean or repeated
snapshot/forensic analysis occurred. The owner again withheld suspension, service
configuration changes, restart/kill/executor operations, funding and deployment.

11:42 UTC read-only inspection confirmed V10 PID 233096, zero restarts,
454,397,952 bytes charged, original unit/drop-in hashes and unchanged policies.
Installed systemd 255 manuals establish a reversible /run drop-in plus marker
mechanism: Restart=no, clear RestartForceExitStatus, SendSIGKILL=no, refused manual
starts and a failed marker condition. Memory/OOM protections stay unchanged.
This is a same-boot scoped systemd mechanism, not a kernel-OOM/global guarantee.
No runtime file, manager reload or signal was applied. All remote probes finished.

v11/control_maintenance.py prepares the exact proposal and a bounded metadata-only
owner inventory. Eight new / 24 related tests passed in 0.59 s. An initial test
expected a size-change error but its replacement string had the same size; the
source-metadata change was correctly rejected, and the assertion was corrected.
The reattached final-reviewed specification was hash checked again before
publication and matches the authority above. No old draft or summary substituted.
The helper was staged mode 0400 outside V10 and hash checked, not executed:
/home/alphaadmin/alpha-v11-preservation-prep-20260924/control_maintenance.py,
SHA-256 50a5067f9d58405ae62c9ef84ecfdefd69fb3cfbf77f8b1008a12aa9e34cd6b9.
The exact owner inventory command, private destination, runtime guard bytes/hash,
limited guarantee, recovery steps and final pending checklist are in
V11_CONTROL_SUSPENSION_PLAN.md. Current backup and full recovery acceptance remain
pending protected access. No repeated privileged-access workaround was attempted.

v11/maker_research.py implements non-executing quote proposals through protected
maker/source/event admission and common paper-account hypothetical risk. Existing
inventory and ambiguous intents retain their risk; proposed research quotes do
not reserve cash, submit orders or create fills. Linked sampled eligibility,
gap/reconnect resets, irreversible research retirement and first-received horizon
markouts are durable and replayable. Public prints cannot become our fills.

32 new / 151 related tests passed in 8.29 s. Initial 28-test and later 150-related
runs passed. Review then found that failed risk revalidation could advance the
sampled eligibility span; moving that risk check before advancement and adding a
negative regression case produced the final 151-pass result. Source/account CAS,
existing inventory, uncertain-order reservations, protected admission changes,
causal marks and no ledger mutations are covered. All evidence is synthetic
off-host testing, not independent review or empirical/live acceptance.

R34/R35 remain PARTIAL. Latest full regression is still 2,894 pass at f69e318;
unchanged components were not subjected to another full run. Cumulative distinct
new tests across recorded runs: 641. Fixed completed packages: 1/50 = 2%.

- No V11 live/paper/control/challenger ledger is shared or migrated.
- Causal dataset, immutable bundle and bounded learner infrastructure implemented; only synthetic test challengers, no actual V10 fit, accepted champion or model promotion.
- Public CWOP access/coverage verified; no actual PWS lead, executable-exit or maker-fill validation.
- No live-eligible strategy/station; no funding/account creation/transfer/order.
- No executor mask change or real-money activation. No arbitrary paper waiting
  period is imposed; mandatory technical/evidence gates remain.

Next concrete engineering action: connect the bounded maker baseline and paper
cancellation/telemetry to runtime scheduling and trustworthy liveness/clock inputs. Research
proposals, sampled eligibility and markout/common-risk integration are now locally
verified; do not restart those modules or fabricate fills. Continue rewards,
guardian/clock/operator integration and ALL remaining matrix/master requirements.

Do not restart completed strategy/basket/exits/forensic work. Protected scoped
models, PWS observation/payout joins, received-release EVENT checks, joint basket
valuation/admission/per-leg reconciliation, discovery/funnels/queue outputs,
inventory-bound active exits and FIFO attribution are implemented and locally
verified. Result-lag exact-finality evidence remains missing and GATED. Continuous
exit scheduling, capital rotation, emergency permissions, full provider/runtime
integration and external reconciliation remain open. Structural redemption is
not implemented. Exact-source label attestation, empirical calibration/PWS lead,
approved initial champion, learning scheduling/OS isolation, protected authority
commissioning and independent acceptance remain open. Observation, payout and
executable-exit targets stay separate. No partial foundation counts as full
strategy or runtime acceptance. Preserve and publish nonsecret milestones.

Routine inspection/queries/analysis are authorized and remain the integrator's
work. The completed snapshot needs no further owner action. If owner-only journal
inspection or commissioning becomes necessary, prepare one precise reviewable
step; continue safe independent engineering meanwhile. Never ask for secrets in
chat or weaken access controls. Runtime acceptance and funding readiness remain
blocked; the full implementation task is still in progress.

Current continuation boundary: health freshness FAIL, financial executor
MASKED/INACTIVE, no suspension/restart/deployment/financial action authorized or
performed. Explicit owner approval is required only for the prepared V10
suspension/recovery decisions; independent off-host maker work is authorized.
Latest full suite: 2,894 pass; subsequent preparation/preservation suite: 24 pass;
latest maker context/strategy/model/probability/account/event/evidence suite: 266 pass.
Earlier preservation 41 and microstructure/evidence/valuation/queue 113 passed.
No local test or publication
operation is intentionally left running. The checkpoint/matrix retain the full
specification. Fixed completion remains 1/50 = 2%; R32, R34 and R35 are PARTIAL.
No independent reviewer or empirical strategy-eligibility pass is claimed.

### Prior off-host milestone: protected maker context

v11/maker_context.py and MakerResearch.context join existing research quotes to
protected final-payout inference, exact token/side distances, shared account risk,
current book/event/operator state and optional received release expectations.
Same-day context requires the separately reviewed conditioned-payout capability
and exact observation/remaining-day coverage; next-official-observation prediction
cannot substitute. Uncalibrated payout intervals remain vacuous. Expected release
times never become confirmed observations or no-event-risk claims. No trading EV,
fill probability, queue position, executable exit, ledger mutation or authority is
inferred from these diagnostics. Actual provider/notice adapters remain pending.

37 new tests / 266 related tests passed in 19.68 s. Initial focused result was
28 failures / two passes: duplicate start references plus a test keyword-override
error prevented evaluation. After those fixes, four fixture/assertion mismatches
remained (target constant, authority helper and stale second-scope review pins).
Correcting them produced 30 passes in 5.92 s. Review then corrected raw-order
midpoint selection and incomplete context expiry, and added seven cases covering
those boundaries, operator races and interrupted/replayed work. The final related
run above passed completely; no open test failure remains. Tests use synthetic
protected reviews and data, never independent acceptance or live evidence.

The only modified existing implementation file is the maker context entry point;
the context module and its tests are additive. Preservation helper bytes remain
unchanged after the denied owner-inventory execution attempt. V10 was not modified,
no guard was installed and no service signal, restart, executor change or deployment
occurred. Current inventory hash is unavailable because execution was rejected.
No full regression was repeated: latest remains 2,894 pass / four existing warnings
at f69e318. Cumulative distinct new tests now 678; full completed packages remain
1/50 = 2%. R34/R35 remain PARTIAL, with baseline cancellation/telemetry, runtime and
empirical acceptance still open. Continue the full specification after those
interfaces, including rewards, guardian, clock and operator integration.

The implementation above is published at b03d6df5cbc9d82a2a28797ee59ca332f9a5dfcb,
tree 8be9e129241b52f02d6eaf563e8c1647b94a612b. Local/public tree comparison passed;
the publication/fetch completed and the worktree was clean before this following
checkpoint update. No running inventory, test or implementation operation remains.
Exact final verification command (off-host, exit 0):

```sh
/workspace/scratch/38af7099c566/alpha-v11-venv-20260924/bin/python -m pytest -q tests/test_v11_maker_context.py tests/test_v11_maker_research.py tests/test_v11_microstructure.py tests/test_v11_strategy_pipeline.py tests/test_v11_strategy_admission.py tests/test_v11_model_artifacts.py tests/test_v11_probability.py tests/test_v11_paper_coordinator.py tests/test_v11_event_risk.py tests/test_v11_evidence_foundation.py --tb=short
```

Result: 266 passed in 19.68 s. A separate collection-only check confirmed 37 new
context cases without rerunning passed tests. SUSPENSION_NOT_READY; inventory hash
unavailable; exact owner command denied by the connector. Source/health findings
remain the recorded observations, not fresh health passes. The next off-host
implementation is cancellation/telemetry and runtime interfaces; the next
owner-only action is the same approved metadata inventory in the authenticated
owner shell, with only its redacted result returned. No V10 configuration/signal,
restart, executor, funding or V11 deployment permission is implied.

### Latest continuation: owner inventory handoff and cancellation integration

Recovered checkpoint 6cacd26d86a6d5b1cfc6c631f3c056da6ce4e78e, tree
7227fee538ebacf68c317a7013156e45211b5653 on the same development branch. Worktree
was clean, no local project operation or active remote session was found, and no
work was reset, cleaned or discarded. The full final-reviewed master remains the
authority; this inventory handoff does not narrow the implementation scope.

The owner completed the metadata inventory at
/var/tmp/alpha-v10-presuspension-inventory-20260924-01 and reports SHA-256
b161426cff5b39b262e72a6e8142982dd29fa8a0bf29c9965232edf1ff364bd3,
status METADATA_INVENTORY_ONLY and all four effect/backup flags false. This
supersedes the prior instruction to perform that capture: DO NOT repeat it.

Connected inspection independently verified the directory is root:root 0700 and
not a symlink. Manifest read returned PROTECTED_INVENTORY_ACCESS_DENIED; the
process completed, exit 0, 0.08 s. Owner-reported hash is recorded, not claimed as
independently recomputed. No mismatch has been observed. Current full preservation,
configuration/dependency closure, recovery acceptance and runtime guard remain
unpassed; SUSPENSION_NOT_READY. V10 health/memory findings remain open and were
not reclassified as healthy from a metadata-only capture. No host workload was
added beyond bounded preparation/read-only checks and staging the verifier.

v11/control_inventory_verify.py verifies only saved inventory/COMPLETE bytes and
metadata, pins this exact hash, checks memory/size/time bounds and returns redacted
coverage. It does not open the live DB/WAL/SHM or private configuration. Twenty new
tests / 44 related preservation checks passed in 0.36 s. Prepared helper was
staged mode 0400 outside V10 at
/home/alphaadmin/alpha-v11-preservation-prep-20260924/control_inventory_verify.py;
remote SHA-256 3262f8c398c36594a542c26d3719c55d249979c9297052c1cda94caed0fb0f87
matched. Staging exited 0 in 0.09 s; the helper was not executed. The exact next
owner action is the inventory-only verification command in
V11_CONTROL_SUSPENSION_PLAN.md. No repeated denied sudo route, permission change,
inventory recapture, temporary guard, manager operation or signal was attempted.

v11/paper_cancellation.py joins existing scoped operator/EVENT cancellation
requests to a bounded dispatcher and common-paper-account telemetry. A cycle
issues at most 16 local cancellation requests, with durable per-intent IDs,
restart recovery and atomic telemetry. PaperCoordinator.transition adds an
optional transaction-bound cancellation identity check; it cannot authorize an
opening transition. Reservations persist until account reconciliation proves
terminal state. Late fills/acks, sticky faults, terminal regression and existing
inventory remain visible; no public print or attempted request becomes a fill or
confirmed cancellation. Local elapsed time is explicitly not exchange latency.

29 new cancellation tests / 223 related account/event/evidence/maker/basket/exit
tests passed in 36.50 s. The initial 43 cancellation/account checks passed in
3.87 s; added cases test bounded retry, late-fill terminal regression, bounds and
plan/account races. There were no failed test runs in this continuation. All
tests are synthetic off-host checks, including explicit downstream economic-
admission fixtures; no empirical, financial or independent-review acceptance.
R34/R37/R39 remain PARTIAL. The module shares the paper process and is not a
commissioned independent guardian or a live transport/credential boundary.

Latest full regression remains 2,894 passed / four existing warnings at f69e318;
it was not repeated. Total distinct new tests across recorded runs: 727. Completed
full packages remain 1/50 = 2%. Next off-host work: runtime scheduling and trusted
source/heartbeat/clock inputs for maker/cancellation, followed by reward economics,
independent guardian commissioning and ALL remaining matrix/master requirements.
Next owner-only step: the prepared redacted inventory verifier, not guard
installation or suspension. No deployment, funding, real order, executor change,
V10 restart/stop/signal or real-money activation is authorized or performed.

The inventory-verifier/cancellation implementation is published at
dc40f637d6286e3ad846410de9a45d5a55f6b6ee, tree
79d00fc9ea9b2c926ab4f5e3ae9e146d93dcf594. Local/public tree equality passed and
the worktree was clean before this following documentation-only checkpoint.
Publication initially encountered truncated local tool output and then a response-
shape parsing error. The already-created local commit and remote tree were
preserved; publication resumed from those objects without a duplicate commit,
reset, clean, overwrite or repeated test run. Fetch/alignment completed, exit 0;
no publication or test operation remains running. No source/test bytes changed
after the passing related runs.

Exact final test commands (off-host, both exit 0):

```sh
/workspace/scratch/38af7099c566/alpha-v11-venv-20260924/bin/python -m pytest -q tests/test_v11_control_inventory_verify.py tests/test_v11_control_maintenance.py tests/test_v11_control_preservation.py --tb=short
/workspace/scratch/38af7099c566/alpha-v11-venv-20260924/bin/python -m pytest -q tests/test_v11_paper_cancellation.py tests/test_v11_paper_coordinator.py tests/test_v11_event_risk.py tests/test_v11_evidence_foundation.py tests/test_v11_maker_research.py tests/test_v11_maker_context.py tests/test_v11_basket_coordinator.py tests/test_v11_position_management.py --tb=short
```

Results: 44 passed in 0.36 s (20 new verifier cases); 223 passed in 36.50 s
(29 new cancellation cases). The first suite cannot attest the protected real
inventory. The second is synthetic PAPER integration, not production guardian or
exchange acceptance. Full completion remains 1/50; NOT_READY_TO_FUND and
SUSPENSION_NOT_READY. Next work and owner-only step remain as stated above.

### Runtime, source-health and clock integration milestone

Off-host modules runtime_health.py and paper_runtime.py now join bounded ticks,
periodic fresh census, causal queue work, real protected temperature evaluation,
common account reservations, cancellation telemetry and maker retirement. Health
leases are required atomically at account/maker opening boundaries once installed.
A restricted safety audit preserves raw timestamps and cancel-only/retire-only
state during clock regression; ordinary evidence/opening chronology is unchanged.
No V10 maintenance operation was resumed. Current inventory remains OWNER-REPORTED
/ INDEPENDENT VERIFICATION PENDING; the verifier action remains deferred.

The actual local read-only clock probe returned SYNC_STATUS_UNAVAILABLE. Tests
use explicitly synthetic host responses, not a deployment health pass. Full real
temperature-pipeline integration correctly rejects the uncalibrated economic
proposal after census/inference/valuation; no synthetic positive-alpha claim.
45 new tests were added. Final runtime/health/maker/cancellation/queue run: 115
passed in 12.42 s. Earlier affected regression: 117 passed / 2 CAS fixture-hook
failures, corrected at the new safety_audit boundary; next run 142 passed in
8.55 s. Initial runtime run 16 passed / 2 fixture assertion/target-field failures;
both corrected. No open failure remains. Latest full regression remains 2,894
at f69e318; a broader regression is due after source/runtime delivery integration.
Cumulative distinct new tests: 772. Completed full packages remain 1/50 (2%).
R33/R37/R38/R39 remain PARTIAL, with live adapters and independent acceptance open.
Next implementation: bounded durable delivery of archived source receipts and
collector scheduling into this runtime, then ALL remaining master requirements.

Runtime milestone published at 8925ae17f5fc4270089b4e3973bea6a47f5c611c, tree
41ac694fd5468fbc2621baddc950f590ff162c45. Public/local tree equality passed;
fetch/alignment completed and the worktree was clean before source-delivery work.

### Source collection, receipt delivery and restart milestone

Added v11/runtime_feed.py and v11/observation_pump.py. The feed drains archived
OFFICIAL/PWS/MODEL/BOOK/TRADE and source schedules with bounded round-robin cursors,
idempotent queue publication and durable pending release expectations. Raw or
historically unavailable data, failed QC and synthetic account fills cannot
become fresh public-source observations. Capacity and delivery failures retain
retryable receipts; one provider failure does not erase another's successes.

The pump joins existing bounded public collectors to pre/post-collection safety
checks and PaperRuntime. An interrupted collection is not blindly repeated.
Exact completed-cycle replay returns history without renewing health. New process
generations reconcile existing SUBMITTING intents to ambiguity and retain their
reservations. Boot-read failure and the entire archive's raw timestamp high-water
mark gate openings. Superseded EVENT cancellation requests remain deliverable for
older in-scope intents; they cannot cancel a later intent via the old event record.

Synthetic integration demonstrates collector -> normalization -> durable feed ->
queue/runtime, source-scoped partial failure, cooldown/restart behavior, and common
account reservation. The positive reservation fixture is explicitly synthetic
and does not attest economics; the real uncalibrated temperature pipeline still
rejects its proposal. No live source/clock/strategy eligibility is inferred.

Verification: 65 related tests passed in 5.76 s; the separately added account
reservation and durable EVENT regressions passed in 0.75 s and 0.57 s. The full
repository suite then passed **3,130 tests, four existing FastAPI deprecation
warnings, 185.10 s** (exit 0). Wrapper wall time 185.824 s, user/system CPU
126.274/52.944 s, peak child RSS 154,848 KiB; all off-host. A first timing-wrapper
attempt exited 127 because /usr/bin/time was absent and ran no tests; the successful
run used Python's stdlib resource/timing wrapper. No duplicate regression remains
running. No source/test change followed the successful full run.

22 additional tests in this milestone; cumulative distinct new cases 794 (baseline
2,336 + 794 = 3,130). Full accepted packages remain **1/50 (2%)**, not increased by
component integrations. R33/R37/R38/R39/R45 remain PARTIAL. Remaining gates include
actual provider/request/census adapters, calibrated models and true labels,
independent guardian/review, empirical paper acceptance and isolated deployment.
Next off-host implementation: maker reward/rebate qualification and accounting,
then remaining master requirements. V10's stale-cycle/memory-pressure finding and
resource/isolation gates remain OPEN. Maintenance is DEFERRED, with inventory
OWNER-REPORTED / INDEPENDENT VERIFICATION PENDING. No new owner action is required
for this independent work. No host workload, service action, deployment or money
movement occurred. NOT_READY_TO_FUND.

Source-delivery milestone published at fb91b25ef2761a626877164c24326105a6fa575c,
tree 0054e370f732934cc3255194399a478d0803f1df. Publication completed, exit 0.
The next reward/rebate milestone is in progress off-host; no owner action required.

### Maker reward/rebate runtime milestone

Added reward_rules.py, maker_rewards.py and V11_REWARDS.md. Exact-market anonymous
public settings feed receipt-bound conditional scoring and a bounded runtime
watcher. Rule changes, stale review and invalid inputs retire tracked research
quotes without any exchange action or cash release. Unknown execution economics
cannot become a positive reward-pursuit decision. Common-account trading P&L,
recorded synthetic income and combined synthetic result remain separate; estimates
never increase available cash. Duplicate transfer proofs and cross-asset addition
are rejected. Actual independently verified income/discrepancy stays UNKNOWN.

43 new reward cases; first affected checks 60 passed in 4.72 s. Broader
runtime/maker/collector checks passed 191 tests in 19.90 s. The endpoint was then
aligned with the current official exact-ID path; final affected verification is
recorded below. Current full regression remains 3,130 passing at fb91b25, not a
claim that the later additive code was in that run. Cumulative distinct new tests
837. No failed tests observed in this reward milestone. All verification is
synthetic/off-host, not empirical or independent acceptance. R36 remains PARTIAL;
full accepted packages remain 1/50. Source/clock/guardian/empirical deployment gates
and the V10 health/resource finding remain open. No V10 maintenance was resumed.
Next implementation: performance lab and durable daily/weekly audit integration,
then all remaining matrix requirements. No funding or deployment authority.

Final reward/runtime/maker/collector verification: **191 passed in 22.23 s**,
exit 0, after the exact-ID endpoint correction. No test operation remains running.
No source/test bytes changed after this passing run.


Reward implementation published at a993ebe1f76cf95b47938a1a5a5e43570ce82e8e,
tree 2db88e02db21714cb01410fab9536146159cb3bc; fetch/alignment completed, exit 0.

### Performance lab and scheduled audit milestone

Added performance.py and audit_reports.py, integrated default daily/weekly request
scheduling in PaperRuntime, and added bounded pinned read views to the V11 archive.
Performance joins the actual common PAPER account and conserves entry attribution;
partial fills/realizations, closed entries and final settlement remain distinct.
Reports separate PAPER/LIVE, hypothetical/validated capital and maker income,
expose lineage/accounting gaps, and do not infer EV capture across different horizons.

The reporter runs outside the cancellation scheduler, using its own lock and
bounded resumable chunks. Sequence/head pins exclude later appends, report identity
survives interruption between publication and cursor commit, and missing/partial
coverage stays explicit. Runtime scheduling -> separate worker -> durable daily
report is demonstrated. Holding the report lock does not prevent runtime safety
retirement. There is no external-message transport or deployed reporter process.

27 new reporting cases. Initial run: 25 passed / one fixture error from a nonexistent
PaperAccountPolicy version field; fixed using an account-identity change. One further
scoped-station case was added. Final related report/account/runtime/reward/evidence
run: **151 passed in 22.07 s**, exit 0. Prior default-runtime verification: 28 passed
in 4.42 s. Full regression was required because shared archive reads and default
runtime scheduling changed; its completed result is recorded below.
Cumulative new cases 864; fully completed implementation packages remain 1/50. R40/R41 remain PARTIAL,
with all unavailable empirical/protected/production metrics explicit. All work is
off-host. V10 maintenance remains DEFERRED and the recorded runtime-health/resource
finding remains OPEN. No inventory verifier, service action, deployment or funding.
Next implementation after verification/publication: exact public book normalization
and fresh census/source integration, then remaining full-spec requirements.


Reporting full regression completed PASS: **3,200 passed, four existing FastAPI
deprecation warnings, 211.18 s**, exit 0. Wrapper elapsed 212.630 s; user/system CPU
148.477/56.942 s; peak child RSS 153,452 KiB. No test operation remains running.
No code/test bytes changed after the passing run. Public/local publication of
this nonsecret milestone follows; next action remains public-book normalization
and census integration. Complete implementation packages (local verification)
remain 1/50; formal runtime, empirical, independent-review and funding gates remain
unpassed. V10 state and deferred inventory verification are unchanged.


### Public-book and fresh-census integration (verification in progress)

Recovered reporting publication b75cc1c65a3ec7b4fa39c876fb999ad9bee023b2, tree
12e939b4d9e7feb73dcbbd6cc31602649c0ea76f. No rollback, cleanup or duplicate operation.
Added book_inputs.py and census_worker.py, with exact contract/depth/time binding,
original receipt preservation, quota-aware bounded batches and a separate census
worker. Existing weather normalization now preserves original receipt and actual
provider observation time. Queue raw-lineage and per-event loss-generation fences
prevent delayed normalization or a second gap from clearing a new census need.
Cancellation remains available while the collector awaits a response. Coverage
then feeds the existing real protected temperature pipeline; its uncalibrated
proposal is rejected without creating an intent/fill. MODEL/PWS-QC census adapters
remain explicitly unavailable, rather than substituting old evidence.

Initial tests: 40 passed / 2.75 s. Wider command first exited 4 due to a nonexistent
test filename and ran no tests. Corrected wider run: 227 passed / one integration
failure (weather normalization omitted actual observation time). Fixed that defect;
228 passed in 10.42 s. Final coverage/completion race guard and regression: 14 passed
in 3.40 s. 45 distinct new cases; cumulative 909. Full suite is IN PROGRESS in
local exec session 58464; resume that session rather than launch a duplicate. No
source/test edits have been made while it runs. Public/local HEAD remains b75cc1c
until this milestone is verified and published. Current uncommitted work is intended.

Market discovery was inspected: legacy discovery exists, but a bounded resumable
V11 discovery/semantic-census adapter is not yet implemented. Next action after this
regression and publication: connect public market discovery to archived per-event
rule/semantic evidence and existing quarantine, then remaining runtime integrations.
No grammar broadening or acceptance waiver is planned. Completed packages remain
1/50 (local implementation verification); all formal acceptance gates remain open.
V10 maintenance is DEFERRED; stale cycles and severe memory pressure are recorded
findings, not rechecked as fresh observations. Host-resource/isolation and private
inventory independent verification remain open. No host workload, deployment,
service action, executor change or funding is authorized or performed.


Public-book/census full regression completed PASS: **3,245 passed, four existing
FastAPI warnings, 179.01 s**, exit 0. Wrapper elapsed 179.685 s, user/system CPU
125.509/47.861 s, peak child RSS 154,880 KiB. Session 58464 completed; no duplicate
run was started after the continuation request. No source/test bytes changed during
or after this full run. All nonsecret implementation and evidence documentation
are now ready for publication. 1/50 implementation packages complete; formal
acceptance unchanged. Next concrete work: bounded resumable market discovery,
semantic rejection census and existing rule/quarantine integration.


### Resumable discovery and rule-cancellation integration

Public-book/census implementation published at aa541aca587016da1143a63ab5819aed3aee71fa,
tree 0af346b26b293e9ee8a5779eb2630d670850079d; alignment completed exit 0. Recovery
confirmed the intended discovery changes and no running operation, preserving all
newer work beyond the user's b75cc1c reference.

Added discovery.py and docs/V11_MARKET_DISCOVERY.md. Bounded keyset collection,
resumable page/event cursors, immutable raw receipts, semantic denominators and
rejection/template census now join existing station metadata and rule quarantine.
Unsupported/closed changes preserve prior valid semantics and request managed
PAPER cancellation. Original receipt/sequence lineage blocks older extracted pages
from replacing newer rule evidence. Current-universe verification remains false;
no grammar expansion, dynamic route registration or strategy approval is inferred.
Rule quarantine reaches account cancellation and maker retirement. Review found
minimum-budget health no-ops could starve other safety intake: a dedicated bounded
health slot and rotating nonhealth channels correct this. Maker retirement now
precedes optional queue work, including a census lock held by another worker.

New discovery tests initially ran 21 passed / one fixture failure: rule fingerprint
was bound to original bytes while a fixture added active/closed fields. The fixture
was corrected, preserving raw binding validation. Expanded related run passed 203
in 19.63 s; receipt-order additions passed 206 in 30.63 s. Two further starvation/
queue-lock cases are under verification. There are 30 distinct new cases in this
milestone (cumulative 939), subject to the final completed results below. Prior
full regression remains 3245 at aa541aca; it does not cover these later changes.

All work and tests are off-host and synthetic. Fully completed implementation
packages remain 1/50; no independent, empirical or forward acceptance is claimed.
V10 maintenance is DEFERRED; stale-cycle/memory-pressure findings and host resource/
isolation requirements remain open. The inventory hash remains OWNER-REPORTED /
INDEPENDENT VERIFICATION PENDING. No V10 operation or new workload, guard, service
change, executor change, deployment, funding or real-money action occurred.
Next: finish the warranted shared-rule/runtime regression, publish this milestone,
then compose bounded off-host candidate workers with durable recovery and safety
scheduling; continue remaining full-spec requirements.

Scheduler tests initially ran 49 passed / one fixture failure: the cancellation
fixture had no prior observed rule, so invalidate correctly returned no invented
binding. Added its actual raw-bound original rule. Final shared discovery/rules/
cancellation/runtime/queue/census/collection/admission/maker suite: **208 passed in
14.64 s**, exit 0. The two additional safety cases pass. Full regression is running
in local exec session 67073 (600-second subprocess ceiling); resume that operation
rather than duplicate it. No source/test edits while it runs. A read-only shell
call briefly hit an exec-server transport disconnection; one identical read retry
succeeded, with no mutation or duplicate test invocation.

While the discovery regression runs against unchanged source/tests, the next
candidate-runner implementation draft is staged outside the repository at
/workspace/scratch/38af7099c566/v11-candidate-runner-next.py. It is unfinished and
untested, not part of that regression or any accepted/public milestone. Preserve
it on interruption; integrate and test only after saving the discovery milestone.


Discovery/shared-runtime full regression completed **PASS: 3,275 passed, four
existing FastAPI deprecation warnings, 188.62 s**, exit 0. Wrapper elapsed 189.337 s;
user/system CPU 133.352/49.071 s; peak child RSS 152,456 KiB. Session 67073 finished.
No source/test bytes changed during the run; the untested next-runner draft remained
outside the repository. 30 new cases, cumulative 939. No open test failures.
Publication of the nonsecret discovery milestone follows. Fully completed packages
remain 1/50 (implementation/local verification), with formal acceptance unpassed.
Next implementation is the bounded candidate runner; V10 and all authority gates
remain unchanged.


### Bounded candidate-runner implementation (in progress)

Discovery publication: a63365901e80d87d1d9d15c6d5534ef5eeedb593, tree
d774fa9c7533f330d6d084538c355220df1e2b32. Fetch/alignment completed exit 0, session
95247; worktree was clean. The runner draft is now integrated as
polymarket_scanner/v11/candidate_runner.py with tests/test_v11_candidate_runner.py.
The outside draft is historical; preserve the newer repository edits on recovery.

The finite runner joins existing paper safety ticks, one shared public-collection
slot, resumable discovery/census, optional observation pump and bounded audit chunks.
Explicit job/tick/time limits, durable command reservation, exact interrupted-command
recovery, completed-run non-renewal and coroutine drainage are implemented, under
test. Only the runtime's actual configured worker emits its own heartbeat. This
is cooperative off-host integration, not a hard real-time or isolated guardian claim.
Initial runner suite is running in local exec session 84588; do not duplicate it.
No source, clock, calibration, deployment or financial acceptance is inferred.
V10 maintenance remains deferred; host-resource/isolation and inventory independent
verification remain open. Next: resolve concrete integration failures, add the real
protected temperature-pipeline runner check and save the verified milestone.

Initial candidate run: 14 passed / one test expectation failure. The recovery test
allowed three jobs and correctly continued to a separate Gamma discovery request;
its one-command recovery assertion was corrected to use a one-job bound. Runner/
protected-pipeline/pump/audit/evidence checks then passed **64 in 12.09 s**. The real
protected temperature pipeline remains uncalibrated and rejects entry without a
paper intent/fill. No economic qualification was stubbed in that integration.

Further integrated review corrected two scheduling gaps: a busy census queue now
returns explicit event-work deferral with all completed safety telemetry retained;
maker safety rotates over observing quotes, preserving retired history without
letting it or an earlier healthy quote starve later checks. Restart retains that
cursor. Final candidate/runtime/pipeline/maker/cancellation/pump/audit/health/census
suite: **131 passed in 23.50 s**, exit 0. 18 distinct new cases in this milestone,
cumulative 957. No test is still running. A full regression is required next because
this operating-candidate milestone also changes shared runtime scheduling.
Completed implementation packages remain 1/50; formal/empirical acceptance remains
open. No alpha-dev workload or service action was performed.

Full candidate regression is running in local exec session 83403 with a 600-second
subprocess ceiling. Source/tests remain unchanged during the run. Resume it rather
than launch a duplicate. Existing a633659 publication remains the latest verified
Git milestone until the candidate result is recorded and published.

GitHub CI for the published discovery commit a633659 completed SUCCESS: workflow
`tests`, run 36030126379. This is additional automated regression evidence, not
independent release review. The candidate regression remains in session 83403;
no candidate source/test edits are being made during that run.

Candidate/shared-runtime full regression is running in local exec session 83403,
with a 600-second subprocess ceiling. Resume it rather than start a duplicate.
No source/test edits while it runs. Current last published HEAD remains a633659;
the runner and scheduling edits are intended uncommitted work, not deployed code.

Full-suite session 83403 became unavailable (`Unknown process id`) before a result
was received. It is UNVERIFIED, not PASS and not a known test failure. No source/
test bytes changed. Read-only process inspection found no visible pytest process;
the original 600-second ceiling plus margin elapsed before a single retry. The
retry started at epoch 1790269848.461895 in local session 1885. Its log and result
are retained outside Git in v11-test-evidence/candidate-full-20260924-retry-01.log
and .json under /workspace/scratch/38af7099c566. The test process inherits an
exclusive full-regression file lock and a 600-second timeout. If the session is
lost, inspect those files/lock before retrying; do not duplicate a locked run.
Latest completed verification remains 131 related passes / 23.50 s and the prior
3275 full passes at a633659. The obsolete unparameterized microstructure entry in
pytest's historical failure cache does not identify a failure in this unavailable
run; current tests use separate YES/NO parameterized cases. No current failed
assertion was received. V10 and all authority boundaries remain unchanged.

Next independent integration inspected: the common coordinator already accepts
BasketProposal, but the runtime has only a temperature request adapter. A draft
RelativeValueEventAdapter plus bounded MultiStrategyEventAdapter is staged outside
Git at /workspace/scratch/38af7099c566/v11-strategy-runtime-next.py while the frozen
candidate regression runs. It is untested, not part of this run. It will reuse
whole-event valuation and exact per-candidate queue links, keep missing lanes gated,
and submit qualified baskets only through the existing common coordinator after
queue completion. Preserve the draft on interruption; do not infer acceptance.


Candidate/shared-runtime full regression **PASS: 3,293 passed, four existing
FastAPI warnings, 212.37 s**, exit 0. Wrapper elapsed 214.045 s; user/system CPU
144.331/62.570 s; peak child RSS 157,352 KiB. Session 1885 completed; retained log
and JSON both confirm completion. The unavailable earlier session remains
unverified; it is not counted separately. No source/test bytes changed during the
verified run. 18 new cases, cumulative 957; no open assertion failure remains.

Publication follows. Fully completed implementation packages remain 1/50; all
formal runtime/empirical/independent review gates remain open. Next concrete
implementation: integrate and test the relative-value/structural runtime adapter
and bounded multi-strategy evaluation through the common account, preserving
exact queue result links and independent lane gating. V10 maintenance remains
deferred, resource/isolation and inventory verification open, and no deployment
or financial authorization is introduced.


### Relative-value and structural strategy runtime (in progress)

Candidate milestone published at 186f4ecc36619b3e96febb84721fee2d9376b566, tree
a7e088483a8ecff056ca5b1d1be5bd8294eda869; fetch/alignment completed exit 0, clean
worktree. The publication helper's in-memory handle had expired; its failed call
made no commit/ref mutation, and the saved helper was reloaded before publishing.

Added strategy_runtime.py and tests/test_v11_strategy_runtime.py from the preserved
outside draft. RelativeValueEventAdapter routes existing whole-event inference
and individual linked candidate outputs into the runtime; the common coordinator
already supported atomic BasketProposal reservations, so that logic is reused.
MultiStrategyEventAdapter keeps a missing lane explicit while continuing unrelated
healthy evaluation. Aggregate result/proposal bounds gate instead of silently
trimming a basket or favoring the first lane. No strategy protection, queue/CAS
fence or common-account risk check is removed. Initial eight-case integration run
is in local session 23430. No V10, deployment or financial action performed.


Relative/structural runtime initial run: five passed / three failed because the
new adapter's identity exceeded the existing 60-character strategy bound; an extra
focused diagnostic reproduced that cause. Corrected the adapter to encode all 256
digest bits in a shorter URL-safe identity, preserving the original bound.
Final related relative-value/basket/runtime/candidate/queue/admission/protected-
pipeline suite: **125 passed in 25.93 s**, exit 0, including eight new cases.

Demonstrated actual protected CROSS_TEMP_RELATIVE_VALUE and STRUCTURAL engines
under the finite runner with HTTP-shaped mock census, exact per-candidate queue
links and three atomic PAPER leg reservations (hypothetical cash). No model economic
qualification stub was used in these cases; complete-set payoff arithmetic uses
explicit synthetic prices/costs, not live alpha evidence. A missing temperature
lane remains separately GATED. Unknown fees or a new stream gap before common
reservation create no partial basket. Namespace, risk, ambiguity, model/metadata
review, rule and clock/source gates remain intact. No fills or financial actions.

Current cumulative distinct new cases: 965. Last full regression remains 3293 at
186f4ecc, not a claim that the later eight adapter cases were included. Targeted
verification is sufficient for this additive adapter change; no unchanged broad
suite was redundantly repeated. No open test failure or running operation remains.
Nonsecret publication follows. 1/50 implementation packages complete; formal
acceptance unpassed. Remaining gates include dynamic source/route/request assembly,
PWS/exit and other strategy runtime joins, exact source/label/calibration evidence,
independent guardian/review and isolated deployment/acceptance. Next concrete work
is the existing PWS/source-release and inventory-exit runtime integration.

V10 remains unchanged with its stale-cycle/severe-memory-pressure finding explicit
and resource/isolation gate OPEN. The private inventory hash b161426cff5b39b262e72a6e8142982dd29fa8a0bf29c9965232edf1ff364bd3
remains OWNER-REPORTED / INDEPENDENT VERIFICATION PENDING. Optional maintenance is
DEFERRED. No guard/systemd/signal/stop/restart/executor change, new host workload,
deployment, funding, account creation, money movement or real order occurred.
**NOT_READY_TO_FUND**.


### Saved continuation handoff

Implementation published and aligned: **e8ed2fdc8ff15878b8245fd6ed826a9fdaf4593d**,
tree **c3914cd8f0445592f0673abce83615f1a932f813**, branch
weather-v11-profitability-upgrade-2026-09-23. Alignment session 18460 exited 0 and
reported a clean worktree. All intended source/test changes are committed. The
outside candidate/strategy drafts are preserved historical copies; the newer Git
implementation supersedes them. No test, publication or worker operation remains
running at this handoff. This final documentation-only checkpoint needs no new
test run; it does not alter the verified code.

Latest exact tests: relative/structural integration **125 passed / 25.93 s**;
runner/shared-runtime full regression **3293 passed / four existing warnings /
212.37 s** at 186f4ecc. Eight later adapter cases were verified in the targeted
suite. Completed implementation packages remain **1/50**, with no new formal
acceptance or live eligibility claimed. Next action: implement bounded runtime
adapters for the existing PWS/source-release and inventory-exit paths, preserving
observation/payout/executable-exit separation and common-account admission, then
continue dynamic source/request assembly and every remaining master requirement.
Host resource/isolation, exact sources/labels/calibration, independent guardian/
review, isolated deployment and formal acceptance remain open. V10 is untouched;
maintenance is deferred. **NOT_READY_TO_FUND**. No owner action is requested for
unrelated independent implementation.

## Reaction and inventory runtime continuation — 2026-09-24

Recovered clean HEAD `3d73473d4bfe8fbb6830fb2d34a437dbf144a772`, tree
`43f45b1fa8509ef7a2960729934a37c8bcad560a`. Retained full-test result PASS and
free regression lock were checked once; no interrupted operation was duplicated.

Implemented `v11/reaction_runtime.py`: bounded PWS paired observation research and
separate payout admission; causal received-release pinning; actual held-inventory
exit evaluation. MultiStrategyEventAdapter accepts these engines, and PaperRuntime
rejects an exit evaluator attached to another coordinator. Existing protected
model/source/semantic/economic/queue/account gates are unchanged. No callback can
turn an observation forecast into a payout, infer a fill, or bypass common risk.

Initial focused run: 12 passed / 5 failed in 4.99 s. Two expected reason names
were corrected; the exit fixture now obtains a fresh strategy admission after
fresh census evidence instead of reusing a stale pin. No gate was weakened.
Focused final: **17 passed in 5.15 s**. Related PWS/lead/release/exit/strategy/
queue/runtime/candidate/basket regression: **175 passed in 57.55 s**. These are
synthetic off-host integration checks, not current market/host/empirical evidence.
Distinct new passing tests total **982** over the recorded 2336 baseline.
No full-suite rerun was needed for this small wiring milestone; the next shared
assembly integration will receive broader regression at its completion.

Publication includes only code, synthetic tests and nonsecret documentation.
The next reporting checkpoint records this implementation's exact public HEAD
and tree after successful publication. V10 and all deferred maintenance artifacts
remain unchanged. Host resource/isolation, protected model/strategy review,
calibration, forward evidence, independent guardian and release gates stay open.

## Current-input assembly full regression — 2026-09-24

Implemented `v11/request_assembly.py`: fixed nonfinancial scope/source selectors,
exact current-token books, current risk/source matching, protected admission pins,
bounded temperature/relative/PWS/release/exit factories and decision expiries.
Factory configuration is included in adapter/runtime recovery identity. Changing
a plan cannot silently replay a prior runtime completion. Actual held queue/census
and source/model/certification/account requirements remain enforced. Exact release
predecessors use a one-row scoped receipt-sequence query, not provider-time sorting
or a full history load. No request factory fetches/re-dates/imputes an input.

Testing found that valid QC captures deliberately have no singular observed_at.
EventRiskEngine now uses as_of minus the oldest contributing sensor age. Bad QC,
raw feeds, missing/negative/future ages remain EVENT gates; reprocessing unchanged
sensors cannot count as fresh recovery. The raw evidence stays unchanged.

Two test-collection syntax errors were corrected before tests ran. The first
executing assembly run had 15 passed / 2 failed in 3.83 s: a test attempted to
mutate a decoded rule copy, and PWS exposed the QC timestamp join above. After
correction, assembly/source-time/event-risk checks: **54 passed in 5.17 s**,
including 17 new factory and nine QC-clock cases.
Full unchanged-input regression: **3344 passed, four existing FastAPI warnings,
203.31 s; exit 0**, wrapper 204.03 s, user 139.345783 s, system 57.727246 s,
peak RSS 156820 KiB. Retained private local log/result:
`/workspace/scratch/38af7099c566/v11-test-evidence/assembly-full-20260924-01.log`
and `.json`. Distinct new tests total **1008** above baseline 2336.
Off-host resource check before the run: 20 GiB cgroup limit, 6.84 GB current use,
eight CPU quota equivalents and 25.72 GB disk free. This is not alpha-dev headroom.

The public-mock finite candidate now reaches protected whole-event inference,
exact queue completion and atomic common-account basket reservation using the
current-input factory. Periodic census reaches a factory-built actual-inventory
exit reservation. PWS/release factories retain separate payout rejection. Risk
measurements and protected custody in these tests remain explicit fixtures, not
live inputs, approved champions or independent acceptance.
Next step: replace supplied risk measurement callbacks with bounded derived inputs
and continue source/route integration; no maintenance dependency has reopened.

## Derived event-risk runtime integration — 2026-09-24

Implemented `v11/risk_inputs.py`. A bounded whole-event adapter measures exact
books through existing microstructure code, applies the protected model bundle
for descriptive model dispersion, uses original model issue age and reads current
common-account downside. Declared contiguous sequences and aligned whole-event
books are necessary for temporal velocity/depth-loss/cross-bucket movement;
REST snapshots do not fabricate these values. Optional unavailable execution
markout/adverse-fill and exact settlement timing remain UNKNOWN, not zero or
inferred from a contract-day boundary. This is not a reviewed first-canary baseline.

RiskAwareEventAdapter feeds those measurements into EventRiskEngine before the
existing factories/economic evaluation. It retains original source/account head
CAS, old measurements across interruption and exact queue/common-account gates.
A finite public-mock candidate now runs this path without supplied risk metrics;
it records useful data and stays nonfinancial/GATED on the explicit unknowns.
No private source, account credential, service or financial endpoint was used.

A future-issue test initially attempted a capture already rejected by the archive;
its assertion now checks that earlier SOURCE_TIME_IN_FUTURE gate. Initial focus:
21 passed / 1 failed in 2.89 s. Final joined risk/source-time/event/assembly/PWS/
release/exit/candidate/microstructure/runtime suite: **220 passed in 43.03 s**.
This adds 11 risk integration and four model-time cases; distinct new passing
cases total **1023** above baseline 2336. The previous full 3344-case result stays
historical; no later full-suite result is implied. Raw captures remain unchanged.
Next implementation is a typed top-level candidate composition; resource/isolation,
raw provider capability, observed execution/settlement inputs, protected review,
actual calibrated champion, independent guardian and acceptance gates stay open.

## Typed candidate assembly — 2026-09-24

Implemented `v11/candidate_assembly.py`: one programmatic constructor wires
explicit typed event/scope/source plans into the existing finite runner. A shared
anonymous collector serves census/discovery/optional observations; common health,
account, queue, derived risk, request factories and audits retain exact identities.
Construction performs no HTTP or archive writes. Existing account, runtime or
queue configuration conflicts fail without replacing state. Real clock status is
used by default; only synthetic tests inject clock/certification/model custody.

Whole-event plan and aggregate proposal bounds are enforced before a run.
PWS keeps separately protected observation/payout scopes. Received-release lanes
resolve actual receipt predecessors. Ordinary exits use actual held inventory.
Unconfigured special PWS/release exit joins fail closed; maker/finality request
assembly, provider collection, approved dynamic routes and acceptance remain open.
Unknown execution/settlement/sequence measurements still gate derived event risk.

Initial builder focus: 9 passed / 1 failed in 2.40 s (test timeout below the
existing minimum); corrected fixture, then 98 related passed in 24.68 s.
Additional lane coverage: 16 passed / 1 failed in 4.56 s (assertion expected a
nonexistent proposal direction field); corrected to the actual immutable
valuation/inventory contract. Final: **105 passed in 23.70 s**, including
**17 new cases**. Distinct new passing cases total **1040** above baseline 2336.
The 3344-case full regression predates 15 risk and 17 builder cases; no newer
full-suite result is implied. No open test failure or test process remains.

Next: bounded maker observation/markout scheduling within this shared candidate.
All implementation and tests remain off-host; no maintenance work reopened.

## Maker telemetry candidate integration — 2026-09-24

Implemented `v11/maker_telemetry.py` and connected it to CandidateRunner and typed
candidate assembly. Maker research, health, scopes and the common account must
match exactly. Runtime recovery now also binds maker policy identity. No quote
creation, transport, public-trade collection, real fill or ledger write is added.

The bounded worker reserves each action before execution and retains its exact
child across interruption. It observes exact same-channel books using existing
microstructure/research engines, retires invalidated quotes locally and rotates
retained history without deleting it. Due markouts precede repeated book samples,
preventing a busy book stream from starving a horizon. Publication waits only for
the specified markout tolerance window to close; this is not a paper waiting or
funding criterion. The first eligible archived book controls the counterfactual.
Unknown fees/absent horizon evidence stay UNKNOWN. All five horizons remain
observable after local quote retirement. Fees supplied here are explicitly
hypothetical declared costs, not attested venue fees or strategy alpha.

First combined run: 18 passed / 11 failed in 6.50 s; the helper omitted the
mandatory MAKER_RESEARCH source scope. Corrected that fixture; 12 focused cases
passed in 5.82 s. Added bounded-stream fairness and retired-horizon coverage.
Final maker/context/runner/assembly/runtime/health/reward integration:
**206 passed in 32.46 s**, including **14 new cases**. Distinct new passing cases
now **1054** above baseline 2336. No open test failure remains. Public HTTP in the
candidate test is mocked; no source/host/execution acceptance is claimed.
Next: factory-built current maker proposals and protected payout context so the
candidate can originate research quotes when all existing gates actually pass.

## Maker proposal and protected-context integration — 2026-09-24

Implemented `v11/maker_runtime.py`: bounded typed maker targets and a current-input
factory reuse held queue claims, exact books, current risk, protected admission
and microstructure. The shared MakerEventAdapter creates only research quotes,
then the existing protected payout-distance context. It publishes event-linked
queue results with no common-account economic proposal, reservation or fill.
Same-day context requires its separately reviewed payout capability and exact
conditioning inputs; observation probability is never substituted for payout.

Typed MakerLane is integrated with the same account/research/telemetry objects.
A plan cannot omit required maker safety/telemetry scope. Source/payout scopes,
configuration identities, actual inventory and current risk evidence remain
mandatory. Duplicate observing tokens cannot renew expiry or multiply exposure.
A partial quote commit survives an interrupted context calculation. Explicitly
configured quote prices and cost reserves are research terms, not a learned maker
policy, verified fee or first-canary execution baseline. Actual payout-context
GATED outcomes remain visible even when a non-executing quote is retained.

Initial new/telemetry/assembly run: 38 passed / 2 failed in 9.87 s. A SELL fixture
crossed the spread before reaching the inventory gate; another tried to claim a
deduplicated unchanged book. Corrected those fixtures to a passive price and a
new received book/current risk state. The assembler now explicitly refuses a
missing claim rather than raising an attribute error. Related run: 171 passed /
1 failed in 32.94 s, due only to a broader expected error name; retained the
actual PENDING_SALES_EXCEED_HELD_INVENTORY rejection. Final focused run:
**9 passed in 3.19 s**. Distinct new passing cases total **1063** over baseline
2336. Full integration regression completed under the existing exclusive lock and
600-second timeout: **3399 passed, four existing warnings, 208.79 s; exit 0**.
Source/test SHA-256 values remained unchanged. Wrapper 209.426 s; user 143.670744 s;
system 58.675137 s; peak RSS 159120 KiB. Retained local result/log:
`/workspace/scratch/38af7099c566/v11-test-evidence/candidate-composition-full-20260924-01.json`
and `.log`. Off-host headroom before the run: 20 GiB memory limit, 6.914 GB current
use, eight CPU quota equivalents, 25.709 GB disk free. This does not establish
alpha-dev headroom. No full regression or other test process remains running.

## Saved implementation handoff — 2026-09-24

Published implementation **306a7ece6ade71e90d436f7c99730d18a8597d3c**, tree
**56977f5bee32381fac7e954abe83cc1c1901020f**, on
`weather-v11-profitability-upgrade-2026-09-23`. GitHub/local tree equality passed;
fetch/alignment session 70687 completed exit 0 with clean status. This subsequent
reporting-only checkpoint changes no source or tests. Resolve this checkpoint's
own exact commit/tree using the commands above. All prior work and history remain.

Completed implementation integrations this continuation: reaction/exit runtime;
current request factories and QC/model clock corrections; derived event risk;
typed shared candidate; retained maker telemetry; current maker quote/context
factories. Final combined regression: **3399 passed, four existing warnings,
208.79 s**, unchanged source/test hashes, peak RSS **159120 KiB**. No test or
publication operation remains running. Formal completion stays **1/50 packages**;
component progress is not empirical, independent or financial acceptance.

Next concrete unfinished source step: connect `samples_from_capture` /
`archive_neighborhood` to the bounded observation/census path, with causal raw
receipt lineage, replay/partial-work handling, metadata quarantine and atomic
source-change rejection. Inspection confirms MADIS XML is already collected and
normalized, but no runtime QC join exists. `CensusWorker._requests` still refuses
required MODEL/PWS adapters; do not mark those dependencies supplied. Preserve
strategy-specific source failure behavior and the current provider cooldowns.
Then integrate original forecast run/issue provenance, exact labels/calibration,
reviewed champion/learning lifecycle and the remaining full-master requirements.
Do not substitute HTTP receipt or generation time for a model's actual issue time.

V10 remains unchanged. Its recorded stale-cycle and memory-pressure findings
remain unresolved; they were not rechecked or waived. Alpha-dev resource/isolation,
protected source/model approval, exact settlement/label inputs, empirical forward
comparison, independent guardian/review and commissioning gates stay open.
Maintenance remains DEFERRED. Inventory hash remains OWNER-REPORTED / INDEPENDENT
VERIFICATION PENDING. No owner maintenance action, deployment, funding, real order
or executor change is requested or performed. **NOT_READY_TO_FUND**.
