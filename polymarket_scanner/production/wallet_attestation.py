"""Narrow, read-only Deposit Wallet custody/code identity policy.

Pins were independently observed against verified deployed source at Polygon
block 0x59a1775 (2026-09-17). See docs/DEPOSIT_WALLET_CODE_POLICY.md.
An upgrade requires review and a new release, never automatic pin learning.
This is application validation, not independently privileged host authority.
"""
from __future__ import annotations

import time

from .chain import ChainReader, ExchangeError, address, keccak

FACTORY = "0x00000000000fb5c9adea0298d729a0cb3823cc07"
FACTORY_IMPLEMENTATION = "0x528cc05efac2b0d255e423272187efd41248abd7"
BEACON = "0x7a18edfe055488a3128f01f563e5b479d92ffc3a"
IMPLEMENTATION = "0xf7f27c29e60fe6325bef8da7f93250353d2e3294"
FORWARDER = "0x6dd7b5ea91608c60cd4a1944432cc30ee5a6d1ca"
ZERO = "0x" + "00" * 20
IMPLEMENTATION_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
BEACON_SLOT = "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50"
NATIVE_PREFIX = bytes.fromhex(
    "363d3d373d3d363d602036600436635c60da1b60e01b36527fa3f0ad74e5423aebfd80d3"
    "ef4346578335a9a72aeaee59ff6cb3582b35133d50545afa5036515af43d6000803e604d573d6000fd5b3d6000f3")
UUPS_PREFIX = bytes.fromhex(
    "363d3d373d3d363d7f360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505"
    "d382bbc545af43d6000803e6038573d6000fd5b3d6000f3")
CODE_KECCAK = {
    FACTORY: "aaa52c8cc8a0e3fd27ce756cc6b4e70c51423e9b597b11f32d3e49f8b1fc890d",
    FACTORY_IMPLEMENTATION: "e6424f1008e46b4b657efacf9500ea7747cbbf3055d9d76459253ac2884793d2",
    BEACON: "f87b06a1302051471df08ff79a938757509569e16b7a7efa55a3ea7b29b0b9d1",
    IMPLEMENTATION: "f5c1072460e64902af84d35f5bb1d0a15d80a88c5827b831a977fbc5a0684b96",
    FORWARDER: "af148766a50fdd614b055a0409af1309de0e9f25d38a7b6982eef13813043d7c",
}
MAX_ATTESTATION_SECONDS = 15


def abi_address(raw: bytes) -> str:
    if not isinstance(raw, bytes) or len(raw) != 32 or raw[:12] != bytes(12):
        raise ExchangeError("DEPOSIT_WALLET_ADDRESS_EVIDENCE_MALFORMED")
    return address("0x" + raw[12:].hex())


