import json
import time

from polymarket_scanner.models import Book, Market, Signal
from polymarket_scanner.sports_v3 import (
    SPORTS_MAPPING_VERSION,
    quarantine_pre_v3_sports_history,
    sports_result_lag_v3,
)
from polymarket_scanner.store import Store


def _market(question: str, *, raw=None) -> Market:
    return Market(
        id="m1",
        event_id="e1",
        event_slug="home-v-away",
        event_title="Home FC vs Away FC",
        event_neg_risk=False,
        question=question,
        slug="m1",
        condition_id="c1",
        outcomes=["Yes", "No"],
        token_ids=["y", "n"],
        outcome_prices=[0.5, 0.5],
        best_bid=0.7,
        best_ask=0.8,
        liquidity=1000,
        volume_24h=5000,
        active=True,
        closed=False,
        end_date="2026-09-08T20:00:00Z",
        description="Match winner rules",
        resolution_source="official sports source",
        category="Sports",
        tags=[],
        raw=raw or {},
    )


def _feed():
    return {
        "home-v-away": {
            "ended": True,
            "score": "3-2",
            "homeTeam": "Home FC",
            "awayTeam": "Away FC",
            "status": "final",
            "last_update": time.time(),
        }
    }


def _books():
    return {
        "y": Book("y", [], [(0.20, 50)]),
        "n": Book("n", [], [(0.20, 50)]),
    }


def test_unqualified_match_winner_still_passes_narrow_adapter():
    signals = sports_result_lag_v3([_market("Will Home FC win?")], _books(), _feed())
    assert len(signals) == 1
    assert signals[0].metadata["sports_mapping_version"] == SPORTS_MAPPING_VERSION
    assert signals[0].metadata["sports_semantic_scope"] == "UNQUALIFIED_MATCH_MONEYLINE_ONLY"


def test_regulation_overtime_extra_time_and_shootout_qualifiers_fail_closed():
    questions = [
        "Will Home FC win in regulation?",
        "Will Home FC win including overtime?",
        "Will Home FC win after overtime?",
        "Will Home FC win after extra time?",
        "Will Home FC win in a shootout?",
        "Will Home FC win on penalties?",
        "Will Home FC win in a penalty shootout?",
    ]
    for question in questions:
        assert sports_result_lag_v3([_market(question)], _books(), _feed()) == []


def test_draw_no_bet_and_two_way_qualified_markets_fail_closed():
    assert sports_result_lag_v3([_market("Will Home FC win? Draw No Bet")], _books(), _feed()) == []
    assert sports_result_lag_v3([_market("Will Home FC win? Two-way moneyline")], _books(), _feed()) == []


def test_raw_moneyline_type_does_not_override_unsafe_qualifier():
    market = _market("Will Home FC win in regulation?", raw={"sportsMarketType": "moneyline"})
    assert sports_result_lag_v3([market], _books(), _feed()) == []


def test_pre_v4_v3_history_is_quarantined_without_changing_forensic_pnl(tmp_path):
    store = Store(str(tmp_path / "signals.db"))
    old = Signal(
        detector="sports_result_lag_v3",
        confidence="ACTIONABLE",
        event_id="old-event",
        market_id="old-market",
        title="old v3 regulation candidate",
        detail="old",
        url="https://example.com",
        edge=0.1,
        entry_cost=0.8,
        theoretical_payout=1.0,
        token_ids=["old-token"],
        metadata={"sports_mapping_version": "home_away_v3_match_moneyline_only"},
    )
    signal_id = store.save_signal(old)
    assert signal_id is not None
    store.resolve(signal_id, True, 100.0)
    before = store.get_signal(signal_id)
    assert before["status"] == "WON"
    assert before["pnl"] == 25.0

    changed = quarantine_pre_v3_sports_history(store.path)
    assert changed == 1
    assert quarantine_pre_v3_sports_history(store.path) == 0

    after = store.get_signal(signal_id)
    assert after["detector"] == "sports_result_lag"
    assert after["status"] == "WON"
    assert after["pnl"] == 25.0
    meta = json.loads(after["metadata"])
    assert meta["sports_original_detector"] == "sports_result_lag_v3"
    assert meta["sports_original_mapping_version"] == "home_away_v3_match_moneyline_only"
    assert meta["sports_mapping_version"] == "PRE_V4_V3_QUARANTINED"

    audit = store.stats()["audit"]
    assert audit["known_bug_excluded"] >= 1
    assert audit["known_bug_resolved"] >= 1
