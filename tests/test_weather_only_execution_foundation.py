import asyncio

import httpx
import pytest

from polymarket_scanner.models import Book
from polymarket_scanner.weather_only_clob import (
    WeatherCLOBClient,
    WeatherCLOBError,
    conservative_taker_fee_per_share,
    parse_book,
    parse_market_info,
)
from polymarket_scanner.weather_only_contracts import DAILY_HIGH, DAILY_LOW, compile_weather_event
from polymarket_scanner.weather_only_maker import (
    ConfirmedFill,
    FairValueBand,
    propose_maker_bid,
    summarize_complete_set_inventory,
)
from polymarket_scanner.weather_only_sources import (
    HKOClimateArchiveClient,
    WeatherSourceError,
    parse_hko_climate_json,
)


def _market(mid, question):
    return {
        "id": mid,
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "question": question,
        "slug": f"market-{mid}",
        "conditionId": f"condition-{mid}",
        "outcomes": '["Yes","No"]',
        "clobTokenIds": f'["{mid}-yes","{mid}-no"]',
    }


def _event():
    return {
        "id": "event-1",
        "slug": "nyc-high",
        "title": "Highest temperature in NYC on September 11?",
        "description": "Observation date 11 Sep '26, in degrees Fahrenheit.",
        "resolutionSource": "https://www.weather.gov/wrh/timeseries?site=KLGA",
        "markets": [
            _market("a", "Will the highest temperature be 69°F or lower?"),
            _market("b", "Will the highest temperature be 70-71°F?"),
            _market("c", "Will the highest temperature be 72°F or higher?"),
        ],
    }


def _market_info(bucket, *, fee=0.05, exponent=2, tick=0.01, minimum=5.0):
    return {
        "t": [
            {"t": bucket.yes_token, "o": "Yes"},
            {"t": bucket.no_token, "o": "No"},
        ],
        "mos": minimum,
        "mts": tick,
        "mbf": 0,
        "tbf": 0,
        "rfqe": False,
        "itode": False,
        "fd": {"r": fee, "e": exponent, "to": True},
    }


def _book_json(token, bid=0.20, ask=0.40, size=20.0):
    return {
        "asset_id": token,
        "bids": [{"price": str(bid), "size": str(size)}],
        "asks": [{"price": str(ask), "size": str(size)}],
        "last_trade_price": "0.30",
        "timestamp": "1234567890",
        "hash": "hash",
    }


def test_market_info_parser_uses_v2_fee_rate_and_exponent_and_binary_identity():
    bucket = compile_weather_event(_event()).buckets[0]
    info = parse_market_info(bucket.condition_id, _market_info(bucket), received_at=10.0)
    assert info.condition_id == bucket.condition_id
    assert info.fee_rate == 0.05
    assert info.fee_exponent == 2
    assert info.taker_only is True
    assert info.minimum_tick_size == 0.01
    assert {token for token, _ in info.token_outcomes} == {bucket.yes_token, bucket.no_token}


def test_market_info_parser_matches_official_v2_zero_fee_default_when_fd_missing():
    bucket = compile_weather_event(_event()).buckets[0]
    payload = _market_info(bucket)
    payload.pop("fd")
    info = parse_market_info(bucket.condition_id, payload, received_at=10.0)
    assert info.fee_rate == 0.0
    assert info.fee_exponent == 0
    assert info.taker_only is None


def test_positive_fee_requires_integral_explicit_exponent():
    bucket = compile_weather_event(_event()).buckets[0]
    missing = _market_info(bucket)
    missing["fd"] = {"r": 0.05, "to": True}
    with pytest.raises(WeatherCLOBError) as raised:
        parse_market_info(bucket.condition_id, missing, received_at=10.0)
    assert raised.value.code == "MARKET_INFO_FEE_EXPONENT_MISSING"

    fractional = _market_info(bucket, exponent=1.5)
    with pytest.raises(WeatherCLOBError) as raised:
        parse_market_info(bucket.condition_id, fractional, received_at=10.0)
    assert raised.value.code == "MARKET_INFO_INTEGER_INVALID"


