from datetime import datetime, timedelta, timezone

from polymarket_scanner.weather import (
    AWC_OBSERVATION_ADAPTER,
    Observation,
    ObservationBatch,
    parse_awc_metar_observation,
)
from polymarket_scanner.weather_contracts import settlement_safe_weather_cache


def _now() -> datetime:
    return datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc)


def _row(**overrides):
    row = {
        "icaoId": "KORD",
        "obsTime": 1788793200,
        "receiptTime": 1788793260,
        "reportTime": "2026-09-07T15:00:30Z",
        "temp": 20.0,
        "rawOb": "KORD TEST",
    }
    row.update(overrides)
    return row


def _obs(when: datetime, temp: float, raw: str, *, station: str = "KORD", adapter: str = AWC_OBSERVATION_ADAPTER, receipt: datetime | None = None):
    return Observation(
        when=when,
        temp_c=temp,
        raw=raw,
        station_id=station,
        source_adapter=adapter,
        receipt_time=receipt,
    )


def test_awc_parser_requires_exact_requested_station_identity():
    assert parse_awc_metar_observation(_row(icaoId="KMDW"), "KORD") is None
    parsed = parse_awc_metar_observation(_row(), "kord")
    assert parsed is not None
    assert parsed.station_id == "KORD"
    assert parsed.source_adapter == AWC_OBSERVATION_ADAPTER


def test_awc_parser_never_substitutes_receipt_or_report_time_for_missing_obstime():
    assert parse_awc_metar_observation(_row(obsTime=None), "KORD") is None
    assert parse_awc_metar_observation(_row(obsTime=""), "KORD") is None


def test_awc_parser_rejects_naive_or_nonfinite_event_time_and_temperature():
    assert parse_awc_metar_observation(_row(obsTime="2026-09-07T15:00:00"), "KORD") is None
    assert parse_awc_metar_observation(_row(obsTime=float("nan")), "KORD") is None
    assert parse_awc_metar_observation(_row(temp=float("nan")), "KORD") is None
    assert parse_awc_metar_observation(_row(temp=float("inf")), "KORD") is None


def test_awc_parser_preserves_receipt_and_report_lineage_without_using_them_as_event_time():
    parsed = parse_awc_metar_observation(_row(), "KORD")
    assert parsed is not None
    assert parsed.when == datetime.fromtimestamp(1788793200, timezone.utc)
    assert parsed.receipt_time == datetime.fromtimestamp(1788793260, timezone.utc)
    assert parsed.report_time == datetime(2026, 9, 7, 15, 0, 30, tzinfo=timezone.utc)


def test_weather_cache_rejects_anonymous_wrong_station_and_wrong_adapter_rows():
    now = _now()
    rows = ObservationBatch([
        Observation(now - timedelta(minutes=30), 20.0, "anonymous"),
        _obs(now - timedelta(minutes=25), 20.0, "wrong-station", station="KMDW"),
        _obs(now - timedelta(minutes=20), 20.0, "wrong-adapter", adapter="LEGACY_PROXY"),
        _obs(now - timedelta(minutes=15), 20.0, "good"),
    ])
    clean = settlement_safe_weather_cache({"KORD": rows}, now=now)["KORD"]
    assert [row.raw for row in clean] == ["good"]


def test_identical_same_time_duplicates_collapse_to_latest_receipt_lineage():
    now = _now()
    when = now - timedelta(minutes=20)
    early = _obs(when, 20.0, "early", receipt=now - timedelta(minutes=19))
    late = _obs(when, 20.0, "late", receipt=now - timedelta(minutes=18))
    clean = settlement_safe_weather_cache({"KORD": ObservationBatch([early, late])}, now=now)["KORD"]
    assert len(clean) == 1
    assert clean[0].raw == "late"


def test_conflicting_same_time_temperature_rows_are_discarded_not_guessed():
    now = _now()
    when = now - timedelta(minutes=20)
    rows = ObservationBatch([
        _obs(when, 20.0, "v1"),
        _obs(when, 21.0, "v2"),
        _obs(now - timedelta(minutes=10), 19.0, "independent"),
    ])
    clean = settlement_safe_weather_cache({"KORD": rows}, now=now)["KORD"]
    assert [row.raw for row in clean] == ["independent"]


def test_weather_cache_rejects_nonfinite_proxy_temperature():
    now = _now()
    rows = ObservationBatch([
        _obs(now - timedelta(minutes=10), float("nan"), "nan"),
        _obs(now - timedelta(minutes=5), float("inf"), "inf"),
    ])
    clean = settlement_safe_weather_cache({"KORD": rows}, now=now)["KORD"]
    assert list(clean) == []
