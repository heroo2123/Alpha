# Deposit Wallet code and authorization policy

This application policy is not independent host approval. Initial independent
review of b7a761786812fdeeab70790e01040a2346f0f492 rejected the global-beacon-only
check. The subsequent correction requires review of its own exact commit/tree.

## Source and observed identities

On 2026-09-17 at Polygon block 0x59a1775 / hash
0xa3c769c7df573008ca18f11a7bcf3154dfa3c220fa1de8c6473650d2c6e4046b,
an independent read-only review compared RPC runtime bytes against verified
PolygonScan deployed source for factory implementation, beacon, wallet logic,
and forwarder. Exact bytes, Keccak/SHA256 hashes and source-match metadata are
preserved in tests/fixtures/deposit-wallet-runtime-2026-09-17.json. The factory
proxy matches the verified Solady ERC1967 runtime. No real user wallet was used.

Official deployment inventory:
https://github.com/Polymarket/contract-security/blob/b6bdbd6232fb52191d082fb18602106ed18ebf11/README.md

Official SDK source (fresh main observation matched the historical reference):
https://github.com/Polymarket/py-sdk/commit/579bb2e56be9cc5d152546985870ee6ad795ec52

Deployed verified sources:
- https://polygonscan.com/address/0x528cc05efac2b0d255e423272187efd41248abd7#code
- https://polygonscan.com/address/0x7a18edfe055488a3128f01f563e5b479d92ffc3a#code
- https://polygonscan.com/address/0xf7f27c29e60fe6325bef8da7f93250353d2e3294#code
- https://polygonscan.com/address/0x6dd7b5ea91608c60cd4a1944432cc30ee5a6d1ca#code

## Required read-only evidence

`production/wallet_attestation.py` pins exact runtime hashes and addresses. All
reads use one fresh Polygon block and recheck its canonical hash/timestamp.
Incomplete, malformed, stale, slow (>15 seconds) or changed evidence rejects
openings. It verifies:

- Factory proxy runtime and implementation slot, implementation code, and beacon.
- Actual wallet runtime with exact immutable factory/owner-id arguments and
  matching deterministic address form; native beacon slots or migrated UUPS
  slots pointing to the reviewed BeaconForwarder. Unmigrated UUPS is unsupported.
- Default implementation, per-wallet pin, and effective `implementation()` with
  the actual wallet as `eth_call.from`. All must resolve consistently to reviewed
  logic. Global-default drift is rejected even if a wallet stays pinned.
- Current owner, factory, immutable id, interface magic, no pending ownership
  handover, and unpaused wallet. A recovered/transferred owner with a different
  immutable id needs a separately reviewed onboarding route.
- Session signer has no factory operator role; exact signer authorization expiry
  on the actual wallet. Configured expiry/exclusivity remain upper bounds with
  the existing 300-second safety margin; a later chain expiry never extends them.

RPC additions are only `eth_getCode` and `eth_getStorageAt`, plus a validated
public `from` field for `eth_call`. No send, approve, grant or signing RPC exists.
Changed addresses or code need explicit source review, regression tests and a
new release/configuration/host approval. Do not learn pins from network results.

## Submission timing and custody limits

The executor repeats wallet/Session attestation after preparation and inside the
actual submit worker. Local stop, durable control state, activation/configuration,
signal, confirmation, schedule and time checks follow network work. Prepared
order and book age are checked again immediately before the single transport
invocation. Known pre-transport rejection consumes the intent, records REJECTED
and releases its reservation. Once transport starts, ambiguity remains UNKNOWN
with its reservation; no automatic replay is allowed.

These sequential observations do not provide atomic exclusivity against later
upgrades/revocation/manual activity. Wallet public activity is only an isolation
witness, never a source of fills, fees, costs, settlement or P&L. Session-visible
CLOB resting-order visibility cannot prove another Session has no unfilled order.

The verified wallet `execute` is factory-only; factory `proxy` is operator-role
only. A Session key and gas alone cannot bypass these gates. An accepted factory
operator can relay a Session-signed batch of arbitrary external calls, however.
The chain grant is not a cryptographic CLOB-only or withdrawal-disabled grant.
Venue CLOB scope, off-chain revocation fence, chain expiry, exclusive-wallet owner
commitment and key/role separation are distinct evidence. Owner, Builder and
Relayer/Gasless material must remain off the executor. The official integration
test grants ALL and is not live proof of this release's narrower CLOB scope.
