# Supplementary engineering estimate

Independent supervisor-batch-1 review — 2026-09-27: reviewed `3c616d1`
against `4210a6c`. Five new cases reproduced policy-digest incompatibility,
accepted conflicts with managed sibling units, and missing stop/start ordering.
The optional legacy-consumer field is preserved: omission now retains the old
canonical policy digest, all managed component names are excluded, and named
consumers render `Conflicts=` plus `After=`. Six added cases include real anchor
verification and refusal of an unapproved target-policy change. **20 focused /
88 relevant integration passed**, no skips/warnings; 740 tracked input hashes
unchanged before/after each run. No broad/full rerun. Exact evidence and scope:
`docs/V11_WORK_CHECKPOINT.md` (independent supervisor-batch-1 review).

Batch 13's requirement-narrowing/closure claims are superseded: master section
35 separately requires installed dependencies, Telegram consumer ownership and
independent host trust; section 40 retains protected risk configuration and
verified operator controls. Optional generated unit declarations do not prove
those outcomes or close R39/R44's offline integration/recovery obligations.
Systemd documents bidirectional Conflicts semantics and requires ordering for
stop completion before start. No installed service or V10 asset was changed.
The reported R37 CI refusal remains unresolved; environment restriction is an
unverified hypothesis. No new C/J/E/A: **85/200 = 42.5% (~43%); 1/50 (2%)**.
R37/R38/R39/R44/R45 PARTIAL; NOT_READY_TO_FUND; V10 unchanged/DEFERRED.

Original supervisor batch 13 (scope and closure claims superseded above) — 2026-09-27: master-grounded correction (section 35/6A)
of R39/R44's remaining-gap framing, plus a real code fix: `host_trust/
weather-paper-authority-v3/authority.py` gained an optional
`legacy_consumer_units` policy field so future-generation controller/execution/
signals units render an explicit `Conflicts=` line against a legacy (V10)
unit, closing the master's actual "Telegram consumer ownership before
side-by-side deployment" code-path gap. Verified read-only against the live
host that V10's installed unit already declares the forward Conflicts= but the
V11 unit's `ConflictedBy=` was empty, motivating the explicit reverse
declaration rather than reliance on unverified automatic symmetry. 4 new /
14 passing tests; 298 passed across the broader authority/host_trust
selection; 54 passed across direct dependents; exactly two files touched; no
V10/credential/private file touched, no unit started/stopped/masked/reloaded.
R37's custody-namespace CI EPERM was diagnosed (preconditions per `man 7
user_namespaces` are met, yet the syscall still fails across two different
code orderings) and recorded as likely GitHub-runner-environment-blocked
rather than re-attempted blind. No new C/J/E/A credit: **85/200 (~43%);
1/50 (2%)**, unchanged. NOT_READY_TO_FUND; V10 unchanged/DEFERRED. Full detail:
`docs/V11_WORK_CHECKPOINT.md` (supervisor batch 13).

Independent batch-12 review — 2026-09-27: published `d4f960d` moved group
clearing before the outer GID map, which a native empty-map probe and five
failing regression cases disproved. Its CI run `36294265757` failed all
11 custody cases on both Python versions before the mapping handshake.
The fixture now clears/verifies groups after helper mapping and verified root
IDs, before the unchanged inner deny-before-map boundary. Permission failures
remain failures with bounded kernel-state diagnostics; no production gate changed.

**21 focused passed, 11 skipped / 0.22 s; 496 related integration passed,
11 skipped, four existing warnings / 54.80 s**, exit 0; all 740 tracked input
hashes unchanged. The review's initial integration scratch-parent mode caused
seven custody failures; correcting only that harness parent yielded 89 broker
passes and then the successful related run. Exact attempts and provenance:
`docs/V11_WORK_CHECKPOINT.md`, `/tmp/v11-codex-b12-tqfc0cuu/`.
No full local regression. Missing local uidmap and the predecessor's separate
post-mapping CI refusal remain unverified; prior recorded WSL custody passes
are preserved. The original batch's universal-denial/first-execution explanation
and blanket R39 owner-only deferral are superseded. Offline configuration
custody and consumer-ownership design/tests remain required under the existing
independent authority; credentials/commissioning remain separate gates.

No new C/J/E/A: **85/200 = 42.5% (~43%); 1/50 (2%)**.
R37/R38/R39/R45 PARTIAL; NOT_READY_TO_FUND; V10 unchanged/DEFERRED.
Next: inspect corrected CI kernel-state diagnostics without weakening custody,
then continue the required offline integrations.

Independent batch-11 review — 2026-09-27: the published first-claim mechanism
blocked an exact legacy owner handoff until the previous configuration polled
again. Reproduced with the predecessor's actual code, then four failing upgrade/
recovery checks. Three ownership checks now retain exact legacy bindings only
when no owner journal exists: polling commits a claim before network access;
reviewed rotation may establish the atomic handoff journal without an old-policy
poll. Existing claims/handoffs always win, and malformed bindings/migrations
remain gated. Thirteen added cases include pending-command authentication,
pre/post-commit recovery and competing initial-claim CAS through separate locks.
**144 focused / 38.43 s; 503 integration / 126.33 s**, exit 0, no skips/warnings;
718 tracked input hashes unchanged through both final runs. No full rerun.

Batch 11 advances database-scoped consistency, not verified cross-deployment
consumer exclusion. Shared local locks remain required; separate databases,
unshared locks, older/uncooperative controllers and cross-host storage/locking
remain open alongside protected operator configuration. No new C/J/E/A:
**85/200 = 42.5% (~43%); 1/50 (2%)**. R39 PARTIAL; NOT_READY_TO_FUND;
V10 unchanged/DEFERRED. Evidence and next offline action: independent batch-11
review in docs/V11_WORK_CHECKPOINT.md.

Original supervisor batch 11 — 2026-09-27 (upgrade and scope claims corrected above): reported closing
the "cross-directory/cross-host consumer ownership" half of the two remaining
purely local R39 gaps the requirements matrix named. Every prior batch's first
bind for a Telegram bot was decided purely by a LOCAL lock file beside the
store; a second consumer in a different directory/host sharing the same store
has its own necessarily-empty local file and could previously claim the same
bot too. `TelegramOperatorCommandPoller._bind_bot_owner` now commits a
CAS-guarded durable `OPERATOR_EVENT` claim (`expected_previous_seq=0`) before
trusting the local file; a same-store intruder is refused via the durable
record even after its local lock file is reset to empty. `handoff_bot_owner`
gained a matching pre-handoff anchor-consistency guard and a dedicated refusal
for handing off a never-claimed bot. Existing local-file integrity and
handoff-anchor-preservation invariants from batches 8-10 are unchanged; every
count-based test assertion that implicitly assumed no durable record existed
before the first handoff was reviewed and updated to match, not loosened.
Two new cases added. **76 focused (`operator_command_poller`, was 74) / 55
candidate-runner / 225 broader affected (was 223)**, exit 0, no skips, four
pre-existing FastAPI warnings, foreground. `git diff --stat`: exactly four
files (two modules, two test files). No full regression (single-module scope,
consistent with the batches 5-10 precedent). Protected (non-cooperative)
configuration custody and older/uncooperative controllers remain open; handoff
itself is still same-host cooperative rotation, not independent authorization.
No new C/J/E/A: R39 remains PARTIAL; **85/200 = 42.5% (~43%); 1/50 (2%)**,
unchanged. NOT_READY_TO_FUND; V10 unchanged/DEFERRED.

Independent batch-10 review — 2026-09-27: two checks failed on published
9cf6fae (2 / 4.81 s): the claimed operator-rotation continuation still gated
polling on bot ownership, and arbitrary worker removal could retain an invalid
scheduler index. The constructor does not validate durable ownership or migrate
worker state. The corrected acknowledgement requires the exact previous runner,
unchanged worker contracts, and an already completed same-cursor bot-owner
handoff. Scheduling changes remain supported; other component/cursor migrations
stay gated. Candidate and bot locks cover validation/sync through the CAS audit,
which links the prior candidate/owner records. Latest-review retries, repeated
rotations and interrupted-job recovery preserve history and pending commands.

**129 focused / 38.01 s; 433 integration / 177.31 s**, exit 0, no skips/warnings;
754 tracked input hashes unchanged through both final runs. Twenty-one added
cases and strengthened rotation checks include failures, both CAS conflicts,
lock cleanup and rotated-command cancellation requests with reservations retained.
No full rerun. Exact evidence: docs/V11_WORK_CHECKPOINT.md (independent batch-10
review), /tmp/v11-codex-b10-1qxnjcak/.

This closes the bounded scheduling/same-cursor rotation path, not arbitrary
configuration migration or independent authorization. Protected configuration,
cross-deployment ownership/recovery, real delivery/deployment, callbacks and
independent operating acceptance remain open. Next: offline protected
configuration and shared ownership/recovery tests; no credentials are needed.
R39 PARTIAL; **85/200 = 42.5% (~43%); 1/50 (2%)**, unchanged.
NOT_READY_TO_FUND; V10 unchanged/DEFERRED.

Original supervisor batch 10 — 2026-09-27 (claims corrected by the independent review above): the independent batch-9 review named three remaining offline R39 gaps
(protected operator configuration, cross-deployment consumer ownership/
recovery, reviewed candidate configuration continuation); this batch closed
the third, the one with an existing reproduced test demonstrating the gap.
Added `CandidateRunner.acknowledge_configuration_review(reason=...)`,
modeled on `handoff_bot_owner`'s reviewed/audited pattern: under the same
exclusive candidate lock, it validates a short single-line reason, refuses
when there is nothing to review or the configuration already matches, and
otherwise writes one CAS-guarded durable record carrying the exact prior
progress state forward under the new configuration hash, so a deliberately
reviewed component-configuration change (e.g. an operator bot-owner
rotation) no longer permanently blocks every future run of the same
candidate identity. No component invariant is re-derived or loosened;
`__init__` already re-validates every bound component against the new
configuration before this method is reachable. Five new cases (full
continuity path, four invalid-reason cases, no-prior-run, no-op, concurrent-
run refusal). **34 focused / 31.05 s** (was 29); broader affected **202
passed / 43.83 s**, exit 0, no skips, four pre-existing unrelated FastAPI
warnings; direct-dependency evidence-foundation/paper-runtime/paper-
coordinator **69 passed / 13.35 s**. Exactly two files touched
(`v11/candidate_runner.py`, its test file); no full regression, consistent
with the no-full-rerun precedent batches 5-9 set for comparable single-
module scope. Protected (non-cooperative) operator configuration custody and
cross-directory/cross-host/older-controller consumer exclusion remain open;
real delivery/deployment, callbacks and independent operating acceptance
remain open. No new C/J/E/A: R39 remains PARTIAL; **85/200 (~43%); 1/50
(2%)**, unchanged. NOT_READY_TO_FUND; V10 unchanged/DEFERRED. Full detail:
docs/V11_WORK_CHECKPOINT.md (reviewed candidate configuration continuation,
supervisor batch 10).

Independent batch-9 review — 2026-09-27: **nine reproductions failed / 0.94 s**
on published 5876bfd. The handoff admitted unrelated cursors/databases, could
empty an owned lock on failure, overwrote unknown bindings, and collided with
prior audits on repeated/retried rotations. The corrected API requires the exact
prior identity/policy on the same store/file, namespace, worker, bot and account.
One CAS-guarded durable audit atomically advances ownership and binds the original
cursor; the lock anchor is never rewritten. Committed retries and repeated
rotations retain correct history. **101 focused / 28.43 s; 432 integration /
164.14 s**, exit 0, no skips/warnings; 754 tracked input hashes unchanged during
both final runs. Actual process-exit recovery and candidate cancellation while
collection waits are covered. No full rerun. Evidence and exact next action:
docs/V11_WORK_CHECKPOINT.md (independent batch-9 review).

This strengthens local rotation only. Protected configuration, cross-deployment
consumer ownership/recovery and reviewed candidate configuration continuation
still require offline implementation/tests; they do not require real credentials.
Real delivery/deployment, callbacks and independent operating acceptance remain
open. R39 PARTIAL; **85/200 = 42.5% (~43%); 1/50 (2%)**, unchanged.
NOT_READY_TO_FUND; V10 unchanged/DEFERRED.

Original supervisor batch 9 — 2026-09-27 (claims corrected by the independent review above): recovery check at
batch start found `git status` clean, local HEAD `106b504d21897d78d71a927e732ca8fb1bc33983`
equal to `origin/weather-v11-profitability-upgrade-2026-09-23`, and no
unfinished process; the checkpoint/matrix/ledger already reflected that exact
HEAD, so no prior same-batch work needed recovery. The batch-8 review's own
recorded next action for R39 named "offline protected configuration and
ownership/handoff implementation... required without needing real
credentials" as the one remaining purely local gap; R31 (result-lag finality)
and R43/R44 (auth/isolated-deployment) all require real external source or
owner access and stay blocked, so this was the batch's target.

Added `TelegramOperatorCommandPoller.handoff_bot_owner` in
`v11/operator_command_poller.py`: under the same exclusive bot-scoped lock
`step()` already uses, it reads the existing binding, refuses a no-op
transfer (`OPERATOR_COMMANDS_HANDOFF_NOT_CHANGED`) and an invalid reason
(`OPERATOR_COMMANDS_HANDOFF_REASON_INVALID`: empty, over 200 characters, or
multi-line), records the exact prior binding, new binding and reason as a
durable `OPERATOR_EVENT` before rewriting the lock, then repeats the same
fsync-file/fsync-parent-directory durability protocol `_bind_bot_owner` uses
for a first claim. The target `worker_key` and `store` are never changed by a
handoff, so the durable Telegram offset survives the transfer exactly —
verified by a new test that hands off to a poller with a different bot
identity/policy and confirms the offset is unchanged and the next poll resumes
from it rather than from zero. `v11/operator_command_runtime.py`'s
`CandidateOperatorCommands.handoff_bot_owner` exposes the same operation for
the candidate-bound wiring. This replaces raw lock-file deletion/truncation
(which the module's docstring already called unsupported) with a reviewed,
audited transfer.

Seven new cases in `tests/test_v11_operator_command_poller.py`: the transfer
itself (offset preserved, old binding subsequently refused, new binding can
poll), a no-op refusal, four parametrized invalid-reason refusals, and a
refusal while a concurrent raw lock holds the bot lock. Targeted: **48 passed
/ 2.38 s** (was 41). Directly related (candidate runner, operator command/
safety, event-risk, evidence-foundation suites): **141 passed / 29.57 s**.
Broader affected selection (`-k "candidate_runner or operator_command or
operator_safety or event_risk or telegram"`, includes production
`Telegram.principal`/panel coverage): **168 passed, 33.23 s, exit 0**, no
skips/warnings, foreground. `git diff --stat` after the change showed exactly
three touched files (`v11/operator_command_poller.py`,
`v11/operator_command_runtime.py`, the poller test file), confirming no
unrelated or private material was touched. No full regression run: this is a
single-module addition plus its direct integration surface, consistent with
the testing budget for one coherent batch.

