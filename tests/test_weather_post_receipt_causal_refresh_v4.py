from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import polymarket_scanner.weather_only_live_paper_all_signals_final_v4 as runtime_v4
from polymarket_scanner.weather_only_independent_review_corrective_v3 import (
    FUTURE_DAY_KIND,
    POST_RECEIPT_WEATHER_EVIDENCE_VERSION,
    SAME_DAY_KIND,
    SOURCE_SHOCK_KIND,
    IndependentReviewPostReceiptStoreV3,
    _normalized_weather_evidence,
)
from polymarket_scanner.weather_only_live_paper_v4 import V4InvariantError
from polymarket_scanner.weather_only_paper_positions import WeatherPaperPositionError
from polymarket_scanner.weather_only_paper_post_receipt import PAPER_EXECUTION_PROTOCOL_V5


NOW = 1_800_000_000.0
SHA64 = "a" * 64


def _directional_signal(store: IndependentReviewPostReceiptStoreV3, *, lane: str = "weather_forecast_raw_gap") -> int:
    sid = store.save_signal(
        fingerprint="causal-fp",
        lane=lane,
        evidence_class="TEST",
        event_id="event-1",
        market_id="market-1",
        side="YES" if lane != "weather_official_extreme_new_exclusion" else "NO",
        token_id="token-1",
        model_probability=0.99,
        entry_cost=0.91,
        raw_gap=0.08,
        theoretical_payout=1.0,
        created_at=NOW - 1.0,
        payload={
            "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
            "decision_id": "decision-1",
            "decision_expires_at": NOW + 30.0,
            "event_title": "test",
            "financial_authority": False,
            "automatic_order_placement": False,
        },
    )
    assert sid is not None
    store.set_signal_status(sid, "PENDING_DELIVERY")
    store.mark_telegram_sent(sid, 1001, sent_at=NOW)
    store.set_signal_status(sid, "POST_RECEIPT_RECHECK")
    return sid


def _execution(*, weather: dict | None) -> dict:
    value = {
        "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
        "decision_id": "decision-1",
        "decision_expires_at": NOW + 30.0,
        "post_receipt_recheck_started_at": NOW + 2.0,
        "post_receipt_recheck_finished_at": NOW + 3.0,
        "entry_cost_per_unit": 0.91,
        "visible_units": 20.0,
        "minimum_order_size": 1.0,
        "theoretical_payout_per_unit": 1.0,
        "legs": [
            {
                "market_id": "market-1",
                "condition_id": "condition-1",
                "token_id": "token-1",
                "side": "YES",
                "ask": 0.90,
                "fee": 0.01,
                "quote_observed_at": NOW + 2.5,
                "minimum_order_size": 1.0,
                "visible_units": 20.0,
                "book_hash": "book-1",
            }
        ],
    }
    if weather is not None:
        value["post_receipt_weather_evidence"] = weather
    return value


def _future_evidence() -> dict:
    return {
        "version": POST_RECEIPT_WEATHER_EVIDENCE_VERSION,
        "kind": FUTURE_DAY_KIND,
        "provider_refresh_started_at": NOW + 0.5,
        "provider_refresh_finished_at": NOW + 1.5,
        "forecast_source_evidence_sha256": SHA64,
        "provider_run_age_known": False,
    }


def test_directional_v5_rejects_missing_weather_evidence(tmp_path):
    store = IndependentReviewPostReceiptStoreV3(tmp_path / "paper.sqlite")
    sid = _directional_signal(store)
    with pytest.raises(WeatherPaperPositionError) as exc:
        store.admit_post_receipt_position(sid, 5.0, _execution(weather=None))
    assert exc.value.code == "V5_WEATHER_EVIDENCE_MISSING"


def test_weather_evidence_survives_and_is_bound_into_execution_identity(tmp_path):
    store = IndependentReviewPostReceiptStoreV3(tmp_path / "paper.sqlite")
    sid = _directional_signal(store)
    opened = store.admit_post_receipt_position(sid, 5.0, _execution(weather=_future_evidence()))
    assert opened["status"] == "OPEN"
    with store._conn() as db:
        row = db.execute("SELECT payload_json FROM weather_paper_signals WHERE id=?", (sid,)).fetchone()
    payload = json.loads(row["payload_json"])
    execution = payload["post_receipt_execution"]
    assert execution["post_receipt_weather_evidence"]["kind"] == FUTURE_DAY_KIND
    digest = execution["post_receipt_weather_evidence_sha256"]
    assert len(digest) == 64 and set(digest) <= set("0123456789abcdef")


