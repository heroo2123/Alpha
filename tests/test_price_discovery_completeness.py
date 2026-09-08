import asyncio
import json

import app_stable_v2 as v2
from polymarket_scanner.models import Book


class FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = int(status_code)
        self._payload = payload
        self.text = json.dumps(payload, separators=(",", ":"))
        self.content = self.text.encode("utf-8")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeHTTP:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    async def post(self, _url, json=None):
        body = list(json or [])
        self.calls.append(body)
        return self.handler(body, len(self.calls))


def _tokens(body):
    return [str(row["token_id"]) for row in body]


def _no_sleep(monkeypatch):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(v2.asyncio, "sleep", no_sleep)


def _install_http(monkeypatch, handler):
    fake = FakeHTTP(handler)
    monkeypatch.setattr(v2.stable.base.poly, "http", fake)
    return fake


def test_all_chunks_complete_produces_transport_complete_diagnostics(monkeypatch):
    _no_sleep(monkeypatch)
    monkeypatch.setattr(v2.stable, "TOP_PRICE_CHUNK_TOKENS", 2)

    def handler(body, _call):
        return FakeResponse(200, {token: {"SELL": "0.42"} for token in _tokens(body)})

    fake = _install_http(monkeypatch, handler)
    books = asyncio.run(v2._fetch_ask_prices(["a", "b", "c", "c"]))
    diag = dict(v2._last_price_fetch_diagnostics)

    assert set(books) == {"a", "b", "c"}
    assert diag["transport_complete"] is True
    assert diag["requested_tokens"] == 3
    assert diag["chunk_count"] == 2
    assert diag["failed_chunks"] == 0
    assert diag["usable_tokens"] == 3
    assert diag["usable_coverage_ratio"] == 1.0
    assert len(fake.calls) == 2


def test_one_failed_chunk_cannot_replace_or_retimestamp_last_complete_snapshot(monkeypatch):
    _no_sleep(monkeypatch)
    monkeypatch.setattr(v2.stable, "TOP_PRICE_CHUNK_TOKENS", 2)

    def handler(body, _call):
        tokens = _tokens(body)
        if "bad-a" in tokens:
            return FakeResponse(503, {"error": "down"})
        return FakeResponse(200, {token: {"SELL": "0.40"} for token in tokens})

    _install_http(monkeypatch, handler)
    partial = asyncio.run(v2._fetch_ask_prices(["good-a", "good-b", "bad-a", "bad-b"]))
    diag = dict(v2._last_price_fetch_diagnostics)

    assert set(partial) == {"good-a", "good-b"}
    assert diag["transport_complete"] is False
    assert diag["failed_chunks"] == 1

    prior = Book("prior", bids=[], asks=[(0.5, 0.0)], timestamp="old")
    v2.stable._price_books = {"prior": prior}
    v2.stable._price_snapshot_at = 111.0
    accepted = v2._accept_price_sweep(partial, diag, now=222.0)

    assert accepted is False
    assert set(v2.stable._price_books) == {"prior"}
    assert v2.stable._price_snapshot_at == 111.0
    assert v2.stable.base.state["price_snapshot_last_attempt_transport_complete"] is False


def test_missing_response_missing_sell_and_invalid_sell_are_distinct(monkeypatch):
    _no_sleep(monkeypatch)
    monkeypatch.setattr(v2.stable, "TOP_PRICE_CHUNK_TOKENS", 20)

    def handler(_body, _call):
        return FakeResponse(
            200,
            {
                "usable": {"SELL": "0.25"},
                # "omitted" is deliberately absent from the response.
                "no-sell": {"BUY": "0.20"},
                "bad-text": {"SELL": "not-a-number"},
                "bad-range": {"SELL": "1.0"},
            },
        )

    _install_http(monkeypatch, handler)
    books = asyncio.run(
        v2._fetch_ask_prices(["usable", "omitted", "no-sell", "bad-text", "bad-range"])
    )
    diag = dict(v2._last_price_fetch_diagnostics)

    assert set(books) == {"usable"}
    assert diag["transport_complete"] is True
    assert diag["response_missing_tokens"] == 1
    assert diag["missing_sell_tokens"] == 1
    assert diag["invalid_sell_tokens"] == 2
    assert diag["response_entries"] == 4
    assert diag["usable_tokens"] == 1
    assert diag["usable_coverage_ratio"] == 0.2


