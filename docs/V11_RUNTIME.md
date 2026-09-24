# Bounded V11 paper runtime

paper_runtime.py joins the existing event queue, runtime health, common paper
account, temperature evaluator, cancellation delivery and maker retirement.
This is an off-host callable candidate, not an installed scanner or deployed
trading system. It has no order transport, wallet access, fill inference or
deployment authority. Public-book/AWC census integration is described in
V11_PUBLIC_BOOK_CENSUS.md; remaining source and request-assembly adapters stay explicit.

PaperRuntime.tick is bounded by source-update count, event count, cancellation
plan count and a cooperative monotonic time budget. One process lock serializes
ticks. Durable checkpoints retain operator/EVENT cursors, active cancellation
plans, round-robin progress and periodic-census deadlines. Replaying a completed
tick returns history without repeating work or refreshing health. Interrupted
claims require census through the existing queue. Partial work/evidence is retained.

Periodic census cannot be replaced by old cached books: the queue requires newly
archived full books for every event token plus required fresh source and current
rule evidence. Missing adapters remain GATED and receive bounded retry delays.
Book/source captures are not invented by the runtime. Evaluators run only under
the queue's serialized claim; results are finished and validated before proposals
reach the common coordinator. Source/queue/health changes invalidate downstream
admission. TemperatureEventAdapter uses the existing protected model and source
admission pipeline; missing strategy dependencies gate that sleeve independently.

Cancellation and maker safety retirement are serviced before evaluations. Existing pending plans get a bounded
round-robin service budget; separate bounded intake prevents ambiguous old cancels
from starving fresh safety requests. At most twice maximum_cancel_plans plus one health adapter
run per tick, each bounded to 16 local account requests. Cash remains reserved
through ambiguity. Account faults, unhealthy clock/worker state and required-source
loss feed the same cancellation records. A terminal proof is reconciled by the
existing account API, never inferred by this scheduler. Maker quotes can be
retired even during backward wall-clock movement and remain research-only.

Tests demonstrate periodic census -> causal source coverage -> real scoped
temperature admission -> protected synthetic model bundle -> conservative
valuation -> completed queue result. The uncalibrated model correctly produces
CONSERVATIVE_EV_NOT_ABOVE_THRESHOLD and creates no intent/fill. Separate synthetic
account fixtures exercise heartbeat/clock cancellation, restart, late fills and
terminal reconciliation; those mechanics are not evidence of trading alpha.

The first affected regression ran 117 passed / 2 failed: the two CAS race fixtures
intercepted the old audit method. Moving the race injection to safety_audit retained
their assertions; 142 related tests then passed in 8.55 s, including 23 new health
cases. Initial runtime integration ran 16 passed / 2 failed: one expected reason
omitted STREAM_GAP and the full-book fixture omitted exact contract target fields.
Both fixture defects were corrected. Final runtime/health/maker/cancellation/queue
suite: 115 passed in 12.42 s, including 45 new tests. No open test failure remains.

runtime_feed.py now provides durable round-robin delivery of normalized archived
receipts and scheduled release expectations. Each publication is idempotent and
checkpointed before cursor advancement. Bounded pending schedules stay distinct
from observations. Raw/unknown availability, unqualified PWS and synthetic account
fills are excluded from public-source delivery; failed publications remain retryable.

observation_pump.py joins existing bounded public collectors to safety ticks before
and after collection. Clock failure suppresses collection while cancellation remains
available. Provider cooldowns and partial successes survive. Interrupted cycles
retain evidence and are not automatically refetched. New process generations use
the common account's existing restart reconciliation, keeping ambiguous submissions
reserved. An exhausted cooperative budget is reported explicitly.

65 related tests passed in 5.76 s; two later focused account/EVENT cases passed.
Full repository regression: 3,130 passed / four existing warnings in 185.10 s,
peak child RSS 154,848 KiB. All tests ran off-host. No actual source, deployed clock,
calibration, independent guardian or financial acceptance is claimed. Remaining
provider/request adapters and empirical acceptance stay open. Subsequent reporting
and public-census integrations are recorded in V11_PERFORMANCE.md and
V11_PUBLIC_BOOK_CENSUS.md; those component milestones do not grant acceptance.

Public market discovery and semantic/rule cancellation are integrated through
`discovery.py`; see V11_MARKET_DISCOVERY.md. Health has one dedicated bounded
intake check, separate from rotating operator/EVENT/rule channels, so no-op health
checks and busy operator streams do not starve rule quarantine at a budget of one.
Maker retirement precedes queue claims and is retained when census work holds
the queue lock. Dynamic route registration and independent guardian remain open.

## Finite candidate runner

