from __future__ import annotations

"""Prospective public Market-WebSocket trade capture for maker PAPER fills.

Maker fill simulation needs complete causal prints after a virtual order starts.  The
public Data API is useful for diagnostics, but its documented ``/trades`` contract
does not promise response ordering, so offset pagination cannot prove gap-free history.
The public CLOB Market WebSocket does document real-time ``last_trade_price`` events
with asset, market, price, size, side, timestamp and transaction hash.  This module
therefore treats only a prospectively healthy WebSocket epoch as maker-fill evidence.

A disconnect/reconnect increments the stream generation and destroys coverage for
existing virtual orders.  The runtime must cancel those orders rather than backfill a
possible gap from REST.  A new token becomes covered only after a post-subscription
book snapshot is observed.  No authenticated channel, wallet, order API, or actual-fill
authority exists here.
"""

import asyncio
import hashlib
import json
import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Callable

import websockets

from .weather_only_maker_shadow import PublicTradePrint


MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
MAKER_TRADE_STREAM_VERSION = "weather_maker_public_ws_v1_prospective_gap_fail_closed"
MAKER_TRADE_STREAM_HEARTBEAT_SECONDS = 10.0
MAKER_TRADE_STREAM_RECONNECT_MAX_SECONDS = 10.0
MAKER_TRADE_STREAM_MAX_TOKENS = 32
MAKER_TRADE_STREAM_MAX_PRINTS_PER_TOKEN = 5_000


class MakerTradeStreamError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite(value: object, code: str, *, positive: bool = False) -> float:
    if value is None or isinstance(value, bool):
        raise MakerTradeStreamError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise MakerTradeStreamError(code) from None
    if not math.isfinite(number) or (positive and number <= 0.0):
        raise MakerTradeStreamError(code)
    return number


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MakerTradeStreamError(code)
    return value.strip()


def _event_epoch(raw: object) -> float:
    value = _finite(raw, "MAKER_STREAM_TIMESTAMP_INVALID", positive=True)
    # Current Market Channel examples are epoch milliseconds.  Accept epoch seconds
    # as well so a provider representation change does not silently shift chronology.
    if value >= 100_000_000_000:
        value /= 1000.0
    if value < 1_000_000_000:
        raise MakerTradeStreamError("MAKER_STREAM_TIMESTAMP_INVALID")
    return value


def _canonical(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError):
        raise MakerTradeStreamError("MAKER_STREAM_JSON_INVALID") from None


def _message_objects(raw: object) -> tuple[dict, ...]:
    value = raw
    if isinstance(raw, (bytes, bytearray)):
        try:
            value = bytes(raw).decode("utf-8")
        except UnicodeError:
            raise MakerTradeStreamError("MAKER_STREAM_MESSAGE_ENCODING_INVALID") from None
    if isinstance(value, str):
        if value == "PONG":
            return ()
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            raise MakerTradeStreamError("MAKER_STREAM_MESSAGE_JSON_INVALID") from None
    if isinstance(value, dict):
        return (value,)
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return tuple(value)
    raise MakerTradeStreamError("MAKER_STREAM_MESSAGE_SHAPE_INVALID")


def parse_last_trade_price_message(message: object, *, received_at: float) -> PublicTradePrint | None:
    if not isinstance(message, dict):
        raise MakerTradeStreamError("MAKER_STREAM_EVENT_INVALID")
    if str(message.get("event_type") or "") != "last_trade_price":
        return None
    token = _text(message.get("asset_id"), "MAKER_STREAM_TOKEN_MISSING")
    market = _text(message.get("market"), "MAKER_STREAM_MARKET_MISSING").lower()
    if not market.startswith("0x") or len(market) != 66 or any(
        ch not in "0123456789abcdef" for ch in market[2:]
    ):
        raise MakerTradeStreamError("MAKER_STREAM_MARKET_INVALID")
    price = _finite(message.get("price"), "MAKER_STREAM_PRICE_INVALID", positive=True)
    size = _finite(message.get("size"), "MAKER_STREAM_SIZE_INVALID", positive=True)
    if price >= 1.0:
        raise MakerTradeStreamError("MAKER_STREAM_PRICE_INVALID")
    side = _text(message.get("side"), "MAKER_STREAM_SIDE_MISSING").upper()
    if side not in {"BUY", "SELL"}:
        raise MakerTradeStreamError("MAKER_STREAM_SIDE_INVALID")
    executed_at = _event_epoch(message.get("timestamp"))
    receipt = _finite(received_at, "MAKER_STREAM_RECEIPT_INVALID")
    if receipt < 0.0 or executed_at > receipt + 2.0:
        raise MakerTradeStreamError("MAKER_STREAM_EVENT_FROM_FUTURE")
    transaction = _text(
        message.get("transaction_hash"), "MAKER_STREAM_TRANSACTION_HASH_MISSING"
    ).lower()
    identity = {
        "market": market,
        "token": token,
        "price": price,
        "size": size,
        "side": side,
        "executed_at": executed_at,
        "transaction_hash": transaction,
    }
    trade_id = "ws:" + hashlib.sha256(_canonical(identity).encode("utf-8")).hexdigest()
    return PublicTradePrint(
        trade_id=trade_id,
        token_id=token,
        price=price,
        shares=size,
        received_at=receipt,
        aggressor_side=side,
        source="POLYMARKET_MARKET_WS_LAST_TRADE_PRICE",
        executed_at=executed_at,
    )


