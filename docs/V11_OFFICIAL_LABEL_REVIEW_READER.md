# V11 Official Label Review Reader (Slice R: pure offline reader)

## Scope: exactly three files

- `polymarket_scanner/v11/official_label_review_reader.py` -- the module.
- `tests/test_v11_official_label_review_reader.py` -- adverse/synthetic tests.
- `docs/V11_OFFICIAL_LABEL_REVIEW_READER.md` -- this file.

No other file was added or modified for this slice. This is the "Slice R"
contract from `/tmp/alpha-brain-packet-semantics-20261007.report.md` section
5; the rest of this doc assumes that architecture map as background.

## What it does

`read_review_inputs(*, store, capture_id, settlement_source_record_id=None)`
takes an already-constructed `EvidenceStore` and an explicit learning-capture
v2 `MEASUREMENT` record id. It:

1. Fetches the capture, pins a read frontier (`store.pin_read_view`) scoped
   to that event's `RULE_STATE`/`MEASUREMENT`/`LABEL`/`RULES` heads, and
   discards every row with `seq` past that frontier for the rest of the call
   (including rows a concurrent writer appends mid-call).
2. Verifies the capture's own retained lineage: version, `complete_event_vector`,
   `financial_authority is False`, `target`, `selection_scope`, the embedded
   `RuleFingerprint`'s own digest integrity and partition bucket shape, the
   embedded rule's own `event_id` against the capture's event,
   `binding.rule_fingerprint`, and every child `DECISION` row's seq-vs-frontier, sha256/binding/
   `target_identity`/`side`/`request_sha256`/`financial_authority` against
   the capture's own stored references. Any violation here is a typed
   `EvidenceError` refusal (`LABEL_REVIEW_READER_<REASON>`), not a packet
   input or a hold -- this mirrors `forward_qualification._require`. A
   missing capture or child record is re-typed the same way rather than
   leaking the store's bare `EVIDENCE_MISSING`.
3. Maps the result into the exact plain-dict/tuple shapes
   `official_label_review_packet.build_review_packet` expects for `rule`,
   `rule_receipt`, `decision`, `capture`, and `disclosures`, and reports
   mapping-policy choices under `provenance` (e.g. `no_token_provenance`,
   `rule_receipt_join`, `decision_seq_policy`, `first_matching_rule_state_seq`).
4. Finds the original rule admission as the latest `RULE_STATE` for the
   event with `seq` strictly before the minimum child `DECISION` seq. It
   refuses (does not merely hold) if that receipt's own declared `preimage`
   does not digest to its own declared `fingerprint` -- an internal-
   consistency check independent of anything else. It then flags (never
   silently accepts) a quarantined or non-`alpha_v11_rule_guard_v1` receipt,
   a receipt whose fingerprint does not equal the capture-embedded rule's
   own `sha256` (`RULE_RECEIPT_FINGERPRINT_MISMATCH` -- the receipt being
   independently admissible says nothing about whether it is the rule this
   decision actually committed to), and any quarantine/fingerprint drift
   -- measured against the decision's own committed `rule.sha256`, not
   against the selected receipt's fingerprint -- in the decision-to-capture
   window or after the capture, up to the pinned frontier. A malformed or
   preimage/fingerprint-inconsistent row in either drift window counts as
   drift even if its declared fingerprint matches. An earlier row considered
   while computing the first matching run cannot extend that run unless its
   own preimage/fingerprint pair is consistent. The
   `rule_receipt_join` provenance says `FINGERPRINT_EQUALITY` only when the
   selected fingerprint actually equals the decision rule; otherwise it says
   `FINGERPRINT_MISMATCH` or `NO_RECEIPT`.
