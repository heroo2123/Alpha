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
P&L or prove a locked executable profit. The measurement remains GATED; a separate
`BasketProposal` now enters the existing common paper-account coordinator. That
path reproduces the whole prediction from leased inputs and the protected model
bundle, recomputes every leg, and repeats scoped certification, event, source,
book, operator and model checks before reservation and submission-state changes.

Baskets and single-leg proposals share one ranking, cash budget and account CAS.
Every leg is reserved together or none is. Changed desired-position deltas require
revaluation rather than dropping a hedge. Account EV uses each leg's permitted
limit price plus reserves, not just the better displayed average. The per-intent
cash ceiling applies to the aggregate basket. Joint EV is not duplicated as
individual-leg alpha. Every unresolved leg retains its adverse optional-fill risk.

Fills debit actual cash and preserve per-leg basis; cancellation/terminal evidence
releases only the reconciled leg. Unknown or canceled sibling legs block further
group submission until replanning. Fully filled sets remain inventory: payout,
claimable and redeemed cash are not invented. Existing uncalibrated single-leg
entries remain rejected. Conditional structural floors can qualify synthetic
paper mechanics only with all protected gates; no strategy is live eligible.

Actual execution, approved calibration and runtime acceptance remain unverified.
Current model reproduction supports archived whole-day final-extreme inputs;
conditioned remaining-day basket inference and automatic discrepancy discovery
remain pending. No financial authority or V10 state is changed.

Verification: 19 synthetic basket tests and 91 combined basket, single-leg
valuation, scenario and probability checks passed in 1.60 seconds. The prior full
repository regression remains 2,808 passing tests at the preceding implementation;
it was not rerun for that additive module. Subsequent common-account integration:
24 new tests and 125 related checks passed in 17.72 seconds. The shared coordinator
change was then covered by a full regression: **2,851 passed, four pre-existing
warnings, 125.91 seconds**. Synthetic tests are code evidence only.
