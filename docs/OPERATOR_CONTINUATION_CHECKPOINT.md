# Production operator continuation

Baseline/main at phase-one inspection: `9552357c9de550c522c60910b4ccb25abd9d9cfd`.
Tree: `a0e6ee81cae61bf064f5c9b3eb0a70606603e863`.
Branch: `weather-telegram-operator-continuation-2026-09-16`, created directly from that baseline after a fresh fetch; main had not moved.

The previous production engine is retained. No Google/UpCloud host, service,
production database/Telegram account, private credentials or funded account is
accessed. No billable resources, financial transactions or production messages
are authorized. All mutation interfaces used in tests are isolated mocks.

## Phase 1 — actual baseline gaps

Inspected canonical `production/__main__.py`, `config.py`, `engine.py`,
`ledger.py`, `service.py`, `signals.py`, `telegram.py`, execution API/host guides,
the production checkpoint and regression suites. Prior main verification is
retained evidence (1,865 cases on each Python version), not a new test run.

| Capability | Classification | Actual implementation / operational meaning |
|---|---|---|
| Automatic directional execution | IMPLEMENTED BUT REQUIRES HOST/ACCOUNT ACCEPTANCE | `ExecutionEngine.execute/tick`; fresh evidence/limits, authenticated BUY FAK, durable accounting; mocked behavior tested, no funded acceptance |
| Authenticated maker execution | IMPLEMENTED BUT REQUIRES HOST/ACCOUNT ACCEPTANCE | Post-only GTD, actual order/chain fills and cancellations; no public-print fill inference |
| Structural baskets | IMPLEMENTED BUT REQUIRES HOST/ACCOUNT ACCEPTANCE | Sequential FAK with full-cost reservation and explicit legging loss cap; partial inventory is held, not automatically unwound |
| Telegram text reports and stop/cancel requests | IMPLEMENTED AND TESTED | `SignalService.commands`, numeric operator authentication, status/recent/positions/stats and durable stop generation |
| Telegram settings/screens/buttons | NOT IMPLEMENTED | Updates currently accept `message` only, with no callback protocol or settings panel |
| Confirm each trade | NOT IMPLEMENTED | Executor currently consumes eligible delivered signals automatically when authorized; no one-use operator decision |
| Proactive execution notifications | NOT IMPLEMENTED | Executor records audit events and a status snapshot; controller has no durable execution-event notification consumer |
| Pause and cancellation request | IMPLEMENTED AND TESTED | Stop opening is distinct from cancellation; cancellation is distinct from selling holdings |
| Telegram resume | NOT IMPLEMENTED | Resume is currently a local config-bound, fresh-status and stop-generation checked CLI operation |
| Direct EOA account/signing | IMPLEMENTED BUT REQUIRES HOST/ACCOUNT ACCEPTANCE | Wallet=signer only; existing L2 credentials and account exclusivity required; EOA key is not technically withdrawal-restricted |
| Deposit Wallet/session-key adapter | EXPLICITLY UNSUPPORTED | Current config/adapter reject these identities; official compatibility/access must be researched before making an onboarding recommendation |
| Autonomous selling | EXPLICITLY UNSUPPORTED | BUY-and-hold; pausing never sells inventory |
| Automatic redemption | EXPLICITLY UNSUPPORTED | Owner performs redemption separately; read-only receipt import verifies burns/proceeds/gas and does not submit a transaction |
| Supported weather semantics | IMPLEMENTED AND TESTED | Strict reviewed station/date/unit contracts; completed enumeration is distinct from partial semantic coverage; current WRH cannot prove result-lag publication finality |
| Signal/simulation/actual accounting | IMPLEMENTED AND TESTED | Outcomes without trade inference; confirmed fills, claim value, redeemed cash and excluded historical research remain separate |

The intentionally excluded wallet/sell/redemption scope is not classified as a
defect. It requires an explicit operational plan, honest panel state and, where
appropriate, a separately scoped extension.

## Phase 2 — operator boundary design

Protected base configuration and its existing activation digest remain the
independently approved maximum authority. A versioned operator-control section
will bind numeric bot/private-chat/operators, authorization version/expiry,
permitted experiences and schedules. Mutable requests can only select a subset
of those permissions and risk ceilings. Telegram never rewrites protected config,
grants operators, clears a fault, runs a shell or supplies a signing payload.

Buttons refer to server-held actions bound to actor/chat/bot, configuration and
authorization versions, control revision, expiration and one-use state. The
executor validates structured requests independently, consumes confirmations
durably, and invokes the existing fresh-evidence execution path. Pause/cancel
remain available during reconciliation; settings changes invalidate stale
confirmation buttons and never erase existing fills or commitments.

Execution notifications derive from durable journal events and pass through a
bounded sanitized feed to a credential-separated controller outbox. Ambiguous
Telegram delivery remains visibly uncertain and is not blindly resent. Telegram
latency must not block financial reconciliation or cancellation.

## Sequential phases and progress

1. Baseline/gap matrix: complete.
2. Telegram operator product and security protocol: implemented on the continuation branch; final boundary review/full exact-head verification pending.
3. Official wallet/security compatibility and onboarding: documented; restricted-wallet adapter and beta access remain explicitly unsupported/unprovisioned.
4. Exact-release UpCloud unfunded commissioning package: prepared; three-role
   host reference policy and bounded public measurement tool implemented. Actual
   VM specification/address/SSH and independent first installation are unprovisioned.
5. Full matrix, exact-head checks, fresh review and gated PR promotion: pending.

Implementation: 85%; verification of new work: 25%; deployment preparation: 90%.
Main changed by this continuation: no. Host acceptance/funded execution: not run.

## Phase 2 implementation checkpoint

