# V11 executable economics

`v11/valuation.py` binds settlement valuation to the exact event, rule, market,
condition, token, side, collateral and model bundle. A next-observation prediction
cannot supply final payout or executable early-exit value. Current probability
artifacts have vacuous bounds; relabeling those as narrow confidence is rejected.
Consequently they cannot justify a positive settlement entry EV. A validated
repricing model is not implemented and that path stays explicitly gated.

Acquisitions walk current ask depth; sales walk bid depth for the requested size.
Stale, unsynchronized, crossed, incomplete or wrong-target books cannot produce a
full-size executable estimate. Requested size above the supplied policy cap is
skipped, not silently reduced. Quotes remain estimates, never fills.

All values use collateral per share and a declared final-payout or current-sale
horizon. Spread and depth walk are included in the executable price. Model bounds
own model uncertainty. Every additional fee/reserve has named, nonoverlapping
risk coverage; unknown/missing coverage gates economics. Explicit zero is a
declared assumption, not evidence of zero venue cost. Revision, fallback,
post-snapshot slippage, execution, redemption and opportunity costs remain visible.
Changing the bound coverage contract requires an explicit implementation/review.

The BUY fee adapter reuses `production/fees.py`, checks exact token/condition and
freshness, and pins its priced BUY limit. A post-only fee cannot price an immediate
acquisition. The published schedule remains conditional, not a signed ceiling;
actual fills must reconcile actual fees. No SELL fee formula is inferred. Fee
snapshot capture, protected production policy and final executor integration remain
pending. Declared assumptions cannot independently prove source authority.

Hold-versus-sale comparison excludes sunk entry cost from the prospective choice
but retains allocated all-in basis in hypothetical lifetime P&L. It requires enough
held units, preserves residual units and reports no actual inventory change or
realized P&L. Actual inventory authority, durable exit intents, partial fills and
reconciliation remain coordinator/ledger work. An opposite-token purchase or
cancellation is not a sale.

Rewards are separate and unknown rewards contribute zero spendable cash. No
reward-aware maker admission is implemented. 26 focused valuation tests and the
existing fee-policy regression pass; these are synthetic code checks, not venue
execution evidence, calibrated strategy eligibility or financial approval.
