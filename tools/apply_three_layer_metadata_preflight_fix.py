from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing patch anchor in {path}: {old[:100]!r}")
    if text.count(old) != 1:
        raise SystemExit(f"non-unique patch anchor in {path}: {text.count(old)}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


PREFLIGHT = "polymarket_scanner/weather_only_network_preflight.py"
TESTS = "tests/test_weather_only_network_preflight.py"

replace_once(
    PREFLIGHT,
    '''REFERENCE_TIMEZONE = "America/New_York"\n''',
    '''REFERENCE_TIMEZONE = "America/New_York"\nFALLBACK_REFERENCE_STATION = "EDDM"\nFALLBACK_REFERENCE_TIMEZONE = "Europe/Berlin"\n''',
)

replace_once(
    PREFLIGHT,
    '''        async def wrh_action() -> str:\n''',
    '''        async def station_metadata_fallback_action() -> str:\n            # EDDM is deliberately outside the NWS station catalog and therefore\n            # exercises the guarded WRH/Synoptic station-metadata fallback that\n            # non-US weather contracts depend on.  Deployment must prove this path\n            # before the service is installed, not discover a break only at runtime.\n            primary = await station_meta._nws_once(FALLBACK_REFERENCE_STATION)\n            if primary is not None:\n                raise WeatherNetworkPreflightError(\n                    "NETWORK_STATION_METADATA_FALLBACK_PRIMARY_UNEXPECTED"\n                )\n            result = await station_meta._wrh_station(FALLBACK_REFERENCE_STATION)\n            if str(result.station).upper() != FALLBACK_REFERENCE_STATION:\n                raise WeatherNetworkPreflightError(\n                    "NETWORK_STATION_METADATA_FALLBACK_IDENTITY_MISMATCH"\n                )\n            if str(result.timezone or "").strip() != FALLBACK_REFERENCE_TIMEZONE:\n                raise WeatherNetworkPreflightError(\n                    "NETWORK_STATION_METADATA_FALLBACK_TIMEZONE_MISMATCH"\n                )\n            if not math.isfinite(float(result.latitude)) or not math.isfinite(float(result.longitude)):\n                raise WeatherNetworkPreflightError(\n                    "NETWORK_STATION_METADATA_FALLBACK_COORDINATE_INVALID"\n                )\n            return (\n                f"guarded fallback station metadata ok; station={result.station}; "\n                f"timezone={result.timezone}"\n            )\n\n        async def wrh_action() -> str:\n''',
)

replace_once(
    PREFLIGHT,
    '''                _probe(\n                    name="nws_wrh_synoptic",\n                    url=WRH_TIMESERIES_PAGE,\n                    required=True,\n                    action=wrh_action,\n                ),\n''',
    '''                _probe(\n                    name="wrh_station_metadata_fallback",\n                    url=WRH_TIMESERIES_PAGE,\n                    required=True,\n                    action=station_metadata_fallback_action,\n                ),\n                _probe(\n                    name="nws_wrh_synoptic",\n                    url=WRH_TIMESERIES_PAGE,\n                    required=True,\n                    action=wrh_action,\n                ),\n''',
)

replace_once(
    TESTS,
    '''    assert "station_metadata_action" in source\n''',
    '''    assert "station_metadata_action" in source\n    assert "station_metadata_fallback_action" in source\n    assert "wrh_station_metadata_fallback" in source\n    assert 'FALLBACK_REFERENCE_STATION = "EDDM"' in source\n    assert 'FALLBACK_REFERENCE_TIMEZONE = "Europe/Berlin"' in source\n    assert "station_meta._nws_once(FALLBACK_REFERENCE_STATION)" in source\n    assert "station_meta._wrh_station(FALLBACK_REFERENCE_STATION)" in source\n''',
)

with Path(TESTS).open("a", encoding="utf-8") as f:
    f.write('''\n\ndef test_required_station_metadata_fallback_failure_blocks_deployment_gate():\n    report = evaluate_network_probes(\n        (\n            _probe("nws_station_metadata", ok=True),\n            _probe("wrh_station_metadata_fallback", ok=False),\n            _probe("nws_wrh_synoptic", ok=True),\n        ),\n        checked_at=100.0,\n    )\n    assert report.required_passed is False\n''')
