# V11 inventory-bound active exit research

Implemented in v11/position_management.py, v11/position_attribution.py and the
shared paper coordinator. These modules have no network order interface and
cannot open live/control ledgers. Synthetic fills are accounting tests, not live
execution, profitability or independent review evidence.

## Decision and reservation

An ExitRequest selects actual held inventory, a scoped protected model admission,
a fresh event state and exact bid book, explicit future hold/sale cost coverage,
size, expiry and a reason category. Whole-day or exact observed/remaining-day
inference is reproduced from archived leased inputs and the protected bundle.
Next-observation probability is not a payout or executable exit price. Current
bounds remain vacuous and no calibrated champion is claimed.

The decision uses the exact event's joint payout partition. For the requested
reduction it compares the minimum prospective wealth before sale with net sale
proceeds at the worst permitted bid plus the residual portfolio payout floor.
Only declared future costs avoided by this sale enter the prospective choice.
Entry costs are sunk for that comparison and retained in lifetime P&L. Other
positions' unchanged future costs cancel; their guaranteed joint payout does not.
This prevents a cheap sale of one complete-set hedge based only on its individual
zero lower bound. Displayed average depth cannot qualify an exit whose permitted
limit fails the EV threshold. This is a conservative max-min research objective,
not a claim about calibrated expected profit or certain execution.

Unknown fees, unhealthy/stale/noncausal books, insufficient depth or inventory,
changed rules, model/source demotion and expired pins gate the decision. Any
unresolved intent in the same event must first be reconciled; cancel requests do
not remove it. The account verifies all holdings, including positions attributed
to other sleeves. Two same-event exit candidates cannot independently spend the
same hedge in one batch. All common cash/inventory/scenario/operator/queue limits
still apply. Existing bare single-token EXIT_COMPARISON records cannot bypass
this new inventory and protected-inference gate.

Evaluations retain their original cutoff and account lineage across interruption.
Historical replay never refreshes expiry. A queue claim can evaluate an exit;
only its completed exact result can subsequently pass installed-queue admission.
Reservation and submission both reproduce valuation against current inventory and
model/source pins. Account and source-head CAS guards close concurrent changes.
Partial fills, unknown submissions, sticky cancel requests and explicit terminal
proof remain handled by the common coordinator. No cancel invents a sale or cash.

## Entry, exit and realized accounting

Every new paper acquisition retains its entry intent, thesis/reason, valuation,
EV units, release binding and partitioned sleeve attribution. Joint basket EV is
explicitly a group value, never independent per-leg alpha. Acquisition evidence
sequence determines FIFO order; canonical JSON key order cannot change it.
Historical lots lacking sequence or lineage remain explicitly unknown and are
not assigned invented source history.

A sale retains its exit reason/EV and the exact consumed entry-lot portions. Basis,
net proceeds and realized P&L allocations conserve totals through fractional fills
and rounding. Realized sleeve P&L follows the entry ownership partition; exit
sleeve attribution is decision metadata, not an additional economic fill/P&L.
Hypothetical lifetime P&L uses the same actual lot-allocation policy as fills.

## Verification and limits

24 new tests cover joint YES/NO floors, complete-basket hedge preservation, limit
versus average depth, unknown fees/size/depth gates, protected-model reproduction,
source/book/operator/expiry changes, account races, ambiguous restart, interrupted
evaluation, exact queue completion, partial fills, cancellation, FIFO and fractional
P&L conservation. The joined exit/account/basket/queue suite passed 78 tests in
16.73 seconds. Full shared-account regression passed 2,894 tests with four existing FastAPI
deprecation warnings in 146.00 seconds.

The bounded engine evaluates a supplied reason category with independently
recomputed economics. It does not yet automatically detect every thesis change,
perform stronger-opportunity capital rotation, run a continuous provider scheduler
or commission emergency exit permissions when an entry scope is demoted. Such
faults currently gate orders while preserving inventory. Multi-leg simultaneous
basket exits, real fee authority, provider/runtime acceptance, protected reviews,
approved calibrated models, guardian integration and live reconciliation remain
open. R32 is PARTIAL, not deployment accepted. No V10 workload or service changes.
