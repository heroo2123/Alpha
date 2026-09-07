from __future__ import annotations

from collections.abc import Iterable

from .models import Signal

# Pending research work is deliberately small on the shared-core VM. The queue
# integration normally collapses all not-yet-processed batches into one latest-state
# batch; maxsize=2 leaves one slot of operational headroom without allowing
# unbounded memory growth.
SIGNAL_QUEUE_MAX_BATCHES = 2
RESEARCH_WATCH_RETENTION = 8


def signal_episode_key(signal: Signal) -> tuple:
    """Stable candidate episode identity that does not depend on time buckets.

    Financial candidates are never dropped solely because a newer scan found them
    again. Repeated observations of the same detector/market/token episode are
    coalesced to the newest copy, which is the one with the most current discovery
    evidence. Distinct ACTIONABLE episodes are all retained.
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
) -> tuple[list[Signal], dict]:
    """Collapse pending detector output without losing unique ACTIONABLE episodes.

    The newest observation wins for duplicate episode keys. Every unique ACTIONABLE
    candidate is retained. WATCH/research rows are bounded by strongest current edge
    (then newest created_at) because they are evidence leads, not financial
    instructions. Returned accounting makes every research drop observable.
    """
    latest: dict[tuple, Signal] = {}
    total = 0
    duplicates = 0
    batch_count = 0
    for batch in batches:
        batch_count += 1
        for signal in batch:
            total += 1
            key = signal_episode_key(signal)
            prior = latest.get(key)
            if prior is not None:
                duplicates += 1
                if signal.created_at < prior.created_at:
                    continue
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
    watches.sort(
        key=lambda s: (
            -(float(s.edge) if s.edge is not None else -1.0),
            -s.created_at.timestamp(),
            signal_episode_key(s),
        )
    )

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
    }
