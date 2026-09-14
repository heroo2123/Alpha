from __future__ import annotations

from pathlib import Path


def rep(path: str, old: str, new: str, *, expected: int = 1) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    found = text.count(old)
    if found != expected:
        raise SystemExit(f"{path}: expected {expected}, found {found}: {old[:180]!r}")
    p.write_text(text.replace(old, new, expected), encoding="utf-8")


# Deployment preflight must exercise the exact guarded station-metadata transport that
# the candidate now uses before the service is installed or started.
rep(
    "polymarket_scanner/weather_only_network_preflight.py",
    "from .weather_only_three_layer_guarded import (\n"
    "    GuardedNWSNearTermGridClient,\n",
    "from .weather_only_three_layer_guarded import (\n"
    "    GuardedNWSNearTermGridClient,\n"
    "    GuardedSameDayStationMetadataClient,\n",
)
rep(
    "polymarket_scanner/weather_only_network_preflight.py",
    'NETWORK_PREFLIGHT_VERSION = "weather_paper_network_preflight_v3_guarded_three_layer_transports"',
    'NETWORK_PREFLIGHT_VERSION = "weather_paper_network_preflight_v4_guarded_three_layer_station_metadata"',
)
rep(
    "polymarket_scanner/weather_only_network_preflight.py",
    "        wrh = GuardedNWSWRHLiveClient()\n"
    "        near = GuardedNWSNearTermGridClient()\n"
    "        gefs = GuardedOpenMeteoGEFSHourlyClient()\n",
    "        wrh = GuardedNWSWRHLiveClient()\n"
    "        near = GuardedNWSNearTermGridClient()\n"
    "        gefs = GuardedOpenMeteoGEFSHourlyClient()\n"
    "        station_meta = GuardedSameDayStationMetadataClient()\n",
)
rep(
    "polymarket_scanner/weather_only_network_preflight.py",
    "        async def wrh_action() -> str:\n",
    "        async def station_metadata_action() -> str:\n"
    "            result = await station_meta.station(REFERENCE_STATION)\n"
    "            if str(result.station).upper() != REFERENCE_STATION:\n"
    "                raise WeatherNetworkPreflightError(\"NETWORK_STATION_METADATA_IDENTITY_MISMATCH\")\n"
    "            if not math.isfinite(float(result.latitude)) or not math.isfinite(float(result.longitude)):\n"
    "                raise WeatherNetworkPreflightError(\"NETWORK_STATION_METADATA_COORDINATE_INVALID\")\n"
    "            if not str(result.timezone or \"\").strip():\n"
    "                raise WeatherNetworkPreflightError(\"NETWORK_STATION_METADATA_TIMEZONE_INVALID\")\n"
    "            return (\n"
    "                f\"guarded station metadata ok; station={result.station}; \"\n"
    "                f\"timezone={result.timezone}\"\n"
    "            )\n\n"
    "        async def wrh_action() -> str:\n",
)
rep(
    "polymarket_scanner/weather_only_network_preflight.py",
    "                _probe(\n"
    "                    name=\"nws_wrh_synoptic\",\n",
    "                _probe(\n"
    "                    name=\"nws_station_metadata\",\n"
    "                    url=NWS_API_ORIGIN,\n"
    "                    required=True,\n"
    "                    action=station_metadata_action,\n"
    "                ),\n"
    "                _probe(\n"
    "                    name=\"nws_wrh_synoptic\",\n",
)
rep(
    "polymarket_scanner/weather_only_network_preflight.py",
    "        finally:\n"
    "            wrh.close()\n"
    "            await asyncio.gather(near.close(), gefs.close(), return_exceptions=True)\n",
    "        finally:\n"
    "            wrh.close()\n"
    "            await asyncio.gather(\n"
    "                station_meta.close(),\n"
    "                near.close(),\n"
    "                gefs.close(),\n"
    "                return_exceptions=True,\n"
    "            )\n",
)

# Static deployment regression: preflight must not drift back to probing only the three
# primary data sources while silently skipping the station-identity transport.
rep(
    "tests/test_weather_only_network_preflight.py",
    "    assert \"GuardedNWSNearTermGridClient\" in source\n"
    "    assert \"GuardedNWSWRHLiveClient\" in source\n",
    "    assert \"GuardedNWSNearTermGridClient\" in source\n"
    "    assert \"GuardedSameDayStationMetadataClient\" in source\n"
    "    assert \"GuardedNWSWRHLiveClient\" in source\n",
)
rep(
    "tests/test_weather_only_network_preflight.py",
    "    assert \"wrh.close()\" in source\n"
    "    assert \"polymarket_gamma\" in source\n",
    "    assert \"wrh.close()\" in source\n"
    "    assert \"station_meta.close()\" in source\n"
    "    assert \"nws_station_metadata\" in source\n"
    "    assert \"station_metadata_action\" in source\n"
    "    assert \"polymarket_gamma\" in source\n",
)
