# Gate 3 offline resource plan binding candidate

This candidate changes only the pure, proposal-only capacity comparison in
`tools/v11_gate3_offline_resource_budget.py`. It does not validate a manifest,
construct a `FrozenPlan`, reserve host resources, or admit an attempt. The caller
must separately validate the exact canonical V4 manifest bytes with
`validate_manifest_v4` and supply their parsed payload. This module does not
verify that the caller did so.

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
the stated external validation precondition. It does not claim independent
validation or that a caller supplied an actual, currently verified `FrozenPlan`.

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
The member-list bound is checked before allocating a snapshot of caller data.
Run `python3 -m unittest tests.test_v11_gate3_offline_resource_budget` and
`python3 -O -m unittest tests.test_v11_gate3_offline_resource_budget`, then
`git diff --check`. Independent different-model exact review is still required
before integration; this document is an author handoff, not approval.
