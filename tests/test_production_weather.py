from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
from datetime import date, datetime, timedelta
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import httpx
import pytest

from polymarket_scanner.production.weather import PublicWeatherSources, WeatherPipeline, WeatherUnavailable
from polymarket_scanner.weather_only_clob import WeatherExecutionSnapshot, parse_book, parse_market_info
from polymarket_scanner.weather_only_contract_strict import compile_strict_temperature_event
from polymarket_scanner.weather_only_gefs_hourly import parse_open_meteo_gefs_hourly_target_day
from polymarket_scanner.weather_only_nws_near_term import build_nws_raw_snapshot
from polymarket_scanner.weather_only_station_metadata import parse_nws_station_metadata
from polymarket_scanner.weather_only_wrh import parse_synoptic_wrh_hourly_snapshot
from test_weather_final_gpt6_exact_replays import _event, _distribution, _info_json

ZONE = ZoneInfo("America/New_York")
LAT, LON = 40.78, -73.88
NOW = datetime(2026, 9, 13, 15, 30, tzinfo=ZONE).timestamp()


class Closed:
    async def close(self):
        pass


class Metadata(Closed):
    def __init__(self, clock):
        self.clock = clock
    async def station(self, station):
        return parse_nws_station_metadata({"type": "Feature", "geometry": {
            "type": "Point", "coordinates": [LON, LAT]}, "properties": {
            "stationIdentifier": station, "timeZone": str(ZONE)}},
            requested_station=station, received_at=self.clock())


class Public(Closed):
    def __init__(self, event, clock):
        self.raw = copy.deepcopy(event)
        self.clock = clock
        self.value = 70
        self.ask = .4
        self.bid = .3
        self.fee = .05
        self.size = 100
        self.stale = 0
        self.forecast_age = 0
        self.calls = []
        self.resolved = {}
    async def event(self, event_id):
        assert event_id == self.raw["id"]
        self.calls.append("event")
        return copy.deepcopy(self.raw)
    async def daily_extreme(self, compiled, metadata):
        self.calls.append("weather")
        return _distribution(compiled, value=self.value, received=self.clock()-self.forecast_age)
    async def neg_risk(self, token):
        return False
    async def exact_event_snapshot(self, compiled):
        self.calls.append("quote")
        now = self.clock()
        infos, books = {}, {}
        for bucket in compiled.buckets:
            raw = _info_json(bucket)
            raw["fd"]["r"] = self.fee
            infos[bucket.condition_id] = parse_market_info(bucket.condition_id, raw, received_at=now)
            for token in (bucket.yes_token, bucket.no_token):
                books[token] = parse_book(token, {"asset_id": token, "timestamp": str((now-self.stale)*1000),
                    "hash": "book-"+token, "bids": [{"price": str(self.bid), "size": "100"}],
                    "asks": [{"price": str(self.ask), "size": str(self.size)}]}, received_at=now)
        return WeatherExecutionSnapshot("fixture", compiled.event_id, books, infos, now, now, True, False)
    async def market(self, market_id):
        return copy.deepcopy(self.resolved[market_id])


def pipeline(event=None, now=NOW):
    clock = lambda: now
    public = Public(event or _event(), clock)
    service = WeatherPipeline(public=public, discovery=Closed(), metadata=Metadata(clock),
        wrh=Closed(), near_term=Closed(), hourly=Closed(), clock=clock)
    return service, public


def candidates(service, event, strategies):
    return asyncio.run(service.evaluate(event, strategies, .05, .01))


def test_directional_and_maker_are_real_public_decisions_without_financial_component():
    service, public = pipeline()
    rows = candidates(service, public.raw, ["DIRECTIONAL", "MAKER"])
    assert {row["strategy"] for row in rows} == {"DIRECTIONAL", "MAKER"}
    assert all(row["uncalibrated"] is True for row in rows)
    assert all(row["evidence"]["provider_run_age_known"] is False for row in rows)
    assert all(len(row["id"]) == 64 and row["expires"] <= NOW+20 for row in rows)
    assert public.calls == ["event", "weather", "quote", "weather", "quote"]
    maker = next(row for row in rows if row["strategy"] == "MAKER")
    assert maker["legs"][0]["post_only"] is True
    assert maker["legs"][0]["price"] == public.bid
    assert maker["legs"][0]["fee"] == 0
    assert maker["legs"][0]["neg_risk"] is False
    assert "financial_authority" not in maker


def test_basket_math_is_conditional_and_keeps_each_token_leg():
    service, public = pipeline()
    public.ask, public.bid, public.fee = .2, .1, 0
    rows = candidates(service, public.raw, ["STRUCTURAL"])
    basket = next(row for row in rows if len(row["legs"]) == 3)
    assert basket["theoretical_payout"] == 1
    assert basket["uncalibrated"] is False
    assert "requires all legs filled" in basket["evidence"]["claim"]
    assert len({leg["condition"] for leg in basket["legs"]}) == 3
    assert "weather" not in public.calls


