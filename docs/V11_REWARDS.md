# V11 maker rewards and rebates

Implemented off-host in reward_rules.py and maker_rewards.py, with bounded
PaperRuntime refresh. Research quotes are not exchange orders. R36 remains PARTIAL;
no live entitlement, payment verification, cash credit or trading eligibility is
claimed.

## Public settings and qualification

The anonymous collector can fetch one exact numeric Gamma market ID through
`GET /markets/<id>`; this route rejects query parameters, redirects, authentication
and other market paths. The existing byte/time/request limits and host cooldowns
apply. Responses remain archived before use and are re-parsed against the rule's
exact market, condition and YES/NO tokens. New receipts cannot silently reuse old
revision authority. Missing or malformed settings stay UNKNOWN.
The route and returned reward/fee fields follow the current official
[market discovery](https://docs.polymarket.com/market-data/discover-markets) and
[market details](https://docs.polymarket.com/market-data/market-details) documentation.

The conditional scoring recipe uses squared distance weighting, complementary
book sides and the documented central-price single-sided adjustment. It has a
versioned, expiring methodology-review input. This input is a local research
review timestamp, not protected independent approval. Research computes a local
book-midpoint diagnostic; the official size-adjusted reference and market-wide
epoch samples are unverified. The score cannot establish a reward entitlement.
See the official [liquidity scoring description](https://docs.polymarket.com/programs/liquidity-rewards).

Pool estimates have zero lower bounds and explicitly labeled whole-market daily
caps. Per-quote caps overlap and are never added. Unknown end-date inclusivity
closes the final calendar day's estimate. A conditional full-fill rebate is
reported separately from expected rebate, with fill probability and actual
payment unknown; category rates are not hardcoded. The official
[maker rebate description](https://docs.polymarket.com/programs/maker-rebates)
requires executed maker liquidity.

## Runtime and accounting integration

Tracked reward quotes are recomputed in bounded round-robin batches. Changed
parameters, stale methodology or invalid evidence retire that local research
quote and require a new proposal. Same-parameter receipts cannot extend quote
lifetime. Exact replay preserves historical results, and guarded publication
rejects parameter/book/quote races. Runtime health and cancellation precede this
optional research work. No operation sends an order or cancellation to a venue.

Trading EV remains UNKNOWN until maker execution economics are validated; reward
pursuit cannot bypass that requirement. Same-horizon conservative floors have a
separate research-only screen. Rewards do not modify common-account cash,
reservations, claimable collateral, hard limits or trading-alpha P&L.

Synthetic payment tests match distinct program-statement and transfer records,
require exact account/asset/amount/program/transaction identity and reject
unfinalized, mismatched or duplicate chain/transaction/log receipts. The same
transfer cannot be counted again under a rebate label. Public-capture claims are
rejected by this synthetic-only API. A production independent payment attestor
has not been commissioned; actual income and estimate/actual discrepancy remain
UNKNOWN. This is an explicit acceptance gap, not independent reconciliation.

Reports separate TRADING_PNL, REWARD_REBATE_INCOME and COMBINED_NET_RESULT.
Combined figures contain recorded synthetic amounts only, use identical asset
units, exclude foreign assets and are never presented as available cash.

## Verification and remaining gates

43 new reward tests cover parameter provenance, revision/clock expiry, unknown
settings, duplicate pools, complementary scoring, midpoint boundaries, same-receipt
replay, rule-change retirement, publication races, synthetic income matching,
duplicate transfers, currency separation and runtime integration. First affected
checks: 60 passed in 4.72 s; broader runtime/maker/collector checks: 191 passed in
19.90 s. After aligning the collector to the current documented exact-ID path, the final
affected run passed 191 tests in 22.23 s.
No live public-market probe or empirical reward measurement was performed.

Remaining: official adjusted-reference/scoring verification, current program-review
capture automation, managed real-order qualification, market-wide epoch support,
independent real payment reconciliation, discrepancy measurement, protected
first-canary policy and independent review. These are not prerequisites for
continuing other off-host implementation. V10 maintenance stays deferred.
