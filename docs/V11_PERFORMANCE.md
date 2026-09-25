# V11 performance and durable audits

Off-host reporting is implemented in performance.py and audit_reports.py. R40/R41
remain PARTIAL: emitted reports explicitly distinguish calculated metrics from
missing empirical, production and independent-review evidence. No report grants
financial authority, changes risk limits, sends a message or validates funding.

## Account-derived performance

PerformanceLab reads one pinned common-account state. Realized sale fragments
retain their entry-lot P&L partition; exit-decision attribution is not credited a
second time. Strategy and station/city/price/event/model/horizon slices conserve
total P&L. Entry-price slices use allocated all-in basis per share. Missing entry
lineage stays UNKNOWN, even if its net P&L happens to be zero. Duplicate realization
IDs and nonconserving allocations are refused. Historical event/entry accounting
gaps are exposed rather than silently filled.

A flattened entry intent is counted only after terminal entry status, full actual
filled-unit liquidation, no remaining entry lots and complete lifecycle coverage
within the report window. Partial sale fragments and settlement-resolved trades
are separate populations. Pending/ambiguous cancellation cannot count as a no-fill.
Reports include realized-only drawdown, profit factor, averages/medians, tail losses
and winner/station exclusions with named sample units. Open-inventory mark-to-market
drawdown remains UNKNOWN without a valid executable valuation. Joint basket EV
is not multiplied by the number of legs. Unmatched entry/exit horizons do not
produce an EV-capture ratio. Initial paper capital is hypothetical, never validated
live capital. Fees/slippage remain unseparated when the ledger has only all-in cost.

## Scheduled generation without blocking the safety loop

PaperRuntime schedules at most two small durable requests per tick: the previous
complete UTC day and ISO Monday-to-Monday week. Repeated ticks do not duplicate a
window; missed windows and unknown initial history are explicit. Scheduling does
not scan the archive or run the reporter. AuditWorker.step is a separate callable
worker with its own lock; no worker process or service has been deployed.

A short read transaction pins the archive sequence boundary and account/health/
reward heads. Bounded pages exclude later appends. Each worker step retains its
cursor and compact aggregation; restart resumes the same view. Publishing a report
before a crash cannot duplicate it on recovery. Config/account changes fail closed.
Default step limits are 64 rows and a cooperative two-second scan budget, plus a
separate at-most-one-second lineage metadata budget when publishing. The default
20,000-row job cap emits explicitly partial coverage, not an acceptance pass.
Group/reference caps and malformed records are visible. Production resource/isolation
and capacity acceptance remain open.

The report separates PAPER and unobserved LIVE, realized/claimable/redeemed values,
current exposure/scenario risk, source/runtime/funnel records, scoped station
transitions, rule drift, model/learner records, rejection reasons, and markout
counts by horizon. Counts refer to records, not independent trades, cycles or
outage episodes. Current pinned exposure is not a historical window-end balance.
Local registry claims do not certify stations or attest the protected champion.
Maker income and quote caps remain separate; actual payment evidence is unverified.
Mixed-horizon markouts are not averaged. Missing labels, counterfactuals, ablations,
champion/challenger acceptance and host resource trends remain explicit gaps.

Reports are durable local records only. Optional external delivery has not been
activated. Existing ambiguous-message-delivery safeguards remain relevant if a
separately authorized transport is connected later.

## Verification

27 new tests cover accounting conservation, partial and complete lifecycles,
ambiguous no-fills, winner exclusions, unknown lineage, basket EV deduplication,
namespace boundaries, pinned reads, bounded coverage, daily/weekly scheduling,
restart after report publication and runtime/report lock independence. The initial
report run had 25 passes and one fixture construction failure (a nonexistent
PaperAccountPolicy version field); the fixture now changes account identity.
An added scoped-station regression prevents one strategy's state from overwriting
another. Final report/account/runtime/reward/evidence checks: 151 passed in 22.07 s.
The preceding default-runtime checks passed 28 tests in 4.42 s. Full repository regression passed 3,200 tests / four existing warnings in
211.18 s, peak child RSS 153,452 KiB. All fixtures are synthetic and off-host.

## Scoped quality integration update — 2026-09-25

Automatic entry-attributed realized-paper loss/drawdown now reaches the existing
reviewed drift worker, candidate safety and audits. Original entry fill/admission/
model lineage, immutable account windows, conserved partial exits, account CAS and
recovery are checked. Horizon-specific maker counterfactual quality also reaches
that worker and candidate retirement; reports keep bounded summaries by original
scope, horizon, direction and evidence class without mixing them with PAPER P&L.
Summary overflow makes semantic coverage incomplete. See docs/V11_MARKOUT.md.

These additions preserve the older reporting evidence above. Latest full regression:
**3922 passed, four existing warnings, 264.11 s**, with all 833 inputs unchanged;
manifest/output: docs/V11_MARKOUT_REGRESSION_EVIDENCE.md. The preceding calibration/
P&L full run passed 3875 / 252.03 s. R40/R41/R42 remain PARTIAL. Actual fill-based
markouts, separately identified fill prices/fees, matched EV capture, empirical
calibration and independent/host/unfunded acceptance stay open.


## Reconciled fill quality update — 2026-09-25

The previous full regression above remains historical evidence. PerformanceLab now
joins reconciled PAPER fills to explicit synthetic execution details, original
single-leg/basket/exit decision scope and causal 1/5/30/120/600-second depth marks.
All retained proofs and intent quantities reconcile before cohort selection;
unknown timing/cost/depth stays visible and gates reduction. Partial fills do not
inflate independent intent counts; basket EV and entry/exit P&L remain separate.
Automatic reviewed monitoring, candidate cancellation and bounded daily/weekly
summaries consume this path. See docs/V11_MARKOUT.md for the metric and its limits.
Legacy reports/proofs are preserved. This does not certify live prices/fees,
empirical adverse selection, net-EV capture, finality or operational acceptance.
R40/R41/R42 remain PARTIAL; progress is unchanged at 82/200, approximately 41%.


Latest complete verification: **3981 passed / four existing warnings / 277.46 s**,
all **840 inputs unchanged**, implementation **33d92731**. Final fill-specific
suite **59 / 10.17 s**. Exact trees, manifests, earlier failure/fix and outputs:
docs/V11_FILL_REGRESSION_EVIDENCE.md. All acceptance limitations above remain.
