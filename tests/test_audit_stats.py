from pathlib import Path

from polymarket_scanner.models import Signal
from polymarket_scanner.store import Store


def sig(detector: str, confidence: str, *, market_id: str = "m1", cost: float = 0.8, edge: float = 0.1, metadata=None) -> Signal:
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
        token_ids=[f"t-{detector}-{market_id}"],
        metadata=metadata or {},
    )


def test_audit_separates_valid_buggy_structural_experimental_and_research(tmp_path: Path):
    store = Store(str(tmp_path / "signals.db"))

    valid_sports = store.save_signal(sig(
        "sports_result_lag", "ACTIONABLE", market_id="sports-v2", cost=0.80,
        metadata={"sports_mapping_version": "home_away_v2"},
    ))
    buggy_sports = store.save_signal(sig("sports_result_lag", "ACTIONABLE", market_id="sports-old", cost=0.80))
    weather = store.save_signal(sig("weather_late_lock", "WATCH", market_id="weather", cost=0.80))
    friend = store.save_signal(sig("weather_friend_lock", "WATCH", market_id="friend", cost=0.95))
    structural = store.save_signal(sig("binary_buy_both", "ACTIONABLE", market_id="struct", cost=0.70, edge=0.25))
    research = store.save_signal(sig("wide_spread", "WATCH", market_id="research", cost=0.50, edge=0.20))

    assert all((valid_sports, buggy_sports, weather, friend, structural, research))
    store.resolve(valid_sports, True, 100.0)
    store.resolve(buggy_sports, False, 100.0)
    store.resolve(weather, False, 100.0)
    store.resolve(friend, True, 100.0)

    st = store.stats()
    audit = st["audit"]

    assert audit["all_alerts"] == 6
    assert audit["actionable"] == 3
    assert audit["watch"] == 3

    # Only the versioned sports detector is valid scored strategy evidence.
    assert audit["directional_total"] == 1
    assert audit["directional_resolved"] == 1
    assert audit["directional_won"] == 1
    assert audit["directional_lost"] == 0
    assert round(audit["resolved_pnl"], 2) == 25.00

    # Both weather lanes remain experimental until calibrated.
    assert audit["experimental_total"] == 2
    assert audit["experimental_resolved"] == 2
    assert audit["experimental_won"] == 1
    assert audit["experimental_lost"] == 1

    # Old sports implementation is preserved but explicitly excluded.
    assert audit["known_bug_excluded"] == 1
    assert audit["known_bug_resolved"] == 1
    assert round(audit["known_bug_pnl"], 2) == -100.00

    assert audit["structural_actionable_unverified"] == 1
    assert audit["research_unscored"] == 1

    by = {row["detector"]: row for row in st["detectors"]}
    assert by["sports_result_lag"]["evidence"] == "RESOLUTION_SCORED"
    assert by["sports_result_lag_PRE_FIX_BUG"]["evidence"] == "KNOWN_BUG_EXCLUDED"
    assert by["weather_late_lock"]["evidence"] == "EXPERIMENTAL_RESOLUTION"
    assert by["weather_friend_lock"]["evidence"] == "EXPERIMENTAL_RESOLUTION"
    assert by["binary_buy_both"]["evidence"] == "EXECUTION_UNVERIFIED"
    assert by["wide_spread"]["evidence"] == "RESEARCH_UNSCORED"


def test_open_directional_scores_valid_sports_and_weather_experiments_only(tmp_path: Path):
    store = Store(str(tmp_path / "signals.db"))

    valid_sports = store.save_signal(sig(
        "sports_result_lag", "ACTIONABLE", market_id="sports-v2",
        metadata={"sports_mapping_version": "home_away_v2"},
    ))
    old_sports = store.save_signal(sig("sports_result_lag", "ACTIONABLE", market_id="sports-old"))
    weather = store.save_signal(sig("weather_late_lock", "WATCH", market_id="weather"))
    friend = store.save_signal(sig("weather_friend_lock", "WATCH", market_id="friend"))
    structural = store.save_signal(sig("neg_risk_underround", "ACTIONABLE", market_id="struct"))
    research = store.save_signal(sig("wide_spread", "WATCH", market_id="research"))

    assert all((valid_sports, old_sports, weather, friend, structural, research))
    rows = store.open_directional()
    ids = {row["id"] for row in rows}

    assert valid_sports in ids
    assert old_sports not in ids
    assert weather in ids
    assert friend in ids
    assert structural not in ids
    assert research not in ids