# Offline daily review v2 candidate reader contract

This module is an isolated, pure verifier for a proposed version-2 protected
selection. It returns a content pin, **not** certification, runtime admission,
commissioning, financial authority, or permission to start a worker. It takes
canonical JSON bytes and a claimed store identity supplied by its caller.
No live registry or store is opened. The existing v1 `StationRegistry` is
unchanged and still refuses a v2 manifest.

## Bounded provisional wire format

`daily_review_v2.py` defines exact keys and upper bounds in code. JSON is UTF-8,
sorted-key, compact, ASCII-escaped canonical JSON; duplicate members, nonfinite
numbers, unsupported keys, and noncanonical bytes refuse. SHA-256 covers exact
canonical bytes. Epoch times and sequences are nonnegative JSON integers (not
booleans); the review frontier starts at 1. The index holds a revision, a
previous-index hash, a list of immutable review ID/hash references, and active
selection entries. The active key is the five-field semantic tuple
`(namespace, stage, scope_key, metadata_fingerprint, rule_fingerprint)`.
Only one entry may exist for that tuple across all days and generations.
Historical review objects are verified but never selected. The requested day,
event, generation, revision and current time must match the sole active entry.

Each selected review pins its generation descriptor hash, full runtime-prefix
hash through its frontier, projection hash, candidate approval hash, times, and
proof references. The commissioning object names the final review hash,
generation descriptor hash and candidate approval hash. The verifier checks
every referenced review and active commissioning object before selecting.
The prefix is an exact canonical list of rows `1..frontier`. Each row binds
sequence, record ID, kind, event, body SHA-256, recording time and availability
time. Proof references must identify a row in that prefix whose body is a
matching passing capability record. A matching semantic body hash in another
generation cannot substitute for its generation-local row ID and sequence.

The descriptor and supplied store identity must be identical, including
generation, date, event, namespace, absolute store path, device, inode, and
generation-marker hash. This catches mixed identities **only when the caller
has independently established the opened file's identity**. The module cannot
prove that a claimed inode/path/marker was read from the actual store.

## Required integration before any authority use

The object format above is provisional. A future reviewed protected reader
must establish root custody, no symlink or hardlink alias, stable opened-file
identity, bounded immutable object reads, and registry/object revision
consistency under the protected lock. A reviewed runtime-prefix snapshot must
be taken from the actual selected store under exclusive writer ownership;
`store_identity` must come from that open handle and verified generation
marker. Its body hashing and envelope projection must be mapped explicitly to
the production ledger schema. A separate verifier must establish projection
snapshot content, latest rule/metadata, later failures/barriers, predecessor
history resolution, exact runner/config/import closure, and time-sensitive
inputs. This module accepts a projection hash but cannot validate projection
content because no exact production projection format is authorized here.

The publisher and migration transaction must validate monotonic revision and
`previous_registry_sha256`, preserve all historical review bytes (including
the old v1 manifest), establish real root-custodied commissioning approval,
and atomically choose one active generation. This module validates the
commissioning object's internal hash links; it cannot attest who approved it.
No fallback or v1 compatibility view is provided. The existing v1 reader and
daily publisher must continue to reject v2. Shared-registry consumer/writer
inventory, independent exact-candidate review, root installation, and live
activation remain separate work.
