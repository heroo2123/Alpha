# Weather v4 adversarial-review traceability

This file is intentionally conservative: `mitigated` means the v4 live path is
fail-closed against the reviewed counterexample, not that the broader feature has
been scientifically certified.

- R01: mitigated in v4 settlement finality/vector gate.
- R02: mitigated by disabling structural delivery; replacement proof still required.
- R03-R06: mitigated at the strict v4 directional admission/CLOB boundary.
- R07: mitigated by dispatch-time station-local expiry + margin; regression still
  required across awaited forecast/CLOB/Telegram paths.
- R08: mitigated for performance by default exclusion of pre-v4/unverified rows.
- R09-R12: mitigated by final reprice, frozen protocol, min/tick checks, capacity
  consumption and station-day exposure cap.
- R13: mitigated by explicit ambiguous-delivery state; settlement notifications also
  retain UNCERTAIN instead of automatic retry.
- R14: mitigated by persisted rotating settlement cursor and surfaced lookup errors.
- R15-R17: mitigated by semantic cache key, raw member evidence, explicit unknown run
  age and grid bound. Provider-native model initialization remains unavailable and is
  not claimed.
- R18: mitigated by cheap local-day gating before the actual per-cycle eligible budget
  plus a persisted event cursor.
- R19: materially improved; an offline replay acceptance test remains required before
  final sign-off.
- R20-R21: materially improved in v4 Telegram/operator UI and fresh-health policy.
- R22: renderer points to v4; actual VM unit/process/import attestation remains a later
  deployment acceptance task.
- R23: v4 bounds forecast caches; paper DB backup/restore remains a later deployment
  acceptance task.
- R24-R26: not promoted. Same-day live emission remains disabled until the corrective
  WRH/as-of/finality source work and acceptance suite are finished.
