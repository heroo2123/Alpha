from __future__ import annotations

import math

import pytest

from polymarket_scanner.models import Book
from polymarket_scanner.weather_only_clob import WeatherMarketParameters
from polymarket_scanner.weather_only_contracts import WeatherBucket
from polymarket_scanner.weather_only_maker import (
    ConfirmedFill,
    FairValueBand,
    propose_maker_bid,
    summarize_complete_set_inventory,
)


def _bucket() -> WeatherBucket:
    return WeatherBucket(
        market_id="m1",
        condition_id="c1",
        question="Will it be 70F?",
        slug="m1",
        lower=70.0,
        upper=70.0,
        unit="F",
        yes_token="yes-1",
        no_token="no-1",
        trade_open=True,
    )


def _params(*, fee_rate: float = 0.0, taker_only=None, minimum: float = 5.0) -> WeatherMarketParameters:
    return WeatherMarketParameters(
        condition_id="c1",
        token_outcomes=(("yes-1", "Yes"), ("no-1", "No")),
        minimum_order_size=minimum,
        minimum_tick_size=0.01,
        fee_rate=fee_rate,
        fee_exponent=1 if fee_rate else 0,
        taker_only=taker_only,
        maker_base_fee_bps=0,
        taker_base_fee_bps=0,
        rfq_enabled=False,
        taker_delay_enabled=False,
        received_at=100.0,
    )


def _fair(**overrides) -> FairValueBand:
    values = dict(
        token_id="yes-1",
        lower=0.38,
        point=0.43,
        upper=0.48,
        model_version="model-v1",
        as_of=100.0,
        calibrated=False,
    )
    values.update(overrides)
    return FairValueBand(**values)


def _book(*, bids=None, asks=None) -> Book:
    return Book(
        token_id="yes-1",
        bids=[(0.20, 10.0)] if bids is None else list(bids),
        asks=[(0.45, 10.0)] if asks is None else list(asks),
        received_at=100.0,
        source="clob_exact_rest_v2",
    )


def _proposal(*, params=None, book=None, budget=10.0):
    return propose_maker_bid(
        event_id="e1",
        bucket=_bucket(),
        outcome="Yes",
        book=book or _book(),
        parameters=params or _params(),
        fair=_fair(),
        max_notional=budget,
        minimum_conditional_edge=0.05,
    )


def test_fair_value_band_rejects_missing_identity_nonboolean_calibration_and_nonfinite_values():
    with pytest.raises(ValueError):
        _fair(token_id="")
    with pytest.raises(ValueError):
        _fair(model_version=" ")
    with pytest.raises(ValueError):
        _fair(calibrated=1)
    with pytest.raises(ValueError):
        _fair(as_of=-1.0)
    with pytest.raises(ValueError):
        _fair(lower=float("nan"))
    with pytest.raises(ValueError):
        _fair(upper=float("inf"))


def test_positive_dynamic_fee_with_unknown_maker_applicability_fails_closed():
    assert _proposal(params=_params(fee_rate=0.05, taker_only=None)) is None
    assert _proposal(params=_params(fee_rate=0.05, taker_only=False)) is None


def test_positive_dynamic_fee_explicitly_taker_only_and_zero_fee_are_maker_screen_eligible():
    positive_taker_only = _proposal(params=_params(fee_rate=0.05, taker_only=True))
    zero_fee = _proposal(params=_params(fee_rate=0.0, taker_only=None))
    assert positive_taker_only is not None
    assert zero_fee is not None
    assert positive_taker_only.financial_authority is False
    assert zero_fee.financial_authority is False


def test_minimum_order_size_is_not_invented_as_share_step():
    proposal = _proposal(params=_params(minimum=5.0), budget=10.0)
    assert proposal is not None
    assert proposal.bid_price == pytest.approx(0.33)
    assert proposal.max_shares == pytest.approx(10.0 / 0.33)
    assert proposal.max_shares >= 5.0
    assert proposal.max_shares / 5.0 != pytest.approx(round(proposal.max_shares / 5.0))
    assert proposal.max_notional == pytest.approx(10.0)


