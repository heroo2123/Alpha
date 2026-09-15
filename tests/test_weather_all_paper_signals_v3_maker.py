from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from polymarket_scanner.models import Book
from polymarket_scanner.weather_only_clob import WeatherMarketParameters
from polymarket_scanner.weather_only_live_paper_all_signals_v3 import (
    AllPaperV3Error,
    AllPaperWeatherLiveV3Service,
    MAKER_EVIDENCE_CLASS,
    MAKER_LANE,
    _order_ws_generation,
)
from polymarket_scanner.weather_only_maker import FairValueBand, MakerBidProposal
from polymarket_scanner.weather_only_maker_paper_accounting import MakerPaperAccountingStore
from polymarket_scanner.weather_only_maker_research_policy import MakerResearchPolicy
from polymarket_scanner.weather_only_maker_shadow import (
    CANCELLED,
    PARTIALLY_SIMULATED,
    MakerShadowPolicy,
    create_virtual_maker_order,
)
from polymarket_scanner.weather_only_maker_trade_stream import MakerStreamCoverage


NOW = 1_800_000_000.0
TOKEN = "YES-1"


class _Stream:
    def __init__(self, *, connected=True, generation=7, started_at=NOW - 10.0):
        self.connected = connected
        self.buffer = SimpleNamespace(generation=generation)
        self._coverage = MakerStreamCoverage(TOKEN, generation, started_at)

    def coverage(self, token_id):
        return self._coverage if token_id == TOKEN else None


def _fair():
    return FairValueBand(
        token_id=TOKEN,
        lower=0.60,
        point=20 / 31,
        upper=0.75,
        model_version="weather_maker_gefs31_minus3_uncalibrated_v1|source=" + "a" * 64 + "|stress=3_of_31",
        as_of=NOW - 30.0,
        calibrated=False,
    )


def _proposal():
    fair = _fair()
    return MakerBidProposal(
        version="weather_maker_v2_fee_fail_closed_unique_fill_accounting_shadow",
        evidence_class="MAKER_CONDITIONAL",
        event_id="event-1",
        market_id="market-1",
        condition_id="condition-1",
        token_id=TOKEN,
        outcome="Yes",
        bid_price=0.40,
        max_shares=25.0,
        max_notional=10.0,
        fair_lower=fair.lower,
        fair_point=fair.point,
        fair_upper=fair.upper,
        conditional_edge_per_share=0.20,
        current_best_bid=0.39,
        current_best_ask=0.50,
        minimum_tick_size=0.01,
        minimum_order_size=1.0,
        model_version=fair.model_version,
        calibrated=False,
        cancel_conditions=("research",),
        financial_authority=False,
    )


def _book(received_at=NOW + 1.9):
    return Book(
        token_id=TOKEN,
        bids=[(0.40, 3.0), (0.39, 5.0)],
        asks=[(0.50, 4.0)],
        received_at=received_at,
        source="exact",
        book_hash="book-hash",
    )


def _params():
    return WeatherMarketParameters(
        condition_id="condition-1",
        token_outcomes=((TOKEN, "Yes"), ("NO-1", "No")),
        minimum_order_size=1.0,
        minimum_tick_size=0.01,
        fee_rate=0.0,
        fee_exponent=0,
        taker_only=None,
        maker_base_fee_bps=0,
        taker_base_fee_bps=0,
        rfq_enabled=False,
        taker_delay_enabled=False,
        received_at=NOW + 1.9,
    )


def _payload(generation=7):
    return {
        "order_id": "maker-order-1",
        "event_id": "event-1",
        "market_id": "market-1",
        "condition_id": "condition-1",
        "token_id": TOKEN,
        "side": "YES",
        "bid_price": 0.40,
        "forecast_evidence_sha256": "a" * 64,
        "contract_sha256": "b" * 64,
        "decision_expires_at": NOW + 20.0,
        "ws_generation_at_signal": generation,
        "fair_value_research": {"fair_lower_reference": 0.60},
    }


def _activation_service(tmp_path, stream=None):
    service = object.__new__(AllPaperWeatherLiveV3Service)
    service.maker_policy = MakerResearchPolicy()
    service.maker_shadow_policy = service.maker_policy.shadow_policy()
    service.maker_store = MakerPaperAccountingStore(tmp_path / "maker.sqlite")
    service.maker_stream = stream or _Stream()
    service._maker_orders_activated = 0
    return service


def _rebuild_result(*, started_at=NOW + 1.0, finished_at=NOW + 2.0):
    return (
        SimpleNamespace(event_id="event-1"),
        SimpleNamespace(source_evidence_sha256="a" * 64),
        _fair(),
        _proposal(),
        _book(received_at=finished_at - 0.1),
        _params(),
        SimpleNamespace(started_at=started_at, finished_at=finished_at),
    )


