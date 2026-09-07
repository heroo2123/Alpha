from polymarket_scanner.hardening import (
    _nested_certification,
    hardened_binary_buy_both,
    hardened_neg_risk_underround,
    hardened_nested_threshold_arbitrage,
)
from polymarket_scanner.models import Book, Market, Signal


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
    outcomes: list[str] | None = None,
    token_ids: list[str] | None = None,
    condition_id: str | None = None,
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
        condition_id=f"c-{mid}" if condition_id is None else condition_id,
        outcomes=outcomes if outcomes is not None else ["Yes", "No"],
        token_ids=token_ids if token_ids is not None else [yes, no],
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


def test_binary_proof_requires_exact_yes_no_labels_and_tokens():
    good = market("1")
    books = {
        "y1": Book("y1", [], [(0.40, 100)]),
        "n1": Book("n1", [], [(0.40, 100)]),
    }
    signals = hardened_binary_buy_both([good], books)
    assert len(signals) == 1
    assert signals[0].confidence == "ACTIONABLE"
    assert signals[0].metadata["certification_status"] == "BINARY_COMPLEMENT_PROOF_V2"
    assert signals[0].metadata["payoff_proof"]["yes_token"] == "y1"
    assert signals[0].metadata["immediate_settlement"] is False

    # Market.yes_token/no_token historically fell back to token position. That must
    # never be enough to certify a structural payout pair.
    bad = market("2", outcomes=["Team A", "Team B"], token_ids=["y2", "n2"])
    bad_books = {
        "y2": Book("y2", [], [(0.40, 100)]),
        "n2": Book("n2", [], [(0.40, 100)]),
    }
    signals = hardened_binary_buy_both([bad], bad_books)
    assert len(signals) == 1  # discovery can see it; hardening must reject it
    assert signals[0].confidence == "WATCH"
    assert signals[0].metadata["certification_status"] == "NOT_ACTIONABLE"
    assert "labels" in signals[0].metadata["certification_reason"].lower()


