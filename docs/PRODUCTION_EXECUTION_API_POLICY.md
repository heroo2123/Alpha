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

The current positive onchain `getMaxFeeRate()` bounds the reserved fee for each
BUY by `limit price × maximum bps / 10000` per share. Zero means unlimited in
the reviewed contract and is rejected. Missing fee data is not zero. The bound
must fit the operator's configured cap. V2 does not sign a fee ceiling, and its
administrator can change the maximum: repository code cannot cryptographically
freeze future venue governance. Existing-order management must stop and request
cancellation if the fee bound ceases to fit, while preserving any committed fill.

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
