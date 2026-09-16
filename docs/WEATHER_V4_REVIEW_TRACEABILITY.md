# Weather v4 adversarial-review traceability

This record stays conservative: `mitigated` means the corrective paper path is fail-closed against the reviewed counterexample. It does not mean the three-layer same-day strategy is scientifically calibrated or approved for financial use.

- R01: mitigated. Paper settlement requires an explicit final binary payout vector; indicative closing prices are rejected.
- R02: contained. Structural delivery is hard-disabled. It remains disabled until a replacement common-resolution proof is independently accepted.
- R03-R06: mitigated by the strict contract/source/date/grammar gate and exact YES/NO CLOB identity checks.
- R07: mitigated by station-local eligibility and expiry rechecks immediately before dispatch.
- R08: mitigated by excluding old/unverified rows and using bounded persisted quarantine progress.
- R09-R12: mitigated by final repricing, quote/fill expiry checks, minimum/tick checks, one-time visible-capacity consumption and station-day exposure limits.
- R13: mitigated. Ambiguous Telegram outcomes remain explicitly uncertain instead of being blindly retried or counted as delivered.
- R14: mitigated by rotating settlement progress and surfaced lookup failures.
- R15-R17: mitigated by semantic forecast cache identity, retained raw forecast evidence, explicit unknown model-run age and station/grid distance checks. Provider-native model initialization age is not claimed when unavailable.
- R18: mitigated by applying same-day/local-date eligibility before the actual per-cycle forecast budget and by persisted cursors.
- R19: substantially mitigated. Decisions retain forecast evidence, contract identity, execution protocol, exact top-book decision evidence and hashes. Final host release identity is separately attested at deployment. A per-signal copy of the complete runtime release/config remains a non-critical replay-hardening gap before claiming perfect provenance.
- R20-R21: mitigated by the corrective Telegram/operator states and freshness-based health reporting.
- R22: code-side runtime attestation is implemented and tested against stale entrypoints, wrong interpreter, duplicate process, DB-path drift and release mismatch. Actual VM attestation remains a deployment acceptance step.
- R23: backup, integrity verification, restore verification and retention policy are implemented and tested. A real pre-release VM backup remains a deployment acceptance step.
- R24-R25: corrective WRH/local-date/unit/as-of handling is implemented and covered by the source acceptance tests. Same-day trade delivery remains disabled.
- R26: mitigated by representing the first-following-row transition as a bounded uncertainty bracket rather than pretending polling proves the exact cutoff publication state. Calibration/settlement authority stays false when exact history is not provable.

Code-side acceptance does not deploy anything, does not enable same-day trade alerts, and does not grant real-money authority.