`CandidateRunner.run(run_id)` now composes existing `PaperRuntime`, `CensusWorker`,
`MarketDiscovery` and `AuditWorker`, plus an optional `ObservationPump` with a fixed
`ObservationBatch`. It requires the same V11 store, queue, health monitor, common
account, scheduled collector and report policy. The configured runtime worker
emits only its own heartbeat. Different worker identities/configurations cannot
silently reuse the runner's saved state. Protected station/model/rule admission
and the existing strategy request assembler remain mandatory.

A run has explicit wall-duration, job-count and safety-tick limits. There is one
collection/optional-work slot, rotated among census, discovery, audit and optional
observations. Existing provider cooldowns remain authoritative. Safety ticks
continue while the collection coroutine awaits HTTP. No thread, daemon, service,
financial transport or system signal is created by this runner. Audit chunks run
outside the runtime lock but in the same cooperative process; their cost can delay
a tick and is not an independent-guardian guarantee.

The runner reserves each work identity durably before dispatch. Completed run
replay returns history without refreshing health or repeating HTTP. An interrupted
coroutine is drained and its exact command retained for the worker's own causal
recovery rules. Resuming a completed discovery step cannot start another scan.
Raw captures, cooldowns, partial reports, queue census needs and account ambiguity
remain in their existing stores. Optional-worker failures are recorded without raw
exception messages and do not suppress unrelated work. No interrupted command is
claimed successful. Each report includes runtime outcome counts, actual maximum
safety-start gap, cooperative budget overrun and pending-recovery status. A bounded
run finishing is not evidence that its strategy, clock or deployment gates passed.

Busy census queue locks now produce explicit event-work deferral while preserving
completed cancellation/retirement telemetry and later report scheduling. Maker
safety checks rotate over observing quotes; retained retired history and an earlier
healthy quote cannot hide a later expiring quote at the minimum update budget.
The number of active maker quotes remains bounded by the existing maker policy.

Integrated tests run HTTP-shaped mock books/observations through the runner into
the real scoped protected temperature pipeline. Its uncalibrated valuation rejects
entry without an intent/fill. Another run completes semantic rejection census and
a durable audit; interruption tests retain original request identities and verify
automatic PAPER cancellation during blocked HTTP with cash still reserved.
These fixtures do not attest source availability, profitability, forward control,
independent review or deployed cancellation capability.

Remaining integration includes dynamic reviewed route/request assembly, exact
forecast issue-time and settlement-population adapters, PWS QC/model/source joins,
all strategy factories, independent guardian and isolated commissioning. The runner
introduces no launcher or V11 deployment on alpha-dev. Resource/isolation, current
clock and formal acceptance gates remain open; V10 maintenance is deferred.

## Relative-value and structural runtime paths

`strategy_runtime.py` adds `RelativeValueEventAdapter`, which invokes existing
whole-event valuation and retains each candidate's exact queue output/valuation
link. It feeds `BasketProposal` through the same runtime completion and common
coordinator already used for account-level ranking, conflicts and atomic basket
reservation. Its compact identity preserves all digest bits within the existing
strategy length limit; no downstream bounds were expanded.

`MultiStrategyEventAdapter` joins bounded temperature and relative-value request
adapters under one event claim. A missing source/request dependency records a
GATED lane without suppressing unrelated valid evaluation. Every returned output
must belong to that claimed event. Exceeding aggregate result/proposal bounds
gates the combined admission and retains child evidence; it does not trim basket
legs, favor an arbitrary first lane or claim an omitted suffix was evaluated.
Shared clock/source/queue/account gates still apply to every proposal.

Eight new integration cases; relative/basket/runtime/candidate/queue/admission/
protected-pipeline suite **125 passed in 25.93 s**. HTTP-shaped mock census feeds
real protected cross-temperature and structural engines, exact queue completion,
and atomic three-leg PAPER reservation. A missing temperature adapter remains
GATED alongside the qualified complete set. These positive cases use explicit
synthetic underround prices/costs and hypothetical cash; no empirical alpha, real
fill, fee, redemption or strategy eligibility is inferred. Unknown acquisition
fees or a post-evaluation stream gap leave the entire basket unreserved. Full
regression remains the 3293-case runner milestone; these eight later cases are
covered by the targeted integration run, not that earlier full run.

## PWS, source-release and inventory runtime paths

`reaction_runtime.py` connects three existing engines. PWSLeadEventAdapter takes
an unjoined entry, the separately protected observation admission, bounded paired
ablation inputs and a fixed lead policy. It resolves exact leased official/PWS
inputs and uses the same frozen observation bundle for both feature comparisons.
The preconfirmation join independently revalidates observation and payout scopes;
the temperature engine then recomputes settlement economics. The adapter creates
no calibrated model and has no repricing-price shortcut.

SourceReleaseEventAdapter binds the exact immediate official predecessor/current
receipt, post-release book, recomputed model and current event state. A schedule
is optional provenance, never actual release proof. The stronger EVENT and
ordinary liquidity/economic/common-account checks remain in force.

