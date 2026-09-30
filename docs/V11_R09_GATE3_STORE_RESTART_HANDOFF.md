# Gate 3 v1 offline store restart author handoff

**AUTHOR CANDIDATE; independent exact-commit review required.** This batch extends
the held `7b5a235` Gate 3 offline I/O worktree. It grants no merge, publication,
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