def test_evidence_validator_rejects_noncausal_forecast_and_29_of_31_same_day():
    late = _future_evidence()
    late["provider_refresh_finished_at"] = NOW + 3.0
    with pytest.raises(WeatherPaperPositionError) as exc:
        _normalized_weather_evidence(late, recheck_started=NOW + 2.0)
    assert exc.value.code == "V5_FORECAST_REFRESH_NOT_BEFORE_CLOB"

    weak = {
        "version": POST_RECEIPT_WEATHER_EVIDENCE_VERSION,
        "kind": SAME_DAY_KIND,
        "as_of": NOW + 1.0,
        "capture_sha256": "b" * 64,
        "wrh_evidence_sha256": "c" * 64,
        "nws_evidence_sha256": "d" * 64,
        "gefs_evidence_sha256": "e" * 64,
        "gefs_hits": 29,
        "gefs_total": 31,
        "nws_sampled_extreme": 90.0,
        "observed_extreme": 91.0,
    }
    with pytest.raises(WeatherPaperPositionError) as exc:
        _normalized_weather_evidence(weak, recheck_started=NOW + 2.0)
    assert exc.value.code == "V5_THREE_LAYER_GEFS_SUPPORT_INVALID"


def test_same_day_refreshes_all_three_layers_before_exact_clob(monkeypatch):
    service = object.__new__(runtime_v4.FinalAllPaperWeatherLiveServiceV4)
    bucket = SimpleNamespace(market_id="market-1", yes_token="yes-1")
    compiled = SimpleNamespace(event_id="event-1", buckets=(bucket,))
    metadata = SimpleNamespace()
    wrh = SimpleNamespace(fetched_at=160.0, snapshot=SimpleNamespace(evidence_sha256="1" * 64))
    nws = SimpleNamespace(received_at=161.0, evidence_sha256="2" * 64)
    gefs = SimpleNamespace(received_at=162.0, evidence_sha256="3" * 64)
    capture = SimpleNamespace(
        as_of=200.0,
        capture_sha256="4" * 64,
        as_dict=lambda: {"capture_sha256": "4" * 64},
    )
    saved = []
    service.three_layer_store = SimpleNamespace(save=lambda value: saved.append(value))

    async def metadata_for(_compiled):
        return metadata

    async def source_bundle(_compiled, _metadata):
        return wrh, nws, gefs

    service._station_metadata_for_compiled = metadata_for
    service._fetch_same_day_source_bundle = source_bundle
    monkeypatch.setattr(runtime_v4, "compile_strict_temperature_event", lambda event: compiled)
    monkeypatch.setattr(runtime_v4, "compile_temperature_rule_authority", lambda event, c: object())
    monkeypatch.setattr(runtime_v4, "build_same_day_contract_semantics", lambda c, a: object())
    monkeypatch.setattr(runtime_v4, "assemble_same_day_capture", lambda **kwargs: capture)
    monkeypatch.setattr(
        runtime_v4,
        "_friend_capture_gate",
        lambda raw, c, b: {
            "raw_support": 30 / 31,
            "gefs_hits": 30,
            "gefs_total": 31,
            "nws_sampled_extreme": 90.0,
            "observed_extreme": 91.0,
        },
    )
    monkeypatch.setattr(runtime_v4.time, "time", lambda: 200.0)

    async def quote_only(_self, candidate, event, *, after_time):
        assert after_time == 200.0
        exact = SimpleNamespace(started_at=201.0)
        params = SimpleNamespace(fee_rate=0.0, taker_only=True)
        return {"entry_cost": 0.91}, exact, bucket, params

    monkeypatch.setattr(runtime_v4.AllPaperWeatherLiveV7Service, "_same_day_exact_recheck", quote_only)
    candidate = {
        "event_id": "event-1",
        "market_id": "market-1",
        "token_id": "yes-1",
        "raw_probability": 30 / 31,
    }
    fresh, exact, _, _ = asyncio.run(
        service._same_day_exact_recheck(candidate, {}, after_time=150.0)
    )
    assert saved == [capture]
    assert exact.started_at > capture.as_of
    evidence = fresh["post_receipt_weather_evidence"]
    assert evidence["kind"] == SAME_DAY_KIND
    assert evidence["gefs_hits"] == 30 and evidence["gefs_total"] == 31
    assert evidence["nws_evidence_sha256"] == "2" * 64