@pytest.mark.parametrize("setting,value,code", [
    ("forecast_age", 121, "FORECAST_SOURCE_STALE"),
    ("stale", 21, "BOOK_PROVIDER_TIMESTAMP_STALE"),
    ("size", .5, "BELOW_MINIMUM_LIQUIDITY"),
])
def test_stale_source_or_quote_or_insufficient_liquidity_skips_with_precise_reason(setting, value, code):
    service, public = pipeline()
    setattr(public, setting, value)
    assert candidates(service, public.raw, ["DIRECTIONAL"]) == []
    assert code in {row["code"] for row in service.last_rejections}


def test_revalidation_reacquires_sources_and_keeps_immutable_thesis_expiry():
    service, public = pipeline()
    original = next(row for row in candidates(service, public.raw, ["DIRECTIONAL"]) if row["legs"][0]["token"] == "t1y")
    public.calls.clear()
    public.ask = .45
    fresh = asyncio.run(service.revalidate(original))
    assert public.calls == ["event", "weather", "quote"]
    assert fresh["key"] == original["key"]
    assert fresh["id"] != original["id"]
    assert fresh["expires"] <= original["expires"]
    assert fresh["legs"][0]["price"] == .45


def test_quote_expiring_while_risk_metadata_is_fetched_cannot_be_delivered():
    service, public = pipeline()
    now = [NOW]
    service.clock = public.clock = service.metadata.clock = lambda: now[0]
    async def delayed_risk(_token):
        now[0] += 21
        return False
    public.neg_risk = delayed_risk
    assert candidates(service, public.raw, ["DIRECTIONAL"]) == []
    assert "QUOTE_EXPIRED" in {row["code"] for row in service.last_rejections}


@pytest.mark.parametrize("change", ["weather", "contract", "price", "tamper", "expiry"])
def test_revalidation_rejects_changed_thesis_prices_semantics_and_local_tampering(change):
    service, public = pipeline()
    row = next(row for row in candidates(service, public.raw, ["DIRECTIONAL"]) if row["legs"][0]["token"] == "t1y")
    if change == "weather":
        public.value = 73
    elif change == "contract":
        public.raw["description"] += " Additional settlement exception."
    elif change == "price":
        public.ask = .99
    elif change == "tamper":
        row["evidence"]["provider_run_age_known"] = True
    else:
        service.clock = lambda: NOW+121
    with pytest.raises(RuntimeError):
        asyncio.run(service.revalidate(row))


def test_outcome_requires_final_status_and_checks_condition_token_side_identity():
    service, public = pipeline()
    row = next(row for row in candidates(service, public.raw, ["DIRECTIONAL"]) if row["legs"][0]["token"] == "t1y")
    market = copy.deepcopy(public.raw["markets"][1])
    market.update(closed=True, outcomePrices=["1", "0"], umaResolutionStatus="proposed")
    public.resolved[market["id"]] = market
    assert asyncio.run(service.outcome(row)) is None
    market["umaResolutionStatus"] = "resolved"
    outcome = asyncio.run(service.outcome(row))
    assert outcome["payout_per_unit"] == 1
    assert outcome["legs"][0]["payout"] == 1
    assert not {"profit", "pnl", "position", "fill"} & set(outcome)
    market["conditionId"] = "changed"
    with pytest.raises(RuntimeError, match="CONDITION_MISMATCH"):
        asyncio.run(service.outcome(row))


def _hourly_event():
    event = _event(target=date(2026, 9, 13), labels=["81°F or lower", "82-90°F", "91°F or higher"])
    event["description"] = (
        "Observation date 13 Sep '26, in whole degrees Fahrenheit. The market resolves using the highest reading "
        'in the "Temp" column across all times on this day. On WRH select hourly data and show hourly data. '
        "If WRH is unavailable, use the Weather Underground Daily Observations table by 11:59 PM ET on the day following "
        "the observation date. If there is no data, the market resolves to the lowest bracket. Revisions are accepted "
        "until the first data point for the following date, whichever comes first, after which any alterations will not be considered."
    )
    return event


def _wrh(*, now=NOW, shock=False):
    start = datetime(2026, 9, 13, tzinfo=ZONE)
    times = [(start+timedelta(hours=hour, minutes=20)).isoformat() for hour in range(16)]
    values = [80.]*12 + ([80, 80, 80, 82] if shock else [90, 89, 88, 87])
    raw = {"UNITS": {"air_temp": "Fahrenheit"}, "SUMMARY": {"RESPONSE_MESSAGE": "OK"}, "STATION": [{
        "STID": "KLGA", "SHORTNAME": "GLOBAL-METAR", "TIMEZONE": str(ZONE),
        "OBSERVATIONS": {"date_time": times, "air_temp_set_1": values,
            "sea_level_pressure_set_1": [1012.]*16, "metar_set_1": ["KLGA SPECI"]*16}}]}
    return parse_synoptic_wrh_hourly_snapshot(raw, station="KLGA", target_date=start.date(),
        query_start_date=start.date(), query_end_date=date(2026, 9, 14), received_at=now)


