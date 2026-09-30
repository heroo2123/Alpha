# Gate 3 v1 offline store restart author handoff

**REPAIRED AUTHOR CANDIDATE; fresh different-model exact-commit review required.**
This batch extends the held `7bc627e` Gate 3 restart candidate after the
[two-finding independent rejection](V11_R09_GATE3_STORE_RESTART_REVIEW_7bc627e.md)
(itself a repair of `74bd122` after its earlier
[five-finding independent rejection](V11_R09_GATE3_STORE_RESTART_REVIEW_74bd122.md)).
It grants no merge, publication,
provider capture, G3-L, SHADOW, learner admission or financial action. The
legacy `ImmutableObjectStore.seal(bytes) -> digest` remains session-only and
continues to refuse any nonempty or v1 root on reopen.

`VersionedImmutableObjectStore` is a separate offline v1 API. It acquires a
nonblocking exclusive `flock` on the pinned private root directory inode before
inventory and holds it until close. The legacy helper now cooperates with that
root lock. The v1 class pins the root, objects and metadata identities, checks
private ownership/modes, rejects inherited use after fork and uses CLOEXEC. Its
caller supplies frozen manifest/policy/build/clock context and must retain the
initial `descriptor_sha256` independently for every reopen. An optional
`expected_head=(sequence, hash)` detects removal of that committed prefix;
without an external head the report states rollback assurance UNAVAILABLE.

The v1 namespace is exact: `objects/`, `store-v1.json`, `seals-v1.jsonl`. It
initializes only an empty private root, writes/fsyncs metadata and root directory,
then appends a canonical INIT event. Journal records have exact schemas,
sequence, previous hash, store ID and event hash. The caps are 4 MiB/object,
64 MiB/journal, 64 KiB/record, 16 KiB embedded raw clock material per record,
10,000 events and 4,096 objects. A new seal reserves room for PREPARE, COMMIT,
recovery and a 64 KiB report, plus checks current available filesystem blocks.
Short writes and I/O failures poison the session; quota and filesystem behavior
remain future storage qualification matters.

`seal_with_provenance` accepts immutable original three-phase clock evidence and
an injected recorder. After durable PREPARE it writes/fsyncs a unique temporary
file, links/fsyncs the final name, unlinks/fsyncs the temporary name, verifies the
object, **then** invokes the recorder. It validates the original four phases and
raw evidence, then writes/fsyncs COMMIT before returning a typed receipt. Raw
object and feature-manifest receipts remain separate; dependencies must point
back to verified committed receipt hashes. A receipt witnesses an object seal,
not historical feature eligibility or capture success. No budget state is
reconstructed from a receipt.

Reopen requires the descriptor pin, replays bounded canonical records, rejects
unresolved PREPARE, verifies the exact object namespace/bytes and original
clock evidence, then fsyncs verified objects/journal/directories and appends a
new RECOVERY_VALIDATED event. Recovered receipts retain byte-identical original
clocks and carry acknowledgement UNKNOWN. Recovery performs no unlink, rename,
repair or fresh historical clock sample. A different boot permits historical
read only. Torn/invalid metadata, unknown names, damaged objects and capacity
failures remain held with deterministic classifications; no valid-prefix
admission is allowed. `read_receipt` requires the exact in-memory receipt,
including its acknowledgement state.

The synthetic restart suite has 28 cases: clean and repeated recovery, lock
competition and fork safety, recorder ordering/reentrancy, uncertain PREPARE and
COMMIT fsync outcomes, failed recovery fsync, FIFO metadata, leftover names,
feature dependencies, cross-boot reads, partial initialization, reserve refusal,
raw clock and age rejection, external-head rollback detection, mutation
poisoning, process interruptions, and a seven-state persistence-survival model.
The model distinguishes synced PREPARE/COMMIT records and directory barriers
from names or bytes that may survive without an acknowledged fsync. Process exits
are **not** power-loss tests. The affected offline I/O, launch, collector and
GRIB suites pass **204 tests** with this candidate; see the author terminal for
exact commands and pinned commit/tree.

Review should inspect lock/metadata identity and fork behavior, canonical replay
and limits, uncertain fsync states, original clock preservation, dependency
binding, and whether all design interruption boundaries have sufficient
adversarial coverage. This is synthetic evidence only. The v1 class does not
perform real clock attestation, external provider identity verification,
filesystem power-loss qualification, storage quota reservation, historical
runtime-decision proof, or coherent rollback detection without an independently
held head. Real-store use and any G3-L acceptance remain separately gated.