PositionExitEventAdapter uses the runtime's identical coordinator object. Each
request evaluates actual holdings and residual whole-event payout floor. New
census evidence requires a new admission pin; current book/source/model/expiry
and account CAS checks remain in the engine and common coordinator. A reservation
does not sell inventory or realize P&L. Runtime replay does not reserve twice.

Each adapter has at most six requests; MultiStrategyEventAdapter retains the
existing aggregate result/proposal bounds. Failures are archived per request,
and unrelated bounded lanes can proceed. All exact evaluation IDs pass through
the held queue claim before any common account operation. A later queue gap
prevents reservation. No source or model is refreshed by re-labeling old evidence.

Verification: **17 new cases**, **175 related tests passed in 57.55 s**. PWS and
release entries remain rejected by the real uncalibrated payout engine. Synthetic
inventory cases demonstrate one qualified SELL reservation, preservation of a
complete basket hedge, unchanged actual lots/cash without fills and replay safety.
Next work is bounded current source/request assembly and remaining route/provider
integration. Protected model/certification custody in tests is a fixture; actual
calibration, independent review, host isolation and runtime acceptance stay open.

## Current-input factories and tested recovery identity

`request_assembly.py` replaces supplied evidence-ID callbacks with configured
source selectors and exact current-token book lookup. ScopeInputs is a fixed
nonfinancial plan, not model/certification approval. StrategyAdmission still reads
protected scope/bundle state. Every request requires the actual held event claim,
completed census where needed, matching current risk/source/book inputs and an
unexpired decision. New sources require risk recomputation; a missing channel never
falls back to another provider or re-dates an older receipt. Receipt-order
predecessor lookup retains revision semantics without loading an entire history.

Temperature, relative-value, PWS, received-release and exit factories share this
assembly. Paired PWS admissions remain separate. Unknown costs still gate economics;
configured exit sizes are checked against actual held inventory downstream. Typed
factory plan hashes are bound into adapter/runtime recovery. Changed plans cannot
replay old runtime completion, and changes within an adapter require review.
Legacy callback seams remain for engineering fixtures and are not attested plans.

EventRiskEngine now interprets a qualified PWS summary through its as_of and
oldest contributing sensor age. No raw observation timestamp is invented. An old
sensor, bad QC, missing/future/negative age or unchanged reprocessed summary cannot
create freshness/recovery. Other missing risk inputs remain unknown.

Assembly/QC-time milestone: 26 new cases, **54 focused passes in 5.17 s**;
**full regression 3344 passed, four existing warnings, 203.31 s**, exit 0,
peak RSS 156820 KiB. Tests include finite public-mock census -> current-input
factory -> protected basket valuation -> common account, periodic census ->
factory-built held-inventory exit, per-lane missing-source isolation, invalid
plans, receipt ordering and changed-plan recovery rejection. No live fees, fills,
calibrated champion or deployment readiness are inferred. Next integration is
bounded risk derivation and remaining source/dynamic-route work.

## Derived risk before strategy evaluation

`risk_inputs.py` now composes bounded full-event books, current scoped model
inference and common-account downside with EventRiskEngine. It checks every token
within its 32-token cap and retains at most two same-channel book receipts per
token. Point spread is separate from temporal velocity/depth loss/cross-bucket
movement. Temporal metrics require aligned, explicitly linked sequence evidence;
a REST book cannot establish that continuity or transport commissioning.

Model disagreement is the protected bundle's descriptive between-model standard
deviation in contract temperature units, not calibration or independent sample
count. Model age uses issue time; a newer receipt or synthetic observed timestamp
cannot refresh it. Current account downside includes unresolved optional-fill
exposure and the fixed daily loss budget. Evidence/account heads are pinned at
measurement publication. An interrupted measurement without its state requires a
new claim; it is not refreshed in place.

RiskAwareEventAdapter runs the measurement/state step before ordinary factories
and economic evaluation. Missing execution markout/adverse-fill inputs and exact
settlement timing remain UNKNOWN and retain EVENT gates. No end-of-day timestamp
becomes a final settlement deadline. A reviewed first-canary execution baseline
and actual execution/settlement input adapters remain unfinished; this adapter
cannot substitute for them or impose a new paper waiting period.

Verification: **220 related tests passed in 43.03 s**, including **15 new cases**.
A finite public-mock run now joins fresh census, derived risk, current request
assembly, protected strategy evaluation and queue completion with no supplied
risk-metric callback. Missing empirical inputs correctly prevent reservation.
Tests also cover declared sequence deltas, missing/skewed/gapped books, source
races, unchanged account reservations, missing health and interrupted publication.
The latest full regression remains the preceding 3344-case assembly run.