This closes the local "ownership/handoff implementation" gap only. It does
not establish cross-directory/cross-host/older-controller exclusion,
protected non-cooperative configuration custody, real bot-token delivery, or
independent operational acceptance — all still open, same as before this
batch. No new C/J/E/A milestone: **85/200 (~43%); 1/50 (2%)**, unchanged.
NOT_READY_TO_FUND; V10 unchanged/DEFERRED. Next: either (a) a production
entry point wiring a real credentialed `Telegram` client to
`CandidateOperatorCommands` for an actual deployed account (owner
credential/deployment decision required), or (b) R31/R43/R44, which all need
real external source/owner access rather than local implementation.

Independent batch-8 review — 2026-09-27: fixed two material defects in the
same-directory bot lock. Alternating consumers could acknowledge and lose safety
commands despite never overlapping; failed bot-lock opens leaked worker file
descriptors. **Six reproductions failed / 0.58 s** on the published code. The
bot lock now durably binds the store/file, namespace, worker and identity/policy
before polling, including idle polls, with no automatic reassignment. Immediate
descriptor cleanup, partial-write/sync rejection and unsafe-file checks preserve
bounded retry/recovery. **41 focused / 2.34 s; 381 integration / 104.57 s**,
exit 0, no skips/warnings; 796 tracked input hashes unchanged during each run.
No full rerun. Exact scope/evidence: `docs/V11_WORK_CHECKPOINT.md` (independent
batch-8 review). No new C/J/E/A: **85/200 = 42.5% (~43%); formal 1/50 (2%)**.
R39 remains PARTIAL. Local hashes/bindings do not establish independent protected
configuration custody or cross-directory/host/controller ownership. Those remain
open, along with real delivery/deployment, callbacks and independent acceptance.
Offline custody/ownership code and tests need no real credentials. NOT_READY_TO_FUND;
V10 unchanged/DEFERRED.

Original supervisor batch 8 — 2026-09-27, claims corrected by review above:
added a same-directory bot-scoped non-blocking lock alongside the worker lock.
This serialized simultaneous polls only; it did not close consumer ownership
between polls or protected configuration custody. Candidate configuration hashing
remains a valid local replay check. Original **24 focused / 1.46 s; 252 combined
/ 51.57 s**, exit 0, no skips/warnings, no full rerun. Three added cases, not four.
The independent review preserves that serialization and adds durable local owner
binding/cleanup. No original or review milestone credit was added.

Independent batch-7 review — 2026-09-27: fixed operator-command starvation in
the optional candidate integration. Degraded synchronization suppressed polling,
and blocked public collection prevented an authenticated cancel from being
applied. Two regression cases failed before the fix. One bounded, owned polling
coroutine now runs alongside ordinary jobs and is drained on shutdown, preserving
authentication, freshness, durable retry and cancellation/reservation semantics.
**26 focused / 22.33 s; 361 integration / 94.04 s**, exit 0, no skips/warnings;
754 tracked input hashes match before/after each run. No full rerun.
Exact scope/evidence: `docs/V11_WORK_CHECKPOINT.md` (independent batch-7 review).
The original wiring proves account/store consistency; protected configuration
custody and exclusive bot-consumer ownership remain unverified. No new C/J/E/A:
**85/200 = 42.5% (~43%); formal 1/50 (2%)**, unchanged. NOT_READY_TO_FUND;
V10 unchanged/DEFERRED. The original batch report below predates this correction.

Candidate-runner operator-command wiring — 2026-09-27 (supervisor batch 7):
closed R39's "runner wiring"/"protected policy/account binding" gap with
`v11/operator_command_runtime.py` (`CandidateOperatorCommands`), which refuses
to bind unless the caller's policy account matches the account passed to it,
and integrated it into `CandidateRunner` as one more finite job kind
(`OPERATOR_COMMANDS`), scoped against the runner's own protected
`coordinator.policy.account_id`. **18 focused / 14.26s; 159 directly related
/ 53.08s; 133 broader affected / 23.42s**, exit 0, no skips/warnings,
foreground. No full rerun (single new module plus direct integration
surface). Exact scope/evidence: `docs/V11_WORK_CHECKPOINT.md` (supervisor
batch 7). R39 remains PARTIAL; no new C/J/E/A: **85/200 = 42.5% (~43%);
formal 1/50 (2%)**, unchanged. Real credentialed delivery/deployment,
callback/button support and independent acceptance remain open.
NOT_READY_TO_FUND; V10 unchanged/DEFERRED.

