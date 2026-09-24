# V11 maker research quote lifecycle

Implemented off-host in v11/maker_research.py. R34 and R35 remain PARTIAL.
This records non-executing proposed quotes and observations, not managed exchange
orders, hypothetical fills or an approved first-canary policy.

## Admission and shared risk

A proposal binds exact contract/token/side, rule, code/config/model bundle,
protected MAKER_RESEARCH capability/model/source admission, current event/operator
state, current reproduced book features, price, size, declared cost reserve and
expiry. Crossing the current opposing best quote fails the post-only boundary.
Unknown models/permissions cannot be supplied as caller probabilities or local
claims. Tests supply explicitly synthetic protected-review/model fixtures;
production authority has not been provisioned or accepted.

The module reads the existing common paper account and projects adverse optional
fills of all observing research quotes alongside actual paper inventory and all
unresolved paper intents. It reuses the coordinator's cash, inventory, scenario,
correlation, daily-loss, capital and intent limits. Uncertain orders retain risk;
sales cannot exceed held inventory; a hypothetical payout is not spendable cash.
The projection makes no account-ledger write or real cash/inventory reservation.
It is identified as a hypothetical overlay with declared research cost assumptions,
not verified venue fees or executable maker EV. It cannot be submitted as an
economically admitted coordinator proposal.

Separate bounded research state enforces unique quote/thesis/token exposure,
policy identity and retention. Default values are caller-supplied reviewed research
policy, not coefficients taken from the reference paper. Absolute implementation
ceilings: 120-second quote lifetime, 30-second feature age, 16 simultaneously
observing quotes, 128 retained quotes and the existing account/position ceilings.
The earliest source/event/admission/quote expiry controls eligibility. Account,
source, event, operator and research-head CAS preserve concurrent updates.

## Sampling, withdrawal and markout

Only distinct linked book updates advance a sampled eligibility span. Missing
sequence links, reconnects and polling gaps reset the span. Repeated observations
of the same book do not become longer proven survival. These are sampled research
points; a hypothetical quote never has independently observed exchange survival,
queue priority or a confirmed fill.

Expiry, operator/event restrictions, source revisions, stale/invalid features,
crossing prices and failed common-account risk checks retire the research quote.
Failed risk checks do not advance the eligible span. Retirement makes no claim
that an order was canceled, that inventory was sold or that cash was released.
Retired quotes cannot silently resume. Replay preserves the original record and
clock; a changed request needs a new ID. Public trade-through remains public data.

Markouts at 1, 5, 30, 120 and 600 seconds take the first received exact-channel book
inside the declared horizon/tolerance. No favorable later replacement is selected
when that first book is stale, unhealthy, mismatched, historical-availability
unknown or insufficient. Both book receipt and observation must be causal.
At most 2,000 archived books are scanned; incomplete bounded scans remain unknown.
Missing fees remain unknown. BUY marks use visible exit bids; SELL marks use
visible replacement asks. Entry prices/cost reserves are explicit hypothetical
assumptions. All resulting marks remain counterfactual, with actual trading P&L
unset. Lifecycle expiry does not prevent later historical markout measurement.

## Verification and remaining acceptance

### Protected payout and release context

v11/maker_context.py is joined through MakerResearch.context. A current observing
quote binds its latest research head, protected maker admission, current reproduced
book, shared account risk, event/operator heads and a protected payout bundle.
Whole-event inference supplies exact YES/NO point and conservative interval
distances from the quote and sorted best-book midpoint. Uncalibrated bounds remain
vacuous. Point distance does not become maker EV, a confirmed payout, an executable
early exit, a fill probability or order authority.

Future-date context accepts complete final-extreme model inputs leased by the
protected admission. Same-day context additionally requires a separately reviewed
SAME_DAY_LATE_LOCK admission for the identical rule/context/bundle, exact accepted
official revision and complete remaining-day coverage. It reuses the existing
conditioned inference path; a maker-only capability does not silently certify an
observation proxy or remaining-extreme model. Next-observation inputs cannot be
relabelled as final contract payout inputs.

