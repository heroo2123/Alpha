from __future__ import annotations

"""Read-only network preflight for the weather PAPER runtime.

The production VM may be IPv6-only. A source can therefore be healthy on a dual-stack
machine yet unreachable from the actual host. This preflight exercises the same public
providers used by the paper bot before systemd is installed or started. It sends no
Telegram message, uses no wallet credentials, places no order, and writes nothing.
"""

import asyncio
import json
import math
import socket
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

from .config import settings
from .weather_only_clob import CLOB
from .weather_only_discovery import GAMMA
from .weather_only_forecast import OPEN_METEO_ENSEMBLE
from .weather_only_gefs_hourly import OpenMeteoGEFSHourlyClient
from .weather_only_nws_near_term import NWS_API_ORIGIN, NWSNearTermGridClient
from .weather_only_wrh_client import NWSWRHLiveClient, WRH_TIMESERIES_PAGE

NETWORK_PREFLIGHT_VERSION = "weather_paper_network_preflight_v2_stable_wrh_reference_day"
TELEGRAM_ORIGIN = "https://api.telegram.org"
REFERENCE_STATION = "KLGA"
REFERENCE_LATITUDE = 40.7769
REFERENCE_LONGITUDE = -73.8740
REFERENCE_TIMEZONE = "America/New_York"


class WeatherNetworkPreflightError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class NetworkProbe:
    name: str
    host: str
    required: bool
    ok: bool
    detail: str
    ipv4_dns: bool
    ipv6_dns: bool
    elapsed_seconds: float

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WeatherPaperNetworkReport:
    version: str
    checked_at: float
    probes: tuple[NetworkProbe, ...]
    required_passed: bool
    financial_authority: bool = False
    automatic_order_placement: bool = False
    telegram_message_sent: bool = False

    def as_dict(self) -> dict:
        value = asdict(self)
        value["probes"] = [probe.as_dict() for probe in self.probes]
        return value


def _host(url: str) -> str:
    host = str(urlparse(url).hostname or "").strip().lower()
    if not host:
        raise WeatherNetworkPreflightError("NETWORK_PREFLIGHT_HOST_INVALID")
    return host


async def _dns_families(host: str) -> tuple[bool, bool]:
    try:
        rows = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return False, False
    families = {row[0] for row in rows}
    return socket.AF_INET in families, socket.AF_INET6 in families


def evaluate_network_probes(
    probes: tuple[NetworkProbe, ...], *, checked_at: float | None = None
) -> WeatherPaperNetworkReport:
    if not probes:
        raise WeatherNetworkPreflightError("NETWORK_PREFLIGHT_EMPTY")
    names = [probe.name for probe in probes]
    if len(names) != len(set(names)):
        raise WeatherNetworkPreflightError("NETWORK_PREFLIGHT_DUPLICATE_PROBE")
    required_passed = all(probe.ok for probe in probes if probe.required)
    instant = time.time() if checked_at is None else float(checked_at)
    if not math.isfinite(instant) or instant < 0.0:
        raise WeatherNetworkPreflightError("NETWORK_PREFLIGHT_TIME_INVALID")
    return WeatherPaperNetworkReport(
        version=NETWORK_PREFLIGHT_VERSION,
        checked_at=instant,
        probes=probes,
        required_passed=required_passed,
    )


async def _probe(*, name: str, url: str, required: bool, action) -> NetworkProbe:
    host = _host(url)
    ipv4, ipv6 = await _dns_families(host)
    started = time.monotonic()
    try:
        detail = await action()
        ok = True
    except Exception as exc:
        ok = False
        detail = str(getattr(exc, "code", type(exc).__name__))
    return NetworkProbe(
        name=name,
        host=host,
        required=required,
        ok=ok,
        detail=str(detail)[:240],
        ipv4_dns=ipv4,
        ipv6_dns=ipv6,
        elapsed_seconds=max(0.0, time.monotonic() - started),
    )


