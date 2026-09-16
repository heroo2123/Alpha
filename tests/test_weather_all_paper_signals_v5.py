from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from polymarket_scanner.weather_only_live_paper_all_signals_v3 import AllPaperV3Error
from polymarket_scanner.weather_only_live_paper_all_signals_v4 import (
    AllPaperWeatherLiveV4Service,
)
from polymarket_scanner.weather_only_live_paper_all_signals_v5 import (
    AllPaperWeatherLiveV5Service,
)
from polymarket_scanner.weather_only_maker_trade_stream import MakerTradeStreamError
from polymarket_scanner.weather_only_maker_trade_stream_v3 import (
    ProspectiveMakerTradeStreamV3,
)


TOKEN = "123456789"
NOW = 1_800_000_000.0


def test_stream_unsubscribe_forgets_inactive_token_without_global_gap():
    async def exercise():
        stream = ProspectiveMakerTradeStreamV3()
        await stream.subscribe(TOKEN)
        generation = stream.buffer.mark_gap()
        stream.buffer.mark_subscribed(TOKEN, received_at=NOW)
        assert stream.coverage(TOKEN) is not None
        await stream.unsubscribe(TOKEN)
        return stream, generation

    stream, generation = asyncio.run(exercise())
    assert stream.desired_tokens() == ()
    assert stream.coverage(TOKEN) is None
    assert stream.buffer.generation == generation
    assert stream.connected is False


def test_stream_retain_only_preserves_active_token_and_releases_stale_tokens():
    async def exercise():
        stream = ProspectiveMakerTradeStreamV3()
        await stream.subscribe("active")
        await stream.subscribe("stale-1")
        await stream.subscribe("stale-2")
        generation = stream.buffer.mark_gap()
        for token in stream.desired_tokens():
            stream.buffer.mark_subscribed(token, received_at=NOW)
        await stream.retain_only({"active"})
        return stream, generation

    stream, generation = asyncio.run(exercise())
    assert stream.desired_tokens() == ("active",)
    assert stream.coverage("active") is not None
    assert stream.coverage("stale-1") is None
    assert stream.coverage("stale-2") is None
    assert stream.buffer.generation == generation


class _FailingSocket:
    def __init__(self):
        self.closed = False

    async def send(self, payload):
        raise RuntimeError("injected dynamic subscribe failure")

    async def close(self):
        self.closed = True


def test_dynamic_subscribe_send_failure_rolls_back_local_token_and_closes_socket():
    async def exercise():
        stream = ProspectiveMakerTradeStreamV3()
        ws = _FailingSocket()
        stream.connected = True
        stream._ws = ws
        with pytest.raises(MakerTradeStreamError) as exc:
            await stream.subscribe(TOKEN)
        return stream, ws, exc.value.code

    stream, ws, code = asyncio.run(exercise())
    assert code == "MAKER_STREAM_SUBSCRIBE_SEND_FAILED"
    assert stream.desired_tokens() == ()
    assert stream.coverage(TOKEN) is None
    assert ws.closed is True


def test_v5_rejects_signal_that_expires_during_post_delivery_recheck(monkeypatch):
    expires = time.time() + 60.0

    async def fake_rebuild(self, payload, event):
        return ("compiled", "forecast", "fair", "proposal", "book", "params", SimpleNamespace(finished_at=expires + 0.1))

    monkeypatch.setattr(
        AllPaperWeatherLiveV4Service,
        "_maker_rebuild_same_proposal",
        fake_rebuild,
        raising=True,
    )
    service = object.__new__(AllPaperWeatherLiveV5Service)
    with pytest.raises(AllPaperV3Error) as exc:
        asyncio.run(
            service._maker_rebuild_same_proposal(
                {"decision_expires_at": expires},
                {},
            )
        )
    assert exc.value.code == "MAKER_SIGNAL_EXPIRED_DURING_POST_DELIVERY_RECHECK"


def test_v5_allows_post_delivery_recheck_that_finishes_before_expiry(monkeypatch):
    expires = time.time() + 60.0
    expected = ("compiled", "forecast", "fair", "proposal", "book", "params", SimpleNamespace(finished_at=expires - 10.0))

    async def fake_rebuild(self, payload, event):
        return expected

    monkeypatch.setattr(
        AllPaperWeatherLiveV4Service,
        "_maker_rebuild_same_proposal",
        fake_rebuild,
        raising=True,
    )
    service = object.__new__(AllPaperWeatherLiveV5Service)
    assert asyncio.run(
        service._maker_rebuild_same_proposal(
            {"decision_expires_at": expires},
            {},
        )
    ) == expected


