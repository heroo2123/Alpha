from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Iterable

import websockets

from .config import settings
from .models import Book

log = logging.getLogger("polybot.streams")

MARKET_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
SPORTS_WS = "wss://sports-api.polymarket.com/ws"
RTDS_WS = "wss://ws-live-data.polymarket.com"


def _f(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _ts_seconds(v) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return time.time()
    return x / 1000.0 if x > 10_000_000_000 else x


class LiveMarketStream:
    """Low-latency public CLOB cache; ACTIONABLE alerts are REST-confirmed."""
    def __init__(self) -> None:
        self.books: dict[str, Book] = {}
        self.changed = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._token_set: set[str] = set()
        self.connected_workers = 0
        self.last_message_at: float | None = None

    def seed(self, books: dict[str, Book]) -> None:
        self.books.update(books)

    def snapshot(self) -> dict[str, Book]:
        return dict(self.books)

    async def configure(self, token_ids: Iterable[str]) -> None:
        tokens = list(dict.fromkeys(t for t in token_ids if t))
        new_set = set(tokens)
        if new_set == self._token_set and self._tasks:
            return
        self._token_set = new_set
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self.connected_workers = 0
        if not settings.market_ws_enabled or not tokens:
            return
        size = max(50, settings.ws_tokens_per_connection)
        for i, chunk_start in enumerate(range(0, len(tokens), size)):
            self._tasks.append(asyncio.create_task(self._worker(i, tokens[chunk_start:chunk_start + size])))

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def _heartbeat(self, ws) -> None:
        while True:
            await asyncio.sleep(10)
            try:
                await ws.send("PING")
            except Exception:
                return

    async def _worker(self, worker_id: int, tokens: list[str]) -> None:
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(MARKET_WS, ping_interval=None, close_timeout=5, max_size=16_000_000) as ws:
                    await ws.send(json.dumps({"assets_ids": tokens, "type": "market", "custom_feature_enabled": True}))
                    self.connected_workers += 1
                    backoff = 1.0
                    hb = asyncio.create_task(self._heartbeat(ws))
                    try:
                        async for raw in ws:
                            self.last_message_at = time.time()
                            if raw in {"PONG", "pong"}:
                                continue
                            try:
                                msg = json.loads(raw)
                            except Exception:
                                continue
                            for row in (msg if isinstance(msg, list) else [msg]):
                                if isinstance(row, dict):
                                    self._apply(row)
                    finally:
                        hb.cancel()
                        self.connected_workers = max(0, self.connected_workers - 1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("market ws worker %s disconnected: %s", worker_id, exc)
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 2)

    def _apply(self, msg: dict) -> None:
        typ = msg.get("event_type") or msg.get("type")
        changed = False
        if typ == "book":
            token = str(msg.get("asset_id") or "")
            if token:
                bids = [(_f(x.get("price"), 0.0), _f(x.get("size"), 0.0)) for x in msg.get("bids", [])]
                asks = [(_f(x.get("price"), 0.0), _f(x.get("size"), 0.0)) for x in msg.get("asks", [])]
                self.books[token] = Book(token, bids, asks, timestamp=str(msg.get("timestamp") or ""))
                changed = True
        elif typ == "price_change":
            for row in msg.get("price_changes") or msg.get("changes") or []:
                token = str(row.get("asset_id") or "")
                if not token:
                    continue
                old = self.books.get(token) or Book(token, [], [])
                bb = _f(row.get("best_bid"), old.best_bid); ba = _f(row.get("best_ask"), old.best_ask)
                bid_size = old.best_bid_size if bb is not None and bb == old.best_bid else 0.0
                ask_size = old.best_ask_size if ba is not None and ba == old.best_ask else 0.0
                old.bids = [] if bb is None else [(bb, bid_size)]
                old.asks = [] if ba is None else [(ba, ask_size)]
                old.timestamp = str(msg.get("timestamp") or "")
                self.books[token] = old
                changed = True
        elif typ == "best_bid_ask":
            token = str(msg.get("asset_id") or "")
            if token:
                old = self.books.get(token) or Book(token, [], [])
                bb, ba = _f(msg.get("best_bid")), _f(msg.get("best_ask"))
                old.bids = [] if bb is None else [(bb, old.best_bid_size if bb == old.best_bid else 0.0)]
                old.asks = [] if ba is None else [(ba, old.best_ask_size if ba == old.best_ask else 0.0)]
                old.timestamp = str(msg.get("timestamp") or "")
                self.books[token] = old
                changed = True
        if changed:
            self.changed.set()


class SportsStream:
    def __init__(self) -> None:
        self.results: dict[str, dict] = {}
        self.changed = asyncio.Event()
        self.last_message_at: float | None = None
        self.connected = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if settings.sports_ws_enabled and self._task is None:
            self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        if self._task:
            self._task.cancel(); await asyncio.gather(self._task, return_exceptions=True); self._task = None

    def snapshot(self) -> dict[str, dict]:
        return dict(self.results)

    async def _run(self) -> None:
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(SPORTS_WS, ping_interval=None, close_timeout=5) as ws:
                    self.connected = True; backoff = 1.0
                    async for raw in ws:
                        self.last_message_at = time.time()
                        if isinstance(raw, str) and raw.upper() == "PING":
                            await ws.send("PONG"); continue
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue
                        for row in (msg if isinstance(msg, list) else [msg]):
                            if not isinstance(row, dict):
                                continue
                            slug = str(row.get("slug") or "")
                            if slug and any(k in row for k in ("score", "ended", "live", "period")):
                                self.results[slug] = row; self.changed.set()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False; log.warning("sports ws disconnected: %s", exc)
                await asyncio.sleep(backoff); backoff = min(30.0, backoff * 2)


@dataclass(slots=True)
class PriceTick:
    topic: str
    symbol: str
    price: float
    ts: float


class CryptoRTDS:
    """Polymarket RTDS crypto reference cache plus Binance-backed cross-check."""
    CHAINLINK_SYMBOLS = ["btc/usd", "eth/usd", "sol/usd", "xrp/usd"]
    TOPICS = ["crypto_prices_chainlink", "crypto_prices_twap_thirty", "crypto_prices_twap_sixty"]

    def __init__(self) -> None:
        self.latest_ticks: dict[tuple[str, str], PriceTick] = {}
        self.history: dict[tuple[str, str], deque[PriceTick]] = defaultdict(lambda: deque(maxlen=20_000))
        self.changed = asyncio.Event()
        self.last_message_at: float | None = None
        self.connected = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if settings.crypto_rtds_enabled and self._task is None:
            self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        if self._task:
            self._task.cancel(); await asyncio.gather(self._task, return_exceptions=True); self._task = None

    def latest(self, topic: str, symbol: str) -> PriceTick | None:
        return self.latest_ticks.get((topic, symbol.lower()))

    def nearest(self, topic: str, symbol: str, target_ts: float, tolerance: float) -> PriceTick | None:
        rows = self.history.get((topic, symbol.lower()))
        if not rows:
            return None
        best = None; best_delta = tolerance + 1.0
        for tick in reversed(rows):
            delta = abs(tick.ts - target_ts)
            if delta < best_delta:
                best, best_delta = tick, delta
            if tick.ts < target_ts - tolerance:
                break
        return best if best and best_delta <= tolerance else None

    async def _heartbeat(self, ws) -> None:
        while True:
            await asyncio.sleep(5)
            try:
                await ws.send(json.dumps({"type": "PING"}))
            except Exception:
                return

    async def _run(self) -> None:
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(RTDS_WS, ping_interval=None, close_timeout=5, max_size=8_000_000) as ws:
                    subscriptions = []
                    for topic in self.TOPICS:
                        for symbol in self.CHAINLINK_SYMBOLS:
                            subscriptions.append({"topic": topic, "type": "update", "filters": json.dumps({"symbol": symbol}, separators=(",", ":"))})
                    subscriptions.append({"topic": "crypto_prices", "type": "update"})
                    await ws.send(json.dumps({"action": "subscribe", "subscriptions": subscriptions}))
                    self.connected = True; backoff = 1.0
                    hb = asyncio.create_task(self._heartbeat(ws))
                    try:
                        async for raw in ws:
                            self.last_message_at = time.time()
                            try:
                                msg = json.loads(raw)
                            except Exception:
                                continue
                            self._apply(msg)
                    finally:
                        hb.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False; log.warning("crypto RTDS disconnected: %s", exc)
                await asyncio.sleep(backoff); backoff = min(30.0, backoff * 2)

    def _apply(self, msg) -> None:
        if isinstance(msg, list):
            for row in msg:
                self._apply(row)
            return
        if not isinstance(msg, dict):
            return
        topic = str(msg.get("topic") or "")
        if not topic.startswith("crypto_prices"):
            return
        payload = msg.get("payload", msg)
        if isinstance(payload, dict) and isinstance(payload.get("data"), list): rows = payload["data"]
        elif isinstance(payload, list): rows = payload
        elif isinstance(payload, dict): rows = [payload]
        else: rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or (payload.get("symbol") if isinstance(payload, dict) else "")).lower()
            price = _f(row.get("value", row.get("price")))
            if not symbol or price is None or price <= 0:
                continue
            ts = _ts_seconds(row.get("timestamp") or row.get("timestamp_ms") or msg.get("timestamp"))
            tick = PriceTick(topic, symbol, price, ts)
            key = (topic, symbol)
            self.latest_ticks[key] = tick; self.history[key].append(tick); self.changed.set()
