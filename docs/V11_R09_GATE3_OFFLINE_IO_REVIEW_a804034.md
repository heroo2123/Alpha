# Independent Gate 3 offline I/O review — a804034

**CHANGES_REQUIRED: five P2 findings. Candidate remains unmerged under its hold.**

Astra/high independently reviewed Sol/high author commit
`a804034670becfe37082f49fa7a060955d8808f8`, tree
`8ab1882aa9228c6d4ba89057ca883a69355b1f19`, in
`/tmp/alpha-v11-r09-gate3-strict-offline-20260930`. The candidate was clean
and unchanged throughout review. This verdict covers the new offline interfaces,
not real I/O, launch permission or the already reviewed parent `1693dd5`.

## Evidence

- Independently reran the offline I/O, strict launch, collector and GRIB suites:
  **148 passed in 2.94 seconds**.
- [Independent synthetic probes](V11_R09_GATE3_OFFLINE_IO_REVIEW_a804034_probes.py):
  **20 passed in 1.18 seconds**. These include **12 defect reproductions and
  8 positive/failure-handling controls**. A passing defect probe means the bug
  reproduced, not that the candidate is acceptable.
- Logs: `/tmp/alpha-v11-gate3-io-review-a804034/{affected,independent}.log`.
  The [completed terminal](V11_R09_GATE3_OFFLINE_IO_REVIEW_a804034_terminal.json)
  binds the report, probes and logs by hash.
- Reproduce from the exact candidate worktree with `PYTHONPATH=.` and the existing
  `/home/alphaadmin/AlphaV11_Dev/venv/bin/python -m pytest -q`, passing the absolute
  probe path above. All fixtures and fault injections are synthetic. The FIFO
  probe terminates only its own disposable child after a bounded check.

## P2-1 — GRIB grid increments are reversed

`tools/v11_r09_gate3_offline_io.py:215-216` reads longitude increment from
zero-based bytes 67:71 and latitude increment from 63:67. Template 3.0 defines
Di at octets 64–67 and Dj at 68–71, respectively; see the
[ECMWF template definition](https://codes.ecmwf.int/grib/format/grib2/templates/3/0/).
The candidate's square-grid author fixtures mask the reversal.

An independently encoded 2-by-2 grid with Di=0.5 degrees, Dj=0.25 degrees and
correct endpoints is rejected under all four supported scan modes. A malformed
grid whose endpoints follow the reversed interpretation is accepted and returns
293 K at (33.5, -83.75) with claimed zero displacement. Four square-grid controls
pass. Fix the octets; require valid rectangular grids to decode and inconsistent
endpoints to reject, including reversed scans. Keep all point/packing/byte bounds.

## P2-2 — Causal eligibility omits earlier bounds and cumulative clock inconsistency

`ClockSequence.record` at lines 304–310 compares adjacent offsets only;
`causal_before` at lines 313–318 checks only the final seal upper bound.
The accepted protocol section 5 requires every dependency receipt/readiness
upper bound to be no later than decision lower bound.

A strictly advancing sequence with UTC (100,100.1,100.2,100.3), monotonic
(1,1.1,1.2,1.3), and uncertainty (1,1,0.1,0) returns true for decision 100.4,
although body receipt upper bound is 101.1. Separately, UTC (100,99,98,97),
monotonic (1,1.1,1.2,1.3), uncertainty 1 throughout passes every adjacent check
and returns true for decision 98 despite having no common UTC/monotonic offset
interval and an earlier body receipt upper bound of 100.

Check every required phase's conservative upper bound and reject globally
inconsistent/stepped sequences under the frozen uncertainty policy. Preserve
external measurement/attestation requirements; hashes remain no proof of sync.
A stable sequence correctly passes an early-enough seal and rejects a late one.

## P2-3 — Directory durability failure leaves apparently ordinary stored objects

`ImmutableObjectStore.seal` at lines 372–381 links the digest name, then fsyncs
and cleans up. Injecting failure into every directory fsync makes seal raise,
but cleanup removes the temporary name, leaving a single-link digest file.
`read` accepts it on the same object while fsync still fails, the object permits
another seal once the fault is removed, and a fresh instance accepts the failed
object without any durability reconciliation. There is no uncertain-state gate.

The probe demonstrates missing provenance/state, not a claim that a simulated
exception itself caused physical power-loss data loss. File-fsync failure before
publication correctly leaves no digest object (positive control).

Make failure after possible publication explicit and fail closed. Recovery must
preserve orphaned bytes, distinguish uncertain publication from a successful
seal, and perform a reviewed local durability reconciliation or refuse it;
file existence/hash/link count alone cannot certify a successful durable seal.
Do not fabricate original seal clocks on recovery. Add directory-fsync,
link/unlink, interrupted-publication and reopen probes. No silent cleanup of
unfamiliar work or deletion/reclamation of real stores is authorized.

## P2-4 — A digest-named FIFO blocks before identity validation

`ImmutableObjectStore.read` line 386 opens with blocking `O_RDONLY` before
checking that the inode is a regular file. A private mode-0600 FIFO named as a
valid digest, with no writer, blocks indefinitely in open. The independent child
announced readiness, remained blocked for the bounded one-second observation,
and was then terminated. This prevents the promised file-type rejection and
bounded offline finalization. Open in a way that cannot wait for FIFO/device
I/O before validation (the older journal already uses `O_NONBLOCK`), then retain
all owner/mode/link/hash checks. Add bounded negative tests for nonregular files.

## P2-5 — Equality to a pin does not establish a strong ETag

`verify_response` lines 104–106 checks equality, length and absence of a `W/`
prefix, but not entity-tag grammar. Matching expected/response values `*`,
`not-quoted`, and `"one", "two"` all pass field identity checks. These are not
single strong entity tags under [RFC 9110 section 8.8.3](https://www.rfc-editor.org/rfc/rfc9110.html#section-8.8.3).
Reject malformed/weak/list/wildcard values at this interface; preserve exact
comparison of one valid bounded quoted tag. Test empty opaque tags and ordinary
valid tags as controls. This does not establish that any actual provider supplied
an invalid tag or that a syntactically valid tag attests publisher truth.

## Scope and handoff

Independent delivered-chunk journal write and fsync fault controls passed:
the two received bytes stay counted exactly once, the object is permanently
failed, and restart holds the eight-byte reservation. The remaining response
status/range, provider/body caps and synthetic no-replay author cases pass.
No real transport, actual clock measurement, source-specific semantic dossier,
private manifest, exclusive runtime integration or G3-L acceptance exists.
This bounded review is not an exhaustive acceptance of those deferred surfaces.

Repair only the five findings and their tests/handoff in the same isolated
worktree, then request fresh different-model review of the new exact commit.
Do not merge or publish the held candidate. The review itself makes no code
changes and confers no network capture, SHADOW, learner, promotion or financial
authority. Public format documentation was consulted; no weather/provider data
was acquired. The private master still matches its pin. V10, PAPER scanner and
execution are inactive; execution is masked. Protected authority remains absent.
Only watchdog statuses are newer than the 06:43 commissioning artifact.
The prior release failure remains closed by accepted `6ec371e`.
**91/200 (45.5%), formal 1/50; NOT_READY_TO_FUND**, unchanged.
