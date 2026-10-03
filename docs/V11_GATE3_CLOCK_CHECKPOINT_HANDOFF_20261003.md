# Gate 3 clock checkpoint comparison and separate replay prerequisite

Status: **UNREVIEWED OFFLINE CANDIDATE; CUSTODY_UNQUALIFIED.**
Base commit: `de2fda002a665e095dbd8a90f2563438c7e87080`.
Base tree: `0b3e82e6ae62e24289140bc793c23cc60373a9ac`.
Sole author: Codex, 2026-10-03. Different-model exact-commit/tree review is
required before merge or any operational claim. No independent review is
claimed by the author or by tests.

Read first: `V11_WORK_CHECKPOINT.md`, `V11_REQUIREMENTS_MATRIX.md`,
`V11_ENGINEERING_PROGRESS.md`, the clock custody, method and recorder
handoffs. Their historical review-pending entries do not override the task's
reviewed `de2fda0` base, which integrates custody successor `7c307b2`.
No private review inputs or external evidence were accessed or altered.

## Smallest honest next checkpoint

The existing writer can replay local bytes but cannot distinguish a valid old
history from its replacement. The next tractable offline prerequisite is a
**bounded exact comparison against separately supplied checkpoint and pin
bytes, with custody framing reconstructed without the custody writer**.
This tests the linkage contract needed by a later independent custodian; it
does not create that custodian, checkpoint, pin provenance or acceptance policy.

Only three new files belong to this candidate: this handoff,
`tools/v11_gate3_clock_checkpoint.py`, and
`tests/test_v11_gate3_clock_checkpoint.py`. No existing code, safety gate,
coordinator status document, writer, parser, probe or production caller changes.
The module has no CLI, export/issuer function, filesystem loader, clock read,
native execution, network, provider dispatch, signature verification, trust
store, key generation, authority installation or persistence path.

All success reports and bounded refusal diagnostics fix:

```
custody_status = CUSTODY_UNQUALIFIED
g3l = NO_GO
qualification_credit = 0
execution_authority = provider_authority = capture_eligibility = false
clock_qualification = custody_qualification = false
external_checkpoint_authenticated = rollback_protection = false
anchor_freshness = recovery_gaps = UNKNOWN
crash_durability = UNKNOWN_ON_REPLAY
diagnostic_persisted = false
evidence_incomplete = true
```

`SUPPLIED_CHECKPOINT_MATCH_UNQUALIFIED` means exact byte agreement with the
supplied pin and complete supplied session set. It never means independent
retention, current authority, fresh/latest checkpoint, receipt, calibration,
clock validity, sample completeness, execution permission or G3-L satisfaction.
Fixture pins and manifests constructed in tests are explicitly synthetic.
**An external checkpoint must never be fabricated from local chain state.**
No production issuer is provided, and no local result is labeled external.

## Pure API and closed formats

`compare_checkpoint(checkpoint_bytes, anchor_bytes, receipt_context_bytes,
sessions)` accepts exact built-in immutable bytes and tuples. There is no
default pin, local-state fallback, URL/path lookup or inferred checkpoint.
`sessions` is an ordered tuple of `(header_bytes, object_bytes_tuple,
terminal_bytes)` triples. These represent literal `session.json`, sequential
framed `.obj` files and `terminal.json` bytes, not a writer's replay report.
Missing terminal refuses: the slice compares closed sessions only. Zero-sample
closed sessions remain valid diagnostic fixtures; they earn no evidence credit.
Interrupted/open stores, `.pending`, recovery append and live prefix checking
are outside this API. A later exporter must separately bind directory inventory
and preserve interrupted entries; this API cannot see omitted filesystem names.

The supplied pin is canonical ASCII JSON with exactly:

| Key | Contract |
| --- | --- |
| `schema` | `CLOCK_CHECKPOINT_FIXTURE_PIN_V1` |
| `scope_id`, `custodian_id` | ASCII identifiers, 1–64 characters; caller declarations |
| `generation` | Exact integer 1 through 2^63−1; explicit logical generation, not time |
| `checkpoint_sha256` | 64 lowercase hexadecimal characters |
| `checkpoint_byte_length` | Exact positive integer, matching literal checkpoint bytes |

The checkpoint is canonical ASCII JSON with exactly the eight fixed authority
fields above (through `custody_status`), plus:

| Key | Contract |
| --- | --- |
| `schema` | `CLOCK_CHECKPOINT_FIXTURE_V1` |
| `scope_id`, `custodian_id`, `generation` | Must equal the separately supplied pin |
| `receipt_context_sha256`, `receipt_context_byte_length` | Bind the supplied nonempty opaque context bytes exactly |
| `sessions` | Ordered complete list of the session summaries below |

