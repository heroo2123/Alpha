# Gate 3 restart R6/R7 review — exact d080eac / 1fa3902

**Scoped PASS: R6 and R7 are resolved. No blocking finding in the repair diff.**

Independent Astra/high review of the Sonnet/high code/test commit
`d080eac0a7b431cbd1d70ca70f8adbccaad3719f` (tree
`d828653843743ed85987398afbdea3361b202f24`), at documentation follow-up HEAD
`1fa39026662075974ecc31a84c716774eed7f38e` (tree
`d8d1ecc8e5f21ba0678ca413e68275d2ab9038af`). The three-file diff from
`7bc627e` contains six store-code line changes, regression tests and the handoff.
The candidate stayed clean, unchanged and unmerged throughout this review.
The [prior findings](V11_R09_GATE3_STORE_RESTART_REVIEW_7bc627e.md) and
[restart design](V11_R09_GATE3_STORE_RESTART_DESIGN_7b5a235.md) remain the scope.

Independent verification: **285 affected tests passed in 5.15 s; 161 independent
probes passed in 2.94 s**. The latter retain 150 prior non-defect controls,
including 54 SIGKILL cases and 28 deterministic survivor cases, and add 11 repair
acceptance cases. The three old defect oracles remain preserved in their original
file and are deliberately excluded from this acceptance run. Two author-suite
and four independent-suite warnings are expected Python multithreaded-fork
warnings from the deliberately exercised failure scenario. No full release suite
was rerun; accepted release resolution `6ec371e` remains an ancestor of main.

[Reproducible probes](V11_R09_GATE3_STORE_RESTART_REVIEW_d080eac_probes.py) and
[completed terminal](V11_R09_GATE3_STORE_RESTART_REVIEW_d080eac_terminal.json)
bind exact commands, identities and SHA-256 evidence. Logs are in
`/tmp/alpha-v11-gate3-restart-review-d080eac/`.

## R6 — process ownership before mutex

Both public read/seal paths now reject foreign process identity before entering
`self._mutex`; their existing locked usability and callback checks remain.
Fork cleanup still closes inherited descriptors without LOCK_UN or acquiring the
copied mutex. Independent tests hold the mutex in a parent writer's recorder,
fork from another thread, and require the exact STORE_OWNER_PROCESS rejection
within two seconds. Read, seal and both operations after child close all pass.
A competing open still fails while the parent writer is paused, and the parent's
completed seal later recovers with original clocks and UNKNOWN acknowledgement.
Two additional probes substitute a mutex that fails on entry and confirm a foreign
PID is rejected without any attempt to acquire it.

Non-blocking test-harness note: the new author fork cases bound their response
assertion but do not kill/reap the child if that assertion times out. A regression
could leave a blocked child behind. The independent probes provide bounded cleanup
on failure. Add equivalent cleanup to the author tests in the next test batch;
this does not invalidate observed prompt rejection or require reopening R6.

## R7 — one encoded descriptor bound

MAX_DESCRIPTOR is 4096, checked against canonical bytes before the first metadata
O_CREAT, and passed to the recovery reader. Constructor failure releases the root
lock. Independent fixtures use the actual limit, without monkeypatching it, and
all four context strings at their maximum 128-character length. Carefully chosen
ASCII, escaped quote and non-BMP characters produce exactly 4095, 4096 and 4097
encoded bytes on the disposable root. Both accepted cases seal, read and recover
twice with unchanged clocks; the 4097-byte case rejects with no create attempt,
metadata or partial initialization. A subsequent normal initialization succeeds.
The original three-field Unicode repro and a four-field Unicode variant also
reject before leaving metadata. The bounded recovery reader and other resource
caps are unchanged. No migration or cap expansion was introduced.

## Acceptance boundary and next unblocked batch

This closes the two repair findings for the standalone synthetic store. It is
not physical power-loss/storage qualification, real clock/provider attestation,
feature or historical runtime-use proof, budget completion, learner admission,
or an integration decision. Preserve candidate merge/publication, provider capture,
G3-L, SHADOW and learner holds. GEFS forward work remains owner/root gated;
no authority installation or forward sample is authorized by this report.

The next concrete offline gap is the restart design's **composed accounting
contract**. The current `test_synthetic_bytes_seal_decode_and_clock_compose` uses
the legacy store, closes the budget before opening it, and attaches a disconnected
ClockSequence afterward. Standalone restart PASS cannot certify that composition.

Route one Sonnet/high batch in the same preserved worktree, based on `1fa3902`:

- Add `tests/test_v11_r09_gate3_restart_composition.py`, using the actual existing
  DurableBudget, SyntheticExchange, decoder and VersionedImmutableObjectStore APIs
  on disposable synthetic fixtures. Acquire budget ownership before store
  ownership and close in reverse order; failure of the second acquisition must
  release only this invocation's first acquisition.
- Prove a healthy synthetic receive/decode/raw-seal and dependent feature-manifest
  seal roundtrip with original clock evidence and retained descriptor/head pins.
  Keep raw and manifest receipts distinct; recovered acknowledgement is UNKNOWN
  and historical_feature_eligible stays false. Synthetic clock fixtures must not
  become claims of measured attestation or historical availability.
- Exercise an incomplete reserved/delivered budget attempt beside a successfully
  committed object, and the converse (budget completion with a held store). Reopen
  the real APIs and assert original budget bytes, reservation, in-flight status,
  start/window limits and refusal semantics remain; store success cannot clear or
  complete the budget. No helper may synthesize capture success from a receipt.
- Cover repeated reopen and cross-boot historical store reads while budget resume
  remains refused. Preserve stream-violation state, missing/late dependencies and
  all existing failure classifications. Do not invent a new operational admission
  API or alter budget/launch/store semantics to make a test pass.
- In the existing store test file, add bounded kill/reap cleanup to the two author
  fork tests. Update the author handoff with exact tests and commit. This is a
  three-file tests/documentation batch; retain the legacy fixture as lineage.

If actual APIs cannot meet those invariants, preserve the reproduction and route
an exact finding for review before changing shared budget/store/launch behavior.
Run the new composition tests plus the existing five affected suites. Terminal
must bind the exact commit/tree, results and holds; fresh different-model review
is required. No provider request, launchable manifest, real store, service, network
adapter, clock installation, production or financial action belongs in this batch.
A later separate integration decision must assess the full held branch against
then-current main; this report does not remove its merge hold.

## Recovered state

Main was clean at `7a054e9`, equal to its local tracking ref. Held candidate and
SHADOW `15e99bd` worktrees were clean; no implementation worker was active.
New commissioning writes after 22:42 UTC were manager/watchdog status files only.
The private FINAL-REVIEWED master SHA-256 matches its pin. V10 demo, PAPER scanner,
controller and execution units are inactive; execution is masked. Protected
`/etc/alpha-v11` and model-authority paths are absent. Disk has 4.3 GiB free and
memory about 939 MiB available. Existing AxiomTrade activity was observed only
for shared-host resource awareness. No service, V10, AxiomTrade, authority or
financial change was made. No new C/J/E/A credit: **91/200 (45.5%), formal 1/50;
NOT_READY_TO_FUND**. This routed review is complete; the next batch is handed to
the router rather than duplicated by a second local worker.
