# Supplementary engineering estimate

Latest 2026-09-25 continuation: original prepared basket/exit numerical valuation
comparisons now join account replay and existing audits. Shared runtime extraction
passed **43 / 5.00 s**; portfolio/account checks **51 / 5.80 s**, exit 0. Candidate
scheduling and failure integration now pass **102 / 16.66 s**, exit 0; derived
new-risk gates remain intact. Affected/full regression is next; evidence is in
docs/V11_PORTFOLIO_REPLAY_EVIDENCE.md. This strengthens existing C/J credit,
including R04; it does not close real-evidence, independent acceptance or isolated
deployment. **83/200 = 41.5%, rounded approximately 42%; formal 1/50 (2%)**, unchanged.
All 200 milestones still cover the full scope through unfunded READY_TO_FUND.
V10 unchanged/DEFERRED. No financial authority or readiness granted.

This estimate covers the full final-reviewed engineering scope, including real
source evidence, integration, verified deployment and unfunded readiness. It is
separate from the requirements matrix: formal completion is still **1/50 (2%)**.
It is neither elapsed effort nor a prediction of time, profit or trading authority.

The fixed denominator is **200 evidence milestones**: four for each of R00–R49.
Each milestone receives one unit only for the named completed substep below:

- C: a bounded core implementation and its recorded local checks (for R00,
  verified input/source identities; for R01, the actual forensic analysis).
- J: a demonstrated upstream/downstream integration of that core. This credits
  the stated integration slice; remaining adapters and scope expansion remain
  reserved in the unearned milestones and the full matrix.
- E: all required real-input, forward, operational or unfunded evidence for that
  package, including required deployment/isolation evidence. Synthetic tests
  alone cannot earn this milestone.
- A: the entire package passes its original acceptance requirements.

C and J are deliberately limited substeps, not claims that all implementation
or integration in a partial package is finished. R43–R49 include authentication,
isolated deployment, independent acceptance, comparison, learning and unfunded
commissioning. Their unearned units remain in the denominator. Funded canary
activity and activation still require separate approval.

The recovery baseline is commit `7bd4b3b51b5abe28efa3eecff051553b2adaa0d7`.
The checkpoint/matrix give code, test and evidence references for these credits:

| Requirements | Earned | Specific recovered substeps |
|---|---|---|
| R00 | C | Input hashes and frozen source/deployed identities; control health still open |
| R01 | C J E A | Consistent actual snapshot, immutable analysis, forensic/funnel report |
| R02 R03 | C J | Receipt archive and decision/funnel records used by the candidate |
| R04 | C | Pinned causal replay checks; complete engine replay remains open |
| R05 R06 | C J | Maker counterfactual scheduling; partial-success public collection/discovery |
| R07 R08 | C J | Scoped admission and rule quarantine joined to paper safety |
| R09 | C J | Actual AWC adapter wired through mocked public census; exact source/forecast gaps remain |
| R10 | C | Raw MADIS parsing, defensive QC and metadata quarantine; runtime join pending |
| R11 R12 R13 R14 R15 | C | Coherent distributions/bounds, calibration fallback, physical features, causal datasets and bounded learner tests |
| R16 R17 | C J | Protected bundle slots, admission pins and reviewed epoch/rollback integration |
| R18 R19 R20 R21 R22 | C J | Derived event risk, valuation, common account, atomic reservations and scenario joins |
| R23 | C | Versioned correlation ceilings; actual reviewed mappings remain open |
| R24 R25 R26 R27 R28 R29 R30 | C J | Allocation and scoped strategy factories joined to the finite candidate |
| R31 | — | Exact finality source/version evidence absent |
| R32 R33 R34 R35 R36 | C J | Inventory exits, bounded runtime, maker quote/context/telemetry and separate reward reporting |
| R37 | — | Cooperative paper cancellation is insufficient for the required independent guardian |
| R38 R39 R40 R41 | C J | Clock/source leases, safety reductions, performance attribution and scheduled audit worker |
| R42 | C | Durable model demotion overlay; wider drift/lifecycle propagation remains open |
| R43 R44 | — | Auth/entitlement and isolated deployment not verified; maintenance preparation earns no deployment credit |
| R45 | C J | Recorded targeted checks and integrated full off-host regression; independent security/acceptance remain open |
| R46 R47 R48 R49 | — | Forward comparison, learning acceptance, unfunded commissioning and release gates remain open |

