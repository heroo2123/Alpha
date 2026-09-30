# Gate 3 offline composition review — exact 0b7209d

**Scoped PASS for the synthetic composition batch. No blocking finding in the
three-file diff. All integration and operational holds remain.**

Independent Astra/high review of Sonnet/high commit
`0b7209dba3e0c6d3ab55aa984881b83855ae0692`, tree
`87cbb35b6615d21b36a989197e490acebdfe0ad0`, against parent `1fa3902` and the
[preceding review's composition contract](V11_R09_GATE3_STORE_RESTART_REVIEW_d080eac.md).
The held candidate remained clean, unchanged and unmerged throughout review.
Only the new composition tests, existing fork-test cleanup, and author handoff
changed. No budget, launch, store, clock or decoder implementation changed.

Independent rerun: **292 affected tests passed, two expected multithreaded-fork
warnings, 7.32 seconds**. Supplementary independent tests: **9 passed in 0.52
seconds**. The [reproducible probes](V11_R09_GATE3_COMPOSITION_REVIEW_0b7209d_probes.py)
and [terminal](V11_R09_GATE3_COMPOSITION_REVIEW_0b7209d_terminal.json) bind exact
commands, candidate identity, results and evidence hashes. Logs are under
`/tmp/alpha-v11-gate3-composition-review-0b7209d`. No full release rerun; the
accepted `6ec371e` release resolution is still an ancestor of current main.

## What the evidence establishes

The author tests execute the actual budget, synthetic exchange, decoder and v1
store together on disposable fixtures. They cover ordered ownership, failed
second acquisition, a received/decoded raw object and distinct dependent manifest,
missing dependencies, incomplete accounting beside a committed object, completed
accounting beside a held store, stable stream-violation refusal and cross-boot
historical reads. Receipt recovery preserves UNKNOWN caller acknowledgement and
false historical feature eligibility. Store receipts never complete budget work.
The fork-test failure path now kills and reaps its own child before unwinding;
accepted-path assertions are retained.

The seven author cases alone do **not** directly assert original start/window
limits or compare dependency clock cutoffs. This review supplies those assertions
in its durable supplementary probe file; the scoped verdict relies on both sets:

- With either a committed or an unresolved store, repeated budget reopen preserves
  exact bytes, reservation, count, attempts, window origin and last request start.
  Too-early starts and starts at/after the original window end refuse without
  changing journal bytes. Equality at the minimum interval succeeds. The window
  remains anchored after a second completed request. Changed duration, interval,
  byte/request cap or boot identity refuses replay, without rewriting the journal.
- Incomplete and stream-violated attempts retain their exact state and journal
  bytes through pinned same-boot/cross-boot/same-boot store recovery. Recovered
  object success cannot discharge either refusal. Delivered violation bytes stay
  charged; original store clocks remain byte-equivalent through typed equality.
- Two raw dependencies and their manifest have distinct, coherent synthetic
  clocks. The existing ClockSequence API rejects a late second dependency and a
  late manifest independently; all phases satisfy the necessary timing bound only
  at the final equality cutoff. No test invents an operational admission API.
  Even all timing checks passing leaves historical_feature_eligible false.
- Instrumenting the real constructors/close methods while invoking the author's
  composition helper proves budget-before-store acquisition and reverse close.

Synthetic samples are supplied fixtures, not measured attestation. The author's
absence-of-attribute checks are not evidence of clock trust or capture success;
acceptance instead depends on explicit receipt flags, preserved evidence and the
actual refusal assertions. The store has no feature-admission or clock-attestation
authority. Its dependency checks enforce committed references; temporal eligibility
is a separate necessary-condition check, not a claim that seal rejects late data.

## Next gate and limits

This closes the requested offline composition review, not the full held branch's
integration decision. That branch adds nine files / 4,945 lines relative to its
common ancestor with main. A separate bounded Astra/high integration reviewer is
the next task: assess the full held lineage against exact newer main, review import
and entrypoint boundaries, verify preservation of current code and docs, construct
and test only a prospective combined tree in an isolated review worktree if safe,
and write a conditional integration verdict tied to exact commits/tree.

That reviewer may not merge main or the held branch, publish, start capture, create
a private launch manifest, install clock/model authority, or admit SHADOW/learner
work. A later coordinator must inspect its actual report, terminal and tests before
any separately authorized integration action. Review-document publication remains
pending the previously recorded automatic approval rejection; no push was retried.

Physical storage qualification, real provider/clock evidence, historical runtime
use, G3-L, forward SHADOW and learner admission remain OPEN. No operational API was
added and no safety gate was weakened. **91/200 (45.5%), formal 1/50;
NOT_READY_TO_FUND**, unchanged.

## Fresh recovery observations

Main was clean at `d2dbbe0`, two commits ahead of its local tracking ref. Held
candidate and SHADOW `15e99bd` were clean. No Gate 3 implementation/review worker
was active when this task began. Commissioning updates remained manager/watchdog
statuses only; no new forward artifact was admitted. Actual demo, PAPER scanner
and controller units were inactive/disabled; execution inactive/masked. Protected
authority paths were absent and the FINAL-REVIEWED private master matched its
pinned SHA-256. Disk had 4.2 GiB available and memory about 873 MiB available.
AxiomTrade was observed only for resource awareness. No service, V10, AxiomTrade,
private-master, authority, financial or publication action was performed.