async def check_weather_paper_network() -> WeatherPaperNetworkReport:
    """Exercise every provider required by the deployed weather PAPER bot."""
    timeout = httpx.Timeout(min(20.0, float(settings.request_timeout)))
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "polymarket-weather-paper-network-preflight/1.0"},
    ) as http:
        wrh = NWSWRHLiveClient(timeout_seconds=min(20.0, float(settings.request_timeout)))
        near = NWSNearTermGridClient()
        gefs = OpenMeteoGEFSHourlyClient()
        today = datetime.now(ZoneInfo(REFERENCE_TIMEZONE)).date()
        wrh_target = today - timedelta(days=1)

        async def gamma_action() -> str:
            response = await http.get(
                f"{GAMMA}/events", params={"limit": 1, "active": "true", "closed": "false"}
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise WeatherNetworkPreflightError("NETWORK_GAMMA_SCHEMA_INVALID")
            return f"HTTP {response.status_code}; events={len(payload)}"

        async def clob_action() -> str:
            response = await http.get(f"{CLOB}/time")
            response.raise_for_status()
            text = response.text.strip().strip('"')
            try:
                value = float(text)
            except ValueError:
                value = float(response.json())
            if not math.isfinite(value) or value <= 0.0:
                raise WeatherNetworkPreflightError("NETWORK_CLOB_TIME_INVALID")
            return f"HTTP {response.status_code}; server_time_ok"

        async def wrh_action() -> str:
            result = await asyncio.to_thread(
                wrh.fetch_snapshot, station=REFERENCE_STATION, target_date=wrh_target
            )
            return (
                f"WRH+Synoptic ok; timezone={result.station_timezone}; "
                f"rows={len(result.snapshot.selected_rows)}"
            )

        async def near_action() -> str:
            result = await near.fetch_snapshot(
                station=REFERENCE_STATION,
                latitude=REFERENCE_LATITUDE,
                longitude=REFERENCE_LONGITUDE,
            )
            return f"NWS grid ok; evidence={result.evidence_sha256[:12]}"

        async def gefs_action() -> str:
            result = await gefs.target_day(
                station=REFERENCE_STATION,
                latitude=REFERENCE_LATITUDE,
                longitude=REFERENCE_LONGITUDE,
                target_date=today,
                unit="F",
                timezone=REFERENCE_TIMEZONE,
            )
            return f"GEFS hourly ok; members={len(result.member_series)}"

        async def telegram_action() -> str:
            response = await http.get(TELEGRAM_ORIGIN)
            if response.status_code >= 500:
                raise WeatherNetworkPreflightError("NETWORK_TELEGRAM_SERVER_ERROR")
            return f"HTTP {response.status_code}; anonymous transport ok"

        try:
            probes = await asyncio.gather(
                _probe(name="polymarket_gamma", url=GAMMA, required=True, action=gamma_action),
                _probe(name="polymarket_clob", url=CLOB, required=True, action=clob_action),
                _probe(
                    name="nws_wrh_synoptic",
                    url=WRH_TIMESERIES_PAGE,
                    required=True,
                    action=wrh_action,
                ),
                _probe(
                    name="nws_near_term_grid",
                    url=NWS_API_ORIGIN,
                    required=True,
                    action=near_action,
                ),
                _probe(
                    name="open_meteo_gefs",
                    url=OPEN_METEO_ENSEMBLE,
                    required=True,
                    action=gefs_action,
                ),
                _probe(
                    name="telegram_transport",
                    url=TELEGRAM_ORIGIN,
                    required=True,
                    action=telegram_action,
                ),
            )
        finally:
            await near.close()
            await gefs.close()

    return evaluate_network_probes(tuple(probes))


def report_json(report: WeatherPaperNetworkReport) -> str:
    return json.dumps(report.as_dict(), sort_keys=True, indent=2) + "\n"
