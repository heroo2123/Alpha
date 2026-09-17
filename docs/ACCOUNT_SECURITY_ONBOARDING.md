# Account security and onboarding decision

Research refreshed: 2026-09-17. The finalized production release
`3114657eff8c95fcf4116f42570ae02c8c3084a1` remains an unfunded, stopped
`LIVE_SIGNALS` deployment and does **not** gain financial authority from this
engineering branch. No account, wallet, funding, owner key, Builder key or real
Session Key was created during this work.

The preferred restricted route is a **Deposit Wallet + CLOB-only Session Key**.
That adapter is implemented on the development branch
`deposit-wallet-session-key-adapter-2026-09-17`, but it is not yet a reviewed
release, host-approved executor, account acceptance, or funding authorization.
Stay signals-only until all of those separate gates pass.

## Compatibility matrix

| Model | Credential capability | Application support | Compromised executor/host |
|---|---|---|---|
| Direct dedicated EOA | Private key can authorize arbitrary transactions from that EOA | Retained and tested; wallet=signer, signature type 0 | Key theft defeats local BUY-only/financial caps and exposes EOA-owned assets |
| Deposit Wallet owner key | Owner authority over wallet/session management | Explicitly forbidden from runtime credentials | Putting it on the host would defeat the restricted-key design |
| Deposit Wallet CLOB Session Key | Venue-scoped CLOB trading; no owner/withdrawal action is exposed by this runtime | Implemented on this development branch; signature type 3, CLOB scope only; release/host/account acceptance still pending | Attacker can still trade allocated funds within session authority; cannot be treated as harmless |
| Legacy Safe/Proxy | Separate owner/wallet identities | Explicitly unsupported by this adapter | Depends on actual contract/owner permissions; labels alone prove nothing |
| CLOB L2 API credentials alone | Authenticate private CLOB calls | Required alongside the relevant signing key; never a substitute for an order signer | Revoking L2 credentials is not equivalent to revoking a leaked signing key |

Polymarket's current wallet documentation says new account wallets are Deposit
Wallets. Current official `Polymarket/py-sdk` source at commit
`579bb2e56be9cc5d152546985870ee6ad795ec52` implements scoped Session Keys: owner
authorization requires Builder access, the Session Key is a separate EOA, `CLOB`
is an explicit scope, and the public authorization lifetime is described as 180
days. The exact v0.10.0 SDK request uses 4,315 hours (179 days 19 hours), leaving
a five-hour buffer. Its live integration test authorizes a Session Key, places and cancels
a CLOB order, then revokes the key. Use `CLOB`, not `ALL`, for this bot.

## Restricted adapter implemented on this branch

The Deposit route requires the protected execution configuration to bind:

- `wallet_type="DEPOSIT_WALLET"` and `signature_type=3`;
- `wallet` = the Deposit Wallet and `signer` = the distinct Session Key EOA;
- `deposit_owner` = the public Owner EOA address from which that Deposit Wallet is derived;
- `session_scopes=["CLOB"]` exactly;
- the externally verified venue authorization expiry in `session_valid_until`;
- a separately approved dedicated-wallet commitment in `session_exclusive_until`.

The private executor credential file keeps the existing four-field schema:
`private_key`, `api_key`, `api_secret`, `api_passphrase`. For this adapter those
are the **Session Key** private key and that Session Key's CLOB L2 credentials.
`deposit_owner` is public identity metadata, not a secret or signing credential.
The Deposit Wallet Owner key and Builder credentials are not accepted by, needed
by, or stored in the runtime. Session secrets in the nonsecret JSON config are rejected.

Order preparation uses the Deposit Wallet as the signed order maker/signer,
signature type 3, the official nested `TypedDataSign` structure, ERC-7739
wrapping, and the Session Key envelope. Offline Python output is checked against
an independently generated `viem` vector for the order hash, inner signature,
app-domain separator, contents hash and final wrapped-signature digest. The
existing EOA type-0 path remains separately tested.

The adapter authenticates CLOB private requests as the Session Key and therefore
accepts that those private order/trade views are session-specific. It combines
that source with wallet-wide public positions, on-chain balances/allowances and
a mandatory wallet-wide `/v2/activity?type=TRADE` witness. Initial reconciliation
walks the wallet's served trade history; later reconciliations re-audit a seven-day
overlap. A wallet TRADE not accounted for by confirmed activity visible to the
configured session becomes sticky `EXTERNAL_WALLET_TRADE_ACTIVITY` and closes
opening authority. The public feed is evidence only; it never creates a fill or
P&L entry.

## Remaining visibility boundary

The public activity witness cannot enumerate a **resting order** owned by another
active Session Key before that order trades. Session-private CLOB history also
cannot prove that no other session exists. Therefore this adapter does not claim
cryptographic account exclusivity.

