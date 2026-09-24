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
