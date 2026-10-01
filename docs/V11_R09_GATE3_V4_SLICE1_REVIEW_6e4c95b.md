# Gate 3 V4 slice 1 independent review — CHANGES_REQUIRED

Astra/high independent review, 2026-10-01. Exact Sonnet candidate
`6e4c95bb9fd0b1627c5bfc0e82f4ecbef92c85a5`, tree
`afc73013e6eb97a400fe193d2c9b839a9f4b97e4`, parent `12eace0`.
Scope: section 7 slice (1) of the independently reviewed
[transport/runtime design](V11_R09_GATE3_TRANSPORT_RUNTIME_DESIGN.md).
**Do not integrate this candidate or start slice 2 yet.** This is a completed
independent rejection, not an incomplete reviewer run. All evidence is offline
and synthetic. No operational approval or C/J/E/A credit follows.

## Findings requiring repair

**R1 — P2: valid journals crossing read boundaries cannot reliably replay.**
`tools/v11_r09_gate3_launch.py:811-818` reads another full 64 KiB when the
buffer contains a partial record, then compares the whole resulting buffer
(including following records) against the single-record cap. The writer accepts
700 one-byte chunks and completion, each record under 1 KiB; replay rejects
this compliant history with `JOURNAL_RECORD_TOO_LARGE`. This breaks existing V3
budget recovery, not just a future V4 path. Bound the unfinished record while
parsing complete records individually; cap reads/allocation and total bytes;
use consistent newline-inclusive record limits on write/replay. Add positive
restarts across multiple 64 KiB boundaries, exact-size records and short reads,
plus oversized and torn negative cases. Do not change journal bytes or migrate
legacy records to hide the defect.

**R2 — P2: a capacity refusal can erase delivered accounting and refund an
uncertain reservation.** `tools/v11_r09_gate3_launch.py:835-843` exempts every
capacity refusal from the failed state. If remaining bytes fit a `complete`
event but not a larger `chunk` event, `consume('one', b'xyz')` refuses with
`JOURNAL_CAPACITY_EXCEEDED`, retaining three uncertain bytes in memory.
`complete('one')` then succeeds: `_state()` resets received/reserved to zero and
clears in-flight. Restart also has zero uncertainty and zero charge. The probe
scales only the fixed byte cap in a disposable fixture to reach this boundary;
it does not change production code. The event-cap test misses this because its
cap rejects completion too. Preserve the full durable reservation across all
post-delivery append failures and block completion/further reads in-process;
restart must retain the unresolved reservation without inventing receipt bytes.
Pre-dispatch oversized-key refusals may remain clean where no bytes arrived.
Also test overdelivery/violation append refusal, byte/record/event exhaustion,
and before/after restart. Never auto-complete an old uncertain reservation.

**R3 — P2: the proposed V4 endpoint separation remains impossible.**
`tools/v11_r09_gate3_launch_v4.py:324-326` requires every endpoint origin/path
match the field source; `:417-424` requires every request use its field path;
`:438-441` requires overhead origin/path equal the field request. Index/cache
IDs still derive from the field object alone (`:418-420`). An explicitly frozen
synthetic INDEX `.idx` path, with recomputed endpoint ID and schedule digest,
is refused at `ENDPOINT_SOURCE_BINDING`. Section 1 explicitly requires distinct
request vs target object paths, reviewed index/object/metadata mapping, and
section 7 explicitly requires a positive separate-path case. Implement those
bindings together; do not merely remove these guards or append suffixes.
Control-domain grouping must be bound to reviewed mappings across related
origins rather than assumed from the current provider/origin-derived ID.
Require positive explicit paths plus wrong-path/purpose, missing or substituted
mapping/validator, implicit suffix and unapproved-origin counterexamples.
Retain the immutable slot inventory, overhead-first ordering and no redirects.

**R4 — P2: V4 accepts an infeasible post-close schedule.**
`tools/v11_r09_gate3_launch_v4.py:477-483` copies V3's
`(N-1)*max(interval, deadline)+deadline+processing+finalization` formula.
With the fixture's four requests, 30-second deadline, two-second interval and
60+60 seconds processing/finalization, it accepts a 240-second cap. Design
section 5 requires 246 seconds: `N*deadline+(N-1)*interval+processing+finalization`.
Change only V4 and test equality and one-second-under boundaries, including
interval greater than deadline. Leave V3's accepted semantics unchanged.