A Deposit Wallet used by this bot must be dedicated to one trading session while
opening authority is enabled. The owner must not place manual CLOB orders or
authorize another trading session during the externally reviewed exclusivity
window. `session_exclusive_until` records that commitment and expires opening
authority automatically; it does not create or prove the commitment. Public
wallet activity and positions provide detection after external execution, not
permission to run multiple writers.

If another session or manual order may exist, revoke/close that authority using
the trusted owner device, reconcile all resulting orders/fills, rotate the bot's
external authorization as needed, and obtain a fresh configuration/activation.
Never “fix” the state by deleting the execution journal or treating an empty
session-private order list as account-wide emptiness.

## Supported sequence before any funding

1. Keep the finalized UpCloud deployment stopped/disabled and signals-only while
   this development branch is independently reviewed and promoted.
2. On a separate trusted owner device, confirm personal/location/platform
   eligibility and the actual wallet type. Account login, recovery and funding
   custody remain independent of this server and Telegram.
3. Obtain the required Polymarket Builder access for Session Key management. The
   owner device creates/authorizes one dedicated Session Key with **CLOB-only**
   scope and records its actual wallet/signer identities and venue expiry.
4. Do not copy the Owner key or Builder credentials to UpCloud. Privately
   provision only the Session Key private key and its CLOB L2 credentials to the
   eventual executor identity after host/release approval.
5. Ensure no other active trading session/manual CLOB writer exists for that
   Deposit Wallet, then independently bind the exclusivity window, exact release,
   config, account identity, explicit risk ceilings and schedule.
6. Run an **unfunded** private account preflight first with
   `preflight --allow-unfunded`. Verify session identity, scope/expiry,
   geographic/close-only status, wallet-wide public activity, current positions,
   balances/allowances, authenticated session orders/trades, empty/unambiguous
   local journal state and the external exclusivity assertion. A successful
   account-only preflight may intentionally report `account_reconciled=true` with
   `funding_ready=false`, `reconciled=false`, and `financial_authority=false`.
   Ordinary `preflight` remains strict and requires both sufficient collateral
   balance and allowance for at least the configured per-order limit.
7. Stop again before funding. Funding and financial activation require a new
   explicit operator decision after the exact release and host executor are
   accepted. Never fund the Session Key EOA as a substitute for the Deposit
   Wallet.

The Session registry is owner-only in the current official SDK, so the executor
cannot independently query its own authorization scope/expiry. Those two fields
are therefore externally verified owner-device evidence bound into the protected
config/activation; authenticated CLOB reads do **not** prove that the Session was
authorized with only `CLOB` scope. A mismatched or stale owner record blocks
commissioning rather than being inferred from server credentials.

The direct-EOA route remains available only after a separate explicit decision to
accept full private-key custody on the executor. There is no silent fallback from
a failed/unavailable Session Key route to an owner or EOA key.

## Revocation and incident response

Session authorization/revocation belongs on the trusted owner/Builder device,
not Telegram and not this host. Venue revocation may cancel open orders, but a
revocation response is not proof that no fill raced with it. Preserve the
execution journal and Session Key history access long enough to reconcile all
terminal order states, wallet-wide activity and chain-confirmed positions.

A compromised Session Key is narrower than a Deposit Wallet Owner key, but it can
still place trades and lose allocated collateral. The execution journal is bound
to that Session signer: do not swap in a fresh Session key and resume against the
same journal. Renewing the same signer’s authorization may update expiry through
a separately approved config; changing signer requires reviewed migration.
Emergency response therefore
includes owner-side session revocation, bot opening stop, authenticated
cancellation/reconciliation of the compromised session where still possible,
wallet-wide activity review and a fresh authorization/configuration before any
resume.

## Claims, redemption and cash

The application remains BUY-and-hold. A resolved position creates claimable
value, not spendable collateral. Automatic early SELL, automatic redemption and
a Deposit Wallet redemption transaction are not added by this branch.

The existing `record-redemption` importer remains the narrower direct-EOA CTF
receipt path. Deposit Wallet execution explicitly rejects that importer instead
of pretending a smart-wallet/adapter redemption can be attributed correctly.
Deposit Wallet redemption or collateral conversion stays an owner-side operation
until a separately reviewed receipt/adapter integration exists. Claims, redeemed
asset proceeds, native gas and spendable pUSD remain distinct accounting states.

No owner key, Builder credential, account password, wallet recovery secret or
financial private key belongs in chat, Git, screenshots, reports or shell
history. Mocked/offline adapter tests do not establish Builder eligibility,
account readiness, funding permission or real-money activation.
