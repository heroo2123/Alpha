from dataclasses import replace

import pytest

from polymarket_scanner.models import Book
from polymarket_scanner.weather_only_clob import WeatherMarketParameters
from polymarket_scanner.weather_only_contracts import WeatherBucket
from polymarket_scanner.weather_only_maker import (
    FairValueBand,
    propose_maker_bid,
    summarize_complete_set_inventory,
)
from polymarket_scanner.weather_only_maker_lifecycle import (
    ACTION_CANCEL,
    ACTION_EXPIRE,
    ACTION_KEEP,
    ACTION_REPRICE,
    ACTION_SHADOW_FILL,
    MakerLifecycleError,
    MakerLifecyclePolicy,
    PublicTradePrint,
    evaluate_virtual_maker_order,
    measure_shadow_fill_markout,
    open_virtual_maker_order,
    validate_virtual_maker_order,
)


def _bucket():
    return WeatherBucket(
        market_id="m1",
        condition_id="c1",
        question="Will the high be 70F?",
        slug="m1",
        lower=70,
        upper=70,
        unit="F",
        yes_token="yes-1",
        no_token="no-1",
        trade_open=True,
    )


def _parameters(*, received_at=100.0, tick=0.01, minimum=5.0, fee=0.05, taker_only=True):
    return WeatherMarketParameters(
        condition_id="c1",
        token_outcomes=(("yes-1", "Yes"), ("no-1", "No")),
        minimum_order_size=minimum,
        minimum_tick_size=tick,
        fee_rate=fee,
        fee_exponent=1,
        taker_only=taker_only,
        maker_base_fee_bps=0,
        taker_base_fee_bps=0,
        rfq_enabled=False,
        taker_delay_enabled=False,
        received_at=received_at,
    )


def _book(*, received_at=100.0, bid=0.20, ask=0.50, epoch=1, book_hash="h1", last=None):
    return Book(
        token_id="yes-1",
        bids=[(bid, 20.0)],
        asks=[(ask, 20.0)],
        last_trade_price=last,
        received_at=received_at,
        source="clob_exact_rest_v2",
        source_epoch=epoch,
        book_hash=book_hash,
    )


def _fair(*, as_of=100.0, lower=0.40, point=0.45, upper=0.50, model="cal-v1"):
    return FairValueBand("yes-1", lower, point, upper, model, as_of, True)


def _policy(**overrides):
    values = dict(
        policy_id="maker-shadow-policy-v1",
        minimum_edge=0.05,
        max_fair_age_seconds=900.0,
        max_book_age_seconds=10.0,
        max_parameter_age_seconds=30.0,
        max_order_age_seconds=1800.0,
        reprice_min_ticks=1,
    )
    values.update(overrides)
    return MakerLifecyclePolicy(**values)


def _open(*, now=100.0, fair=None, book=None, parameters=None, policy=None):
    fair = fair or _fair()
    book = book or _book()
    parameters = parameters or _parameters()
    policy = policy or _policy()
    proposal = propose_maker_bid(
        event_id="event-1",
        bucket=_bucket(),
        outcome="Yes",
        book=book,
        parameters=parameters,
        fair=fair,
        max_notional=10.0,
        minimum_conditional_edge=policy.minimum_edge,
    )
    assert proposal is not None
    order = open_virtual_maker_order(
        order_id="order-1",
        proposal=proposal,
        fair=fair,
        book=book,
        parameters=parameters,
        policy=policy,
        now=now,
    )
    return order, policy


def _trade(trade_id, price, *, at=105.0, size=10.0, epoch=1):
    return PublicTradePrint(
        trade_id=trade_id,
        token_id="yes-1",
        price=price,
        size=size,
        received_at=at,
        source="clob_exact_rest_v2",
        source_epoch=epoch,
    )


def test_virtual_order_freezes_policy_and_evidence_and_has_no_authority():
    order, policy = _open()
    assert order.bid_price == pytest.approx(0.35)
    assert order.actual_order is False
    assert order.financial_authority is False
    assert len(order.policy_sha256) == 64
    assert len(order.evidence_sha256) == 64
    validate_virtual_maker_order(order, policy)

    tampered = replace(order, bid_price=0.34)
    with pytest.raises(MakerLifecycleError) as raised:
        validate_virtual_maker_order(tampered, policy)
    assert raised.value.code == "MAKER_ORDER_EVIDENCE_DIGEST_MISMATCH"

    with pytest.raises(MakerLifecycleError) as raised:
        validate_virtual_maker_order(order, _policy(minimum_edge=0.06))
    assert raised.value.code == "MAKER_ORDER_POLICY_MISMATCH"


