# Offline resource preparation refusal candidate — 2026-10-03

**SYNTHETIC_ONLY / UNQUALIFIED / NO_GO / qualification credit 0.** Execution,
provider, host, allocation and SHADOW authority are false. This candidate requires
independent different-model exact review; author tests are not approval.

Sole Codex Astra/high author in
`/tmp/alpha-v11-gate3-resource-custody-offline-20261003`, based on
`de879af00f401c2cef6b0453bec61111987b9602`. No other writer/worktree was modified.
Final candidate commit/tree are recorded in the external candidate evidence and
final handoff, avoiding a self-referential commit identifier here.

## Delivered scope

Three new files only:

- `tools/v11_gate3_resource_preparation_model.py`: pure bounded byte parser and
  immutable refusal result; fixed syscall-result script, no syscall implementation.
- `tests/test_v11_gate3_resource_preparation_model.py`: 20 test methods, including
  adverse tables and 100 repeated identical replays.
- This handoff.

This is the assessment's permitted **dependency-first refusal slice**, not its
entire proposed custodian. It simulates read-only ownership/history reconciliation,
prospective resource checks, pre-existing emergency intent capacity, and native
same-inode report backing and refusal persistence. It ends with
`CONSTRUCTOR_ALLOCATION_MAP_UNIMPLEMENTED`, even when every supplied predicate and
syscall double agrees. It refuses every constructor operation at every position.
There is no route to allocation permission, dispatch, release or SHADOW.

The current runtime's existing ordering remains unchanged: ledger constructors
create journals/locks, store initialization creates metadata, ReportSink allocates,
and GateRuntime binds session context before its resource check. The model proves
its own ordering barriers and refuses those constructors; it does **not** repair or
qualify current runtime ordering. Wiring them now would exceed this slice.

## Inputs and interpretation

Public API:

```python
result = evaluate(projection_raw, scenario_raw, expected_projection_sha256)
```

Both raw inputs are exact canonical ASCII JSON bytes, sorted keys and compact
separators. The parser accepts at most 32,768 bytes per input, depth six, 16 syscall
results, 96-character record strings, and nonnegative signed-64 integers. It
rejects duplicate/unknown keys, floats/nonfinite numbers, booleans as quantities,
subclass byte inputs, noncanonical encodings and overflow. Numeric derivations for
both observations are checked before simulated writes. Input dataclasses and the
returned result are frozen and slotted; arrays become bounded tuples internally.
There are no supplied callbacks or arbitrary objects to execute.

`Projection` is an intentionally narrow, caller-supplied resource projection:
source-budget and manifest digests; runtime disk/memory; V4 formula disk; report;
manifest floors; uncovered disk/memory; inode allowance; fresh-ceiling conjunction.
The separate expected digest binds its **exact bytes**. It is not an independent
anchor. The module does not read the source budget/manifest or verify extraction,
V4 validation, schedule completeness, source custody or the caller's ceiling flag.
A focused test derives the three amounts and ceiling conjunction from the current
existing offline calculator's synthetic fixture. That test is arithmetic
compatibility, not a production validated-plan export. The parallel export worker's
files and APIs are not used or duplicated.

Full prospective disk demand is the conservative sum of runtime disk, V4 formula
disk, uncovered disk and emergency metadata. Memory includes runtime, uncovered
peak and emergency memory. Supplied outstanding and bounded foreign demand are
added separately. Disk and memory floors cannot be below 2 GiB and 512 MiB; stricter
supplied floors apply. Pool/free-disk floors, quota, inode and single-ancestor
headroom are checked separately. Already occupied emergency backing is not added
back to free space or subtracted twice. Report backing is removed from outstanding
demand only after its two supplied fsync successes; the after-observation then
checks the remaining demand. Truncation never refunds capacity.

The deliberately limited domain admits only a fabricated persistent native local
single mount, no aliases, one completely visible memory ancestor, known quota,
and enforced all-consumer bounds. Unknown foreign consumers, monitoring-only
bounds, hidden ancestry, multiple/aliased mount scopes, tmpfs, shared/CoW storage,
and unsupported backing refuse. Domain identity includes namespace, mount,
device, pool, quota and root; after-state changes refuse. These are assertions,
not host truth: writing the accepted strings cannot grant qualification.

Ownership includes a supplied process/start token, boot, history/independent-head
tokens, exclusive-owner fact, inherited-handle fact and predecessor disposition.
Only `EXITED_REAPED_ALL_HOLDERS` can pass that synthetic predicate. A termination
request, live child, expired lease, timeout or unknown cannot. A different process
or inherited handles refuses before even the modeled recovery barrier; no lock
or mutex is acquired. The fork test is a **fabricated inherited-lock case with a
lock-construction bomb**, not a real kernel fork/lock qualification test. No process
is terminated. Missing/empty history cannot create genesis; reboot and rollback
facts refuse. Supplied tokens are not independent durable checkpoints.

## Fixed simulated preparation and refusal order

1. Parse/bind all bytes and arithmetic; fence predecessor and reconcile history.
2. Check scope and full prospective floors before any mutating event.
3. Require already supplied exclusive KEEP_SIZE emergency backing for four 4 KiB
   records (intent, partial acquisition, refusal, recovery), retained ownership,
   memory and metadata allowance. Unknown/exhausted backing refuses without writing.
