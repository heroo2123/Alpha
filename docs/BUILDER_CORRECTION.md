# Corrective pass after failed live acceptance

Baseline: `1971089280f945283bb08b1349554a3599988ee2`.
Scope: one repository correction and one replacement live acceptance. No VM was
started or modified during this work. Promotions remain **0**, financial delivery
remains disabled, and no detector, fee, economic threshold or eligibility policy
is changed by this correction.

## Root causes and evidence

The 45-minute live run accepted no generations. Outbox count stayed 436, every
process had zero swap/OOM/restarts, and the host retained at least 380.7 MiB
MemAvailable. That is a contained availability failure, not a successful shadow
deployment and not evidence of host-wide RAM exhaustion.

**The strongest explanation for the first early SnapshotError is the 16 MiB
Gamma response limit with 100-event pages.** A bounded public replay on September
9 reached page 99 with **20,789,112 response bytes and 6,622 child markets**. This
exceeds the old 16,777,216-byte parser limit immediately after the same 98-page
progress boundary reported in the failed run. Captured response SHA-256:
`84237e39aa81d23d02f709cf23b7b4cdc771f0472ccb59d29b7e1deb1bcc5f14`.
The original failure message was discarded, so this is a reproduced concrete
defect and highly likely attribution, not recovery of the lost historical reason.

Other observations distinguish the competing hypotheses:

| Measurement | Result | Implication |
|---|---:|---|
| Original-format storage, first 15 pages | 5,959,680 bytes | Small compared with 256 MiB file cap |
| Original-format storage, separate pages 16–93 replay | 37,138,432 bytes | Combined partial growth about 43.1 MB; no file cap failure |
| Largest compact parent in that replay | 36,984 bytes | Below former 256 KiB shared row/parent cap |
| Largest materialized market in that replay | 6,749 bytes | Below unchanged 256 KiB market cap |
| Largest successful 100-event page before page 99 | 13,912,490 bytes | Already close to the parser limit |
| Captured page 99 split into four 25-event envelopes | 5,609,351; 5,694,268; 3,978,986; 5,506,991 bytes | All pass corrected parser/materializer without increasing byte cap |
| Corrected isolated captured-page replay peak RSS | 77,639,680 bytes / 74.0 MiB | Within unchanged builder hard bound |

This was a bounded replay, not a complete live traversal or executable price
evidence. The page-99 partitions contain no selected detector rows; they test the
large source graph and full inventory path. The revised complete synthetic test
separately exercises selected rows, parent reconstruction and publication.

The original 47 MiB synthetic measurement omitted the dominant workload: buffered
Gamma JSON, its decoded Python graph, rich unused fields, long identifiers,
closed children and dense event clusters. The old loop also retained the previous
page while awaiting/parsing the next. Selected raw dictionaries were copied, and
the original parent was repeatedly encoded/read/compared for each selected child.
These allocation and processing costs were unnecessary even though parent data
was already stored only once on disk. Python allocator high-water and SQLite/file
cache pressure explain why a small selected subset did not imply a small builder.

The later deadline failures are consistent with **per-cgroup reclaim and I/O/CPU
deprioritization**, despite spare host RAM. MemoryHigh=112 MiB was below observed
real builder demand; MemoryMax=160 MiB, Nice=10 and idle I/O compounded that risk.
The old telemetry cannot prove which of reclaim, I/O scheduling or CPU scheduling
dominated. No claim is made that the precise scheduler contribution was measured.
The correction removes the redundant workload and records the missing counters.

Repeated Git lookup was a separate confirmed availability defect. `rev-parse`
with a three-second timeout ran for every generation and could fail before a new
status was written. Release/dependency authority now runs once at process startup.
Subsequent builds use the pinned SHA plus cheap marker, detached HEAD and tracked
file-signature checks before construction and publication. Source/release changes
fail closed; no Git subprocess runs during or between generations.

## Representation and processing correction

* **25 events/request**, one in flight, minimum 50 ms between request starts.
  Natural cursor exhaustion, continuation validation and inventory caps remain
  mandatory. The 16 MiB decompressed response cap is unchanged, with bounded
  64 KiB parser chunks. A single unusually large event can still fail the cap;
  it will report `PAGE_BYTES_CAP`, never disappear through truncation.
* Release the previous page before fetching another; release raw response bytes
  after JSON parsing; reuse the pure materializer; reference selected raw rows
  instead of copying all their fields. Build the original semantic parent once.
