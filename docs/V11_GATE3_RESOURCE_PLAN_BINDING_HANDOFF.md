# Gate 3 offline resource plan binding candidate

The original `calculate_offline_resource_budget` remains a pure proposal API
for an already validated in-memory payload. Its caller must still validate the
exact canonical V4 manifest bytes separately. The new
`calculate_validated_v4_offline_resource_budget(raw_manifest, frozen_plan, *,
repo, object_root, now_utc)` removes that external validation precondition for
one offline API. It does not construct a runtime `FrozenPlan`, reserve host
resources, or admit an attempt.

The new API accepts only built-in `bytes` within the V4 parser's 32 MiB limit.
It copies only the bounded, built-in request and event fields of the narrow
caller plan before validation. It passes those exact immutable bytes and the
required caller context to `validate_manifest_v4`, checks that the validator's
reported digest equals its own SHA-256 of those bytes, and parses the same
bytes with `parse_canonical` for the existing capacity recount. Failed V4
validation, digest disagreement, malformed canonical bytes, and a plan that
differs from the validated schedule refuse without a capacity report. Success
adds `validated_manifest_sha256` and
`manifest_validation=V4_VALIDATED_EXACT_BYTES`; its event binding explicitly
names the validated V4 field expansion. There is no file reread or alternate
manifest payload argument.

## Exact derivation

The V4 validator limits `cohort.events` to one or two distinct ordered HIGH/LOW
entries, and validates the ordered `schedule.requests` list and its FIELD
partition. The nonfixture `FrozenPlan.verify_validated_projection` requires the
plan's event sides to equal that cohort order and requires **each** event's
`field_request_ids` to equal the tuple of **all** FIELD request IDs in the
manifest schedule order. `CapacityPlan.for_requests` uses event count and each
field list's length to compute its aggregate node reserve. Therefore the
capacity-relevant event expansion is exactly derivable from those public,
already validated manifest bytes.

The offline calculator now refuses an event count different from the cohort,
or any missing, extra, reordered, duplicated, substituted, or malformed FIELD
member. It rejects protocol dictionary-key and mode subclasses before their
comparison hooks can mutate the caller's event list, and computes against a
bounded snapshot of the supplied event collection and member lists. It retains
the existing request/order, cardinality, link, numeric and
resource caps. On success `event_binding` is
`MATCHES_SUPPLIED_V4_MANIFEST_FIELD_EXPANSION` and
`capacity_covers_frozen_schedule=true` means only that its event-dependent
capacity arithmetic covers the V4 projection of the supplied manifest under
the stated external validation precondition for the original API. The new
API's same capacity arithmetic follows successful validation of its own exact
input bytes. Neither API claims that a caller supplied an actual, currently
verified runtime `FrozenPlan`.

The narrow input projection contains no event side, event ID, primary provider,
requested key, trial key, review digest, or full request metadata. Their
binding remains the runtime's separate responsibility. Swapping two identical
field-member arrays is indistinguishable in this projection and cannot change
the capacity calculation. Existing occupancy, delivered bytes and live host
resources remain unknown. `execution_authority`, `provider_authority` and
`resource_qualification` remain false; qualification credit remains zero and
G3-L remains NO_GO. No real SHADOW admission follows.

## Review checks

Focused tests cover one-event and two-event parity with `CapacityPlan`,
omissions, order and membership mutations, malformed event/cohort shapes,
bounded oversized mapping refusal, and unchanged no-qualification outputs.
The new entrypoint is exercised with the existing complete synthetic V4
fixture, an invalid canonical representation, a canonical candidate rejected
by V4 validation, mismatched and validator-time mutated plans, a wrong
validator digest, bounded early refusal, and zero authority. The fixture
builder is loaded without its optional pytest or provider-adapter imports;
the candidate uses local synthetic Git and object files only.

The reported capacity remains a fresh-root paper estimate. It does not check
existing journal occupancy, delivered bytes, live disk or memory, actual
runtime `FrozenPlan` custody, or the provenance and freshness of caller-supplied
`now_utc`. The V4 validator's local repo/object checks still require those
arguments to identify the intended offline evidence context; this candidate
does not touch real private fixtures. It performs no network, provider,
resource allocation, host clock, financial, or execution action.
Run `python3 -m unittest tests.test_v11_gate3_offline_resource_budget` and
`python3 -O -m unittest tests.test_v11_gate3_offline_resource_budget`, then
`git diff --check`. Independent different-model exact review is still required
before integration; this document is an author handoff, not approval.
