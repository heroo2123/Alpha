"""Synthetic, integer-only legacy NegRisk conversion and binary merge model.

This module does not attest a deployed route, retrieve chain data, or publish
inventory/cash. A source-version match alone is never a transaction receipt.
"""
from __future__ import annotations

from dataclasses import dataclass


LEGACY_SOURCE_VERSION = "NEG_RISK_ADAPTER_SOURCE_LEGACY_V1"
LEGACY_MERGE_VERSION = "CONDITIONAL_TOKENS_BINARY_MERGE_V1"


@dataclass(frozen=True)
class ContractRoute:
    chain_id: int
    block_number: int
    adapter_address: str
    adapter_code_hash: str
    abi_version: str
    collateral_id: str
    conversion_version: str
    merge_version: str
    fee_bips: int
    verification: str = "SYNTHETIC_ONLY"

    def __post_init__(self) -> None:
        if (type(self.chain_id) is not int or self.chain_id <= 0
                or type(self.block_number) is not int or self.block_number < 0
                or type(self.fee_bips) is not int or not 0 <= self.fee_bips < 10_000
                or not all(isinstance(value, str) and value for value in
                           (self.adapter_address, self.adapter_code_hash,
                            self.abi_version, self.collateral_id))):
            raise ValueError("INVALID_CONTRACT_ROUTE")
        if (self.conversion_version != LEGACY_SOURCE_VERSION
                or self.merge_version != LEGACY_MERGE_VERSION
                or self.verification != "SYNTHETIC_ONLY"):
            raise ValueError("UNSUPPORTED_OR_UNATTESTED_ROUTE")


@dataclass(frozen=True)
class NegRiskTopologyProof:
    """Explicit synthetic ordered universe; not a live rule certificate."""

    ordered_condition_ids: tuple[str, ...]
    yes_token_ids: tuple[str, ...]
    no_token_ids: tuple[str, ...]
    collateral_id: str
    rule_identity: str
    universe_status: str = "SYNTHETIC_EXACTLY_ONE"

    def __post_init__(self) -> None:
        n = len(self.ordered_condition_ids)
        sequences = (self.ordered_condition_ids, self.yes_token_ids, self.no_token_ids)
        if (n < 2 or n > 256
                or any(not isinstance(sequence, tuple) for sequence in sequences)
                or len(self.yes_token_ids) != n or len(self.no_token_ids) != n
                or any(not isinstance(x, str) or not x for sequence in sequences for x in sequence)
                or len(set(self.ordered_condition_ids)) != n
                # Token identity is one global namespace: a YES token can never
                # equal a NO token, whether same-condition or cross-condition.
                or len(set(self.yes_token_ids) | set(self.no_token_ids)) != 2 * n
                or not self.collateral_id or not self.rule_identity
                or self.universe_status != "SYNTHETIC_EXACTLY_ONE"):
            raise ValueError("INVALID_OR_UNSUPPORTED_TOPOLOGY")


@dataclass(frozen=True)
class ConversionResult:
    consumed_no: tuple[tuple[str, int], ...]
    output_yes: tuple[tuple[str, int], ...]
    collateral_units: int
    fee_units_per_selected: int
    net_units: int
    proof_class: str = "SYNTHETIC_PROOF"
    account_effects: tuple[()] = ()


@dataclass(frozen=True)
class MergeResult:
    consumed_yes_token_id: str
    consumed_no_token_id: str
    consumed_units_each: int
    collateral_units: int
    proof_class: str = "SYNTHETIC_PROOF"
    account_effects: tuple[()] = ()


def _bind(route: ContractRoute, topology: NegRiskTopologyProof) -> None:
    if route.collateral_id != topology.collateral_id:
        raise ValueError("COLLATERAL_MISMATCH")


def simulate_conversion(route: ContractRoute, topology: NegRiskTopologyProof,
                        selected_mask: int, quantity: int,
                        available_no_units: tuple[int, ...]) -> ConversionResult:
    """Model q - floor(q * fee_bips / 10000) for each selected NO.

    The mask indexes the supplied ordered universe, and available units are
    synthetic caller inputs. This function never consumes a real lot.
    """
    _bind(route, topology)
    n = len(topology.ordered_condition_ids)
    if type(selected_mask) is not int or selected_mask <= 0 or selected_mask >= (1 << n):
        raise ValueError("INVALID_SELECTED_MASK")
    if type(quantity) is not int or quantity <= 0 or quantity > 10**30:
        raise ValueError("INVALID_QUANTITY")
    if (not isinstance(available_no_units, tuple) or len(available_no_units) != n
            or any(type(value) is not int or value < 0 for value in available_no_units)):
        raise ValueError("INVALID_ORDERED_INVENTORY")
    selected = tuple(i for i in range(n) if selected_mask & (1 << i))
    if any(available_no_units[i] < quantity for i in selected):
        raise ValueError("INSUFFICIENT_SYNTHETIC_INPUT")
    fee = quantity * route.fee_bips // 10_000
    net = quantity - fee
    return ConversionResult(
        tuple((topology.no_token_ids[i], quantity) for i in selected),
        tuple((topology.yes_token_ids[i], net) for i in range(n) if i not in selected),
        (len(selected) - 1) * net, fee, net)


def simulate_merge(route: ContractRoute, topology: NegRiskTopologyProof,
                   condition_index: int, quantity: int,
                   available_yes_units: int, available_no_units: int) -> MergeResult:
    """Model an equal YES/NO pair for one condition in the pinned collateral."""
    _bind(route, topology)
    if type(condition_index) is not int or not 0 <= condition_index < len(topology.ordered_condition_ids):
        raise ValueError("INVALID_CONDITION_INDEX")
    if (type(quantity) is not int or quantity <= 0 or quantity > 10**30
            or type(available_yes_units) is not int or type(available_no_units) is not int
            or min(available_yes_units, available_no_units) < quantity):
        raise ValueError("INVALID_OR_INSUFFICIENT_MERGE_INPUT")
    return MergeResult(topology.yes_token_ids[condition_index],
                       topology.no_token_ids[condition_index], quantity, quantity)