* Snapshot v2 keeps the same semantic parent fields, complete original child
  membership (including closed/excluded children), market reconstruction fields,
  source update strings and local page receipt times. The selection policy version
  is unchanged. Zlib level 1 is lossless; no semantic text is shortened or removed.
* Store one compressed parent per event and one compressed materialized market
  per row, with declared decoded byte lengths. Identity hashes use 32-byte blobs.
  The large tables use `WITHOUT ROWID`. Compare parent hashes once per event,
  rather than repeatedly decoding/serializing the full parent for each child.
* Keep the **256 MiB generation file** and **256 KiB market row** limits. A separate
  **4 MiB semantic parent** limit permits high-cardinality complete membership;
  the previous shared 256 KiB limit incorrectly assumed a parent was the size of
  one market. Add a **128 MiB total decoded payload** limit. Readers independently
  validate aggregate/per-row sizes and reject malformed, truncated, trailing or
  over-expanding compressed records. These are capacity limits, not a promise
  that arbitrary Python graphs have identical byte size.
* The private build database uses a 4 MiB cache, no rollback journal, and no
  per-page durability writes. **Any build error abandons it**; it is never resumed
  or read by the scanner. Final publication still closes/validates the database,
  fsyncs the file, renames/fsyncs the directory, verifies the reader contract/hash,
  checks release/deadline authority, then atomically publishes/fsyncs the pointer.
  Before/after-publication process-death tests preserve complete authority.
  Accounting database WAL, schema, backup and restore behavior are unchanged.
* Progress JSON is fsynced at most every five seconds rather than on every page;
  initial, successful and failed states are recorded immediately.

