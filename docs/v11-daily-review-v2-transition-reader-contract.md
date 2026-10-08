# Offline daily review v2 transition-index candidate verifier

`daily_review_v2_transition.py` is a second, separate, pure verifier. It does
not modify `daily_review_v2.py`: the existing five-key v2 index, its reader,
and the legacy v1 `StationRegistry` refusal of any v2 shape are all unchanged
and remain exactly as previously reviewed. This module imports
`daily_review_v2`'s existing canonicalization, hashing and per-object schema
primitives rather than duplicating them; only the index-orchestration logic
(`_validate_registry`), which has a different exact key set, is a documented
duplicate of `_validate_index`'s body.

`verify_transition` returns a `VerifiedTransition` pin: internal graph
consistency only, never certification, runtime admission, commissioning,
financial authority, or permission to start a worker. It carries no
"authorized"/"approved"/"admitted" field. Approval and provenance objects
referenced by a transition are resolved, size-bounded and hash-verified; this
module does **not** authenticate who issued an approval, does not establish
root custody, and does not resolve the caller's original request bytes
(`request_sha256` is checked only for hash-pattern well-formedness). A missing
or absent approval/provenance reference refuses outright for every operation
except `GATE` (where `successor_provenance_sha256` and `candidate_approval_sha256`
may be `null`); there is no code path that returns a pin while silently
treating a missing approval as present.

The pin carries the new-selection pin fields (`review_id`, `review_sha256`,
`commission_sha256`, `candidate_approval_sha256` -- `None` for `GATE`, which
has no new selection), `next_state_sha256`, and `commissioning_subject_sha256`:
the digest of the transition content the commissioning approval is expected
to attest to (the transition with only its own `commissioning_approval_sha256`
field omitted, by the same omit-one-field pattern as `next_state_sha256`).
None of this claims that any approval was actually checked against that
subject digest -- only that the subject a future adapter must check it
against is computed here rather than left for every caller to recompute.

There is no caller-supplied current time and no clock/freshness check: a
previous revision of this module accepted and validated a `now` parameter
that was never used for anything, which was misleading, so the parameter is
gone. The only time-ordering guarantee is content-internal: for `MIGRATE`,
`ACTIVATE` and `SUPERSEDE` (which have a new selection), `cutover_at` must
fall within `[max(new_review.approved_at, new_commission.commissioned_at),
new_review.expires_at)`, refusing `CUTOVER_TIME_ORDER` otherwise. `GATE` has
no new selection and so no cutover ordering to check.

## Wire format

New version string: `alpha_v11_certification_reviews_v2_transition_1`. The
index has exactly six fields: the five existing v2 fields plus
`supersession_objects`, a one-element list of exactly
`{transition_id, transition_sha256}`. There is no empty-list bypass.

`next_state_sha256` is the SHA-256 of the canonical index with only
`supersession_objects` omitted; it is recomputed and compared, never trusted
from the transition object. The transition commits to this core; it is a
refusal (`SELF_HASH_CYCLE`) for any field of the transition to contain either
its own `transition_sha256` or the completed index's full `registry_sha256`.