Baseline arithmetic: C=42, J=32, E=1, A=1; **76/200**, approximately **38%**.
Round the fraction to the nearest whole percentage point (half rounds upward).
Only newly completed named milestones change the numerator. More tests for an
already credited slice, time spent, maintenance preparation and a session ending
do not change it. A correction must retain the prior score and explain the defect.

## Changes after recovery

PWS runtime milestone: R10 earns J for the demonstrated raw MADIS collection →
bounded defensive QC → health/admission/event routing join, plus required fresh
census recovery. Evidence: `test_v11_pws_runtime.py`, `test_v11_pws_census.py` and
the candidate PWS integration cases; 255 related passes plus four focused checks
after the final policy-consistency change. Real lead/calibration/certification,
full provider acceptance and deployment remain open. New total **77/200**,
approximately **39%**. Formal completion remains **1/50 (2%)**.

Forecast normalization/candidate integration subsequently passed at implementation
`84068f641840574a6fd82f73e53a2a0ea14e944e`; full regression 3442 passed in 229.86 s.
The score remains **77/200, approximately 39%**. R09 already has its named AWC
integration credit; its actual forecast run/access/coverage evidence is still
missing. A research normalization that correctly retains that gate does not
earn E or A. More tests do not increase the estimate. No formal package was
newly accepted.

Run-bound GEFS integration: R11 earns J for the bounded GRIB source → complete
31-member local-day forecast path → archived model input → immutable probability
bundle/inference join. Candidate collection and constituent health/admission
checks are demonstrated by synthetic integration tests (242 related passes in
46.97 s). The named CDF core was already credited; the new credit is its source
join. Actual NOAA access/packing parity, calibrated temporal approximation,
other models and operational acceptance remain unearned. Total **78/200**, still
approximately **39%** after whole-percentage rounding. Formal **1/50 (2%)** is
unchanged. This does not credit E/A, extra tests or elapsed effort.

Forecast learning capture: R14 earns J for the protected whole-event forecast
vector -> exact model feature/decision archive -> explicit exact-label join ->
existing causal dataset integration. Evidence: 17 new learning-capture tests,
114 related passes in 22.55 s. The retained event vector is independent of entry
economics; global universe coverage and independent label truth remain unverified.
Other learning targets, actual calibration, isolated training and learning
acceptance remain open. Total **79/200**, approximately **40%** by the same
whole-percentage rounding. Formal **1/50 (2%)** remains unchanged.

Bounded GEFS rollover subsequently passed 112 related checks (18 new cases).
R09/R11 already hold their source integration credits, so this expansion leaves
**79/200, approximately 40%**, and formal **1/50 (2%)** unchanged. Actual source
availability/packing, calibration, independent and deployment gates stay open.

Fresh multi-step GEFS census, bounded source views and completed-path adoption
passed 241 related checks (21 new cases). R09/R11/R33 already have their named
integration credits. Total remains **79/200, approximately 40%**; formal **1/50
(2%)**. Source access, other providers, real calibration and operational/independent
acceptance remain unearned. No numerator increase follows from more tests.

Final census full regression at `f1752a8157c85ce1e975f64cd80b11e5a6318780`
passed 3566 tests with four existing warnings in 283.92 s, all 806 tracked inputs
unchanged. This verifies the newly connected local code; **79/200, approximately
40%**, and formal **1/50 (2%)** remain unchanged. No operational E/A is credited.

Declared forecast contract and research integration: R15 earns J for the
31-member whole-event capture -> explicit complete-label cohort -> frozen causal
dataset -> bounded learner -> compatible immutable challenger -> numerical
inference parity path. Evidence: 29 new cases, 145 related passes in 29.35 s.
Replay does not refit completed/interrupted attempts; the source evidence and
parent remain unchanged. These are synthetic tests, including labels. Actual
labels, calibration, OS isolation, initial champion and learning acceptance are
still open. Total **80/200, approximately 40%**; the displayed estimate and formal
**1/50 (2%)** are unchanged. This credits the named integration, not more tests.

