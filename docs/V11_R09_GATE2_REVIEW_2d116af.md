# R09 gate 2 repair review — CHANGES_REQUIRED

Reviewed commit: `2d116af4c7bc8aa5527ef28a064c5eff68abb87e`.
Tree: `bfea9dfe580f39cab1825ee645865a43993cc212`.
Reviewer: independent Astra/high, separate from the Sonnet/high repair builder.
Date: 2026-09-30 UTC. Exact detached review tree:
`/tmp/alpha-v11-r09-gate2-review-2d116af/Alpha`.
Newer main inspected: `bab852d68bdb2424e1287d2ffda90ff0ff3c95cd`.

**CHANGES_REQUIRED. Gate 2 remains OPEN; do not merge this implementation or
advance to gate 3.** The repair fixes several original counterexamples, but
its statement that all F1–F8 are repaired is not supported. Seven remaining
P2 correctness/contract defects are detailed below. These are offline
acceptance blockers; no operational financial bypass was demonstrated.

## Independent evidence

Read the three authoritative navigation ledgers first, applicable CLAUDE.md,
the accepted `V11_R09_DATA_CONTRACT_ADJUDICATION.md`, the full original
`fed1cbe` review/reproduction harness, and the repaired source and tests.
Verified the private FINAL-REVIEWED master in place against SHA-256
`a0e16d9bd7344c943a54a16a53c6757662363d93642f6e5cb7953cd047659b4a`.
No private content was copied. The accepted public contract suffices to
establish these remaining defects; this review does not reinterpret the master.

Independent execution with the existing V11 venv, bytecode/cache disabled:

- Exact repaired contract suite: **61 passed in 3.64 s**.
- Existing R09 geometry/causality selection: **34 passed, 103 deselected in 2.13 s**.
- Independent synthetic reproduction harness: **32/32 checks completed**,
  exit 0. These checks include repaired rejection controls and remaining
  incorrect acceptances; they are not 32 independent defects.
- Full implementation diff check is clean. Initial/final detached worktree
  status is clean. Only the new validator and its tests differ from the
  implementation base. No other Python module imports the validator.

Evidence files are in `/tmp/alpha-v11-r09-gate2-review-2d116af/`:
`repro_review.py`, `repro_results.json`, `repro.log`, `targeted-new.log`,
`targeted-existing.log`, `review.md`, `verdict.json`, `terminal.json`.
The terminal binds the exact commit/tree and SHA-256 identities of the report,
script, source, tests and result logs. Successful review completion means a
completed CHANGES_REQUIRED verdict, not acceptance of the implementation.

## Original findings: what actually closed

| Original finding | Independently observed result |
| --- | --- |
| F1 receipt uncertainty | Original 118-second late conservative receipt is now rejected. |
| F2 timing | Post-day prediction and fit/selection after prediction persistence now reject; artifact binding and uncertain pre-day freeze remain incomplete (R1). |
| F3 corpus | Plain dictionaries, detached cutoffs and incomplete cutoff sets reject; nonadjacent embargo is checked. Ordinary typed reconstruction still bypasses admission (R2). |
| F4 run/release | Date/init mismatch and mixed runs now reject. Frozen run-selection/freshness policy remains absent, covered by R7. |
| F5 target identity | Within-example station drift, wrong date text and same-version alias duplicates reject. Labels remain unbound to settlement identity (R3). |
| F6 label lineage | Orphans, pending status, out-of-range bucket and shared-registry payload rewrites reject. Family/partition drift and corpus-wide rewrites still pass (R4). |
| F7 capture | Shared-registry byte/index/value conflicts and wrong tzdata hash reject. Evidence binding remains incomplete, and legitimate station extraction conflicts were introduced (R5/R6). |
| F8 coverage | Example reports absent providers. Same-ID expectation shrinkage remains accepted, with no corpus missingness/fallback contract (R7). |

The repaired tests often prove a narrower condition than the original finding.
For example, the shrinking-coverage test voluntarily changes `policy_id`; the
actual bug is changing policy content without changing its asserted ID. The
orphan and rewrite tests partly exercise helpers rather than the entire corpus.

## R1 — P2 — prediction/artifact causality and conservative pre-day freeze are incomplete (F2)

Locations: `tools/v11_trajectory_contract.py:380`, `:492`, `:496`, `:506`, `:513`.

The new day-boundary check compares only nominal `prediction_frozen_at.utc`.
A prediction persisted nominally one second before day start with 120 seconds
of uncertainty passes: its conservative bound is **119 seconds into the target
day**. This directly violates the uncertainty-sensitive pre-day requirement.
The corrected post-day nominal test misses this case.

Split ordering compares fit/selection cutoff to prediction persistence, with
no parameter/selected-artifact identity or actual availability evidence. Both
DEVELOPMENT and CONFIRMATION accept a fit/selection cutoff 30 seconds after
decision, provided persistence is 60 seconds after decision. This illustrates
the unresolved distinction between decision, artifact readiness and persistence;
it does not by itself prove an actual fitted model used future labels because
no model identity is represented at all.

