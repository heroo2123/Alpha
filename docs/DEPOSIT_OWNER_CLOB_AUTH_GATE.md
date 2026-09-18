# Deposit Wallet Owner CLOB authentication gate — 2026-09-18

This checkpoint is intentionally fail-closed. It does **not** authorize funding,
credential export, deployment or trading.

## Account observed publicly

- Deposit Wallet: `0x875ebe4c187a29a56283ab9ba04fe842e2196e2d`
- Current Owner EOA: `0x08cd34119571b01ae6fb2c8e3844be64720696ca`
- Official wallet derivation matches the Deposit Wallet.
- The corresponding legacy proxy and Safe addresses have no deployed code on the
  public Polygon probe, so this account cannot silently fall back to the legacy
  type-1/type-2 wallet path.

## Required CLOB identity

A type-3/Poly1271 order has the Deposit Wallet as maker and signer while the Owner
EOA provides the inner private signature. Consequently the L2 API credential used
by this adapter must authenticate with `POLY_ADDRESS` equal to the Deposit Wallet.
`ExchangeDepositOwner._headers()` enforces that identity. The application never
creates API keys, switches to the Owner EOA as L2 identity, or retries through a
different account.

Authenticated preflight remains the acceptance test: `/auth/api-keys`, closed-only
status, account history, positions/orders/activity, wallet attestation, reconciliation
and funding checks must all succeed under the protected Deposit Wallet identity.
Failure keeps openings closed.

## Upstream limitation under investigation

The official `Polymarket/rs-clob-client-v2` source inspected at commit
`561830b9ee502c6e67cce314f0e53a80b8885b09` documents `Poly1271` plus a funder,
but `authenticate()` calls `create_or_derive_api_key(self.signer, ...)`; the L1
header builder receives only the EOA signer before funder/signature type is stored
in authenticated state. Multiple open upstream reports describe API keys remaining
bound to the EOA and CLOB rejecting Poly1271 orders because the order signer is the
Deposit Wallet. These reports are external evidence, not a venue guarantee.

Do not export/store the Owner key or fund this account merely to test that upstream
limitation. Obtain a venue-supported Deposit-Wallet-bound credential method or
credential, then run the real account in unfunded preflight first.
