import pytest

from polymarket_scanner.models import Market
from polymarket_scanner.production_gamma_bbo import gamma_screening_books
from polymarket_scanner.production_universe import (
    MATERIALIZED_HARD_CAP,
    PRODUCTION_UNIVERSE_FILTER_VERSION,
    ProductionPolymarketClient,
    market_matches_existing_detector,
)


def _binary(mid: str, question: str, *, bid=0.40, ask=0.42, **extra) -> dict:
    row = {
        "id": mid,
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "question": question,
        "slug": f"slug-{mid}",
        "conditionId": f"condition-{mid}",
        "outcomes": ["Yes", "No"],
        "clobTokenIds": [f"y-{mid}", f"n-{mid}"],
        "outcomePrices": [ask, 1 - ask],
        "bestBid": bid,
        "bestAsk": ask,
        "liquidityNum": 100,
        "volume24hr": 100,
        "spread": ask - bid,
    }
    row.update(extra)
    return row


def _event(*markets: dict, **extra) -> dict:
    row = {
        "id": "e1",
        "slug": "event-slug",
        "title": "Example event",
        "description": "Example rules",
        "resolutionSource": "official source",
        "markets": list(markets),
    }
    row.update(extra)
    return row


def test_irrelevant_open_binary_is_not_materialized():
    event = _event(_binary("m1", "Will an unrelated thing happen?"))
    assert market_matches_existing_detector(event, event["markets"][0]) is False


def test_existing_detector_lanes_are_retained_conservatively():
    threshold_row = _binary("threshold", "Will BTC be above $100000?")
    weather_row = _binary("weather", "Will the highest temperature in NYC be 80°F?")
    crypto_row = _binary(
        "crypto",
        "Will Bitcoin be up or down?",
        description="Resolves using Chainlink 60-second price.",
    )
    sports_row = _binary("sports", "Will the Lakers win?", sportsMarketType="moneyline")
    macro_row = _binary(
        "macro",
        "Will payrolls exceed 100k?",
        resolutionSource="https://www.bls.gov/official-release",
    )
    spread_row = _binary(
        "spread",
        "Will a liquid market resolve Yes?",
        bid=0.40,
        ask=0.55,
        spread=0.15,
        volume24hr=5000,
    )
    event = _event(threshold_row, weather_row, crypto_row, sports_row, macro_row, spread_row)
    assert all(market_matches_existing_detector(event, row) for row in event["markets"])


def test_unsupported_sports_scope_is_excluded_when_no_other_lane_matches():
    row = _binary("sport-period", "Will Lakers win in the first half?", sportsMarketType="moneyline")
    event = _event(row)
    assert market_matches_existing_detector(event, row) is False


def test_multi_lane_market_is_retained_if_any_existing_detector_can_consider_it():
    row = _binary("sport-threshold", "Will Lakers win the first half over 51.5?", sportsMarketType="moneyline")
    event = _event(row)
    assert market_matches_existing_detector(event, row) is True


def test_non_orderable_market_is_excluded_even_if_semantically_relevant():
    row = _binary("weather", "Will the highest temperature in NYC be 80°F?", acceptingOrders=False)
    event = _event(row)
    assert market_matches_existing_detector(event, row) is False


def _append(event: dict) -> list[Market]:
    client = object.__new__(ProductionPolymarketClient)
    client._discovered_market_count = 0
    client._materialized_market_count = 0
    client._fetch_reason = ""
    out: list[Market] = []
    client._append_detector_eligible_events(out, [event], set())
    return out


def test_small_neg_risk_parent_keeps_whole_child_set_for_downstream_proof():
    children = [
        _binary("a", "Candidate A", negRiskMarketID="parent"),
        _binary("b", "Candidate B", negRiskMarketID="parent"),
        _binary("c", "Other candidate", negRiskMarketID="parent"),
    ]
    event = _event(
        *children,
        negRisk=True,
        negRiskMarketID="parent",
        description="If no named candidate wins, resolution resolves to Other.",
    )
    out = _append(event)
    assert len(out) == 3
    parent = out[0].raw["_event"]
    assert len(parent["markets"]) == 3
    assert all(m.raw["_event"] is parent for m in out)