def test_maker_payload_is_explicitly_uncalibrated_and_not_v4_taker_fill():
    service = object.__new__(AllPaperWeatherLiveV3Service)
    service.maker_policy = MakerResearchPolicy()
    candidate = {
        "proposal": _proposal(),
        "fair": _fair(),
        "event": {"id": "event-1", "title": "Weather event", "slug": "weather-event"},
        "forecast_evidence_sha256": "a" * 64,
        "forecast_received_at": NOW - 30.0,
        "contract_sha256": "b" * 64,
        "contract_version": "strict-test",
        "book_received_at": NOW - 0.1,
        "book_hash": "book-hash",
        "pre_delivery_queue_at_bid": 3.0,
        "exact_finished_at": NOW,
    }
    payload = service._maker_signal_payload(candidate, coverage_generation=7)
    assert payload["lane"] == MAKER_LANE
    assert payload["evidence_class"] == MAKER_EVIDENCE_CLASS
    assert payload["calibrated_probability"] is False
    assert payload["confidence_interval"] is False
    assert payload["actual_order_placed"] is False
    assert payload["actual_fill_authority"] is False
    assert payload["financial_authority"] is False
    assert "paper_execution_protocol_version" not in payload


def test_activation_rejects_ws_generation_change_during_delivery(tmp_path, monkeypatch):
    service = _activation_service(tmp_path, _Stream(generation=8))
    monkeypatch.setattr(
        "polymarket_scanner.weather_only_live_paper_all_signals_v3.time.time",
        lambda: NOW + 1.0,
    )
    with pytest.raises(AllPaperV3Error) as exc:
        asyncio.run(service._activate_maker_after_delivery(
            payload=_payload(generation=7),
            event={"id": "event-1"},
            signal_id=1,
            telegram_message_id=2,
            telegram_sent_at=NOW + 0.5,
        ))
    assert exc.value.code == "MAKER_STREAM_GENERATION_CHANGED_DURING_DELIVERY"
    assert service.maker_store.orders() == []
    service.maker_store.close()


def test_activation_requires_post_telegram_causal_exact_recheck(tmp_path, monkeypatch):
    service = _activation_service(tmp_path)
    async def fake_rebuild(payload, event):
        return _rebuild_result(started_at=NOW, finished_at=NOW + 0.2)
    service._maker_rebuild_same_proposal = fake_rebuild
    monkeypatch.setattr(
        "polymarket_scanner.weather_only_live_paper_all_signals_v3.time.time",
        lambda: NOW + 1.0,
    )
    with pytest.raises(AllPaperV3Error) as exc:
        asyncio.run(service._activate_maker_after_delivery(
            payload=_payload(),
            event={"id": "event-1"},
            signal_id=1,
            telegram_message_id=2,
            telegram_sent_at=NOW + 0.5,
        ))
    assert exc.value.code == "MAKER_POST_DELIVERY_RECHECK_NOT_CAUSAL"
    assert service.maker_store.orders() == []
    service.maker_store.close()


def test_successful_activation_starts_after_telegram_and_persists_queue(tmp_path, monkeypatch):
    service = _activation_service(tmp_path)
    async def fake_rebuild(payload, event):
        return _rebuild_result(started_at=NOW + 1.0, finished_at=NOW + 2.0)
    service._maker_rebuild_same_proposal = fake_rebuild
    monkeypatch.setattr(
        "polymarket_scanner.weather_only_live_paper_all_signals_v3.time.time",
        lambda: NOW + 1.0,
    )
    order = asyncio.run(service._activate_maker_after_delivery(
        payload=_payload(),
        event={"id": "event-1"},
        signal_id=11,
        telegram_message_id=22,
        telegram_sent_at=NOW + 0.5,
    ))
    assert order.created_at == pytest.approx(NOW + 2.0)
    assert order.queue_ahead_shares == pytest.approx(3.0)
    assert order.simulated_filled_shares == 0.0
    assert order.actual_order_placed is False
    assert order.actual_fill_authority is False
    assert _order_ws_generation(order) == 7
    audit = service.maker_store.audit_order(order.order_id)
    assert audit["event_count"] == 2  # ORDER_CREATED + TELEGRAM_SIGNAL_LINKED
    assert service._maker_orders_activated == 1
    service.maker_store.close()


def test_disconnected_stream_cancels_partial_order_but_preserves_fill(tmp_path):
    service = _activation_service(tmp_path, _Stream(connected=False))
    base = create_virtual_maker_order(
        order_id="order-partial",
        proposal=_proposal(),
        fair=_fair(),
        book=_book(received_at=NOW - 0.1),
        parameters=_params(),
        policy=service.maker_shadow_policy,
        created_at=NOW,
        contract_evidence_sha256="b" * 64,
        source_generation="a" * 64 + "|ws_generation=7",
    )
    partial = replace(
        base,
        status=PARTIALLY_SIMULATED,
        queue_ahead_shares=0.0,
        simulated_filled_shares=2.0,
    )
    service.maker_store.save_new_order(partial)
    errors = asyncio.run(service._progress_maker_orders({}))
    assert errors == []
    final = service.maker_store.load_order(partial.order_id)
    assert final.status == CANCELLED
    assert final.simulated_filled_shares == pytest.approx(2.0)
    assert service.maker_store.unsettled_filled_orders()[0].order_id == partial.order_id
    service.maker_store.close()


def test_order_ws_generation_parser_fails_closed_on_missing_marker():
    order = SimpleNamespace(source_generation="forecast-only")
    with pytest.raises(AllPaperV3Error) as exc:
        _order_ws_generation(order)
    assert exc.value.code == "MAKER_ORDER_WS_GENERATION_MISSING"