Independent batch-6 review — 2026-09-27: fixed a material command-polling
availability defect. The original poller caught adapter errors but let router
rejections and reducer validation/replay conflicts abort the batch, blocking
later emergency commands. Seven reproductions failed on the published code.
The poller now continues only for recognized command rejections; storage,
integrity, CAS and cursor-write failures remain retryable without advancing the
cursor. Interrupted batches replay committed reductions idempotently.
**21 focused passes / 0.97 s; 238 integration passes / 39.90 s**, exit 0, no
skips/warnings, all 731 tracked code/configuration/dependency hashes unchanged
during testing. No full rerun. Exact scope/evidence: `docs/V11_WORK_CHECKPOINT.md`
(independent batch-6 review). R39 remains PARTIAL; no new C/J/E/A:
**85/200 = 42.5% (~43%); formal 1/50 (2%)**, unchanged. Protected binding,
runner/deployment and independent acceptance remain open. NOT_READY_TO_FUND;
V10 unchanged/DEFERRED.

Bounded Telegram-command polling loop — 2026-09-27 (supervisor batch 6): closed
the exact gap batch 5 identified by adding `v11/operator_command_poller.py`.
`TelegramOperatorCommandPoller` calls `telegram.updates(offset)` (any object
shaped like the real `production.telegram.Telegram`) and applies each update
through the unchanged `TelegramOperatorCommandAdapter.handle()`, durably
advancing its own per-worker-key offset as a CAS-guarded `RUNTIME_STATUS`
record — the same pattern the existing audit worker uses for its resumable
cursor. Restart resumes at the committed batch boundary; an interrupted batch
may replay already-committed reductions idempotently. A non-blocking
`flock` on a per-worker lock file refuses a second concurrent poll for the
same key. One authentication/grammar/authorization failure is reported without
stalling later updates in the same batch or wedging the offset. New suite
**7 passed**; combined with the adapter, router, event-risk, evidence-foundation
and operator-panel suites: **138 passed, 13.36s, exit 0**, no skips/warnings,
foreground. No full regression run (one new module plus its direct integration
surface); an initial `-k "v11 or operator or telegram"` selection matched most
of the suite and exceeded the foreground timeout twice, so both partial runs
were stopped rather than left running in the background.

This is the missing polling *loop*, usable unchanged by a real credentialed
Telegram client, but it is not itself a live deployment: no production script
yet constructs a real credentialed client plus this poller and drives `step()`
on an interval, that wiring/credential decision is left for a dedicated batch
with owner input, callback/button commands remain unsupported, and protected
policy/account-binding review and independent operational acceptance remain
open. No V10, credential, private-input or existing production code changed.
No new C/J/E/A milestone: **85/200 = 42.5% (~43%); formal 1/50 (2%)**, unchanged.
NOT_READY_TO_FUND; V10 unchanged/DEFERRED.

Authenticated Telegram-command adapter — 2026-09-27 (supervisor batch 5): closed
the exact gap the batch-4 review identified by adding
`v11/operator_command_adapter.py`. `TelegramOperatorCommandAdapter` reuses the
existing, already-tested `production.telegram.Telegram.principal` private-chat
identity check to authenticate the Telegram sender (bot id, chat id, chat type,
operator id, non-bot, no forward/sender-chat/via-bot markers, message-date
freshness), then parses a strict `/ACTION SCOPE scope_id reason` grammar and
derives the command id and timestamps from the message's own envelope, before
calling `OperatorSafetyRouter.route` with only the authenticated actor and
parsed values. Callback/button updates are explicitly out of scope
(`COMMAND_CALLBACK_NOT_SUPPORTED`). New suite **16 passed**; combined with the
router, event-risk, evidence-foundation and operator-panel suites (the last
exercises the reused `principal` in its own existing coverage): **131 passed,
11.98s, exit 0**, no skips/warnings, foreground, on the recorded project
interpreter. No full regression run (single new module plus its direct
integration surface).

Text-command routing is now sender-authenticated end to end at the core/
integration level, but no production entry point yet constructs this adapter
against a real credentialed `Telegram` client and polls real Telegram updates
with it; that live-polling wiring and its own credential/deployment evidence,
protected policy/account-binding review, callback/button command support, and
independent executor/guardian integration and operational acceptance all remain
open. No V10, credential, private-input or existing production code changed.
No new C/J/E/A milestone: **85/200 = 42.5% (~43%); formal 1/50 (2%)**, unchanged.
NOT_READY_TO_FUND; V10 unchanged/DEFERRED.

Independent batch-4 review — 2026-09-27: corrected the published claim that
`OperatorSafetyRouter` closes protected/authenticated command routing. It checks
a caller-supplied numeric actor and policy, and has no transport or candidate
caller. This is reusable authorization core with synthetic reducer integration;
authenticated transport and protected policy/account binding remain required.
Only documentation/docstrings changed; executable behavior and tests are preserved.
Independent affected integration: **166 passed / 30.91 s, exit 0**, no skips or
warnings; 710 tracked Python/config input hashes and clean source stayed unchanged.
Exact retained evidence and scope: `docs/V11_WORK_CHECKPOINT.md` (batch-4 review).
No full rerun or new C/J/E/A: **85/200 = 42.5% (~43%); formal 1/50 (2%)**.
NOT_READY_TO_FUND; V10 unchanged/DEFERRED.

Operator authorization helper — 2026-09-27 (supervisor batch 4, claim corrected):
recovered two already-written untracked files from a stopped invocation, with
local/remote otherwise equal. `v11/operator_safety_router.py` checks an operator
allowlist, scope/action ceilings, freshness and ACCOUNT target equality before
calling the existing durable monotonic reduction path. It grants no financial
authority and does not itself authenticate the supplied actor. Protected routing
is still open; real delivery/credentials and independent executor/guardian and
operational acceptance are also unclaimed.

Original new suite **11 passed**; with directly affected event-risk/source-time/
evidence coverage, **80 passed**, exit 0, no skips/warnings. No full regression
ran. No new formal credit: **85/200 (~43%); 1/50 (2%)**, unchanged. Next: implement
and test the upstream authenticated adapter, protected account/policy binding and
original command/retry handling with offline fixtures. The earlier 47-case cohort
already has recorded passing coverage; missing historical attribution stays UNKNOWN.
Retain exact source/runtime/case evidence at the next required batch regression.

Complete-collection regression; independent evidence correction — 2026-09-26
(supervisor batch 3): reviewed documentation-only publication
`ead5354dea1bdb4c727f14a0a2dc0fed1be76778` against
`5b16f4f535025b12994733c742563538d8fcb317`. The retained logs support **4753
passed, 11 skipped, 0 failed** across four pytest sessions. Independent
collection checks verified all **4764 distinct default-collected IDs exactly
once** in the saved selections. Cross-chunk session/order effects and missing
at-run provenance are not established by those totals.

The 141-failure chunk contains actual storage-capacity gate errors and its
retry passed after reported disk recovery. Corrected the unsupported claim
that this also explains the earlier 47 failures: the prior umask/custody and
broker-test diagnoses remain distinct; unmatched historical records stay
UNKNOWN. Earlier clean branch regressions remain valid historical evidence.
Also corrected the claim that hashes preserve the deleted batch-2 raw bundle;
its absence limits reinspection. Retain evidence separately from fixture scratch.

Independent storage/operator/submission/broker/guardian integration passed
**166, with 11 custody skips / 51.89 s, exit 0**; all 775 selected code/config
input hashes stayed unchanged. No full rerun, production/test/gate change,
new implementation or new acceptance is claimed. Exact evidence and limits:
`docs/V11_FULL_REGRESSION_DISK_CAPACITY_EVIDENCE.md`.
R45 remains PARTIAL, with no new C/J/E/A: **85/200 = 42.5% (~43%); formal
1/50 (2%)**, unchanged. NOT_READY_TO_FUND; V10 unchanged/DEFERRED. Next:
resume required PARTIAL integrations and preserve source/runtime/selection
provenance in the next required coherent-batch regression.

Regression recovery and guardian-stop test synchronization — 2026-09-26
(supervisor batch 2): clean local/remote source
`640a5d5421e625059a98aab45294756cc41829cf`, tree
`6a1554517fa4fa55fa0a825b74862fc1c2a50972`, passed initial targeted **2 / 3.68 s**
and affected integration **279 / 78.14 s**. The single foreground full regression
completed through Remote Desktop Commander: **4752 passed, 1 failed, 11 skipped,
four existing warnings / 1245.36 s**, exit 1; all 788 input hashes unchanged.
All 47 retained earlier failure IDs passed within the full run.

The sole additional failure was the guardian-stop test checking admission before
confirming asynchronous SIGSTOP delivery. A synthetic 100-cycle kernel-state
probe observed R immediately after signal return in 14 cycles, T in 86; the
unchanged isolated test passed 5/5 times. The test now awaits an actual stopped
child notification, asserts SIGSTOP and then checks admission once. Production
code and safety gates are unchanged. Final stop/kill pair passed in five
invocations (10 passes); eight affected guardian/health modules passed
**338 / 42.88 s**, exit 0. Final hashes stayed unchanged; only that test differs
from the full-run source. No second full run or post-fix full-green claim.

Eleven actual custody cases remain skipped for missing `newuidmap`. No helper,
host policy, service, V10, deployment or financial action was performed; no owner
action was needed for this bounded batch. Exact evidence:
`docs/V11_REGRESSION_RECOVERY_EVIDENCE.md`. R45 remains PARTIAL, with no new
C/J/E/A: **85/200 = 42.5% (approximately 43%); formal 1/50 (2%)**, unchanged.
**NOT_READY_TO_FUND**. Next: one full regression on the published synchronization
fix in the next batch; unavailable custody and independent acceptance stay open.

