"""Offline replay of public, unrelated, already-executed redemption receipts."""
from copy import deepcopy
import json
from pathlib import Path

from polymarket_scanner.production import deposit_redemption as redeem
from polymarket_scanner.production import wallet_attestation as wallet_code
from polymarket_scanner.production.chain import ChainReader, ExchangeError, abi
from polymarket_scanner.production.exchange import _deposit_wallet_owner_forms
from test_production_exchange import Wire

ROOT = Path(__file__).parent / "fixtures"
DATA = ROOT / "deposit-redemption-2026-09-17"


def load(name):
    return json.loads((DATA / name).read_text())


class ReplayChain(ChainReader):
    def __init__(self, route):
        self.sample = load(route + "-sample-revalidated.json")
        self.derivation = load("token_derivation_report.json")["derivations"][route]
        self.wallet, self.owner = self.sample["wallet"], self.derivation["owner"]
        self.condition = self.derivation["condition"]
        self.route = route
        self.calls = []
        self.changed = False
        self.reorg_at_end = False
        self.numerators = (1, 0) if route == "standard" else (0, 1)
        self.codes = {address: bytes.fromhex(item["runtime_hex"][2:]) for address, item in
            json.loads((ROOT / "deposit-wallet-runtime-2026-09-17.json").read_text())["contracts"].items()}
        self.codes.update({item["address"]: bytes.fromhex(item["runtime"][2:])
                          for item in load("runtime-fixtures.json")["contracts"].values()})
        self.proxy = next(kind for kind, addr in _deposit_wallet_owner_forms(self.owner).items()
                          if addr == self.wallet)
        prefix = wallet_code.NATIVE_PREFIX if self.proxy == "NATIVE_BEACON" else wallet_code.UUPS_PREFIX
        self.codes[self.wallet] = prefix + abi(["address", "bytes32"],
            [redeem.FACTORY, bytes(12) + bytes.fromhex(self.owner[2:])])
        self.slots = {
            (redeem.FACTORY, wallet_code.IMPLEMENTATION_SLOT): wallet_code.FACTORY_IMPLEMENTATION,
            (redeem.FACTORY, wallet_code.BEACON_SLOT): redeem.ZERO,
            (self.wallet, wallet_code.IMPLEMENTATION_SLOT):
                redeem.ZERO if self.proxy == "NATIVE_BEACON" else wallet_code.FORWARDER,
            (self.wallet, wallet_code.BEACON_SLOT):
                wallet_code.BEACON if self.proxy == "NATIVE_BEACON" else redeem.ZERO,
            (redeem.PUSD, wallet_code.IMPLEMENTATION_SLOT): redeem.PUSD_IMPLEMENTATION}
        self.at = self.sample["receipt"]["blockNumber"]
        self.block_value = self.sample["canonical_block"]
        clock = lambda: int(self.sample["finalized_at_capture"]["timestamp"], 16)
        super().__init__(transport=Wire(), clock=clock)

    def rpc(self, method, params):
        self.calls.append((method, params))
        assert method in self.METHODS, "Mutation attempted in receipt replay"
        if method == "eth_chainId":
            return "0x89"
        if method == "eth_getTransactionReceipt":
            assert params == [self.sample["transaction"]["hash"]]
            return deepcopy(self.sample["receipt"])
        if method == "eth_getTransactionByHash":
            return deepcopy(self.sample["transaction"])
        if method == "eth_getBlockByNumber":
            if params[0] == "finalized":
                return deepcopy(self.sample["finalized_at_capture"])
            assert params[0] == self.at
            block = deepcopy(self.block_value)
            if self.changed:
                block["hash"] = "0x" + "44" * 32
            return block
        raise AssertionError((method, params))

    def code(self, target, *, block):
        assert block == self.at
        self.calls.append(("code", target))
        return self.codes[target]

    def storage(self, target, slot, *, block):
        assert block == self.at
        self.calls.append(("storage", target, slot))
        return abi(["address"], [self.slots[target, slot]])

    def call(self, target, signature, types, values, *, block="latest", sender=None):
        assert block == self.at
        self.calls.append(("call", target, signature))
        w = wallet_code
        addresses = {
            (redeem.FACTORY, "BEACON()"): w.BEACON,
            (w.FORWARDER, "BEACON()"): w.BEACON,
            (w.BEACON, "defaultImplementation()"): w.IMPLEMENTATION,
            (w.BEACON, "pinnedImplementation(address)"): redeem.ZERO,
            (w.BEACON, "implementation()"): w.IMPLEMENTATION,
            (self.wallet, "owner()"): self.owner,
            (self.wallet, "factory()"): redeem.FACTORY,
            (self.wallet, "pendingOwner()"): redeem.ZERO,
            (redeem.PUSD, "VAULT()"): redeem.VAULT,
            (redeem.PUSD, "USDCE()"): redeem.USDCE,
            (redeem.NEGATIVE, "NEG_RISK_ADAPTER()"): redeem.LEGACY,
            (redeem.NEGATIVE, "WRAPPED_COLLATERAL()"): redeem.WCOL,
            (redeem.LEGACY, "ctf()"): redeem.CTF,
            (redeem.LEGACY, "col()"): redeem.USDCE,
            (redeem.LEGACY, "wcol()"): redeem.WCOL,
            (redeem.WCOL, "underlying()"): redeem.USDCE,
            (redeem.WCOL, "owner()"): redeem.LEGACY}
        for adapter in (redeem.STANDARD, redeem.NEGATIVE):
            addresses.update({(adapter, "CONDITIONAL_TOKENS()"): redeem.CTF,
                              (adapter, "COLLATERAL_TOKEN()"): redeem.PUSD,
                              (adapter, "USDCE()"): redeem.USDCE})
        if (target, signature) in addresses:
            if signature == "implementation()":
                assert sender == self.wallet
            return abi(["address"], [addresses[target, signature]])
        if target == self.wallet and signature == "id()":
            return bytes(12) + bytes.fromhex(self.owner[2:])
        if target == self.wallet and signature == "walletInterfaceId()":
            return redeem.keccak(b"Polymarket.DepositWallet")
        if target == self.wallet and signature == "paused()":
            return bytes(32)
        if target == redeem.FACTORY and signature == "isOperator(address)":
            assert values == [self.sample["transaction"]["from"]]
            return abi(["uint256"], [1])
        assert target == redeem.CTF
        if signature == "getCollectionId(bytes32,bytes32,uint256)":
            assert values[:2] == [bytes(32), bytes.fromhex(self.condition[2:])]
            return bytes.fromhex(self.derivation["outcomes"][values[2] - 1]["collection_raw"][2:])
        if signature == "getPositionId(address,bytes32)":
            assert values[0] == self.derivation["collateral"]
            item = next(x for x in self.derivation["outcomes"] if bytes.fromhex(x["collection_raw"][2:]) == values[1])
            return abi(["uint256"], [int(item["token"])])
        if signature == "payoutNumerators(bytes32,uint256)":
            if values[1] == 1 and self.reorg_at_end:
                self.changed = True
            return abi(["uint256"], [self.numerators[values[1]]])
        if signature in ("getOutcomeSlotCount(bytes32)", "payoutDenominator(bytes32)"):
            return abi(["uint256"], [2 if signature.startswith("getOutcome") else 1])
        raise AssertionError((target, signature, types, values))
