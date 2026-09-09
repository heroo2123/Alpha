"""Scanner-side, nonblocking loading and acceptance of immutable generations."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time

from .models import Book, Market
from .production_gamma_bbo import gamma_screening_books
from .production_universe import PRODUCTION_UNIVERSE_FILTER_VERSION
from .universe_snapshot import (
    GAMMA_QUOTE_MAX_AGE_SECONDS, UNIVERSE_MAX_AGE_SECONDS, SnapshotError,
    current_pointer, generation_age, open_generation, read_json,
)


@dataclass
class PreparedUniverse:
    manifest: dict
    markets: list[Market]
    tokens: list[str]
    priority_tokens: tuple[str, ...]
    weather_markets: list[Market]
    stations: list[str]
    screening: dict[str, Book]
    lane_tokens: dict[str, tuple[str, ...]]


class UniverseReader:
    def __init__(self, directory: Path, *, producer_sha: str, priority, weather_universe):
        self.directory, self.producer_sha = directory, producer_sha
        self.priority, self.weather_universe = priority, weather_universe
        self.accepted: PreparedUniverse | None = None
        self.accepted_monotonic = 0.0
        self.age_at_acceptance = 0.0
        self.last_error: str | None = None
        self.builder: dict = {"state": "UNKNOWN"}

    def poll(self) -> PreparedUniverse | None:
        """Called in a worker thread. Does not mutate accepted generation data."""
        try:
            self.builder = read_json(self.directory / "builder-status.json")
        except (OSError, ValueError, SnapshotError):
            self.builder = {"state": "UNKNOWN"}
        try:
            pointer = current_pointer(self.directory)
            if pointer is None:
                self.last_error = "no published generation"
                return None
            if self.accepted is not None:
                accepted = self.accepted.manifest
                if pointer["sequence"] < accepted["sequence"]:
                    raise SnapshotError("generation rollback rejected")
                if pointer["sequence"] == accepted["sequence"]:
                    if (pointer["file"] != accepted["generation_id"] + ".sqlite"
                            or pointer["sha256"] != accepted["file_sha256"]):
                        raise SnapshotError("same sequence with conflicting generation identity")
                    return None
            db, manifest = open_generation(self.directory, pointer, producer_sha=self.producer_sha,
                                           filter_version=PRODUCTION_UNIVERSE_FILTER_VERSION)
            manifest["file_sha256"] = pointer["sha256"]
            manifest["published_at"] = pointer.get("published_at")
            try:
                parents = {key: json.loads(body) for key, body in db.execute("SELECT id,payload FROM events")}
                markets = []
                tokens: set[str] = set()
                for market_id, event_id, body in db.execute("SELECT id,event_id,payload FROM markets ORDER BY id"):
                    value = json.loads(body)
                    if value.get("id") != market_id or value.get("event_id") != event_id or event_id not in parents:
                        raise SnapshotError("market/parent identity mismatch")
                    value["raw"]["_event"] = parents[event_id]
                    market = Market(**value)
                    receipt = market.raw.get("_gamma_received_at")
                    if not isinstance(receipt, (int, float)) or not manifest["started_at"] <= receipt <= manifest["finished_at"]:
                        raise SnapshotError("market quote receipt outside generation interval")
                    if not market.active or market.closed or not market.token_ids or not all(market.token_ids):
                        raise SnapshotError("invalid materialized market identity/lifecycle")
                    if len(set(market.token_ids)) != len(market.token_ids) or tokens.intersection(market.token_ids):
                        raise SnapshotError("duplicate token ownership in generation")
                    tokens.update(market.token_ids)
                    markets.append(market)
                weather, stations = self.weather_universe(markets)
                priority = []
                for market in sorted(markets, key=self.priority):
                    priority.extend(market.token_ids)
                    if len(priority) >= 800:
                        break
                lane_tokens = {}
                for market in markets:
                    for lane in market.raw.get("_filter_reasons", []):
                        lane_tokens.setdefault(lane, []).extend(market.token_ids)
                result = PreparedUniverse(manifest, markets, sorted(tokens), tuple(priority[:800]),
                                          weather, stations, gamma_screening_books(markets),
                                          {lane: tuple(ids) for lane, ids in lane_tokens.items()})
            finally:
                db.close()
            if generation_age(manifest) >= UNIVERSE_MAX_AGE_SECONDS:
                raise SnapshotError("generation expired while loading")
            self.last_error = None
            return result
        except Exception as exc:
            self.last_error = f"generation rejected: {type(exc).__name__}"
            return None

    def accept(self, generation: PreparedUniverse) -> None:
        age = generation_age(generation.manifest)
        if age >= UNIVERSE_MAX_AGE_SECONDS:
            raise SnapshotError("generation expired before acceptance")
        if self.accepted and generation.manifest["sequence"] <= self.accepted.manifest["sequence"]:
            raise SnapshotError("nonmonotonic generation acceptance")
        self.accepted = generation
        self.age_at_acceptance, self.accepted_monotonic = age, time.monotonic()

    def universe_status(self, *, now: float | None = None) -> dict:
        manifest = self.accepted.manifest if self.accepted else {}
        wall_age = generation_age(manifest, now=now)
        age = max(wall_age, self.age_at_acceptance + time.monotonic() - self.accepted_monotonic)
        builder = dict(self.builder)
        updated = builder.get("heartbeat_at", builder.get("updated_at"))
        current = time.time() if now is None else now
        builder["status_age_seconds"] = current - updated if isinstance(updated, (int, float)) else None
        builder["status_fresh"] = bool(isinstance(updated, (int, float)) and 0 <= current - updated < 60)
        return {
            **manifest, "refresh_owner": "separate_universe_builder", "complete": bool(manifest),
            "safe_for_detection": bool(manifest) and age < UNIVERSE_MAX_AGE_SECONDS,
            "age_seconds": age if age != float("inf") else None,
            "hard_stale_seconds": UNIVERSE_MAX_AGE_SECONDS,
            "last_error": self.last_error, "builder": builder,
            "refresh_progress": builder if builder.get("state") == "BUILDING" else None,
            "refresh_in_progress": builder.get("state") == "BUILDING" and builder["status_fresh"],
        }

    def screening_status(self) -> dict:
        generation = self.accepted
        now = time.time()
        books = generation.screening if generation else {}
        fresh_tokens = {book.token_id for book in books.values() if book.received_at is not None
                       and 0 <= now - book.received_at < GAMMA_QUOTE_MAX_AGE_SECONDS}
        fresh = len(fresh_tokens)
        total = len(generation.tokens) if generation else 0
        oldest = min((book.received_at for book in books.values()), default=None)
        return {
            "source": "gamma_bbo_screening_only", "executable": False,
            "target_tokens": total, "quoted_tokens": len(books), "usable_price_tokens": fresh,
            "usable_coverage_ratio": fresh / total if total else 0.0,
            "missing_or_stale_tokens": total - fresh, "stale": fresh == 0,
            "snapshot_at": oldest, "snapshot_age_seconds": now - oldest if oldest is not None else None,
            "stale_after_seconds": GAMMA_QUOTE_MAX_AGE_SECONDS,
            "quote_time_known": False, "coverage_scope": "materialized_subset_only",
            "per_lane": {lane: {"target_tokens": len(ids), "fresh_ask_tokens": sum(t in fresh_tokens for t in ids)}
                         for lane, ids in (generation.lane_tokens.items() if generation else [])},
        }
