# Weather production engineering checkpoint

Status: IN PROGRESS — NOT APPROVED FOR DEPLOYMENT OR MAIN.

## Exact source identity and ancestry

- Original/current main: `077b09e832600cd8c8e83cd97cbcf09becd73243`, tree `70eddd1d897661064b961a1a6f0a81807af09cd2`.
- Remote recovery reference: `recovery/main-before-weather-production-2026-09-16` at original main (verified preserved).
- Failed baseline branch: `weather-all-paper-final-review-fixes-2026-09-16`, `93d04f347f5e670c2821edc7d714c713d061b5e3`, tree `ef2a61a2cd2f354a03d08498863ddda6487e7544`.
- Stage 1: `weather-stage1-findings1-4-corrective-2026-09-16`, `80c34842def611ab176fb62bb6e5924441487979`.
- Stage 2: `weather-all-paper-stage2-findings-5-7-2026-09-16`, `a117b149ad0c19b551e9dd8160c47cf3111fa169`.
- Additional v3 host corrections inspected/selected: `c2e20b560afc3682c53cfe4cfd6df3837eada65d`.
- Alternate host branch inspected: `9f0c516ba4300022db31cd56bbc902f7ee118804`.
- Integration branch: `weather-production-integration-2026-09-16`.
- Initial integration merge: `d02a335c120e9a07803e8fa378579bd09a887668`.
- Weather corrections: `5d1cf96`; composed weather pipeline: `758623ad0a570bf5611210c59f67399d30319dd3`.
- Reviewed execution adapter commits: `9d7442d596519708e0ed0534268fa537f0742fed`, `b28ecc2d662c19162ec33d74fe76b0aaf3466aa8`, `23bdb09bb4fe3a790fd00c7ac2217c14eb0a653b`.
- Production lifecycle: `888413aa5951a5b0b928108213413372dddefe82`; real adapter/engine mocked integration: `048913b4bbd7ecb9563739c024582a49d683878f`.
- First frozen independent candidate: `c4131c64c4603d781bde7ee262183d70d1fbd8fe`, tree `e84056f6d515dc98a527f9120d9839b024e62f68`. Rejected by independent review; follow-up changes require a new frozen review.

Main is an ancestor of both correction branches. Stage 2 lacks latest Stage 1:
34 Stage-1-only / 32 Stage-2-only commits, merge base is the failed baseline.
Integration retains both histories and selects v3 immutable source/environment
architecture from the additional correction branch. Unrelated main code is preserved.

## Automation and preservation

Checked-in automation contains tests and public read-only probes; no production deployment command or secret reference was found. The obsolete Stage 2 test-rewriting/pushing workflow was removed; legitimate tests remain subject to full verification. Reinspect before publication/merge.

Private webhook/deployment settings were not exposed by the connected GitHub API. Direct Git push dry-run reports no GitHub username credential; publication will need the authorized GitHub connector. No production implementation has been published yet. Earlier draft PR #1 contains only an explicitly incomplete historical checkpoint and must not be merged as the production product.

## Review and finding ledger

