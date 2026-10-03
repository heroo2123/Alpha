# Gate 3 standalone offline fixture custody and replay candidate

Status: **UNREVIEWED IMPLEMENTATION CANDIDATE; CUSTODY_UNQUALIFIED.**
Base: `c20677b95a337bf4c8b7d29ff7409384c9a23406`.
Author: sole Codex Astra/high specialist, 2026-10-03.

This adds only `tools/v11_gate3_clock_custody.py`,
`tests/test_v11_gate3_clock_custody.py`, and this handoff. Existing reviewed
recorder/parser files, production callers, and coordinator-owned status
files are unchanged. No native recorder was compiled or executed. No
host clock observation, provider/network request, credential/account/order
access, authority installation, protected-master modification, V10/Axiom
change, or production integration was performed.

Read before implementation: method handoff sections 5–6, recorder
implementation handoff, latest work-checkpoint entry (reviewed recorder
integrated, 12:38 UTC), and retained exact review
`/tmp/alpha-v11-clock-recorder-review-6f289d0.final`. That review's
**PASS_IN_SCOPE applies only to the prior offline fixture code**, not this
candidate. Historical native preload descriptions and older test counts in
the original recorder handoff do not describe this slice's validation.

## API and scope

`FixtureStore(anchor_fd, name, metadata, create=True)` exclusively creates
one new fixture session. `create=False` opens an existing session for replay
only. Use a context manager or explicitly close. There is no CLI, recorder
launcher, implicit acquisition, calibration source, checkpoint acceptance,
signing key, trust-list input, or import into a production caller.

The caller supplies an already-open directory descriptor for a private
0700 directory owned by the effective uid. Synthetic tests create this
anchor with `TemporaryDirectory(dir=worktree)`. `name` must be one ASCII
component, `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`. Slash, dot aliases, absolute
paths, NUL and string subclasses are refused. Descriptor aliases of the
same directory still share the kernel directory-inode lock.

Metadata is a closed exact-built-in dict of `session_nonce`, `method_id`,
`profile_id`, `build_id`, `host_id`, and `event_kind`. Values use that same
bounded identifier grammar; `event_kind` must be `SYNTHETIC`. These are
caller declarations, not verified identities. Unsupported review,
calibration, checkpoint, authority and local-observation fields fail
closed. The copied metadata is bound into the chain's genesis.

- `append(raw: bytes)` retains exact supplied fixture bytes, including
  bounded malformed/truncated/refused inputs, and returns a local publication
  result. It calls only the reviewed pure dossier parser. Parsed `OK` means
  structural fixture acceptance, never clock/custody acceptance.
- `finish()` publishes a local terminal binding nonce, count, ordered head
  and chain-byte count. `CALLER_FINISHED` is a caller statement, not proof
  that all events or failures were submitted.
- `replay()` verifies the complete currently visible bounded store and
  returns the original raw bytes and recomputed projections' digests.
  Errors raise `CustodyError` with bounded, fixed-authority diagnostics.
  It never silently returns only a valid prefix when later bytes fail.

Every result, error diagnostic and stored envelope has fixed false
execution/provider/capture/clock/custody flags, zero qualification credit,
`g3l=NO_GO`, and explicit `custody_status=CUSTODY_UNQUALIFIED`.

## Object format and ordered replay

`session.json` is canonical ASCII JSON binding the closed metadata and
flags. Its literal-byte SHA-256 is the first prior head.

`000001.obj` through `000064.obj` contain a four-byte big-endian envelope
length, canonical ASCII JSON envelope, then original raw binary fixture
bytes. The envelope binds nonce, sequence, prior object head, exact raw
byte length and SHA-256, independently computed canonical parsed-projection
SHA-256, and parse status/refusal code. A parser refusal has null projection
digest, never an invented successful projection. Partial native refusal
details remain in the literal raw bytes even when the existing parser's
projection deliberately omits those details.

The next head is SHA-256 of the **whole framed object**. Replay re-parses
raw bytes with the reviewed parser, reconstructs the expected envelope,
and compares the entire frame byte-for-byte. Unknown envelope fields,
forged authority, reordered/gapped sequences, truncation, substituted raw
bytes, wrong projections, and wrong previous heads refuse. Distinct raw
JSON formatting can yield different raw hashes and equal projection
hashes. Filesystem timestamps are neither read nor interpreted as event,
seal, durability or calibration times.

Optional `terminal.json` canonically binds the final head, count, byte
count and reason; replay returns its separate literal-byte digest. Missing
terminal is explicitly reported, not synthesized. Extra directory entries
and `.pending` refuse replay. No files are deleted during recovery.

## Filesystem protocol and failure behavior

All descendants use pinned directory descriptors. Anchor and store must
be uid-owned 0700 directories on the same device. Files must be uid-owned
0600 regular files with one link. Named versus open device/inode identity
is checked before/after reads and publication; store binding to the anchor
is rechecked. Opens use `O_NOFOLLOW`, close-on-exec, and nonblocking file
opens. Pre-open type checks reject existing special files before opening.
No `realpath`-then-open or arbitrary nested path traversal is used.

