from __future__ import annotations

from datetime import datetime, timedelta, timezone

from polymarket_scanner.backpressure import coalesce_signal_batches, signal_episode_key
from polymarket_scanner.models import Signal


def sig(
    key: str,
    *,
    confidence: str = "WATCH",
    edge: float | None = None,
    seconds: int = 0,
    detector: str = "test",
) -> Signal:
    return Signal(
        detector=detector,
        confidence=confidence,
        event_id=f"event-{key}",
        market_id=f"market-{key}",
        title=key,
        detail=key,
        url="https://example.com",
        edge=edge,
        entry_cost=None,
        theoretical_payout=None,
        token_ids=[f"token-{key}"],
        metadata={"fingerprint_key": key},
        created_at=datetime(2026, 9, 7, tzinfo=timezone.utc) + timedelta(seconds=seconds),
    )


def test_duplicate_episode_keeps_newest_observation():
    old = sig("same", confidence="ACTIONABLE", edge=0.03, seconds=1)
    new = sig("same", confidence="ACTIONABLE", edge=0.04, seconds=2)

    batch, stats = coalesce_signal_batches([[old], [new]])
    assert len(batch) == 1
    assert batch[0] is new
    assert stats["duplicate_episodes_coalesced"] == 1
    assert stats["unique_actionable_retained"] == 1


def test_unique_actionable_episodes_are_never_capped_by_watch_limit():
    actionables = [
        sig(f"a{i}", confidence="ACTIONABLE", edge=0.03 + i / 1000, seconds=i)
        for i in range(25)
    ]
    watches = [sig(f"w{i}", confidence="WATCH", edge=i / 1000, seconds=i) for i in range(30)]

    batch, stats = coalesce_signal_batches([actionables + watches], watch_limit=3)
    kept_actionable = [s for s in batch if s.confidence == "ACTIONABLE"]
    kept_watch = [s for s in batch if s.confidence != "ACTIONABLE"]

    assert len(kept_actionable) == 25
    assert {signal_episode_key(s) for s in kept_actionable} == {signal_episode_key(s) for s in actionables}
    assert len(kept_watch) == 3
    assert stats["watch_dropped"] == 27


def test_watch_retention_prefers_strongest_current_research_leads():
    watches = [
        sig("weak", edge=0.01, seconds=10),
        sig("strong", edge=0.20, seconds=5),
        sig("medium", edge=0.10, seconds=20),
    ]
    batch, _ = coalesce_signal_batches([watches], watch_limit=2)
    assert [s.metadata["fingerprint_key"] for s in batch] == ["strong", "medium"]


def test_duplicate_watch_does_not_consume_retention_twice():
    old = sig("same", edge=0.50, seconds=1)
    new = sig("same", edge=0.02, seconds=2)
    other = sig("other", edge=0.01, seconds=3)

    batch, stats = coalesce_signal_batches([[old], [new, other]], watch_limit=2)
    assert len(batch) == 2
    kept_same = next(s for s in batch if s.metadata["fingerprint_key"] == "same")
    assert kept_same is new
    assert stats["duplicate_episodes_coalesced"] == 1


def test_different_tokens_are_distinct_even_with_same_semantic_key():
    a = sig("same", confidence="ACTIONABLE", edge=0.05)
    b = sig("same", confidence="ACTIONABLE", edge=0.04)
    b.token_ids = ["different-token"]

    batch, stats = coalesce_signal_batches([[a, b]])
    assert len(batch) == 2
    assert stats["duplicate_episodes_coalesced"] == 0


def test_zero_watch_limit_still_keeps_all_actionables():
    rows = [
        sig("a", confidence="ACTIONABLE", edge=0.05),
        sig("w", confidence="WATCH", edge=0.99),
    ]
    batch, stats = coalesce_signal_batches([rows], watch_limit=0)
    assert len(batch) == 1
    assert batch[0].confidence == "ACTIONABLE"
    assert stats["watch_dropped"] == 1