class _FakeStream:
    def __init__(self, *, fail_start: bool = False):
        self.connected = True
        self.desired = set()
        self.max_desired = 0
        self._task = None
        self.retained = None
        self.fail_start = fail_start
        self.unsubscribe_calls = []

    async def subscribe(self, token):
        self.desired.add(str(token))
        self.max_desired = max(self.max_desired, len(self.desired))

    async def start(self):
        if self.fail_start:
            raise RuntimeError("injected start failure")
        return None

    def coverage(self, token):
        if str(token) in self.desired:
            return SimpleNamespace(token_id=str(token), generation=1, started_at=NOW)
        return None

    async def unsubscribe(self, token):
        self.unsubscribe_calls.append(str(token))
        self.desired.discard(str(token))

    async def retain_only(self, tokens):
        self.retained = set(tokens)
        self.desired.intersection_update(self.retained)


class _FakeStore:
    def __init__(self):
        self.active = set()

    def active_token_ids(self):
        return set(self.active)


def _service(*, stream=None):
    service = object.__new__(AllPaperWeatherLiveV5Service)
    service.maker_stream = stream or _FakeStream()
    service.maker_store = _FakeStore()
    return service


def test_v5_rejected_candidates_do_not_accumulate_toward_stream_token_cap(monkeypatch):
    async def fake_parent_send(self, candidate):
        return False, None

    monkeypatch.setattr(
        AllPaperWeatherLiveV4Service,
        "_send_maker_candidate",
        fake_parent_send,
        raising=True,
    )
    service = _service()

    async def exercise():
        for index in range(40):
            candidate = {"proposal": SimpleNamespace(token_id=f"token-{index}")}
            assert await service._send_maker_candidate(candidate) == (False, None)

    asyncio.run(exercise())
    assert service.maker_stream.desired == set()
    assert service.maker_stream.max_desired == 1


def test_v5_stream_start_failure_still_releases_subscribed_token():
    service = _service(stream=_FakeStream(fail_start=True))
    candidate = {"proposal": SimpleNamespace(token_id=TOKEN)}
    with pytest.raises(RuntimeError, match="injected start failure"):
        asyncio.run(service._send_maker_candidate(candidate))
    assert service.maker_stream.desired == set()
    assert service.maker_stream.unsubscribe_calls == [TOKEN]


def test_v5_ambiguous_delivery_result_does_not_keep_subscription(monkeypatch):
    async def fake_parent_send(self, candidate):
        return True, "PAPER_TELEGRAM_DELIVERY_UNCERTAIN"

    monkeypatch.setattr(
        AllPaperWeatherLiveV4Service,
        "_send_maker_candidate",
        fake_parent_send,
        raising=True,
    )
    service = _service()
    candidate = {"proposal": SimpleNamespace(token_id=TOKEN)}
    assert asyncio.run(service._send_maker_candidate(candidate)) == (
        True,
        "PAPER_TELEGRAM_DELIVERY_UNCERTAIN",
    )
    assert service.maker_stream.desired == set()
    assert service.maker_stream.unsubscribe_calls == [TOKEN]


def test_v5_unexpected_parent_exception_still_releases_subscription(monkeypatch):
    async def fake_parent_send(self, candidate):
        raise RuntimeError("injected parent activation failure")

    monkeypatch.setattr(
        AllPaperWeatherLiveV4Service,
        "_send_maker_candidate",
        fake_parent_send,
        raising=True,
    )
    service = _service()
    candidate = {"proposal": SimpleNamespace(token_id=TOKEN)}
    with pytest.raises(RuntimeError, match="injected parent activation failure"):
        asyncio.run(service._send_maker_candidate(candidate))
    assert service.maker_stream.desired == set()
    assert service.maker_stream.unsubscribe_calls == [TOKEN]


def test_v5_keeps_subscription_when_parent_activates_order(monkeypatch):
    async def fake_parent_send(self, candidate):
        self.maker_store.active.add(str(candidate["proposal"].token_id))
        return True, None

    monkeypatch.setattr(
        AllPaperWeatherLiveV4Service,
        "_send_maker_candidate",
        fake_parent_send,
        raising=True,
    )
    service = _service()
    candidate = {"proposal": SimpleNamespace(token_id=TOKEN)}
    assert asyncio.run(service._send_maker_candidate(candidate)) == (True, None)
    assert service.maker_stream.desired == {TOKEN}
    assert service.maker_stream.unsubscribe_calls == []


def test_v5_progress_reconciles_stream_to_exact_active_token_set(monkeypatch):
    async def fake_parent_progress(self, by_id):
        return []

    monkeypatch.setattr(
        AllPaperWeatherLiveV4Service,
        "_progress_maker_orders",
        fake_parent_progress,
        raising=True,
    )
    service = _service()
    service.maker_stream.desired = {"active", "old"}
    service.maker_store.active = {"active"}
    errors = asyncio.run(service._progress_maker_orders({}))
    assert errors == []
    assert service.maker_stream.retained == {"active"}
    assert service.maker_stream.desired == {"active"}