An exclusive nonblocking `flock` is held on the **directory inode** for the
entire handle lifetime, including replay handles. There is no replaceable
lock-file pathname. A per-handle mutex serializes methods; use after fork
refuses by process identity. Cooperating duplicate handles refuse.

Publication creates fixed `.pending` exclusively, writes with bounded
progress, verifies bytes, fsyncs the object, hardlinks to its final name
without clobbering, verifies both names refer to the pinned object with
exactly two links, unlinks `.pending`, fsyncs the store directory, then
verifies published bytes. New-store creation also fsyncs the anchor.
The transient second link is internal to publication, not an accepted
resting state. A crash retaining both names refuses restart.

Failures preserve all remaining bytes and names. Diagnostics distinguish
write-returned byte count, file-fsync return, publication return, and
directory-fsync return, with the failed phase. These are observations of
syscall returns, not independent durability attestations. A failed or
uncertain append poisons the writer; no retry, overwrite, cleanup, fresh
session age, or automatic resume occurs. A full write followed by directory
fsync failure can leave a valid visible object: replay calls its crash
durability unknown and never upgrades the failed call into success.

Capacity exhaustion reserves room to attempt a `CAPACITY_EXHAUSTED`
terminal; the refused incoming sample was **not retained**. Oversized or
wrong-type input is refused before serialization and poisons the writer.
A failed storage operation or invalid API input may leave no durable
refusal diagnostic; `CustodyError.evidence` explicitly says
`evidence_incomplete=True` and `diagnostic_persisted=False`. A successful
capacity terminal is separately identified as `terminal_persisted=True`.
The full exception diagnostic is still not claimed persisted. Inability
to fsync cannot be repaired by pretending the error report itself is durable.

## Bounds and unresolved contracts

At most 64 samples; 16,384 raw bytes per sample; 65,536 bytes per physical
object/read; 1,048,576 serialized session bytes including reserved terminal
capacity; 4,096 bytes reserved for terminal. Directory scans stop after 67
entries. Reads/writes have finite progress-attempt bounds; short writes
complete or refuse. An EINTR exposed to this module refuses without an
application retry loop. The parser retains its existing depth/node bounds.
Replay holds at most the bounded raw session plus bounded envelopes in
memory. These are serialized-byte and algorithm bounds, not disk-block,
kernel-I/O-latency, process-RSS, or whole-capture budget guarantees.

**External custody is deliberately not implemented.** Replay always says
`external_checkpoint_present=False`, `rollback_detectable=False`,
`crash_durability=UNKNOWN_ON_REPLAY`, and `recovery_gaps=UNKNOWN`. A same-uid
actor can roll back the store to an older entirely valid chain and terminal,
or rewrite a whole internally consistent history. Without an independently
retained accepted head, this cannot be distinguished. The regression test
requires the rolled-back history to remain unqualified, not falsely detected.
A live writer additionally compares replay's head/count to its in-memory
state, detecting ordinary suffix rollback during that lifetime.

The caller's anchor descriptor is a trust boundary. This module does not
validate ancestry above it, certify a private real-retention path, defend
against root/mount manipulation or arbitrary hostile same-uid races, enforce
mandatory locking, or make an atomic adversary-proof filesystem snapshot.
Metadata/inode checks and advisory locking catch tested substitutions but
are not isolation from code with the same filesystem permissions. No real
retention root is authorized here. Existing stores are replay-only, even
when intact and unterminated; recovery append policy is intentionally absent.

There is no external checkpoint ingestion/acceptance, independent replay
implementation, original calibration/reference blob import, qualification
consumer, complete-request ledger, or proof that the caller submitted every
failure. The existing parser's deferred cross-clock rate consistency and
exact realtime/adjtimex structural equality remain unchanged. All method,
source, custody, real-local-recording, execution, G3-L and SHADOW gates remain
separate and unqualified. This candidate needs independent exact-commit
review; the author does not approve or merge it.

## Validation

- `python3 -B tests/test_v11_gate3_clock_custody.py`: **30/30 passed**.
- `python3 -O -B tests/test_v11_gate3_clock_custody.py`: **30/30 passed**.
- `python3 -B tests/test_v11_gate3_clock_dossier.py`: **73/73 passed**.
- `python3 -O -B tests/test_v11_gate3_clock_dossier.py`: **73/73 passed**.

The new tests use unittest assertions that remain active under optimization.
They cover aliases/exact types, directory permissions, symlinks/hardlinks/
FIFOs, substitutions, duplicate writers, no-clobber races, partial writes,
truncation, write errors, object/directory/parent fsync failure, crash after
link before receipt/terminal, preserved interrupted evidence, missing/swapped
objects/terminal, forged authority/projection/prior hashes, valid-old-chain
rollback, deterministic replay, capacity and size bounds. Capability-denial
patches reject Python clock/socket/process/random-acquisition calls during
normal append/finish/replay, alongside an import allowlist check. No account
or provider module is imported. These synthetic faults are not a physical
power-loss or malicious-kernel durability test.

All scratch roots are worktree-local and removed by the test harness; no
other worktree/evidence is touched. Git candidate diff hygiene and clean
post-commit status are checked separately; exact candidate SHA/tree are
reported in the final handoff to avoid self-referential Git identifiers.
