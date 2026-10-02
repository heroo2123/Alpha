# Gate 3 A8 concrete composition preparation

**Offline preparation candidate only. G3-L remains NO-GO.** This change adds
`tools/v11_r09_gate3_a8_composition.py`, a canonical, fail-closed checker. It
does not construct a private V4 manifest, instantiate a real adapter, dispatch
a request, or return launch authority. The current V4 validator deliberately
rejects the mandatory unsupported real purpose mappings, so no real package
can pass this checker today.

## Boundary

The candidate package is canonical JSON with schema
`R09_GATE3_A8_COMPOSITION_PREPARATION_V1`. It binds exact manifest, FINAL
inventory, frozen plan, runtime-context, commit/tree and adapter-source
digests, a review timestamp, seven separately sealed A1–A7 evidence/review
pairs, and 20 distinct component evidence/review pairs: one for each of the
five adapters and each of the 15 provider/purpose contracts. Component review
records bind the exact adapter source or contract identity digest. An
independent A8 record uses schema
`R09_GATE3_A8_COMPOSITION_REVIEW_V1`; its **exact SHA-256 must be supplied
outside the candidate package**. The checker requires its `ACCEPTED` record to
bind the package, manifest, inventory, plan, commit/tree, all seven distinct
accepted prerequisite-review hashes, all 20 distinct accepted component-review
hashes, and the detached G3-L terminal hash.
This hash parameter is an external pin, not evidence that a reviewer is
independent or authorized. The owner must authenticate that fact separately.

The FINAL inventory must pass the existing 79-identity sealed-reference,
scope and freshness checker at the stated review time. Its manifest-byte and
digest entries must equal the supplied canonical V4 bytes, and its detached
G3-L terminal must be a typed `PASS` binding that manifest and exact runtime
commit/tree. The V4 validator must then accept the actual bytes and the
`FrozenPlan` must revalidate its exact projection. The mapping scope must be
`PROVIDER_REVIEW_REQUIRED`; synthetic pilots and placeholders refuse.
Every one of the 15 provider/purpose response contracts must resolve to
canonical `R09_GATE3_REAL_PURPOSE_CONTRACT_V1` bytes matching its provider,
purpose, GET origin, path grammar, source dossier and parser identity.
Opaque response-contract digests cannot satisfy this check.

The five live adapter roles are transport, clock, storage, resource probe and
decoder. Synthetic transport, `FakeClock` and `FakeResourceProbe` refuse.
Adapter classes/functions must come from reviewed `tools/` source files at
the pinned SHA-256, with exact qualified name and no instance-level override
of the relevant method. The loaded method code must equal code compiled from
those pinned bytes, and its executable identity must survive recheck. This
Python-level check does not provide A4's pre-import or native dependency lock.
The current private store must remain usable and retain its owner, directory,
manifest, reviewed descriptor and policy identity. Both the directory check
and the `_usable` health wrapper that gates it on `_failed`/recovery
classification are bound to reviewed source and rechecked immediately before
invocation, at adapter preparation and again at point of use, so an instance,
class, or in-place replacement of either cannot skip the directory check. This
is Python-level offline composition-check correctness; it is not A4's
transitive/native immutability boundary, a general native/runtime redesign, or
launch authority. A fresh clock sample must
match the reviewed boot, method, uncertainty and measurement-age bounds; a
fresh resource sample must cover the fixed floors plus prospective storage and
decoded reservations.
`recheck_a8_composition` repeats validation inside the acquisition window and
compares actual adapter object, class, method and code, source inode, boot,
storage descriptor/policy and storage-root identities against preparation. It
returns another ephemeral check result, not a runtime or permission token.

## Accepted input scope and outstanding evidence

The [identity criteria](V11_R09_GATE3_IDENTITY_ACCEPTANCE_20261001.md) define
A1–A8 and say no source/build/runtime qualification has been granted. The
[mapping repair review](V11_R09_GATE3_V4_PROVIDER_MAPPING_REPAIR_REVIEW_23c11e0.md)
accepts exact offline grammar and real-purpose refusal. The
[slice-3 review](V11_R09_GATE3_V4_SLICE3_REVIEW_6340cb4.md) accepts injected
offline runtime behavior, not real adapters or decoder qualification. The
[G3-L prep repair review](V11_R09_GATE3_G3L_PREP_REPAIR_REVIEW_f03d2fd.md)
accepts the repaired nonlaunchable inventory/planner after the
[earlier review](V11_R09_GATE3_G3L_PREP_REVIEW_3241abf.md) required changes.
The earlier
[composition review](V11_R09_GATE3_COMPOSITION_REVIEW_0b7209d.md) covers a
synthetic composition batch only. None can be promoted into a missing real
identity by a digest or this checker.

The typed A1–A7 records ensure presence, distinct review bytes and exact
binding. Their semantic truth, original provenance, reviewer independence,
complete dependency/native closure and pre-import A4 enforcement require
their own accepted artifacts and external verification. A Python-level
source-file recheck cannot make loaded native code immutable. A8 use also
requires an independently reviewed exact integration commit/tree and real
capacity, custody and clock evidence. The checker deliberately contains no
provider request or launch path. This candidate remains unmerged pending
upstream evidence and independent exact-commit review.