R07 clean-audit checkpoint — 2026-09-26 (supervised batch 3): following the
prior checkpoint's own recommended next action, performed a full-file,
line-by-line audit of `v11/certification.py`'s station-registry/capability-
certification path — `StationMetadata`/`CapabilityScope` validation and
fingerprinting, root-custodied `protected_reviews` read path, and
`StationRegistry.observe`/`demote`/`proof`/`assess` — plus the one real
caller (`StrategyAdmission._assess`). Found no exploitable provenance,
quarantine, CAS, capability-proof-forgery, or replay defect; `CapabilityScope
.station` is only loosely validated by `certification.py` itself, but its
sole caller pins it against the already-validated rule-preimage station
identity before use, so the looseness is not locally exploitable. No code
changed; no C/J/E/A claimed. Total unchanged: **85/200 = 42.5%, approximately
43%; formal 1/50 (2%)**. Full detail: `docs/V11_WORK_CHECKPOINT.md`.

R08/R32 clean-audit checkpoint — 2026-09-26 (supervised batch 2): following the
prior checkpoint's own recommended next action, performed two independent
full-file, line-by-line audits of untouched PARTIAL packages rather than
deepening already-credited slices. R08 (`v11/rules.py` and its `evidence.py`
CAS/canonical dependencies, `weather_only_contract_strict.py`/
`weather_only_rules.py`, the `certification.py` recertify gate, every caller and
`tests/test_v11_certification_rules.py`) found no exploitable fingerprint-hash,
quarantine-transition, TOCTOU, fail-open, or caller-misuse defect; a genuine
mutated-rule test already exercises real drift detection, not just identity
cases. R32 (`v11/position_management.py`) found the same self-checking pattern:
`revalidate_exit` independently re-derives and `canonical()`-compares the entire
prediction/inventory/valuation chain before trusting any proposal, so a stale or
tampered valuation fails closed. No code changed; no C/J/E/A claimed for either
audit. Total unchanged: **85/200 = 42.5%, approximately 43%; formal 1/50 (2%)**.
Full detail: `docs/V11_WORK_CHECKPOINT.md`.

Checkpoint correction — 2026-09-26 (supervised batch 1): the prior checkpoint's
"Exact next unfinished action" (connect `samples_from_capture`/`archive_neighborhood`
to the census path, then forecast-run provenance/labels) was verified against
`polymarket_scanner/v11/pws_quality.py`, `census_worker.py`, `pws_runtime.py`,
`pws_lead.py` and git history and found already implemented since commit
`cbe5796` (2026-09-24) — it had been copy-pasted forward unverified through three
later checkpoint entries. No code changed; this is a documentation-accuracy fix,
not new work. No remaining PARTIAL requirement was found with a concrete,
purely-local, non-owner, non-production implementation gap this batch; the true
remainder for every PARTIAL package is real external source/label/provider access,
independent review, or owner-authorized host/deployment access. Total unchanged:
**85/200 = 42.5%, approximately 43%; formal 1/50 (2%)**. Full detail:
`docs/V11_WORK_CHECKPOINT.md`.

Authenticated PAPER candidate liveness — 2026-09-26: recovered unpublished local
work (`v11/liveness_protocol.py`, `v11/candidate_liveness.py`,
`v11/liveness_broker.py`, plus binding changes in `v11/evidence.py` and
`v11/runtime_health.py`) reviewed, preserved and verified. Adds a bounded
authenticated candidate-liveness producer endpoint: a separate AF_UNIX
SOCK_SEQPACKET listener, packet-level SCM_CREDENTIALS plus connected-peer
authentication, exact pinned worker/health-configuration identity, single
preemptible publication child confined to a private process group, and durable
accepted-before-effect journal entries bound into the existing atomic
heartbeat/sample publication path. The guardian's cancel-only stream protocol
and its own connection budget are unchanged. New-module suite: **202 passed, 7
skipped**, exit 0. Affected guardian/health/evidence integration: **782 passed,
11 skipped**, exit 0, plus one pre-existing failure
(`test_actual_broker_death_stale_socket_restart_and_receipt_replay`) verified to
reproduce identically on the unmodified published 3e80339818ddc5b67b4485c28b9fda54c4f391e8
tree, so it is unrelated to this work. Evidence:
`docs/V11_CANDIDATE_LIVENESS_EVIDENCE.md`. Existing R37/R38 C/J strengthened
only; no additional E/A or full acceptance: **85/200 = 42.5%, approximately
43%; 1/50 (2%)**, unchanged. Full candidate source/execution custody and
independent commissioning remain open. NOT_READY_TO_FUND; V10 unchanged/DEFERRED.


Coherent PAPER health continuation, 2026-09-25: atomic heartbeat/sample publication,
consistent snapshot reads, health-head fences and READY freshness revalidation
close the local healthy-publication race while retaining durable cancellation.
Malformed data/config, extended deadlines and true liveness failures still cancel
without releasing reservations. Final targeted **184 passed / 30.75 s**, exit 0,
no skips, all 781 canonical inputs unchanged. Full shared-writer regression:
**4549 passed / four existing FastAPI warnings / 623.00 s / exit 0**, no skips,
same canonical source/mirror inputs unchanged. Evidence:
`docs/V11_HEALTH_PUBLICATION_EVIDENCE.md`. Existing R37/R38 C/J only; no additional
E/A or full acceptance: **85/200 = 42.5%, approximately 43%; 1/50 (2%)**, unchanged.
Protected producer transport and operational evidence remain open. NOT_READY_TO_FUND;
V10 unchanged/DEFERRED.

Actual local custody/restart, 2026-09-25: **20 passed / 9.84 s**, no skips, exit 0,
778 canonical inputs unchanged. Four real mapped-principal PAPER scenarios verify
denied state/endpoint/signal access, peer and order-verb refusal, authorized cancel
delivery and three abrupt broker crash/restart boundaries with client replacement.
Evidence: `docs/V11_GUARDIAN_CUSTODY_EVIDENCE.md`. This closes the local gate blocked
by uidmap and strengthens existing R37 C/J. E still requires the complete required
operational/deployment evidence; A still requires full acceptance. No new unit:
**85/200 = 42.5%, approximately 43%; 1/50 (2%)**. NOT_READY_TO_FUND; V10 DEFERRED.

PAPER Unix-socket guardian broker continuation, 2026-09-25: bounded typed local
protocol, mutual kernel peer checks, broker-owned cancellation journal, immutable
retry receipts, both-process lease fences and finite independent client driving.
Affected integration **512 passed / 75.25 s** precedes the final overflow-identity
guard. Final focused **305 passed, 1 skipped / 26.83 s**, exit 0, all 778 canonical
inputs unchanged. Evidence: `docs/V11_GUARDIAN_BROKER_EVIDENCE.md`. The actual
distinct-user custody harness is prepared but remains unverified pending the local
owner `uidmap` prerequisite. This extends existing R37 C/J, without new E/A,
authentication/deployment or full-package acceptance. **85/200 = 42.5%, approximately
43%; completed requirements 1/50 (2%)**, unchanged. NOT_READY_TO_FUND; V10 DEFERRED.

Independent local PAPER guardian, 2026-09-25: **46 focused passed / 13.53 s**,
exit 0, with 771 canonical inputs unchanged. Full integrated regression **4294
passed / four existing FastAPI warnings / 600.37 s**, exit 0; all source/mirror
inputs unchanged. Evidence: `docs/V11_GUARDIAN_ISOLATION_EVIDENCE.md`.
R37 newly earns **C** for a bounded independently scheduled Linux process and actual
local process-failure/resource/restart checks, and **J** for durable cancellation,
account/basket/maker lease and required candidate configuration integration.
These are newly implemented substeps, not additional credit for the preceding
cooperative cancellation tests. Retain the recovered baseline below unchanged.
**83 + 2 = 85/200 = 42.5%, approximately 43%** using the existing half-up rule.
Formal completion remains **1/50 (2%)**. R37 E/A and all unearned R43/R44 milestones
remain open: same-UID trusted process tests and shared SQLite do not establish
protected custody, supported cancel authentication, network isolation, deployment
or independent commissioning. NOT_READY_TO_FUND; V10 unchanged/DEFERRED.

Scheduled PWS score-audit continuation, 2026-09-25: the existing pinned worker
now optionally reports complete retained receipt-score cohorts, including UNKNOWN,
legacy and unsupported protocols. Measured labels and score matches remain
separate; shared resource/selection failures clear every positive prefix.
Integrated **156 / 58.68 s** precedes the final read-budget propagation guard;
final guarded **35 / 13.16 s**, exit 0, includes combined temperature/account/PWS
reporting and finite candidate/recovery checks. All 767 final canonical inputs
unchanged. Evidence: `docs/V11_PWS_SCORE_AUDIT_EVIDENCE.md`. **83/200 (~42%);
formal 1/50 (2%)**, unchanged. NOT_READY_TO_FUND; V10 unchanged/DEFERRED.

Pinned PWS receipt-score continuation, 2026-09-25: a durable original scan start
now survives interrupted/completed scoring, and shared selection/numerics replay
is required by paired observation-label datasets. Later receipts cannot change
old scores; UNKNOWN and missing legacy boundaries remain explicit. **149 focused
and candidate integration passes / 37.30 s**, plus **233 affected learning,
admission and audit passes / 62.06 s**, exit 0; all canonical inputs unchanged.
Full 4195 / 578.00 s belongs to preceding published **0776697**, before this
PWS change. No new milestone credit: **83/200 (approximately 42%); formal 1/50
(2%)**. NOT_READY_TO_FUND; V10 unchanged/DEFERRED. Evidence:
`docs/V11_PWS_SCORE_REPLAY_EVIDENCE.md`.

