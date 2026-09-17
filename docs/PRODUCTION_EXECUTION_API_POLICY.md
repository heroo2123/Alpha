# Execution adapter scope and dependency policy

The financial worker uses `production/exchange.py` and `production/chain.py`.
It supports a dedicated, operator-provisioned EOA on Polygon chain 137, with the
signer equal to the wallet and existing level-2 CLOB credentials. Signal processes
do not load these credentials. It neither creates API keys nor deploys wallets,
sets allowances, relays transactions, bridges collateral, sells inventory, or
redeems positions. Those are explicit operator-controlled provisioning/actions.
Unsupported wallet signatures, protocol position IDs, market inputs and chain
proofs fail with controlled error codes. There is no simulation fallback.

## Source contract

Reviewed against the official `polymarket-client` 0.10.0 wheel (source inspection
only), the official V2 Python order builder, and CTF Exchange V2 source at
`ccc0596074f4dfd62c944fbca4de252893b82b4b`:

- [Order hashing](https://github.com/Polymarket/ctf-exchange-v2/blob/ccc0596074f4dfd62c944fbca4de252893b82b4b/src/exchange/mixins/Hashing.sol)
- [Trade and fee accounting](https://github.com/Polymarket/ctf-exchange-v2/blob/ccc0596074f4dfd62c944fbca4de252893b82b4b/src/exchange/mixins/Trading.sol)
- [Fee bound implementation](https://github.com/Polymarket/ctf-exchange-v2/blob/ccc0596074f4dfd62c944fbca4de252893b82b4b/src/exchange/mixins/Fees.sol)
- [Current contract addresses](https://docs.polymarket.com/resources/contracts)
- [Market constraints and fee curve](https://docs.polymarket.com/api-reference/markets/get-clob-market-info)
- [Published platform fee calculation and precision](https://docs.polymarket.com/trading/fees)
- [Official all-in BUY budget guidance](https://docs.polymarket.com/trading/place-orders#cap-market-buy-spending)
- [Reviewed Python SDK release](https://pypi.org/project/polymarket-client/0.10.0/)
- [Authenticated orders](https://docs.polymarket.com/api-reference/trade/get-single-order-by-id)
- [Authenticated trades](https://docs.polymarket.com/api-reference/trade/get-trades)
- [Position census](https://docs.polymarket.com/api-reference/wallet/list-positions-for-a-user-or-market)
- [Order submission](https://docs.polymarket.com/api-reference/trade/post-a-new-order)
- [Conditional token redemption](https://github.com/gnosis/conditional-tokens-contracts/blob/master/contracts/ConditionalTokens.sol)

The SDK itself is deliberately absent from the production dependency lock:
its convenience client can provision wallets/keys, approve spenders and retry.
The narrow adapter explicitly performs HTTP authentication and EIP-712 signing
using reviewed pinned primitives. No SDK source is vendored.

## Supported orders and recovery

BUY FAK/FOK limit orders are available to the execution engine; maker orders use
post-only GTD. There is no client order ID or assumed exchange idempotency.
`prepare_buy` rechecks Gamma/CLOB identity, tradability, timestamp, tick size,
minimum size, executable depth and fee evidence, then signs locally without a
financial API call. A standard V2 EIP-712 order hash identifies reconciliation.
Because expiration, order type and post-only behavior are outside the order hash,
the engine also persists the exact signed wire and its SHA-256 before arming one
POST. Lost, inconsistent or failed HTTP responses remain UNKNOWN unless an
explicit rejection was received. The worker must never replay an unknown POST.

Minimum-size admission uses an explicit conservative subset because the
[market-details documentation](https://docs.polymarket.com/market-data/market-details#trading-constraints)
labels the minimum as notional, whereas the CLOB book exposes an unlabeled
numeric minimum and limit-order sizes are shares. The bot does not claim the
venue enforces both interpretations. Its policy
`REQUIRE_BOTH_SHARES_AND_BUY_NOTIONAL` requires submitted shares to be at least
the returned minimum and `shares × BUY limit price` to be at least that same
numeric minimum. The public reader exposes both thresholds and the policy;
the public quote probe and pre-sign adapter call the same validation helper.
`BUY_NOTIONAL_BELOW_CONSERVATIVE_MINIMUM` precisely identifies a share-valid
order that fails the notional condition. This applies equally to FAK/FOK takers
and post-only GTD makers: maker status changes fees, not this admission policy.
For example, a returned minimum of 5 and a BUY limit of 0.40 require at least
12.50 submitted shares, plus separately reserved fees. The bot never increases
the operator's capital or order limits to meet a minimum. Smaller partial fills
remain valid accounting evidence; the policy constrains submission, not each fill.

GTD expiration has the documented safety buffer; this implementation requires
an expiration at least 180 seconds ahead. Weather/thesis expiry is independent
and can trigger an earlier cancellation request. A cancel response alone does
not release reserves: authenticated order status and matched quantities must
reconcile with confirmed chain fills. `CANCELED_MARKET_RESOLVED` preserves all
matched quantities while cancelling the remainder.

All account listings follow bounded pagination. A repeated cursor, incomplete
response or resource cap is a reconciliation fault, not a complete census.
Opening eligibility is distinct from read permission. Closed-only, geoblock or
an unavailable geographic probe set `openings_allowed=false` and a precise
`opening_restrictions` list, while successful authorized GETs can still reconcile
existing fills and cancellations. Invalid credentials and denied reads remain
fatal. Cancellation is subject to the exchange's own permission checks. No
alternative location, identity or endpoint is used to avoid a restriction.
The entire authenticated trade census exposes our exact order IDs, including
external SELL trades, for the dedicated-account check. Public position values
are cross-checked against CTF balances; neither indexer P&L nor public prints
create a bot fill. A public indexer cannot prove the absence of unindexed external
activity, so account exclusivity remains an explicit operator prerequisite.

`account_snapshot(trade_after=...)` and
`account_trades(after=..., before=..., token=...)` support a durable incremental
trade census and bounded historical audits. The official authenticated trade
endpoint supports both decimal Unix-second bounds. They filter exchange match
time, not last-update time. Bounds persist across every page; malformed or
inverted windows fail before network access. Returned timestamps outside the
requested bounds fail reconciliation. Boundary equality is accepted without
assuming endpoint inclusivity or snapshot isolation; adjacent windows overlap.
Normalized `matched_at`, `updated_at` and `trade_watermark` are Unix seconds.
`trade_watermark` is exactly the maximum match time returned, defaulting to the
requested `trade_after` (or zero) for an empty response. It is neither server time
nor proof that older trades cannot later be indexed. The engine must overlap its persisted
watermark and include the oldest outstanding/UNKNOWN order's creation time;
older order audits need an explicit earlier per-token query. Persist a watermark
only after processing the entire response. Initial accounts without a trusted
local cursor require a complete census. A window exceeding 100 pages or 10,000
items is an explicit reconciliation fault, never silently truncated. A complete
account snapshot older than 15 seconds is rejected; book freshness is rechecked
after the onchain fee call so slow I/O cannot make an old quote look fresh.

## Fees, fills and settlement

Approved V2 exchanges are `0xe111180000d2663c0091e4f400237545b87b996b` and
`0xe2222d279d744050d28e00520010520000310f59`. Collateral spending is pUSD at
`0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb`. Conditional balances and payout
proofs use CTF at `0x4d97dcd97ec945f40cf65f87097ace5ea0476045`.

LIVE_EXECUTION requires an explicit operator-selected `fee_policy`:

| Policy | Submission requirement per BUY share | Authority and limitation |
| --- | --- | --- |
| `ONCHAIN_BOUND` | `limit_price × getMaxFeeRate() / 10000` | A fresh positive contract maximum is required. Zero means unbounded and fails this policy. The maximum remains administrator-controlled. |
| `EXCHANGE_PUBLISHED_SCHEDULE` | Conservative envelope of the fresh CLOB fee curve over all possible BUY fill prices up to the limit | Relies on the venue following its published fee schedule and rounding. Permits a zero contract maximum while explicitly reporting that no chain fee bound exists. |

The adapter never selects a policy or risk limit for the operator. Both policies
recheck raw CLOB fee details, base fees and chain maximum before signing; missing
fields fail closed. Current V2 orders do not sign a fee ceiling. Neither choice
is a promise that future fee administration, matching or cancellation will obey
a locally configured cap. The SDK's documented BUY budget feature likewise
reduces the signed notional using fee estimates, rather than signing a fee cap.
Published-schedule mode explicitly accepts that platform risk; the worker
reserves the operator's entire configured fee allowance. A confirmed fill that
exceeds price, quantity or fee limits remains actual accounting evidence and
atomically faults execution and queues managed remainders for cancellation.
A cancellation request cannot undo the fill or guarantee an immediate stop.

The reviewed SDK's `_internal/actions/orders/market.py` function
`adjust_buy_amount_for_fees` computes platform fees as
`shares × fd.r × (price × (1-price)) ** fd.e`, added to BUY notional. This
implementation supports explicit nonnegative integer exponents 0–4, rates 0–1,
`fd.to=true` and the zero signed builder identifier. Unknown curve components
or other exponents are rejected.
Zero published rates are accepted only when explicitly supplied. Post-only GTD
uses the documented maker fee of zero; a taker order cannot claim that exemption.

The CLOB `mbf`/`tbf` fields are preserved as nonnegative int64 basis-point
metadata. Their presence does not add another platform fee or require a zero
value. In the reviewed SDK, `_parse_platform_fee_info` in
`_internal/actions/orders/market_data.py` selects only `fd.r` and `fd.e`;
`MarketInfo` carries no base-fee fields. The BUY budget calculation adds that
platform curve and a separately attributed builder fee. Requiring zero base
fields therefore rejected ordinary API records for a reason absent from the
official implementation. The adapter follows the SDK calculation; it does not
invent an additive composition or assert that the base metadata is an onchain
cap. Actual confirmed fees still override every estimate.

The reviewed `polymarket_client-0.10.0-py3-none-any.whl` SHA-256 is
`f378e07351d4bfdf390ca84c6ba47898f9a715f2f4446afe0446bbeafa495ee5`.
This identifies the inspected source artifact; the SDK is not installed into the
production worker. The public probe also records bounded CLOB `fd`/`mbf`/`tbf`
and Gamma `feesEnabled`/`feeType`/`feeSchedule` fields on failures, so unsupported
schemas and stale books retain their real public evidence.

For a taker BUY limit `L`, the curve's peak over possible execution prices is at
`min(L, 0.5)`, including favorable price improvement. The schedule requirement
is twice that peak raw fee per share. This conservative margin covers arbitrary
partial-fill fragmentation under the published five-decimal/minimum-fee rule:
a raw fragment below the minimum rounds to zero; otherwise an adjacent-quantum
rounding adds at most one quantum, no more than the raw fee. Ordinary nearest
rounding at half a quantum is also covered. No particular tie-breaking rule or
fragment count is assumed. This is a conditional schedule envelope, not an
immutable maximum; actual receipt fees remain authoritative.

The raw fee fields, policy, token, condition, exchange, observation time and
chain block identity have a stable SHA-256 evidence identity. The engine persists
both planning evidence and the final signing evidence with its submission audit.
This hash prevents accidental evidence substitution; it is not an exchange
signature or independent provenance. `PublicMarketReader` is shared between the
signer adapter and the public probe, and does not load credentials. The public
probe reports real zero chain maxima, strict weather-contract semantics and
current CLOB evidence. It collects the bounded event sample first, then gives
each supported event one book attempt per round, visiting central buckets first.
An event with many stale buckets cannot consume every attempt before later
events are examined. Its bounded sample is not a full census or account/host
acceptance; an empty order book is a labelled opportunity skip, not a fabricated
quote or proof of a profitable trade.

The optional CLOB `oas` value is retained as `minimum_order_age_seconds` and in
public diagnostics. The API reference labels it minimum order age, while the
reviewed SDK does not consume it. Neither source establishes a cancellation
latency guarantee. The bot therefore does not infer that requesting cancellation
is immediately effective; outstanding orders and reserves remain until actual
reconciliation. GTD's separate 180-second expiration buffer follows the SDK.

Only successful receipts at or below a fresh finalized Polygon block, with
canonical block hashes and consistent log identities, produce actual fills.
`OrderFilled` must match the approved exchange, order hash, wallet, BUY side and
token. Actual quantity is `takerAmountFilled`, purchase cost is
`makerAmountFilled`, and the event's fee is additional collateral cost. The fee
does not reduce BUY share quantity. Amounts are exact integer micros; receipt
identity plus log index deduplicates partial fills. API acknowledgement, trade
fee rates, order touches and provisional matches never create actual P&L.
`confirmed_fills(..., recorded_fills=...)` independently re-fetches every recorded
transaction supplied for that audit, even if the trade indexer has dropped it.
A missing/nonfinal receipt or missing log produces `RECORDED_FILL_PROOF_MISSING`;
changed block identity or amounts produce `RECORDED_FILL_PROOF_CHANGED` (and
noncanonical receipts fail directly). Preserve immutable accounting history and
stop new openings on either fault; never erase the prior fill to make balances
appear reconciled. The engine's bounded historical audit must supply ledger rows.

Final CTF payout numerators/denominator are bound to the condition and token ID
at a finalized block. This establishes claimable outcome value, not cash receipt.
The read-only redemption command currently proves direct EOA → CTF
`redeemPositions` transactions for USDC.e collateral only: exact calldata,
`PayoutRedemption` and matching preceding `TransferSingle` burns are required.
USDC.e (`0x2791bca1f2de4661ed88a30c99a7a9449aa84174`) proceeds are labelled
separately from spendable pUSD. Adapter/negative-risk redemption attribution is
not implemented and must not be inferred from the adapter's payout event.

Successful imported direct redemptions also preserve `proof.native_gas` from
the finalized receipt's `gasUsed × effectiveGasPrice` in POL wei (18 decimals),
bound to the same sender and transaction block. [Receipt field definitions](https://ethereum.org/developers/docs/apis/json-rpc/)
and [Polygon's native gas token](https://docs.polygon.technology/pos/concepts/tokens/pol)
define those units. The gas record's stable `137:<transaction hash>:gas` identity
must be deduplicated across imports; it is one transaction expense shared by all
burned outcome legs. No USD/pUSD conversion is invented, and exchange matching
transaction gas is not charged to our wallet merely because it contains our fill.
This import does not discover failed/operator provisioning transactions or their
gas costs; reports must describe gas coverage as imported successful direct
redemptions, not total wallet gas expenditure.

## Isolated dependency policy

`requirements-execution-hashed.txt` is a complete, flat 39-distribution lock for
Linux x86_64 CPython 3.11/3.12. It retains every base runtime pin because the worker
independently validates public weather evidence. Install it with
`--require-hashes --only-binary=:all:` in a fresh release-specific worker venv.
Never install it into the signal runtime or upgrade a predecessor venv in place.

The additional reviewed execution dependency families are:

| Purpose | Pinned distributions |
| --- | --- |
| Local EOA/EIP-712 signing | eth-account 0.13.7, eth-keys 0.7.0, eth-keyfile 0.8.1 |
| ABI and typed values | eth-abi 5.2.0, eth-typing 5.2.1, eth-utils 5.3.1, hexbytes 1.3.1 |
| Cryptography and required signing transitives | eth-hash 0.8.0, pycryptodome 3.23.0, eth-rlp 2.2.0, rlp 4.1.0, bitarray 3.11.0, ckzg 2.1.8 |
| Required parser/utility transitives | parsimonious 0.10.0, regex 2026.9.10, cytoolz 1.1.0, toolz 1.1.0 |

The complete inventory, import origins and installed file integrity are release
acceptance checks, including dependencies not exercised by BUY signing. Hash
pins establish exact artifacts, not a security audit of every upstream line.
`requirements-dev.txt` adds four hash-pinned test distributions to the worker
lock; a deployable worker should not contain that extra test inventory.

HTTP disables environment proxies/custom CAs and redirects, bounds response
bytes and timeouts, and performs no retry. Polygon RPC exposes only read methods
to this adapter. Operational acceptance must verify the operator-selected RPC's
chain ID, fresh finalized-block support, current exchange fee limits and actual
account eligibility from the intended host. These checks were not certified
against an operator account or target host during repository tests.

### Deposit Wallet attestation extension (requires independent host-policy review)

The application RPC read allowlist additionally permits only `eth_getCode` and
`eth_getStorageAt` for exact reviewed runtime/proxy evidence. `eth_call` may carry
a validated public wallet `from` for caller-dependent beacon resolution. All
attestation reads are pinned to one fresh block, whose canonical hash is checked
again. No transaction, approval, Session management or remote signing method is
permitted. See DEPOSIT_WALLET_CODE_POLICY.md for pins, source provenance, upgrade
handling and the distinction between application policy and host authority.
