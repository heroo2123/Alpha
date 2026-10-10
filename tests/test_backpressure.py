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


def test_actionable_after_watch_count_saturation_is_not_displaced(monkeypatch):
    """A late genuine ACTIONABLE must outrank earlier WATCH within count cap."""
    from polymarket_scanner import backpressure as bp
    monkeypatch.setattr(bp, "CANDIDATE_MAX_COUNT", 2)
    old_watch = sig("old-watch", confidence="WATCH", edge=0.80)
    new_watch = sig("new-watch", confidence="WATCH", edge=0.70)
    late_action = sig("late-action", confidence="ACTIONABLE", edge=0.04)
    result, stats = bp.coalesce_signal_batches([[old_watch, new_watch, late_action]])
    assert late_action in result
    assert len(result) <= 2
    assert stats["unique_actionable_retained"] == 1
    assert not stats["evidence_complete"]


def test_actionable_after_watch_byte_saturation_is_not_displaced(monkeypatch):
    """A later ACTIONABLE cannot be starved by WATCH bytes admitted first."""
    import json
    from polymarket_scanner import backpressure as bp

    def bytes_needed(row):
        return len(json.dumps({"metadata": row.metadata, "detail": row.detail,
                               "title": row.title, "tokens": row.token_ids},
                              default=str).encode())

    w1 = sig("byte-watch-1", confidence="WATCH", edge=0.75)
    w2 = sig("byte-watch-2", confidence="WATCH", edge=0.55)
    actionable = sig("byte-action", confidence="ACTIONABLE", edge=0.05)
    allowed = bytes_needed(w1) + bytes_needed(w2)
    assert bytes_needed(actionable) <= allowed
    monkeypatch.setattr(bp, "CANDIDATE_MAX_BYTES", allowed)
    result, stats = bp.coalesce_signal_batches([[w1, w2, actionable]])
    assert actionable in result
    assert stats["retained_payload_bytes"] <= allowed
    assert stats["unique_actionable_retained"] == 1
    assert not stats["evidence_complete"]


def test_unfittable_new_duplicate_removes_superseded_episode(monkeypatch):
    """Obsolete ACTIONABLE is removed even if newer evidence is too large."""
    from polymarket_scanner import backpressure as bp
    prior = sig("same", confidence="ACTIONABLE", edge=0.03, seconds=1)
    updated = sig("same", confidence="ACTIONABLE", edge=0.04, seconds=3)
    updated.detail = "x" * 2000
    monkeypatch.setattr(bp, "CANDIDATE_MAX_BYTES", 900)
    result, stats = bp.coalesce_signal_batches([[prior], [updated]])
    assert result == []  # The old entry is no longer current.
    assert stats["duplicate_episodes_coalesced"] == 1
    assert stats["overflow_dropped"] == 1
    assert stats["retained_payload_bytes"] == 0
    assert not stats["evidence_complete"]


def test_new_watch_can_displace_only_weaker_watch_under_count_limit(monkeypatch):
    """A stronger research lead may replace the weakest, not an actionable."""
    from polymarket_scanner import backpressure as bp
    monkeypatch.setattr(bp, "CANDIDATE_MAX_COUNT", 2)
    old = sig("old", edge=0.01, seconds=1)
    strong = sig("strong", edge=0.30, seconds=1)
    arriving = sig("arriving", edge=0.15, seconds=2)
    output, stats = bp.coalesce_signal_batches([[old, strong, arriving]], watch_limit=2)
    assert output == [strong, arriving]
    assert stats["overflow_dropped"] == 1
    assert stats["retained_payload_bytes"] > 0
    weak = sig("weaker", edge=0.001, seconds=3)
    output, stats = bp.coalesce_signal_batches([[strong, arriving, weak]], watch_limit=2)
    assert output == [strong, arriving]
    assert stats["overflow_dropped"] == 1


def test_actionable_does_not_evade_count_limit_when_all_reserved(monkeypatch):
    from polymarket_scanner import backpressure as bp
    monkeypatch.setattr(bp, "CANDIDATE_MAX_COUNT", 2)
    first = sig("first", confidence="ACTIONABLE", edge=0.03)
    second = sig("second", confidence="ACTIONABLE", edge=0.04)
    late = sig("late", confidence="ACTIONABLE", edge=0.70)
    result, stats = bp.coalesce_signal_batches([[first, second, late]])
    assert len(result) == 2
    assert set(signal_episode_key(x) for x in result) == {
        signal_episode_key(first), signal_episode_key(second)
    }
    assert stats["overflow_dropped"] == 1
    assert stats["unique_actionable_retained"] == 2


def test_oversized_actionable_does_not_evict_watch_when_cannot_fit(monkeypatch):
    from polymarket_scanner import backpressure as bp
    watch = sig("watch", edge=0.04)
    huge = sig("huge", confidence="ACTIONABLE")
    huge.detail = "x" * 1000
    monkeypatch.setattr(bp, "CANDIDATE_MAX_BYTES", 800)
    result, stats = bp.coalesce_signal_batches([[watch, huge]])
    assert result == [watch]
    assert stats["overflow_dropped"] == 1
    assert stats["retained_payload_bytes"] <= 800