Local temperature-account replay continuation, 2026-09-25: all five original
temperature valuations now join exact prepared account proposals within the same
read snapshot/deadline. Every PWS/release comparison remains material, and early
strategy/preparation/allocation rejections retain their distinct populations.
Existing replay extraction: **83 passed / 194.17 s**; final new integration:
**29 passed / 54.16 s**, exit 0. The latter includes candidate scheduling,
report recovery, unavailable originals, mismatches and budget exhaustion.
Positive branches use an explicit test-only payout oracle; unmodified production
vacuous bounds still reject. Full canonical-input local regression: **4195 passed,
four existing FastAPI warnings / 578.00 s / exit 0**; all 763 non-document
source/mirror inputs unchanged, session 9247. No new C/J/E/A or formal completion:
**83/200 = 41.5%, approximately 42%; 1/50 (2%)**. V10 unchanged/DEFERRED;
NOT_READY_TO_FUND. Evidence: docs/V11_TEMPERATURE_ACCOUNT_REPLAY_EVIDENCE.md.

Latest 2026-09-25 continuation: original prepared basket/exit numerical valuation
comparisons now join account replay and existing audits. Shared runtime extraction
passed **43 / 5.00 s**; portfolio/account checks **51 / 5.80 s**, exit 0. Candidate
scheduling and failure integration now pass **102 / 16.66 s**, exit 0; derived
new-risk gates remain intact. Saved **0a655cb3** passed affected **411 / 63.04 s**
and full **4163 / four existing warnings / 306.16 s**, exit 0, all **861 tracked
inputs unchanged**. Subsequent aggregate audit byte/deadline enforcement passed
final focused **99 / 20.57 s**, exit 0. The broad run explicitly predates that
nine-line reporting guard; it adds no runtime/financial authority. Exact evidence is in
docs/V11_PORTFOLIO_REPLAY_EVIDENCE.md. This strengthens existing C/J credit,
including R04; it does not close real-evidence, independent acceptance or isolated
deployment. **83/200 = 41.5%, rounded approximately 42%; formal 1/50 (2%)**, unchanged.
All 200 milestones still cover the full scope through unfunded READY_TO_FUND.
V10 unchanged/DEFERRED. No financial authority or readiness granted.

This estimate covers the full final-reviewed engineering scope, including real
source evidence, integration, verified deployment and unfunded readiness. It is
separate from the requirements matrix: formal completion is still **1/50 (2%)**.
It is neither elapsed effort nor a prediction of time, profit or trading authority.

The fixed denominator is **200 evidence milestones**: four for each of R00–R49.
Each milestone receives one unit only for the named completed substep below:

- C: a bounded core implementation and its recorded local checks (for R00,
  verified input/source identities; for R01, the actual forensic analysis).
- J: a demonstrated upstream/downstream integration of that core. This credits
  the stated integration slice; remaining adapters and scope expansion remain
  reserved in the unearned milestones and the full matrix.
- E: all required real-input, forward, operational or unfunded evidence for that
  package, including required deployment/isolation evidence. Synthetic tests
  alone cannot earn this milestone.
- A: the entire package passes its original acceptance requirements.

C and J are deliberately limited substeps, not claims that all implementation
or integration in a partial package is finished. R43–R49 include authentication,
isolated deployment, independent acceptance, comparison, learning and unfunded
commissioning. Their unearned units remain in the denominator. Funded canary
activity and activation still require separate approval.

The recovery baseline is commit `7bd4b3b51b5abe28efa3eecff051553b2adaa0d7`.
The checkpoint/matrix give code, test and evidence references for these credits:

| Requirements | Earned | Specific recovered substeps |
|---|---|---|
| R00 | C | Input hashes and frozen source/deployed identities; control health still open |
| R01 | C J E A | Consistent actual snapshot, immutable analysis, forensic/funnel report |
| R02 R03 | C J | Receipt archive and decision/funnel records used by the candidate |
| R04 | C | Pinned causal replay checks; complete engine replay remains open |
| R05 R06 | C J | Maker counterfactual scheduling; partial-success public collection/discovery |
| R07 R08 | C J | Scoped admission and rule quarantine joined to paper safety |
| R09 | C J | Actual AWC adapter wired through mocked public census; exact source/forecast gaps remain |
| R10 | C | Raw MADIS parsing, defensive QC and metadata quarantine; runtime join pending |
| R11 R12 R13 R14 R15 | C | Coherent distributions/bounds, calibration fallback, physical features, causal datasets and bounded learner tests |
| R16 R17 | C J | Protected bundle slots, admission pins and reviewed epoch/rollback integration |
| R18 R19 R20 R21 R22 | C J | Derived event risk, valuation, common account, atomic reservations and scenario joins |
| R23 | C | Versioned correlation ceilings; actual reviewed mappings remain open |
| R24 R25 R26 R27 R28 R29 R30 | C J | Allocation and scoped strategy factories joined to the finite candidate |
| R31 | — | Exact finality source/version evidence absent |
| R32 R33 R34 R35 R36 | C J | Inventory exits, bounded runtime, maker quote/context/telemetry and separate reward reporting |
| R37 | — | Cooperative paper cancellation is insufficient for the required independent guardian |
| R38 R39 R40 R41 | C J | Clock/source leases, safety reductions, performance attribution and scheduled audit worker |
| R42 | C | Durable model demotion overlay; wider drift/lifecycle propagation remains open |
| R43 R44 | — | Auth/entitlement and isolated deployment not verified; maintenance preparation earns no deployment credit |
| R45 | C J | Recorded targeted checks and integrated full off-host regression; independent security/acceptance remain open |
| R46 R47 R48 R49 | — | Forward comparison, learning acceptance, unfunded commissioning and release gates remain open |

Baseline arithmetic: C=42, J=32, E=1, A=1; **76/200**, approximately **38%**.
Round the fraction to the nearest whole percentage point (half rounds upward).
Only newly completed named milestones change the numerator. More tests for an
already credited slice, time spent, maintenance preparation and a session ending
do not change it. A correction must retain the prior score and explain the defect.

## Changes after recovery

PWS runtime milestone: R10 earns J for the demonstrated raw MADIS collection →
bounded defensive QC → health/admission/event routing join, plus required fresh
census recovery. Evidence: `test_v11_pws_runtime.py`, `test_v11_pws_census.py` and
the candidate PWS integration cases; 255 related passes plus four focused checks
after the final policy-consistency change. Real lead/calibration/certification,
full provider acceptance and deployment remain open. New total **77/200**,
approximately **39%**. Formal completion remains **1/50 (2%)**.

Forecast normalization/candidate integration subsequently passed at implementation
`84068f641840574a6fd82f73e53a2a0ea14e944e`; full regression 3442 passed in 229.86 s.
The score remains **77/200, approximately 39%**. R09 already has its named AWC
integration credit; its actual forecast run/access/coverage evidence is still
missing. A research normalization that correctly retains that gate does not
earn E or A. More tests do not increase the estimate. No formal package was
newly accepted.

Run-bound GEFS integration: R11 earns J for the bounded GRIB source → complete
31-member local-day forecast path → archived model input → immutable probability
bundle/inference join. Candidate collection and constituent health/admission
checks are demonstrated by synthetic integration tests (242 related passes in
46.97 s). The named CDF core was already credited; the new credit is its source
join. Actual NOAA access/packing parity, calibrated temporal approximation,
other models and operational acceptance remain unearned. Total **78/200**, still
approximately **39%** after whole-percentage rounding. Formal **1/50 (2%)** is
unchanged. This does not credit E/A, extra tests or elapsed effort.

Forecast learning capture: R14 earns J for the protected whole-event forecast
vector -> exact model feature/decision archive -> explicit exact-label join ->
existing causal dataset integration. Evidence: 17 new learning-capture tests,
114 related passes in 22.55 s. The retained event vector is independent of entry
economics; global universe coverage and independent label truth remain unverified.
Other learning targets, actual calibration, isolated training and learning
acceptance remain open. Total **79/200**, approximately **40%** by the same
whole-percentage rounding. Formal **1/50 (2%)** remains unchanged.

Bounded GEFS rollover subsequently passed 112 related checks (18 new cases).
R09/R11 already hold their source integration credits, so this expansion leaves
**79/200, approximately 40%**, and formal **1/50 (2%)** unchanged. Actual source
availability/packing, calibration, independent and deployment gates stay open.

Fresh multi-step GEFS census, bounded source views and completed-path adoption
passed 241 related checks (21 new cases). R09/R11/R33 already have their named
integration credits. Total remains **79/200, approximately 40%**; formal **1/50
(2%)**. Source access, other providers, real calibration and operational/independent
acceptance remain unearned. No numerator increase follows from more tests.

Final census full regression at `f1752a8157c85ce1e975f64cd80b11e5a6318780`
passed 3566 tests with four existing warnings in 283.92 s, all 806 tracked inputs
unchanged. This verifies the newly connected local code; **79/200, approximately
40%**, and formal **1/50 (2%)** remain unchanged. No operational E/A is credited.

Declared forecast contract and research integration: R15 earns J for the
31-member whole-event capture -> explicit complete-label cohort -> frozen causal
dataset -> bounded learner -> compatible immutable challenger -> numerical
inference parity path. Evidence: 29 new cases, 145 related passes in 29.35 s.
Replay does not refit completed/interrupted attempts; the source evidence and
parent remain unchanged. These are synthetic tests, including labels. Actual
labels, calibration, OS isolation, initial champion and learning acceptance are
still open. Total **80/200, approximately 40%**; the displayed estimate and formal
**1/50 (2%)** are unchanged. This credits the named integration, not more tests.