Read-only learning snapshots and complete normalized-source derivations
subsequently passed 168 related checks, including 15 new cases and a 621-record
synthetic GEFS graph. R14/R15 already have their named integrations. Total stays
**80/200, approximately 40%** and formal **1/50 (2%)**. No actual-label, calibrated,
independent, host/deployment or operational acceptance credit is earned.

The combined forecast-contract/source-provenance full regression passed **3610
tests**, four existing warnings, in 288.26 seconds at implementation `6347e704`.
All 811 tracked inputs remained unchanged. This confirms local integration, not
new E/A evidence: **80/200, approximately 40%**, formal **1/50 (2%)**, unchanged.

The separate finite learner worker subsequently passed 125 related tests with
17 new cases: exact cohort triggers, interval/daily budgets, nonblocking locking,
durable attempt reservation, request/dataset-bound recovery and no duplicate
fits. It uses the already credited R15 integration and earns no new milestone.
Total remains **80/200, approximately 40%**, formal **1/50 (2%)**. Actual label,
calibration, process isolation, initial champion and learning acceptance remain
open. The current six active-work ranges are in the checkpoint; they are not
derived from this percentage and exclude external/owner waiting.

The archived exact-interval/GEFS remaining-path join now reaches protected same-day
inference and conservative economics, with 24 new cases verified. A shared-source
admission guard defect was corrected: identical guards merge and differing reads
gate. R11/R26 already have their integration credits. Total remains **80/200,
approximately 40%**, formal **1/50 (2%)**; actual exact-population coverage,
calibration and operational/independent acceptance remain unearned. The first
remaining milestone now excludes this bounded derivation implementation, but its
45–90 active-hour range remains appropriate to the larger unresolved source scope.

Physical/PWS inference: R13 earns J for raw MADIS/AWC -> QC/physical feature archive
-> immutable parameter/feature contract -> paired observation model -> separately
protected payout/same-day economics. The final new suite passed 26 tests in 2.06 s;
source absence, stale/revised inputs, actual dependency ablation, target separation
and immutable coefficients are demonstrated with synthetic sources/review fixtures.
Production scheduling, feature fitting, actual OOS/calibration and deployment
remain unearned. Total **81/200, approximately 41%** under the unchanged rounding
rule; formal **1/50 (2%)**. This is one named integration credit, not credit for
additional tests or an assertion that all R13 implementation is complete.

Recovered full regression at `47c3b999` passed **3677 tests**, four existing warnings,
in 201.69 seconds. All 817 tracked inputs remained unchanged and match on recovery.
This completed run was recovered rather than repeated. **81/200, approximately
41%**, formal **1/50 (2%)**, unchanged; actual-source, operational and independent
acceptance milestones remain unearned. The stale 40% summary in the matrix header
was corrected to agree with the already recorded R13 credit; no new unit was added.

Bounded current-input preparation now connects archived remaining paths and
physical/PWS paired inputs to the typed finite candidate, with preserved clock,
source, event and conservative economic gates. Final affected verification:
**245 passed / 56.98 s**, including 24 new cases. R09/R11/R13/R26/R33 already have
the applicable integration milestones. **81/200, approximately 41%**, formal
**1/50 (2%)**, unchanged. Source truth, calibrated target models, independent
review, isolated deployment and operational acceptance remain unearned. The six
remaining active-work ranges are retained with this completed preparation join
removed from the source implementation tasks; they exclude external waiting.

Recovered preparation full regression at published tree
`6cd23e57f8ef1765fc8f3767549438f87a330c52`: **3701 passed**, four existing
warnings, 221.56 seconds; all 819 inputs reverified unchanged. No duplicate run
was made. **81/200, approximately 41%**, formal **1/50 (2%)**, unchanged. This
verification adds no actual-source, independent or operational acceptance credit.

Conditioned payout and paired receipt-window observation capture now reach the
finite candidate and read-only exact-label dataset path, preserving conditioning,
original source derivations and separate learning feature records. Related checks
passed **154 / 26.41 s**, followed by **41 / 4.82 s** after the final provenance
checks; 26 new cases. R14/R15/R27 already have their named integration credits.
**81/200, approximately 41%**, formal **1/50 (2%)**, unchanged. Exact real labels,
calibration, target-specific fitting and operational/independent acceptance stay
open. The unchanged six active-work ranges exclude external/owner waiting.

