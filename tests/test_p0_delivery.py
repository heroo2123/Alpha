import asyncio

import pytest

import command_worker_trade_only as trade_worker
from polymarket_scanner.telegram import Telegram


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {"ok": True, "result": {"message_id": 123}}

    def json(self):
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
        return self.responses.pop(0)


def _tg():
    tg = object.__new__(Telegram)
    tg.token = "123:SECRET"
    tg.last_command_error = None
    tg.last_alert_error = None
    return tg


def _no_sleep(monkeypatch):
    async def no_sleep(_seconds):
        return None
    monkeypatch.setattr(trade_worker.asyncio, "sleep", no_sleep)


def test_three_429s_never_become_success(monkeypatch):
    _no_sleep(monkeypatch)
    client = FakeClient([
        FakeResponse(429, {"ok": False, "parameters": {"retry_after": 1}}),
        FakeResponse(429, {"ok": False, "parameters": {"retry_after": 1}}),
        FakeResponse(429, {"ok": False, "parameters": {"retry_after": 1}}),
    ])
    with pytest.raises(RuntimeError, match="rate limited|send failed"):
        asyncio.run(trade_worker._safe_post_message(_tg(), client, "1", "x", lane="alert"))
    assert client.calls == 3


def test_http_200_requires_telegram_ok_and_receipt(monkeypatch):
    _no_sleep(monkeypatch)
    rejected = FakeClient([FakeResponse(200, {"ok": False}) for _ in range(3)])
    with pytest.raises(RuntimeError, match="rejected|send failed"):
        asyncio.run(trade_worker._safe_post_message(_tg(), rejected, "1", "x", lane="command"))

    missing_receipt = FakeClient([FakeResponse(200, {"ok": True, "result": {}}) for _ in range(3)])
    with pytest.raises(RuntimeError, match="message_id|send failed"):
        asyncio.run(trade_worker._safe_post_message(_tg(), missing_receipt, "1", "x", lane="command"))


def test_success_returns_message_receipt(monkeypatch):
    _no_sleep(monkeypatch)
    client = FakeClient([FakeResponse(200, {"ok": True, "result": {"message_id": 987}})])
    result = asyncio.run(trade_worker._safe_post_message(_tg(), client, "1", "x", lane="alert"))
    assert result == 987
    assert client.calls == 1
