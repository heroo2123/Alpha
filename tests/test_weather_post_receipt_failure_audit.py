from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import polymarket_scanner.weather_only_live_paper_all_signals_final_v4 as runtime_v4
from polymarket_scanner.weather_only_live_paper_v4 import V4InvariantError


def test_same_day_metadata_failure_becomes_auditable_v4_invariant(monkeypatch):
    service = object.__new__(runtime_v4.FinalAllPaperWeatherLiveServiceV4)
    bucket = SimpleNamespace(market_id="market-1", yes_token="yes-1")
    compiled = SimpleNamespace(event_id="event-1", buckets=(bucket,))
    monkeypatch.setattr(runtime_v4, "compile_strict_temperature_event", lambda event: compiled)

    async def fail_metadata(_compiled):
        raise RuntimeError("metadata transport failed")

    service._station_metadata_for_compiled = fail_metadata
    candidate = {"market_id": "market-1", "token_id": "yes-1"}
    with pytest.raises(V4InvariantError) as exc:
        asyncio.run(
            service._fresh_three_layer_friend_gate(candidate, {}, after_time=100.0)
        )
    assert exc.value.code == "V5_SAME_DAY_THREE_LAYER_REFRESH_FAILED:RuntimeError"


def test_same_day_capture_assembly_failure_becomes_auditable_v4_invariant(monkeypatch):
    service = object.__new__(runtime_v4.FinalAllPaperWeatherLiveServiceV4)
    bucket = SimpleNamespace(market_id="market-1", yes_token="yes-1")
    compiled = SimpleNamespace(event_id="event-1", buckets=(bucket,))
    metadata = SimpleNamespace()
    wrh = SimpleNamespace(fetched_at=110.0, snapshot=SimpleNamespace())
    nws = SimpleNamespace(received_at=111.0)
    gefs = SimpleNamespace(received_at=112.0)
    monkeypatch.setattr(runtime_v4, "compile_strict_temperature_event", lambda event: compiled)
    service._station_metadata_for_compiled = lambda _compiled: None

    async def metadata_for(_compiled):
        return metadata

    async def bundle(_compiled, _metadata):
        return wrh, nws, gefs

    service._station_metadata_for_compiled = metadata_for
    service._fetch_same_day_source_bundle = bundle
    monkeypatch.setattr(runtime_v4.time, "time", lambda: 120.0)
    monkeypatch.setattr(runtime_v4, "compile_temperature_rule_authority", lambda event, c: object())
    monkeypatch.setattr(runtime_v4, "build_same_day_contract_semantics", lambda c, a: object())

    def fail_assembly(**kwargs):
        raise RuntimeError("bad capture")

    monkeypatch.setattr(runtime_v4, "assemble_same_day_capture", fail_assembly)
    candidate = {"market_id": "market-1", "token_id": "yes-1"}
    with pytest.raises(V4InvariantError) as exc:
        asyncio.run(
            service._fresh_three_layer_friend_gate(candidate, {}, after_time=100.0)
        )
    assert exc.value.code == "V5_SAME_DAY_THREE_LAYER_ASSEMBLY_FAILED:RuntimeError"


def test_source_shock_wrh_failure_becomes_auditable_v4_invariant(monkeypatch):
    service = object.__new__(runtime_v4.FinalAllPaperWeatherLiveServiceV4)
    bucket = SimpleNamespace(market_id="market-1", no_token="no-1")
    compiled = SimpleNamespace(event_id="event-1", buckets=(bucket,), family=runtime_v4.DAILY_HIGH)
    monkeypatch.setattr(runtime_v4, "compile_strict_temperature_event", lambda event: compiled)

    async def fail_wrh(_compiled, *, after_time):
        raise RuntimeError("WRH unavailable")

    service._fresh_wrh_state = fail_wrh
    candidate = {"market_id": "market-1", "token_id": "no-1"}
    with pytest.raises(V4InvariantError) as exc:
        asyncio.run(
            service._source_shock_exact_recheck(
                candidate,
                {},
                after_time=100.0,
                decision_expires_at=150.0,
            )
        )
    assert exc.value.code == "V5_SOURCE_SHOCK_WRH_REFRESH_FAILED:RuntimeError"