An optional archived FEATURES record with schema alpha_v11_received_release_notice_v1
provides station/rule, release_kind, expected_release_at and valid_until. Provider,
source identity, issue/receipt/availability times and revision come from its
evidence envelope. The current revision must be causal, unexpired and within the
declared bounded notice age. This is a received provider expectation, not official
source certification or proof of release. Missing/stale/malformed/superseded or
historically unavailable notices remain UNKNOWN. Passing the expected time yields
EXPECTED_TIME_PASSED_UNCONFIRMED, never an invented observation or zero event risk.
Source adapters have not yet been commissioned to populate these notices.

All inputs are bounded; at most 16 leased models are used. Started work preserves
its original cutoff across interruption. Completed replay cannot refresh its
clock or authority. Protected model revalidation and atomic book/source/account/
quote/operator head checks reject concurrent changes. Published context expires
with its shortest feature/book/source/event/quote/local-day boundary. It writes
only separate research evidence, with no quote renewal, account mutation, wallet,
transport or order action.

37 new context tests and 266 related context/maker/microstructure/strategy/admission/
model/probability/account/event/evidence tests passed in 19.68 seconds. Early test
failures exposed duplicate start references and fixture mistakes; these were
corrected. Review added sorted-midpoint, shortest-expiry, partial-replay and
concurrent-change regression cases. No failing case remains. All fixtures are
explicitly synthetic, including protected reviews; this is not independent review
or empirical validation. Prior quote-lifecycle verification follows.

32 new synthetic tests and 151 related maker/microstructure/account/admission/
event/evidence tests passed in 8.29 seconds. The initial 28-test run passed;
additional common-account cases passed in a 150-test related run. Review then
found that failed risk revalidation could advance sampled eligibility before
withdrawal; the correction and new negative case passed the final related run.
No failed test remains open. Exact final run duration is also in the checkpoint.

Remaining: provider/runtime scheduling and actual release-notice adapters,
prospective observation completeness, the separately
reviewed first-canary execution baseline, actual cancellation/telemetry/guardian
acceptance and genuine canary data. Fill/intensity/adverse-selection models stay
unknown until supported. Prior real fills are not a prerequisite for the first
separately approved bounded canary. Broader learned authority/scaling still needs
prospective support, genuine canary evidence and out-of-sample validation.
Rewards/rebates are separate from trading alpha; no funding or deployment is
authorized. V10 remains untouched and its resource/health finding stays open.

## Bounded maker telemetry integration

`MakerTelemetryWorker` is an optional CandidateRunner job, composed by
`MakerTelemetryPlan`. It shares the runtime's MakerResearch, health and common
account. Existing retained quotes receive exact current-book observations and
local invalidation/expiry retirement. Gap/reconnect samples reset eligibility
spans; public trades are not treated as fills. This worker does not collect trade
prints, create quotes, reserve cash or mutate inventory.

Each reserved action survives interruption with its original identity. Retained
quotes rotate under count/time bounds. Due markouts precede fresh samples, so
continuous book updates cannot starve them. A mark is finalized after its own
1s/5s/30s/2m/10m tolerance window closes, using the first eligible archived book.
Missing data/costs remain UNKNOWN; configured costs remain declared research
inputs. Retired history still receives due marks. No arbitrary paper waiting
period or financial eligibility is introduced. Maker retirement still runs before
optional runtime work. The shared process is not an independent guardian.

**206 related tests passed in 32.46 s**, including **14 new worker/candidate
cases**. Quote origination/context factories, actual provider/trade inputs,
reviewed first-canary baseline and empirical/independent acceptance remain open.

## Current maker quote and context assembly

`MakerLane` and `MakerRequestFactory` now originate bounded research quotes from
current held-claim source/book/risk/admission inputs. `MakerEventAdapter` uses the
same MakerResearch as runtime retirement and telemetry, publishes event-linked
queue evidence and returns no economic account proposal. Conditional same-day
context retains its separately reviewed payout capability, exact population and
remaining-day model inputs. A GATED context is reported explicitly. Raw members
and point distances do not become calibrated maker EV or executable exits.

The factory enforces exact declared terms and configuration identity; the existing
research/common-risk engine still rejects crossing quotes, excessive inventory
and duplicate observing tokens. A later claim cannot silently renew the original
quote's expiry. Research quote and context child records survive interruption.
Source models, clock custody and risk metrics in these tests remain fixtures.
The nine new factory/adapter tests passed in 3.19 s. Final integrated regression
is recorded in V11_WORK_CHECKPOINT.md. Public trade collection, empirical models,
reviewed first-canary baseline and formal/independent acceptance remain open.
