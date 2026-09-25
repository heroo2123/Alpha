# V11 markout evidence and remaining acceptance

R05/R35/R42 remain PARTIAL. The implemented nonfinancial path connects archived
maker quotes/books to scoped quality measurement, reviewed safety reduction,
finite candidate quote retirement and durable audits. Reconciled synthetic PAPER
fills now have a separate depth-markout path. Neither path is actual venue fill evidence,
actual source acceptance, calibrated payout skill, deployment or trading approval.

## Populations and units

| Population | Existing evidence/metric | Boundary |
|---|---|---|
| Decision counterfactual | measurement.measure_markout: first received exact-token horizon book and full visible depth | A decision is not a fill; unknown fees/depth remain unknown |
| Maker research quote | MakerResearch.markout plus MakerTelemetryWorker: exact original quote, 1/5/30/120/600-second horizons, first received matching book | Hypothetical entry at quoted price and declared cost; no fill probability, queue position, actual survival or income inference |
| Scoped maker quality | markout_drift.measure_markout_window: original admission/rule/model scope, declared BUY/SELL, class, horizon and rolling window | Mean and negative-quote fraction describe retained counterfactuals, not empirical adverse-selection or live execution |
| Reconciled PAPER positions | PerformanceLab.scoped_fill_markouts: explicit synthetic timing/price/cost, original decision/admission, first horizon depth; separately retained realized P&L | PAPER execution details do not attest venue fills; legacy unknowns remain; matched EV capture and finality stay open |

## Selection, provenance and calculation

MarkoutDriftPolicy declares the horizon, direction, evidence class, window,
tolerance, fee assumption, cohort minimums, metric definition and adverse-mean
threshold. There is no implicit production threshold. Telemetry assumptions must
match. Every matured retained quote in the original MAKER_RESEARCH scope/bundle is
included when its horizon target lies in the half-open window and its tolerance
window has closed. Retired quotes remain eligible. Missing/unpublished books and
unknown fee/depth observations stay in the denominator and gate reduction; observed
subsets are visibly partial. Retained-window coverage does not establish full
market-universe coverage.

Read-only snapshots pin receipt sequence, quote and measurement heads. Original
quote/admission/hash/time/rule/model references and normalized-source derivations
are checked. A measured value must reproduce the first received horizon book,
exact token/collateral, causal availability, healthy uncrossed book and full depth.
Later better books cannot replace the first. New receipts cannot fill holes in an
interrupted snapshot. Bounds are 128 retained quotes, 64 events, existing read-view
record/byte limits, two-second cooperative views and 512 KiB results; bounds gate
rather than truncate. These are limits, not verified host capacity guarantees.

For BUY quotes, counterfactual per-share quality is full-depth net bid value per
share minus quoted entry price and declared entry cost reserve. For SELL quotes,
it is quoted sale price minus declared entry reserve and full-depth ask acquisition
cost per share. Horizon fee assumptions are explicit research costs, not verified
venue fees. The fixed mean gives equal weight to city-days, then events within
city-days, then quotes within events. Counts, signs and negative-quote fractions
are descriptive; no sample independence or confidence interval is asserted.
Different horizons/directions/evidence classes are never pooled.

## Candidate action and reporting

The typed candidate shares its MakerTelemetryWorker with DriftWorker. New immutable
cohorts trigger work automatically. Round-robin scope selection includes realized-
paper monitoring so busy short horizons do not starve other configured scopes.
One active request is preserved; repeated commands replay without renewal.

Only an exact protected pre-window policy review, fresh measurement and matching
original current model epoch permit a scope reduction to DISABLED. Pinned heads
are checked and guarded atomically. Changed evidence gates the old action; a new
snapshot may retry. Saved measurement/review/reduction recovery is idempotent.
A healthy measure cannot restore scope authority or mutate model parameters.
Existing runtime safety ticks retire affected research quotes; no order is sent,
account cash is unchanged, and the independent guardian requirement remains open.