def test_book_parser_rejects_crossed_and_wrong_token_books():
    bucket = compile_weather_event(_event()).buckets[0]
    book = parse_book(bucket.yes_token, _book_json(bucket.yes_token), received_at=10.0)
    assert book.best_bid == 0.20
    assert book.best_ask == 0.40
    assert book.received_at == 10.0
    assert book.source == "clob_exact_rest_v2"

    with pytest.raises(WeatherCLOBError) as raised:
        parse_book(bucket.yes_token, _book_json("wrong"), received_at=10.0)
    assert raised.value.code == "BOOK_TOKEN_IDENTITY_MISMATCH"

    crossed = _book_json(bucket.yes_token, bid=0.50, ask=0.40)
    with pytest.raises(WeatherCLOBError) as raised:
        parse_book(bucket.yes_token, crossed, received_at=10.0)
    assert raised.value.code == "BOOK_CROSSED"


def test_conservative_taker_fee_matches_v2_exponent_and_rounds_up_to_five_decimals():
    raw = 0.05 * (0.33 * 0.67) ** 2
    fee = conservative_taker_fee_per_share(0.33, 0.05, 2)
    assert fee >= raw
    assert fee == 0.00245

    # Exponent=1 reproduces the documented weather fee curve.
    raw_e1 = 0.05 * 0.33 * 0.67
    fee_e1 = conservative_taker_fee_per_share(0.33, 0.05, 1)
    assert fee_e1 >= raw_e1
    assert fee_e1 == 0.01106


def test_exact_event_snapshot_reads_only_clob_and_cross_checks_gamma_tokens():
    compiled = compile_weather_event(_event())
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path.startswith("/clob-markets/"):
            condition = request.url.path.rsplit("/", 1)[-1]
            bucket = next(row for row in compiled.buckets if row.condition_id == condition)
            return httpx.Response(200, json=_market_info(bucket))
        if request.method == "POST" and request.url.path == "/books":
            import json
            wanted = [row["token_id"] for row in json.loads(request.content)]
            return httpx.Response(200, json=[_book_json(token) for token in wanted])
        return httpx.Response(404)

    async def run():
        client = WeatherCLOBClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await client.exact_event_snapshot(compiled)
        finally:
            await client.close()

    snapshot = asyncio.run(run())
    assert snapshot.exact_clob is True
    assert snapshot.financial_authority is False
    assert len(snapshot.parameters) == 3
    assert len(snapshot.books) == 6
    assert all(path.startswith("/clob-markets/") or path == "/books" for _, path in calls)


def test_exact_event_snapshot_rejects_clob_token_mismatch():
    compiled = compile_weather_event(_event())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            condition = request.url.path.rsplit("/", 1)[-1]
            bucket = next(row for row in compiled.buckets if row.condition_id == condition)
            payload = _market_info(bucket)
            if condition == compiled.buckets[0].condition_id:
                payload["t"][0]["t"] = "different-token"
            return httpx.Response(200, json=payload)
        import json
        wanted = [row["token_id"] for row in json.loads(request.content)]
        return httpx.Response(200, json=[_book_json(token) for token in wanted])

    async def run():
        client = WeatherCLOBClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await client.exact_event_snapshot(compiled)
        finally:
            await client.close()

    with pytest.raises(WeatherCLOBError) as raised:
        asyncio.run(run())
    assert raised.value.code == "GAMMA_CLOB_TOKEN_MISMATCH"


def test_maker_bid_uses_conservative_fair_lower_bound_and_never_grants_authority():
    compiled = compile_weather_event(_event())
    bucket = compiled.buckets[0]
    info = parse_market_info(bucket.condition_id, _market_info(bucket), received_at=10.0)
    book = Book(
        token_id=bucket.yes_token,
        bids=[(0.20, 10.0)],
        asks=[(0.45, 10.0)],
        received_at=10.0,
        source="clob_exact_rest_v2",
    )
    fair = FairValueBand(
        token_id=bucket.yes_token,
        lower=0.38,
        point=0.43,
        upper=0.48,
        model_version="calibration-test",
        as_of=10.0,
        calibrated=True,
    )
    proposal = propose_maker_bid(
        event_id=compiled.event_id,
        bucket=bucket,
        outcome="Yes",
        book=book,
        parameters=info,
        fair=fair,
        max_notional=10.0,
        minimum_conditional_edge=0.05,
    )
    assert proposal is not None
    assert proposal.bid_price == 0.33
    assert proposal.conditional_edge_per_share >= 0.05 - 1e-9
    assert proposal.calibrated is True
    assert proposal.financial_authority is False