@dataclass(frozen=True, slots=True)
class MakerStreamCoverage:
    token_id: str
    generation: int
    started_at: float


class ProspectiveMakerTradeBuffer:
    """Bounded per-token prospective evidence tied to one uninterrupted WS epoch."""

    def __init__(self) -> None:
        self.generation = 0
        self._coverage: dict[str, float] = {}
        self._prints: dict[str, deque[PublicTradePrint]] = defaultdict(
            lambda: deque(maxlen=MAKER_TRADE_STREAM_MAX_PRINTS_PER_TOKEN)
        )
        self._seen: dict[str, set[str]] = defaultdict(set)

    def mark_gap(self) -> int:
        self.generation += 1
        self._coverage.clear()
        self._prints.clear()
        self._seen.clear()
        return self.generation

    def mark_subscribed(self, token_id: str, *, received_at: float) -> MakerStreamCoverage:
        token = _text(token_id, "MAKER_STREAM_TOKEN_MISSING")
        receipt = _finite(received_at, "MAKER_STREAM_RECEIPT_INVALID")
        if receipt < 0.0:
            raise MakerTradeStreamError("MAKER_STREAM_RECEIPT_INVALID")
        self._coverage[token] = receipt
        return MakerStreamCoverage(token, self.generation, receipt)

    def coverage(self, token_id: str) -> MakerStreamCoverage | None:
        token = str(token_id or "").strip()
        started = self._coverage.get(token)
        if started is None:
            return None
        return MakerStreamCoverage(token, self.generation, started)

    def record(self, trade: PublicTradePrint) -> None:
        if not isinstance(trade, PublicTradePrint):
            raise MakerTradeStreamError("MAKER_STREAM_TRADE_TYPE_INVALID")
        if trade.token_id not in self._coverage:
            return
        if trade.trade_id in self._seen[trade.token_id]:
            return
        queue = self._prints[trade.token_id]
        if len(queue) >= MAKER_TRADE_STREAM_MAX_PRINTS_PER_TOKEN:
            # Losing an unprocessed prospective print would create an unknowable queue
            # gap, so invalidate the entire epoch instead of silently dropping it.
            self.mark_gap()
            raise MakerTradeStreamError("MAKER_STREAM_PRINT_BUFFER_CAP")
        queue.append(trade)
        self._seen[trade.token_id].add(trade.trade_id)

    def trades_for_order(
        self,
        *,
        token_id: str,
        order_created_at: float,
        required_generation: int,
    ) -> tuple[PublicTradePrint, ...]:
        token = _text(token_id, "MAKER_STREAM_TOKEN_MISSING")
        created = _finite(order_created_at, "MAKER_STREAM_ORDER_TIME_INVALID")
        if required_generation != self.generation:
            raise MakerTradeStreamError("MAKER_STREAM_GENERATION_GAP")
        started = self._coverage.get(token)
        if started is None or started > created + 1e-9:
            raise MakerTradeStreamError("MAKER_STREAM_ORDER_PREDATES_COVERAGE")
        return tuple(
            row for row in self._prints.get(token, ())
            if row.effective_executed_at > created + 1e-12
        )

    def prune_before(self, cutoff: float) -> None:
        threshold = _finite(cutoff, "MAKER_STREAM_PRUNE_TIME_INVALID")
        for token in list(self._prints):
            rows = self._prints[token]
            retained = [row for row in rows if row.effective_executed_at >= threshold]
            self._prints[token] = deque(
                retained, maxlen=MAKER_TRADE_STREAM_MAX_PRINTS_PER_TOKEN
            )
            self._seen[token] = {row.trade_id for row in retained}


