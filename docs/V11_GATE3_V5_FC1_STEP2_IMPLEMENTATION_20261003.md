# FC1 step 2 — pure schemas and synthetic bounds

Candidate for independent exact commit/tree review. No integration, execution,
provider enrollment, host qualification or acceptance authority. The merged
[execution contract](V11_GATE3_V5_FULL_COHORT_EXECUTION_CONTRACT_20261003.md)
and the separately reviewed [IA1 amendment](V11_GATE3_V5_FC1_INTERFACE_AMENDMENT_1_20261003.md)
with their companions remain unchanged. This successor uses IA1 identities;
original FC1 schema bytes refuse.

## Interfaces and boundary

`tools/v11_gate3_v5_fc1.py` implements the exact execution-profile,
allocation-plan, record-plan and timing-plan envelopes from §§1/3/4/5. Readers
accept canonical supplied bytes only, reject unknown/missing keys and versions,
and return frozen dataclasses containing tuples, immutable Refs and original
bytes. These are values, never validated-plan capabilities. Record vocabulary
is the contract's closed synthetic transition certificate; no production
serializer or producer is enrolled. No existing schema or consumer is patched.

Checked integers reject booleans, floats and overflow. JSON has a byte/token/depth
preflight and decoded string/array/key bounds. The supplied-byte implementation
has a conservative 32 MiB aggregate referenced-input ceiling; it does not claim
to stream or accept a 1 GiB supporting artifact. Streaming/segmentation and store
lifecycle belong to step 3. The allocation reader retains the inherited 3,600
array ceiling as well as checking the 4,096 physical-object bound.

`derive_costs` derives full-cap bytes plus the one global abort allowance,
conditional IA1 actual-start timing with two charged copies of the start-bound
sum and local/finalization/other-jitter/clock costs, physical-object
peak with one temporary, new store events, existing-plus-remaining journal
counts/bytes, and conservative allocation-domain sums. Unqualified lifetimes
receive no overlap/alias credit. It performs no physical allocation or host
probe. Both original V4 and runtime disk estimates, original object/event and
additive timing diagnostics, and the historical size estimate remain visible;
none becomes a replacement admission path. The caller must not treat arithmetic
or a successfully constructed dataclass as authentication.

`tools/v11_gate3_v5_fc1_synthetic.py` is a separate, closed **synthetic test
language**, not a V5 production manifest validator, native interpretation
registry or accepted exporter. It constructs immutable full-byte projections
in `G3_V5_FC1_SYNTHETIC_*` domains. The production FC1 schema names are reserved;
V4, V5 and production manifest versions refuse in this entrypoint.
`consume_accepted_fc1` always refuses TRUST, including caller-supplied self-pins,
credentials or objects that implement an apparent trust interface.

The synthetic language binds all seven input roles, full execution-profile
bytes, plan/review/build/dependency hashes and lengths, original requested/trial
keys, complete request and role records, and canonical closure bytes. It
checks the ordered GEFS/IFS/AIFS inventory, 8,139 role uses, shared HIGH/LOW
captures, scoped pre-freeze synthetic facts, rights/cutoff/restriction predicates,
and semantic plus physical graph edges together. Every declared imported object
has actual supplied bytes. Missing costs/imports, dangling or cyclic references,
extra artifacts and combined graph excess refuse. Domain sums include source
copies, temporary, journals/tails, snapshots, parent/child decoder allowances,
report and emergency allocations. Fixture costs are deliberately synthetic;
they do not prove a successful native decoder or serializer bound.

The entire inherited 79-row preservation map is pinned by canonical digest,
including V4-named identities, stage/scope/disposition and zero credit. Missing
stays 77; H1–H6, A1–A8, G3-L and G3-E remain gates. Fresh-plan refusal diagnostics
always retain 2,713 rows and 8,139 MISSING cells. They do not replace runtime
receipts, original reasons, debits, or historical evidence.

The synthetic fixture pins the original contract and IA1 document plus both
companion sets and IA1 schema bytes by SHA-256 and length. Its cost/build
references remain synthetic and grant no independent trust.

The IA1 record vocabulary places TERMINAL before CAPTURE_RECEIPT within the
same twelve-record bound. Pure supplied-byte replay binds the receipt's
`session_terminal_head` to the actual terminal hash and checks identity,
context, outcome and declared acknowledgement. Missing or unacknowledged
receipt leaves custody incomplete; duplicates and wrong heads refuse. The
acknowledgement set is test input, not store durability proof.