Score remains **91/200 (45.5%), formal 1/50; NOT_READY_TO_FUND**.

## Five-finding repair and synthetic interruption evidence

- R1: an empty pinned root with either a retained descriptor or journal head is
  held before metadata creation; only an explicitly fresh empty root initializes.
- R2: ordinary close shares the read/seal mutex and rejects close from a recorder
  callback. The fork-child path still only closes inherited descriptors.
- R3: the accepted list/tuple clock prefix is copied to a tuple before PREPARE;
  PREPARE, complete validation, COMMIT and the receipt use that same snapshot.
- R4: PREPARE reserves its own encoded bytes plus two maximum future records and
  report space. COMMIT consumes one of those reserved records; recovery checks
  its own encoded record and report reserve. Event limits are checked in the same
  remaining-event manner. The cap remains 64 MiB with no eviction.
- R5: the immutable pinned descriptor records original host and boot IDs. Reopen
  requires the original host; a different boot can inspect historical receipts
  but cannot acquire. INIT-only stores obey the same cross-boot hold.

The author suite now includes bounded thread close/read/seal and callback checks,
fork/exec ownership, 15 initialization interruption points, 25 seal interruption
points, 12 recovery interruption points, complete/torn journal and volatile
name/byte survivor states, plus explicit capacity boundaries. A process exit
leaves some unsynced bytes visible on this host; the deterministic survivor
fixtures separately exercise loss and survival possibilities. These tests make
no physical power-loss, filesystem qualification, real clock-attestation,
provider-identity, historical feature-eligibility or learner-admission claim.

## Two-finding repair on top of the five-finding candidate

Independent Astra/high review of exact `7bc627e` accepted R1–R5 and reproduced
two further P2 gaps with bounded probes (three defect-reproduction cases, 54
SIGKILL boundary cases, 28 survivor cases; 279 affected + 153 independent probe
passes). Both are now repaired in this commit:

- R6: `seal_with_provenance` and `read_receipt` now check process ownership
  (`os.getpid() == self._pid`) **before** attempting `self._mutex`, mirroring
  the pattern `close()` already used. Previously, a forked child could block
  indefinitely trying to acquire an `RLock` copied mid-hold from a parent
  thread that does not exist in the child; the fork-child path stays lock-free
  and descriptor-close-only, and never releases the parent's shared flock.
- R7: a single `MAX_DESCRIPTOR` (4096 bytes) bound now gates the encoded
  descriptor **before** any metadata file is created during initialization,
  using the same constant recovery's bounded read already enforced. An
  oversized encoded context (including Unicode expansion of `build_id`,
  `clock_method` or `host_id`) is refused up front, with no partial
  initialization left behind, instead of being silently accepted and then
  bricking every future reopen with `JOURNAL_OR_IDENTITY_INVALID`.

New bounded regression tests: two fork tests (read/seal) that pause a parent
seal in its recorder, fork from another thread, and assert the child rejects
in-process instead of hanging (bounded by `select`/pipe with a 2 s timeout, no
SIGKILL needed since rejection is now prompt); and three descriptor-capacity
tests — a monkeypatched below/exact/above `MAX_DESCRIPTOR` boundary with a full
seal/reopen roundtrip for the accepted cases, plus the exact 128-character
non-BMP Unicode reproduction from the independent review, now asserting
upfront refusal rather than delayed bricking. Affected suite: **285 passed**
(279 prior + 6 new: 2 fork-ownership cases, 3 descriptor-boundary cases, 1
Unicode-expansion case). This is still synthetic evidence only; no independent
review has inspected these exact bytes yet.

Author verification command:

```sh
/home/alphaadmin/alpha-review-test-venv/bin/python -m pytest -q \
  tests/test_v11_r09_gate3_store_v1.py \
  tests/test_v11_r09_gate3_offline_io.py \
  tests/test_v11_r09_gate3_launch.py \
  tests/test_v11_r09_gate3_collector.py \
  tests/test_v11_grib_fields.py
```

The final author test count, commit and tree are bound by the separate terminal
marker. Independent review must inspect the exact committed bytes and rerun
affected tests and adversarial probes before any integration decision.
