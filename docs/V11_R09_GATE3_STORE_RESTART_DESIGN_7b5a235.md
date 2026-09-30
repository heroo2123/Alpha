# Gate 3 immutable-store restart design — held 7b5a235

**DESIGN_READY_FOR_IMPLEMENTATION_REVIEW; no implementation or restart acceptance.**

Astra/high, 2026-09-30. Base commit
`7b5a23582d444c0c499faec63b6afd8872a3e90d`, tree
`3ff45d661fcd4c7db0faa536a9aae2152b992c6a`. This completes the design task
routed by `61bac9e`; the candidate stays unchanged and unmerged. The
[prior scoped repair PASS](V11_R09_GATE3_OFFLINE_IO_REVIEW_7b5a235.md)
and its 176 affected / 53 adversarial passes are prior evidence, not tests of
this design. All merge, publication, capture, G3-L, SHADOW and learner holds
remain. No real stores, authority or services may be changed by this design.

## Decision and limitations

Implement a versioned, exclusively owned store with an append-only seal journal.
Recover only a fully validated store with complete transactions. Classify uncertain
names without deleting, renaming, completing or blessing them. Any unresolved
transaction or namespace discrepancy holds the entire store. This deliberately
supports successful-seal restart first; automatic orphan repair is out of scope.
Existing nonempty stores without the new journal remain refused. No migration,
reclamation, counter reset or fresh clock may manufacture missing provenance.

Keep three separate facts in APIs and reports:

- **Object seal witnessed:** the recorded operation observed successful data and
  namespace persistence before obtaining the original seal clock.
- **Recovery validated:** those exact bytes and records were verified and persisted
  for this recovery session. This event has its own later time.
- **Historical feature eligibility / caller acknowledgement:** neither follows
  from reopening. A record cannot prove that its own final fsync returned or that
  the caller received success. A recovered transaction has acknowledgement UNKNOWN.

A complete record surviving a failed journal fsync can still witness an earlier
successful object seal. It does not certify successful completion of the whole
store API call. Recovery may preserve that distinction and retrieve the bytes;
it must not return an unconditional historical-admission or successful-capture flag.
The current module has no real clock attestation or feature-admission integration.
Those remain separately reviewed G3-L/G3-E/Gate 4 work.

## Ownership and filesystem contract

Use an existing private root and `objects/`, both owned by the current UID and
mode 0700, on an explicitly supported local filesystem. All regular files are
0600, current-UID-owned and single-link at stable boundaries. Preserve the current
4 MiB object limit and protected-path refusals. No directory creation outside a
disposable synthetic fixture is authorized by this implementation batch.

Open and pin the root directory descriptor, then take `flock(LOCK_EX | LOCK_NB)`
on that descriptor **before inventory, initialization, journal replay, reads or
writes**. Hold it for the entire session, including recovery and report creation.
The root inode is the lock anchor; there is no unlinkable/recreated lock-file
anchor. Open `objects/` relative to that root descriptor. Check resolved ancestors,
UID/mode, device/inode and current pathname-to-descriptor identity at entry and
before/after each mutation. A replacement/rename/mode change poisons the session.
Never follow symlinks. Inspect file type with O_PATH before a bounded O_NONBLOCK,
O_NOFOLLOW data open, then compare inode identity. Do this for the journal and all
metadata as well as objects; FIFOs/devices must not be opened for blocking I/O.

