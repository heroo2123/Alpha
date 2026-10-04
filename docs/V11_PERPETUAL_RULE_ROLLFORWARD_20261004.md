# V11 perpetual daily rule roll-forward — isolated engineering handoff

Status: isolated development only. Not installed. Not part of the running 0dd809e Shadow release.

## Goal

Eliminate daily manual station-review installation for recurring, semantically identical weather contracts without weakening exact-rule certification.

## Safety design

1. The existing strict weather compiler remains the semantic authority and must successfully consume the complete operative rule text.
2. DailyRuleEnvelope freezes only reviewed invariant semantics: station/city, source, source family, family/statistic, unit/timezone, observation population, precision, fallback/correction/finality/no-data policies, metadata identity, compiler/profile versions, strict-contract identity and partition shape.
3. Daily-varying event/date/market/condition/token IDs, questions and shifted bucket thresholds may change only after the strict compiler has accepted the new exact event.
4. StationRegistry.assess() is not weakened and no wildcard rule fingerprints are introduced.
5. Production follow-up should use a separately reviewed root-owned publisher. It should independently verify the protected envelope and prepared exact capability proofs, then append an exact daily review to the protected manifest atomically.
6. The publisher must have no wallet credentials, no order API, no funding authority, no model-promotion authority and no V10 mutation path.
7. Any invariant change, partition-shape change, unsupported strict grammar, metadata drift, capability failure, review expiry or root-custody failure must fail closed and notify the operator.

## Evidence

- New unit/mutation tests: 5/5 passed.
- Adjacent V11 certification + strict grammar + roll-forward tests: 27/27 passed.
- Real current KATL HIGH/F rules for 2026-10-04, 2026-10-05 and 2026-10-06 all map to the same envelope SHA-256:
  e4507cc9882a246707dec289512aa498e5d561d921c598edd711dca1ea5bdaed.

## Remaining work before deployment

- independently review the envelope surface;
- implement the root-side exact-review publisher with atomic rollback/custody tests;
- bind publisher input to exact raw Gamma bytes + strict compiler result + exact capability-proof hashes;
- add negative tests for wording/source/finality/metadata/token/partition mutations;
- test restart/replay/idempotency and manifest-race handling;
- run in nonfinancial Shadow before any CANARY/LIVE stage.

This work grants no financial authority.
