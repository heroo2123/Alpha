from __future__ import annotations

from collections.abc import Iterable
import json

from .models import Signal

# Pending research work is deliberately small on the shared-core VM. The queue
# integration normally collapses all not-yet-processed batches into one latest-state
# batch; maxsize=2 leaves one slot of operational headroom without allowing
# unbounded memory growth.
SIGNAL_QUEUE_MAX_BATCHES = 2
RESEARCH_WATCH_RETENTION = 8
CANDIDATE_MAX_COUNT = 1000
CANDIDATE_MAX_BYTES = 4 * 1024 * 1024
CANDIDATE_MAX_AGE_SECONDS = 60


def signal_episode_key(signal: Signal) -> tuple:
    """Stable candidate episode identity that does not depend on time buckets.

    Financial candidates are never dropped solely because a newer scan found them
    again. Repeated observations of the same detector/market/token episode are
    coalesced to the newest copy, which is the one with the most current discovery
    evidence. Distinct episodes are retained within explicit resource bounds.
    """
    meta = signal.metadata if isinstance(signal.metadata, dict) else {}
    semantic_key = meta.get("fingerprint_key") or signal.market_id or signal.event_id
    return (
        str(signal.detector or ""),
        str(semantic_key or ""),
        tuple(str(token) for token in signal.token_ids),
    )


def coalesce_signal_batches(
    batches: Iterable[Iterable[Signal]],
    *,
    watch_limit: int = RESEARCH_WATCH_RETENTION,
    now: float | None = None,
) -> tuple[list[Signal], dict]:
    """Collapse pending output with explicit count, byte and age bounds.

    The newest observation wins for duplicate episode keys. ACTIONABLE candidates
    have priority within the resource budget. WATCH rows are bounded by current edge
    (then newest created_at) because they are evidence leads, not financial
    instructions. Returned accounting makes every research drop observable.
    """
    def watch_priority(signal: Signal) -> tuple:
        # Smaller tuple is the stronger research lead. Retain the existing
        # deterministic edge, recency and episode identity ordering.
        return (
            -(float(signal.edge) if signal.edge is not None else -1.0),
            -signal.created_at.timestamp(),
            signal_episode_key(signal),
        )

    latest: dict[tuple, Signal] = {}
    total = 0
    duplicates = 0
    batch_count = 0
    byte_count = overflow = expired = 0
    highwater_evicted = 0
    # Keep a bounded newest-seen timestamp even for an episode whose latest
    # observation could not fit. Otherwise a delayed older ACTIONABLE could
    # resurrect after a newer WATCH was rejected as oversized. This history
    # is explicitly bounded: exhaustive recall of arbitrarily many rejected
    # episodes would defeat the shared-VM memory budget.
    newest_seen: dict[tuple, object] = {}
    highwater_limit = max(1, 2 * CANDIDATE_MAX_COUNT)
    sizes = {}
    for batch in batches:
        batch_count += 1
        for signal in batch:
            total += 1
            if now is not None and not 0 <= now - signal.created_at.timestamp() <= CANDIDATE_MAX_AGE_SECONDS:
                expired += 1
                continue
            key = signal_episode_key(signal)
            prior = latest.get(key)
            seen_at = newest_seen.get(key)
            if prior is not None or seen_at is not None:
                duplicates += 1
                if ((seen_at is not None and signal.created_at < seen_at) or
                        (prior is not None and signal.created_at < prior.created_at)):
                    continue
            newest_seen.pop(key, None)
            newest_seen[key] = signal.created_at
            if len(newest_seen) > highwater_limit:
                del newest_seen[next(iter(newest_seen))]
                highwater_evicted += 1
            if (highwater_evicted and prior is None and seen_at is None
                    and signal.confidence == "ACTIONABLE"):
                # Once bounded freshness history has discarded any episode,
                # an unknown ACTIONABLE might actually be a delayed obsolete
                # receipt from that episode. Drop instead of manufacturing
                # false actionable currentness while evidence is incomplete.
                overflow += 1
                continue
            size = len(json.dumps({"metadata": signal.metadata, "detail": signal.detail,
                                   "title": signal.title, "tokens": signal.token_ids}, default=str).encode())
            # Stage resource evictions BEFORE mutating the working set.
            # A newer duplicate supersedes the earlier episode even if it
            # cannot fit: an obsolete ACTIONABLE must never remain live.
            old_size = sizes.get(key, 0)
            next_count = len(latest) + (prior is None)
            next_bytes = byte_count - old_size + size
            victims: list[tuple] = []
            if next_count > CANDIDATE_MAX_COUNT or next_bytes > CANDIDATE_MAX_BYTES:
                # An ACTIONABLE must get capacity ahead of WATCH regardless
                # of arrival order. WATCH may replace only weaker WATCH, not
                # a stronger research lead or any ACTIONABLE.
                weakest_watch_keys = sorted(
                    (item for item, existing in latest.items()
                     if item != key and existing.confidence != "ACTIONABLE"),
                    key=lambda item: watch_priority(latest[item]),
                    reverse=True,
                )
                for victim in weakest_watch_keys:
                    if (signal.confidence != "ACTIONABLE" and
                            watch_priority(signal) >= watch_priority(latest[victim])):
                        break
                    victims.append(victim)
                    next_count -= 1
                    next_bytes -= sizes[victim]
                    if next_count <= CANDIDATE_MAX_COUNT and next_bytes <= CANDIDATE_MAX_BYTES:
                        break
            if (next_count > CANDIDATE_MAX_COUNT or next_bytes > CANDIDATE_MAX_BYTES
                    or size > CANDIDATE_MAX_BYTES):
                overflow += 1
                if prior is not None:
                    # Supersession is unconditional once a newer observation
                    # is seen. Do NOT evict any speculative unrelated WATCH
                    # victims when the replacement itself cannot be admitted.
                    byte_count -= sizes.pop(key)
                    del latest[key]
                continue
            for victim in victims:
                byte_count -= sizes.pop(victim)
                del latest[victim]
                overflow += 1
            byte_count = byte_count - old_size + size
            sizes[key] = size
            latest[key] = signal

    actionables = [s for s in latest.values() if s.confidence == "ACTIONABLE"]
    watches = [s for s in latest.values() if s.confidence != "ACTIONABLE"]

    actionables.sort(
        key=lambda s: (
            -(float(s.edge) if s.edge is not None else -1.0),
            s.created_at,
            signal_episode_key(s),
        )
    )
    watches.sort(key=watch_priority)

    keep = max(0, int(watch_limit))
    kept_watches = watches[:keep]
    watch_dropped = max(0, len(watches) - len(kept_watches))
    output = actionables + kept_watches
    return output, {
        "input_batches": batch_count,
        "input_signals": total,
        "duplicate_episodes_coalesced": duplicates,
        "unique_actionable_retained": len(actionables),
        "unique_watch_retained": len(kept_watches),
        "watch_dropped": watch_dropped,
        "output_signals": len(output),
        "retained_payload_bytes": sum(sizes[signal_episode_key(s)] for s in output),
        "overflow_dropped": overflow,
        "expired_dropped": expired,
        "newest_seen_history_evicted": highwater_evicted,
        "evidence_complete": overflow == 0 and expired == 0 and highwater_evicted == 0,
    }