The transition object (`alpha_v11_daily_transition_v1`) has exactly the
eighteen fields in the adjudication report
(`/tmp/alpha-daily-v2-schema-adjudication-20261008.report.md`, "Exact offline
wire contract for the next sole writer"). Operations are `MIGRATE`, `ACTIVATE`,
`SUPERSEDE`, `GATE`, each paired with a fixed reason set
(`OPERATION_REASON_PAIRING` otherwise). `MIGRATE` always uses
`previous_revision=0`, `next_revision=1`: in this slice it is treated as a
single, one-time transition that establishes the extended index with exactly
one review and one active selection, bound to exactly one legacy v1 manifest
member via `old_selection.kind == "LEGACY_UNBOUND"`. **While no reviewed
migration/gating plan for more than one legacy member exists, `MIGRATE`
refuses outright (`LEGACY_MANIFEST_MUST_HAVE_SINGLE_MEMBER`) unless the raw
v1 manifest has exactly one member.** This closes the two gaps an earlier
slice left open: a second legacy member silently losing authority with no
GATE transition and no record, and a v1-ambiguous duplicate-KEY member
(which the legacy `certification.assess` matcher would treat as granting no
authority at all) being named as a migrated predecessor anyway. A manifest
with more than one member needs its own later, explicitly reviewed
multi-member migration design; this module does not invent one.

For `ACTIVATE`/`SUPERSEDE`/`GATE`, the predecessor must itself be a trusted,
already-extended (six-key) index at `previous_revision`; this module validates
one bounded edge from that supplied predecessor, including that every
predecessor review ID/hash is preserved unchanged and every active selection
other than the one at `semantic_key` is byte-identical before and after. It
does **not** re-walk or re-verify the predecessor's own transition — that is
assumed already checkpointed, per the adjudication's "one bounded edge at a
time from an explicitly trusted verified checkpoint."

**`ACTIVATE` and `SUPERSEDE` refuse `HISTORICAL_REVIEW_REUSE`** if the new
selection's `review_id` or `review_sha256` already appears anywhere in the
predecessor's `review_objects` -- whether that review is still active,
already superseded, or was just removed by a `GATE`. Content consistency
alone cannot tell a fresh validated re-review from a replayed historical
one reusing its original commission and candidate approval, so this module
requires the review to be new to the history; it never resumes a
superseded or `FAIL_CLOSED`-gated review automatically. A genuine rollback
needs its own new monotonic review with fresh approval, exactly as for any
other reselection.

**`SUPERSEDE` with `reason=SPARSE_SEED_NEW_GENERATION` refuses
`SPARSE_SEED_REQUIRES_NEW_GENERATION`** unless *both* the new review's
`generation_id` and its `generation_descriptor_sha256` differ from the
existing selection's. A sparse-seed supersession is specifically a new
generation with new underlying content; a new `generation_id` alone with the
identical descriptor is not a different generation, it is the same content
relabeled.

## Bounds

Transition and metadata objects share the existing 256 KiB `MAX_OBJECT_BYTES`
bound. A transaction's metadata graph (the transition object, predecessor
evidence, successor provenance, candidate approval, commissioning approval,
and the incomplete-interval evidence, plus any further hash-shaped string
inside any of those that is itself present in the object map) is walked with
an explicit cycle guard and a 256-object / 16 MiB aggregate bound
(`METADATA_GRAPH_BOUND`, `CIRCULAR_EVIDENCE`). Candidate approval content is
additionally checked for one specific circularity: it must not contain the
hash of the very review it is supposed to predate.

## Exception typing

Every refusal raises `reader.ReviewV2Error` with a specific code; no
`RecursionError` or `TypeError` escapes from malformed-but-canonical input.
The leaf-string walk used for the metadata graph and the self-hash-cycle
check is iterative, not recursive, so a canonical, hash-verified, size-bounded
object that nests thousands of levels deep cannot overflow Python's
recursion limit before a typed refusal is reached. `operation`, `reason`,
`incomplete_interval.status` and `provenance.release_commit` are type-checked
(`type(...) is str`) before any `frozenset` membership test or regex match,
so a non-string value for any of them refuses with its ordinary code
(`UNKNOWN_OPERATION`, `UNKNOWN_REASON`, `INCOMPLETE_INTERVAL_STATUS`,
`PROVENANCE_RELEASE_COMMIT_SCHEMA`) instead of raising an untyped `TypeError`.

## What this module does not do

No filesystem publisher, no root custody adapter, no migration executor, no
manager/preparer wiring, no commissioning. No production provenance/approval
authenticity adapter — `successor_provenance_sha256` and
`candidate_approval_sha256` content beyond the `PROVENANCE` object's own
declared shape is accepted structurally and hashed, never authenticated.
`request_sha256` is not resolved against any supplied request bytes in this
slice (there is no reservation/preparation-record model here); a future
filesystem transaction engine must bind it. All of this remains separate,
later, authorized work, exactly as scoped by the adjudication report's "Next
sole-writer boundary."
