from __future__ import annotations

import asyncio
import copy
import json
import subprocess
import sys
import time
from types import SimpleNamespace as NS

import pytest

from polymarket_scanner.weather_only_contract_strict import (
    StrictWeatherContractError, compile_strict_temperature_event,
)
from polymarket_scanner.weather_only_discovery import WeatherOnlyDiscovery
from polymarket_scanner.weather_only_forecast import map_ensemble_to_contract_buckets
from polymarket_scanner.weather_only_live_paper_all_signals_final_v4 import (
    FinalAllPaperWeatherLiveServiceV4,
)
from polymarket_scanner.weather_only_live_paper_all_signals_final_v3 import (
    FinalAllPaperWeatherLiveServiceV3,
)
from polymarket_scanner.weather_only_live_paper_v4 import (
    FORECAST_MAPPING_POLICY, V4InvariantError, _semantic_digest,
)
from polymarket_scanner.weather_only_operator_state_corrective_v5 import OperatorStatePostReceiptStoreV5
from test_weather_final_gpt6_exact_replays import _event, _distribution
from test_weather_current_polymarket_grammar_v7 import _current_rules
from test_weather_stage2_semantic_terminal_maker import _signal, _service, _EditTelegram, _row


@pytest.mark.parametrize("station,city", [("KSEA", "Seattle"), ("KLGA", "NYC"), ("EDDM", "Munich")])
def test_reviewed_legacy_city_station_pairs_remain_supported(station, city):
    event = _event(station=station)
    event["title"] = f"Highest temperature in {city} on September 14?"
    assert compile_strict_temperature_event(event).station_hint == station


@pytest.mark.parametrize("city,code", [
    ("Seattle", "STRICT_CITY_STATION_MISMATCH"),
    ("Unknownville", "STRICT_CITY_UNREVIEWED"),
])
def test_coherent_source_rules_cannot_authorize_wrong_or_unknown_city(city, code):
    event = _event(station="KATL")
    event["title"] = f"Highest temperature in {city} on September 14?"
    with pytest.raises(StrictWeatherContractError, match=code):
        compile_strict_temperature_event(event)


def test_current_city_question_cannot_substitute_another_reviewed_station():
    event = _event(station="KATL")
    event["title"] = "Highest temperature in Seattle on September 14?"
    event["description"] = _current_rules(statistic="highest", station="KATL",
        station_name="Hartsfield-Jackson International Airport", unit_word="Fahrenheit", unit_symbol="°F",
        target_day=14, hourly_clause='This market will resolve off of the Hourly Data provided using the "Show Hourly Data" button. ',
        switch_button="Switch to US Units w/ kts", example="21°F")
    for market, label in zip(event["markets"], ("69°F or below", "70-71°F", "72°F or higher")):
        market["question"] = f"Will the highest temperature in Seattle be {label} on September 14?"
        market["groupItemTitle"] = label
    with pytest.raises(StrictWeatherContractError, match="STRICT_CITY_STATION_MISMATCH"):
        compile_strict_temperature_event(event)


def test_unknown_temperature_grammar_stays_in_complete_semantic_denominator():
    async def run():
        discovery = WeatherOnlyDiscovery()
        unknown = [{"id": f"unknown-{i}", "title": "Daily maximum temperature in London on 2026-09-16?",
                    "markets": [{"id": f"u{i}", "question": "Will the temperature be above 20 degrees Celsius?"}],
                    "tags": [{"slug": "weather"}]} for i in range(25)]
        rows = [_event(), *unknown, {"id": "other", "title": "Election winner?"}]
        async def page(*_args, **_kwargs):
            return rows, None
        discovery._keyset_page = page
        try:
            retained, _, scanned, _ = await discovery._global_weather_census()
            status = discovery.global_recall_status()
            assert scanned == 27
            assert len(retained) == 1
            assert status["gamma_census_complete"] is True
            assert status["weather_semantic_coverage_complete"] is False
            assert status["weather_looking_events"] == 26
            assert status["unsupported_weather_events"] == 25
            ledger = status["semantic_event_ledger"]
            assert len(ledger) == 26
            assert {item["event_id"] for item in ledger} == {row["id"] for row in rows[:-1]}
            assert all(item["detail"] for item in ledger if item["classification"] != "SUPPORTED")
            assert len(status["unsupported_examples"]) < len(unknown)
        finally:
            await discovery.close()
    asyncio.run(run())