def test_open_requires_fresh_provenance_and_causal_fair_value():
    with pytest.raises(MakerLifecycleError) as raised:
        _open(book=Book("yes-1", [(0.2, 1)], [(0.5, 1)], received_at=100.0), now=100.0)
    assert raised.value.code == "MAKER_BOOK_PROVENANCE_MISSING"

    with pytest.raises(MakerLifecycleError) as raised:
        _open(fair=_fair(as_of=101.0), now=100.0)
    assert raised.value.code == "MAKER_OPEN_FAIR_NOT_FRESH"


def test_quote_touch_without_trade_never_becomes_shadow_fill():
    order, policy = _open()
    decision = evaluate_virtual_maker_order(
        order=order,
        current_book=_book(received_at=105.0, bid=0.34, ask=0.35, book_hash="h2"),
        fair=_fair(as_of=104.0),
        parameters=_parameters(received_at=105.0),
        public_trades=(),
        policy=policy,
        now=105.0,
    )
    assert decision.action == ACTION_KEEP
    assert decision.touch_observed is True
    assert decision.shadow_fill is None
    assert decision.actual_fill_authority is False


def test_equal_price_trade_is_queue_ambiguous_and_not_a_fill():
    order, policy = _open()
    decision = evaluate_virtual_maker_order(
        order=order,
        current_book=_book(received_at=105.0, bid=0.34, ask=0.36, book_hash="h2"),
        fair=_fair(as_of=104.0),
        parameters=_parameters(received_at=105.0),
        public_trades=(_trade("t-equal", order.bid_price, at=104.0),),
        policy=policy,
        now=105.0,
    )
    assert decision.action in {ACTION_KEEP, ACTION_REPRICE}
    assert decision.equal_price_trade_queue_ambiguous is True
    assert decision.shadow_fill is None


def test_strict_public_trade_through_creates_shadow_fill_only():
    order, policy = _open()
    decision = evaluate_virtual_maker_order(
        order=order,
        current_book=_book(received_at=105.0, bid=0.33, ask=0.37, book_hash="h2"),
        fair=_fair(as_of=104.0),
        parameters=_parameters(received_at=105.0),
        public_trades=(_trade("t-through", order.bid_price - 0.01, at=103.0),),
        policy=policy,
        now=105.0,
    )
    assert decision.action == ACTION_SHADOW_FILL
    fill = decision.shadow_fill
    assert fill is not None
    assert fill.virtual_fill_price == order.bid_price
    assert fill.shares == order.max_shares
    assert fill.actual_fill is False
    assert fill.inventory_eligible is False
    assert fill.financial_authority is False
    assert decision.actual_fill_authority is False
    assert decision.financial_authority is False

    # A simulated/public price-through fill must never enter actual-fill inventory.
    with pytest.raises(ValueError, match="fill type invalid"):
        summarize_complete_set_inventory(
            event_id="event-1",
            required_yes_tokens=("yes-1", "yes-2"),
            fills=[fill],  # type: ignore[list-item]
            exactly_one_outcome_proven=True,
        )


def test_public_trade_duplicate_conflict_fails_closed():
    order, policy = _open()
    with pytest.raises(MakerLifecycleError) as raised:
        evaluate_virtual_maker_order(
            order=order,
            current_book=_book(received_at=105.0, book_hash="h2"),
            fair=_fair(as_of=104.0),
            parameters=_parameters(received_at=105.0),
            public_trades=(
                _trade("same", 0.34, at=103.0),
                _trade("same", 0.33, at=103.0),
            ),
            policy=policy,
            now=105.0,
        )
    assert raised.value.code == "MAKER_PUBLIC_TRADE_ID_CONFLICT"


def test_trade_before_fair_invalidation_counts_but_trade_after_does_not():
    order, policy = _open()
    lost_fair = _fair(as_of=104.0, lower=0.39, point=0.41, upper=0.45)

    before = evaluate_virtual_maker_order(
        order=order,
        current_book=_book(received_at=106.0, book_hash="h2"),
        fair=lost_fair,
        parameters=_parameters(received_at=106.0),
        public_trades=(_trade("before", 0.34, at=103.0),),
        policy=policy,
        now=106.0,
    )
    assert before.action == ACTION_SHADOW_FILL

    after = evaluate_virtual_maker_order(
        order=order,
        current_book=_book(received_at=106.0, book_hash="h3"),
        fair=lost_fair,
        parameters=_parameters(received_at=106.0),
        public_trades=(_trade("after", 0.34, at=105.0),),
        policy=policy,
        now=106.0,
    )
    assert after.action == ACTION_CANCEL
    assert after.reason == "FAIR_LOWER_BOUND_EDGE_LOST"
    assert after.shadow_fill is None


