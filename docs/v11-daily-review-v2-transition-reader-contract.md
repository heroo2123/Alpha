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
seventeen fields in the adjudication report
(`/tmp/alpha-daily-v2-schema-adjudication-20261008.report.md`, "Exact offline
wire contract for the next sole writer"). Operations are `MIGRATE`, `ACTIVATE`,
`SUPERSEDE`, `GATE`, each paired with a fixed reason set
(`OPERATION_REASON_PAIRING` otherwise). `MIGRATE` always uses
`previous_revision=0`, `next_revision=1`: in this slice it is treated as a
single, one-time transition that establishes the extended index with exactly
one review and one active selection, bound to exactly one legacy v1 manifest
member via `old_selection.kind == "LEGACY_UNBOUND"`. Any other legacy member
would need its own later `ACTIVATE` (it has no prior v2 selection, so
`old_selection` would legitimately be absent) under an explicitly reviewed
migration/gating plan; this module does not attempt to migrate more than one
member per transition and does not invent a bulk-migration protocol.

For `ACTIVATE`/`SUPERSEDE`/`GATE`, the predecessor must itself be a trusted,
already-extended (six-key) index at `previous_revision`; this module validates
one bounded edge from that supplied predecessor, including that every
predecessor review ID/hash is preserved unchanged and every active selection
other than the one at `semantic_key` is byte-identical before and after. It
does **not** re-walk or re-verify the predecessor's own transition — that is
assumed already checkpointed, per the adjudication's "one bounded edge at a
time from an explicitly trusted verified checkpoint."

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