def test_huge_integer_partition_completes_without_enumerating_temperature_span():
    event = _event(labels=["-100000000°F or lower", "-99999999-99999999°F", "100000000°F or higher"])
    script = "import json,sys; from polymarket_scanner.weather_only_contract_strict import compile_strict_temperature_event; assert len(compile_strict_temperature_event(json.load(sys.stdin)).buckets)==3"
    result = subprocess.run([sys.executable, "-c", script], input=json.dumps(event), text=True,
                            capture_output=True, timeout=5, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("labels", [
    ["69°F or lower", "71-72°F", "73°F or higher"],
    ["69°F or lower", "69-70°F", "71°F or higher"],
])
def test_partition_proof_still_rejects_gap_and_overlap(labels):
    with pytest.raises(StrictWeatherContractError):
        compile_strict_temperature_event(_event(labels=labels))


def test_partition_rejects_float_precision_overlap_beyond_exact_integer_range():
    labels = ["10000000000000000°F or lower", "10000000000000000°F or higher"]
    with pytest.raises(StrictWeatherContractError, match="STRICT_PARTITION_UNPROVEN"):
        compile_strict_temperature_event(_event(labels=labels))


@pytest.mark.parametrize("provider_fails", [False, True])
def test_post_receipt_refresh_reaches_provider_with_real_800_second_semantic_cache(monkeypatch, provider_fails):
    event = _event()
    compiled = compile_strict_temperature_event(event)
    metadata = NS(latitude=40.78, longitude=-73.88, timezone="America/New_York")
    distribution = _distribution(compiled, value=70.0, received=time.time())
    cached = map_ensemble_to_contract_buckets(compiled, distribution, FORECAST_MAPPING_POLICY)
    service = object.__new__(FinalAllPaperWeatherLiveServiceV4)
    service.forecast_cache_seconds = 900
    service._forecast_cache = {_semantic_digest(compiled, metadata): (time.monotonic()-800, cached)}
    service._forecast_distribution_by_sha = {}
    calls = []
    async def station(_compiled):
        return metadata
    async def provider(**_kwargs):
        calls.append("weather")
        if provider_fails:
            raise RuntimeError("controlled fixture failure")
        return distribution
    async def quote(_self, *_args, **_kwargs):
        calls.append("quote")
        return {"post_receipt_recheck_started_at": time.time()}
    service._station_metadata_for_compiled = station
    service.forecast_client = NS(daily_extreme=provider)
    monkeypatch.setattr(FinalAllPaperWeatherLiveServiceV3, "_forecast_post_receipt_execution", quote)
    candidate = {"forecast_evidence_sha256": cached.source_evidence_sha256}
    if provider_fails:
        with pytest.raises(V4InvariantError, match="V5_FORECAST_REFRESH_FAILED"):
            asyncio.run(service._forecast_post_receipt_execution(candidate, event, telegram_sent_at=time.time()-1))
        assert calls == ["weather"]
    else:
        execution = asyncio.run(service._forecast_post_receipt_execution(candidate, event, telegram_sent_at=time.time()-1))
        assert calls == ["weather", "quote"]
        assert execution["post_receipt_weather_evidence"]["forecast_source_evidence_sha256"] == cached.source_evidence_sha256


def test_new_delivered_terminal_edit_bypasses_two_hundred_older_failed_messages(tmp_path):
    store = OperatorStatePostReceiptStoreV5(tmp_path / "signals.sqlite")
    for index in range(200):
        signal_id, _, candidate = _signal(store, suffix=str(index))
        store.terminalize_delivered_signal(signal_id, terminal_status="EXPIRED", reason="old backlog",
            decision_id=candidate["decision_id"], event_id=candidate["event_id"],
            market_id=candidate["market_id"], side="YES", recorded_at=102)
    newest, _, candidate = _signal(store, suffix="newest")
    telegram = _EditTelegram()
    async def edit(message_id, text):
        telegram.calls.append((message_id, text))
        if message_id != 1000+newest:
            raise RuntimeError("older unavailable message")
        return "APPLIED"
    telegram.edit_html = edit
    service = _service(store, telegram)
    asyncio.run(service._terminalize_delivered_signal(newest, candidate, status="EXPIRED", reason="newly expired"))
    assert [item[0] for item in telegram.calls] == [1000+newest]
    assert _row(store, newest)[1]["state"] == "APPLIED"
    assert len(store.pending_operator_sync(limit=200)) == 200
