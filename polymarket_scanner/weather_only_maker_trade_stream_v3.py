from __future__ import annotations

"""Bounded prospective maker WebSocket subscription lifecycle.

V2 fixed the first-book coverage anchor.  V3 additionally prevents historical maker
candidates from accumulating forever in the public Market-WebSocket subscription set.
Tokens can be forgotten independently once no active virtual order needs them, and the
stream waits locally instead of reconnect-looping while the desired set is empty.

Removing one inactive token never advances the global generation for unrelated active
orders.  Any real connection loss still advances the generation through the inherited
gap semantics, so active virtual orders continue to fail closed.  A failed dynamic
subscribe is rolled back locally and the socket is closed so local/server subscription
state can never remain silently divergent.
"""

import asyncio

import websockets

from .weather_only_maker_trade_stream import (
    MAKER_TRADE_STREAM_HEARTBEAT_SECONDS,
    MAKER_TRADE_STREAM_MAX_TOKENS,
    MAKER_TRADE_STREAM_RECONNECT_MAX_SECONDS,
    MARKET_WS_URL,
    MakerTradeStreamError,
    _canonical,
    _text,
)
from .weather_only_maker_trade_stream_v2 import (
    MAKER_TRADE_STREAM_V2_VERSION,
    ProspectiveMakerTradeBufferV2,
    ProspectiveMakerTradeStreamV2,
)


MAKER_TRADE_STREAM_V3_VERSION = (
    "weather_maker_public_ws_v3_bounded_subscription_lifecycle_idle_wait"
)


class ProspectiveMakerTradeBufferV3(ProspectiveMakerTradeBufferV2):
    def forget_token(self, token_id: str) -> None:
        token = _text(token_id, "MAKER_STREAM_TOKEN_MISSING")
        self._coverage.pop(token, None)
        self._prints.pop(token, None)
        self._seen.pop(token, None)


class ProspectiveMakerTradeStreamV3(ProspectiveMakerTradeStreamV2):
    def __init__(self) -> None:
        super().__init__()
        self.buffer = ProspectiveMakerTradeBufferV3()
        self._desired_event = asyncio.Event()

    async def subscribe(self, token_id: str) -> None:
        token = _text(token_id, "MAKER_STREAM_TOKEN_MISSING")
        if token in self._desired:
            self._desired_event.set()
            return
        if len(self._desired) >= MAKER_TRADE_STREAM_MAX_TOKENS:
            raise MakerTradeStreamError("MAKER_STREAM_TOKEN_CAP")
        try:
            await super().subscribe(token)
        except Exception:
            # The foundation adds to _desired before sending a dynamic subscribe on
            # an already-connected socket.  If that send fails, roll the addition
            # back and force the socket down so any surviving active tokens re-enter
            # through a new generation rather than trusting divergent local/server
            # subscription state.
            self._desired.discard(token)
            self.buffer.forget_token(token)
            ws = self._ws
            if ws is not None:
                try:
                    await ws.close()
                except Exception:
                    pass
            raise MakerTradeStreamError("MAKER_STREAM_SUBSCRIBE_SEND_FAILED") from None
        self._desired_event.set()

    async def unsubscribe(self, token_id: str) -> None:
        token = _text(token_id, "MAKER_STREAM_TOKEN_MISSING")
        if token not in self._desired:
            self.buffer.forget_token(token)
            return
        self._desired.discard(token)
        self.buffer.forget_token(token)
        ws = self._ws
        if self.connected and ws is not None:
            try:
                async with self._send_lock:
                    await ws.send(
                        _canonical({"operation": "unsubscribe", "assets_ids": [token]})
                    )
            except Exception:
                # Local evidence for the removed token is already gone.  Force a
                # reconnect so the server-side subscription set is rebuilt exactly
                # from the remaining desired tokens; that gap correctly invalidates
                # any other active virtual orders.
                try:
                    await ws.close()
                except Exception:
                    pass
                raise MakerTradeStreamError("MAKER_STREAM_UNSUBSCRIBE_SEND_FAILED") from None
        if not self._desired and ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    async def retain_only(self, token_ids: set[str]) -> None:
        keep = {str(value).strip() for value in token_ids if str(value).strip()}
        for token in tuple(sorted(self._desired - keep)):
            await self.unsubscribe(token)

    def desired_tokens(self) -> tuple[str, ...]:
        return tuple(sorted(self._desired))

    def status(self) -> dict:
        value = dict(super().status())
        value.update(
            {
                "foundation_version": MAKER_TRADE_STREAM_V2_VERSION,
                "version": MAKER_TRADE_STREAM_V3_VERSION,
                "bounded_subscription_lifecycle": True,
                "failed_dynamic_subscribe_rolls_back": True,
                "idle_without_network_reconnect": not self._desired and not self.connected,
            }
        )
        return value

    async def _run(self) -> None:
        delay = 1.0
        while not self._closed:
            if not self._desired:
                self.connected = False
                self._ws = None
                self._desired_event.clear()
                if not self._desired:
                    await self._desired_event.wait()
                if self._closed:
                    break
                continue

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
                    if not desired:
                        self._ws = None
                        continue
                    await ws.send(
                        _canonical(
                            {
                                "assets_ids": desired,
                                "type": "market",
                                "custom_feature_enabled": False,
                            }
                        )
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
            if not self._desired:
                continue
            self.reconnect_count += 1
            await asyncio.sleep(delay)
            delay = min(MAKER_TRADE_STREAM_RECONNECT_MAX_SECONDS, delay * 2.0)