Required: bind held-out predictions to the appropriate frozen trained/selected
artifact and its causal availability; define/enforce the decision versus
persistence ordering. Enforce the conservative persistence bound strictly
before local-day start, with boundary-equality and uncertainty controls.
Do not implement a real fitter. Synthetic artifact manifests suffice.

Probes: `F2_remaining_*` in `repro_results.json`.

## R2 — P2 — a public dataclass is not a validated-record trust boundary (F3)

Locations: `tools/v11_trajectory_contract.py:431`, `:555`.

`ValidatedExample` has a public unchecked constructor, mutable nested coverage,
and no retained source inputs or validation seal. `isinstance` does not prove
that `validate_example` produced the content. Ordinary `dataclasses.replace`
on a genuine record can set `split='NOT_A_SPLIT'`, `evidence_class='REAL'`,
`learner_admitted=True`, `financial_authority=True`, `points=0` and a fabricated
freeze time. `validate_corpus` accepts it, counts one city-day and zero rows
in every split, and prints a fixed zero real-admission count. No private
reflection or `object.__setattr__` is needed.

A second probe first establishes rejection of a nonadjacent TRAIN/CONFIRMATION
embargo overlap, then replaces only the earlier record's label upper bound
with `1.0`; the corpus accepts. The nested `coverage['required']` list can also
be cleared directly on the purportedly immutable record.

Required: revalidate typed complete source examples against the corpus's actual
frozen inputs, or use a deeply immutable, fully bound record whose origin and
content are verified at admission. Reject undeclared splits/evidence classes
and true authority/admission flags; do not rely on a public class or a supplied
checksum alone. Retain/check the source facts needed to verify all predicates.
This is an offline input-validation failure, not real execution enablement.

Probes: `F3_remaining_typed_forgery_ACCEPTED`,
`F3_remaining_embargo_rewrite_ACCEPTED`.

## R3 — P2 — labels and station/rule metadata still lack settlement-target binding (F5)

Locations: `tools/v11_trajectory_contract.py:306`, `:316`, `:470`, `:516`.

A label has no station/event/day/rule/settlement-unit/rounding/bucket-partition
identity. A nonempty city string plus matching date is insufficient. The
independent probe validates a KATL example, then transplants the same label
to KORD with a different station-version and different rule-version on every
point. Both use the SAME capture and label registries; both pass and the corpus
counts two city-days. Thus this does not depend on the separate-registry defect.

`dedup_key=(station_version,date)` also identifies a metadata version, not a
stable canonical station/event/day target. It cannot express the relationship
between provider/family rows and one outcome trial. A version change must not
silently create a new independent day.

Required: a typed canonical settlement identity with stable station/event,
local day, family, rule, actual bucket partition, unit and rounding; bind
versioned station/rule metadata and its availability to the decision and
label. Define grouping/dedup independently of arbitrary display names and
metadata revision hashes. Do not grant extra independent-trial counts to
providers, families or station-version changes.

Probe: `F5_remaining_label_transplant_ACCEPTED`.

## R4 — P2 — label immutability/lineage is not enforced across the corpus (F6)

Locations: `tools/v11_trajectory_contract.py:340`, `:359`, `:488`, `:555`.

The original family-change counterexample still passes through the integrated
API: a FINAL HIGH label is revised to LOW with a new version one second later,
changing the partition from ten buckets to two. Neither lineage nor admission
requires the target/family/partition to stay stable.

Registries are supplied independently per example and are not bound to the
result or rechecked by `validate_corpus`. Two examples validated with separate
registries can reuse the identical label-version hash with different winners;
both are accepted into one corpus. The registry's digest also omits timestamp
origin/health/uncertainty. A digest-shaped identifier is not a content binding.

Required: enforce target/family/partition invariants through the lineage, bind
all label/provenance content to its version, and check uniqueness/conflicts at
the integrated corpus boundary independently of caller registry reuse. Apply
explicit as-of revision selection rather than trusting a supplied final tail.
Use synthetic delayed/corrected-label and cross-example tests.

Probes: `F6_original_family_change_STILL_ACCEPTED`,
`F6_remaining_separate_registry_rewrite_ACCEPTED`.

## R5 — P2 — capture/clock provenance still cannot represent or verify required evidence (F7)

Locations: `tools/v11_trajectory_contract.py:86`, `:104`, `:136`, `:420`, `:486`.

`Timestamp` still contains only number/origin/uncertainty/health assertions.
`CaptureRef` still contains only two digests, a string containing `private`,
and a boolean. There is no evidence reference tying clock health or receipt
attestation to the bytes; no request start/end, HTTP outcome/range/header,
sealed/partial state, decode/extraction manifest or code/policy/dependency
binding. This was explicitly part of the original F7 required correction,
not a newly imposed operational acquisition requirement.