def test_same_day_changed_three_layer_thesis_fails_before_clob(monkeypatch):
    service = object.__new__(runtime_v4.FinalAllPaperWeatherLiveServiceV4)
    bucket = SimpleNamespace(market_id="market-1", yes_token="yes-1")
    compiled = SimpleNamespace(event_id="event-1", buckets=(bucket,))
    service.three_layer_store = SimpleNamespace(save=lambda value: None)

    async def metadata_for(_compiled):
        return SimpleNamespace()

    async def source_bundle(_compiled, _metadata):
        return (
            SimpleNamespace(fetched_at=160.0, snapshot=SimpleNamespace(evidence_sha256="1" * 64)),
            SimpleNamespace(received_at=161.0, evidence_sha256="2" * 64),
            SimpleNamespace(received_at=162.0, evidence_sha256="3" * 64),
        )

    service._station_metadata_for_compiled = metadata_for
    service._fetch_same_day_source_bundle = source_bundle
    monkeypatch.setattr(runtime_v4, "compile_strict_temperature_event", lambda event: compiled)
    monkeypatch.setattr(runtime_v4, "compile_temperature_rule_authority", lambda event, c: object())
    monkeypatch.setattr(runtime_v4, "build_same_day_contract_semantics", lambda c, a: object())
    monkeypatch.setattr(
        runtime_v4,
        "assemble_same_day_capture",
        lambda **kwargs: SimpleNamespace(as_of=200.0, capture_sha256="4" * 64, as_dict=lambda: {}),
    )
    monkeypatch.setattr(runtime_v4, "_friend_capture_gate", lambda raw, c, b: None)
    monkeypatch.setattr(runtime_v4.time, "time", lambda: 200.0)
    clob_called = False

    async def should_not_quote(*args, **kwargs):
        nonlocal clob_called
        clob_called = True
        raise AssertionError("CLOB must not be queried after failed fresh weather thesis")

    monkeypatch.setattr(runtime_v4.AllPaperWeatherLiveV7Service, "_same_day_exact_recheck", should_not_quote)
    candidate = {"event_id": "event-1", "market_id": "market-1", "token_id": "yes-1", "raw_probability": 30 / 31}
    with pytest.raises(V4InvariantError) as exc:
        asyncio.run(service._same_day_exact_recheck(candidate, {}, after_time=150.0))
    assert exc.value.code == "V5_SAME_DAY_THREE_LAYER_THESIS_CHANGED"
    assert clob_called is False


def test_future_day_forces_uncached_provider_refresh_before_clob(monkeypatch):
    service = object.__new__(runtime_v4.FinalAllPaperWeatherLiveServiceV4)
    service._forecast_cache = {"event-1": (999.0, object())}
    compiled = SimpleNamespace(event_id="event-1")
    monkeypatch.setattr(runtime_v4, "compile_strict_temperature_event", lambda event: compiled)
    clock = iter((100.0, 101.0))
    monkeypatch.setattr(runtime_v4.time, "time", lambda: next(clock))

    async def mapped(event_id, _compiled):
        assert event_id == "event-1"
        assert "event-1" not in service._forecast_cache
        forecast = SimpleNamespace(source_evidence_sha256=SHA64)
        service._forecast_cache[event_id] = (100.0, forecast)
        return forecast

    service._mapped_forecast = mapped

    async def parent(_self, candidate, event, *, telegram_sent_at):
        return {
            "post_receipt_recheck_started_at": 102.0,
            "post_receipt_recheck_finished_at": 103.0,
            "legs": [{}],
            "visible_units": 1.0,
        }

    monkeypatch.setattr(
        runtime_v4.FinalAllPaperWeatherLiveServiceV3,
        "_forecast_post_receipt_execution",
        parent,
    )
    result = asyncio.run(
        service._forecast_post_receipt_execution(
            {"forecast_evidence_sha256": SHA64}, {}, telegram_sent_at=99.0
        )
    )
    evidence = result["post_receipt_weather_evidence"]
    assert evidence["kind"] == FUTURE_DAY_KIND
    assert evidence["provider_refresh_finished_at"] == 101.0
    assert result["post_receipt_recheck_started_at"] > evidence["provider_refresh_finished_at"]


def test_source_shock_refetches_wrh_before_clob(monkeypatch):
    service = object.__new__(runtime_v4.FinalAllPaperWeatherLiveServiceV4)
    bucket = SimpleNamespace(market_id="market-1", no_token="no-1", upper=94.0, lower=90.0)
    compiled = SimpleNamespace(event_id="event-1", buckets=(bucket,), family=runtime_v4.DAILY_HIGH)
    monkeypatch.setattr(runtime_v4, "compile_strict_temperature_event", lambda event: compiled)

    result = SimpleNamespace(fetched_at=160.0, snapshot=SimpleNamespace(evidence_sha256="f" * 64))
    observed = SimpleNamespace(observed_state=SimpleNamespace(extreme_value=95.0))

    async def fresh_wrh(_compiled, *, after_time):
        assert after_time == 150.0
        return result, observed

    service._fresh_wrh_state = fresh_wrh
    service._fresh_wrh_rows = lambda value: [(100.0, 94.0), (150.0, 95.0)]

    async def quote_after_wrh(_self, candidate, event, *, after_time, decision_expires_at):
        assert after_time == 160.0
        exact = SimpleNamespace(started_at=161.0)
        return {"entry_cost": 0.95}, exact, bucket, SimpleNamespace()

    monkeypatch.setattr(runtime_v4.AllPaperWeatherLiveV8Service, "_source_shock_exact_recheck", quote_after_wrh)
    candidate = {
        "market_id": "market-1",
        "token_id": "no-1",
        "previous_official_extreme": 94.0,
        "new_official_extreme": 95.0,
        "latest_official_observed_at": 150.0,
    }
    fresh, exact, _, _ = asyncio.run(
        service._source_shock_exact_recheck(
            candidate, {}, after_time=150.0, decision_expires_at=190.0
        )
    )
    assert exact.started_at > result.fetched_at
    assert fresh["post_receipt_weather_evidence"]["kind"] == SOURCE_SHOCK_KIND
