# Account security and onboarding decision

Research date: 2026-09-16. No account, key, signature, financial transaction or
cloud resource was created. This release retains the tested direct EOA adapter.
**The safe immediate onboarding path is unfunded LIVE_SIGNALS.** Do not fund a
new default Polymarket wallet expecting this EOA adapter to control it.

## Compatibility matrix

| Model | Credential capability | Application support | Compromised executor/host |
|---|---|---|---|
| Direct dedicated EOA | Private key can authorize arbitrary transactions from that EOA, not just trades | Implemented/tested BUY-only CLOB V2; wallet=signer; local limits, account census, actual fill reconciliation | Key theft defeats local BUY-only/financial caps; assets owned by the EOA are exposed |
| Deposit Wallet owner key | Owner signing authority; not a restricted trading credential | Explicitly unsupported; never substituted for a session key | Placing owner authority on this host would defeat the intended restriction |
| Deposit Wallet CLOB session key | Venue-scoped trading, without Deposit Wallet withdrawal permission | NOT IMPLEMENTED; beta provisioning and adapter/reconciliation work remain | Trading can still lose allocated funds; local strategy/price/capital settings are not a cryptographic restriction on an attacker |
| Legacy Safe/Proxy | Separate owner and wallet identities; a proxy label alone does not prove restricted signing | Explicitly unsupported | Depends on actual owner/contract permissions; no safety claim based on account label |
| CLOB L2 API credentials alone | Authenticate private CLOB requests; do not replace order signatures | Existing adapter requires both intended signer and matching credentials | Revoking an API key is not revoking a leaked EOA private key |

