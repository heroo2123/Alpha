# Operator continuation: verified scope and handover

Baseline main: `9552357c9de550c522c60910b4ccb25abd9d9cfd`, tree
`a0e6ee81cae61bf064f5c9b3eb0a70606603e863`. No concurrent main changes were found
at the final code verification fetch. Recovery reference:
`recovery/telegram-operator-baseline-9552357`.

Branch: `weather-telegram-operator-continuation-2026-09-16`.
[Normal promotion PR #3](https://github.com/heroo2123/Alpha/pull/3) is the source
of the eventual merge commit and its actual-main checks; this evidence snapshot
precedes that merge. Publication uses ordinary descendant commits, no force push.
The local chronological checkpoint commits remain preserved; GitHub publication
has different commit metadata but exactly matching verified trees.

Final independently reviewed code: local
`0351a3f10f932c899c9357b7dc2d412ff33dbe6d`, published
`680d21a8fca4e1440ba5a6adaf157058e5f7c702`; both have tree
`965ecb0afd7d6cab15e0c62c5e4f06f7184be8b3`. The subsequent handover/evidence
commit changes only documentation and its evidence archive. It must pass final
exact-head checks before merge; verify the actual merge SHA/tree and its checks
in PR #3 before independently approving a deployment. A moving main name alone
is not a release identity.

Implementation of the scoped continuation: **100%**. Repository/code verification:
**100% at the identified candidate**. Deployment-package preparation: **100%**.
Actual UpCloud host acceptance, test-bot acceptance, private account acceptance,
funded execution and deployment: **not performed**. These percentages do not
describe correctness probabilities or completion of unsupported wallet features.

## Final capability matrix

| Capability | Classification | Operational result |
|---|---|---|
| Automatic directional/same-day/source-shock | IMPLEMENTED BUT REQUIRES HOST/ACCOUNT ACCEPTANCE | Retained fresh validated BUY execution, enabled strategies and explicit limits |
| Authenticated maker | IMPLEMENTED BUT REQUIRES HOST/ACCOUNT ACCEPTANCE | Retained post-only GTD; authenticated actual fills; cancel management |
| Structural baskets | IMPLEMENTED BUT REQUIRES HOST/ACCOUNT ACCEPTANCE | Full-cost reservation, sequential FAK, explicit legging risk; partial inventory held, no unwind |
| Private Telegram screens/settings/buttons | IMPLEMENTED AND TESTED | Status, strategies, risk, modes, orders, positions, performance, claims, coverage and wallet scope; mocks only |
| Confirm each trade | IMPLEMENTED AND TESTED | One durable bounded attempt tied to displayed settings; fresh weather/contract/book/risk checks; no stale quote purchase |
| Proactive trade/fill/failure notifications | IMPLEMENTED AND TESTED | Bounded durable audit-derived outbox; uncertain send stays UNKNOWN; no exactly-once promise |
| Pause/cancel/resume | IMPLEMENTED AND TESTED | Safety requests survive delay/restart; cancellation is not selling; resume requires existing independently granted authority |
| Direct EOA | IMPLEMENTED BUT REQUIRES HOST/ACCOUNT ACCEPTANCE | Dedicated wallet=signer; full key authority, not technically withdrawal-restricted |
| Deposit Wallet/session keys | NOT IMPLEMENTED | Beta access unknown; explicit adapter/signing/reconciliation gaps; no owner-key fallback |
| Automatic early selling | EXPLICITLY UNSUPPORTED | BUY-and-hold; held inventory survives pause/cancel |
| Automatic redemption | EXPLICITLY UNSUPPORTED | Owner acts externally; read-only direct CTF/USDC.e receipt import has narrower scope than modern adapters |
| Weather semantics/coverage | IMPLEMENTED AND TESTED | Strict supported subset, unsupported reason census; same-day/source-shock Fahrenheit-only; result-lag gated by missing finality |
| Signal/hypothetical/actual accounting | IMPLEMENTED AND TESTED | Outcomes never infer a manual trade; actual fills/fees, claims, redeemed asset proceeds and excluded legacy history stay separate |

## Findings and executable evidence

| Reproduction / finding | Correction | Regression / final evidence |
|---|---|---|
| Accepted `/stop` then `/cancel_open` in one poll lost cancellation after revision advanced | Accepted safety reductions validate original acceptance, remain durable and advance epochs monotonically | `test_operator_safety_priority.py`; retained initial failing tests/logs |
| Worker restart/delay expired an already accepted cancellation | Acceptance must have been fresh; subsequent processing time cannot erase safety work | Same suite; all opening/resume/settings expiry checks retained |
| 500 unused UI previews or a full reply queue could block safety dispatch | Safety can retire an unused preview; poll continues despite reply pressure | Preview-capacity and same-poll/full-reply-queue cases |
| Trade preview showed a lower limit but its button acquired a newer revision | Text/buttons bind one captured settings revision | Deterministic concurrent-publication regression |
| Direct `/stop` made two settings reads and could reject itself during publication | One captured revision for fresh safety action and consumption | `test_operator_direct_stop_race.py` |
| Manual settings were not revalidated on restart or propagated fully into collection | Durable manual receipts, common envelope validation, current scanner thresholds and queued-signal filtering | `test_operator_recovery.py`, collection tests |
| Three-role deployment custody was absent; old rollback could not bind control/scanner state | Independent role policy, path masks/locks/fresh envs; changed state requires forward recovery | `test_host_operator_roles.py`; actual generated-unit syntax validation |
| Runtime lock had only 3.11 native wheels; 3.12 public jobs failed before probing | Add six already-reviewed 3.12 hashes, unchanged 22 pins; fresh isolated runtime jobs | Initial CI failure retained; both isolated installs pass |
| Canonical-name duplicate could hide an extra hash in the new verification tool | Normalize case/PEP-503 names before duplicate rejection | Original independent failure plus four spelling regressions |
| Expiry/skip/emergency state lacked complete operator evidence | Explicit expiry display, sanitized event export and durable local-stop event | Notification/lifecycle cases; no ACK-as-fill or cancel-as-terminal inference |

The separate reviewer rejected two earlier freezes and then approved the final
scope. It independently reproduced six control failures, checked 126 boundary
cases per Python on unchanged final runtime/host code, and ran seven final hash
gate cases per Python. Earlier failures were preserved, not relabeled as passes.
This is distinct from the primary agent's self-review/full-suite runs.

## Verification results

Local final code suites: Python 3.11 **2,033 passed**, 59.48s; Python 3.12
**2,033 passed**, 68.87s. Four pre-existing FastAPI deprecation warnings per run.
Migration, ledger failure injection, legacy performance exclusion, account/signer,
host-loader, rollback, callback/replay, cancellation/fill and notification cases
remain in the supported suites. No legitimate test was deleted to obtain green.

Final local static/dependency gates: **47 passed** (compile on both versions,
dependency consistency, canonical runtime inventories and 41 shell syntax checks).
Fresh runtime-only hash installs pass on both versions, with scanner/controller
imports and no signing packages. Generated three-role units pass systemd syntax
verification; six retained research units also validate. Nothing was installed
or started as a host service.

Synthetic capacity gate: 23,200 events, 243,750 inventory markets, 13,500 selected
records; builder peak RSS 100,929,536 bytes, reader 121,888,768 bytes; max reader
tick 0.00630s. This is a local synthetic test, not the new bot's measured target
capacity. Public collection on the GitHub runner: 492 requests, peak process RSS
253,681,664 bytes for one bounded cycle. The workflow artifact also contains CPU,
wall time, API bytes/statuses and census. Neither measurement certifies UpCloud.

Exact published code-candidate results:

| Workflow | Run | Result |
|---|---|---|
| Full matrix/hash/isolated runtime/compile/shell/systemd/scale (push) | [35163276784](https://github.com/heroo2123/Alpha/actions/runs/35163276784) | SUCCESS |
| Same gates on the proposed PR merge | [35163281712](https://github.com/heroo2123/Alpha/actions/runs/35163281712) | SUCCESS |
| Public finalized exchange/fee contract probe | [35163276772](https://github.com/heroo2123/Alpha/actions/runs/35163276772) | SUCCESS |
| WRH/NWS/GEFS source acceptance | [35163276789](https://github.com/heroo2123/Alpha/actions/runs/35163276789) | SUCCESS |
| Current semantic census | [35163276798](https://github.com/heroo2123/Alpha/actions/runs/35163276798) | SUCCESS; partial strict subset |
| Bounded public collection measurement | [35163276775](https://github.com/heroo2123/Alpha/actions/runs/35163276775) | SUCCESS |

The census enumerated 20,960 active events: 314 weather-looking, 76 supported,
238 unsupported. Enumeration completion is not full semantic coverage. Unknown
templates stay skipped and counted; no parser was loosened to improve coverage.
These are timestamped observations, not a permanent market inventory.

Logs/XML, independent reports/reproductions, hash-install evidence and measurement
records are in [the evidence archive](review-evidence/operator-continuation-2026-09-16.zip).
Use PR #3 for checks on the documentation-inclusive head and actual main commit.

## Operator steps and remaining scope

1. Read [account/security onboarding](ACCOUNT_SECURITY_ONBOARDING.md) before
   creating or funding an account. Immediate supported onboarding is unfunded
   LIVE_SIGNALS. Restricted session trading still needs external approval and
   a verified adapter; full EOA custody requires a separate explicit choice.
2. Select the actual UpCloud configuration and arrange new-host SSH access.
   Follow the [unfunded commissioning package](UPCLOUD_UNFUNDED_COMMISSIONING.md),
   with independently provisioned host authority/first installation, exact
   release approval, source licensing and measured resource budgets. Do not use
   a repository bootstrap to approve its own code.
3. Privately provision only the authorized test bot and nonsecret policy first.
   Real test-bot messages need separate authorization. Verify all three custody
   boundaries with actual service identities when the executor is later added.
4. Stop before funded activation. Later select explicit financial limits,
   supported wallet/account and private credentials, then run account eligibility,
   allowance/balance/order/fill reconciliation and external config-bound approval.
   Claims are not available collateral. Owner redemption/revocation and recovery
   must work independently of Telegram and the bot.

Production Google/UpCloud services, databases, Telegram messages, private account
credentials and funded accounts were untouched. No resource purchase, wallet
creation/funding, actual financial sign/submit/cancel/redeem or deployment occurred.
The continuation adds no recurring paid dependency. Source licensing, any paid
data/RPC integration and actual infrastructure costs remain explicit decisions.