| Finding | Reproduction | Correction | Regression/final state |
|---|---|---|---|
| F1 independent host authority | Candidate-owned bootstrap/provenance and corrupt candidate recovery reviewed | External independently approved host authority; candidate copies are reference only; unsafe self-bootstrap retired | New temporary-host behavioral tests; integrated host gates pending |
| F2 immutable generation | A snapshot → B prepare → repeated snapshot/cross-generation recovery | Immutable predecessor/candidate bindings, leases and independent custody | New generation tests; integrated host gates pending |
| F3 clean environment | Polluted predecessor, removed dependencies, incomplete inventory | Fresh role-specific approved hash locks, exact inventories, sealed predecessor | Host tests pending final; execution hashes installed on both supported Pythons |
| F4 loader isolation | Python/ELF/proxy/dotenv/custom-CA injection and effective paths | Minimal environment, isolated startup, origin/inventory checks; separate UIDs/state dirs | New behavioral host tests; final integrated run pending |
| F5 semantic coverage | Seven weather regressions reproduced against d02a335; strict authority/complete weather-looking accounting | Strict reviewed city/station map, inclusive census with rejection ledger, bounded partitions and fresh forecast/quote checks | Scoped 1,121 tests pass on each Python; exact-head public census pending |
| F6 delivered terminal state | Historical backlog starvation, late receipts, failed outbox writes | Atomic terminal/audit/sync; immediate targeted invalidation and bounded retries; separate real order ledger | Weather and production lifecycle/independent regressions pass; frozen pass pending |
| F7 legacy maker P&L | Populated historical research/settlement migration | Preserve excluded uncertified maker history; actual ledger never imports paper P&L | Historical weather migration tests pass in scoped suites; final full run pending |
| Production authority/account identity | Zero allowance, wrong account/config, unknown external trades/holdings | Explicit verified mode/credentials/account/risk/activation/stop/reconciliation; exact signed-order identity | Independent regressions pass; close-only reads separated from opening authority |
| Crash/race accounting | Lost response, rejected/unknown submit, partial/cancel races, DB audit faults, expiry in flight | Durable reserved intent before one POST, no blind resend, authenticated/chain-confirmed fills and fees | Lifecycle and adapter tests pass; real ABI receipt integration covered |
| Operator consistency | Command starvation, oversized/escaped message crash, output alias activation, stale worker report | Independent command loop, complete-message refusal, bounded human reports, output custody and exact status identity | Independent regressions pass |
| Long-running execution | Lifetime history scan, repeated terminal audit, delayed finality forcing one-leg baskets | Bounded rolling history plus all hot orders; idempotent terminal audit; explicitly selected full-cost sequential basket policy | Bounded history regression and delayed/partial basket tests pass; scale fixture added |
| Financial recovery | Rewinding execution journal would erase externally committed state | No execution journal rewind; preserve changed journal and require approved compatible recovery | Host generation/rollback proof pending final |
| Frozen R1 terminal reversal | Locally CANCELLED/REJECTED, remotely LIVE, reserve zero | Atomic fault, restored remaining reservation, cancellation quarantine; never repost | Reviewer baseline fails; corrected exposure/cancellation/DB-failure regressions pass |
| Frozen R2 station/date budget | Real weather revalidation corrects forged producer label, but intent used original label | Reserve against independently derived fresh station/day | Real pipeline/SQLite regression fails baseline and passes fix |
| Frozen R3 opening census | New unmanaged order/holding observed immediately before submission | Refuse opening and require account reconciliation | Both reviewer regressions fail baseline and pass fix |
| Frozen R4 stale account authority | 40-second proof walk relabelled old census as current | Authority age starts at earliest actual snapshot observation/start | Reviewer baseline fails; corrected regression passes |
| Frozen R5 Telegram custody | Same message ID edited in new configured chat | Durable bot/chat identity binding before send/service startup | Real adapter with mocked transport baseline fails, fix passes |
| Frozen R6 legacy cutover | Canonical service ignored populated predecessor terminal outbox | Explicit chat-bound offline import, read-only predecessor snapshot, excluded immutable archives, canonical outbox; direct legacy startup refuses | Populated maker/terminal history, idempotence, source mutation, retry budget, failed outbox transaction and leases tested |
| Frozen R7 lifetime trade scan | Opening discarded rolling census cursor | Bound opening refresh to persisted overlap plus oldest outstanding order | Reviewer baseline cap reproduction fails, fix passes |
| Host snapshot race | Concurrent directory/symlink swap reads outside predecessor environment | Descriptor-relative O_NOFOLLOW traversal through hash/archive; independent follow-up tests | Baseline executable race confirmed; corrected host verification ongoing |

## Observed verification (development trees, not final release approval)

- Weather reviewer: 1,121 passed on both Python 3.11 and 3.12; seven selected regressions fail against preserved integration baseline. Archive: `docs/review-evidence/weather-production-2026-09-16.zip`.
- Execution adapter reviewer: 80 passed on both versions; exact hash installations and `pip check` passed. No real authenticated account/order calls.
- Independent production reviewer: 34 scenarios passed on its latest Python 3.12 development snapshot; preceding 33-test snapshot passed both versions. New implementation is still mutable.
- Root focused production integration: 149 passed on Python 3.12 before the latest small controls/permission changes; fresh runs ongoing.
- First complete integration Python 3.12 run: 1,647 passed, 25 failed, 4 legacy FastAPI deprecation warnings, 41.32 seconds. Failures: 24 host/deployment/dirty-checkout assertions plus one dev-lock policy test; corrections and final rerun pending.
- Public Polygon RPC probes: all three documented provider endpoints returned TRANSPORT_FAILURE from this workspace. Deployed fee-bound readiness is UNVERIFIED; exact-head public CI probe added.
- Complete final Python 3.11/3.12 suites, frozen independent review, exact-head source/census CI, merged-tree validation and actual-main CI: NOT YET COMPLETE.
- Frozen reviewer: eight failing cases on each Python against c4131c6 (seven findings above); independent context, not author self-review.
- Corrected production focused suite: 90 passed on Python 3.12 in 4.35s, including all eight reviewer cases, migration and quarantine interactions. Full exact-head verification remains required.
- Full c413 parallel runs were interrupted by scratch disk exhaustion; both exited1 and cannot be counted as successful verification. Completed disposable host fixtures were removed; logs retained. Subsequent full suites run sequentially.

No production VM/services/database/Telegram/host-trust installation, private credentials or funded account was accessed. All financial mutations in tests are isolated mocks. No production activation occurred.

## Remaining gates and progress

Finish host correction/traceable test modernization; complete full integration and source/protocol probes; commit/freeze; fresh independent review; publish a real product PR; fetch main and verify actual proposed merge; merge only after every gate passes; verify actual main SHA/tree/CI.

Review coverage: 85%; corrective fixes: 85%; production implementation: 90%; required verification: 55%; main integration: 0%. These are work estimates, not reliability probabilities. Main has not changed.
