# Gate 3 offline I/O interface candidate

This is an **author candidate only**, extending the held `1693dd5` strict
validator in its existing isolated worktree. It preserves the merge hold and
grants no G3-L, network, SHADOW, learner, promotion, or financial permission.

`tools/v11_r09_gate3_offline_io.py` supplies four offline components:

1. `SyntheticExchange` and `consume_synthetic_response` bind a frozen request
   ID to preloaded bytes, charge every delivered chunk through the existing
   `DurableBudget`, and verify public peer, TLS/redirect facts, exact 206 range,
   object length, one bounded RFC 9110 strong ETag, bounded headers, and identity encoding. This is
   **not a real transport**: there is no socket or URL opener. Any future adapter
   must independently enforce DNS resolution, TLS and peer binding, deadlines,
   headers, no ambient credentials/proxies/cookies, provider restrictions, and
   streaming reads under the reviewed budget.
2. `decode_full_grid_station` parses a bounded single GRIB2 field on a regular
   grid, checks externally frozen hashes for sections 1–6, and extracts a
   nearest station point within 50 km using simple or IEEE packing. Template
   3.0 Di/Dj octets and endpoints are checked for all four supported scan modes,
   including rectangular grids. It rejects
   CCSDS, bitmap, unknown grid and unsupported packing. Section pins are not
   vendor release evidence; future G3-L must bind source-specific run, member,
   parameter, level, grid, packing and build identity to a genuine dossier.
3. `ClockSequence` checks an externally measured four-phase clock sequence for
   age, one-second uncertainty, boot identity, phase order, a common feasible
   UTC/monotonic offset, and every phase upper bound against the decision lower
   bound. A digest
   supplied by a caller does not itself prove synchronization; the actual
   unprivileged measurement method and raw evidence need separate review.
4. `ImmutableObjectStore` creates no directory, opens only an existing private
   root/object directory, seals one digest-named object with file and directory
   fsync, refuses duplicate names, and verifies owner/mode/link count/hash on
   read. It checks file type without a blocking FIFO/device open. After a
   possible publication or directory persistence failure, that instance refuses
   reads and seals and preserves the remaining names and bytes for inspection.
   A newly opened nonempty store also refuses all reads and seals. This is a
   deliberate conservative recovery refusal: the current interface has no
   persistent successful-seal provenance, local durability reconciliation, or
   original seal-clock record. Even successfully sealed objects cannot be read
   after reopen until a separate recovery design and review are completed.
   Filename, hash, link count, and a fresh fsync cannot recreate original seal
   clocks or certify causal eligibility. No real store was altered. Root custody,
   disk reservation and runtime report integration remain to be bound to the
   exact private manifest.

Synthetic adversarial coverage includes wrong 206 status/range/ETag, malformed,
weak, list and wildcard ETags with empty and ordinary strong-tag controls, private
peer, redirects, TLS failure, duplicate/oversized headers, delivered-byte
accounting and recovery, rectangular GRIB scan and endpoint cases, GRIB
pin/packing/bitmap/count/station failures, clock step/stale/boot and phase-bound
failures, object alias/mutation/mode/symlink, directory fsync, link/unlink,
interrupted publication, reopen and bounded FIFO failures, and one offline
end-to-end composition. The focused suite passed **45 tests** and the requested
offline I/O, launch, collector and GRIB suites passed **176 tests**. No real
provider bytes or timing evidence were acquired.

Before integration, a **different-model exact-commit independent review** must
assess these repaired interfaces and synthetic counterexamples, especially
response identity, journal failure accounting, full-grid bounds, clock trust
and store crash/recovery refusal. Store recovery semantics need a separate
design and review before any persistent store use. The previous `1693dd5` PASS
covers only its older bytes; `a804034` was independently rejected and this
repair is not an independent acceptance.
There is no approval to merge this candidate or to assemble a launchable
private manifest. Score remains **91/200 (45.5%), formal 1/50;
NOT_READY_TO_FUND**.
