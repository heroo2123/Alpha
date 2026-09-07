import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

import command_worker_trade_only as trade_worker
from polymarket_scanner.telegram import Telegram
from polymarket_scanner.trade_only import TradeNowPreSendInvalid


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {"ok": True, "result": {"message_id": 123}}

    def json(self):
        self._body if not isinstance(self._body, Exception) else (_ for _ in ()).throw(self._body)
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def post(self, _url, json=None):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _tg():
    tg = object.__new__(Telegram)
    tg.token = "123:SECRET"
    tg.last_command_error = None
    tg.last_alert_error = None
    tg._trade_alert_not_after_epoch = None
    return tg


def _no_sleep(monkeypatch):
    async def no_sleep(_seconds):
        return None
    monkeypatch.setattr(trade_worker.asyncio, "sleep", no_sleep)


def test_alert_expired_certificate_is_suppressed_before_any_http_call():
    tg = _tg()
    tg._trade_alert_not_after_epoch = time.time() - 1.0
    client = FakeClient([FakeResponse()])

    with pytest.raises(trade_worker.worker.AlertSuppressed, match="expiry"):
        asyncio.run(trade_worker._safe_post_message(tg, client, "1", "x", lane="alert"))
    assert client.calls == 0


def test_alert_near_expiry_is_suppressed_with_network_headroom():
    tg = _tg()
    tg._trade_alert_not_after_epoch = time.time() + 0.2
    client = FakeClient([FakeResponse()])

    with pytest.raises(trade_worker.worker.AlertSuppressed, match="expiry"):
        asyncio.run(trade_worker._safe_post_message(tg, client, "1", "x", lane="alert"))
    assert client.calls == 0


def test_alert_with_enough_certificate_life_may_cross_network_boundary():
    tg = _tg()
    tg._trade_alert_not_after_epoch = time.time() + 5.0
    client = FakeClient([FakeResponse(200, {"ok": True, "result": {"message_id": 456}})])

    result = asyncio.run(trade_worker._safe_post_message(tg, client, "1", "x", lane="alert"))
    assert result == 456
    assert client.calls == 1


def test_command_lane_ignores_financial_certificate_deadline():
    tg = _tg()
    tg._trade_alert_not_after_epoch = time.time() - 10.0
    client = FakeClient([FakeResponse(200, {"ok": True, "result": {"message_id": 457}})])

    result = asyncio.run(trade_worker._safe_post_message(tg, client, "1", "status", lane="command"))
    assert result == 457
    assert client.calls == 1


def test_guarded_send_maps_local_pre_send_failure_to_suppressed_and_clears_deadline(monkeypatch):
    signal = SimpleNamespace(
        detector="binary_buy_both",
        metadata={
            "trade_ready_expires_at": (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
        },
    )
    tg = _tg()
    tg.store = SimpleNamespace(path="unused.db")

    async def ready(_signal, _poly):
        return True

    async def fail_before_transport(_tg, _signal_id, _signal):
        raise TradeNowPreSendInvalid("expired while rendering")

    monkeypatch.setattr(trade_worker, "_poly", lambda: object())
    monkeypatch.setattr(trade_worker, "refresh_trade_readiness", ready)
    monkeypatch.setattr(trade_worker, "send_trade_now", fail_before_transport)

    with pytest.raises(trade_worker.worker.AlertSuppressed, match="expired while rendering"):
        asyncio.run(trade_worker._guarded_send_signal(tg, 1, signal))
    assert tg._trade_alert_not_after_epoch is None


def test_alert_429_is_explicit_no_delivery_and_not_blindly_retried(monkeypatch):
    _no_sleep(monkeypatch)
    client = FakeClient([
        FakeResponse(429, {"ok": False, "parameters": {"retry_after": 1}}),
        FakeResponse(200, {"ok": True, "result": {"message_id": 999}}),
    ])
    with pytest.raises(trade_worker.DeliveryRetryable, match="rate limited"):
        asyncio.run(trade_worker._safe_post_message(_tg(), client, "1", "x", lane="alert"))
    # The outbox, not the HTTP helper, owns any later retry after revalidation.
    assert client.calls == 1


def test_command_lane_may_retry_429(monkeypatch):
    _no_sleep(monkeypatch)
    client = FakeClient([
        FakeResponse(429, {"ok": False, "parameters": {"retry_after": 1}}),
        FakeResponse(429, {"ok": False, "parameters": {"retry_after": 1}}),
        FakeResponse(429, {"ok": False, "parameters": {"retry_after": 1}}),
    ])
    with pytest.raises(RuntimeError, match="rate limited"):
        asyncio.run(trade_worker._safe_post_message(_tg(), client, "1", "x", lane="command"))
    assert client.calls == 3


def test_alert_transport_timeout_is_uncertain_and_never_retried(monkeypatch):
    _no_sleep(monkeypatch)
    client = FakeClient([httpx.ReadTimeout("timeout"), FakeResponse()])
    with pytest.raises(trade_worker.DeliveryUncertain, match="uncertain"):
        asyncio.run(trade_worker._safe_post_message(_tg(), client, "1", "x", lane="alert"))
    assert client.calls == 1


def test_alert_5xx_is_uncertain(monkeypatch):
    _no_sleep(monkeypatch)
    client = FakeClient([FakeResponse(502, {"ok": False})])
    with pytest.raises(trade_worker.DeliveryUncertain, match="502"):
        asyncio.run(trade_worker._safe_post_message(_tg(), client, "1", "x", lane="alert"))
    assert client.calls == 1


def test_http_200_requires_telegram_ok_and_receipt(monkeypatch):
    _no_sleep(monkeypatch)
    rejected = FakeClient([FakeResponse(200, {"ok": False}) for _ in range(3)])
    with pytest.raises(RuntimeError, match="rejected"):
        asyncio.run(trade_worker._safe_post_message(_tg(), rejected, "1", "x", lane="command"))

    missing_receipt = FakeClient([FakeResponse(200, {"ok": True, "result": {}}) for _ in range(3)])
    with pytest.raises(RuntimeError, match="message_id"):
        asyncio.run(trade_worker._safe_post_message(_tg(), missing_receipt, "1", "x", lane="command"))


def test_alert_missing_receipt_is_uncertain(monkeypatch):
    _no_sleep(monkeypatch)
    client = FakeClient([FakeResponse(200, {"ok": True, "result": {}})])
    with pytest.raises(trade_worker.DeliveryUncertain, match="message_id"):
        asyncio.run(trade_worker._safe_post_message(_tg(), client, "1", "x", lane="alert"))
    assert client.calls == 1


def test_success_returns_message_receipt(monkeypatch):
    _no_sleep(monkeypatch)
    client = FakeClient([FakeResponse(200, {"ok": True, "result": {"message_id": 987}})])
    result = asyncio.run(trade_worker._safe_post_message(_tg(), client, "1", "x", lane="alert"))
    assert result == 987
    assert client.calls == 1