5. Scans every retained `RULES` payout receipt from any provider whose
   payload nests a market object under some combination of `event`/
   `market`/`response`/`markets` (bounded-depth walk, matching
   `tools/v11_brain_label_attestation.py`'s `_gamma_market` precedent) with
   a resolvable payout for one of the rule's own partition markets, and
   every `LABEL` record for the event, as disclosure candidates -- every
   one, not only a "selected" one -- bounded, never returning a truncated
   tuple. If any Gamma wrapper exceeds the depth bound, the scan returns an
   empty tuple with `DISCLOSURE_SCAN_INCOMPLETE`; finding a payout in the
   same receipt does not excuse an unexamined branch. Each disclosure's
   `recorded_at` is clamped to the earliest of the archive's own
   `recorded_at` and any self-declared `observed_at`/
   `issued_at`/`published_at`/(`LABEL`'s) `knowable_at` in the same row,
   per the architecture report's disclosure-time rule -- fail-closed, since
   an earlier declared time can only make lookahead easier to flag.
6. Returns a frozen `ReviewInputs` dataclass whose `packet` and
   `gamma_comparator` fields are always `None`, and whose six authority
   fields are hardcoded `False` and refused if forced to any other value by
   `dataclasses.replace(...)` or a direct constructor call, exactly like
   `ReviewPacket.__post_init__`.

It never opens a path (the caller constructs `store`), never calls a writer
method on the store (`audit`, `capture`, `decision`, `safety_audit`,
`funnel`, `source_result`), never uses a wall clock, and performs no network
or provider access. It never imports or calls
`official_label_review_packet.build_review_packet` or
`official_settlement_source.derive_offline_settlement_source` -- it only
produces that first function's *inputs*.

## Why `packet` is always `None`

No V11 writer today retains a WRH/WU settlement-source byte receipt (see
`official_settlement_source.py` and the architecture report's section 0/6).
Without one, no disclosure can carry the `raw_sha256` the packet's
`SOURCE_DISCLOSURE_UNBOUND` check requires, so a genuinely consistent packet
can never be built from retained evidence yet. Rather than build one from a
caller-supplied or synthetic source -- which risks exactly the kind of
accidental authority this module must never grant -- this slice hardcodes
`packet=None` and `gamma_comparator=None`, each with its own permanent hold.

`settlement_source_record_id` exists on the public signature only so a
future additive writer slice ("Slice W" in the architecture report) can be
wired in without changing this function's call shape. It is never read,
resolved, or otherwise used by this module today; the return value does not
depend on it regardless of what is passed (a non-`None`, non-`str` value is
still rejected as a type error, since that is unambiguously a caller bug,
not a forward-compatibility gap).

## Hold vocabulary

Six holds are permanent declared residuals of this slice -- present on
every call regardless of input, because the gaps they describe are
structural to Slice R, not data-dependent:

- `SETTLEMENT_SOURCE_NOT_RETAINED` -- no writer retains WRH/WU bytes yet.
- `PUBLISHER_RIGHTS_AND_AUTHENTICITY_UNREVIEWED`
- `ARCHIVE_NOT_INDEPENDENT_TAMPERPROOF`
- `GAMMA_COMPARATOR_NOT_CONSTRUCTED`
- `DISCOVERY_CATALOG_NOT_SCANNED` -- `v11-discovery-catalog` pages (D6) are a
  declared residual; this reader only scans the market's own event-scoped
  `RULES`/`LABEL` history.
- `WU_FALLBACK_OUT_OF_SCOPE` -- Weather Underground fallback receipts (D2)
  are out of scope categorically, not conditionally.

The rest are conditional on the mapped evidence:

- `RULE_STATE_NOT_ADMISSIBLE_AT_DECISION` -- the selected rule receipt is
  quarantined, not `alpha_v11_rule_guard_v1`, or not in
  `{SEMANTICS_OBSERVED, REVIEWED_RECERTIFIED}` (or no receipt exists at all).
- `RULE_RECEIPT_FINGERPRINT_MISMATCH` -- the selected rule receipt is
  independently admissible, but its `fingerprint` does not equal the
  capture-embedded rule's own `sha256`; it is admissible for a rule the
  decision did not actually commit to. Orthogonal to, and independent of,
  `RULE_STATE_NOT_ADMISSIBLE_AT_DECISION`.
- `RULE_DRIFT_IN_DECISION_WINDOW` / `RULE_DRIFT_AFTER_CAPTURE` -- a
  quarantined or internally malformed `RULE_STATE`, or one whose fingerprint
  differs from the decision's own committed `rule.sha256`, exists in `[min_child_seq,
  capture.seq]` or after `capture.seq` (through the pinned frontier),
  respectively.
- `CHILD_DECISION_AFTER_CAPTURE` -- a child `DECISION`'s seq/time is not
  strictly before/at-or-before the capture's.
- `CAPTURE_VERSION_UNSUPPORTED` -- the capture's `details.version` is not
  `alpha_v11_forecast_learning_capture_v2`; `rule`/`rule_receipt`/`decision`
  are `None` in this case (there is no v2 lineage to verify), but `capture`
  is still mapped directly from the audit row and disclosures are still
  scanned.
- `MULTIPLE_CAPTURES_FOR_EVENT` -- another v2 `MEASUREMENT` exists for the
  same event; `provenance['other_capture_ids']` lists them.
- `DISCLOSURE_SCAN_INCOMPLETE` -- a bounded `RULES`/`LABEL`/
  `OFFICIAL_OBSERVATION` scan hit its row cap, or a Gamma payload exceeded
  the payout-walk depth bound. `RULES`/`LABEL` truncation and excess Gamma
  depth return `disclosures=()`; proxy scan truncation retains the complete
  `RULES`/`LABEL` tuple but still carries this hold.
- `INTRADAY_PROXY_INFORMATION_PRESENT` -- at least one `NOAA_AWC`
  `OFFICIAL_OBSERVATION` record exists for the event; count reported under
  `provenance['proxy_receipts_target_day_before_decision']`. This is a
  proxy population (see `label_attestation.py`), never the settlement
  source; information only.

A scan-completeness failure on the rule-state history or the
multiple-capture uniqueness check (not disclosures) raises instead of
holding, since those feed directly into lineage/drift correctness rather
than being a declared residual. Likewise, a child `DECISION` whose own
`seq` is past the pinned frontier -- only reachable if a caller's `store`
wrapper injects a write between the pin and the read, since a genuinely
pinned frontier excludes it by construction -- is a refusal
(`CHILD_AFTER_FRONTIER`), not a hold.

`holds` itself is enforced, not merely populated: `ReviewInputs.__post_init__`
requires a `tuple` of `str`, requires every permanent hold to be present,
and requires every element to be drawn from the closed `HOLDS` vocabulary
-- `dataclasses.replace(...)` cannot strip the permanent holds, substitute
a non-tuple, or inject an unrecognized token.

## What it explicitly does NOT do

- It never mints a `LABEL`, builds a `ReviewPacket`, or calls
  `derive_offline_settlement_source` -- `packet` is unconditionally `None`.
- It never chooses a "latest" or "best" capture; `capture_id` must be
  explicit, matching the architecture report's requirement.
- It never scans `v11-discovery-catalog` pages or WU/WRH receipts (none
  exist to scan).
- It performs no I/O beyond the supplied `store`'s own read methods, no
  network access, and calls no writer method on the store.
- Every one of its six authority fields is a frozen dataclass field with a
  literal `False` default, defended the same way
  `official_label_review_packet.ReviewPacket` defends its own six fields.

## Adverse test coverage (summary)

`tests/test_v11_official_label_review_reader.py` uses only synthetic
`tmp_path` stores (chmod `0o700`); one test reuses the real learning-capture
v2 writer path (`capture_forecast_vector`, via the existing
`factory`/`evaluate` fixtures from `test_v11_learning_capture.py`) to prove
compatibility with genuine production-shaped data. Every other test
hand-constructs a minimal but structurally genuine `RuleGuard`-observed rule
plus `DECISION`/`MEASUREMENT` rows directly through `EvidenceStore`, so each
scenario is exactly controlled. It exercises: a quarantined `RULE_STATE`
whose fingerprint still matches (the admission trap the packet's own
fingerprint-equality check would miss); rule drift strictly inside the
decision-to-capture window versus strictly after the capture; a
threshold-ordered (non-market-id-ordered) set of children, confirming the
reader's own output is sorted by `market_id` while a direct
`build_review_packet` call with the unsorted capture order documents
`BUCKET_REORDERED`; a mutated embedded `RuleFingerprint` whose digest no
longer matches its own stored hash; a naturally sha-mismatched child decision,
and a "dishonest store" that mutates a child's `binding`/`request_sha256`
while leaving its reported hash stale, to prove those checks are independent
of the sha comparison, not gated behind it; a child `DECISION` genuinely
appended (by archive sequence) after its capture, built via precomputed
content-addressed hashing rather than any after-the-fact mutation; a legacy
v1 capture and a second v2 capture for the same event; a closed-market
payout receipt recorded before any decision exists, mapped and then fed to a
direct `build_review_packet` call with a synthetic (test-only) source claim
to show `LOOKAHEAD_VIOLATION`; an unselected recapture and a
`GAMMA_DISCOVERY_EVENT` copy, both included as disclosures; a `LABEL`
disclosure whose `knowable_at` predates its own `recorded_at`, confirming
the fail-closed `min(...)` clamp; an `OFFICIAL_OBSERVATION` proxy receipt
reported as information only; a disclosure scan forced to its bound
(`DISCLOSURE_SCAN_INCOMPLETE`, never a partial tuple); a record injected into
the underlying store immediately after the pin is taken, confirmed excluded
from the result while still genuinely present in the store; every store
writer method and `socket.socket.connect` monkeypatched to raise, confirming
the reader never touches either; and an exhaustive attempt to force `holds`
empty or any authority/`packet`/`gamma_comparator` field to a disallowed
value via `dataclasses.replace(...)`, a forged keyword argument, and direct
attribute assignment on the frozen result.

A later repair round (independent-review findings F1-F8) added further
cases, each isolating one check so that removing just that check (and no
other) would make the case fail: a decision bound to a rule that was
genuinely admitted for a *different* fingerprint
(`RULE_RECEIPT_FINGERPRINT_MISMATCH`); a forged `RULE_STATE` preimage
whose digest no longer matches its own declared fingerprint; each of the
three receipt-admissibility fields (`version`, `quarantined`, `state`)
failing on its own while the other two stay genuinely valid; drift
measured against the decision's own rule rather than a mismatched
receipt, using a directly-crafted (not `RuleGuard.observe`'d) in-window
`RULE_STATE` to avoid the guard's own quarantine-on-change masking the
check; a capture whose embedded rule's `event_id` does not match the
capture's own event; all four non-`GAMMA_CLOSED_MARKET`/
`GAMMA_DISCOVERY_EVENT` Gamma providers the production label-attestation
tool also accepts as label sources, each shown to disclose and flag
`LOOKAHEAD_VIOLATION`; a self-declared earlier `published_at` clamping a
`RULES` disclosure's time; `holds` rejected for dropping a permanent hold,
for not being a tuple, or for carrying a token outside the closed
vocabulary; a child `DECISION` injected after the pin, now refused rather
than silently folded in; typed refusals for a missing capture, a missing
child, a non-dict row, and a non-string embedded `canonical_json` field;
`decision.seq`/`recorded_at` equal to the true child minimum with children
genuinely spread across distinct seqs and times (not the max); each child-
level shape refusal (`side`, `financial_authority`) and each capture-level
one (`financial_authority`, `complete_event_vector`, `target`,
`selection_scope`, `binding.rule_fingerprint`) in isolation; an incomplete
vector covering only a strict subset of the rule's partition; a duplicated
child id; a malformed row-level `target_identity`; a capture row of the
wrong kind; a child of the wrong kind or event; an `OFFICIAL_OBSERVATION`
from a provider other than `NOAA_AWC` not counted as a proxy receipt; and
a `LABEL` whose `target_identity.market_id` is outside the rule's
partition, excluded from disclosures. All tests are deterministic and pass
under both `python -m pytest` and `python -O -m pytest`.

The next synthetic repair cases cover an early Gamma payout beyond the
bounded walk, including a receipt with a shallow payout before the deep
branch; changed and missing preimages in both the decision window and
after-capture `RULE_STATE` history; a digest-consistent partition bucket
missing `market_id`; and provenance for a selected receipt whose
fingerprint disagrees with the decision's rule. The depth cases require an
incomplete-disclosure hold with no partial tuple. The malformed later state
cases require the appropriate drift hold. The partition case requires a
namespaced typed refusal.
