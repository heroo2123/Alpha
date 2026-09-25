# V11 markout evidence and remaining acceptance

R05/R35/R42 remain PARTIAL. The implemented nonfinancial path connects archived
maker quotes/books to scoped quality measurement, reviewed safety reduction,
finite candidate quote retirement and durable audits. It is not fill evidence,
actual source acceptance, calibrated payout skill, deployment or trading approval.

## Populations and units

| Population | Existing evidence/metric | Boundary |
|---|---|---|
| Decision counterfactual | measurement.measure_markout: first received exact-token horizon book and full visible depth | A decision is not a fill; unknown fees/depth remain unknown |
| Maker research quote | MakerResearch.markout plus MakerTelemetryWorker: exact original quote, 1/5/30/120/600-second horizons, first received matching book | Hypothetical entry at quoted price and declared cost; no fill probability, queue position, actual survival or income inference |
| Scoped maker quality | markout_drift.measure_markout_window: original admission/rule/model scope, declared BUY/SELL, class, horizon and rolling window | Mean and negative-quote fraction describe retained counterfactuals, not empirical adverse-selection or live execution |
| Reconciled PAPER positions | PerformanceLab: entry-attributed all-in basis/proceeds and realized P&L | Separate fill-based horizon marks, actual fill price/fees and matched EV capture remain unfinished |

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

## Exact remaining execution join

Existing synthetic PAPER_FILL proofs retain units, all_in_collateral, direction,
intent/token/account identities and source receipt/observation timestamps. That
all-in amount does not separately establish raw fill price, fees or slippage.
Source observation time must not silently become an independently attested exchange
fill time. Older unsupported records must stay UNKNOWN; preserve their hashes.

Next, define and validate explicit synthetic PAPER fill timing/cost evidence, then
connect reconciled proof -> original single-leg/basket/exit valuation/admission ->
exact horizon book -> PerformanceLab and reviewed monitoring. Preserve basket joint
EV, partial-fill units and original entry/exit attribution without duplicate P&L.
Keep the evidence class separate from maker hypothetical entries. Matched EV capture,
settlement/finality and source residuals need their own target-aligned evidence;
none can be inferred from an unrelated positive markout or realized-only P&L.
Real source/calibration/forward execution, independent review and isolated host/
unfunded acceptance remain required. No owner action is needed to implement the
next bounded off-host join. No funding, order or deployment permission is implied.
