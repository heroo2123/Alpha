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
loss feed the same cancellation records. Explicit archived fill/terminal proofs can now be delivered by the optional receipt
worker through the existing account API; terminal state is never inferred by this scheduler. Maker quotes can be
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

## Typed candidate assembly

`candidate_assembly.assemble_candidate(store, client, plan, generation=...)`
constructs the shared finite runner from typed policies, explicit event routes,
source channels, protected scope/bundle bindings and bounded lane targets.
Construction neither collects nor writes evidence. Callers own the anonymous
HTTP client; the runner keeps its existing finite execution and recovery rules.
Authenticated clients and existing account/queue/runtime configuration conflicts
are rejected without replacement. The ordinary host clock probe stays enabled.

Temperature, relative/structural, PWS, received-release and ordinary exit lanes
use current-input factories. Event plans share a common risk binding; observation
and payout model scopes may remain distinct for PWS. No discovery result becomes
a reviewed route automatically. Unconfigured special exit joins are rejected.
The integrated HTTP-mock run and individual real lane pipelines passed **105
related tests in 23.70 s**, with **17 new builder cases**. Test-only clock/model
custody is not a deployment or empirical acceptance result. Maker/finality
assembly and remaining source/acceptance requirements stay open.

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

## Scoped drift reduction in the finite candidate

`CandidatePlan.drift` optionally supplies up to 16 exact DriftPlans whose scope and
bundle match configured inputs. CandidateRunner's DRIFT job shares the coordinator
and existing safety cadence. It processes one explicitly queued complete-label cohort
(maximum 64 captures); no implicit source universe, historical label backfill or
threshold default is inferred. Completed requests replay without renewing work;
pending cohorts are retained across interruption and a busy slot rejects new work.

The separate read-only review path is `/etc/alpha-v11/approvals/drift-policies.json`.
Its schema is `alpha_v11_drift_reviews_v1` with `reviews`. Each exact review supplies
plan_key, account_id, namespace, review_id, reviewer, approved_at, expires_at,
model_state_sha256, maximum_measurement_age_seconds, selection
EXPLICIT_CAPTURE_COHORT, action SAFETY_REDUCTION_ONLY and financial_authority false.
The plan digest covers all thresholds, window, cohort minimums and target/evidence
class; approval must predate the window. This implementation installs no reviews.
Capture epochs, current protected epoch and policy must match. Label revisions and
station changes are guarded at the safety write. Action is CALIBRATION_DEGRADED in
the existing scoped StationRegistry; there is no model-parameter change or automatic
restoration. Saved reduction recovery reports the original action without reapplying
it after an independent station review. The learner is not attached to this runtime.

Drift result counts and incident references join the versioned pinned audit layout.
This is cooperative off-host paper/shadow preparation, not an isolated guardian or
operational quality/threshold acceptance. Scalar calibration error and profitability/
source-residual metric reductions are still gated; actual observed evidence and
independent policy/label/host reviews remain required for acceptance.

## Explicit calibration and realized-paper monitoring

The optional `CalibrationDriftPolicy` preserves existing default scorer and DriftPolicy
identities. Its mandatory method CITY_DAY_EVENT_SNAPSHOT_BUCKET_EQUAL_WIDTH_10_V1 and
maximum_calibration_error bind ten fixed bins, equal city-day/event/snapshot weighting,
and equal shares for each vector's buckets. The resulting ECE is descriptive and
bin-dependent, without a confidence or calibration-acceptance claim. Existing reviews
cannot authorize the added threshold.

`RealizedDriftPolicy` uses SCOPED_REALIZED_PAPER_COLLATERAL_V1 with explicit decimal
maximum_realized_loss/maximum_realized_drawdown, window and cohort minimums. It accepts
only SYNTHETIC paper accounting evidence. Its review selection must be
ALL_RECONCILED_ACCOUNT_REALIZATIONS_IN_WINDOW_FOR_SCOPE. The usual protected review
schema, pre-window approval, current original model epoch and expiry checks still apply.

The finite worker automatically pins one current account snapshot when retained
realized history changes. It measures every realized fragment in the declared half-open
window from its original entry admission/model, verifies fill proofs and account/event
conservation, and never substitutes exit attribution. Repeated fragments remain one
event/city-day. Missing/unknown lineage gates the result. Unchanged history does not
retrigger a completed action; explicit request replay also does not renew work. An
account race before reduction gates that measurement and permits a new exact snapshot.
Pending requests/measurements/actions survive interruption without rewriting old records.

One prediction-quality and one realized-paper plan may share the same exact scope;
all configured plans still share one bounded pending slot and common account. A reviewed
realized loss breach records DISABLED without changing model parameters, pointers or
permissions. Existing reducing exits/inventory are retained and the ordinary admission/
cancellation path remains authoritative. Audits include durable drift outcome counts.
Realized-only drawdown is not mark-to-market drawdown; these statistics do not establish
live execution, matched EV capture, settlement truth or source residual bias. The
independent guardian and actual host/operational acceptance remain uncommissioned.