Full target-capture integration at `61a5cc84` passed **3727 tests**, four existing
warnings, **260.57 s**, all 821 tracked inputs unchanged. The code's added joins
are verified locally; no new real or independent acceptance is earned.
**81/200, approximately 41%**, formal **1/50 (2%)**, unchanged.

Explicit same-day-conditioned Gaussian fitting now joins captured exact revisions
and remaining-day coverage to the existing offline job and finite research worker.
Original unconditioned policy hashes/semantics and all promotion/safety boundaries
remain unchanged. **87 related checks passed / 15.31 s**, including 18 new cases;
C/F and high/low candidate inference matches fitting numerically. R14/R15 already
hold these integration credits. **81/200, approximately 41%**, formal **1/50 (2%)**,
unchanged. Actual labels/calibration, physical/observation learning, process/host
isolation and independent acceptance stay open. The six active-work ranges remain
appropriate to that larger scope and exclude owner/external waiting.

Protected lifecycle withdrawal: R42 earns J for existing protected model/station/
strategy failure -> original admission invalidation -> finite PAPER cancellation ->
exact common-account reconciliation and maker retirement -> durable audit join.
Evidence: **24 new cases, 267 related passes / 35.43 s**; both PWS model scopes,
interruption, late fills, preservation of reducing exits, reviewed recovery without
resurrection and clock/identity guards are demonstrated. Fixed total **82/200,
approximately 41%**. Formal completion remains **1/50 (2%)**. Statistical drift
threshold/evidence acceptance, real calibration/lead quality, OS-independent guardian,
protected host and unfunded operational acceptance stay unearned. R37 gets no C/J
credit from the cooperative runtime. No numerator change is attributed to more tests.

Full lifecycle regression at `7dd8a462` passed **3769 tests**, four existing warnings,
**231.74 s**, with all 823 inputs unchanged. This verifies the R42 integration just
credited and the earlier conditioned-learning extension; it adds no E/A milestone.
**82/200, approximately 41%**, formal **1/50 (2%)**, unchanged. The next implementation
is scoped, predeclared rolling degradation measurement from exact captured/labelled
vectors; actual source/calibration, independent, host and unfunded gates stay open.

Scoped drift measurement now joins original admissions, model-bound complete forecast/
conditioned vectors and current exact labels in a read-only bounded snapshot. Policies
are explicit, cohorts grouped by event/city-day and unsupported metrics/attestations
remain visible. **121 related checks / 22.01 s, 27 new cases**. Automatic reviewed
reduction/candidate scheduling remains next. R42 already holds C/J; **82/200,
approximately 41%**, formal **1/50 (2%)**, unchanged. No actual or independent evidence
is inferred. Six remaining active-work ranges still apply; waiting is excluded.

Reviewed drift now connects the finite candidate, original model/capture scope,
predeclared protected policy, durable safety demotion, existing paper withdrawal/
terminal reconciliation and audit outcomes. **156 related passes / 29.77 s**, then
**two final boundary checks / 0.60 s**, 35 new worker cases. Full exact-tree regression
is next. R42 C/J already credited: **82/200, approximately 41%**, formal **1/50 (2%)**,
unchanged. Remaining metrics, meaningful actual evidence, independent review and host/
unfunded acceptance remain unearned. The six active-work ranges exclude external waits.

Full scoped drift/candidate/lifecycle regression at **4bbb8bee** passed **3831 tests**,
four existing warnings, **245.54 s**, with all **827 inputs unchanged**. The complete
manifest/output and recorded targeted results are saved in
`docs/V11_DRIFT_REGRESSION_EVIDENCE.md`. This confirms the extended R42 C/J slice;
no extra credit is earned from tests or work sessions. **82/200, approximately 41%**,
formal **1/50 (2%)**, unchanged. Remaining metric families and actual/independent/
host/unfunded acceptance remain open. Six active-work ranges remain appropriate to
that larger scope and exclude external/owner waiting.

