# Selected production account path — Session Key beta (2026-09-18)

## Decision

For newly deployed Deposit Wallets, direct Owner/Poly1271 CLOB trading is kept
fail-closed until the venue can issue or validate a CLOB API key whose account
identity matches the Deposit Wallet. The production route is therefore the
already-implemented Deposit Wallet Session Key adapter.

This does not authorize deployment, funding or trading. It records the remaining
external account gate and prevents engineering from silently falling back to an
EOA or incompatible credential.

## Current Polymarket requirements

Current Polymarket documentation says all account wallets deployed on/after
2026-05-04 are Deposit Wallets. Existing account connection can use a Relayer API
key created in polymarket.com Settings -> API Keys -> Relayer API Keys. Session
Keys are scoped/time-limited EOAs and cannot withdraw from the Deposit Wallet.
Authorizing one currently requires a Builder API key, and during the beta that
Builder API key must be authorized for session-key management by Polymarket.

Once authorized, create one fresh Session EOA, authorize only the CLOB scope,
then create CLOB L2 credentials under the Session signer address. Alpha's Session
adapter requires the on-chain/venue authorization, the protected expiry, the
wallet owner/implementation attestation, and wallet-wide activity reconciliation
before openings. The Deposit Wallet Owner private key stays off the executor.

## Owner adapter fail-closed behavior

The Owner adapter remains in source for compatibility/review. Its L2 headers now
identify the Deposit Wallet. An EOA-bound key must fail authenticated preflight.
Do not restore `POLY_ADDRESS=owner_eoa` merely to make authenticated reads work;
that recreates the venue's signer/API-key mismatch at order submission.

## External gate

The only account-side production blocker is obtaining authorization to manage a
Session Key (or a future venue-supported Deposit-Wallet-bound Owner credential).
All server/release work should proceed unfunded and with execution masked until
that account gate is satisfied.
