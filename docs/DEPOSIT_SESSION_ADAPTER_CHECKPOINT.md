# Deposit Wallet CLOB Session Key adapter — development checkpoint

Date: 2026-09-17

Base release: `3114657eff8c95fcf4116f42570ae02c8c3084a1`
(tree `b765be586835d50f6fe5eab7a51ea7817717b59b`).
Development branch: `deposit-wallet-session-key-adapter-2026-09-17`.

The finalized UpCloud release remains stopped/disabled, unfunded `LIVE_SIGNALS`.
This branch has not installed an executor, changed host authority/configuration,
created an account or wallet, provisioned credentials, funded collateral, sent an
order or granted financial authority.

## Purpose

Add the preferred restricted execution route without weakening the retained EOA
engine: a Deposit Wallet using one **CLOB-only Session Key**. The Deposit Wallet
Owner key and Builder credentials remain outside the runtime and outside the
server. No unsupported Safe/Proxy or owner-key fallback is added.

## Configuration and credential boundary

Deposit execution requires `wallet_type=DEPOSIT_WALLET`, `signature_type=3`, a
Deposit Wallet `wallet`, distinct Session EOA `signer`, public Owner EOA address
`deposit_owner`, exact `CLOB` scope, externally verified venue expiry and a separately reviewed
`session_exclusive_until` dedicated-wallet assertion. Direct EOA remains type 0
with wallet=signer.

The private four-field runtime credential file contains the Session EOA private
key and that session's CLOB API key/secret/passphrase. Session private keys are
rejected from nonsecret configuration. `deposit_owner` is public identity metadata used only to verify the Deposit Wallet
derivation and bind journal identity. Owner/Builder credentials have no runtime
schema and are not needed for order execution.

## Signing implementation

The adapter constructs the venue's signature-type-3 order with Deposit Wallet as
both order maker and signer, then signs the documented nested `TypedDataSign`
with the Session EOA. It applies the ERC-7739 app-domain/contents/type wrapping
and the final Session Key ABI envelope/magic suffix. The CLOB request itself is
L2-authenticated as the Session EOA and identifies that session's API key as the
request owner.

No Polymarket SDK was added to the production dependency lock. Existing pinned
`eth-account`/`eth-abi` execution dependencies are used. A temporary user-owned
`viem` installation outside the Python/runtime environments generated an
independent deterministic vector. Python matches its fixed order hash, inner
signature, app-domain separator, contents hash, wrapped lengths and final SHA-256
digests.

A second cross-check pins the current official `Polymarket/py-sdk` source at
commit `579bb2e56be9cc5d152546985870ee6ad795ec52`. Its
`orders/typed_data.py`, `wallet.py`, `session_keys.py`, secure-client credential
bootstrap, and live integration test independently confirm signature type 3, the
`TypedDataSign`/ERC-7739 shape, the Session Key ABI envelope and `0x6492...` magic,
Deposit Wallet maker/signer identity, Session-signer L2 headers, derived CLOB
credentials, `CLOB` scope, and authorize/place/cancel/revoke Session Key flow.
The public authorization lifetime is described as 180 days; the exact v0.10.0 SDK
request uses 4,315 hours (179 days 19 hours), leaving a five-hour buffer. This
source check is compatibility evidence only; it does not replace real unfunded account acceptance.
`fetch_session_keys` is owner-only in that SDK. The executor therefore treats
`session_scopes` and `session_valid_until` as externally verified, protected
owner-device evidence; its own CLOB credentials cannot self-prove those grants.

## Reconciliation and session-private visibility

Authenticated CLOB orders/trades are intentionally treated as **session-scoped**.
The adapter also verifies wallet-wide public positions and on-chain balances and
adds a mandatory cursor-complete `/v2/activity?type=TRADE` witness. The current
Data API v2 contract was probed read-only from UpCloud before implementation:
`{data,pagination}`, opaque `next_cursor`, snake-case wallet/token/transaction
fields and `ASC` sorting were observed.