The registry now hashes byte/index/value content, which repairs the original
shared-registry probes. But changing extraction grid and receipt timestamp
under the same capture coordinates is still accepted on replay through the
same registry. Fresh registries also remove cross-example history, and the
corpus cannot reconstruct capture facts from summaries. No raw-byte verifier
or injected evidence verifier is invoked.

Required: typed content-bound capture/provenance manifests, with synthetic
bytes and an injected verification interface; enforce receipt/clock dependency
and metadata binding through actual admission. Preserve the implemented tzdata
pin and test that geometry uses the pinned timezone source. Actual provider
attestation and real collection remain later gates; schema/evidence binding
belongs in this offline gate. A hash asserts integrity, not trusted time.

Probe: `F7_remaining_metadata_rewrite_ACCEPTED`; schema field inventories are
captured in the independent JSON evidence.

## R6 — P2 — the new capture key conflates raw messages with station extractions (F7 regression)

Locations: `tools/v11_trajectory_contract.py:420`, `:486`.

The registry key is `(provider,run_date,cycle,member,forecast_hour)`, but its
value digest now includes `value_native_k`. The same forecast grid legitimately
has different values at different stations. A second station extracted from
the same raw message, with its own station version and temperature one Kelvin
higher, is rejected as `IMMUTABLE_CAPTURE_CONFLICT` when the required shared
registry is used. This blocks a multi-station corpus. Using separate registries
to evade it would reopen replay conflicts rather than repair identity.

Required: separate immutable raw-response/message identity from decoded
station/grid/extraction identity. Permit multiple legitimate extractions of
one immutable message; reject semantic rewrites of the SAME extraction.
Test both stations together with shared corpus evidence, plus changed bytes,
index, decoder/grid/station binding, and same-extraction replay controls.

Probe: `F7_new_legitimate_second_station_REJECTED`.

## R7 — P2 — coverage policy is neither immutable nor content-bound; corpus fallback remains undefined (F8/F4)

Locations: `tools/v11_trajectory_contract.py:231`, `:252`, `:524`, `:560`.

`CoveragePolicy.expected` is a mutable dictionary inside a frozen dataclass.
`policy_id` is any caller-supplied SHA-shaped string, never a verified digest
of the policy. Removing every hour-24 point correctly fails the original
expectation; replacing `policy.expected['GEFS']` with an hour-12-only expectation
under the SAME policy ID then passes. A full-coverage row and this shortened
31-point row pass together in one corpus as if the protocol were frozen.

Required providers are now visible at example level, but missing IFS/AIFS
still permit a successful example without a frozen fallback rule, and the
corpus report discards the missingness. Only one provider can be represented
per example; adding other providers under the same station/day collides with
the duplicate guard. No preregistration/freeze time, native cadence/window,
run-selection/freshness or outage/fallback policy is represented.

Required: deeply immutable policy content with recomputed identity bound to
the frozen corpus/protocol and availability; enforce run selection/freshness,
fixed native expected sets, provider completeness or explicitly preregistered
fallback. Define one multi-provider outcome example or an explicit grouping
contract. Report missingness/refusals over the full requested cohort without
counting providers as independent days. Same-ID policy-content mutation MUST
be tested; changing the ID voluntarily does not prove this property.

Probes: `F8_original_shrinking_same_policy_STILL_ACCEPTED`,
`F8_example_absence_fixed_corpus_absence_STILL_OMITTED`.

## Compatibility, safety and next action

Newer main differs from implementation base `cdbc95c` only in the three
navigation ledgers (341 added lines); no source conflict was found. The
builder and review trees remain clean at exact `2d116af`; no implementation
merge, adapter, fit, collection, real admission or gate-3 action occurred.

The independent combined GEFS/SHADOW release driver 781650 and pytest 783254
were still active, with the full log advancing through 76%; no terminal result
was present at that check. It was not restarted or duplicated. PAPER scanner
514629 was active with zero restarts; V10 and V11 execution/controller checks
were inactive, weather execution masked, protected authority paths absent.
Latest commissioning writes inspected were watchdog status, not forward
qualification. Disk was 3.1 GiB free; memory about 728 MiB available. AxiomTrade
was observed only as host resource load. No service or protected state changed.

Next: substantive Sonnet/high repair of R1–R7 in the SAME preserved builder
worktree, using this review's independent counterexamples as acceptance cases.
Avoid another nominal-wrapper-only pass: fix binding at the public corpus
boundary and model distinct raw/extraction/target/policy identities explicitly.
No new real-admission capability. Run focused/affected tests, commit the repair,
then obtain fresh independent exact-commit review. Keep the separate release
worker running and recover its actual terminal before any integration decision.

Score remains **91/200 (45.5%), formal 1/50; NOT_READY_TO_FUND**.

R09_GATE2_REVIEW_CHANGES_REQUIRED