The lock covers every cooperating store instance, even read-only ones. Use a
process-local mutex for calls on the same instance and reject reentrant recorder
callbacks. Remember the opening PID; inherited instances reject all operations
in a fork child. Child cleanup closes inherited descriptors without LOCK_UN;
register at-fork cleanup, use CLOEXEC, and do not expose/duplicate descriptors.
A child releasing an inherited flock explicitly could release its parent's lock.
Advisory locking does not defend against a malicious same-UID process or root;
those actors and coherent filesystem rollback are outside this offline claim.
Linux documents the shared open-file-description lifetime and advisory nature of
[flock](https://man7.org/linux/man-pages/man2/flock.2.html).

Future composed use acquires `DurableBudget` ownership first, then store ownership,
nonblocking; failure releases only locks acquired by that invocation. Release in
reverse order. A standalone synthetic store does not acquire the budget lock or
change the budget journal. No store method reacquires budget ownership while
holding the store mutex. Budget identity, reservations, bytes, cooldowns and
original boot restrictions remain authoritative and cannot be reconstructed from
object success. A budget-incomplete attempt never becomes capture-success merely
because the store transaction completed.

## Format and identity

Inside the root keep `objects/`, immutable `store-v1.json`, and append-only
`seals-v1.jsonl`. The store descriptor binds a random store ID, schema version,
root/objects device and inode, manifest and policy digests, author code/build
identity, clock method, and frozen resource bounds. Pin descriptor digest and
expected manifest/policy through the caller's existing frozen context, not by
trusting whatever the directory advertises. Runtime identity and build must match;
upgrades and copied stores require a later reviewed migration policy.

Initialization requires an empty `objects/` and no other store entries, after
locking. Create metadata with O_EXCL, fully write and fsync each new file, then
fsync the root directory before any seal. An interruption leaving incomplete
initialization is a held store; never silently recreate a missing descriptor or
journal beside existing content. The root itself is preexisting and externally
pinned. File fsync alone does not persist a new name; Linux requires the containing
directory to be synced as well ([fsync](https://man7.org/linux/man-pages/man2/fsync.2.html)).

Journal records use canonical encoding, exact schemas, sequence number, previous
record hash, store ID and event hash. Reject duplicate keys, nonfinite numbers,
unknown fields/versions, gaps, duplicate operation IDs, invalid transitions and
noncanonical encodings. Require a newline-terminated complete final record.
Bound records, total journal bytes, event count, entry count and evidence bytes
*before* allocation or reading; freeze concrete caps in the implementation
handoff (journal at most 64 MiB, individual record at most 64 KiB, embedded clock
material at most 16 KiB total per record). Reject inputs needing more capacity;
no truncation or cap growth during recovery. Account for worst-case prepare,
commit and recovery records plus the existing report reserve before starting a
transaction. ENOSPC/EDQUOT or reserve failure never causes old evidence eviction.

Events are INIT, PREPARE, COMMIT and RECOVERY_VALIDATED. Permit one outstanding
PREPARE at a time. PREPARE binds operation ID, object kind, digest, length,
unique temporary basename, request/attempt identity, frozen source/decoder pins,
evidence dependencies and original clock-prefix bytes. COMMIT binds the exact
PREPARE hash, final object identity, all original phase evidence and the clock
policy digest. A feature-manifest object also binds its exact dependency receipt
hashes and extraction/semantic identity. Dependency references must point strictly
backward to verified committed records; cycles, missing or cross-store references
reject. Do not add a success field meaning more than the event actually proves.

Raw objects may be shared only through explicit references to the same original
receipt; identical bytes do not create a new acquisition time or request outcome.
Keep `seal` duplicate refusal. A read by digest alone cannot identify a particular
capture; provenance reads require the bound operation/receipt hash. A digest is
content identity, not source, event, station, decoder or time identity.

## Seal ordering and clocks

The current `seal(bytes) -> digest` and separately populated `ClockSequence` are
insufficient as a restart provenance API. Add an explicit versioned operation
returning a typed receipt. If a compatibility helper remains, it must stay
synthetic/session-only and must not generate restart or causal claims. Do not
silently upgrade the old end-to-end fixture's disconnected timestamps into proof.

For a provenance-bearing seal, the critical order is:

1. Under ownership and mutex, verify store, resource reserve, frozen identity,
   object bytes and provenance inputs. Refuse an already-present digest before
   PREPARE so ordinary duplicate refusal leaves a healthy store usable. Validate
   the already-recorded original
   clock prefix and its raw evidence. Obtain immutable copies, not mutable caller
   dictionaries/lists. Choose a unique operation ID and temporary name.
2. Append PREPARE, fully write and fsync the journal. No object writes precede
   successful PREPARE persistence. Any short write/error poisons the session.
3. O_EXCL-create the bound temporary object; fully write, flush and fsync its
   descriptor. Link to the digest name without replacement; fsync `objects/`.
   Unlink only this operation's temporary name; fsync `objects/` again. Verify
   the final regular-file identity, single link, length and hash. Check pinned
   directory identities throughout. A failed operation preserves remaining names.
4. Only after all step 3 barriers return successfully, invoke the clock recorder
   to obtain the original durable-object-seal sample and bounded raw evidence.
   Revalidate phase order, boot, uncertainty, contemporaneous measurement age,
   monotonic order and one common UTC/monotonic offset interval. A recorder
   failure leaves the PREPARE outstanding and holds the store.
5. Append COMMIT containing the complete evidence; fully write and fsync journal.
   Only after that succeeds may this live instance return a receipt and admit an
   ordinary provenance read. Do not expose a newly linked digest before this point.

The recorder is injected for synthetic tests and called by the store at the stated
boundary; accepting a caller's precomputed final timestamp is insufficient.
Production-quality measurement and its raw-evidence verifier remain unimplemented
and unapproved. Hashes alone are not time attestation. Embed bounded raw clock
material, or reference already verified durable evidence through an explicitly
reviewed dependency; do not accept dangling evidence hashes.

Raw-object durability is distinct from **feature-manifest durability**. A raw
COMMIT is only a raw dependency. A future feature-ready claim requires the actual
immutable feature manifest, its complete dependency graph and its own post-fsync
seal reading. Completion evidence is a later audit receipt about that already
persisted manifest, not a field pretending to timestamp its own fsync. This
avoids an infinite self-timestamp/receipt chain. It still cannot prove that the
receipt itself was durably available to a historical decision. Historical
runtime-use/admission claims need the separately reviewed original decision or
terminal evidence binding that receipt; absence stays UNKNOWN/GATED.

Persist original evidence bytes unchanged, including host/boot, phase, UTC,
monotonic time, uncertainty, measured-monotonic time, method and evidence digest.
On replay validate measurement age relative to the **original phase**, not current
uptime. Never compare monotonic values across boots or replace old values with
recovery time. Cross-boot inspection is allowed as historical evidence; acquisition
resume remains refused by the existing budget boot policy. For any later eligibility
check, every dependency's original phase upper bound must be <= the frozen decision
lower bound. Equality is allowed; one late dependency rejects the complete feature.
Receipt, metadata, decode and feature-manifest readiness all remain distinct.

## Restart classification and uncertain names

Opening a v1 store acquires ownership, verifies the descriptor against the expected
context, inventories without following entries, replays bounded records, verifies
all referenced bytes/evidence, and reconciles the exact object namespace. Before
returning a recovered view, fsync verified object descriptors, journal and affected
directories, then append/fsync RECOVERY_VALIDATED binding the prior journal head,
canonical inventory digest, new session/host/boot and recovery event time. These
barriers establish current persistence only. Do not modify original receipts.
Replay ignores recovery events for object identity and original clock selection.
A prior valid recovery event never substitutes for a fresh full verification.

| Observed state | Required outcome |
| --- | --- |
| Complete valid COMMIT, exact final object, no temp or unexplained name, all dependencies valid | Recover receipt as object-seal-witnessed; acknowledgement UNKNOWN; bytes become accessible only after current recovery validation. No automatic causal/learner admission. |
| PREPARE only; no file, temp only, final only, or both | UNRESOLVED_PREPARE; hold entire store, preserve all names. Even a matching digest is not a COMMIT. |
| COMMIT plus leftover temporary link/name | NAMESPACE_CONFLICT; hold. Do not unlink to repair link count. |
| Complete COMMIT survived uncertain journal fsync | Same recoverable object witness as first row, if every invariant holds; never assert original journal-fsync success or caller acknowledgement. |
| Torn/noncanonical/invalid record, missing journal/descriptor, hash/sequence conflict | JOURNAL_OR_IDENTITY_INVALID; hold; no truncation, rebuild or valid-prefix-only admission. |
| Missing/corrupt committed object, wrong link count/mode/owner, alias or unrecognized name | NAMESPACE_CONFLICT; hold, bounded metadata-only report where safe. |
| Legacy nonempty store | LEGACY_PROVENANCE_MISSING; preserve current refusal. |
| Fully valid clean store under a different boot | Historical evidence inspection only; refuse acquisition resume and all cross-boot monotonic comparisons. |
| Capacity, lock, path, read or fsync failure during recovery | RECOVERY_INCOMPLETE; no ordinary reads/seals, no retry-in-place that clears poison. |

The only automatic unlink remains normal in-session removal of the transaction's
own temporary link in step 3. Recovery performs no unlink, rename, overwrite,
relink, clock remeasurement for old data, or cleanup. Quarantine means a logical
held classification, not moving evidence. Report all reasons deterministically;
report failures leave the store held and never turn an incomplete scan into PASS.
Return the bounded report to the caller or a separately pinned report sink; do not
create unexplained report files inside the exact store namespace.
An unresolved state needs a separately reviewed reconciliation/export procedure,
not a hidden `force` flag. A new synthetic store does not clear any old budget or
attempt, and is never an implicit acquisition retry.

A local hash chain detects many corruptions but cannot detect a coherent rollback
of all local state. If the caller has a previously recorded terminal/head digest,
require the exact hash at its sequence and permit only valid extensions. Missing
that checkpoint rejects. Without an independently retained head, report rollback
assurance UNAVAILABLE; do not invent an anchor or install root authority. In
particular, dropping an entire last transaction and all its files can be invisible
to local inspection. Offline retrieval is not operational archive acceptance.

## Synthetic acceptance contract for implementation

The following are required tests, **not tests already run**. Every fault fixture
uses disposable roots and only children it created. Pin the resulting candidate
commit/tree; independent review must verify the exact bytes and rerun affected
and adversarial suites. Do not replace the old conservative tests with permissive
ones: retain legacy/uncertain-store refusals and add explicit v1 success controls.

| Group | Required cases and oracle |
| --- | --- |
| Ownership | Competing processes and separately opened instances cannot read/seal/recover while owned; same-instance threads serialize; fork child cannot use/unlock parent instance; exec does not retain ownership; close/crash releases only appropriate descriptors; root/objects replacement poisons. |
| Initialization | Interrupt before/after each create/write/file-fsync/root-fsync; complete untouched initialization recovers, partial metadata never becomes an empty new store. |
| Seal boundaries | Interrupt before/after PREPARE write/fsync, temp create/write/file-fsync, link, both directory fsyncs, unlink, recorder call, COMMIT write/fsync and caller return. PREPARE-only always holds; fully valid COMMIT may recover only with acknowledgement UNKNOWN. |
| I/O uncertainty | Short writes; ENOSPC/EDQUOT/EIO/EINTR; failures before/after real link/unlink/fsync side effects; callback exceptions; failed recovery record write/fsync; repeat reopen. Original session stays poisoned; names and bytes are preserved. |
| Recovery interruption | Kill after verification, every recovery barrier and recovery record write/fsync. Reopen repeats validation; no historical clock changes; torn record holds; full record does not prove prior caller success. |
| Namespace/integrity | Truncate, mutate, missing object, duplicate/cross-store operation, substituted receipt, aliases/hardlinks, unexpected directory, FIFO/symlink/device metadata, oversized sparse file/line/journal/name count; bounded rejection without blocking or resource blowup. |
| Clocks | Byte-identical original evidence through multiple restarts, old boot retained, stale-at-original-phase sample, fake/missing raw evidence, precomputed seal time, earlier phase over cutoff, cumulative offset inconsistency, recorder failure, equality/just-late cutoff controls. |
| Feature semantics | Raw success alone never supplies feature-ready time; late/missing manifest or one late dependency rejects; frozen request/provider/station/decoder/context mismatch rejects; duplicate bytes preserve original receipt identity. |
| Accounting | Store success with budget-incomplete attempt never becomes capture success; restart preserves reserved/delivered bytes and restriction state; no added download/retry, window extension or boot reset. |
| Limits/rollback | Capacity checks precede mutation, reserve survives refusal, trusted head mismatch rejects, coherent rollback without external head explicitly remains unprovable; unknown names never silently ignored. |
| Positive controls | Healthy seal/read, clean close/reopen/read, repeated valid recovery, same-boot new distinct offline seal after recovery, duplicate refusal without overwriting, cross-boot evidence-only inspection. |

Process exits/SIGKILL do not simulate power loss. Add a deterministic persistence
model that distinguishes volatile file bytes/names from successfully synced state;
explore interrupted operations and allowed survival/loss of unsynced data. Model
assumptions must be explicit, with invalid on-disk combinations rejected as above.
Do not claim physical disk/filesystem durability from mocked fsync or subprocess
exits. Actual storage qualification remains a separate future G3-L prerequisite.

## Review evidence, handoff and completion

The [completed design terminal](V11_R09_GATE3_STORE_RESTART_DESIGN_7b5a235_terminal.json)
binds this report and the primitive probe by SHA-256.

This design inspected the exact candidate store/clock code, its disconnected
composition fixture, launch storage/clock schema, DurableBudget lock/replay rules,
Gate 3 protocol section 5 and the authoritative master's causal archive requirement.
The master's SHA-256 matched its pinned value; no private input is reproduced here.
One disposable Linux directory-lock capability probe established separate-open
exclusion, preservation of the parent's lock after fork-child descriptor close,
and release on final owner close. Its record is
`/tmp/alpha-v11-restart-design-probe-au8mr393/result.json`. This is a narrow host
primitive check, not recovery implementation or crash acceptance. No unchanged
176/53 suite or full release suite was rerun for a documentation-only design.

Next implementation handoff: use the existing held isolated worktree, preserve
`7b5a235`, limit work to the offline store/clock interface, its tests and handoff
(with one dedicated offline recovery helper/test file if justified). First freeze
the v1 schema, typed receipt, concrete caps and injection boundaries; then implement
ownership, ordered records, conservative replay and the synthetic matrix above.
No launch-contract weakening, real adapter, provider bytes, private manifest or
real-store operation belongs in that batch. Stop for architectural review if the
proposal needs migration, automatic orphan repair, cross-boot acquisition or a
claim stronger than evidence retrieval. A fresh different-model exact-commit
review is mandatory before any separate integration decision. This design itself
is not independent acceptance of future implementation.

Freshness at this design pass: clean main `61bac9e`, 45 ahead / 0 behind its local
tracking ref; no duplicate implementation worker; held Gate 3 and SHADOW worktrees
clean. New commissioning writes remain watchdog statuses only after 06:43 UTC.
Accepted release resolution `6ec371e` remains current. V10 demo, PAPER scanner and
controller inactive/disabled; V11 execution inactive/masked. Protected authority
paths absent. Disk 4.4 GiB free, memory about 1.0 GiB available. GEFS forward
SHADOW remains owner/root-gated; no forward evidence was admitted. This bounded
routed design completed synchronously; no persistent worker was needed or launched.
No service, V10, AxiomTrade, financial, authority or publication action.
**91/200 (45.5%), formal 1/50; NOT_READY_TO_FUND**, unchanged.
