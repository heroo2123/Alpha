# V11 event and correlated portfolio risk

`v11/scenario_risk.py` values each supported mutually exclusive resolving bucket
against held YES/NO inventory, all-in basis, realized event P&L and each unresolved
order remainder. Pending legs may fill independently in the adverse direction for
each outcome. An unfilled complete set therefore retains legging risk. Optional
future profitable fills cannot finance current losses. Selling part of a hedge can
increase risk and is not assumed to be a portfolio reduction.

Views retain every outcome's conservative P&L, favorable optional-fill result,
worst loss, best result, concentration and incremental proposed-order change.
Strategy weights partition one economic position; explanations never multiply a
fill or its P&L. Held and pending BUY units share the position ceiling; outstanding
SELL units cannot exceed held inventory. Quoted resolution payout is not cash.

Versioned station membership binds the exact metadata fingerprint and city,
region, weather, source and model groups. Legacy contracts without a city label
use this explicitly supplied metadata-bound mapping; no city is guessed from a
nearby station. Unknown dependence must use shared unresolved groups. The mapping
is declared research configuration pending protected review, not empirical proof.

Account aggregation sums event worst losses within each group and portfolio-wide.
Different city names earn no independence credit. Optimistic profit in another
event cannot offset downside. Per-event/city/region/weather/source/model/portfolio
ceilings are checked along with aggregate position quantities. Duplicate events,
tokens shared across events, mismatched account/namespaces and mutated views are
refused. LIVE and V10_CONTROL are not accepted development namespaces.

15 synthetic tests pass, including exact complements, partial baskets, hedge-breaking
sales, concurrent inventory demands, fee/basis accounting, shared dependencies,
metadata drift and duplicate attribution. Account reservations, authoritative fill
and terminal reconciliation, actual regional evidence and protected commissioning
remain separate requirements. No financial authority or deployment is enabled.