4. Match intent write and fsync doubles. The fresh intent's logical EOF is 4 KiB;
   extending it to the physical reserve length is refused.
5. Match native exclusive 16 MiB report allocation, file fsync and directory fsync.
   Partial/sparse/emulated/shared/unsupported backing or a replaced inode refuses.
6. Recheck the supplied post-state and retain all claims. Refuse constructors.
7. Match a bounded refusal write, shrink on the same inode, file fsync,
   no-replace same-inode link, and directory fsync. Both names remain; no unlink or
   reallocation transfer, cleanup or release is modeled.

Every failure is terminal. No retry, recursive allocation to log failure, or
fallback constructor exists. A syscall result saying success with the wrong count,
logical size, backing or identity still refuses. Short writes, allocation failures,
all fsync failures, link failure, missing suffixes and extra/reordered operations
stay `UNCERTAIN_HELD`. A complete script is only
`SYNTHETIC_REFUSAL_PERSISTED_HELD`, still UNQUALIFIED/NO_GO with false authority.

`held_report_bytes` describes the minimum verified **within the simulation**;
zero is not proof of absence of partial allocation. `outstanding_disk_bytes`
retains the entire unverified portion. Malformed input can produce zero numeric
fields because no bounded accounting could be derived, not because claims were
cleared: every result also says `NO_RELEASE_UNKNOWN_CLAIMS_RETAINED`, released
bytes zero. The original planned constructor-refusal reason remains explicit
when an intermediate syscall/persistence failure becomes the reported reason.
Denominator is always 2,713; no slot receives credit or becomes eligible.

## Validation and retained evidence

**34 distinct tests passed in each of normal and optimized Python:** 20 new model
tests plus all 14 existing offline-budget tests. Reproduction from this checkout:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -B -m unittest discover -s tests -p 'test_v11_gate3_*resource*.py' -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -O -B -m unittest discover -s tests -p 'test_v11_gate3_*resource*.py' -v
```

The retained runner loads exactly those two modules. An initial runner redundantly
called nine function helpers already wrapped by the existing budget TestCase;
its 43-invocation logs are preserved as `*.duplicate-discovery.log`. Corrected
34-distinct-test logs are the results cited here.

| Retained file under `/tmp/` | SHA-256 |
| --- | --- |
| `alpha-v11-resource-preparation-normal.log` | `db53db0cbbad1bcb223879bbaa7b36195a2c97bc300b953befefce1bc391d310` |
| `alpha-v11-resource-preparation-optimized.log` | `a094c683a9766093c54c19e52a44f5f730b53b964886f390156efc4dbcfdb20e` |
| `alpha-v11-resource-preparation-test-runner.py` | `1b7b9306e4ca095b1bbe41f31b597890b794c7a3f9a5d874e016669e6b358091` |
| `alpha-v11-resource-preparation-evidence.json` | `59c57170c6b553912e9a0bd9699ee3e2faf6142cb04abddee2018e747823da5a` |

The 34,009-byte replay bundle contains one projection, five complete supplied
scenarios/results, and exact code/test/source/log hashes. It includes bindings to
the reviewed assessment, its observation companion and the retained
`/tmp/alpha-v11-host-feasibility-review-a6f98ad.final` PASS. That PASS covers the
assessment only, never this code. Reviewed assessment commit:
`a6f98ada98a80b1af1bf868a8a05642c62d396f1`, tree
`0cbaaadbae7f2dde42186107c896d92fa2428812`.

Tests use small in-memory doubles; no allocator/resource backing is created.
Existing budget regressions use their bounded temporary fixtures. Free disk was
2,856,812,544 bytes at the pre-handoff check, above the 2 GiB floor; final disk and
Git checks are retained with the external candidate metadata. No full/native or
runtime suite was necessary because no existing implementation was edited.
Default command sandbox startup failed with a bwrap loopback error; reviewed
execution fallback was used for scoped reads, edits and tests. No automatic
approval rejection occurred.

## Explicit remaining dependencies

Not delivered: authenticated exact budget extraction/production plan export;
complete role-to-inode/range and overlapping-lifetime accounting; occupied-journal
replay and custody-journal format; constructor-safe emergency bootstrap; actual
native allocation qualification; multiple mounts/shared pools/quotas; tmpfs and
full cgroup ancestry; bounded real foreign-consumer enforcement; exact report
content serialization; retained independent checkpoints; kernel fork/holder
fencing; actual persistence/crash recovery; transport/decoder termination and
reaping; post-budget/post-dispatch-intent revalidation; retention transfer/release;
resource freshness and host-clock qualification. The model's fixed report/intent
sizes and syscall tags are synthetic schema constants, not an accepted storage
protocol. A complete snapshot replay cannot prove rollback resistance or durability.

No real filesystem reservation, provider/network call, runtime/launcher/admission
wiring, root/service/cgroup change, financial action, V10/Axiom or private evidence
access occurred. No gate was weakened. Status remains **91/200, formal 1/50,
77 missing identities, G3-L NO_GO, NOT_READY_TO_FUND**.

Next step is a different-model exact review of the final candidate/tree, all three
files and retained evidence. Do not merge or infer approval from author checks.
