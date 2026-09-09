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
    latest: dict[tuple, Signal] = {}
    total = 0
    duplicates = 0
    batch_count = 0
    byte_count = overflow = expired = 0
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
            if prior is not None:
                duplicates += 1
                if signal.created_at < prior.created_at:
                    continue
            size = len(json.dumps({"metadata": signal.metadata, "detail": signal.detail,
                                   "title": signal.title, "tokens": signal.token_ids}, default=str).encode())
            next_bytes = byte_count - sizes.get(key, 0) + size
            if (prior is None and len(latest) >= CANDIDATE_MAX_COUNT) or next_bytes > CANDIDATE_MAX_BYTES:
                overflow += 1
                continue
            byte_count = next_bytes
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
        "retained_payload_bytes": byte_count,
        "overflow_dropped": overflow,
        "expired_dropped": expired,
        "evidence_complete": overflow == 0 and expired == 0,
    }
