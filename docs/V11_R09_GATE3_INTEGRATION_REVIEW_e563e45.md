# Gate 3 repair adjudication and full-branch integration review — e563e45

**PASS for offline combined-tree integration; I1/I2 repair acceptance adjudicated.**
Independent Astra/high review of the held offline candidate against current main.
No blocking finding. **779 passed, 37 skipped, six expected fork warnings.**
The original repair driver's missing terminal is preserved as missing. Its exit
code and termination cause are unknown; no replacement driver certificate is
fabricated. This review issues its own explicitly reviewer-authored completion
record after independent code inspection and exact combined-tree verification.

The original report, result, final output and 15 referenced evidence hashes agree.
Both original P2 acceptance regressions and all 44 additional repair probes pass
again on the combined tree. This new decision supersedes the missing-terminal
acceptance dependency; it does not assert that the old driver completed normally.

Candidate: `e563e45a50f7b6df8b85c60df80aa879040edab8`, tree
`19faace86e5c60276e9a69aeaab42fc090993aea`. Reviewed main:
`a7463311968440f0744dbe5aefd7ee6e68c7d12c`. Prospective combined tree:
`0c3e5d794dcedb2e43eef7b4d70446ab3d404ecd` in
`/tmp/alpha-v11-gate3-adjudication-e563e45-a746331`. This is an uncommitted
merge in a new detached review worktree. Main and the author branch were not
merged or rewritten. All 996 tracked main files retain their original bytes;
exactly nine candidate paths / 5,100 lines are added, with no collisions. All
nine blobs match the author. The earlier preflight's main `928401a` differs only
in the three ledgers; no executable delta was overlooked.

## Findings and scope

No new blocking finding in the offline integration scope. I1 and I2 are resolved.

- I1: the budget captures its constructor PID. Reserve, next-read, complete and
  append check ownership through `_healthy`; consume checks before inspecting or
  mutating accounting. Child descriptor closure does not issue LOCK_UN. The
  original one-byte-cap reproduction now refuses inherited use, preserves the
  parent's exclusion and accounting, and reopens cleanly. Independent probes also
  cover active, partial, complete, violated and poisoned budgets, no child I/O,
  double close, parent descriptor state and refusal evidence preservation.
- I2: launch artifact resolution first pins metadata with O_PATH/O_NOFOLLOW,
  rejects nonregular type, reopens the pinned regular inode through procfs, and
  compares descriptor/path identity before and after reading. Both ordinary
  references and the second TZif read use it. Public-validator probes verify
  static FIFO/directory/symlink refusal, substitutions after pin/read, equal-byte
  inode replacement, metadata/digest checks, and proc-open failure cleanup.
  Missing Linux/proc support refuses; no unsafe fallback is present.
- Full lineage: reviewed the strict V3 manifest, request accounting, synthetic
  exchange, bounded regular-grid decoder, clock sequence, legacy store and v1
  restart implementation, alongside the author composition tests and predecessor
  reviews. The complete 2,713-slot inventory, provider-specific caps, bound
  overhead/field identities, canonical pins, original clocks and uncompleted
  budget reservations remain intact. V1 recovery retains UNKNOWN historical
  acknowledgement and false historical feature eligibility. A store receipt
  never completes a budget request or establishes runtime availability.
- Retained repair acceptance covers the earlier strict-validator/I/O fixes,
  store R1–R7, interruption boundaries and composed accounting. Old tests whose
  purpose was to demonstrate defective behavior are excluded from acceptance;
  their evidence remains preserved. No failed predecessor verdict was silently
  converted to PASS. This is a new decision for repaired bytes on newer main.
- Static and fresh-subprocess import probes find no production or existing-tool
  import edge to the three new modules. All six module import orders work. The
  production entrypoints, candidate runner and Brain readiness import without
  loading these modules. The new tools contain no CLI or top-level launch loop.
  Existing main executable/test blobs and the 64 KiB production CGI decoder
  remain unchanged. Accepted release resolution `6ec371e` remains an ancestor;
  there is no new reason to reopen its resolved load-sensitive release issue.

## Verification and completion record

All suites ran serially with a 180-second owned-process-group timeout per suite,
using the existing review venv, explicit combined-tree PYTHONPATH, bytecode/cache
writes disabled and disposable evidence basetemps. The repository conftest
removes proxies and refuses external socket connections; its identical copy
protects external probes. No real-input opt-in was enabled.

