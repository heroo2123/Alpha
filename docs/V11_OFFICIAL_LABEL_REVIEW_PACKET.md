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

- exact event/station/date/timezone/fingerprint/partition identity binding
  across all of the above, plus one specific hash binding: at least one
  well-formed disclosure receipt must carry the same `raw_sha256` as the
  source claim's own winning bytes, and that receipt must independently
  pass the lookahead check below (`SOURCE_DISCLOSURE_UNBOUND` otherwise).
  No other pair of hash-bearing fields is cross-checked against each other
  beyond that one binding and the Gamma-hash-collision check described
  below;
- that the rule payload itself carries all of its own required identity
  fields (`event_id`/`station`/`target_date`/`timezone`/`unit`) --
  `RULE_IDENTITY_INCOMPLETE` if not, rather than silently skipping the
  matching checks that depend on a missing field;
- that the original rule version and its receipt strictly predate the
  decision (an equal seq/timestamp is a violation, not a pass);
- that every decision bucket/target is covered exactly once, in the rule's
  canonical order (catching reordering, omission, and duplication
  separately, plus swapped condition/YES-token bindings);
- that every label-determining disclosure receipt -- selected or not -- is
  strictly after both the decision and its capture, rejecting lookahead
  (at-or-before is a violation, not just strictly-before); an empty or
  entirely malformed disclosure set is its own explicit violation
  (`DISCLOSURE_MISSING`), not silence;
- that duplicate/conflicting disclosure receipts for the same id are
  caught, including an *exact* duplicate (same content, not just same id),
  which gets its own `DISCLOSURE_DUPLICATED` code distinct from
  `DISCLOSURE_CONFLICT`;
- that a settlement-source claim with no usable winner -- any code other
  than `SYNTHETIC_DERIVATION_ONLY`, or a malformed winner even under that
  code -- is flagged `SOURCE_WINNER_UNAVAILABLE` unconditionally, whether or
  not a Gamma comparator is even supplied; station/target_date identity
  binding against the source claim also runs unconditionally, not only for
  a `SYNTHETIC_DERIVATION_ONLY` code;
- that a claimed winner (from the source claim, and separately from Gamma)
  is actually a member of the rule's own bucket partition
  (`WINNER_NOT_IN_PARTITION` otherwise, and never reported `MATCH`), and
  that the source claim's `winning_yes_token` matches the winning bucket's
  own `yes_token` field (`WINNING_TOKEN_MISMATCH` otherwise);
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
  of input. `ReviewPacket.__post_init__` additionally rejects any non-False
  value on those six fields, so the guarantee holds even for
  `dataclasses.replace(...)` or a direct `ReviewPacket(...)` construction
  outside this module, not only calls through `build_review_packet`.
  `source_claim` on the returned packet is also read-only (wrapped in
  `types.MappingProxyType`), not a mutable dict a caller could alter after
  the fact.
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
`SYNTHETIC_DERIVATION_ONLY` source claim is passed through unchanged; an
explicit adversarial test that tries multiple ways to force an
independent-label/settlement/calibration/financial/promotion flag to `True`
and confirms every attempt fails, including direct dataclass-field
introspection, a `TypeError` check on the public builder's signature, a
`ValueError` check on `dataclasses.replace(...)` forcing a flag `True`, and
a `TypeError` check on mutating the returned `source_claim` mapping; an
empty disclosure tuple (`DISCLOSURE_MISSING`); a disclosure that passes the
lookahead check but whose hash is not the source claim's own
(`SOURCE_DISCLOSURE_UNBOUND`); a refusal-code source claim with no Gamma
comparator at all and with station identity mismatched
(`SOURCE_WINNER_UNAVAILABLE`, `STATION_MISMATCH`); a winner id outside the
rule's bucket partition and a winning-token mismatch against the winning
bucket (`WINNER_NOT_IN_PARTITION`, `WINNING_TOKEN_MISMATCH`); a rule
payload missing one of its own identity fields
(`RULE_IDENTITY_INCOMPLETE`); an exact-duplicate disclosure receipt
(`DISCLOSURE_DUPLICATED`); and a rule receipt at the exact same seq/time as
the decision it should strictly predate (`RULE_RECEIPT_NOT_BEFORE_DECISION`).
All tests are deterministic and pass under both `python -m pytest` and
`python -O -m pytest`.