First Deposit reconciliation audits the served wallet history; later cycles use
a seven-day overlapping wallet activity window. Public TRADE rows are compared
as a multiset of transaction/condition/token/side identities against confirmed
trades visible to the configured Session Key. Any extra wallet-wide trade raises
sticky `EXTERNAL_WALLET_TRADE_ACTIVITY`. The public feed is never promoted into a
fill, order, cost or P&L record.

A wallet-wide public feed still cannot reveal an unfilled resting order owned by
another active Session Key. Therefore this adapter requires the external
single-session/dedicated-wallet commitment. The config-bound exclusivity expiry
closes openings automatically; it is an assertion under independent operator
custody, not cryptographic proof of absence. A second session/manual writer is a
configuration/operational violation even if public positions currently look
empty.

The financial journal is now durably bound to wallet type, wallet, Session signer
and signature type. Renewing authorization/expiry for the **same** Session signer
is compatible with that identity; silently replacing the Session signer is not.
A signer change fails before crash-recovery mutation because session-scoped CLOB
history could hide the previous signer’s orders/trades. Such rotation requires a
separately reviewed migration/recovery procedure rather than a config edit.

## Fail-closed behavior

Adversarial review reproduced two lifetime-boundary failures before correction:
openings remained possible during the last five minutes of the dedicated-session
exclusivity window, and a GTD maker order could be created with server expiration
after the safe Session/exclusivity boundary. Both reproductions are retained as
regressions. A shared 300-second safety window now applies to venue authorization
and exclusivity, and Deposit-session order preparation rejects any GTD lifetime
that crosses the earlier safe boundary. Non-finite expiry metadata is also rejected.

Opening authority closes when the Session authorization is within five minutes
of its configured venue expiry, when exclusivity is within the same safety window, when
session/wallet/type identity changes, when the wallet-wide activity witness is
missing/incomplete, or when external wallet TRADE activity is detected. Existing
unknown/open order reconciliation and cancellation semantics remain unchanged.

The direct-EOA redemption receipt importer is not generalized. Deposit execution
fails `record-redemption` explicitly until a smart-wallet/adapter receipt path is
separately implemented and reviewed.

Private account compatibility now has an explicit unfunded preflight mode.
`preflight --allow-unfunded` requires complete account/session reconciliation but
does not mislabel missing collateral or allowance as an account-identity failure.
It leaves `funding_ready=false`, `reconciled=false`, and financial authority false.
The ordinary `preflight` command remains strict and additionally requires both
collateral balance and allowance sufficient for at least `risk.per_order`.

## Verification to date

Final development-branch verification on 2026-09-17 used Python 3.12 under
`umask 0022`. The focused account/session/operator regression aggregate is
**239 passed**. The complete repository suite is **2073 passed, 4 warnings in
98.08s**; the four warnings are retained FastAPI `on_event` deprecations.
`compileall` and `git diff --check` pass.

The current official `Polymarket/py-sdk` `origin/main` independently refreshed to
`579bb2e56be9cc5d152546985870ee6ad795ec52`, the same source pinned for the
signature/session compatibility cross-check. Scanner/controller runtime import
isolation remains intact: `eth_account` and `eth_abi` are absent from the
non-execution review venv.

Adversarial development review additionally reproduced and corrected: the
Session/exclusivity near-expiry boundary and GTD lifetime leak; non-finite expiry
metadata; silent Session-signer journal rotation; populated legacy-journal
reinterpretation; misleading signals-only wallet UI; conflation of account
reconciliation with funding readiness; allowance-only funding readiness; and a
preflight path that could otherwise invoke exchange order management. Regression
tests retain those cases.

The installed finalized production release remains exactly
`3114657eff8c95fcf4116f42570ae02c8c3084a1`; scanner/controller are inactive and
disabled, no execution systemd service exists, and the Deposit-session branch has
not modified the installed host release, authority, account state, or funding.

This is development evidence, not a release approval. The remaining gates are an
exact committed candidate freeze, independent adversarial release review,
independent host-policy extension for the execution component, and real
**unfunded** account/Builder/Session acceptance before any deployment. Funding and
financial activation remain separate later decisions.