Explicit grouped scalar calibration error now reaches predeclared reviewed drift,
scoped reduction, PAPER withdrawal/reconciliation and audits. Original default scorer
and DriftPolicy digests remain unchanged; no automatic calibration or restoration is
inferred. **117 related passes / 8.85 s, 14 new cases**. Existing integration credits
are not counted again: **82/200, approximately 41%**, formal **1/50 (2%)**, unchanged.
Actual calibration/independent/host/unfunded gates and six remaining hour ranges remain.

Automatic realized-PAPER monitoring now joins new ledger realizations, original entry
scope/model/fill proofs, conserved partial-exit accounting, protected predeclared loss/
drawdown reviews, account CAS/recovery, finite candidate safety and audits. **202 related
passes / 35.32 s, 30 new cases**; no new scoring milestone closes because R40/R42 C/J
already apply. **82/200, approximately 41%**, formal **1/50 (2%)**, unchanged. Realized
loss does not establish mark-to-market risk, live execution, net-EV capture or actual
calibration. Independent, host and unfunded readiness remain open; six hour ranges and
separation of external/owner waiting remain appropriate to the unresolved scope.

Locked full calibration/P&L integration regression at **95b00abc** passed **3875
tests**, four existing warnings, **252.03 s**, all **830 inputs unchanged**.
The manifest/output and targeted evidence are saved in
`docs/V11_QUALITY_REGRESSION_EVIDENCE.md`. No new C/J/E/A milestone closes:
**82/200, approximately 41%**, formal **1/50 (2%)**, unchanged. Actual/independent/
host/unfunded acceptance and the six active-hour ranges remain open; waiting is
excluded. The interrupted save was recovered without duplicate publication.

Horizon-specific maker counterfactual quality now joins original admission/model/
source/depth provenance, complete retained-window selection, fair automatic scope
scheduling, protected reduction, finite candidate retirement and bounded audits.
**276 related passes / 49.78 s**, then final **47 new cases / 7.98 s**. R05/R35/R42
already hold applicable integration credits; **82/200, approximately 41%**, formal
**1/50 (2%)**, unchanged. No actual fill, EV capture, calibration, independent,
host or unfunded acceptance is inferred. A single full regression is next. Six
remaining active-hour ranges remain appropriate to the larger unresolved scope,
with fill-based markout joins still open and external/owner waiting excluded.

Locked full maker-markout integration regression at **444c71fd** passed **3922
tests**, four existing warnings, **264.11 s**, all **833 inputs unchanged**.
Manifest/output and targeted results are in `docs/V11_MARKOUT_REGRESSION_EVIDENCE.md`;
the required `docs/V11_MARKOUT.md` records implemented behavior and remaining fill-
evidence semantics. Verification/documentation add no extra credit: **82/200,
approximately 41%**, formal **1/50 (2%)**, unchanged. Actual/independent/host/unfunded
acceptance and six active-work ranges remain open; external/owner waiting is separate.


Reconciled synthetic PAPER fill quality now joins explicit engine timing/price/cost,
original single-leg/basket/exit attribution, conservative unknown-timing selection,
all five causal depth horizons, reviewed automatic reduction, candidate cancellation
and bounded audits. **338 affected passes / 57.18 s**, **57 targeted / 8.23 s**, plus
**one single-leg case / 0.42 s**. Bad optional telemetry never hides reconciled cash
or units. Existing R05/R35/R40/R42 C/J credits already cover the integration slice;
**82/200, approximately 41%**, formal **1/50 (2%)**, unchanged. This does not validate
actual execution, EV capture, empirical adverse selection, independent review,
host deployment or unfunded acceptance. Full changed-tree regression is next.
Six remaining active-hour ranges remain appropriate to the unresolved source,
proof-delivery/governance/host scope; external waiting and owner actions are separate.


Initial fill integration full regression at **3fa1663c** passed **3980 / four
existing warnings / 267.20 s**, all **839 inputs unchanged**. Review then found a
repeating per-share Decimal incorrectly rejected as a ledger input; the new case
failed once, and the corrected aggregation passed **59 targeted / 10.17 s**.
Full changed-tree verification follows. Evidence: docs/V11_FILL_REGRESSION_EVIDENCE.md.
This is correctness work within existing credit: **82/200, approximately 41%**,
formal **1/50 (2%)**, unchanged. Six active-hour ranges and external gates remain.