- **affected: 345 passed, 2 warnings in 24.83s**, exit 0; log SHA-256 `2e28cd784a5be742be2078508c33f3e9578181f808572d2254737523a40fbe6f`.
- **retained: 238 passed, 4 warnings in 7.49s**, exit 0; log SHA-256 `a4ad445c556b37c86fb550b3ce6edc71dc09714ff5e55de64c0e1e9ace3fabef`.
- **boundaries: 196 passed, 37 skipped in 76.58s (0:01:16)**, exit 0; log SHA-256 `041f3c2cac6f3ab0fef180b745c11171dff93a82dbd5ae3b59bbda2f611b2148`.

The 238 retained cases comprise 223 prior acceptance cases plus 15 main-file,
import and entrypoint probes. The affected 345 include the two original I1/I2
regressions and 44 independent repair probes. The 37 adjacent skips concern
optional ecCodes and explicit local raw/immutable-input gates; they confer no
coverage. No full release suite was rerun: existing executable blobs are unchanged,
no reverse production import edge exists, and the relevant adjacent suite passed.

The [result](V11_R09_GATE3_INTEGRATION_REVIEW_e563e45_result.json) records exact
commands, identities, log hashes and original anomaly evidence. The
[completion record](V11_R09_GATE3_INTEGRATION_REVIEW_e563e45_terminal.json) is
issued by this synchronous reviewer, **not the missing external driver**.
The unchanged [44 repair probes](V11_R09_GATE3_INTEGRATION_REVIEW_e563e45_repair_probes.py)
and [two predecessor probes](V11_R09_GATE3_INTEGRATION_REVIEW_e563e45_predecessor_probes.py)
are now durable. Their fixtures resolve through the tested module's actual path.
Other retained probes are already tracked with their original reviews.

All complete logs, the prepared tree, per-file hashes and adapted preservation
probes remain under `/tmp/alpha-v11-gate3-adjudication-e563e45-a746331-evidence`.
Original reviewer artifacts are unchanged. The driver source would write a terminal
after collecting worker status, but no such file or observed exit status exists;
its empty driver log does not establish why it stopped. Report/final-output
agreement is supporting evidence only; this new verdict rests on the independent
code review and reproduced exact-tree results.

## Remaining boundaries and next action

The technical recommendation is to accept this exact tree for **offline local
code integration only**, after the coordinator reads this review and its own
completion record and checks then-current main. The routed review explicitly
retains the merge hold: no merge or publication is performed or authorized by
this report. A documentation-only main advance can be reconciled by blob
preservation; an executable or candidate change requires appropriate review.
The original missing driver terminal need not be recreated or repeatedly retried.

After that coordinator decision, the next substantive Gate 3 work is a bounded
**offline design** for actual transport/runtime orchestration: bind approved
origins and persistent denial history to durable attempts, reserve/account every
purpose, enforce the absolute acquisition window and one-in-flight pacing, and
compose budget/store/clock receipts without implying historical admission.
Implementation must follow a reviewed design and exact-commit independent review.
No live request or private launch manifest is needed for that design step.

All operational holds persist: real anonymous transport/DNS/public-peer/TLS and
source/index/range/object qualification; genuine source/build and clock evidence;
supported storage/process lifecycle, quota/resource/report capacity and physical
persistence qualification; genuine typed launch evidence and exact G3-L envelope;
G3-E replay and original runtime-use proof; feature/label/split/learner acceptance;
root-custodied model authority and genuine forward SHADOW. The validator returns a
digest only and cannot attest the truth of referenced bytes. Synthetic clocks,
SIGKILL and survivor fixtures are not real synchronization or power-loss evidence.
Same-UID malicious mutation, general thread-safe budget use and namespace
immutability after return are not claimed. No self-install, authority bypass,
provider request, promotion, financial execution or score credit follows.

V10 and AxiomTrade are untouched. Read-only recovery confirms demo/scanner/controller
inactive and disabled, execution inactive and masked, protected model-authority
paths absent, and the private FINAL-REVIEWED master hash matching its pin. New
commissioning writes are status files only. GEFS forward SHADOW remains owner/root
gated. **91/200 (45.5%), formal 1/50; NOT_READY_TO_FUND**, unchanged.
