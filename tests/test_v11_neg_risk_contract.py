"""Synthetic integer mechanics only; these tests do not verify deployment."""
from dataclasses import FrozenInstanceError

import pytest

from polymarket_scanner.v11.neg_risk_contract import (
    ContractRoute, LEGACY_MERGE_VERSION, LEGACY_SOURCE_VERSION,
    NegRiskTopologyProof, simulate_conversion, simulate_merge,
)


def setup(n=11, fee_bips=0):
    route = ContractRoute(137, 1, "synthetic-adapter", "synthetic-code-hash", "legacy-abi",
                          "synthetic-usdce", LEGACY_SOURCE_VERSION, LEGACY_MERGE_VERSION,
                          fee_bips)
    topology = NegRiskTopologyProof(tuple(f"condition-{i}" for i in range(n)),
                                    tuple(f"yes-{i}" for i in range(n)),
                                    tuple(f"no-{i}" for i in range(n)),
                                    "synthetic-usdce", "synthetic-exactly-one")
    return route, topology


def test_k_one_n_minus_one_and_n_integer_outputs():
    route, topology = setup()
    available = (5,) * 11
    one = simulate_conversion(route, topology, 1, 5, available)
    assert one.collateral_units == 0 and len(one.output_yes) == 10
    ten = simulate_conversion(route, topology, (1 << 11) - 2, 5, available)
    assert ten.collateral_units == 45
    assert ten.output_yes == (("yes-0", 5),)
    merge = simulate_merge(route, topology, 0, 5, 5, 5)
    assert merge.collateral_units == 5
    assert merge.consumed_yes_token_id == "yes-0" and merge.consumed_no_token_id == "no-0"
    eleven = simulate_conversion(route, topology, (1 << 11) - 1, 5, available)
    assert eleven.collateral_units == 50 and eleven.output_yes == ()
    assert ten.proof_class == merge.proof_class == "SYNTHETIC_PROOF"
    assert ten.account_effects == merge.account_effects == ()
    with pytest.raises(FrozenInstanceError):
        ten.collateral_units = 100


def test_fee_rounds_down_in_integer_units():
    route, topology = setup(3, fee_bips=333)
    below = simulate_conversion(route, topology, 0b011, 30, (30, 30, 0))
    assert below.fee_units_per_selected == 0 and below.net_units == 30
    at_boundary = simulate_conversion(route, topology, 0b011, 31, (31, 31, 0))
    assert at_boundary.fee_units_per_selected == 1 and at_boundary.net_units == 30
    assert at_boundary.collateral_units == 30
    assert at_boundary.output_yes == (("yes-2", 30),)


@pytest.mark.parametrize("mask,quantity,available", [
    (0, 5, (5, 5, 5)), (8, 5, (5, 5, 5)), (-1, 5, (5, 5, 5)),
    (1, 0, (5, 5, 5)), (1, 5, (4, 5, 5)), (1, 5, (5, 5)),
])
def test_invalid_masks_quantity_and_ordered_input_fail(mask, quantity, available):
    route, topology = setup(3)
    with pytest.raises(ValueError):
        simulate_conversion(route, topology, mask, quantity, available)


def test_cross_side_and_same_side_token_aliases_are_rejected():
    """IT-R2: a YES token can never equal a NO token, same-condition or cross-condition."""
    with pytest.raises(ValueError, match="INVALID_OR_UNSUPPORTED_TOPOLOGY"):
        NegRiskTopologyProof(("c0", "c1"), ("n0", "y1"), ("n0", "n1"),
                            "synthetic-usdce", "rules")
    with pytest.raises(ValueError, match="INVALID_OR_UNSUPPORTED_TOPOLOGY"):
        NegRiskTopologyProof(("c0", "c1", "c2"), ("y0", "y1", "n2"), ("n0", "n1", "n2"),
                            "synthetic-usdce", "rules")


def test_topology_rejects_mutable_sequences_and_cannot_be_mutated_after_validation():
    """IT-R2: lists must be refused at construction, and a validated topology's
    tuples cannot be altered afterward, closing the alias-after-validation defect."""
    with pytest.raises(ValueError, match="INVALID_OR_UNSUPPORTED_TOPOLOGY"):
        NegRiskTopologyProof(("c0", "c1", "c2"), ("y0", "y1", "y2"),
                            ["n0", "n1", "n2"], "synthetic-usdce", "rules")
    _, topology = setup(3)
    with pytest.raises(TypeError):
        topology.no_token_ids[1] = "n0"


def test_route_topology_and_merge_mismatches_fail_closed():
    route, topology = setup(3)
    with pytest.raises(ValueError, match="UNSUPPORTED_OR_UNATTESTED_ROUTE"):
        ContractRoute(137, 1, "adapter", "hash", "abi", "collateral", "UNKNOWN",
                      LEGACY_MERGE_VERSION, 0)
    with pytest.raises(ValueError, match="UNSUPPORTED_OR_UNATTESTED_ROUTE"):
        ContractRoute(137, 1, "adapter", "hash", "abi", "collateral",
                      LEGACY_SOURCE_VERSION, LEGACY_MERGE_VERSION, 0, "DEPLOYED_ATTESTED")
    with pytest.raises(ValueError, match="INVALID_OR_UNSUPPORTED_TOPOLOGY"):
        NegRiskTopologyProof(("a", "a"), ("y0", "y1"), ("n0", "n1"),
                             "synthetic-usdce", "rules")
    with pytest.raises(ValueError, match="INVALID_OR_UNSUPPORTED_TOPOLOGY"):
        NegRiskTopologyProof(("a", "b"), ("y0", "y1"), ("n0", "n1"),
                             "synthetic-usdce", "rules", "ALL_FALSE_POSSIBLE")
    wrong_collateral = NegRiskTopologyProof(topology.ordered_condition_ids,
                                           topology.yes_token_ids, topology.no_token_ids,
                                           "other-collateral", "rules")
    with pytest.raises(ValueError, match="COLLATERAL_MISMATCH"):
        simulate_conversion(route, wrong_collateral, 1, 5, (5, 5, 5))
    with pytest.raises(ValueError, match="INVALID_OR_INSUFFICIENT_MERGE_INPUT"):
        simulate_merge(route, topology, 0, 5, 5, 4)
    with pytest.raises(ValueError, match="INVALID_CONDITION_INDEX"):
        simulate_merge(route, topology, 3, 5, 5, 5)