def make_neg_rows(n: int, include_other: bool = True, *, other_active: bool = True, augmented: bool = False):
    children = []
    for i in range(n):
        is_other = include_other and i == n - 1
        label = "Other" if is_other else f"Candidate {i}"
        children.append({
            "id": str(i),
            "active": other_active if is_other else True,
            "closed": (not other_active) if is_other else False,
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
    if augmented:
        event["negRiskAugmented"] = True
    rows = []
    books = {}
    ask = min(0.25, 0.70 / max(1, n))
    for i in range(n):
        # The scanner universe only contains active/open children.
        if children[i]["closed"] or not children[i]["active"]:
            continue
        raw = dict(children[i])
        raw["_event"] = event
        rows.append(market(str(i), neg=True, raw=raw, description=event["description"]))
        books[f"y{i}"] = Book(f"y{i}", [], [(ask, 100)])
    return rows, books


def test_neg_risk_requires_complete_open_parent_set_and_other_fallback():
    rows, books = make_neg_rows(3, include_other=False)
    signals = hardened_neg_risk_underround(rows, books)
    assert len(signals) == 1
    assert signals[0].confidence == "WATCH"
    assert "other" in signals[0].metadata["certification_reason"].lower()

    # Astra counterexample class: omitting a CLOSED child is unsafe even if it is
    # called Other. The omitted child could be the sole YES winner.
    rows, books = make_neg_rows(4, include_other=True, other_active=False)
    signals = hardened_neg_risk_underround(rows, books)
    assert len(signals) == 1
    assert signals[0].confidence == "WATCH"
    assert "inactive/closed" in signals[0].metadata["certification_reason"].lower()


def test_neg_risk_rejects_closed_named_child_even_when_other_is_open():
    rows, books = make_neg_rows(4, include_other=True)
    event = rows[0].raw["_event"]
    event["markets"][0]["active"] = False
    event["markets"][0]["closed"] = True
    # Simulate the normal universe shape: the closed child disappears from scanner rows.
    rows = [row for row in rows if row.id != "0"]
    books.pop("y0")

    signals = hardened_neg_risk_underround(rows, books)
    assert len(signals) == 1
    assert signals[0].confidence == "WATCH"
    assert "inactive/closed" in signals[0].metadata["certification_reason"].lower()


def test_neg_risk_rejects_missing_raw_child_state_instead_of_defaulting_open():
    rows, books = make_neg_rows(3, include_other=True)
    rows[0].raw["_event"]["markets"][0].pop("active")
    signals = hardened_neg_risk_underround(rows, books)
    assert len(signals) == 1
    assert signals[0].confidence == "WATCH"
    assert "incomplete active/closed state" in signals[0].metadata["certification_reason"].lower()


def test_augmented_neg_risk_is_not_payoff_certified():
    rows, books = make_neg_rows(3, include_other=True, augmented=True)
    signals = hardened_neg_risk_underround(rows, books)
    assert len(signals) == 1
    assert signals[0].confidence == "WATCH"
    assert "augmented" in signals[0].metadata["certification_reason"].lower()


def test_small_neg_risk_proof_includes_complete_parent_set_and_other_yes():
    rows, books = make_neg_rows(3, include_other=True)
    signals = hardened_neg_risk_underround(rows, books)
    assert len(signals) == 1
    assert signals[0].confidence == "ACTIONABLE"
    assert signals[0].metadata["certification_status"] == "NEG_RISK_COMPLETE_SET_PROOF_V3"
    proof = signals[0].metadata["payoff_proof"]
    assert proof["version"] == "neg_risk_complete_parent_set_v3"
    assert proof["other_market_id"] == "2"
    assert proof["other_yes_token"] == "y2"
    assert proof["parent_child_market_ids"] == ["0", "1", "2"]
    assert proof["all_parent_children_open"] is True
    assert proof["minimum_bundle_payout"] == 1.0
    assert "y2" in proof["purchased_yes_tokens"]
    assert signals[0].metadata["immediate_settlement"] is False


def test_large_neg_risk_basket_is_watch_for_manual_execution():
    rows, books = make_neg_rows(7, include_other=True)
    signals = hardened_neg_risk_underround(rows, books)
    assert len(signals) == 1
    assert signals[0].confidence == "WATCH"
    assert "manual-execution limit" in signals[0].metadata["certification_reason"]


def test_nested_above_proves_looser_yes_plus_stricter_no():
    a = market("a", question="Will BTC be above $100?", yes="ya", no="na")
    b = market("b", question="Will BTC be above $200?", yes="yb", no="nb")
    books = {
        "ya": Book("ya", [], [(0.40, 100)]),
        "nb": Book("nb", [], [(0.40, 100)]),
    }
    signals = hardened_nested_threshold_arbitrage([a, b], books)
    assert len(signals) == 1
    s = signals[0]
    assert s.confidence == "ACTIONABLE"
    assert s.metadata["certification_status"] == "NESTED_PAYOFF_PROOF_V2"
    proof = s.metadata["payoff_proof"]
    assert proof["looser_market_id"] == "a"
    assert proof["stricter_market_id"] == "b"
    assert proof["looser_yes_token"] == "ya"
    assert proof["stricter_no_token"] == "nb"
    assert proof["minimum_bundle_payout"] == 1.0


def test_nested_below_proves_correct_reverse_threshold_order():
    loose = market("a", question="Will BTC be below $200?", yes="ya", no="na")
    strict = market("b", question="Will BTC be below $100?", yes="yb", no="nb")
    books = {
        "ya": Book("ya", [], [(0.40, 100)]),
        "nb": Book("nb", [], [(0.40, 100)]),
    }
    signals = hardened_nested_threshold_arbitrage([loose, strict], books)
    assert len(signals) == 1
    assert signals[0].metadata["payoff_proof"]["looser_market_id"] == "a"
    assert signals[0].metadata["payoff_proof"]["stricter_market_id"] == "b"


def test_nested_rejects_mixed_comparator_semantics_even_if_legacy_template_matches():
    a = market("a", question="Will BTC be at least $100?", yes="ya", no="na")
    b = market("b", question="Will BTC be above $200?", yes="yb", no="nb")
    books = {
        "ya": Book("ya", [], [(0.40, 100)]),
        "nb": Book("nb", [], [(0.40, 100)]),
    }
    signals = hardened_nested_threshold_arbitrage([a, b], books)
    assert len(signals) == 1
    assert signals[0].confidence == "WATCH"
    assert "formally proven" in signals[0].metadata["certification_reason"].lower()


def test_nested_certificate_rejects_reversed_purchased_tokens_that_can_both_lose():
    a = market("a", question="Will BTC be above $100?", yes="ya", no="na")
    b = market("b", question="Will BTC be above $200?", yes="yb", no="nb")
    # Buying stricter YES + looser NO can both lose for 100 < BTC <= 200.
    wrong = Signal(
        detector="nested_threshold_arb",
        confidence="ACTIONABLE",
        event_id="e",
        market_id="b",
        title="wrong",
        detail="wrong",
        url="https://example.com",
        edge=0.1,
        entry_cost=0.8,
        theoretical_payout=1.0,
        token_ids=["yb", "na"],
        metadata={},
    )
    certified, reason, proof = _nested_certification(a, b, wrong)
    assert certified is False
    assert "looser yes plus stricter no" in reason.lower()
    assert proof["version"] == "nested_implication_v2"


def test_nested_requires_matching_rules_and_source():
    a = market("a", question="Will BTC be above $100?", yes="ya", no="na")
    b = market("b", question="Will BTC be above $200?", yes="yb", no="nb")
    books = {
        "ya": Book("ya", [], [(0.40, 100)]),
        "nb": Book("nb", [], [(0.40, 100)]),
    }
    b.description = "Different settlement rules"
    signals = hardened_nested_threshold_arbitrage([a, b], books)
    assert len(signals) == 1
    assert signals[0].confidence == "WATCH"