Implemented the private panel, structured bounded control protocol, executor-owned
settings/receipts and one-use confirmations, independent safety loop, durable
audit-derived notifications, scanner/controller separation, and an explicit local
authorization rotation path preserving the financial journal. Existing submission,
reconciliation, fills, risk reservation and fault recovery remain in their original
engine/ledger ownership. No new runtime dependencies.

Evidence so far (isolated mocks/temporary DBs):
- Initial request protocol + lifecycle: 48 passed on Python 3.12.
- Protocol/executor + lifecycle: 64 passed.
- Panel/events/protocol/executor + lifecycle: 109 passed.
- Scanner/controller handoff: 7 passed.
- Full intermediate Python 3.12 suite: 1,954 passed, four unchanged legacy
  FastAPI deprecation warnings, 65.31s; XML/log retained in scratch. This predates
  the final grant-rotation and same-day screen edits and is not final-SHA evidence.
- Python 3.11, final hash/shell/systemd/public workflows and final independent
  review: not yet run for this continuation.

This was the phase-2 checkpoint; the subsequent phases below supersede its next
step. No host installation or deployment has occurred. Main remains at baseline.

## Phase 3 compatibility checkpoint

Official research confirms default new Deposit Wallets and beta session keys with
restricted venue authority, approval requirements and separate order visibility.
The existing direct EOA adapter is retained, not relabeled withdrawal-disabled.
`ACCOUNT_SECURITY_ONBOARDING.md` records capability/compromise distinctions, the
precise unimplemented restricted-adapter gap, revocation, supported unfunded
onboarding and the narrower direct CTF/USDC.e receipt scope. The panel discloses
these limitations; configuration now rejects explicit unsupported wallet/session
requests instead of ignoring them and falling through to EOA.

Wallet-scope plus retained exchange tests: 99 passed on Python 3.12 (0.47s).
No beta access, account acceptance, financial activation or owner-key fallback
was attempted. Recommended immediate operation remains unfunded LIVE_SIGNALS;
a restricted account requires external approval and a separately verified adapter.
Next: new-host commissioning package and three-service independent host policy.

## Phase 4 commissioning checkpoint

`UPCLOUD_UNFUNDED_COMMISSIONING.md` supplies the unfunded sequence, three service
identities, exact-release approval/environment custody, resource measurements,
backup/restart/failure drills, private-account checklist and explicit funding stop.
It names external first-install authority, unknown host facts, source licensing
and optional paid-route integration as unresolved prerequisites. No subscription
or billable resource was added. The source probe only permits bounded public
GET/HEAD requests and records exact checkout identity and aggregate resource/API
metrics; it is not a host/account certification.

The reference host policy now binds separate scanner/controller units, UIDs,
fresh environments, credentials and writer leases; changed control/scanner state
requires forward recovery. Existing host regressions: 37 passed on Python 3.12;
new three-role/custody/rollback cases: 9 passed. Fixture exceptions were corrected
to the authority's actual `AuthorityError`; no production check was relaxed.
Source-probe/full exact-release checks follow during phase 5.

Current work before final freeze: additional grant-rotation/config parsing and
manual-settings recovery tests, final UI/state consistency review, full 3.11/3.12
verification and independent review. Main unchanged; no production messages,
financial calls, services or databases touched.

## Phase 5 candidate freeze preparation

Main was fetched again and remained `9552357c9de550c522c60910b4ccb25abd9d9cfd`.
Workflow review found runner tests/public probes with read-only repository
permissions, no deployment jobs, production secrets, SSH, service control or
funded actions. The continuation branch is added to the three existing relevant
public probes and a new bounded public-collection measurement workflow. These
checks do not contact Telegram or private account APIs.

Additional boundary fixes propagate reduced thresholds into collection and
filter queued older signals, revalidate/audit manual-mode requests on restart,
retain last displayed settings when execution status expires (authority remains
closed), expose exchange expiry, export sanitized execution events, and record
local emergency pauses durably. Status publication is bounded independently of
the UI reply loop. Grant rotation preserves actual fills and sticky faults and
rejects open orders, wrong identity, missing activation or a nonempty new store.

Scoped results: 173 operator/lifecycle cases passed on 3.12; 156 operator/host
cases passed on 3.12 after the final refinements, including actual
`systemd-analyze verify` of all three rendered fixture units. Probe guards cover
both asynchronous sources and synchronous WRH requests. Full suites and final
independent review remain pending; these numbers are test results, not a
probability of correctness. No host/account acceptance has run.

The initial frozen tree `b73aab3202e3a298f2dd19540e7acfdc45e15d0f`
(local commit `022c3eaee1fdad90d46fac45a5691fcc2669f3fe`) passed 2,021 cases
on each Python version (3.11: 56.81s; 3.12: 65.76s), with four existing legacy
FastAPI deprecation warnings. Compile/dependency-consistency/shell checks: 45
passed. Six retained systemd units verified; new three-role units verified in
the host suite. Synthetic 23,200-event / 243,750-market inventory gate passed;
builder peak RSS 100,929,536 bytes, reader 121,888,768 bytes, maximum reader tick
0.00630s. These are local synthetic measurements, not UpCloud acceptance.

**Independent review rejected that freeze despite the green suites.** Reproductions
showed accepted cancellation lost behind a preceding pause/settings revision or
processing expiry, navigation preview capacity blocking `/stop`, and a trade
preview acquiring a newer more permissive revision while rendering. Fixes make
already accepted safety reductions durable and monotonic, allow safety priority
over unused previews/reply pressure, and bind text/buttons to one captured
revision. Fresh opening/resume/settings authorizations retain strict expiry and
revision checks. The review's failing tests are retained and promoted to
`test_operator_safety_priority.py`; final re-review and exact-head tests are still
required. No branch/main promotion occurred on the rejected freeze.