Read-only learning snapshots and complete normalized-source derivations
subsequently passed 168 related checks, including 15 new cases and a 621-record
synthetic GEFS graph. R14/R15 already have their named integrations. Total stays
**80/200, approximately 40%** and formal **1/50 (2%)**. No actual-label, calibrated,
independent, host/deployment or operational acceptance credit is earned.

The combined forecast-contract/source-provenance full regression passed **3610
tests**, four existing warnings, in 288.26 seconds at implementation `6347e704`.
All 811 tracked inputs remained unchanged. This confirms local integration, not
new E/A evidence: **80/200, approximately 40%**, formal **1/50 (2%)**, unchanged.

The separate finite learner worker subsequently passed 125 related tests with
17 new cases: exact cohort triggers, interval/daily budgets, nonblocking locking,
durable attempt reservation, request/dataset-bound recovery and no duplicate
fits. It uses the already credited R15 integration and earns no new milestone.
Total remains **80/200, approximately 40%**, formal **1/50 (2%)**. Actual label,
calibration, process isolation, initial champion and learning acceptance remain
open. The current six active-work ranges are in the checkpoint; they are not
derived from this percentage and exclude external/owner waiting.

The archived exact-interval/GEFS remaining-path join now reaches protected same-day
inference and conservative economics, with 24 new cases verified. A shared-source
admission guard defect was corrected: identical guards merge and differing reads
gate. R11/R26 already have their integration credits. Total remains **80/200,
approximately 40%**, formal **1/50 (2%)**; actual exact-population coverage,
calibration and operational/independent acceptance remain unearned. The first
remaining milestone now excludes this bounded derivation implementation, but its
45–90 active-hour range remains appropriate to the larger unresolved source scope.

Physical/PWS inference: R13 earns J for raw MADIS/AWC -> QC/physical feature archive
-> immutable parameter/feature contract -> paired observation model -> separately
protected payout/same-day economics. The final new suite passed 26 tests in 2.06 s;
source absence, stale/revised inputs, actual dependency ablation, target separation
and immutable coefficients are demonstrated with synthetic sources/review fixtures.
Production scheduling, feature fitting, actual OOS/calibration and deployment
remain unearned. Total **81/200, approximately 41%** under the unchanged rounding
rule; formal **1/50 (2%)**. This is one named integration credit, not credit for
additional tests or an assertion that all R13 implementation is complete.

Recovered full regression at `47c3b999` passed **3677 tests**, four existing warnings,
in 201.69 seconds. All 817 tracked inputs remained unchanged and match on recovery.
This completed run was recovered rather than repeated. **81/200, approximately
41%**, formal **1/50 (2%)**, unchanged; actual-source, operational and independent
acceptance milestones remain unearned. The stale 40% summary in the matrix header
was corrected to agree with the already recorded R13 credit; no new unit was added.

Bounded current-input preparation now connects archived remaining paths and
physical/PWS paired inputs to the typed finite candidate, with preserved clock,
source, event and conservative economic gates. Final affected verification:
**245 passed / 56.98 s**, including 24 new cases. R09/R11/R13/R26/R33 already have
the applicable integration milestones. **81/200, approximately 41%**, formal
**1/50 (2%)**, unchanged. Source truth, calibrated target models, independent
review, isolated deployment and operational acceptance remain unearned. The six
remaining active-work ranges are retained with this completed preparation join
removed from the source implementation tasks; they exclude external waiting.

Recovered preparation full regression at published tree
`6cd23e57f8ef1765fc8f3767549438f87a330c52`: **3701 passed**, four existing
warnings, 221.56 seconds; all 819 inputs reverified unchanged. No duplicate run
was made. **81/200, approximately 41%**, formal **1/50 (2%)**, unchanged. This
verification adds no actual-source, independent or operational acceptance credit.

Conditioned payout and paired receipt-window observation capture now reach the
finite candidate and read-only exact-label dataset path, preserving conditioning,
original source derivations and separate learning feature records. Related checks
passed **154 / 26.41 s**, followed by **41 / 4.82 s** after the final provenance
checks; 26 new cases. R14/R15/R27 already have their named integration credits.
**81/200, approximately 41%**, formal **1/50 (2%)**, unchanged. Exact real labels,
calibration, target-specific fitting and operational/independent acceptance stay
open. The unchanged six active-work ranges exclude external/owner waiting.

Full target-capture integration at `61a5cc84` passed **3727 tests**, four existing
warnings, **260.57 s**, all 821 tracked inputs unchanged. The code's added joins
are verified locally; no new real or independent acceptance is earned.
**81/200, approximately 41%**, formal **1/50 (2%)**, unchanged.

Explicit same-day-conditioned Gaussian fitting now joins captured exact revisions
and remaining-day coverage to the existing offline job and finite research worker.
Original unconditioned policy hashes/semantics and all promotion/safety boundaries
remain unchanged. **87 related checks passed / 15.31 s**, including 18 new cases;
C/F and high/low candidate inference matches fitting numerically. R14/R15 already
hold these integration credits. **81/200, approximately 41%**, formal **1/50 (2%)**,
unchanged. Actual labels/calibration, physical/observation learning, process/host
isolation and independent acceptance stay open. The six active-work ranges remain
appropriate to that larger scope and exclude owner/external waiting.

Protected lifecycle withdrawal: R42 earns J for existing protected model/station/
strategy failure -> original admission invalidation -> finite PAPER cancellation ->
exact common-account reconciliation and maker retirement -> durable audit join.
Evidence: **24 new cases, 267 related passes / 35.43 s**; both PWS model scopes,
interruption, late fills, preservation of reducing exits, reviewed recovery without
resurrection and clock/identity guards are demonstrated. Fixed total **82/200,
approximately 41%**. Formal completion remains **1/50 (2%)**. Statistical drift
threshold/evidence acceptance, real calibration/lead quality, OS-independent guardian,
protected host and unfunded operational acceptance stay unearned. R37 gets no C/J
credit from the cooperative runtime. No numerator change is attributed to more tests.

Full lifecycle regression at `7dd8a462` passed **3769 tests**, four existing warnings,
**231.74 s**, with all 823 inputs unchanged. This verifies the R42 integration just
credited and the earlier conditioned-learning extension; it adds no E/A milestone.
**82/200, approximately 41%**, formal **1/50 (2%)**, unchanged. The next implementation
is scoped, predeclared rolling degradation measurement from exact captured/labelled
vectors; actual source/calibration, independent, host and unfunded gates stay open.

Scoped drift measurement now joins original admissions, model-bound complete forecast/
conditioned vectors and current exact labels in a read-only bounded snapshot. Policies
are explicit, cohorts grouped by event/city-day and unsupported metrics/attestations
remain visible. **121 related checks / 22.01 s, 27 new cases**. Automatic reviewed
reduction/candidate scheduling remains next. R42 already holds C/J; **82/200,
approximately 41%**, formal **1/50 (2%)**, unchanged. No actual or independent evidence
is inferred. Six remaining active-work ranges still apply; waiting is excluded.

Reviewed drift now connects the finite candidate, original model/capture scope,
predeclared protected policy, durable safety demotion, existing paper withdrawal/
terminal reconciliation and audit outcomes. **156 related passes / 29.77 s**, then
**two final boundary checks / 0.60 s**, 35 new worker cases. Full exact-tree regression
is next. R42 C/J already credited: **82/200, approximately 41%**, formal **1/50 (2%)**,
unchanged. Remaining metrics, meaningful actual evidence, independent review and host/
unfunded acceptance remain unearned. The six active-work ranges exclude external waits.

Full scoped drift/candidate/lifecycle regression at **4bbb8bee** passed **3831 tests**,
four existing warnings, **245.54 s**, with all **827 inputs unchanged**. The complete
manifest/output and recorded targeted results are saved in
`docs/V11_DRIFT_REGRESSION_EVIDENCE.md`. This confirms the extended R42 C/J slice;
no extra credit is earned from tests or work sessions. **82/200, approximately 41%**,
formal **1/50 (2%)**, unchanged. Remaining metric families and actual/independent/
host/unfunded acceptance remain open. Six active-work ranges remain appropriate to
that larger scope and exclude external/owner waiting.

Explicit grouped scalar calibration error now reaches predeclared reviewed drift,
scoped reduction, PAPER withdrawal/reconciliation and audits. Original default scorer
and DriftPolicy digests remain unchanged; no automatic calibration or restoration is
inferred. **117 related passes / 8.85 s, 14 new cases**. Existing integration credits
are not counted again: **82/200, approximately 41%**, formal **1/50 (2%)**, unchanged.
Actual calibration/independent/host/unfunded gates and six remaining hour ranges remain.

Automatic realized-PAPER monitoring now joins new ledger realizations, original entry
scope/model/fill proofs, conserved partial-exit accounting, protected predeclared loss/
drawdown reviews, account CAS/recovery, finite candidate safety and audits. **202 related
passes / 35.32 s, 30 new cases**; no new scoring milestone closes because R40/R42 C/J
already apply. **82/200, approximately 41%**, formal **1/50 (2%)**, unchanged. Realized
loss does not establish mark-to-market risk, live execution, net-EV capture or actual
calibration. Independent, host and unfunded readiness remain open; six hour ranges and
separation of external/owner waiting remain appropriate to the unresolved scope.

Locked full calibration/P&L integration regression at **95b00abc** passed **3875
tests**, four existing warnings, **252.03 s**, all **830 inputs unchanged**.
The manifest/output and targeted evidence are saved in
`docs/V11_QUALITY_REGRESSION_EVIDENCE.md`. No new C/J/E/A milestone closes:
**82/200, approximately 41%**, formal **1/50 (2%)**, unchanged. Actual/independent/
host/unfunded acceptance and the six active-hour ranges remain open; waiting is
excluded. The interrupted save was recovered without duplicate publication.