Final corrected fill integration full regression at **33d92731** passed
**3981 / four existing warnings / 277.46 s / exit 0**, all **840 inputs unchanged**.
Both full manifests/output and the fractional-cost failure/correction are preserved
in docs/V11_FILL_REGRESSION_EVIDENCE.md. No new scored milestone closes:
**82/200, approximately 41%**, formal **1/50 (2%)**, unchanged. Remaining actual,
independent, host and unfunded gates, six active-hour ranges and separation of
external/owner waiting are unchanged. Next is bounded candidate reconciliation
of archived PAPER fill/terminal receipts; no owner action blocks that code.


Archived PAPER receipt reconciliation now joins the finite candidate priority tick,
existing common-account proof checks, atomic admission/submission fences, resumable
pending/cursor state and audits. The 34 targeted passes (5.12 s) include archive-only
fill input through reviewed monitoring/cancellation, proven terminal release and
daily audit. Malformed/public/foreign separation, interrupted delivery and health
loss remain fail-closed. Applicable R02/R03/R05/R21/R32/R33/R40/R45 C/J slices were
already credited; **82/200, approximately 41%**, formal **1/50 (2%)**, unchanged.
No actual source/calibration, independent acceptance, guardian or deployment gate
closed. The six active-hour ranges remain appropriate to the broader unresolved
scope. Account-change reevaluation, actual/owner evidence and READY_TO_FUND remain
open. Affected/full verification of this new tree is pending at this checkpoint.


The subsequent receipt-to-event join now advances the current census generation,
preserves source-loss findings and invalidates old inventory evaluations. The
candidate can consume an archived BUY fill, evaluate the existing whole-event
exit, reserve a common-account SELL, consume its explicit PAPER fill and reevaluate
remaining inventory with realized-P&L attribution. Final focused 40 / 6.33 s;
preceding saved receipt tree affected 328 / 39.18 s, all 842 inputs unchanged.
This is existing C/J scope: **82/200, approximately 41%**, formal **1/50 (2%)**,
unchanged. Source, calibration, independent and deployment/unfunded acceptance
remain open. Full verification of the combined integration is pending; next
implementation is validated receipt cost/slippage reporting through existing
PerformanceLab/audits, keeping unmatched evidence UNKNOWN. Six remaining active
hour ranges retain LOW confidence and exclude external waiting/owner actions.


Combined receipt/event/exit verification is complete at saved implementation
**5bfd38f0caa1f891738df459826fd1a7d6a4e203**, tree
**5b29ee49eba4ed46639205b4d3cc0916f6f97789**: **4020 passed, four existing warnings,
276.83 s, exit 0**, all **843 inputs unchanged**. Final event/exit regression
**91 / 9.84 s**; focused **40 / 6.33 s**; preceding receipt tree **328 / 39.18 s**.
Full manifest/output: V11_RECONCILIATION_REGRESSION_EVIDENCE.md. Verification of
already credited integrations earns no extra unit: **82/200, approximately 41%**,
formal **1/50 (2%)**, unchanged. READY_TO_FUND, six active-hour ranges, independent,
actual-source/calibration and owner/host gates remain open. Next off-host action is
validated receipt cost/slippage reporting through PerformanceLab and daily audits;
legacy or unmatched evidence remains UNKNOWN. No owner action blocks that code.


Receipt cost/causal price audit integration — 2026-09-25: validated optional
synthetic execution details now join retained reconciled fills to bounded
PerformanceLab execution-window costs, original signal/post-validation depth
comparisons and scheduled candidate audits. Partial fills share exact-book depth;
legacy/malformed timing stays in possible cohorts, costs already in all-in ledger
are never deducted twice, and pinned crash/replay preserves report identities.
Final 26 new checks passed in 6.99 s (exit 0), including the actual typed candidate
receipt-to-account-to-audit path. Affected/full verification of this new tree is
pending; the saved 4020-pass run remains evidence for the preceding implementation.
R05/R40/R41 already hold C/J, so this earns no new named milestone: **82/200,
approximately 41%**, formal **1/50 (2%)**, unchanged. No E/A, venue execution,
source calibration, independent safety or host/unfunded acceptance is credited.


