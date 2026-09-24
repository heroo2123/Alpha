# Joint temperature basket valuation

`v11/basket_valuation.py` evaluates an exact set of YES/NO tokens against one
validated event partition. It preserves the coherent whole-event point vector
and keeps conservative bounds separate. Under the currently implemented vacuous
simplex, the conservative expected payout is the minimum joint outcome payout.
No independent lower bounds are normalized into a probability distribution.

Each leg needs its own current, exact-token, healthy, uncrossed book, sufficient
depth and complete cost coverage. Fees, depth walk and other reserves are counted
once. Unknown costs, stale/replaced books, excessive book-time skew or one missing
leg gate the entire calculation. Complete YES sets and same-condition YES/NO
pairs use exact common resolution semantics; unequal quantities retain only the
minimum common payout floor. Next-observation predictions cannot price settlement.

The report separates fully filled outcomes from adverse optional partial fills.
The latter include existing positions and unresolved orders in the same event,
namespace and account. Cash reservation uses the worst consumed price plus costs;
full-fill economics use the actual depth-integrated acquisition cost. Unfilled
hedges never remove scenario exposure.

These measurements do not admit orders, create spendable cash, recognize trading
P&L or prove a locked executable profit. All results remain GATED until atomic
multi-leg common-account admission and protected strategy/model/source checks are
integrated. Actual execution, approved calibration and runtime acceptance remain
unverified. No financial authority or V10 state is changed.

Verification: 19 synthetic basket tests and 91 combined basket, single-leg
valuation, scenario and probability checks passed in 1.60 seconds. The prior full
repository regression remains 2,808 passing tests at the preceding implementation;
it was not rerun for this additive module. Synthetic tests are code evidence only.
