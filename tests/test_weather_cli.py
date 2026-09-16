from __future__ import annotations

from datetime import date, datetime, timezone

from polymarket_scanner.weather_cli import parse_cli_daily_report, resolve_daily_rain_after_cutoff
from polymarket_scanner.weather_rule_tree import DailyRainContract, RuleSource


def contract() -> DailyRainContract:
    return DailyRainContract(
        adapter_version="DAILY_RAIN_NWS_CLI_V1",
        target_date=date(2026, 9, 11),
        city_label="New York, NY",
        station="KNYC",
        primary=RuleSource(
            "NWS_CLI",
            "https://forecast.weather.gov/product.php?site=NWS&product=CLI&issuedby=NYC",
            "forecast.weather.gov",
            "KNYC",
            issuer="NYC",
        ),
        threshold_inches=0.01,
        trace_counts=False,
        observation_interval="MIDNIGHT_TO_MIDNIGHT",
        observation_time_basis="LOCAL_STANDARD_TIME",
        report_cutoff_local=datetime(2026, 9, 12, 14, 0),
        report_cutoff_timezone="America/New_York",
        multiple_version_rule="LAST_VERSION_PUBLISHED_BEFORE_CUTOFF_GOVERNS",
        no_figure_resolution="NO",
    )


def cli_payload(*, value: str, issued: str = "730 AM EDT SAT SEP 12 2026", issuer: str = "NYC") -> str:
    return f"""
    101
    CDUS41 KOKX 121130
    CLI{issuer}

    CLIMATE REPORT
    NATIONAL WEATHER SERVICE NEW YORK, NY
    {issued}

    ...THE CENTRAL PARK NY CLIMATE SUMMARY FOR SEPTEMBER 11 2026...

    WEATHER ITEM   OBSERVED TIME   RECORD YEAR NORMAL DEPARTURE LAST
                    VALUE   (LST)  VALUE       VALUE  FROM      YEAR
    ...................................................................
    TEMPERATURE (F)
     YESTERDAY
      MAXIMUM         80    126 PM
      MINIMUM         60    614 AM

    PRECIPITATION (IN)
      YESTERDAY        {value}          2.90 2018   0.14  -0.14     0.00
      MONTH TO DATE    1.24                      1.71  -0.47     0.06
      SINCE JAN 1     20.95                     30.02  -9.07    25.11

    SNOWFALL (IN)
      YESTERDAY        0.0
    """


def test_parse_cli_daily_report_extracts_target_issue_time_and_precipitation():
    report = parse_cli_daily_report(cli_payload(value="0.37"), expected_issuer="NYC")
    assert report is not None
    assert report.issuer == "NYC"
    assert report.climate_date == date(2026, 9, 11)
    assert report.issued_at.isoformat() == "2026-09-12T07:30:00-04:00"
    assert report.issued_timezone_abbrev == "EDT"
    assert report.precipitation_inches == 0.37
    assert report.trace is False
    assert report.missing is False
    assert len(report.content_sha256) == 64


def test_parse_cli_accepts_html_wrapped_product_text():
    wrapped = f"<html><body><pre>{cli_payload(value='0.02')}</pre></body></html>"
    report = parse_cli_daily_report(wrapped, expected_issuer="NYC")
    assert report is not None
    assert report.precipitation_inches == 0.02


def test_cli_trace_and_missing_are_distinct():
    trace = parse_cli_daily_report(cli_payload(value="T"), expected_issuer="NYC")
    missing = parse_cli_daily_report(cli_payload(value="MM"), expected_issuer="NYC")
    assert trace is not None and trace.trace is True and trace.missing is False
    assert trace.precipitation_inches is None
    assert missing is not None and missing.missing is True and missing.trace is False
    assert missing.precipitation_inches is None


def test_cli_parser_rejects_wrong_issuer_and_impossible_timezone_label():
    assert parse_cli_daily_report(cli_payload(value="0.10"), expected_issuer="LAX") is None
    # September in New York is EDT, so an EST publication label is treated as ambiguous.
    assert parse_cli_daily_report(
        cli_payload(value="0.10", issued="730 AM EST SAT SEP 12 2026"),
        expected_issuer="NYC",
    ) is None


def test_result_lag_refuses_deterministic_outcome_before_contract_cutoff():
    c = contract()
    report = parse_cli_daily_report(cli_payload(value="0.37"), expected_issuer="NYC")
    assert report is not None
    result = resolve_daily_rain_after_cutoff(
        c,
        [report],
        now=datetime(2026, 9, 12, 17, 0, tzinfo=timezone.utc),  # 1 PM EDT
    )
    assert result is None


def test_result_lag_uses_last_matching_version_before_two_pm_cutoff():
    c = contract()
    early = parse_cli_daily_report(
        cli_payload(value="0.00", issued="730 AM EDT SAT SEP 12 2026"), expected_issuer="NYC"
    )
    revised = parse_cli_daily_report(
        cli_payload(value="0.04", issued="125 PM EDT SAT SEP 12 2026"), expected_issuer="NYC"
    )
    after_cutoff = parse_cli_daily_report(
        cli_payload(value="0.00", issued="230 PM EDT SAT SEP 12 2026"), expected_issuer="NYC"
    )
    assert early and revised and after_cutoff

    result = resolve_daily_rain_after_cutoff(
        c,
        [after_cutoff, early, revised],
        now=datetime(2026, 9, 12, 18, 5, tzinfo=timezone.utc),
    )
    assert result is not None
    assert result.outcome_yes is True
    assert result.precipitation_inches == 0.04
    assert result.controlling_report_issued_at == revised.issued_at
    assert result.no_figure_by_cutoff is False
    assert result.deterministic_after_cutoff is True


def test_trace_resolves_no_after_cutoff():
    c = contract()
    report = parse_cli_daily_report(cli_payload(value="T"), expected_issuer="NYC")
    assert report
    result = resolve_daily_rain_after_cutoff(
        c,
        [report],
        now=datetime(2026, 9, 12, 19, 0, tzinfo=timezone.utc),
    )
    assert result is not None
    assert result.outcome_yes is False
    assert result.trace is True
    assert result.no_figure_by_cutoff is False


def test_no_matching_figure_by_cutoff_resolves_no_per_contract():
    c = contract()
    result = resolve_daily_rain_after_cutoff(
        c,
        [],
        now=datetime(2026, 9, 12, 19, 0, tzinfo=timezone.utc),
    )
    assert result is not None
    assert result.outcome_yes is False
    assert result.no_figure_by_cutoff is True
    assert result.controlling_report_issued_at is None