## Horizon-specific maker quality monitoring

A `MarkoutDriftPolicy` in `CandidatePlan.drift` requires the candidate's shared
maker telemetry and matching explicit cost/tolerance assumptions. One policy
covers one horizon, direction, evidence class and original MAKER_RESEARCH scope/
bundle. Policies for different horizons share fair round-robin scheduling with
realized-paper plans; the total remains at most 16 plans. The optional-free worker
and candidate configuration identities are unchanged. Changing a configured plan
still requires review and cannot replace saved worker history.

The read-only selection includes all matured retained quotes in a half-open
horizon-target window, at a pinned receipt boundary. It is bounded to 128 quotes,
64 events, two-second selection/measurement views, existing view byte limits and
a 512 KiB result. Unknown observations remain explicit; a partial observed subset
cannot authorize reduction. Mean counterfactual price quality weights city-days,
events and quotes equally within each level. Actual fills, payout calibration,
real P&L, venue fee attestation and net EV capture are not inferred.

Protected reviews use selection
`ALL_RETAINED_MATURED_MAKER_QUOTES_IN_WINDOW_FOR_SCOPE`, the exact plan key and
original current model state. Review must precede the window and remain fresh;
only safety reduction is allowed. Quote and event-measurement heads are guarded
atomically; changed evidence gates action and may trigger a new snapshot. Completed
requests never renew, a successful measure never restores authority, and model
parameters/pointers are untouched. Existing paper safety ticks retire quotes.

Daily/weekly reports retain separate bounded summaries by scope, direction and
horizon, including unknowns and exact measurement references. They never average
across horizons or claim full universe coverage. More than 32 summaries sets
metadata overflow and incomplete semantic coverage. The additive optional field
preserves existing completed reports and in-progress aggregate identity. Evidence:
`tests/test_v11_markout_drift.py`; 276 affected checks plus final 47 cases passed.


## Archived PAPER receipt reconciliation

`CandidatePlan.reconciliation=ReconciliationPolicy(...)` binds a
`PaperReconciliation` worker to the exact candidate coordinator. Construction does
not write or deploy anything. The configured coordinator rejects new reservations
and SUBMITTING until the first successful journal reconciliation. Once activated,
its durable journal also fences other coordinator instances sharing that account.
Historical plans without this field keep their original assembly/runtime hashes.

Each priority tick gives receipts at most a quarter of its cooperative time budget
(and the worker's own <=2-second policy), before cancellation/evaluation. Scans use
archive sequence, at most 64 rows and 2 MiB, with a SQLite read deadline; pending
receipts have a <=64-entry bound and round-robin retries. SQLite/account writes are
not preemptible; this is not independent wall-time/host guardian acceptance. The
worker lock is nonblocking, no-follow and regular-file checked. A full pending map
retains the first unrecordable failure behind the scan cursor and gates admission.

TRADE records with account/intent/namespace markers or PAPER_* type are classified
separately from public trades, including malformed envelopes. Valid explicit foreign
identities are retained/classified; missing identities, wrong classes/targets,
unknown types and incomplete terminal reconciliation remain pending. Deterministic
receipt-derived coordinator command IDs make restart after an account commit
idempotent. Only the existing synthetic proof, all-in cost, inventory, cumulative
fill quantity and final terminal authority checks may mutate the paper ledger.
No public print or maker quote becomes a fill. Data is never relabeled as actual
venue execution, and received proofs do not certify a simulation or production engine.

A ready journal must have no pending receipts and reach the exact receipt frontier.
New reservations/submission guard its head plus the account/source/health heads and
that frontier inside the same SQLite account transaction. New arrivals, concurrent
activation and account/journal races invalidate the old admission. Cancellation and
known-fill reconciliation do not require new-risk health approval; clock regression
still blocks ordinary financial-state/journal writes. Receipt worker errors retain
cancellation service. Audits expose delivery attempts separately from unique fills.
The five-horizon measurement/candidate test includes archive-only input, scoped
reduction, cancellation, later terminal receipts and daily audit without direct
manual `record_fill`. Candidate assembly also shares its exact EventQueue with this worker. Each proven
account update requests a fresh census before new decisions, preserves existing
source-loss reasons, advances the generation and removes the old evaluation.
In-flight work cannot complete against pre-fill inventory. Separate deterministic
queue IDs make recovery before/after event delivery idempotent without new cash or
fake market prints. Unregistered/expired event routes are explicitly reported.
The receipt remains pending if event delivery fails; success covers both the
account and queue. The existing inventory-aware exit runtime is verified from
archived BUY receipt through fresh sources and a SELL reservation to an archived
SELL receipt, realized PAPER accounting and another fresh evaluation.