def test_retry_attempts_are_included_in_request_byte_accounting(monkeypatch):
    _no_sleep(monkeypatch)
    monkeypatch.setattr(v2.stable, "TOP_PRICE_CHUNK_TOKENS", 20)
    monkeypatch.setattr(v2.stable, "TOP_PRICE_REFRESH_SECONDS", 20.0)

    calls = {"n": 0}

    def handler(body, _call):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResponse(429, {"error": "rate limited"})
        return FakeResponse(200, {token: {"SELL": "0.33"} for token in _tokens(body)})

    fake = _install_http(monkeypatch, handler)
    body = [{"token_id": "a", "side": "SELL"}, {"token_id": "b", "side": "SELL"}]
    one_attempt_bytes = v2._request_body_bytes(body)

    books = asyncio.run(v2._fetch_ask_prices(["a", "b"]))
    diag = dict(v2._last_price_fetch_diagnostics)

    assert set(books) == {"a", "b"}
    assert len(fake.calls) == 2
    assert diag["request_body_bytes"] == one_attempt_bytes * 2
    expected = diag["request_body_bytes"] * (86400.0 / 20.0) / float(1024 ** 3)
    assert diag["projected_request_gib_per_day"] == expected
    assert "not TCP/TLS overhead or cloud billing" in diag["application_byte_scope"]


def test_transport_complete_but_empty_usable_result_keeps_prior_snapshot(monkeypatch):
    _no_sleep(monkeypatch)
    monkeypatch.setattr(v2.stable, "TOP_PRICE_CHUNK_TOKENS", 20)

    def handler(body, _call):
        return FakeResponse(200, {token: {"BUY": "0.1"} for token in _tokens(body)})

    _install_http(monkeypatch, handler)
    refreshed = asyncio.run(v2._fetch_ask_prices(["a", "b"]))
    diag = dict(v2._last_price_fetch_diagnostics)
    assert refreshed == {}
    assert diag["transport_complete"] is True
    assert diag["missing_sell_tokens"] == 2

    prior = Book("prior", bids=[], asks=[(0.5, 0.0)], timestamp="old")
    v2.stable._price_books = {"prior": prior}
    v2.stable._price_snapshot_at = 321.0

    assert v2._accept_price_sweep(refreshed, diag, now=999.0) is False
    assert set(v2.stable._price_books) == {"prior"}
    assert v2.stable._price_snapshot_at == 321.0
    assert v2.stable.base.state["price_snapshot_last_attempt_transport_complete"] is True


def test_complete_nonempty_sweep_advances_snapshot_and_records_cumulative_bytes(monkeypatch):
    v2.stable.base.state.pop("price_discovery_request_bytes_total", None)
    v2.stable.base.state.pop("price_discovery_response_bytes_total", None)
    prior = Book("prior", bids=[], asks=[(0.5, 0.0)], timestamp="old")
    fresh = Book("fresh", bids=[], asks=[(0.4, 0.0)], timestamp="new")
    v2.stable._price_books = {"prior": prior}
    v2.stable._price_snapshot_at = 1.0

    diag = {
        "version": v2.PRICE_DISCOVERY_DIAGNOSTICS_VERSION,
        "transport_complete": True,
        "reason": "all chunks complete",
        "response_missing_tokens": 2,
        "missing_sell_tokens": 3,
        "invalid_sell_tokens": 4,
        "usable_coverage_ratio": 0.5,
        "request_body_bytes": 123,
        "response_body_bytes": 456,
    }

    assert v2._accept_price_sweep({"fresh": fresh}, diag, now=777.0) is True
    assert set(v2.stable._price_books) == {"fresh"}
    assert v2.stable._price_snapshot_at == 777.0
    assert v2.stable.base.state["price_snapshot_authoritative_transport_complete"] is True
    assert v2.stable.base.state["price_snapshot_response_missing_tokens"] == 2
    assert v2.stable.base.state["price_snapshot_missing_sell_tokens"] == 3
    assert v2.stable.base.state["price_snapshot_invalid_sell_tokens"] == 4
    assert v2.stable.base.state["price_discovery_request_bytes_total"] == 123
    assert v2.stable.base.state["price_discovery_response_bytes_total"] == 456
