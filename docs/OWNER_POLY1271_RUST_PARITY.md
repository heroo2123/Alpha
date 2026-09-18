# Owner / Poly1271 parity with official Rust V2 — 2026-09-18

This is offline compatibility evidence only. It does not authorize credential
export, funding, deployment, an order, or activation.

Official source inspected: `Polymarket/rs-clob-client-v2` commit
`222143d321eba97d5711a848265eb9aab3bc7ff4`. Its authentication builder creates
or derives API credentials from the Owner EOA signer and stores that EOA as the
authenticated L2 address. For Poly1271 orders, the funder/Deposit Wallet becomes
both V2 `maker` and `signer`; the Owner EOA signs the nested typed digest.

The official test `v2_poly1271_signing_matches_deposit_wallet_signature` publishes
a deterministic 317-byte expected signature. Alpha replays those exact public
order/domain fields without the published private key: the official 65-byte inner
signature recovers the known public fixture signer under Alpha's nested typed-data
digest, and Alpha's wrapper reproduces all 317 bytes exactly.

Therefore Owner mode intentionally keeps authenticated CLOB `POLY_ADDRESS` bound
to the Owner EOA while the type-3 order maker+signer is the Deposit Wallet. Do not
change L2 `POLY_ADDRESS` to the Deposit Wallet merely because some older clients
reported the server message `order signer address has to be the address of the API
KEY`; the official Rust implementation that introduced the correct nested Poly1271
signature uses the EOA-bound authentication model.

Real account acceptance still requires unfunded authenticated preflight with the
user's actual EOA-derived CLOB credentials, current wallet attestation, no unmanaged
activity/orders/positions, and no activation.
