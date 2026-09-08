from __future__ import annotations

import asyncio
import json
import logging
import math
import socket
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


def _ts_seconds(v) -> float | None:
    """Backward-compatible timestamp parser that never fabricates receipt time."""
    return _strict_ts_seconds(v)


def _strict_ts_seconds(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(x) or x <= 0:
        return None
    return x / 1000.0 if x > 10_000_000_000 else x


def _valid_price(v) -> float | None:
    x = _f(v)
    if x is None or not (0.0 < x < 1.0):
        return None
    return x


def _valid_size(v) -> float | None:
    x = _f(v)
    if x is None or x < 0.0:
        return None
    return x


def _levels(rows) -> list[tuple[float, float]] | None:
    out: dict[float, float] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            return None
        price = _valid_price(row.get("price"))
        size = _valid_size(row.get("size"))
        if price is None or size is None:
            return None
        if size > 0:
            out[price] = out.get(price, 0.0) + size
    return list(out.items())


def _same_price(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= 1e-12


class LiveMarketStream:
    """Low-latency public CLOB cache with reconnect-safe depth reconstruction.

    Polymarket's market-channel ``price_change`` rows identify the exact aggregate
    price level and its new size. We apply those level changes to the latest full
    ``book`` snapshot instead of retaining a stale top-level size. Every reconnect
    starts a new epoch and invalidates the worker's cached books until a new full
    snapshot arrives, so pre-disconnect state cannot leak into the new connection.

    ACTIONABLE alerts are still independently REST-confirmed at delivery time.
    """

    def __init__(self) -> None:
        self.books: dict[str, Book] = {}
        self.changed = asyncio.Event()
        self._tasks: dict[int, asyncio.Task] = {}
        self._queues: dict[int, asyncio.Queue] = {}
        self._worker_tokens: dict[int, set[str]] = {}
        self._token_owner: dict[str, int] = {}
        self._token_set: set[str] = set()
        self._next_worker_id = 0
        self._worker_epoch: dict[int, int] = {}
        self._snapshot_epoch: dict[str, int] = {}
        self._token_remote_ts: dict[str, float] = {}
        self.connected_workers = 0
        self.last_message_at: float | None = None
        self.last_valid_update_at: float | None = None
        self.last_full_book_at: float | None = None
        self.invalidated_books = 0
        self.out_of_order_ignored = 0

    def seed(self, books: dict[str, Book]) -> None:
        self.books.update({token: book.clone() for token, book in books.items()})

    def snapshot(self) -> dict[str, Book]:
        return {token: book.clone() for token, book in self.books.items()}

    def _start_worker(self, tokens: Iterable[str]) -> int:
        worker_id = self._next_worker_id
        self._next_worker_id += 1
        token_set = set(tokens)
        self._worker_tokens[worker_id] = token_set
        self._queues[worker_id] = asyncio.Queue()
        self._worker_epoch[worker_id] = 0
        for token in token_set:
            self._token_owner[token] = worker_id
        self._tasks[worker_id] = asyncio.create_task(self._worker(worker_id))
        return worker_id

    def _invalidate_token(self, token: str, *, count: bool = True) -> None:
        existed = token in self.books or token in self._snapshot_epoch
        self.books.pop(token, None)
        self._snapshot_epoch.pop(token, None)
        self._token_remote_ts.pop(token, None)
        if count and existed:
            self.invalidated_books += 1
            self.changed.set()

    def _begin_worker_epoch(self, worker_id: int) -> int:
        epoch = int(self._worker_epoch.get(worker_id, 0)) + 1
        self._worker_epoch[worker_id] = epoch
        for token in tuple(self._worker_tokens.get(worker_id, set())):
            self._invalidate_token(token)
        return epoch

    def _token_allowed(self, token: str, worker_id: int | None) -> bool:
        if worker_id is None:
            return True
        return self._token_owner.get(token) == worker_id and token in self._worker_tokens.get(worker_id, set())

    def _epoch_for(self, token: str, worker_id: int | None, epoch: int | None) -> int:
        if epoch is not None:
            return int(epoch)
        if worker_id is not None:
            return int(self._worker_epoch.get(worker_id, 0))
        return int(self._snapshot_epoch.get(token, 0))

    async def configure(self, token_ids: Iterable[str]) -> None:
        tokens = list(dict.fromkeys(t for t in token_ids if t))
        new_set = set(tokens)
        if not settings.market_ws_enabled:
            if self._tasks:
                await self.close()
            self._token_set = new_set
            return
        if new_set == self._token_set and self._tasks:
            return

        size = max(50, settings.ws_tokens_per_connection)
        if not self._tasks:
            for chunk_start in range(0, len(tokens), size):
                self._start_worker(tokens[chunk_start:chunk_start + size])
            self._token_set = new_set
            return

        removed = self._token_set - new_set
        unsubscribe_by_worker: dict[int, list[str]] = defaultdict(list)
        for token in removed:
            worker_id = self._token_owner.pop(token, None)
            if worker_id is not None:
                self._worker_tokens.get(worker_id, set()).discard(token)
                unsubscribe_by_worker[worker_id].append(token)
            self._invalidate_token(token)
        for worker_id, ids in unsubscribe_by_worker.items():
            queue = self._queues.get(worker_id)
            if queue and ids:
                queue.put_nowait({"operation": "unsubscribe", "assets_ids": ids})

        subscribe_by_worker: dict[int, list[str]] = defaultdict(list)
        for token in new_set - self._token_set:
            candidates = [wid for wid, owned in self._worker_tokens.items() if len(owned) < size]
            if candidates:
                worker_id = min(candidates, key=lambda wid: len(self._worker_tokens[wid]))
            else:
                worker_id = self._start_worker([])
            self._worker_tokens[worker_id].add(token)
            self._token_owner[token] = worker_id
            self._invalidate_token(token, count=False)
            subscribe_by_worker[worker_id].append(token)
        for worker_id, ids in subscribe_by_worker.items():
            queue = self._queues.get(worker_id)
            if queue and ids:
                queue.put_nowait({"operation": "subscribe", "assets_ids": ids})

        self._token_set = new_set

    async def close(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks = {}
        self._queues = {}
        self._worker_tokens = {}
        self._token_owner = {}
        self._token_set = set()
        self._worker_epoch = {}
        self._snapshot_epoch = {}
        self._token_remote_ts = {}
        self.books = {}
        self.connected_workers = 0

    async def _heartbeat(self, ws) -> None:
        while True:
            await asyncio.sleep(10)
            try:
                await ws.send("PING")
            except Exception:
                return

    async def _subscription_sender(self, ws, worker_id: int) -> None:
        queue = self._queues[worker_id]
        while True:
            message = await queue.get()
            try:
                await ws.send(json.dumps(message, separators=(",", ":")))
            except Exception:
                try:
                    await ws.close()
                except Exception:
                    pass
                return

    async def _worker(self, worker_id: int) -> None:
        backoff = 1.0
        while True:
            tokens = list(self._worker_tokens.get(worker_id, set()))
            if not tokens:
                await asyncio.sleep(1)
                continue
            try:
                async with websockets.connect(MARKET_WS, ping_interval=None, close_timeout=5, max_size=16_000_000) as ws:
                    queue = self._queues[worker_id]
                    while not queue.empty():
                        try:
                            queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    tokens = list(self._worker_tokens.get(worker_id, set()))
                    if not tokens:
                        continue
                    epoch = self._begin_worker_epoch(worker_id)
                    await ws.send(json.dumps({"assets_ids": tokens, "type": "market", "custom_feature_enabled": True}, separators=(",", ":")))
                    self.connected_workers += 1
                    backoff = 1.0
                    hb = asyncio.create_task(self._heartbeat(ws))
                    sender = asyncio.create_task(self._subscription_sender(ws, worker_id))
                    try:
                        async for raw in ws:
                            received_at = time.time()
                            self.last_message_at = received_at
                            if raw in {"PONG", "pong"}:
                                continue
                            try:
                                msg = json.loads(raw)
                            except Exception:
                                continue
                            for row in (msg if isinstance(msg, list) else [msg]):
                                if isinstance(row, dict):
                                    self._apply(row, worker_id=worker_id, epoch=epoch, received_at=received_at)
                    finally:
                        hb.cancel()
                        sender.cancel()
                        await asyncio.gather(hb, sender, return_exceptions=True)
                        self.connected_workers = max(0, self.connected_workers - 1)
                        for token in tuple(self._worker_tokens.get(worker_id, set())):
                            if self._snapshot_epoch.get(token) == epoch:
                                self._invalidate_token(token)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("market ws worker %s disconnected: %s", worker_id, exc)
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 2)

    def _message_is_new_enough(self, token: str, remote_ts: float) -> bool:
        prior = self._token_remote_ts.get(token)
        if prior is not None and remote_ts < prior:
            self.out_of_order_ignored += 1
            return False
        return True

    def _apply(self, msg: dict, *, worker_id: int | None = None, epoch: int | None = None, received_at: float | None = None) -> None:
        typ = msg.get("event_type") or msg.get("type")
        received = float(received_at if received_at is not None else time.time())
        remote_ts = _strict_ts_seconds(msg.get("timestamp"))
        if remote_ts is None:
            return
        changed = False

        if typ == "book":
            token = str(msg.get("asset_id") or "")
            if not token or not self._token_allowed(token, worker_id) or not self._message_is_new_enough(token, remote_ts):
                return
            bids = _levels(msg.get("bids", []))
            asks = _levels(msg.get("asks", []))
            if bids is None or asks is None:
                self._invalidate_token(token)
                return
            current_epoch = self._epoch_for(token, worker_id, epoch)
            self.books[token] = Book(
                token,
                bids,
                asks,
                timestamp=str(msg.get("timestamp") or ""),
                received_at=received,
                source="clob_ws_book",
                source_epoch=current_epoch,
                book_hash=str(msg.get("hash") or "") or None,
            )
            self._snapshot_epoch[token] = current_epoch
            self._token_remote_ts[token] = remote_ts
            self.last_valid_update_at = received
            self.last_full_book_at = received
            changed = True

        elif typ == "price_change":
            current_epoch_by_token: dict[str, int] = {}
            for row in msg.get("price_changes") or msg.get("changes") or []:
                if not isinstance(row, dict):
                    continue
                token = str(row.get("asset_id") or "")
                if not token or not self._token_allowed(token, worker_id):
                    continue
                current_epoch = self._epoch_for(token, worker_id, epoch)
                current_epoch_by_token[token] = current_epoch
                if self._snapshot_epoch.get(token) != current_epoch or token not in self.books:
                    continue
                if not self._message_is_new_enough(token, remote_ts):
                    continue

                price = _valid_price(row.get("price"))
                size = _valid_size(row.get("size"))
                side = str(row.get("side") or "").strip().upper()
                if price is None or size is None or side not in {"BUY", "SELL"}:
                    self._invalidate_token(token)
                    continue

                old = self.books[token]
                bids = dict(old.bids)
                asks = dict(old.asks)
                levels = bids if side == "BUY" else asks
                if size == 0.0:
                    levels.pop(price, None)
                else:
                    levels[price] = size

                candidate = Book(
                    token,
                    list(bids.items()),
                    list(asks.items()),
                    last_trade_price=old.last_trade_price,
                    timestamp=str(msg.get("timestamp") or ""),
                    received_at=received,
                    source="clob_ws_price_change",
                    source_epoch=current_epoch,
                    book_hash=str(row.get("hash") or msg.get("hash") or old.book_hash or "") or None,
                )

                declared_bid = _valid_price(row.get("best_bid")) if row.get("best_bid") not in {None, ""} else None
                declared_ask = _valid_price(row.get("best_ask")) if row.get("best_ask") not in {None, ""} else None
                if row.get("best_bid") not in {None, ""} and declared_bid is None:
                    self._invalidate_token(token)
                    continue
                if row.get("best_ask") not in {None, ""} and declared_ask is None:
                    self._invalidate_token(token)
                    continue
                if declared_bid is not None and not _same_price(candidate.best_bid, declared_bid):
                    self._invalidate_token(token)
                    continue
                if declared_ask is not None and not _same_price(candidate.best_ask, declared_ask):
                    self._invalidate_token(token)
                    continue

                self.books[token] = candidate
                self._token_remote_ts[token] = remote_ts
                self.last_valid_update_at = received
                changed = True

        elif typ == "best_bid_ask":
            token = str(msg.get("asset_id") or "")
            if not token or not self._token_allowed(token, worker_id):
                return
            current_epoch = self._epoch_for(token, worker_id, epoch)
            if self._snapshot_epoch.get(token) != current_epoch or token not in self.books:
                return
            if not self._message_is_new_enough(token, remote_ts):
                return
            declared_bid = _valid_price(msg.get("best_bid")) if msg.get("best_bid") not in {None, ""} else None
            declared_ask = _valid_price(msg.get("best_ask")) if msg.get("best_ask") not in {None, ""} else None
            if msg.get("best_bid") not in {None, ""} and declared_bid is None:
                self._invalidate_token(token)
                return
            if msg.get("best_ask") not in {None, ""} and declared_ask is None:
                self._invalidate_token(token)
                return
            old = self.books[token]
            if not _same_price(old.best_bid, declared_bid) or not _same_price(old.best_ask, declared_ask):
                self._invalidate_token(token)
                return
            self._token_remote_ts[token] = remote_ts
            self.last_valid_update_at = received

        if changed:
            self.changed.set()


class SportsStream:
    def __init__(self) -> None:
        self.results: dict[str, dict] = {}
        self.changed = asyncio.Event()
        self.last_message_at: float | None = None
        self.connected = False
        self.last_error: str | None = None
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
                async with websockets.connect(
                    SPORTS_WS,
                    open_timeout=15,
                    close_timeout=5,
                    ping_interval=20,
                    ping_timeout=20,
                    family=socket.AF_INET6,
                ) as ws:
                    self.connected = True
                    self.last_error = None
                    backoff = 1.0
                    try:
                        async for raw in ws:
                            self.last_message_at = time.time()
                            heartbeat = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else str(raw)
                            if heartbeat.strip().strip('"').lower() == "ping":
                                await ws.send("pong")
                                continue
                            try:
                                msg = json.loads(raw)
                            except Exception:
                                continue
                            for row in (msg if isinstance(msg, list) else [msg]):
                                if not isinstance(row, dict):
                                    continue
                                payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
                                slug = str(payload.get("slug") or "")
                                if slug and any(k in payload for k in ("score", "ended", "live", "period")):
                                    self.results[slug] = payload
                                    self.changed.set()
                    finally:
                        self.connected = False
            except asyncio.CancelledError:
                self.connected = False
                raise
            except Exception as exc:
                self.connected = False
                self.last_error = repr(exc)
                log.warning("sports ws disconnected: %r", exc)
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 2)


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
        self.last_valid_update_at: float | None = None
        self.connected = False
        self.invalid_rows = 0
        self.out_of_order_ignored = 0
        self.conflicting_timestamp_rows = 0
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if settings.crypto_rtds_enabled and self._task is None:
            self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        if self._task:
            self._task.cancel(); await asyncio.gather(self._task, return_exceptions=True); self._task = None
        self.connected = False

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
                    self.connected = True
                    backoff = 1.0
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
                        await asyncio.gather(hb, return_exceptions=True)
                        self.connected = False
            except asyncio.CancelledError:
                self.connected = False
                raise
            except Exception as exc:
                self.connected = False
                log.warning("crypto RTDS disconnected: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 2)

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
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            rows = payload["data"]
        elif isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = [payload]
        else:
            rows = []

        for row in rows:
            if not isinstance(row, dict):
                self.invalid_rows += 1
                continue
            symbol = str(row.get("symbol") or (payload.get("symbol") if isinstance(payload, dict) else "")).lower()
            price = _f(row.get("value", row.get("price")))
            if not symbol or price is None or not math.isfinite(price) or price <= 0:
                self.invalid_rows += 1
                continue
            ts = _strict_ts_seconds(row.get("timestamp") or row.get("timestamp_ms") or msg.get("timestamp"))
            if ts is None:
                self.invalid_rows += 1
                continue

            key = (topic, symbol)
            prior = self.latest_ticks.get(key)
            if prior is not None and ts < prior.ts:
                self.out_of_order_ignored += 1
                continue
            if prior is not None and math.isclose(ts, prior.ts, rel_tol=0.0, abs_tol=1e-9):
                if math.isclose(price, prior.price, rel_tol=0.0, abs_tol=1e-12):
                    continue
                # Two different prices for the same source timestamp are ambiguous.
                # Remove that timestamp from usable state rather than arbitrarily
                # choosing one. A later valid timestamp can restore the stream.
                self.conflicting_timestamp_rows += 1
                self.latest_ticks.pop(key, None)
                rows_for_key = self.history[key]
                if rows_for_key and math.isclose(rows_for_key[-1].ts, ts, rel_tol=0.0, abs_tol=1e-9):
                    rows_for_key.pop()
                self.changed.set()
                continue

            tick = PriceTick(topic, symbol, price, ts)
            self.latest_ticks[key] = tick
            self.history[key].append(tick)
            self.last_valid_update_at = time.time()
            self.changed.set()
