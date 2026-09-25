# Supplementary engineering estimate

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