The timing plan adds `start_bound_ms` per request and two JITTER entries,
`START_BOUND_TOTAL` and `DISPATCH_BOUND_TOTAL`, each equal to its checked sum.
The supplied trace check requires original-clock fields around the declared
first transport activity, a bounded bracket, immutable predispatch deadline,
confirmed close and receipt custody, and next permission no earlier than the
previous upper bound plus spacing. It cannot prove transport-build ordering,
clock authenticity or fsync. Inherited close-plus-spacing consumers stay intact.

The selected read bound is `ceil(cap/65536)+1 <= k <= min(cap+1,65536)`:
4 MiB needs 64 payload calls and a 65th counted EOF probe. The pure trace
charges all supplied reads and data/eager/queued/late bytes before evaluating
success, retains the full cap plus global abort allowance on uncertain
accounting, and poisons extra calls, missing EOF, excess and adapter violations.
Only an exact qualified adapter can expose hidden buffering and all error,
cancel and close paths.

## Focused matrix coverage

| Cases | Implemented step-2 checks |
| --- | --- |
| FC01 | Exact canonical schema/ref bytes, round trip, immutable values, version/key/number refusals |
| FC02 | Full provider/member/native-hour order, 612-point IFS subset loss, duplicate/omitted/reordered rows |
| FC03 | One/two events sharing 2,713 FIELDs; all event/provider pairs; original keys; no RAW eligibility |
| FC04 | Full frozen caps, selected ranges, global abort reserve, byte equality/+1 and attempt ceiling |
| FC05–06 | IA1 recurrence/equality/+1, start-bound double charge, whole-second deadlines, P/Z floors, phase completeness, successful/failure costs, no zero jitter, sequential CPU bounds; pure supplied trace checks for brackets, spacing, delay, close and abort lag |
| FC13/16–18 | Pure adverse terminal/receipt replay and counted stream traces, including EOF, missing durability, wrong heads, k+1, short reads and charged overdelivery; integrated store/adapter cases remain future work |
| FC19 | Scoped synthetic role facts, object/index coherence, official metadata role, original-time/rights bounds, frozen confirmation modes and unknown interpreter refusal |
| FC20 | Seven input roles, exact byte bindings, isolated producer/reviewer fixture identities, stale/self-pin refusal, unconditional production trust refusal |
| FC21 | Literal 79-row map equality, 77 missing, all holds/gates, no G3-L/G3-E credit |
| FC22 | Original cutoff ordering, metadata strict-before, seal upper/decision lower, uncertainty and retained global restriction predicates |
| FC23 | Constructed 2,713-RAW/two-event graph with actual support bytes, all necessary inequalities checked together; missing artifacts/costs and coupled-edge overflow mutations |
| FC24 | Network, file/private-path, environment credential and subprocess/service tripwires; immutable input/no-authority output; full refusal denominator |

This covers the **pure step-2 portions** of the matrix. The original FC matrix
continues to describe future integrated acceptance tests. This candidate does
not claim to execute store crashes, ledger transitions, real scheduler/stream
behavior, successful native decode, actual external authentication, all inherited
production semantic checks, or actual-byte G3-E replay. Those require later
ordered steps and independent reviews. The coupled fixture is a synthetic
boundary control, not a production all-gates PASS or the arithmetic dossier's
unconstructed I=64 witness.

## Verification commands

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_v11_gate3_v5_fc1.py'
PYTHONDONTWRITEBYTECODE=1 python3 -O -m unittest discover -s tests -p 'test_v11_gate3_v5_fc1.py'
PYTHONDONTWRITEBYTECODE=1 python3 tests/verify_v11_fc1_ia1_documents.py
PYTHONDONTWRITEBYTECODE=1 python3 -O tests/verify_v11_fc1_ia1_documents.py
```

The unittest assertions remain active under `-O`; production refusals use
explicit exceptions, never `assert`. Tests read only the fixed public contract
companions before activating supplied-byte containment. No protected/private
master, credentials, provider, service, network, native decoder or host allocator
is accessed. Independent exact review is required before integration.

The new suite uses unittest assertions and explicit refusal exceptions in both
modes. The IA1 checker independently replays the reviewed document source pins.
Verification results for this successor are retained in the
[repair verification log](V11_GATE3_V5_FC1_STEP2_IA1_REPAIR_VERIFICATION_20261003.md)
and reported with the exact commit/tree for independent review.
