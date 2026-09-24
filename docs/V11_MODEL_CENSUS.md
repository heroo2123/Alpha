# Multi-step fresh forecast census

The typed candidate now shares one GEFS worker and one scheduled collector with
the census worker. For an explicitly required MODEL source, collection reserves
a separate queue epoch before any raw response. `v11/model_census.py` obtains at
most one exact field per step, retains partial state and reconciles interrupted
requests. An epoch expires within the route, source, pending-update and 1800-second
worker limits; its absolute API ceiling is 3600 seconds. A new loss generation
invalidates it. No event work claim or lock spans the full collection period.

While a valid epoch is collecting, ordinary evaluation waits for that event and
the auxiliary GEFS job yields its collection slot. Safety ticks, cancellation,
other events and bounded discovery/audits continue. A prior interrupted auxiliary
operation is reconciled before starting a new epoch. The source's existing
backoff and one-second successful-request spacing remain in force.

Once all 31 members and local-day brackets are present, the ordinary short census
claim collects fresh books and required observations. It assembles the model
and checks the exact preparation plan, every field/raw hash and receipt, current
constituents, rule state, original issue age and current loss generation. Model
raw receipts must follow the preparation; books/observations must follow the short
claim. Aggregate publication follows that claim without renewing earlier input
receipt times. A later same-run capture may replace the current model only when
every constituent field is newer than the previous aggregate; history remains
immutable. The auxiliary worker adopts an exact current completed path without
refetching it or issuing a redundant aggregation.

A prepared epoch does not clear a gap. Only successful complete coverage and
normal queue completion do so. Late loss, stale input, changed rules/fields,
expired work or insufficient collection capacity remains GATED. Repeated losses
can prevent progress; the implementation does not extend a deadline or relax
freshness to manufacture readiness. Other required forecast providers still need
their own complete adapters. No source truth, calibration, strategy admission,
fill, independent guardian or deployment acceptance follows from census coverage.

An actual local integration test exposed the two-second assembly limit: repeated
per-member SQLite scans exhausted it. `EvidenceStore.source_batch` now reads a
consistent snapshot with a two-second query/decoding budget, 8 MiB decoded-body
budget, at most 900 explicitly requested rows / 512 channels and 1000 total decoded
rows. The aggregate MODEL head still guards publication/admission atomically.
Existing 64-reference decision/CAS limits and the two-second assembly limit stay
unchanged. No database schema or service configuration migration is performed.

Verification: 21 new model-census/source-view/candidate cases; final related suite
**241 passed in 78.97 s**. A complete mocked multi-step collection replaces an old
model with fresh raw evidence, finishes under the original short claim and
preserves both versions. Other cases cover invalidation, original timing, source
backoff, concurrent/interrupted work, no duplicate auxiliary collection and safety
scheduling. The earlier 76-pass run preceded the final auxiliary adoption change.
The full regression passed **3566 tests, four existing warnings, 283.92 s**
with all 806 tracked inputs unchanged; exact tree/resources are in the checkpoint.

Initial new tests found an overlong request ID (corrected without raising the
80-character collector limit), the assembly time-bound problem above, and a
fixture incorrectly demanding zero pre-existing coordinator records. The fixture
now checks that those records remain unchanged. No bound was enlarged to make a
test pass. All network and clock fixtures here are explicitly synthetic; actual
source availability/packing and host isolation remain unverified.

V10 is unchanged and its maintenance is deferred. This work does not authorize
deployment, money movement or financial activation. **NOT_READY_TO_FUND**.
