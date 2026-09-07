from pathlib import Path

from polymarket_scanner.models import Signal
from polymarket_scanner.store import Store


def sig(detector: str, confidence: str, *, market_id: str = "m1", cost: float = 0.8, edge: float = 0.1) -> Signal:
    return Signal(
        detector=detector,
        confidence=confidence,
        event_id=f"e-{detector}",
        market_id=market_id,
        title=detector,
        detail="test",
        url="https://example.com",
        edge=edge,
        entry_cost=cost,
        theoretical_payout=1.0,
        token_ids=[f"t-{detector}"],
        metadata={},
    )


def test_audit_separates_scored_structural_and_research(tmp_path: Path):
    store = Store(str(tmp_path / "signals.db"))

    weather = store.save_signal(sig("weather_late_lock", "ACTIONABLE", cost=0.80))
    friend = store.save_signal(sig("weather_friend_lock", "WATCH", cost=0.95))
    structural = store.save_signal(sig("binary_buy_both", "ACTIONABLE", cost=0.70, edge=0.25))
    research = store.save_signal(sig("wide_spread", "WATCH", cost=0.50, edge=0.20))

    assert weather and friend and structural and research
    store.resolve(weather, True, 100.0)
    store.resolve(friend, False, 100.0)

    st = store.stats()
    audit = st["audit"]

    assert audit["all_alerts"] == 4
    assert audit["actionable"] == 2
    assert audit["watch"] == 2
    assert audit["directional_total"] == 2
    assert audit["directional_resolved"] == 2
    assert audit["directional_won"] == 1
    assert audit["directional_lost"] == 1
    assert audit["structural_actionable_unverified"] == 1
    assert audit["research_unscored"] == 1
    assert audit["experimental_total"] == 1
    assert audit["experimental_resolved"] == 1

    by = {row["detector"]: row for row in st["detectors"]}
    assert by["weather_late_lock"]["evidence"] == "RESOLUTION_SCORED"
    assert by["weather_friend_lock"]["evidence"] == "EXPERIMENTAL_RESOLUTION"
    assert by["binary_buy_both"]["evidence"] == "EXECUTION_UNVERIFIED"
    assert by["binary_buy_both"]["resolved"] == 0
    assert by["wide_spread"]["evidence"] == "RESEARCH_UNSCORED"

    # $100 at 0.80 wins +$25; friend experiment loses $100.
    assert round(audit["resolved_pnl"], 2) == -75.00


def test_open_directional_scores_friend_watch_but_not_structural_or_research(tmp_path: Path):
    store = Store(str(tmp_path / "signals.db"))

    weather = store.save_signal(sig("weather_late_lock", "ACTIONABLE", market_id="weather"))
    friend = store.save_signal(sig("weather_friend_lock", "WATCH", market_id="friend"))
    structural = store.save_signal(sig("neg_risk_underround", "ACTIONABLE", market_id="struct"))
    research = store.save_signal(sig("wide_spread", "WATCH", market_id="research"))

    assert weather and friend and structural and research
    rows = store.open_directional()
    ids = {row["id"] for row in rows}

    assert weather in ids
    assert friend in ids
    assert structural not in ids
    assert research not in ids