Each summary has exactly `session_nonce`, `header_sha256`, `count`,
`object_sha256` (ordered list of whole-frame digests), `head`,
`chain_byte_length` (header plus frames, excluding terminal), and
`terminal_sha256`. Header digest binds all original declared metadata, including
method/profile/build/host and `SYNTHETIC` kind. Whole-frame digests bind nonce,
sequence, prior head, raw/projection digests, lengths, statuses and original raw
bytes. Terminal digest binds final count/head/chain length and either
`CALLER_FINISHED` or `CAPACITY_EXHAUSTED`. Session nonces must be unique within
this set. The schema does not assert completeness across unlisted sessions.

Context is retained by the caller and compared as opaque bytes. Calling it
`receipt_context` defines a future linkage slot, not evidence that a receipt
exists. This module does not interpret identity, signatures, timing or causal
acknowledgments inside it. A future real format needs separate review and an
authority consumer; this synthetic schema is deliberately not live admission.

## Separate framing replay and its independence limit

The module imports only `hashlib`, `json`, `re`, and the reviewed pure dossier
parser (plus future annotations). It never imports or calls the custody writer,
its replay method, private constructors, serializers or filesystem code.
It reconstructs header schema, ordered object framing, raw digest, prior head,
projection digest, refusal reason, terminal and summary from supplied bytes.
Frame and terminal comparisons are literal bytes. Duplicate JSON keys,
noncanonical encodings, unknown keys and bool/int substitutions refuse.

The raw-record interpretation **shares** `dossier.parse_probe_record` with the
writer. Its projection serialization is reproduced independently as sorted
compact UTF-8 JSON; custody envelopes use sorted compact ASCII JSON. This is a
separate custody framing verifier, not an independently authored clock parser,
not an independent reviewer, and not independent evidence provenance. Shared
parser defects remain shared. Tests use writer-generated synthetic fixtures to
check interoperability, then mutate them; that is not an external witness.
An eventual independent replay must pin the exact implementation/parser bytes
and either independently implement raw interpretation or explicitly bound that
shared dependency in its reviewed scope.

Both valid `REFUSED` records and malformed raw bytes are valid retained
diagnostic evidence in a structurally consistent chain. The result preserves
their sequence/status/reason, and their exact raw bytes remain hash-bound in
the supplied immutable frames. Their presence is never upgraded to a successful
observation. No input is modified. Any bundle mismatch refuses the entire call,
without returning a successful prefix or skipping a failed session. Reports
contain digests and diagnostics, not copies of the raw evidence; callers must
keep the original bundles and context.

## Rollback, freshness and failure behavior

| Counterexample | Result or remaining limitation |
| --- | --- |
| Valid shorter/rewritten chain against unchanged checkpoint | Summary mismatch; refusal |
| Old checkpoint against newer pinned digest | Anchor mismatch; refusal |
| Matching digest but wrong supplied scope/custodian/generation | Refusal |
| Missing pin or unknown digest | Refusal; no bootstrap from local history |
| Both old checkpoint and old pin supplied together | May match; freshness remains UNKNOWN, rollback protection false |
| Attacker replaces checkpoint, pin, context and chain consistently | May match; no authentication or authority follows |
| Reordered/duplicate/gapped objects or sessions | Refusal; no sorting, deduplication or skipped prefix |
| Missing/partial/truncated checkpoint, frame or terminal | Refusal of complete comparison |
| Omitted uncheckpointed session or omitted acquisition failure | Cannot detect; no campaign/request ledger or completeness assertion |
| Capacity terminal | Reason preserved; refused incoming sample may be absent, as in writer contract |
| Prior fsync/write uncertainty but currently valid bytes | Unknown crash durability; bytes cannot upgrade the prior failed call |

Generation equality is relative to the provided pin; this stateless function
has no trusted latest-generation memory, revocation state, clock or freshness
threshold. Passing an older pin is not distinguishable from legitimate historic
review. No stale-by-wall-time claim is made. There is no append-only checkpoint
history or cross-generation transition policy in this slice.

No failure diagnostic is written. Errors carry fixed bounded codes and explicit
`diagnostic_persisted=false`, `evidence_incomplete=true`; the caller still owns
all original bytes. A later persistence layer must durably retain failures and
uncertain publication states without retries that erase history. This candidate
cannot promise persistence on disk-full, crash, omitted input or caller loss.
Existing custody failure-persistence semantics are unchanged and covered by its
focused regression suite. No new storage writer is justified for this slice.

## Bounds

At most 8 sessions, each at most 64 frames, 16,384 raw bytes per frame,
65,536 bytes per supplied frame, 4,096 header bytes and 4,096 terminal bytes.
Per-session total is at most 1,048,576 bytes, with chain length independently
capped at 1,048,576 minus the 4,096 terminal reserve. Checkpoint <=65,536 bytes,
pin <=4,096 bytes, opaque context 1–16,384 bytes. All containers and byte sizes
are checked before replay. Total supplied evidence is therefore at most
8,474,624 bytes. Caller allocation before the call is outside the bound.