def test_trade_before_parameter_change_counts_but_trade_after_does_not():
    order, policy = _open()
    changed = _parameters(received_at=104.0, tick=0.02)

    before = evaluate_virtual_maker_order(
        order=order,
        current_book=_book(received_at=106.0, book_hash="h2"),
        fair=_fair(as_of=103.0),
        parameters=changed,
        public_trades=(_trade("before-param", 0.34, at=103.5),),
        policy=policy,
        now=106.0,
    )
    assert before.action == ACTION_SHADOW_FILL

    after = evaluate_virtual_maker_order(
        order=order,
        current_book=_book(received_at=106.0, book_hash="h3"),
        fair=_fair(as_of=103.0),
        parameters=changed,
        public_trades=(_trade("after-param", 0.34, at=105.0),),
        policy=policy,
        now=106.0,
    )
    assert after.action == ACTION_CANCEL
    assert after.reason == "MARKET_PARAMETERS_CHANGED"


def test_expiry_is_causally_ordered_against_trade_through():
    policy = _policy(max_order_age_seconds=20.0)
    order, _ = _open(policy=policy)

    filled = evaluate_virtual_maker_order(
        order=order,
        current_book=_book(received_at=121.0, book_hash="h2"),
        fair=_fair(as_of=110.0),
        parameters=_parameters(received_at=121.0),
        public_trades=(_trade("pre-expiry", 0.34, at=119.0),),
        policy=policy,
        now=121.0,
    )
    assert filled.action == ACTION_SHADOW_FILL

    expired = evaluate_virtual_maker_order(
        order=order,
        current_book=_book(received_at=121.0, book_hash="h3"),
        fair=_fair(as_of=110.0),
        parameters=_parameters(received_at=121.0),
        public_trades=(_trade("post-expiry", 0.34, at=120.5),),
        policy=policy,
        now=121.0,
    )
    assert expired.action == ACTION_EXPIRE
    assert expired.shadow_fill is None


def test_source_epoch_regression_fails_closed():
    order, policy = _open()
    with pytest.raises(MakerLifecycleError) as raised:
        evaluate_virtual_maker_order(
            order=order,
            current_book=_book(received_at=105.0, epoch=0, book_hash="old"),
            fair=_fair(as_of=104.0),
            parameters=_parameters(received_at=105.0),
            policy=policy,
            now=105.0,
        )
    assert raised.value.code == "MAKER_BOOK_SOURCE_EPOCH_REGRESSION"


def test_reprice_only_when_new_fair_and_queue_allow_more_edge_safe_bid():
    order, policy = _open()
    decision = evaluate_virtual_maker_order(
        order=order,
        current_book=_book(received_at=105.0, bid=0.36, ask=0.55, book_hash="h2"),
        fair=_fair(as_of=104.0, lower=0.50, point=0.54, upper=0.58),
        parameters=_parameters(received_at=105.0),
        policy=policy,
        now=105.0,
    )
    assert decision.action == ACTION_REPRICE
    assert decision.suggested_bid == pytest.approx(0.37)
    assert decision.suggested_bid + policy.minimum_edge <= 0.50 + 1e-12
    assert decision.financial_authority is False


def test_stale_current_book_or_parameters_cancel_without_fill_claim():
    order, policy = _open()
    stale_book = evaluate_virtual_maker_order(
        order=order,
        current_book=_book(received_at=101.0, book_hash="h2"),
        fair=_fair(as_of=105.0),
        parameters=_parameters(received_at=105.0),
        public_trades=(_trade("maybe", 0.34, at=104.0),),
        policy=policy,
        now=120.0,
    )
    assert stale_book.action == ACTION_CANCEL
    assert stale_book.reason == "CURRENT_BOOK_NOT_FRESH"
    assert stale_book.shadow_fill is None

    stale_parameters = evaluate_virtual_maker_order(
        order=order,
        current_book=_book(received_at=120.0, book_hash="h3"),
        fair=_fair(as_of=119.0),
        parameters=_parameters(received_at=80.0),
        public_trades=(_trade("maybe-2", 0.34, at=110.0),),
        policy=policy,
        now=120.0,
    )
    assert stale_parameters.action == ACTION_CANCEL
    assert stale_parameters.reason == "CURRENT_PARAMETERS_NOT_FRESH"
    assert stale_parameters.shadow_fill is None


def test_shadow_fill_markout_is_quoted_research_not_realized_pnl():
    order, policy = _open()
    decision = evaluate_virtual_maker_order(
        order=order,
        current_book=_book(received_at=105.0, bid=0.33, ask=0.37, book_hash="h2"),
        fair=_fair(as_of=104.0),
        parameters=_parameters(received_at=105.0),
        public_trades=(_trade("fill", 0.34, at=103.0),),
        policy=policy,
        now=105.0,
    )
    fill = decision.shadow_fill
    assert fill is not None
    markout = measure_shadow_fill_markout(
        fill=fill,
        book=_book(received_at=165.0, bid=0.40, ask=0.44, book_hash="h3"),
        now=165.0,
    )
    assert markout.age_seconds == pytest.approx(62.0)
    assert markout.best_bid_markout_per_share == pytest.approx(0.05)
    assert markout.midpoint_markout_per_share == pytest.approx(0.07)
    assert markout.realized_pnl is False
    assert markout.financial_authority is False