Horizon-specific maker counterfactual quality now joins original admission/model/
source/depth provenance, complete retained-window selection, fair automatic scope
scheduling, protected reduction, finite candidate retirement and bounded audits.
**276 related passes / 49.78 s**, then final **47 new cases / 7.98 s**. R05/R35/R42
already hold applicable integration credits; **82/200, approximately 41%**, formal
**1/50 (2%)**, unchanged. No actual fill, EV capture, calibration, independent,
host or unfunded acceptance is inferred. A single full regression is next. Six
remaining active-hour ranges remain appropriate to the larger unresolved scope,
with fill-based markout joins still open and external/owner waiting excluded.

Locked full maker-markout integration regression at **444c71fd** passed **3922
tests**, four existing warnings, **264.11 s**, all **833 inputs unchanged**.
Manifest/output and targeted results are in `docs/V11_MARKOUT_REGRESSION_EVIDENCE.md`;
the required `docs/V11_MARKOUT.md` records implemented behavior and remaining fill-
evidence semantics. Verification/documentation add no extra credit: **82/200,
approximately 41%**, formal **1/50 (2%)**, unchanged. Actual/independent/host/unfunded
acceptance and six active-work ranges remain open; external/owner waiting is separate.


Reconciled synthetic PAPER fill quality now joins explicit engine timing/price/cost,
original single-leg/basket/exit attribution, conservative unknown-timing selection,
all five causal depth horizons, reviewed automatic reduction, candidate cancellation
and bounded audits. **338 affected passes / 57.18 s**, **57 targeted / 8.23 s**, plus
**one single-leg case / 0.42 s**. Bad optional telemetry never hides reconciled cash
or units. Existing R05/R35/R40/R42 C/J credits already cover the integration slice;
**82/200, approximately 41%**, formal **1/50 (2%)**, unchanged. This does not validate
actual execution, EV capture, empirical adverse selection, independent review,
host deployment or unfunded acceptance. Full changed-tree regression is next.
Six remaining active-hour ranges remain appropriate to the unresolved source,
proof-delivery/governance/host scope; external waiting and owner actions are separate.


Initial fill integration full regression at **3fa1663c** passed **3980 / four
existing warnings / 267.20 s**, all **839 inputs unchanged**. Review then found a
repeating per-share Decimal incorrectly rejected as a ledger input; the new case
failed once, and the corrected aggregation passed **59 targeted / 10.17 s**.
Full changed-tree verification follows. Evidence: docs/V11_FILL_REGRESSION_EVIDENCE.md.
This is correctness work within existing credit: **82/200, approximately 41%**,
formal **1/50 (2%)**, unchanged. Six active-hour ranges and external gates remain.


Final corrected fill integration full regression at **33d92731** passed
**3981 / four existing warnings / 277.46 s / exit 0**, all **840 inputs unchanged**.
Both full manifests/output and the fractional-cost failure/correction are preserved
in docs/V11_FILL_REGRESSION_EVIDENCE.md. No new scored milestone closes:
**82/200, approximately 41%**, formal **1/50 (2%)**, unchanged. Remaining actual,
independent, host and unfunded gates, six active-hour ranges and separation of
external/owner waiting are unchanged. Next is bounded candidate reconciliation
of archived PAPER fill/terminal receipts; no owner action blocks that code.


Archived PAPER receipt reconciliation now joins the finite candidate priority tick,
existing common-account proof checks, atomic admission/submission fences, resumable
pending/cursor state and audits. The 34 targeted passes (5.12 s) include archive-only
fill input through reviewed monitoring/cancellation, proven terminal release and
daily audit. Malformed/public/foreign separation, interrupted delivery and health
loss remain fail-closed. Applicable R02/R03/R05/R21/R32/R33/R40/R45 C/J slices were
already credited; **82/200, approximately 41%**, formal **1/50 (2%)**, unchanged.
No actual source/calibration, independent acceptance, guardian or deployment gate
closed. The six active-hour ranges remain appropriate to the broader unresolved
scope. Account-change reevaluation, actual/owner evidence and READY_TO_FUND remain
open. Affected/full verification of this new tree is pending at this checkpoint.


The subsequent receipt-to-event join now advances the current census generation,
preserves source-loss findings and invalidates old inventory evaluations. The
candidate can consume an archived BUY fill, evaluate the existing whole-event
exit, reserve a common-account SELL, consume its explicit PAPER fill and reevaluate
remaining inventory with realized-P&L attribution. Final focused 40 / 6.33 s;
preceding saved receipt tree affected 328 / 39.18 s, all 842 inputs unchanged.
This is existing C/J scope: **82/200, approximately 41%**, formal **1/50 (2%)**,
unchanged. Source, calibration, independent and deployment/unfunded acceptance
remain open. Full verification of the combined integration is pending; next
implementation is validated receipt cost/slippage reporting through existing
PerformanceLab/audits, keeping unmatched evidence UNKNOWN. Six remaining active
hour ranges retain LOW confidence and exclude external waiting/owner actions.


Combined receipt/event/exit verification is complete at saved implementation
**5bfd38f0caa1f891738df459826fd1a7d6a4e203**, tree
**5b29ee49eba4ed46639205b4d3cc0916f6f97789**: **4020 passed, four existing warnings,
276.83 s, exit 0**, all **843 inputs unchanged**. Final event/exit regression
**91 / 9.84 s**; focused **40 / 6.33 s**; preceding receipt tree **328 / 39.18 s**.
Full manifest/output: V11_RECONCILIATION_REGRESSION_EVIDENCE.md. Verification of
already credited integrations earns no extra unit: **82/200, approximately 41%**,
formal **1/50 (2%)**, unchanged. READY_TO_FUND, six active-hour ranges, independent,
actual-source/calibration and owner/host gates remain open. Next off-host action is
validated receipt cost/slippage reporting through PerformanceLab and daily audits;
legacy or unmatched evidence remains UNKNOWN. No owner action blocks that code.


Receipt cost/causal price audit integration — 2026-09-25: validated optional
synthetic execution details now join retained reconciled fills to bounded
PerformanceLab execution-window costs, original signal/post-validation depth
comparisons and scheduled candidate audits. Partial fills share exact-book depth;
legacy/malformed timing stays in possible cohorts, costs already in all-in ledger
are never deducted twice, and pinned crash/replay preserves report identities.
Final 26 new checks passed in 6.99 s (exit 0), including the actual typed candidate
receipt-to-account-to-audit path. Affected/full verification of this new tree is
pending; the saved 4020-pass run remains evidence for the preceding implementation.
R05/R40/R41 already hold C/J, so this earns no new named milestone: **82/200,
approximately 41%**, formal **1/50 (2%)**, unchanged. No E/A, venue execution,
source calibration, independent safety or host/unfunded acceptance is credited.


Receipt-cost integration final verification: published 79a7b1e98388c34a9c817fc567cef5867ea59e0e,
tree 1852925219ce2ab0453b97a226fd0bbae44dc1f1, passed **4046 / four existing warnings /
347.89 s**, exit 0; all **845 inputs unchanged**. Affected **245 / 35.93 s** on the
same tree. Complete shared manifest/results are in
V11_EXECUTION_COST_REGRESSION_EVIDENCE.md. The preceding pending-verification note
is historical. More regression checks earn no new C/J/E/A milestone: **82/200,
approximately 41%**, formal **1/50 (2%)**, unchanged. Replay review identified the
remaining historical-model/receipt-boundary join; it is not yet implemented or
credited. Six full-scope active-hour milestones and external dependencies remain
in V11_WORK_CHECKPOINT.md; no calendar wait or financial authority is implied.


Historical economic replay first slice — 2026-09-25: PerformanceLab now reconstructs
original future/same-day temperature source/receipt boundaries, retained protected
model history and immutable bundles, reuses runtime prediction/valuation functions,
and compares original common-account risk/context. 18 new cases / 72 related passes
in 4.48 s after documented JSON decoding/assertion corrections. Automatic candidate
audits, full control-flow/PWS/challenger replay and historical executable attestation
remain open. No new named milestone is credited at this intermediate checkpoint:
**82/200, approximately 41%**, formal **1/50 (2%)**, unchanged. Actual independent,
host and unfunded evidence remain unearned; six active-hour ranges are unchanged.


Candidate replay audit join — 2026-09-25: optional typed replay now runs from the
scheduled candidate audit worker against all retained in-window temperature
decisions, with bounded references/shared budget, unknown-preserving selection,
original model/account identities and report recovery. The finite candidate test
covers mocked census -> derived risk -> original temperature decision -> common
account context -> replay audit. Final new 27 / 4.94 s; initial related 65 / 13.93 s;
a new invalid initial-cash fixture was corrected without relaxing account limits.
Combined affected/full saved-tree regression remains due before scoring R04's new
join. Intermediate total stays **82/200, approximately 41%**, formal **1/50 (2%)**.
This does not credit full control flow, PWS/other strategy/executable attestation,
actual sources/calibration, independent review or host/unfunded acceptance.


Verified historical temperature replay integration — 2026-09-25: **R04 earns J**
for original receipt-bound source/model reconstruction -> shared prediction and
valuation -> archived common-account context -> scheduled typed candidate audit.
The core C existed, but this upstream/downstream historical join did not. Later
source revisions, protected model changes and account appends cannot replace the
original inputs; missing history/policy gates, and report crash recovery is pinned.
Finite mocked-source candidate coverage plus 218 affected passes / 28.45 s and
4073 full passes / four existing warnings / 285.58 s verify implementation
`1fea164abd676d0b6f15f5ec11beba2e9fb45576`, tree
`af0a53888907076e68072b8a52fec163502751b5`, all 849 inputs unchanged.
Exact evidence: docs/V11_REPLAY_REGRESSION_EVIDENCE.md. New total **83/200**,
**approximately 42%** by the fixed half-up rounding. Formal **1/50 (2%)** unchanged.
This is the named integration slice, not complete engine/control-flow replay,
original executable attestation, empirical calibration or renewed financial
permission. PWS/other strategies/challengers, real/operational evidence and original
acceptance remain open. No E/A credit and no credit for more tests or elapsed time.