JSON is ASCII canonical, depth <=8 checked before decode, <=4,096 traversed
nodes (including mapping keys), strings <=4,096 characters, numeric tokens
<=20 characters, no floats/NaN/Infinity. Depth and byte limits bound decoder
allocation before the post-decode node check; the node cap is not a claim that
the decoder allocates only that many nodes. Byte caps and exact container types
prevent unbounded iteration or caller hooks. The existing raw parser keeps its
own stricter limits. No runtime assertions enforce safety; checks survive `-O`.
These are representation/work bounds, not RSS, latency, disk-block or full
capture resource guarantees, and do not enlarge any existing Gate-3 budget.

## Real independently retained evidence and authority still needed

Before any real checkpoint can be accepted, a separately authorized custodian
outside the recorder's rewrite/rollback domain must actually retain the exact
checkpoint, original session evidence and receipt context. Its controlled
ledger must bind scope, session identities/order, object digests, final heads,
terminal digests and receipt context, and record every submitted/rejected or
uncertain acknowledgment. Evidence of actual receipt and durable retention
must come from that custodian's completed operation, not a local hash, fsync
return, generated key, local PASS string or copied writer terminal.

A separately accepted authority must supply the current anchor through a
channel the recorder cannot rewrite, with authenticated custodian identity,
scope, policy, exact bytes, current generation, revocation and continuity
evidence. That authority needs an independently retained monotonic history or
equivalent antirollback mechanism, procedures for equivocation/forks, missing
generations, duplicate nonces, cross-scope substitution, interruptions and
stale anchors. Receipt times, if required, need qualified independent time
and causal receipt evidence; a local wall clock is not sufficient. No numeric
freshness rule or cryptographic trust anchor is invented here.

The independent replay reviewer/operator must receive original immutable bytes
from that retained evidence, a separately accepted anchor and policy, exact
source/tree/build/parser bindings, and the complete inventory including
refusals/interrupted objects. It must recompute the chain and compare to the
independently retained anchor, preserving mismatches and unknown gaps, then
produce a detached exact-byte report and genuine completed terminal under
accepted identity/custody. A different-model source review alone is not an
independent custodian or clock source. No such evidence exists in this task.

Real retention additionally needs the separately authorized private root outside
Git and /tmp, reviewed permissions/ancestry/locking/durability/space policy and
protected failure ledger. Method, original calibration/reference accuracy,
host/build/boot/namespace applicability, drift/continuity and time validity
remain separate qualification gates. Any real observation, authority installation,
execution/provider integration, revised executable window and G3-L/G3-E/SHADOW
decision remain outside this candidate. No protected master, V10, Axiom,
credentials, services, financial/order path or production path was touched.

## Focused validation and review handoff

The new tests use `unittest` assertions in both Python modes. They construct only synthetic
fixture bytes; the one writer interoperability case uses a temporary directory
inside this worktree and removes only that scratch directory. No native probe
test, C build or host clock observation is run. Capability-denial tests prohibit
clock/socket/process/random/file acquisition while executing the pure API;
an import allowlist and writer-helper denial verify its dependency boundary.

| Focused command | Result |
| --- | --- |
| `python3 -B tests/test_v11_gate3_clock_checkpoint.py` | 22/22 passed |
| `python3 -O -B tests/test_v11_gate3_clock_checkpoint.py` | 22/22 passed |
| `python3 -B tests/test_v11_gate3_clock_custody.py` | 34/34 passed |
| `python3 -O -B tests/test_v11_gate3_clock_custody.py` | 34/34 passed |
| `python3 -B tests/test_v11_gate3_clock_dossier.py` | 73/73 passed |
| `python3 -O -B tests/test_v11_gate3_clock_dossier.py` | 73/73 passed |
| `git diff --check` (including staged candidate) | Passed before commit |

Total: 129 passing cases in each mode. The custody suite's expected
multithreaded-fork deprecation warning accompanies its synthetic deadlock
regressions. No native acquisition suite or full repository suite was run.
Checkpoint adversaries include every proper truncation prefix of a fixture
checkpoint and pin, valid-old-chain rollback, fully rewritten chains,
context substitution, scope/generation mismatch, duplicate/reordered sessions,
invalid/reordered frames, nested-schema and authority forgery, parser bounds,
exact-type hostile inputs, retained failure records and zero/max-count sessions.

Exact final commit, tree and diff are reported in the author completion message
to avoid self-referential Git hashes here. This candidate is review input only;
the author does not merge or approve it. No acceptance score or missing Gate-3
identity changes. `CUSTODY_UNQUALIFIED / G3-L NO_GO / zero credit` remains fixed.