def test_maker_bid_refuses_to_cross_ask_or_overpay_fair_lower_bound():
    compiled = compile_weather_event(_event())
    bucket = compiled.buckets[0]
    info = parse_market_info(bucket.condition_id, _market_info(bucket), received_at=10.0)
    book = Book(token_id=bucket.yes_token, bids=[(0.34, 1)], asks=[(0.35, 1)])
    fair = FairValueBand(bucket.yes_token, 0.38, 0.40, 0.42, "test", 10.0, True)
    assert propose_maker_bid(
        event_id=compiled.event_id,
        bucket=bucket,
        outcome="yes",
        book=book,
        parameters=info,
        fair=fair,
        max_notional=10,
        minimum_conditional_edge=0.05,
    ) is None


def test_complete_set_inventory_counts_only_confirmed_balanced_shares_as_locked():
    tokens = ("a-yes", "b-yes", "c-yes")
    fills = [
        ConfirmedFill("e", "a", tokens[0], "Yes", 10, 0.20),
        ConfirmedFill("e", "b", tokens[1], "Yes", 8, 0.25),
        ConfirmedFill("e", "c", tokens[2], "Yes", 12, 0.30),
    ]
    summary = summarize_complete_set_inventory(
        event_id="e",
        required_yes_tokens=tokens,
        fills=fills,
        exactly_one_outcome_proven=True,
    )
    assert summary.complete_sets == 8
    assert summary.complete_set_cost_basis == pytest.approx(0.75)
    assert summary.locked_redemption_value == 8
    assert summary.locked_pnl == pytest.approx(2.0)
    assert dict(summary.residual_shares) == {"a-yes": 2, "b-yes": 0, "c-yes": 4}
    assert summary.financial_authority is False

    unproven = summarize_complete_set_inventory(
        event_id="e",
        required_yes_tokens=tokens,
        fills=fills,
        exactly_one_outcome_proven=False,
    )
    assert unproven.complete_sets == 0
    assert unproven.locked_pnl is None


def test_hko_archive_parser_is_calibration_only_and_never_initial_publication_authority():
    payload = {
        "type": ["Max Temperature"],
        "fields": ["Year", "Month", "Day", "Temperature(C)"],
        "data": [[2026, 9, 10, 31.1], [2026, 9, 11, 30.7], [2026, 9, 12, "***"]],
        "legend": ["HKO"],
    }
    rows = parse_hko_climate_json(payload, station="HKO", family=DAILY_HIGH)
    assert [row.temperature_c for row in rows] == [31.1, 30.7]
    assert all(row.settlement_authority is False for row in rows)
    assert all(row.initial_publication_state_reconstructable is False for row in rows)
    assert all(row.source_role == "HISTORICAL_MODEL_CALIBRATION_ONLY" for row in rows)


def test_hko_archive_client_uses_documented_dataset_and_month_filter():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={
            "fields": ["Year", "Month", "Day", "Temperature(C)"],
            "data": [[2026, 9, 11, 25.2]],
        })

    async def run():
        client = HKOClimateArchiveClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await client.daily_extremes(station="HKO", year=2026, month=9, family=DAILY_LOW)
        finally:
            await client.close()

    rows = asyncio.run(run())
    assert len(rows) == 1
    request = seen[0]
    assert request.url.path.endswith("/opendata/opendata.php")
    assert request.url.params.get("dataType") == "CLMMINT"
    assert request.url.params.get("station") == "HKO"
    assert request.url.params.get("year") == "2026"
    assert request.url.params.get("month") == "9"
    assert request.url.params.get("rformat") == "json"


def test_hko_archive_rejects_duplicate_day():
    payload = {
        "fields": ["Year", "Month", "Day", "Temperature(C)"],
        "data": [[2026, 9, 11, 30.0], [2026, 9, 11, 30.1]],
    }
    with pytest.raises(WeatherSourceError) as raised:
        parse_hko_climate_json(payload, station="HKO", family=DAILY_HIGH)
    assert raised.value.code == "HKO_ARCHIVE_DUPLICATE_DAY"