Receipt-cost integration final verification: published 79a7b1e98388c34a9c817fc567cef5867ea59e0e,
tree 1852925219ce2ab0453b97a226fd0bbae44dc1f1, passed **4046 / four existing warnings /
347.89 s**, exit 0; all **845 inputs unchanged**. Affected **245 / 35.93 s** on the
same tree. Complete shared manifest/results are in
V11_EXECUTION_COST_REGRESSION_EVIDENCE.md. The preceding pending-verification note
is historical. More regression checks earn no new C/J/E/A milestone: **82/200,
approximately 41%**, formal **1/50 (2%)**, unchanged. Replay review identified the
remaining historical-model/receipt-boundary join; it is not yet implemented or
credited. Six full-scope active-hour milestones and external dependencies remain
in V11_WORK_CHECKPOINT.md; no calendar wait or financial authority is implied.


Historical economic replay first slice — 2026-09-25: PerformanceLab now reconstructs
original future/same-day temperature source/receipt boundaries, retained protected
model history and immutable bundles, reuses runtime prediction/valuation functions,
and compares original common-account risk/context. 18 new cases / 72 related passes
in 4.48 s after documented JSON decoding/assertion corrections. Automatic candidate
audits, full control-flow/PWS/challenger replay and historical executable attestation
remain open. No new named milestone is credited at this intermediate checkpoint:
**82/200, approximately 41%**, formal **1/50 (2%)**, unchanged. Actual independent,
host and unfunded evidence remain unearned; six active-hour ranges are unchanged.


Candidate replay audit join — 2026-09-25: optional typed replay now runs from the
scheduled candidate audit worker against all retained in-window temperature
decisions, with bounded references/shared budget, unknown-preserving selection,
original model/account identities and report recovery. The finite candidate test
covers mocked census -> derived risk -> original temperature decision -> common
account context -> replay audit. Final new 27 / 4.94 s; initial related 65 / 13.93 s;
a new invalid initial-cash fixture was corrected without relaxing account limits.
Combined affected/full saved-tree regression remains due before scoring R04's new
join. Intermediate total stays **82/200, approximately 41%**, formal **1/50 (2%)**.
This does not credit full control flow, PWS/other strategy/executable attestation,
actual sources/calibration, independent review or host/unfunded acceptance.


Verified historical temperature replay integration — 2026-09-25: **R04 earns J**
for original receipt-bound source/model reconstruction -> shared prediction and
valuation -> archived common-account context -> scheduled typed candidate audit.
The core C existed, but this upstream/downstream historical join did not. Later
source revisions, protected model changes and account appends cannot replace the
original inputs; missing history/policy gates, and report crash recovery is pinned.
Finite mocked-source candidate coverage plus 218 affected passes / 28.45 s and
4073 full passes / four existing warnings / 285.58 s verify implementation
`1fea164abd676d0b6f15f5ec11beba2e9fb45576`, tree
`af0a53888907076e68072b8a52fec163502751b5`, all 849 inputs unchanged.
Exact evidence: docs/V11_REPLAY_REGRESSION_EVIDENCE.md. New total **83/200**,
**approximately 42%** by the fixed half-up rounding. Formal **1/50 (2%)** unchanged.
This is the named integration slice, not complete engine/control-flow replay,
original executable attestation, empirical calibration or renewed financial
permission. PWS/other strategies/challengers, real/operational evidence and original
acceptance remain open. No E/A credit and no credit for more tests or elapsed time.


PWS historical observation/payout replay — 2026-09-25: the existing R04 integration
now also reconstructs original separate observation/payout epochs, paired PWS-on/
PWS-off inputs and exact research ablation, feeding shared observation and payout
calculations and scheduled candidate audits. Later labels cannot leak into the
original receipt boundary; original policies/history/inputs cannot be replaced.
15 new checks passed / 5.15 s, including candidate/audit recovery. Affected/full
verification remains due. **83/200, approximately 42%**, formal **1/50 (2%)**,
unchanged: R04 J is already earned. Full control-flow/commands/other strategies,
PWS label scoring, empirical source/calibration/lead and all independent/operational
acceptance remain open. No more credit for this expansion or additional tests.