Daily/weekly audits retain separate original-scope/horizon summaries, raw horizon
status counts and reduction outcomes. The optional summaries preserve completed
reports and prior aggregate identities. More than 32 summaries sets metadata
overflow and incomplete semantic coverage; no cross-horizon average is emitted.
See docs/V11_RUNTIME.md and docs/V11_MARKOUT_REGRESSION_EVIDENCE.md.

## Reconciled PAPER fill integration — 2026-09-25

fill_evidence validates additive execution_details on synthetic PAPER_FILL proofs:
explicit engine execution time, raw price, fees, other cost, collateral and exact
signal/post-validation book hashes. Raw price times units plus BUY costs (minus
SELL costs) must conserve the existing all-in ledger amount. Original single-leg,
basket-leg or exit valuation and receipt chronology are checked. A source's
observed_at alone never becomes an attested execution time. Invalid optional
metadata gates metrics while the valid original proof still reconciles units and
cash. Legacy proof/result hashes are unchanged and unsupported fields stay UNKNOWN.

FillMarkoutPolicy declares one horizon, direction and source evidence class.
PerformanceLab.scoped_fill_markouts pins the account and receipt sequence, checks
all retained fill proofs against intent quantities, and reproduces the complete
original-scope/bundle cohort. Unknown timing is included whenever the interval
from original valuation to proof receipt could overlap the matured window. It is
never selected away based on an invalid timestamp. Every unknown member gates
reduction. First received exact-provider/token horizon books, causal availability,
healthy uncrossed full depth, explicit future cost and normalized raw provenance
are checked. Snapshot recovery cannot use later receipts to fill missing evidence.

BUY marks equal hypothetical net bid liquidation per share minus reconciled all-in
acquisition basis. SELL marks equal reconciled all-in proceeds per share minus
hypothetical net ask reacquisition cost. Raw-price marks are reported separately.
These are PAPER-fill-to-hypothetical-depth measures, not realized P&L. Original
strategy/model/decision attribution is retained; joint basket EV is not assigned
to each leg. Partial fills are quantity weighted within an intent, then intents,
events and city-days are equally weighted at their respective levels. Signs and
negative-intent fractions are descriptive; empirical adverse-selection rate,
confidence intervals, independent samples and matched EV capture remain unknown.

Bounds are 2048 retained proofs, 512 intents/selected fill rows, 64 events, existing
8 MiB/8192-record read limits, two-second views and 512 KiB results. Overflow gates
rather than truncates. These bounds are not production capacity acceptance.

The existing DriftWorker automatically selects new complete fill cohorts fairly
alongside realized-paper and maker-counterfactual plans. Original model epoch and
protected pre-window policy review are still required for a scope reduction.
Account and book heads are atomically guarded; saved measurements/reductions
recover without renewal. Candidate safety ticks cancel the affected opening
intents while preserving cash, positions and reconciliation. Audits retain a
separate bounded fill_markout_monitoring summary, without averaging across horizons
or mixing synthetic execution with maker quote counterfactuals or live labels.

Remaining: archived PAPER proof delivery into candidate reconciliation still
requires its own bounded runtime integration (the new measurement consumes already
reconciled proofs). The candidate does not invent fills. Matched EV capture,
settlement/finality, source residuals, actual-source/calibration/forward execution,
independent review and isolated host/unfunded acceptance remain required. No owner
action is needed for the next off-host integration. No financial or deployment
authority is added. Targeted verification and exact checkpoints are recorded in
docs/V11_WORK_CHECKPOINT.md.


Latest complete verification: **3981 passed / four existing warnings / 277.46 s**,
all **840 inputs unchanged**, implementation **33d92731**. Final fill-specific
suite **59 / 10.17 s**. Exact trees, manifests, earlier failure/fix and outputs:
docs/V11_FILL_REGRESSION_EVIDENCE.md. All acceptance limitations above remain.