PWS historical observation/payout replay — 2026-09-25: the existing R04 integration
now also reconstructs original separate observation/payout epochs, paired PWS-on/
PWS-off inputs and exact research ablation, feeding shared observation and payout
calculations and scheduled candidate audits. Later labels cannot leak into the
original receipt boundary; original policies/history/inputs cannot be replaced.
15 new checks passed / 5.15 s, including candidate/audit recovery. Affected/full
verification remains due. **83/200, approximately 42%**, formal **1/50 (2%)**,
unchanged: R04 J is already earned. Full control-flow/commands/other strategies,
PWS label scoring, empirical source/calibration/lead and all independent/operational
acceptance remain open. No more credit for this expansion or additional tests.


Received-source strategy replay — 2026-09-25: SOURCE_SHOCK and RELEASE_OPPORTUNITY
now join the original received-report predecessor, exact post-receipt book/event
context, original payout model and common-account context to scheduled candidate
audits. Shared runtime receipt/change-type calculations and bounded historical
source queries preserve causal ordering after later reports/promotions. All five
temperature strategy variants have numerical joins. PWS affected 380 passed /
57.74 s at f0335ede; combined focused 79 passed / 17.50 s, with final combined
regression still due. **83/200, approximately 42%**, formal **1/50 (2%)**, unchanged.
R04 C/J are already earned. No full control/command/label replay, actual evidence,
independent/operational acceptance or financial authority is credited.


Final combined scoped replay verification — 2026-09-25: **4104 passed, four existing
warnings, 296.20 s**, plus affected **463 passed / 64.41 s** on published
`41d406951579a4c0acbe75f256cf3fc96ac588ed`, tree
`1d3cb8c4ea543a61681361428e68d44cd988ce1d`. All **853 inputs unchanged**.
Exact provenance: docs/V11_SCOPED_REPLAY_REGRESSION_EVIDENCE.md. This verifies the
future/same-day, PWS observation/payout and received-source candidate replay joins.
It does not close full control/command/label/executable replay or actual/independent/
operational acceptance. **83/200, approximately 42%**, formal **1/50 (2%)**, unchanged.
No additional C/J/E/A is credited for extension, regression count or elapsed effort.


Conditional PAPER account replay integration — 2026-09-25: original pre-state,
policy, prepared candidates/rejections, conditional exit checks and exact clock/
receipt inputs now feed the shared coordinator numerical engine without commands
or current admission. Reservation/status/recovery/fill/terminal comparisons join
complete bounded scheduled candidate audit cohorts, including protected synthetic
basket/exit reconciliation. Missing originals and incomplete cohorts gate; legacy
and unsupported commands remain unknown denominator members. Final focused
**82 passed / 10.84 s**, session **42335**, with initial shared-runtime **69 / 8.45 s**.
Intermediate fixture errors and raw evidence: docs/V11_ACCOUNT_REPLAY_EVIDENCE.md.
Affected/full regression on the saved implementation is next.

No new milestone is earned: R04 and the joined account/audit packages already have
C/J. Preparation/control-flow and executable replay, genuine source/model evidence,
independent acceptance, verified isolated deployment and unfunded READY_TO_FUND
remain open. **83/200 = 41.5%, approximately 42%; formal 1/50 (2%)**, unchanged.
The denominator remains 200 and covers all engineering through verified deployment
and unfunded readiness. V10 unchanged/DEFERRED; no financial authority.


Verified original account-effect integration — 2026-09-25: **4138 passed, 4 warnings in 298.08s (0:04:58)**,
exit 0, session **44615**, and **573 passed in 70.83s (0:01:10)**, exit 0, session **55056**,
on published implementation **e21ae6e4fbef2c14e3fd748fbda8314d8d54773a**, tree
**adf2367c7ca319b607f629d910acf54fb73cb7d8**. All 858 tracked inputs unchanged through both runs;
exact metadata/logs/map: docs/V11_ACCOUNT_REPLAY_EVIDENCE.md. This verifies the
shared conditional numerical account/candidate audit integration; it does not
complete original preparation/control-flow, actual evidence, independent review,
isolated deployment or unfunded readiness. More regression earns no extra credit.
**83/200 (~42%)**, formal **1/50 (2%)**, unchanged; V10 DEFERRED/unchanged and
NOT_READY_TO_FUND. No financial authority.


Final original-policy guard — 2026-09-25: replay also verifies the freshly
constructed historical configuration digest, preventing a replaced caller
policy/limit object from hiding behind a cached hash. Two production lines and
two focused cases followed the 4138-pass full integration. Final **84 passed /
8.92 s / exit 0**, session **39669**; exact final file hashes and the prior full
manifest are retained in docs/V11_ACCOUNT_REPLAY_EVIDENCE.md. Broad tests are
explicitly attributed to e21ae6e4; final targeted tests include the additional
guard. No additional full release or independent acceptance is claimed.
**83/200 (~42%)**, formal **1/50 (2%)**, unchanged. Existing C/J coverage improved;
no new milestone or authority. V10 unchanged/DEFERRED; NOT_READY_TO_FUND.


Test-environment umask defect fixed — 2026-09-26: diagnosed the reproduced
baseline authority failure carried over from the prior regression-attribution
checkpoint. Root cause: `ProductionConfig.activation_requested()` and the
equivalent `host_trust/*/authority.py` custody checks correctly reject
group/other-writable production files (`st_mode & 0o022`/`0o077`) as a real
security requirement; several test fixtures wrote those files without an
explicit mode, relying on umask `0o022` to produce safe permissions, but this
host's umask is `0o002`, so fixtures landed group-writable and tripped the
checks with no production defect. Pinned `os.umask(0o022)` for the whole pytest
process in `tests/conftest.py::pytest_configure`; no production source changed.

Verification: the retained 47-ID full-run failure cohort re-run against
unmodified HEAD plus this fix: **46 passed, 1 failed / 22.82 s**; all 14
contributing modules in full: **209 passed, 1 failed / 66.93 s**. The one
remaining failure (`test_v11_guardian_broker.py::test_actual_broker_death_stale_socket_restart_and_receipt_replay`)
is an unrelated subprocess-timing issue, root cause not yet investigated.
No full-suite regression was run this batch: the Bash tool's 600 s hard timeout
and the no-background-execution constraint are jointly incompatible with the
~1113-1125 s duration of the last several recorded full runs on this branch.

This resolves the diagnostic ambiguity left by the immediately preceding
regression-attribution checkpoint (46 independent-looking failures were one
shared test-infrastructure defect, not 46 separate production defects), but
claims no new C/J/E/A milestone: **85/200 (~43%); formal 1/50 (2%)**, unchanged.
NOT_READY_TO_FUND; V10 unchanged/DEFERRED. Next: investigate the guardian-broker
subprocess-timing failure, then run one full regression to confirm the
corrected failure count.


Guardian-broker restart test defect fixed — 2026-09-26: the remaining
`test_v11_guardian_broker.py::test_actual_broker_death_stale_socket_restart_and_receipt_replay`
failure (100% reproducible, not flaky) was a test defect: it detected broker
takeover of a killed sibling's stale Unix-domain socket by comparing inode
numbers, but this filesystem recycles an unlinked path's inode number into the
very next bind at that path, so the comparison never distinguished "still
stale" from "already replaced" and the wait loop only exited once the
replacement's whole finite run had already ended. Fixed by waiting for an
actual successful replayed `cancel` call instead of a filesystem identity
comparison; `paper_guardian_broker.py`'s stale-socket takeover logic was
verified correct via temporary reverted debug instrumentation and left
unchanged. Fixed test: 5/5 isolated passes. Retained 47-ID cohort against
current HEAD: **47 passed / 21.52 s**, exit 0 (previously 46/1 failed); same 14
contributing modules in full: **210 passed / 63.10 s**, exit 0 (previously
209/1 failed), no new failures. No production source changed. Full-suite
regression still not run this batch: this host has a single CPU and recorded
full runs take 1113-1125 s against this tool's 600 s foreground cap with no
background execution permitted — the same constraint the immediately preceding
entry recorded. No new C/J/E/A milestone: **85/200 (~43%); formal 1/50 (2%)**,
unchanged. NOT_READY_TO_FUND; V10 unchanged/DEFERRED. Next: run one full
regression when a background-capable or longer test window is available to
confirm the fully corrected failure count, then continue closing PARTIAL
requirements end-to-end.


Original supervisor batch 12 — 2026-09-27 (corrected by the independent
review above): changed the custody test fixture and documentation after finding
repeated CI failures. Reported **16 passed, 11 skipped** in the custody modules
and **491 passed, 11 skipped / 55.01 s** in the related selection. Missing local
uidmap meant those skips never executed the altered namespace path. No full
local regression or new C/J/E/A credit: **85/200 (~43%); formal 1/50 (2%)**,
NOT_READY_TO_FUND; V10 unchanged/DEFERRED.

The original pre-mapping fix and claimed root cause were incorrect; the
published CI remained red. The independent entry above and
`docs/V11_CI_FINDINGS.md` record the corrected sequence, preserved historical
WSL evidence and remaining CI diagnosis. R39 offline protected-configuration
and consumer-ownership work remains required; only actual credentials and
commissioning require the corresponding authorization.
