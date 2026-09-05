from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class Market:
    id: str
    event_id: str
    event_slug: str
    event_title: str
    event_neg_risk: bool
    question: str
    slug: str
    condition_id: str
    outcomes: list[str]
    token_ids: list[str]
    outcome_prices: list[float]
    best_bid: float | None
    best_ask: float | None
    liquidity: float
    volume_24h: float
    active: bool
    closed: bool
    end_date: str | None
    description: str
    resolution_source: str
    category: str
    tags: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def token_for_outcome(self, label: str) -> str | None:
        wanted = label.strip().lower()
        for i, outcome in enumerate(self.outcomes):
            if outcome.strip().lower() == wanted and i < len(self.token_ids):
                return self.token_ids[i]
        return None

    @property
    def yes_token(self) -> str | None:
        return self.token_for_outcome("yes") or (self.token_ids[0] if self.token_ids else None)

    @property
    def no_token(self) -> str | None:
        return self.token_for_outcome("no") or (self.token_ids[1] if len(self.token_ids) > 1 else None)


@dataclass(slots=True)
class Book:
    token_id: str
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]
    last_trade_price: float | None = None
    timestamp: str | None = None

    @property
    def best_bid(self) -> float | None:
        return max((p for p, _ in self.bids), default=None)

    @property
    def best_ask(self) -> float | None:
        return min((p for p, _ in self.asks), default=None)

    @property
    def best_bid_size(self) -> float:
        if self.best_bid is None:
            return 0.0
        return sum(s for p, s in self.bids if p == self.best_bid)

    @property
    def best_ask_size(self) -> float:
        if self.best_ask is None:
            return 0.0
        return sum(s for p, s in self.asks if p == self.best_ask)


@dataclass(slots=True)
class Signal:
    detector: str
    confidence: str
    event_id: str
    market_id: str | None
    title: str
    detail: str
    url: str
    edge: float | None
    entry_cost: float | None
    theoretical_payout: float | None
    token_ids: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)

    def fingerprint(self) -> str:
        key = self.metadata.get("fingerprint_key") or self.market_id or self.event_id
        # Keep persistent opportunities from spamming; a fresh alert can recur after
        # the configured cooldown bucket if the condition is still present.
        bucket_seconds = int(self.metadata.get("fingerprint_bucket_seconds", 900))
        bucket = int(self.created_at.timestamp() // max(60, bucket_seconds))
        return f"{self.detector}:{key}:{bucket}"