def test_maker_screen_fails_closed_on_missing_ask_crossed_book_and_identity_drift():
    assert _proposal(book=_book(asks=[])) is None
    assert _proposal(book=_book(bids=[(0.50, 1.0)], asks=[(0.45, 1.0)])) is None

    wrong_condition = _params()
    wrong_condition = WeatherMarketParameters(
        condition_id="other",
        token_outcomes=wrong_condition.token_outcomes,
        minimum_order_size=wrong_condition.minimum_order_size,
        minimum_tick_size=wrong_condition.minimum_tick_size,
        fee_rate=wrong_condition.fee_rate,
        fee_exponent=wrong_condition.fee_exponent,
        taker_only=wrong_condition.taker_only,
        maker_base_fee_bps=wrong_condition.maker_base_fee_bps,
        taker_base_fee_bps=wrong_condition.taker_base_fee_bps,
        rfq_enabled=wrong_condition.rfq_enabled,
        taker_delay_enabled=wrong_condition.taker_delay_enabled,
        received_at=wrong_condition.received_at,
    )
    assert _proposal(params=wrong_condition) is None

    wrong_token_book = Book("different", bids=[(0.20, 1.0)], asks=[(0.45, 1.0)])
    assert propose_maker_bid(
        event_id="e1",
        bucket=_bucket(),
        outcome="Yes",
        book=wrong_token_book,
        parameters=_params(),
        fair=_fair(),
        max_notional=10.0,
    ) is None


def _fills():
    return [
        ConfirmedFill("e", "a", "a-yes", "Yes", 10.0, 0.20, fee_usdc=0.10, fill_id="fa"),
        ConfirmedFill("e", "b", "b-yes", "Yes", 8.0, 0.25, fee_usdc=0.08, fill_id="fb"),
        ConfirmedFill("e", "c", "c-yes", "Yes", 12.0, 0.30, fee_usdc=0.12, fill_id="fc"),
    ]


def test_inventory_requires_unique_receipt_identity_and_yes_side_for_required_yes_token():
    missing = ConfirmedFill("e", "a", "a-yes", "Yes", 1.0, 0.20)
    with pytest.raises(ValueError, match="unique fill_id"):
        summarize_complete_set_inventory(
            event_id="e",
            required_yes_tokens=("a-yes",),
            fills=[missing],
            exactly_one_outcome_proven=True,
        )

    duplicate = [
        ConfirmedFill("e", "a", "a-yes", "Yes", 1.0, 0.20, fill_id="same"),
        ConfirmedFill("e", "a", "a-yes", "Yes", 1.0, 0.21, fill_id="same"),
    ]
    with pytest.raises(ValueError, match="duplicate fill_id"):
        summarize_complete_set_inventory(
            event_id="e",
            required_yes_tokens=("a-yes",),
            fills=duplicate,
            exactly_one_outcome_proven=True,
        )

    wrong_side = ConfirmedFill("e", "a", "a-yes", "No", 1.0, 0.20, fill_id="wrong-side")
    with pytest.raises(ValueError, match="non-YES"):
        summarize_complete_set_inventory(
            event_id="e",
            required_yes_tokens=("a-yes",),
            fills=[wrong_side],
            exactly_one_outcome_proven=True,
        )


def test_inventory_rejects_duplicate_required_token_and_nonboolean_semantic_proof():
    with pytest.raises(ValueError, match="duplicate required"):
        summarize_complete_set_inventory(
            event_id="e",
            required_yes_tokens=("a-yes", "a-yes"),
            fills=[],
            exactly_one_outcome_proven=True,
        )
    with pytest.raises(ValueError, match="must be boolean"):
        summarize_complete_set_inventory(
            event_id="e",
            required_yes_tokens=("a-yes",),
            fills=[],
            exactly_one_outcome_proven=1,
        )


def test_inventory_uses_unique_actual_fill_rows_including_fees_and_never_grants_authority():
    summary = summarize_complete_set_inventory(
        event_id="e",
        required_yes_tokens=("a-yes", "b-yes", "c-yes"),
        fills=_fills(),
        exactly_one_outcome_proven=True,
    )
    # Average all-in costs are .21, .26, .31, so each covered set costs .78.
    assert summary.complete_sets == pytest.approx(8.0)
    assert summary.complete_set_cost_basis == pytest.approx(0.78)
    assert summary.locked_cost_basis == pytest.approx(6.24)
    assert summary.locked_redemption_value == pytest.approx(8.0)
    assert summary.locked_pnl == pytest.approx(1.76)
    assert dict(summary.residual_shares) == pytest.approx({"a-yes": 2.0, "b-yes": 0.0, "c-yes": 4.0})
    assert summary.actual_fill_identity_proven is True
    assert summary.financial_authority is False