Polymarket says new account wallets deployed since May 4, 2026 are Deposit
Wallets; older accounts may be Proxy or Safe wallets. Its account identity
explicitly separates signer, wallet and type. [Wallets and authentication](https://docs.polymarket.com/trading/wallets-auth)

Session keys require a Deposit Wallet and approved Builder API access during
rollout. Current expiry is 180 days, without configurable shorter duration.
Prefer CLOB scope, not ALL. Owner revocation fences trading first; order
cancellation and chain confirmation finish asynchronously. Order/trade visibility
is session-specific; owner credentials cannot simply query session orders.
[Session keys](https://docs.polymarket.com/trading/session-keys)

This is narrower account authority, **not** a guarantee against trading losses.
Never fund the session EOA itself as a substitute for the Deposit Wallet.
Telegram policy, signer capability and host compromise are separate boundaries.
The panel's Wallet security screen exposes the current full-EOA limitation.

## Precise restricted-adapter gap

The current `ExchangeEOA`, config, deterministic order hashing and receipt
validation deliberately enforce signature type 0 and wallet=signer. Deposit
Wallet orders use type 3, wallet maker/signer fields and ERC-7739 wrapping; the
session path adds its signer envelope. A type-label change would produce neither
a correct signer nor honest recovery. [Official order signing](https://docs.polymarket.com/trading/place-orders)

A restricted integration still needs independently checked wrapping/hash vectors,
authorized-session identity/scope/expiry readiness, a separate session credential
namespace and an account census that does not mistake restricted visibility for
absence of other activity. It also needs revocation/cancel-race recovery and owner
redemption attribution. Those are implementation gaps, not completed features.
Beta management access for this user is unknown; an unavailable permission must
not be bypassed by using the owner's key. No half-supported adapter is exposed.
The config now rejects explicit non-EOA/session/withdrawal-disabled requests,
even when an accidentally supplied owner address would otherwise equal signer.

## Supported sequence before any funding

1. Commission LIVE_SIGNALS without a trading account, wallet key or executor.
   Test public sources and a separately authorized **test bot** on the new host.
2. On a separate trusted owner device, check actual personal/location/platform
   eligibility and identify the intended account/wallet type. Keep account login,
   recovery and funding custody independent of the server and Telegram. No
   credentials belong in chat, Git, reports or shell history.
3. Preferred restricted route: ask Polymarket whether this account's Builder API
   can manage session keys. The user performs that contact; this task sends no
   message. Approval is an external provisioning gate. Until obtained and the
   precise adapter gap above is implemented/verified, remain signals-only.
4. A direct-EOA route is available only after a **separate explicit operator
   choice accepting full-key custody on the executor**. Use a dedicated account
   with no unrelated/manual trading. Do not select it silently when beta access
   is unavailable. Owner backup/emergency access must work without this host.
5. After the chosen route is genuinely supported: independently select limits,
   provision credentials privately, verify account identity/geographic eligibility,
   balances/allowances and empty/unambiguous journal reconciliation. Approve the
   immutable release/config/host grant. Stop before funding/financial activation
   until separately authorized. Mock tests do not establish account readiness.

## Revocation without Telegram or a working bot

For a session route, keep the owner's official revocation capability and Builder
management access on the trusted owner device, outside this deployment. Verify
registry removal, terminal order states and chain confirmation; do not interpret
a revocation response as proof that fills cannot arrive. Preserve the bot's
journal and session credentials needed for supported history access securely.

For the currently supported EOA route, an application stop or API credential
revocation cannot cryptographically revoke an EOA key. On suspected compromise,
use independent owner/account access to stop venue access and protect remaining
assets under a separately approved incident response. A fresh key/account may be
required. Never delete fills, reuse an uncertain signed intent, or rewind the
execution journal to make reconciliation pass. The bot exposes no withdrawal,
owner-grant, credential-creation or revocation transaction command.

## BUY, hold, claim and redemption

The app buys supported outcome tokens and holds confirmed inventory. Finalized
CTF payout evidence creates a **claimable value**, not cash. A structural partial
basket remains held directional inventory; the full basket acquisition cost is
bounded by the legging-loss setting. There is no automatic unwind or early SELL.

Current official collateral-adapter redemption burns resolved outcome balances
and returns pUSD to the calling wallet. The standard and negative-risk adapters
have different addresses; approval of the correct adapter to transfer outcome
tokens is required. Gasless wallet operations require supported Relayer/Builder
access and owner authority. [Manage positions](https://docs.polymarket.com/trading/positions/manage)

The existing receipt importer supports a **narrower direct EOA -> CTF
redeemPositions(USDC.e, zero parent, condition, index sets)** transaction. Its
chain decoder checks canonical transaction sender/target/calldata, finalized
block, payout log, exact burns and asset; it records USDC.e proceeds back to that
EOA and sender-paid POL gas. It rejects smart-wallet/adapter paths whose proceeds
it cannot attribute. Do not use the default website's adapter redemption and
expect this narrower importer to recognize it. See `production/chain.py` and its
retained receipt tests; expanding this importer is an optional integration gap.

For an operator choosing the supported direct route, first verify that the held
position IDs are the USDC.e-based CTF IDs checked by this app and that finalized
payouts exist. Use an independently trusted wallet/contract interface to perform
the direct redemption, paying Polygon gas in POL. No ERC-20 spending allowance is
needed for the CTF to burn the caller's own outcome tokens. Retain the confirmed
transaction hash and condition. Run local read-only `record-redemption` and then
preflight; the command signs/submits nothing. The owner must not transfer the
positions elsewhere and expect this bot's dedicated-account balance to reconcile.

USDC.e returned by that direct path is not the spendable pUSD balance. Official
wrapping requires approval of CollateralOnramp to spend USDC.e, followed by wrap
to the intended recipient; it is a separate owner operation with gas, not a bot
feature. The bot sees spendable capital only after actual pUSD balance and exchange
allowances reconcile. [pUSD wrapping](https://docs.polymarket.com/concepts/pusd)

Automatic redemption, collateral conversion and broader transfer signing are
not added. Settlement/cash in Telegram separates unredeemed claims, confirmed
redemption by asset, native gas and observed trading collateral. No theoretical
basket, alert delivery, historical simulation or unredeemed claim becomes cash.
