from polymarket_scanner.hardening import hardened_neg_risk_underround
from polymarket_scanner.models import Book
from polymarket_scanner.polymarket import PolymarketClient


def _raw_child(mid: str, question: str, yes: str, no: str) -> dict:
    return {
        "id": mid,
        "active": True,
        "closed": False,
        "question": question,
        "slug": f"slug-{mid}",
        "conditionId": f"condition-{mid}",
        "outcomes": ["Yes", "No"],
        "clobTokenIds": [yes, no],
        "outcomePrices": [0.30, 0.70],
        "bestBid": 0.29,
        "bestAsk": 0.30,
        "liquidityNum": 500,
        "volume24hr": 1000,
        "negRiskMarketID": "parent-neg-risk-id",
        "sportsMarketType": "moneyline",
        "spread": 0.01,
        "veryLargeUnusedPayload": "x" * 100_000,
    }


def _event() -> dict:
    children = [
        _raw_child("m1", "Candidate A", "y1", "n1"),
        _raw_child("m2", "Candidate B", "y2", "n2"),
        _raw_child("m3", "Other candidate", "y3", "n3"),
    ]
    return {
        "id": "event-1",
        "slug": "event-slug",
        "title": "Neg-risk event",
        "negRisk": True,
        "negRiskAugmented": False,
        "negRiskMarketID": "parent-neg-risk-id",
        "description": "If no named candidate wins, the resolution resolves to Other.",
        "resolutionSource": "official source",
        "category": "Politics",
        "markets": children,
        "veryLargeUnusedEventPayload": "y" * 100_000,
    }


def _materialized_markets():
    # _append_events is intentionally side-effect free with respect to the HTTP
    # client, so avoid constructing an AsyncClient in this unit test.
    client = object.__new__(PolymarketClient)
    out = []
    client._append_events(out, [_event()], set())
    return out


def test_gamma_materialization_compacts_raw_payload_and_shares_parent_event():
    markets = _materialized_markets()
    assert len(markets) == 3

    parent = markets[0].raw["_event"]
    assert all(m.raw["_event"] is parent for m in markets)

    assert "veryLargeUnusedEventPayload" not in parent
    assert "veryLargeUnusedPayload" not in markets[0].raw
    assert markets[0].raw["spread"] == 0.01
    assert markets[0].raw["sportsMarketType"] == "moneyline"
    assert markets[0].raw["negRiskMarketID"] == "parent-neg-risk-id"

    assert parent["description"].startswith("If no named candidate")
    assert parent["negRisk"] is True
    assert len(parent["markets"]) == 3
    assert set(parent["markets"][0]) <= {
        "id",
        "active",
        "closed",
        "question",
        "slug",
        "negRiskMarketID",
        "negRiskMarketId",
    }
    assert "clobTokenIds" not in parent["markets"][0]
    assert "veryLargeUnusedPayload" not in parent["markets"][0]


def test_compact_neg_risk_payload_preserves_hardened_payoff_certificate():
    markets = _materialized_markets()
    books = {}
    for i in range(1, 4):
        books[f"y{i}"] = Book(
            token_id=f"y{i}",
            bids=[(0.29, 100.0)],
            asks=[(0.30, 100.0)],
        )
        books[f"n{i}"] = Book(
            token_id=f"n{i}",
            bids=[(0.69, 100.0)],
            asks=[(0.70, 100.0)],
        )

    signals = hardened_neg_risk_underround(markets, books)
    assert len(signals) == 1
    signal = signals[0]
    assert signal.metadata["certification_status"] == "NEG_RISK_COMPLETE_SET_PROOF_V3"
    proof = signal.metadata["payoff_proof"]
    assert proof["neg_risk_market_id"] == "parent-neg-risk-id"
    assert proof["complete_parent_child_count"] == 3
    assert proof["other_market_id"] == "m3"
    assert proof["minimum_bundle_payout"] == 1.0