class ProspectiveMakerTradeStream:
    """One public WS connection with dynamic token subscriptions and gap epochs."""

    def __init__(self) -> None:
        self.buffer = ProspectiveMakerTradeBuffer()
        self._desired: set[str] = set()
        self._ws = None
        self._task: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()
        self.connected = False
        self.last_message_at: float | None = None
        self.last_error: str | None = None
        self.reconnect_count = 0
        self._closed = False

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._closed = False
            self._task = asyncio.create_task(self._run(), name="weather-maker-public-market-ws")

    async def close(self) -> None:
        self._closed = True
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        self.connected = False

    async def subscribe(self, token_id: str) -> None:
        token = _text(token_id, "MAKER_STREAM_TOKEN_MISSING")
        if token in self._desired:
            return
        if len(self._desired) >= MAKER_TRADE_STREAM_MAX_TOKENS:
            raise MakerTradeStreamError("MAKER_STREAM_TOKEN_CAP")
        self._desired.add(token)
        ws = self._ws
        if self.connected and ws is not None:
            async with self._send_lock:
                await ws.send(_canonical({"operation": "subscribe", "assets_ids": [token]}))

    def coverage(self, token_id: str) -> MakerStreamCoverage | None:
        return self.buffer.coverage(token_id)

    def status(self) -> dict:
        return {
            "version": MAKER_TRADE_STREAM_VERSION,
            "connected": bool(self.connected),
            "generation": int(self.buffer.generation),
            "desired_tokens": len(self._desired),
            "covered_tokens": sum(1 for token in self._desired if self.buffer.coverage(token)),
            "last_message_at": self.last_message_at,
            "last_error": self.last_error,
            "reconnect_count": self.reconnect_count,
            "authenticated": False,
            "actual_fill_authority": False,
            "financial_authority": False,
        }

    async def _heartbeat(self, ws) -> None:
        while True:
            await asyncio.sleep(MAKER_TRADE_STREAM_HEARTBEAT_SECONDS)
            async with self._send_lock:
                await ws.send("PING")

    async def _consume(self, ws) -> None:
        async for raw in ws:
            receipt = time.time()
            self.last_message_at = receipt
            for message in _message_objects(raw):
                event_type = str(message.get("event_type") or "")
                if event_type == "book":
                    token = _text(message.get("asset_id"), "MAKER_STREAM_TOKEN_MISSING")
                    if token in self._desired:
                        self.buffer.mark_subscribed(token, received_at=receipt)
                    continue
                trade = parse_last_trade_price_message(message, received_at=receipt)
                if trade is not None:
                    self.buffer.record(trade)

    async def _run(self) -> None:
        delay = 1.0
        while not self._closed:
            heartbeat = None
            try:
                self.buffer.mark_gap()
                async with websockets.connect(
                    MARKET_WS_URL,
                    ping_interval=None,
                    close_timeout=5,
                    open_timeout=10,
                    max_size=2 * 1024 * 1024,
                    max_queue=256,
                ) as ws:
                    self._ws = ws
                    desired = sorted(self._desired)
                    # The server requires an initial subscription immediately after
                    # connect.  With no desired token yet, connect lazily by waiting.
                    if not desired:
                        self._ws = None
                        await ws.close()
                        await asyncio.sleep(0.5)
                        continue
                    await ws.send(
                        _canonical({
                            "assets_ids": desired,
                            "type": "market",
                            "custom_feature_enabled": False,
                        })
                    )
                    self.connected = True
                    self.last_error = None
                    delay = 1.0
                    heartbeat = asyncio.create_task(self._heartbeat(ws))
                    await self._consume(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = getattr(exc, "code", type(exc).__name__)
            finally:
                self.connected = False
                self._ws = None
                if heartbeat is not None:
                    heartbeat.cancel()
                    await asyncio.gather(heartbeat, return_exceptions=True)
            if self._closed:
                break
            self.reconnect_count += 1
            await asyncio.sleep(delay)
            delay = min(MAKER_TRADE_STREAM_RECONNECT_MAX_SECONDS, delay * 2.0)