def _same_day_sources(service, *, shock=False):
    class WRH(Closed):
        def fetch_snapshot(self, **_kwargs):
            return NS(snapshot=_wrh(now=service.clock(), shock=shock))
    class Near(Closed):
        async def fetch_snapshot(self, **_kwargs):
            start = datetime.fromtimestamp(NOW, ZONE).replace(minute=0).isoformat()
            return build_nws_raw_snapshot({"type": "Feature", "properties": {
                "forecastGridData": "https://api.weather.gov/gridpoints/OKX/33,37"}},
                {"type": "Feature", "properties": {"gridId": "OKX", "gridX": 33, "gridY": 37,
                 "updateTime": datetime.fromtimestamp(NOW, ZONE).isoformat(),
                 "temperature": {"uom": "wmoUnit:degC", "values": [{"validTime": start+"/PT2H", "value": 25.}]}}},
                station="KLGA", latitude=LAT, longitude=LON, points_received_at=NOW, grid_received_at=NOW)
    class Hourly(Closed):
        async def target_day(self, **_kwargs):
            start = datetime(2026, 9, 13, tzinfo=ZONE).timestamp()
            keys = ["temperature_2m"]+[f"temperature_2m_member{i:02}" for i in range(1, 31)]
            return parse_open_meteo_gefs_hourly_target_day({"latitude": LAT, "longitude": LON, "timezone": str(ZONE),
                "hourly": {"time": [int(start+h*3600) for h in range(24)], **{key: [80.]*24 for key in keys}},
                "hourly_units": {"time": "unixtime", **{key: "°F" for key in keys}}},
                station="KLGA", target_date=date(2026, 9, 13), unit="F", timezone=str(ZONE),
                requested_latitude=LAT, requested_longitude=LON, received_at=NOW)
    service.wrh, service.near_term, service.hourly = WRH(), Near(), Hourly()


def test_same_day_composes_real_wrh_nws_gefs_parsers_without_calibration_claim():
    service, public = pipeline(_hourly_event())
    public.ask, public.bid = .91, .9
    _same_day_sources(service)
    rows = candidates(service, public.raw, ["SAME_DAY"])
    assert len(rows) == 1, service.last_rejections
    assert rows[0]["legs"][0]["token"] == "t1y"
    assert rows[0]["model_frequency"] == 1
    assert rows[0]["evidence"]["population_alignment_certified"] is False
    assert rows[0]["uncalibrated"] is True


def test_source_shock_uses_actual_parsed_new_extreme_and_rejects_revision():
    service, public = pipeline(_hourly_event())
    _same_day_sources(service, shock=True)
    rows = candidates(service, public.raw, ["SOURCE_SHOCK"])
    assert len(rows) == 1, service.last_rejections
    assert rows[0]["legs"][0]["token"] == "t0n"
    assert rows[0]["revision_sensitive"] is True
    _same_day_sources(service, shock=False)
    with pytest.raises(WeatherUnavailable, match="THESIS_NO_LONGER_SUPPORTED"):
        asyncio.run(service.revalidate(rows[0]))


def test_result_lag_does_not_manufacture_finality_from_repeated_poll():
    service, public = pipeline(_hourly_event())
    _same_day_sources(service)
    assert candidates(service, public.raw, ["RESULT_LAG"]) == []
    assert service.last_rejections[-1]["code"] == "RESULT_LAG_BASELINE_REQUIRED"
    assert candidates(service, public.raw, ["RESULT_LAG"]) == []
    assert service.last_rejections[-1]["code"].startswith("RESULT_LAG_FINALITY_INVALID:")
    assert "quote" not in public.calls


@pytest.mark.parametrize("family,variable", [("high", "temperature_2m_max"), ("low", "temperature_2m_min")])
def test_public_daily_request_binds_extreme_unit_date_and_31_members(family, variable):
    async def run():
        public = PublicWeatherSources()
        compiled = compile_strict_temperature_event(_event(family=family))
        metadata = await Metadata(lambda: NOW).station("KLGA")
        requested = []
        async def handler(request):
            requested.append(dict(request.url.params))
            keys = [variable]+[f"{variable}_member{i:02}" for i in range(1, 31)]
            return httpx.Response(200, json={"latitude": LAT, "longitude": LON, "timezone": str(ZONE),
                "daily": {"time": ["2026-09-14"], **{key: [70.] for key in keys}},
                "daily_units": {"time": "iso8601", **{key: "°F" for key in keys}}})
        await public.http.aclose()
        public.http = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
        try:
            value = await public.daily_extreme(compiled, metadata)
            assert requested[0]["daily"] == variable
            assert requested[0]["timezone"] == str(ZONE)
            assert requested[0]["temperature_unit"] == "fahrenheit"
            assert requested[0]["start_date"] == requested[0]["end_date"] == "2026-09-14"
            assert len(value.member_values) == 31
        finally:
            await public.close()
    asyncio.run(run())
