# V11 unused pure journal checker slice — 2026-10-07

This slice is unreviewed and has no runtime caller. It adds
`polymarket_scanner.v11.forward_protected_journal.verify_interval` as a pure,
bounded checker of synthetic journal and logical catalog inputs. It returns a
typed `Refusal` for every input. `Coverage` is a reserved result type with no
construction path. Forward protected interval remains unproven; this code
grants no qualification or sample credit.

## Concrete version 1 test format

The journal is at most 1 MiB and consists of 1–128 canonical, sorted,
compact JSON envelopes, each at most 8 KiB, terminated by a newline. Every
envelope has exact base keys, `version=1`, `financial_authority=false`, one of
the ten amendment record kinds, a contiguous sequence and previous-envelope
SHA-256. It binds journal, session, boot, ledger, genesis, interval and
operation IDs, ledger frontier, row/dependency/witness digests and a finite
clock bracket. Kind-specific `detail` keys are exact. Duplicate/unknown keys,
wrong scalar types, noncanonical bytes and unsupported versions refuse.

The caller-supplied catalog has exact keys and at most 16 sorted unique ledger
IDs. Each declared member has a complete view with up to 256 contiguous rows.
Rows retain at most 4 KiB of exact opaque bytes as lowercase hex. Row SHA-256
and a genesis-derived prefix chain are recalculated. Every journal frontier
must match the corresponding catalog prefix and final high-water; every
declared member must have an enrollment in the journal. A checkpoint binds the
catalog digest. PREPARE/SEAL pairs bind fresh sequence, operation and row
digest; one pending preparation per ledger reserves its target, and a sealed
row cannot be sealed again. Admission, interval and operation IDs remain spent
after ABORT or SEAL, including after a lost acknowledgement. An exact lost-ACK
retry adds no journal record. An unmatched PREPARE refuses. GAP, INVALIDATION
and same-event rows in multiple members refuse.

All `TRANSITION` records return `TRANSITION_CONTRACT_UNDEFINED`. A generation
number and reason do not define an authenticated owner, affected scope or
before/after transition rule. This checker therefore makes no continuity
claim across a transition. Ordinary transition-free synthetic histories can
still reach the final `ANCHOR_UNAVAILABLE` refusal.

These are format checks, not an assertion that a caller supplied catalog is
complete or authentic. A jointly rewritten journal and catalog can claim a
shorter membership set or a restored valid prefix. Tail deletion can leave a
syntactically valid prefix. In those cases the final result remains exact
`ANCHOR_UNAVAILABLE`; the library never promotes a self-declared catalog or
hash chain into independent authority. Structural refusals take precedence
for malformed inputs. Nonempty `witnesses` return
`WITNESS_CONTRACT_UNDEFINED`: the amendment does not freeze a semantic witness
byte format or validation contract. A supplied `anchor` object is deliberately
ignored because this proposal has no commissioned independent anchor type or
verification rule. Even a synthetic caller claim of commissioning returns
`ANCHOR_UNAVAILABLE` after structural checks.

## Unresolved positive coverage requirements

Before any positive result can be designed, independent review must fix the
anchor and authentic catalog membership/completeness mechanisms; exact
dependency bytes and witness proofs; time and lease comparisons; semantic
admission, certification, model, safety and guardian re-derivation; disclosure
history; archive identity; and a target interval/request binding. This pure
format checker does not open a ledger, authenticate a custodian, confer host
authority or implement a qualification verdict. The unconditional
`_require_protected_interval` refusal in `shadow_commission.py` is unchanged.

Tests use only synthetic bytes. They exercise exact refusal codes under
ordinary Python and `python -O`, including malformed schema, sequence/hash,
frontier, catalog, cross-ledger and gate/new/replay/status controls. A later
independent different-model exact-commit review is required before this
candidate can be merged; this slice earns no qualification credit.
