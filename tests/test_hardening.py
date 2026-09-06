from polymarket_scanner.hardening import (
    hardened_binary_buy_both,
    hardened_neg_risk_underround,
    hardened_nested_threshold_arbitrage,
)
from polymarket_scanner.models import Book, Market


def market(
    mid: str,
    eid: str = "e",
    *,
    question: str = "Q?",
    yes: str | None = None,
    no: str | None = None,
    neg: bool = False,
    raw: dict | None = None,
    description: str = "Shared rules",
    source: str = "https://example.com/source",
    end: str = "2026-12-31T00:00:00Z",
) -> Market:
    yes = yes or f"y{mid}"
    no = no or f"n{mid}"
    return Market(
        id=mid,
        event_id=eid,
        event_slug="event",
        event_title="Event",
        event_neg_risk=neg,
        question=question,
        slug=f"m-{mid}",
        condition_id=f"c-{mid}",
        outcomes=["Yes", "No"],
        token_ids=[yes, no],
        outcome_prices=[0.5, 0.5],
        best_bid=None,
        best_ask=None,
        liquidity=1000,
        volume_24h=5000,
        active=True,
        closed=False,
        end_date=end,
        description=description,
        resolution_source=source,
        category="",
        tags=[],
        raw=raw or {},
    )


def test_binary_arb_is_not_marked_immediate_win():
    m = market("1")
    books = {
        "y1": Book("y1", [], [(0.40, 100)]),
        "n1": Book("n1", [], [(0.40, 100)]),
    }
    signals = hardened_binary_buy_both([m], books)
    assert len(signals) == 1
    assert signals[0].confidence == "ACTIONABLE"
    assert signals[0].metadata["immediate_settlement"] is False
    assert signals[0].metadata["max_visible_notional_usd"] > 10


def make_neg_rows(n: int, include_other: bool = True):
    children = []
    for i in range(n):
        label = "Other" if include_other and i == n - 1 else f"Candidate {i}"
        children.append({
            "id": str(i),
            "active": True,
            "closed": False,
            "question": f"Will {label} win?",
            "slug": f"will-{label.lower().replace(' ', '-')}-win",
            "negRiskMarketID": "nr-1",
        })
    event = {
        "id": "e",
        "negRisk": True,
        "negRiskMarketID": "nr-1",
        "description": "If no listed candidate wins, this market will resolve to Other.",
        "markets": children,
    }
    rows = []
    books = {}
    for i in range(n):
        raw = dict(children[i])
        raw["_event"] = event
        rows.append(market(str(i), neg=True, raw=raw, description=event["description"]))
        books[f"y{i}"] = Book(f"y{i}", [], [(0.20 if n >= 5 else 0.25, 100)])
    return rows, books


def test_neg_risk_requires_explicit_exhaustive_other_fallback():
    rows, books = make_neg_rows(3, include_other=False)
    signals = hardened_neg_risk_underround(rows, books)
    assert len(signals) == 1
    assert signals[0].confidence == "WATCH"
    assert "Other" in signals[0].metadata["certification_reason"]


def test_small_certified_neg_risk_can_be_actionable():
    rows, books = make_neg_rows(3, include_other=True)
    signals = hardened_neg_risk_underround(rows, books)
    assert len(signals) == 1
    assert signals[0].confidence == "ACTIONABLE"
    assert signals[0].metadata["certification_status"] == "NEG_RISK_CERTIFIED"
    assert signals[0].metadata["immediate_settlement"] is False


def test_large_neg_risk_basket_is_watch_for_manual_execution():
    rows, books = make_neg_rows(7, include_other=True)
    signals = hardened_neg_risk_underround(rows, books)
    assert len(signals) == 1
    assert signals[0].confidence == "WATCH"
    assert "manual-execution limit" in signals[0].metadata["certification_reason"]


def test_nested_threshold_requires_matching_rules_and_source():
    a = market("a", question="Will BTC be above $100?", yes="ya", no="na")
    b = market("b", question="Will BTC be above $200?", yes="yb", no="nb")
    books = {
        "ya": Book("ya", [], [(0.40, 100)]),
        "nb": Book("nb", [], [(0.40, 100)]),
    }
    signals = hardened_nested_threshold_arbitrage([a, b], books)
    assert len(signals) == 1
    assert signals[0].confidence == "ACTIONABLE"
    assert signals[0].metadata["certification_status"] == "NESTED_RULES_CERTIFIED"

    b.description = "Different settlement rules"
    signals = hardened_nested_threshold_arbitrage([a, b], books)
    assert len(signals) == 1
    assert signals[0].confidence == "WATCH"