The request ceiling is below the published Gamma events limit of 500/10 seconds.
This does not guarantee that every route or IP will always get that service rate.
[Polymarket rate limits](https://docs.polymarket.com/api-reference/rate-limits).
SQLite explicitly warns that journal mode OFF can corrupt a failed transaction;
that is acceptable only for this disposable private file, never the account DB or
accepted generation. [SQLite journal mode](https://www.sqlite.org/pragma.html#pragma_journal_mode).

## Resource budget

| Builder setting | Failed candidate | Corrected candidate |
|---|---:|---:|
| Nice | 10 | 5 |
| CPUWeight / IOWeight | 10 / 10 | 50 / 50 |
| I/O scheduling | idle | best-effort, priority 6 |
| MemoryHigh | 112 MiB | 144 MiB |
| MemoryMax | 160 MiB | **160 MiB unchanged** |
| MemorySwapMax | 0 | **0 unchanged** |

The scanner retains higher priority and its 480 MiB hard bound; command worker
remains at 112 MiB. The aggregate slice remains MemoryHigh=560 / MemoryMax=640 MiB,
with zero swap. The live acceptance gate remains **combined <600 MiB and host
available >=128 MiB**. Individual maxima are not additive reservations. Raising
MemoryHigh avoids premature local reclaim; no process/host hard budget was raised.
I/O weights are scheduler-dependent hints, so actual concurrent completion remains
an acceptance requirement.

## Diagnostics the next run must retain

`builder-status.json` now includes an attempt ID, generation ID, producer SHA,
stage, counts, file/decoded/parent/market sizes, largest page/row/parent, network/
JSON/transform timings, process RSS/swap/CPU and cgroup memory/events/stat/pressure,
CPU throttling and I/O pressure. Unavailable counters are null, never invented zeros.
The final published file size remains available after the private filename changes.

`builder-failures.json` retains the most recent **16** failures and a lifetime
failure count across attempts and restarts, including failure-stage/resource
evidence. Each attempt records its own status even if initialization fails.
Journald provides a safe fallback if status writes fail, for example on ENOSPC.

Internal failures have fixed codes such as `PAGE_BYTES_CAP`, `GENERATION_FILE_CAP`,
`ROW_SIZE_CAP`, `PARENT_SIZE_CAP`, `DECODED_SIZE_CAP`, `DISCOVERY_CAP`,
`MATERIALIZED_CAP`, `INVENTORY_CAP`, `CURSOR_REPEAT`, `DUPLICATE_IDENTITY_CONFLICT`,
`BUILD_DEADLINE`, `PUBLICATION_DEADLINE`, `SQLITE_INTEGRITY`, `RELEASE_MISMATCH` and
`RELEASE_LOOKUP_TIMEOUT`. External failures map to fixed categories such as
`HTTP_TIMEOUT`, `HTTP_STATUS`, `HTTP_TRANSPORT` or `IO_ERROR`. Only bounded numeric
details are included. Transport access logs are disabled; exception text, remote
response bodies, URLs/query cursors and credentials are not diagnostic fields.

## Repository validation evidence

The revised separate-process scale test sends **934,765,538 bytes** through the
real HTTP/JSON parser: 23,200 events, 243,750 inventory rows, 195,000 active markets,
13,500 selected markets, 27,000 tokens and 800 hot tokens. It includes 60-child
dense events, selected markets in dense/sparse parents, closed children, long IDs
and dozens of unused Gamma fields. Maximum response is **5,715,809 bytes**,
slightly larger than the largest captured page-99 partition.

An engineering-environment run completed 929 pages in **74.3 seconds**, produced
a **25,612,288-byte / 24.4 MiB** generation, and peaked at **89,698,304 bytes /
85.5 MiB builder RSS** and **121,122,816 bytes / 115.5 MiB reader RSS**. The reader
continued using its accepted generation during the build and accepted the next
complete generation afterward. This is an offline capacity measurement, not an
e2-micro scheduling guarantee. CI publishes each matrix run's own capacity report.

The full pytest suite includes safe failure-code/history tests, initialization
failure cleanup, page graph release, compressed corruption/expansion bounds,
lossless large-parent reconstruction, immutable producer checks without Git,
bootstrap readiness, crash/flock/pointer atomicity, stale suppression, real
scanner-generation rollover and silent outbox regressions. Compile, all deployment
shell syntax, rendered systemd verification, exact dependency pins and consistency
are checked. GitHub Actions runs on Python 3.11 and 3.12. Use the exact green
candidate SHA supplied in the handoff, not a moving branch tip.

## Exact migration delta and final live acceptance

Follow the [full migration and 45-minute gates](SILENT_SHADOW_HANDOFF.md). The delta
from `1971089` is code plus v2 disposable snapshots plus reviewed builder scheduling
and MemoryHigh settings. There is **no account schema, secret, detector-promotion,
economic or financial-delivery migration**.

1. Keep all three services stopped. Preserve failed-run journals/status/snapshots
   and verify the normal online account backup, hashes and restore check.
2. Transfer/check out the exact replacement SHA detached through the established
   bundle route. Run `deploy/prepare-shadow-release.sh EXACT_SHA` in offline-bundle
   mode, then `deploy/setup-shadow-services.sh`. Verify release/preflight/schema
   authority and effective unit/drop-in settings. These helpers start nothing.
3. Archive the disposable v1 universe directory outside the active snapshot path;
   create a fresh mode-0700 directory owned by the service user. Do not change or
   delete the account DB, secrets or backup timer. Both snapshot versions cannot be
   mixed as authority. A pointer from another release is rejected, never relabeled.
4. In the separately authorized deployment session, start the **builder only** and
   start the 45-minute acceptance clock. Run the read-only `.venv/bin/python -m
   polymarket_scanner.universe_ready --timeout 900` in the prepared release environment.
   A missing/corrupt/stale/incompatible generation or <70% coverage is not ready.
   On failure, stop and retain evidence. On success, start scanner, then command.
5. Require the first accepted generation and **two later complete generations
   while scanning**. Each later build, including publication, must take <=600s.
   Bootstrap sequencing is not evidence that concurrent refresh works.
6. Retain the original gates: no partial acceptance or hard-stale interval; compute
   p95 <=10s/max <=20s; completed-scan gap <=60s; promotions 0; both financial latches
   false; outbox count unchanged; zero process swap/OOM/unexpected restarts;
   per-process RSS <160/480/112 MiB; combined <600 MiB; host available >=128 MiB;
   hot WS <=800; normal fresh coverage >=70%; overflow zero; feeds/schema/backup/
   release authority healthy. Record cgroup pressure and timing stages as well.
7. Any failed gate ends this acceptance. Stop the services and retain the reason
   codes, history and resource evidence. Do not increase memory, stale limits or
   deadlines, truncate markets or begin another transport experiment sequence.
   Failure of sustained concurrent capacity means rework same-VM isolation or seek
   separately approved capacity; it is not a silent-shadow PASS.

On PASS, continue silent evidence collection. Gamma coverage is still partial
(historically about 77%); unsupported sports scopes, weather calibration, contract
semantics, manual latency/legging risk and detector promotion evidence remain
separate limitations. This corrective release grants no real-money permission.