Received-source strategy replay — 2026-09-25: SOURCE_SHOCK and RELEASE_OPPORTUNITY
now join the original received-report predecessor, exact post-receipt book/event
context, original payout model and common-account context to scheduled candidate
audits. Shared runtime receipt/change-type calculations and bounded historical
source queries preserve causal ordering after later reports/promotions. All five
temperature strategy variants have numerical joins. PWS affected 380 passed /
57.74 s at f0335ede; combined focused 79 passed / 17.50 s, with final combined
regression still due. **83/200, approximately 42%**, formal **1/50 (2%)**, unchanged.
R04 C/J are already earned. No full control/command/label replay, actual evidence,
independent/operational acceptance or financial authority is credited.


Final combined scoped replay verification — 2026-09-25: **4104 passed, four existing
warnings, 296.20 s**, plus affected **463 passed / 64.41 s** on published
`41d406951579a4c0acbe75f256cf3fc96ac588ed`, tree
`1d3cb8c4ea543a61681361428e68d44cd988ce1d`. All **853 inputs unchanged**.
Exact provenance: docs/V11_SCOPED_REPLAY_REGRESSION_EVIDENCE.md. This verifies the
future/same-day, PWS observation/payout and received-source candidate replay joins.
It does not close full control/command/label/executable replay or actual/independent/
operational acceptance. **83/200, approximately 42%**, formal **1/50 (2%)**, unchanged.
No additional C/J/E/A is credited for extension, regression count or elapsed effort.


Conditional PAPER account replay integration — 2026-09-25: original pre-state,
policy, prepared candidates/rejections, conditional exit checks and exact clock/
receipt inputs now feed the shared coordinator numerical engine without commands
or current admission. Reservation/status/recovery/fill/terminal comparisons join
complete bounded scheduled candidate audit cohorts, including protected synthetic
basket/exit reconciliation. Missing originals and incomplete cohorts gate; legacy
and unsupported commands remain unknown denominator members. Final focused
**82 passed / 10.84 s**, session **42335**, with initial shared-runtime **69 / 8.45 s**.
Intermediate fixture errors and raw evidence: docs/V11_ACCOUNT_REPLAY_EVIDENCE.md.
Affected/full regression on the saved implementation is next.

No new milestone is earned: R04 and the joined account/audit packages already have
C/J. Preparation/control-flow and executable replay, genuine source/model evidence,
independent acceptance, verified isolated deployment and unfunded READY_TO_FUND
remain open. **83/200 = 41.5%, approximately 42%; formal 1/50 (2%)**, unchanged.
The denominator remains 200 and covers all engineering through verified deployment
and unfunded readiness. V10 unchanged/DEFERRED; no financial authority.


Verified original account-effect integration — 2026-09-25: **4138 passed, 4 warnings in 298.08s (0:04:58)**,
exit 0, session **44615**, and **573 passed in 70.83s (0:01:10)**, exit 0, session **55056**,
on published implementation **e21ae6e4fbef2c14e3fd748fbda8314d8d54773a**, tree
**adf2367c7ca319b607f629d910acf54fb73cb7d8**. All 858 tracked inputs unchanged through both runs;
exact metadata/logs/map: docs/V11_ACCOUNT_REPLAY_EVIDENCE.md. This verifies the
shared conditional numerical account/candidate audit integration; it does not
complete original preparation/control-flow, actual evidence, independent review,
isolated deployment or unfunded readiness. More regression earns no extra credit.
**83/200 (~42%)**, formal **1/50 (2%)**, unchanged; V10 DEFERRED/unchanged and
NOT_READY_TO_FUND. No financial authority.


Final original-policy guard — 2026-09-25: replay also verifies the freshly
constructed historical configuration digest, preventing a replaced caller
policy/limit object from hiding behind a cached hash. Two production lines and
two focused cases followed the 4138-pass full integration. Final **84 passed /
8.92 s / exit 0**, session **39669**; exact final file hashes and the prior full
manifest are retained in docs/V11_ACCOUNT_REPLAY_EVIDENCE.md. Broad tests are
explicitly attributed to e21ae6e4; final targeted tests include the additional
guard. No additional full release or independent acceptance is claimed.
**83/200 (~42%)**, formal **1/50 (2%)**, unchanged. Existing C/J coverage improved;
no new milestone or authority. V10 unchanged/DEFERRED; NOT_READY_TO_FUND.
