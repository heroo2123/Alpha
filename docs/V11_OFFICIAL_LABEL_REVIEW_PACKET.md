# V11 Official Label Review Packet (offline preflight/join)

## Scope: exactly three files

- `polymarket_scanner/v11/official_label_review_packet.py` -- the module.
- `tests/test_v11_official_label_review_packet.py` -- adverse/synthetic tests.
- `docs/V11_OFFICIAL_LABEL_REVIEW_PACKET.md` -- this file.

No other file was added or modified for this slice.

## What it does

`build_review_packet(...)` is a bounded, pure, offline preflight/join. Given
plain caller-supplied Python objects -- an original rule (`RuleFingerprint`
from `rules.py`) and its admission receipt, a whole-vector decision and the
capture receipt that pins it, every known label-determining disclosure
receipt for the event, the existing `derive_offline_settlement_source` result
(see `official_settlement_source.py`), and an independently supplied Gamma
comparator vector -- it checks:

- exact event/station/date/timezone/fingerprint/partition/receipt/hash
  binding consistency across all of the above;
- that the original rule version and its receipt predate the decision;
- that every decision bucket/target is covered exactly once, in the rule's
  canonical order (catching reordering, omission, and duplication
  separately, plus swapped condition/YES-token bindings);
- that every label-determining disclosure receipt -- selected or not -- is
  strictly after both the decision and its capture, rejecting lookahead
  (at-or-before is a violation, not just strictly-before);
- that duplicate/conflicting disclosure receipts for the same id are caught;
- that the settlement-source claim and the Gamma comparator are compared as
  two separate pieces of evidence (`gamma_comparison` is `MATCH`,
  `MISMATCH`, or `UNAVAILABLE`), never merged into one "truth". A Gamma
  comparator whose raw document hash equals the source claim's own raw hash
  is treated as suspicious (not independent evidence) and is forced to
  `MISMATCH` with an explicit violation code, never `MATCH`.

It returns a frozen `ReviewPacket` dataclass with a structured `violations`
tuple (explicit reason codes, not a bare boolean) and passes the supplied
`source_claim` through verbatim, so a claim already marked
`SYNTHETIC_DERIVATION_ONLY` stays visibly marked as such -- this module never
upgrades it to look like a real/authoritative label.

## What it explicitly does NOT do

- It grants **zero label, qualification, settlement, calibration,
  financial, or promotion credit**. Every such field on `ReviewPacket`
  (`independent_label_attestation`, `settlement_authority`,
  `calibration_authority`, `financial_authority`, `automatic_promotion`,
  `qualified`) is a frozen dataclass field with a literal `False` default.
  No code path in this module ever passes any of those fields as a
  constructor argument, so none of them can ever become `True`, regardless
  of input.
- It performs no I/O, no file reads beyond its arguments, and no network or
  provider access of any kind.
- It never calls `derive_offline_settlement_source` or any archive reader,
  scorer, or supervisor module -- those results are caller-supplied data.
- It never mints anything that looks like a `LABEL`, never adjudicates
  publisher rights or authenticity, and never marks any event "qualified".

## Adverse test coverage (summary)

`tests/test_v11_official_label_review_packet.py` uses only synthetic
in-memory fixtures (no real files, no network) and exercises: reordered,
omitted, and duplicated decision buckets; a swapped condition/YES-token
binding; a stale/drifted rule fingerprint and a missing rule receipt;
disclosure timing both strictly after decision/capture (valid) and at-or-
before (rejected as lookahead), including a case where a later "selected"
disclosure cannot mask an earlier unselected one; duplicated/conflicting
disclosure receipts for the same id; a Gamma comparator whose hash equals
the source hash (treated with suspicion, forced to `MISMATCH`); Gamma
agreement and disagreement with the source result; an entirely absent Gamma
vector (`UNAVAILABLE`); malformed/wrong-type clock and hash fields across
several inputs; a Fahrenheit/Celsius unit mismatch and a DST-relevant
timezone mismatch between inputs; confirmation that a
`SYNTHETIC_DERIVATION_ONLY` source claim is passed through unchanged; and an
explicit adversarial test that tries multiple ways to force an
independent-label/settlement/calibration/financial/promotion flag to `True`
and confirms every attempt fails, including direct dataclass-field
introspection and a `TypeError` check on the public builder's signature.
All tests are deterministic and pass under both `python -m pytest` and
`python -O -m pytest`.
