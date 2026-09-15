from __future__ import annotations

"""Corrective prospective maker stream coverage semantics.

The Market WebSocket can publish additional ``book`` snapshots after the initial
subscription snapshot.  Coverage begins at the first accepted post-subscription book
in an uninterrupted generation and must not move forward merely because a later book
arrives.  This wrapper preserves that invariant while retaining the fail-closed gap
semantics of v1.
"""

from .weather_only_maker_trade_stream import (
    MAKER_TRADE_STREAM_VERSION,
    MakerStreamCoverage,
    ProspectiveMakerTradeBuffer,
    ProspectiveMakerTradeStream,
    _finite,
    _text,
)


MAKER_TRADE_STREAM_V2_VERSION = (
    "weather_maker_public_ws_v2_first_book_coverage_anchor_gap_fail_closed"
)


class ProspectiveMakerTradeBufferV2(ProspectiveMakerTradeBuffer):
    def mark_subscribed(self, token_id: str, *, received_at: float) -> MakerStreamCoverage:
        token = _text(token_id, "MAKER_STREAM_TOKEN_MISSING")
        receipt = _finite(received_at, "MAKER_STREAM_RECEIPT_INVALID")
        if receipt < 0.0:
            from .weather_only_maker_trade_stream import MakerTradeStreamError
            raise MakerTradeStreamError("MAKER_STREAM_RECEIPT_INVALID")
        existing = self.coverage(token)
        if existing is not None:
            return existing
        return super().mark_subscribed(token, received_at=receipt)


class ProspectiveMakerTradeStreamV2(ProspectiveMakerTradeStream):
    def __init__(self) -> None:
        super().__init__()
        self.buffer = ProspectiveMakerTradeBufferV2()

    def status(self) -> dict:
        value = dict(super().status())
        value["foundation_version"] = MAKER_TRADE_STREAM_VERSION
        value["version"] = MAKER_TRADE_STREAM_V2_VERSION
        value["first_book_coverage_anchor"] = True
        return value
