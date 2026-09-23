# V11 paper coordinator and reservations

`v11/paper_coordinator.py` is the sole admission interface for its hypothetical
account journal. It has no order/network adapter, accepts only the existing
nonfinancial evidence namespaces and never opens a V10 or real execution ledger.
The supplied initial cash is simulated budget, not a wallet balance or funding.
`v11/allocation.py` supplies bounded deterministic ranking and reduction-only
sizing inside explicit ceilings; neither component changes protected live limits.

Every proposal names an exact account/city/station/event, rule, target total
position, economic thesis, attribution, valuation and event-state pin. The city
must match the metadata-bound correlation map. Unqualified/stale economics,
operator reductions, state-adjusted EV/size/liquidity, changed scopes and expired
evidence reject admission. Current uncalibrated payout estimates remain rejected.

Candidates rank by conservative EV per full capital at risk, then absolute EV
and stable identity. The coordinator evaluates account cash, inventory and
scenario/group limits after each choice and records every rejection. Conflicting
token proposals keep the stronger candidate. Existing held/reserved units count
toward desired total exposure; another strategy or thesis cannot duplicate them.
A smaller executable delta requires a fresh valuation rather than silently using
old size-dependent economics. Retained cash is an explicit policy setting.

One append-only account head serializes all city-days through SQLite's immediate
transaction and compare-and-swap. Admission also checks every operator scope head
(including absent scopes) and event-state head inside that transaction. A racing
halt or reservation invalidates the proposed update. A replayed batch retains its
original result; it does not reserve again. Policy/account/map/limit identity is
fixed once the first account event is recorded.

RESERVED, SUBMITTING, UNKNOWN, ACKNOWLEDGED, PARTIAL and CANCEL_REQUESTED keep
remaining reservations. Restart converts SUBMITTING to UNKNOWN. A timeout, local
expiry, cancellation request or late acknowledgement cannot release capital or
inventory, and ambiguous submission cannot be retried as a fresh submit action.
Actual network execution is absent, including for a recorded SUBMITTING state.

Explicit synthetic paper fills atomically debit/credit paper cash, retain all-in
basis, update actual paper lots and reserve remainders. Fill identity is deduped;
cost overruns are preserved and fault new admission. Partial sales retain residual
inventory and basis rounding residue; later completion consumes exact remaining
basis. Prospective exit choice and realized paper P&L stay distinct. Only an exact
paper terminal record with complete cumulative fill reconciliation releases the
unfilled remainder. Public trades and other namespaces cannot mutate the journal.

20 coordinator/allocation tests and 91 combined account/scenario/event/evidence
checks pass. They include concurrent writers and operator halt, cross-city cash,
partial fills, restart, fee breach, desired-position conflicts, repeated fractional
sales and immutable policy identity. Downstream accepted-entry fixtures are
explicitly synthetic: they do not establish positive EV for the current champion.

Remaining work includes authenticated production control, actual account-wide
external exposure, scoped strategy/certification/rule/model-authority admission,
strategy signal migration, original-entry/exit/realized attribution integration,
settlement/claim/redemption reconciliation, bounded long-running retention and
guardian/executor commissioning. Claims/rewards currently provide no spendable
cash. The account implementation is research-only and is not unfunded or live
acceptance. The V10 resource gate and executor mask remain unchanged.