def attest_wallet(chain: ChainReader, wallet: str, owner: str, signer: str,
                  *, expected_proxy_type: str, clock=time.time) -> dict:
    """Attest one supported wallet and exact signer at a single canonical block.

    All RPCs are reads. Sequential reads cannot exclude later upgrades, other
    sessions, an off-chain revocation fence, or future operator/owner actions.
    """
    started, monotonic_started = clock(), time.monotonic()
    wallet, owner, signer = address(wallet), address(owner), address(signer)
    block = chain.block("latest")
    at = hex(block["number"])

    def read_address(target, signature, types=None, values=None, **kwargs):
        return abi_address(chain.call(target, signature, types or [], values or [], block=at, **kwargs))

    def require(condition, code):
        if not condition:
            raise ExchangeError(code)

    def code_identity(target):
        require(keccak(chain.code(target, block=at)).hex() == CODE_KECCAK[target],
                "DEPOSIT_WALLET_CODE_IDENTITY_DRIFT")

    for target in (FACTORY, FACTORY_IMPLEMENTATION, BEACON, IMPLEMENTATION):
        code_identity(target)
    require(abi_address(chain.storage(FACTORY, IMPLEMENTATION_SLOT, block=at)) == FACTORY_IMPLEMENTATION,
            "DEPOSIT_WALLET_FACTORY_IMPLEMENTATION_DRIFT")
    require(abi_address(chain.storage(FACTORY, BEACON_SLOT, block=at)) == ZERO,
            "DEPOSIT_WALLET_FACTORY_PROXY_UNSUPPORTED")
    require(read_address(FACTORY, "BEACON()") == BEACON, "DEPOSIT_WALLET_FACTORY_BEACON_DRIFT")

    immutable_id = bytes(12) + bytes.fromhex(owner[2:])
    immutable_args = bytes(12) + bytes.fromhex(FACTORY[2:]) + immutable_id
    runtime = chain.code(wallet, block=at)
    wallet_impl = abi_address(chain.storage(wallet, IMPLEMENTATION_SLOT, block=at))
    wallet_beacon = abi_address(chain.storage(wallet, BEACON_SLOT, block=at))
    if runtime == NATIVE_PREFIX + immutable_args:
        require(wallet_impl == ZERO and wallet_beacon == BEACON, "DEPOSIT_WALLET_NATIVE_PROXY_DRIFT")
        proxy_type = "NATIVE_BEACON"
    elif runtime == UUPS_PREFIX + immutable_args:
        require(wallet_impl == FORWARDER and wallet_beacon == ZERO,
                "DEPOSIT_WALLET_LEGACY_IMPLEMENTATION_UNSUPPORTED")
        code_identity(FORWARDER)
        require(read_address(FORWARDER, "BEACON()") == BEACON, "DEPOSIT_WALLET_FORWARDER_DRIFT")
        proxy_type = "UUPS_BEACON_FORWARDER"
    else:
        raise ExchangeError("DEPOSIT_WALLET_RUNTIME_OR_IMMUTABLE_IDENTITY_MISMATCH")

    require(proxy_type == expected_proxy_type, "DEPOSIT_WALLET_PROXY_DERIVATION_MISMATCH")

    default = read_address(BEACON, "defaultImplementation()")
    pinned = read_address(BEACON, "pinnedImplementation(address)", ["address"], [wallet])
    effective = read_address(BEACON, "implementation()", sender=wallet)
    require(default == IMPLEMENTATION and pinned in (ZERO, IMPLEMENTATION)
            and effective == (pinned if pinned != ZERO else default) == IMPLEMENTATION,
            "DEPOSIT_WALLET_BEACON_IMPLEMENTATION_DRIFT")
    require(read_address(wallet, "owner()") == owner, "DEPOSIT_WALLET_CURRENT_OWNER_MISMATCH")
    require(read_address(wallet, "factory()") == FACTORY, "DEPOSIT_WALLET_CURRENT_FACTORY_MISMATCH")
    require(chain.call(wallet, "id()", [], [], block=at) == immutable_id,
            "DEPOSIT_WALLET_CURRENT_ID_MISMATCH")
    require(chain.call(wallet, "walletInterfaceId()", [], [], block=at)
            == keccak(b"Polymarket.DepositWallet"), "DEPOSIT_WALLET_INTERFACE_MISMATCH")
    require(read_address(wallet, "pendingOwner()") == ZERO, "DEPOSIT_WALLET_OWNERSHIP_HANDOVER_PENDING")
    require(chain.call_uint(wallet, "paused()", [], [], block=at) == 0, "DEPOSIT_WALLET_PAUSED")
    require(chain.call_uint(FACTORY, "isOperator(address)", ["address"], [signer], block=at) == 0,
            "SESSION_SIGNER_FACTORY_OPERATOR_ROLE_FORBIDDEN")
    expiry = chain.call_uint(wallet, "sessionSignerAuthorizedUntil(address)", ["address"], [signer], block=at)
    chain.confirm_block(block)
    require(-30 <= clock() - block["timestamp"] <= 180, "STALE_CHAIN_BLOCK")
    require(0 <= clock() - started <= MAX_ATTESTATION_SECONDS
            and time.monotonic() - monotonic_started <= MAX_ATTESTATION_SECONDS,
            "DEPOSIT_WALLET_ATTESTATION_TOO_SLOW")
    return {"block": block, "proxy_type": proxy_type, "implementation": effective,
            "onchain_valid_until": expiry}