def test_watch_limit_reports_bytes_of_emitted_payload_only():
    import json
    first = sig("w1", edge=0.01)
    second = sig("w2", edge=0.02)
    output, stats = coalesce_signal_batches([[first, second]], watch_limit=1)
    assert output == [second]
    expected = len(json.dumps({"metadata": second.metadata,
                               "detail": second.detail,
                               "title": second.title,
                               "tokens": second.token_ids}, default=str).encode())
    assert stats["retained_payload_bytes"] == expected
    assert stats["watch_dropped"] == 1


def test_later_oversized_watch_invalidates_prior_actionable_without_evicting_peer(monkeypatch):
    """Latest downgraded evidence must not leave an obsolete actionable live."""
    from polymarket_scanner import backpressure as bp
    monkeypatch.setattr(bp, "CANDIDATE_MAX_COUNT", 2)
    monkeypatch.setattr(bp, "CANDIDATE_MAX_BYTES", 900)
    first = sig("same", confidence="ACTIONABLE", edge=0.05, seconds=1)
    peer_watch = sig("peer", confidence="WATCH", edge=0.06, seconds=2)
    latest = sig("same", confidence="WATCH", edge=0.00, seconds=3)
    latest.detail = "x" * 1500  # cannot fit even if peer is discarded
    output, stats = bp.coalesce_signal_batches([[first, peer_watch, latest]])
    assert output == [peer_watch]
    assert stats["unique_actionable_retained"] == 0
    assert stats["unique_watch_retained"] == 1
    assert stats["duplicate_episodes_coalesced"] == 1
    assert stats["overflow_dropped"] == 1
    assert not stats["evidence_complete"]


def test_latest_rejected_duplicate_never_resurrects_older_actionable(monkeypatch):
    """A delayed old ACTIONABLE cannot come back after a newer oversized WATCH."""
    from polymarket_scanner import backpressure as bp
    monkeypatch.setattr(bp, "CANDIDATE_MAX_COUNT", 2)
    monkeypatch.setattr(bp, "CANDIDATE_MAX_BYTES", 900)
    old_action = sig("same", confidence="ACTIONABLE", seconds=1, edge=0.08)
    newest_watch = sig("same", confidence="WATCH", seconds=3, edge=0.01)
    newest_watch.detail = "x" * 1500
    delayed_action = sig("same", confidence="ACTIONABLE", seconds=2, edge=0.09)
    result, stats = bp.coalesce_signal_batches(
        [[old_action], [newest_watch], [delayed_action]]
    )
    assert result == []
    assert stats["duplicate_episodes_coalesced"] == 2
    assert stats["overflow_dropped"] == 1
    assert stats["retained_payload_bytes"] == 0
    assert not stats["evidence_complete"]


def test_first_oversized_observation_blocks_later_stale_actionable(monkeypatch):
    """Even a rejected first receipt establishes bounded freshness history."""
    from polymarket_scanner import backpressure as bp
    monkeypatch.setattr(bp, "CANDIDATE_MAX_COUNT", 2)
    monkeypatch.setattr(bp, "CANDIDATE_MAX_BYTES", 900)
    newest_watch = sig("same", confidence="WATCH", seconds=3, edge=0.01)
    newest_watch.detail = "x" * 1500
    older_action = sig("same", confidence="ACTIONABLE", seconds=2, edge=0.09)
    output, stats = bp.coalesce_signal_batches(
        [[newest_watch], [older_action]]
    )
    assert output == []
    assert stats["overflow_dropped"] == 1
    assert stats["duplicate_episodes_coalesced"] == 1
    assert not stats["evidence_complete"]


def test_newest_seen_history_is_bounded_and_reports_evictions(monkeypatch):
    from polymarket_scanner import backpressure as bp
    monkeypatch.setattr(bp, "CANDIDATE_MAX_COUNT", 1)
    monkeypatch.setattr(bp, "CANDIDATE_MAX_BYTES", 120)
    oversized = [
        sig(f"oversize-{i}", confidence="ACTIONABLE", seconds=i)
        for i in range(20)
    ]
    for signal in oversized:
        signal.detail = "x" * 300
    output, stats = bp.coalesce_signal_batches([oversized])
    assert output == []
    assert stats["overflow_dropped"] == 20
    assert stats["newest_seen_history_evicted"] == 18
    assert not stats["evidence_complete"]


def test_history_eviction_does_not_allow_resurrection_of_unknown_stale_actionable(monkeypatch):
    """When bounded freshness metadata is lost, unknown ACTIONABLE fails closed."""
    from polymarket_scanner import backpressure as bp
    monkeypatch.setattr(bp, "CANDIDATE_MAX_COUNT", 1)
    monkeypatch.setattr(bp, "CANDIDATE_MAX_BYTES", 900)
    newest = sig("same", confidence="WATCH", seconds=3)
    newest.detail = "x" * 1500
    other1 = sig("other1", confidence="WATCH", seconds=4)
    other1.detail = "x" * 1500
    other2 = sig("other2", confidence="WATCH", seconds=5)
    other2.detail = "x" * 1500
    stale = sig("same", confidence="ACTIONABLE", seconds=2, edge=0.12)
    out, stats = bp.coalesce_signal_batches([[newest], [other1, other2], [stale]])
    assert out == []
    assert stats["newest_seen_history_evicted"] >= 1
    assert stats["overflow_dropped"] == 4
    assert not stats["evidence_complete"]