def test_small_neg_risk_parent_without_other_rule_is_not_retained_merely_for_flag():
    children = [
        _binary("a", "Candidate A", negRiskMarketID="parent"),
        _binary("b", "Candidate B", negRiskMarketID="parent"),
        _binary("c", "Candidate C", negRiskMarketID="parent"),
    ]
    event = _event(
        *children,
        negRisk=True,
        negRiskMarketID="parent",
        description="One named candidate will win.",
    )
    assert _append(event) == []


def test_small_neg_risk_parent_with_closed_child_is_not_retained_for_neg_risk():
    children = [
        _binary("a", "Candidate A", negRiskMarketID="parent"),
        _binary("b", "Candidate B", negRiskMarketID="parent"),
        _binary("c", "Other candidate", negRiskMarketID="parent", closed=True),
    ]
    event = _event(
        *children,
        negRisk=True,
        negRiskMarketID="parent",
        description="If no named candidate wins, resolution resolves to Other.",
    )
    assert _append(event) == []


def test_small_augmented_neg_risk_parent_is_not_retained_for_neg_risk():
    children = [
        _binary("a", "Candidate A", negRiskMarketID="parent"),
        _binary("b", "Candidate B", negRiskMarketID="parent"),
        _binary("c", "Other candidate", negRiskMarketID="parent"),
    ]
    event = _event(
        *children,
        negRisk=True,
        negRiskAugmented=True,
        negRiskMarketID="parent",
        description="If no named candidate wins, resolution resolves to Other.",
    )
    assert _append(event) == []


def test_large_neg_risk_parent_is_not_retained_merely_for_neg_risk_flag():
    children = [
        _binary(f"m{i}", f"Candidate {i}", negRiskMarketID="parent")
        for i in range(7)
    ]
    event = _event(*children, negRisk=True, negRiskMarketID="parent")
    assert _append(event) == []


def _market() -> Market:
    return Market(
        id="m1",
        event_id="e1",
        event_slug="event",
        event_title="Event",
        event_neg_risk=False,
        question="Will X happen?",
        slug="m1",
        condition_id="condition",
        outcomes=["Yes", "No"],
        token_ids=["yes-token", "no-token"],
        outcome_prices=[0.42, 0.58],
        best_bid=0.40,
        best_ask=0.42,
        liquidity=100.0,
        volume_24h=1000.0,
        active=True,
        closed=False,
        end_date=None,
        description="",
        resolution_source="",
        category="",
        tags=[],
        raw={},
    )


def test_gamma_screening_builds_binary_complement_and_marks_non_execution_source():
    books = gamma_screening_books([_market()], received_at=123.0)
    yes = books["yes-token"]
    no = books["no-token"]
    assert yes.best_bid == pytest.approx(0.40)
    assert yes.best_ask == pytest.approx(0.42)
    assert no.best_bid == pytest.approx(0.58)
    assert no.best_ask == pytest.approx(0.60)
    assert yes.source == "gamma_bbo_screening"
    assert no.source == "gamma_bbo_screening"
    assert yes.timestamp == "price-discovery"
    assert yes.received_at == 123.0
    assert yes.best_ask_size == 1.0


def test_production_universe_status_exposes_complete_vs_materialized_counts():
    client = object.__new__(ProductionPolymarketClient)
    client._active_cache = [_market()]
    client._active_cache_at = 100.0
    client._active_refresh_task = None
    client._active_cache_complete = True
    client._active_cache_reason = "complete"
    client._active_last_error = None
    client._active_last_attempt_at = 90.0
    client._active_last_success_at = 100.0
    client._discovered_market_count = 184815
    client._materialized_market_count = 19278
    client._keyset_page_count = 223
    status = client.universe_status(now=101.0)
    assert status["filter_version"] == PRODUCTION_UNIVERSE_FILTER_VERSION
    assert status["discovered_market_count"] == 184815
    assert status["materialized_market_count"] == 19278
    assert status["keyset_pages"] == 223
    assert status["materialized_hard_cap"] == MATERIALIZED_HARD_CAP
    assert status["safe_for_detection"] is True