**R5 — P2: V4 resource bounds do not enforce the frozen design limits.**
`tools/v11_r09_gate3_launch_v4.py:124` allows a report reserve as small as one
byte; `:368-371` accepts metadata up to 1 GiB. The existing fixture passes with
4 KiB report storage, versus the design's separate 16 MiB report reserve.
A synthetic candidate with 5 MiB OBJECT_ID and METADATA reservations and
correctly recomputed totals also passes, exceeding section 2's 4 MiB cap.
Enforce the V4 bounds and explicitly bind the journal/report/resource contract
needed by the following slices; do not claim schema completion while relying
on unvalidated opaque policy bytes for these numeric constraints. Actual disk
allocation, host checks and runtime journals remain later-slice work, not
permission to install or allocate protected authority. Add below/exact/above
numeric boundary tests without loosening V3.

**R6 — P2: V4 has no required design independent-review binding.**
`tools/v11_r09_gate3_launch_v4.py:74-99` accepts exactly the five legacy review
names; its closed protocol group provides no explicit new design/review pin.
The valid fixture therefore succeeds with only V3 review references. Design
section 1 requires the independent design review in addition to the existing
original/addendum references. Extend the V4 closed schema with an explicit,
resolved and pinned reviewed-design reference and its independent report/
terminal binding; preserve existing pins and distinct review identities.
Test missing/substituted references and a correct synthetic binding. A valid
manifest still returns a digest only; it must never imply G3-L approval.

## Verification and compatibility

- Exact-candidate affected suite: **290 passed**, two existing multithreaded
  fork deprecation warnings, 9.01 seconds, exit 0. Suites: launch, launch_v4,
  offline_io, restart_composition, message_sizes, collector, store_v1.
- [Independent diagnostic probes](V11_R09_GATE3_V4_SLICE1_REVIEW_6e4c95b_probes.py):
  **9 passed**, 1.37 seconds, exit 0. Seven probes assert existing defects and
  two are positive/negative controls. Passing these diagnostic reproductions
  is evidence for rejection, not acceptance. Replace/invert defect expectations
  into required behavior tests during repair; retain this original evidence.
- All 17 V3 top-level functions, including `validate_manifest`, have identical
  ASTs to the candidate parent. The original tests are preserved. This does
  not clear shared `DurableBudget` compatibility: R1/R2 remain blocking.
- Purpose totals are independently recomputed, exact nested schemas reject
  inflated/shuffled buckets, endpoint IDs distinguish purpose, authority flags
  remain false, and clocks separate declared phases from observation records.
  These implemented parts are useful but do not compensate for the findings.
- The new V4 validator has no transport/decoder/service entrypoint. Inspection
  found no import from production, Brain or SHADOW entrypoints. Existing caps
  reject oversized total files and unterminated records; capacity refusal is
  insufficiently conservative after delivered bytes (R2).
- Initial runner attempts were setup failures (system Python lacked pytest;
  a probe launched from main mixed namespace-package imports). Corrected runs
  use the existing `/home/alphaadmin/alpha-review-test-venv/bin/python` and
  candidate cwd for all imports. Neither setup failure is counted as test
  evidence. No package installation or full-release rerun occurred.

## Newer-main reconciliation and disposition

Main recovered clean at `cb1d24c35e34983e08bce585fa6eda0f81d4704e`, tree
`4b5be3aa03ed2728e1cd31d05556f6da39dfbb40`, one commit ahead of the local
tracking ref. Its changes after candidate base `12eace0` affect only the three
ledgers. `git merge-tree --write-tree cb1d24c 6e4c95b` succeeds with prospective
tree `0af1380fb5b4a62796ab65ca0580268491fc5bdc`. Independently compared tree
entries: every unaffected main blob survives and all four candidate paths
match exact `6e4c95b`. This is conflict-free reconciliation only. No branch,
index or worktree was merged; the rejected candidate remains unmodified.

Read-only recovery: master hash matches `a0e16d9b...563b4a`; scanner/controller/
execution inactive, execution masked; protected model-authority paths absent.
Newest commissioning artifacts are watchdog/manager statuses, not forward
samples. Held SHADOW worktree clean at `15e99bd`; release resolution `6ec371e`
is in main. Disk about 4.0 GiB free; memory about 979 MiB available at recovery.
No duplicate Gate 3 task was active. AxiomTrade was observed only for process
resource awareness. V10 and all services/authority were untouched. Existing
publication hold retained; no push attempted.

Next task: Sonnet/high repairs R1–R6 in the existing isolated
`/home/alphaadmin/AlphaV11_Gate3V4Slice1/Alpha` worktree, preserving `6e4c95b`
and appending new commits. Run repaired counterexamples and the affected suite;
then obtain a fresh different-model exact-commit review and reconcile newer
main before integration. Do not proceed to slice 2, providers, G3-L, SHADOW/
learner admission, root installation, service wiring or financial execution.
The routed review completed synchronously; no persistent worker was launched
in this review invocation. The next router should launch exactly one repair,
not duplicate this completed reviewer. **91/200 (45.5%), formal 1/50;
NOT_READY_TO_FUND**, unchanged.
