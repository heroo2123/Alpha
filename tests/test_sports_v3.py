import json
import sqlite3

from polymarket_scanner.models import Book, Market, Signal
from polymarket_scanner.sports_v3 import (
    SPORTS_DETECTOR,
    SPORTS_MAPPING_VERSION,
    quarantine_pre_v3_sports_history,
    sports_result_lag_v3,
)
from polymarket_scanner.store import Store


def mkt(question: str, *, mid="m1", event_slug="home-v-away", raw=None, title="Home FC vs Away FC") -> Market:
    return Market(
        id=mid,
        event_id="e1",
        event_slug=event_slug,
        event_title=title,
        event_neg_risk=False,
        question=question,
        slug=mid,
        condition_id=f"c-{mid}",
        outcomes=["Yes", "No"],
        token_ids=[f"y-{mid}", f"n-{mid}"],
        outcome_prices=[0.5, 0.5],
        best_bid=0.7,
        best_ask=0.8,
        liquidity=1000,
        volume_24h=5000,
        active=True,
        closed=False,
        end_date="2026-09-07T20:00:00Z",
        description="Match winner rules",
        resolution_source="official sports source",
        category="Sports",
        tags=[],
        raw=raw or {},
    )


def feed(**extra):
    payload = {
        "slug": "home-v-away",
        "ended": True,
        "score": "3-1",
        "homeTeam": "Home FC",
        "awayTeam": "Away FC",
        "status": "final",
    }
    payload.update(extra)
    return {"home-v-away": payload}


def test_v3_match_moneyline_uses_explicit_home_away_and_clean_version():
    m = mkt("Will Away FC win?", title="Away FC vs Home FC")
    books = {"n-m1": Book("n-m1", [], [(0.80, 50)])}
    signals = sports_result_lag_v3([m], books, feed())
    assert len(signals) == 1
    s = signals[0]
    assert s.detector == SPORTS_DETECTOR
    assert s.metadata["sports_mapping_version"] == SPORTS_MAPPING_VERSION
    assert s.metadata["trade_outcome"] == "NO"
    assert s.token_ids == ["n-m1"]
    assert "home-away" in s.metadata["sports_reason"]


def test_v3_rejects_spreads_even_when_team_winner_is_known():
    m = mkt("Will Home FC (-2.5) win?", raw={"sportsMarketType": "spread"})
    books = {"y-m1": Book("y-m1", [], [(0.20, 50)])}
    assert sports_result_lag_v3([m], books, feed()) == []


def test_v3_rejects_period_set_game_and_total_contracts():
    questions = [
        "Will Home FC win Set 1?",
        "Will Home FC win Game 2?",
        "Will Home FC win the first half?",
        "Home FC vs Away FC: Over 2.5 total goals?",
    ]
    for i, question in enumerate(questions):
        m = mkt(question, mid=f"m{i}")
        books = {
            f"y-m{i}": Book(f"y-m{i}", [], [(0.20, 50)]),
            f"n-m{i}": Book(f"n-m{i}", [], [(0.20, 50)]),
        }
        assert sports_result_lag_v3([m], books, feed()) == []


def test_v3_rejects_cancelled_postponed_and_ambiguous_ties():
    m = mkt("Will Home FC win?")
    books = {"y-m1": Book("y-m1", [], [(0.20, 50)]), "n-m1": Book("n-m1", [], [(0.20, 50)])}
    assert sports_result_lag_v3([m], books, feed(cancelled=True)) == []
    assert sports_result_lag_v3([m], books, feed(status="postponed")) == []
    assert sports_result_lag_v3([m], books, feed(score="1-1")) == []


def test_v3_rejects_missing_or_ambiguous_team_mapping():
    m = mkt("Will Mystery Club win?")
    books = {"y-m1": Book("y-m1", [], [(0.20, 50)])}
    assert sports_result_lag_v3([m], books, feed()) == []

    both = mkt("Will Home FC or Away FC win?")
    assert sports_result_lag_v3([both], books, feed()) == []


def test_pre_v3_history_is_quarantined_without_erasing_forensic_pnl(tmp_path):
    db = str(tmp_path / "signals.db")
    store = Store(db)
    old = Signal(
        detector="sports_result_lag",
        confidence="ACTIONABLE",
        event_id="e-old",
        market_id="m-old",
        title="old sports",
        detail="old",
        url="https://example.com",
        edge=0.1,
        entry_cost=0.8,
        theoretical_payout=1.0,
        token_ids=["old-token"],
        metadata={"sports_mapping_version": "home_away_v2"},
    )
    old_id = store.save_signal(old)
    assert old_id is not None
    store.resolve(old_id, False, 100.0)

    changed = quarantine_pre_v3_sports_history(db)
    assert changed == 1
    # Idempotent migration.
    assert quarantine_pre_v3_sports_history(db) == 0

    row = store.get_signal(old_id)
    assert row is not None
    meta = json.loads(row["metadata"])
    assert meta["sports_mapping_version"] == "PRE_V3_QUARANTINED"
    assert row["status"] == "LOST"
    assert row["pnl"] == -100.0

    audit = store.stats()["audit"]
    assert audit["known_bug_excluded"] == 1
    assert audit["known_bug_resolved"] == 1
    assert audit["known_bug_pnl"] == -100.0
