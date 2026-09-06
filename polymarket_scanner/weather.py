from __future__ import annotations

import asyncio
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from .config import settings
from .models import Market

AWC = "https://aviationweather.gov/api/data/metar"
AWC_TAF = "https://aviationweather.gov/api/data/taf"
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
FORECAST_REFRESH_SECONDS = 600
MAX_OBSERVATION_AGE_MINUTES = 100
FORECAST_MARGIN_F = 2
FORECAST_MARGIN_C = 1
FRONT_WIND_SHIFT_DEGREES = 100.0
PRESSURE_SWING_HPA = 5.0
THUNDERSTORM_CODES = {95, 96, 99}

MONTHS = {
    "january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
    "july":7,"august":8,"september":9,"october":10,"november":11,"december":12,
    "jan":1,"feb":2,"mar":3,"apr":4,"jun":6,"jul":7,"aug":8,"sep":9,"sept":9,"oct":10,"nov":11,"dec":12,
}


def market_observation_date(market: Market, local_now: datetime):
    text = f"{market.event_title} {market.question}"
    m = re.search(r"\bon\s+(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})(?:,?\s+(20\d{2}))?", text, re.I)
    if not m:
        return None
    month = MONTHS[m.group(1).lower()]
    day = int(m.group(2))
    year = int(m.group(3)) if m.group(3) else local_now.year
    try:
        return local_now.date().replace(year=year, month=month, day=day)
    except ValueError:
        return None


# Known airport timezones. Unknown stations are never treated as UTC for an
# ACTIONABLE risk-only TAF fallback; they remain unscored until a reliable timezone
# is available. Open-Meteo, when explicitly enabled on a dual-stack host, can still
# provide a dynamic timezone.
STATION_TZ = {
    "KLGA":"America/New_York","KJFK":"America/New_York","KEWR":"America/New_York",
    "KATL":"America/New_York","KBOS":"America/New_York","KDCA":"America/New_York",
    "KBWI":"America/New_York","KPHL":"America/New_York","KMIA":"America/New_York",
    "KMCO":"America/New_York","KTPA":"America/New_York","KCLT":"America/New_York",
    "KDTW":"America/Detroit","KORD":"America/Chicago","KDAL":"America/Chicago",
    "KDFW":"America/Chicago","KIAH":"America/Chicago","KHOU":"America/Chicago",
    "KAUS":"America/Chicago","KSAT":"America/Chicago","KMSP":"America/Chicago",
    "KDEN":"America/Denver","KSLC":"America/Denver","KPHX":"America/Phoenix",
    "KLAX":"America/Los_Angeles","KLAS":"America/Los_Angeles","KSEA":"America/Los_Angeles",
    "KSFO":"America/Los_Angeles","KPDX":"America/Los_Angeles",
    "CYYZ":"America/Toronto","CYUL":"America/Toronto","CYVR":"America/Vancouver",
    "EGLC":"Europe/London","EGLL":"Europe/London","EIDW":"Europe/Dublin",
    "LFPG":"Europe/Paris","EDDF":"Europe/Berlin","EDDM":"Europe/Berlin",
    "EHAM":"Europe/Amsterdam","LEMD":"Europe/Madrid","LEBL":"Europe/Madrid",
    "LIRF":"Europe/Rome","LOWW":"Europe/Vienna","LSZH":"Europe/Zurich",
    "ESSA":"Europe/Stockholm","ENGM":"Europe/Oslo","EKCH":"Europe/Copenhagen",
    "UUWW":"Europe/Moscow","UUEE":"Europe/Moscow","LTFM":"Europe/Istanbul",
    "OMDB":"Asia/Dubai","OMAA":"Asia/Dubai","VIDP":"Asia/Kolkata","VABB":"Asia/Kolkata",
    "VTBS":"Asia/Bangkok","WMKK":"Asia/Kuala_Lumpur","WSSS":"Asia/Singapore",
    "VHHH":"Asia/Hong_Kong","RCTP":"Asia/Taipei","ZBAA":"Asia/Shanghai",
    "ZSPD":"Asia/Shanghai","RJTT":"Asia/Tokyo","RKSI":"Asia/Seoul","RPLL":"Asia/Manila",
    "WIII":"Asia/Jakarta","YSSY":"Australia/Sydney","YMML":"Australia/Melbourne",
    "YBBN":"Australia/Brisbane","SBGR":"America/Sao_Paulo",
    "SAEZ":"America/Argentina/Buenos_Aires","FAOR":"Africa/Johannesburg","HECA":"Africa/Cairo",
}


@dataclass(slots=True)
class Observation:
    when: datetime
    temp_c: float
    raw: str


@dataclass(slots=True)
class ForecastHour:
    when: datetime
    temp_c: float | None
    precipitation_probability: float | None = None
    cloud_cover: float | None = None
    weather_code: int | None = None
    pressure_msl: float | None = None
    wind_direction: float | None = None
    wind_speed: float | None = None


@dataclass(slots=True)
class ForecastContext:
    station: str
    latitude: float
    longitude: float
    timezone_name: str
    fetched_at: datetime
    hours: list[ForecastHour]
    provider: str = "Open-Meteo best-match forecast"
    temperature_forecast: bool = True
    risk_only_reason: str | None = None


class ObservationBatch(list):
    """List-compatible observations with advisory forecast metadata attached."""
    def __init__(self, rows=(), *, forecast: ForecastContext | None = None):
        super().__init__(rows)
        self.forecast = forecast


def station_from_market(market: Market) -> str | None:
    text = " ".join([market.description, market.resolution_source, market.question, market.event_title])
    match = re.search(r"[?&]site=([A-Za-z0-9]{4})", text)
    if match:
        return match.group(1).upper()
    for code in re.findall(r"\b[A-Z]{4}\b", text):
        if code[0] in "KEYLRVWOCZSUFH":
            return code
    return None


def settlement_source_check(market: Market, station: str) -> dict:
    """Require an exact NWS Western Region time-series station before actionability."""
    text = " ".join([market.resolution_source or "", market.description or ""])
    for url in re.findall(r"https?://[^\s<>\"')]+", text, re.I):
        if "weather.gov/wrh/timeseries" not in url.lower():
            continue
        m = re.search(r"[?&]site=([A-Za-z0-9]{4})", url, re.I)
        if m and m.group(1).upper() == station.upper():
            return {"verified": True, "kind": "NOAA/NWS WRH", "url": url}
    if "weather.gov/wrh/timeseries" in text.lower():
        m = re.search(r"[?&]site=([A-Za-z0-9]{4})", text, re.I)
        if m and m.group(1).upper() == station.upper():
            return {"verified": True, "kind": "NOAA/NWS WRH", "url": market.resolution_source or "WRH rules text"}
    return {"verified": False, "kind": "unsupported/unverified", "url": market.resolution_source or ""}


def market_unit(market: Market) -> str:
    text = f"{market.question} {market.description}"
    if re.search(r"(?:°\s*)?C\b|Celsius", text, re.I):
        return "C"
    return "F"


def _to_unit(c: float, unit: str) -> float:
    return c if unit == "C" else c * 9.0 / 5.0 + 32.0


def _round_settlement(v: float) -> int:
    return math.floor(v + 0.5) if v >= 0 else math.ceil(v - 0.5)


def is_hourly_observation(dt: datetime, station: str) -> bool:
    minute = dt.minute
    if station.startswith("K"):
        return 51 <= minute <= 59
    return minute >= 56 or minute <= 4


def parse_bucket(question: str, unit: str) -> tuple[float | None, float | None]:
    q = question.replace("°", "").replace("–", "-").replace("—", "-")
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*[FC]?", q, re.I)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*[FC]?\s*(?:or\s*)?(?:higher|above|more)", q, re.I)
    if m:
        return float(m.group(1)), None
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*[FC]?\s*(?:or\s*)?(?:lower|below|less)", q, re.I)
    if m:
        return None, float(m.group(1))
    nums = re.findall(r"(-?\d+(?:\.\d+)?)\s*[FC]\b", q, re.I)
    if nums:
        x = float(nums[-1])
        return x, x
    return None, None


def in_bucket(value: float, bounds: tuple[float | None, float | None]) -> bool:
    lo, hi = bounds
    return (lo is None or value >= lo) and (hi is None or value <= hi)


def _first_number(row: dict, *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
    return None


def _angle_diff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _cloud_percent(clouds) -> float:
    mapping = {"SKC": 0.0, "CLR": 0.0, "FEW": 25.0, "SCT": 50.0, "BKN": 75.0, "OVC": 100.0}
    vals = []
    for cloud in clouds or []:
        if isinstance(cloud, dict):
            vals.append(mapping.get(str(cloud.get("cover") or "").upper(), 0.0))
    return max(vals, default=0.0)


class WeatherClient:
    def __init__(self) -> None:
        self.http = httpx.AsyncClient(
            timeout=settings.request_timeout,
            headers={"User-Agent": "polymarket-edge-scanner/0.6 https://github.com/heroo2123/Alpha"},
        )
        self.station_coordinates: dict[str, tuple[float, float]] = {}
        self.forecast_cache: dict[str, ForecastContext] = {}
        self.forecast_retry_after: dict[str, float] = {}
        self.forecast_tasks: dict[str, asyncio.Task] = {}
        self._forecast_sem = asyncio.Semaphore(6)

    async def close(self) -> None:
        for task in self.forecast_tasks.values():
            task.cancel()
        if self.forecast_tasks:
            await asyncio.gather(*self.forecast_tasks.values(), return_exceptions=True)
        self.forecast_tasks.clear()
        await self.http.aclose()

    async def _open_meteo_forecast(self, station: str, lat: float, lon: float) -> ForecastContext | None:
        try:
            r = await self.http.get(OPEN_METEO, params={
                "latitude": lat,
                "longitude": lon,
                "hourly": "temperature_2m,precipitation_probability,cloud_cover,weather_code,pressure_msl,wind_direction_10m,wind_speed_10m",
                "timezone": "auto",
                "forecast_days": 2,
            }, timeout=6.0)
            r.raise_for_status()
            data = r.json()
            tz_name = str(data.get("timezone") or "")
            if not tz_name:
                return None
            tz = ZoneInfo(tz_name)
            hourly = data.get("hourly") or {}
            times = hourly.get("time") or []
            temps = hourly.get("temperature_2m") or []
            precip = hourly.get("precipitation_probability") or []
            clouds = hourly.get("cloud_cover") or []
            codes = hourly.get("weather_code") or []
            pressure = hourly.get("pressure_msl") or []
            wind_dir = hourly.get("wind_direction_10m") or []
            wind_speed = hourly.get("wind_speed_10m") or []
            rows: list[ForecastHour] = []
            for i, raw_time in enumerate(times):
                if i >= len(temps) or temps[i] is None:
                    continue
                dt = datetime.fromisoformat(str(raw_time))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=tz)
                def val(seq):
                    if i >= len(seq) or seq[i] is None:
                        return None
                    try:
                        return float(seq[i])
                    except (TypeError, ValueError):
                        return None
                code = None
                if i < len(codes) and codes[i] is not None:
                    try:
                        code = int(codes[i])
                    except (TypeError, ValueError):
                        pass
                rows.append(ForecastHour(dt, float(temps[i]), val(precip), val(clouds), code, val(pressure), val(wind_dir), val(wind_speed)))
            if not rows:
                return None
            return ForecastContext(station, lat, lon, tz_name, datetime.now(timezone.utc), rows)
        except Exception:
            return None

    async def _taf_forecast(self, station: str, lat: float, lon: float) -> ForecastContext | None:
        """Worldwide IPv6-safe risk forecast from NOAA/AWC TAF."""
        tz_name = STATION_TZ.get(station)
        if not tz_name:
            return None
        try:
            r = await self.http.get(AWC_TAF, params={"ids": station, "format": "json"}, timeout=6.0)
            if r.status_code == 204:
                return None
            r.raise_for_status()
            payload = r.json()
            if not payload:
                return None
            taf = payload[0]
            rows: list[ForecastHour] = []
            for fcst in taf.get("fcsts") or []:
                ts = fcst.get("timeFrom")
                if not isinstance(ts, (int, float)):
                    continue
                when = datetime.fromtimestamp(ts, timezone.utc)
                wdir = None
                raw_wdir = fcst.get("wdir")
                if raw_wdir not in (None, "VRB"):
                    try:
                        wdir = float(raw_wdir)
                    except (TypeError, ValueError):
                        pass
                wspd = _first_number(fcst, "wspd", "windSpeed")
                wx = str(fcst.get("wxString") or fcst.get("wx") or "").upper()
                thunder = "TS" in wx
                precip = 60.0 if re.search(r"(?:RA|SN|SH|DZ)", wx) else 0.0
                rows.append(ForecastHour(
                    when=when,
                    temp_c=None,
                    precipitation_probability=precip,
                    cloud_cover=_cloud_percent(fcst.get("clouds")),
                    weather_code=95 if thunder else None,
                    pressure_msl=None,
                    wind_direction=wdir,
                    wind_speed=wspd,
                ))
            if not rows:
                return None
            return ForecastContext(
                station, lat, lon, tz_name, datetime.now(timezone.utc), rows,
                provider="NOAA/AviationWeather TAF risk forecast",
                temperature_forecast=False,
                risk_only_reason="TAF has no reliable surface-temperature maximum; stricter observed-lock gate used",
            )
        except Exception:
            return None

    async def _forecast(self, station: str) -> ForecastContext | None:
        cached = self.forecast_cache.get(station)
        if cached and (time.time() - cached.fetched_at.timestamp()) < FORECAST_REFRESH_SECONDS:
            return cached
        if time.time() < self.forecast_retry_after.get(station, 0.0):
            return None
        coords = self.station_coordinates.get(station)
        if not coords:
            return None
        lat, lon = coords

        forecast = None
        if settings.weather_open_meteo_enabled:
            forecast = await self._open_meteo_forecast(station, lat, lon)
        if forecast is None:
            forecast = await self._taf_forecast(station, lat, lon)
        if forecast is not None:
            self.forecast_cache[station] = forecast
            self.forecast_retry_after.pop(station, None)
        else:
            self.forecast_retry_after[station] = time.time() + FORECAST_REFRESH_SECONDS
        return forecast

    def _fresh_cached_forecast(self, station: str) -> ForecastContext | None:
        cached = self.forecast_cache.get(station)
        if not cached:
            return None
        if time.time() - cached.fetched_at.timestamp() >= FORECAST_REFRESH_SECONDS:
            return None
        return cached

    async def _forecast_background_worker(self, station: str) -> None:
        try:
            async with self._forecast_sem:
                await self._forecast(station)
        finally:
            self.forecast_tasks.pop(station, None)

    def _ensure_forecast_background(self, station: str) -> None:
        if self._fresh_cached_forecast(station) is not None:
            return
        if time.time() < self.forecast_retry_after.get(station, 0.0):
            return
        if station not in self.station_coordinates:
            return
        task = self.forecast_tasks.get(station)
        if task is None or task.done():
            self.forecast_tasks[station] = asyncio.create_task(self._forecast_background_worker(station))

    async def observations(self, station: str, hours: int = 30) -> ObservationBatch:
        """Return official METAR observations promptly; refresh forecast separately.

        Slow/missing TAF data must never hold the entire observation batch hostage.
        A fresh cached forecast is attached when available. Otherwise a bounded
        background job refreshes it and a later observation cycle will attach it.
        """
        try:
            r = await self.http.get(AWC, params={"ids": station, "format": "json", "hours": hours})
            if r.status_code == 204:
                return ObservationBatch()
            r.raise_for_status()
            payload = r.json()
            out: list[Observation] = []
            for row in payload:
                lat = _first_number(row, "lat", "latitude")
                lon = _first_number(row, "lon", "longitude")
                if lat is not None and lon is not None:
                    self.station_coordinates[station] = (lat, lon)
                ts = row.get("obsTime")
                if isinstance(ts, (int, float)):
                    dt = datetime.fromtimestamp(ts, timezone.utc)
                else:
                    raw_ts = row.get("reportTime") or row.get("receiptTime")
                    if not raw_ts:
                        continue
                    dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                temp = row.get("temp")
                if temp is None:
                    continue
                out.append(Observation(dt, float(temp), row.get("rawOb") or ""))
            forecast = self._fresh_cached_forecast(station)
            if forecast is None:
                self._ensure_forecast_background(station)
            return ObservationBatch(sorted(out, key=lambda x: x.when), forecast=forecast)
        except Exception:
            return ObservationBatch()


def lock_probability(market: Market, observations: list[Observation], station: str, now: datetime | None = None) -> dict | None:
    """Conservative late-day daily-high lock model.

    Exact WRH settlement station and fresh official hourly observations are always
    required. A full temperature forecast is preferred. If unavailable, an AWC TAF
    may be used only as a risk gate, with stricter late-day cooling/drop requirements.
    """
    if not observations:
        return None
    forecast: ForecastContext | None = getattr(observations, "forecast", None)
    if forecast is None:
        return None
    source = settlement_source_check(market, station)
    if not source["verified"]:
        return None

    now = now or datetime.now(timezone.utc)
    unit = market_unit(market)
    tz_name = forecast.timezone_name or STATION_TZ.get(station)
    if not tz_name:
        return None
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        return None
    local_now = now.astimezone(tz)
    local_date = local_now.date()
    target_date = market_observation_date(market, local_now)
    if target_date is not None and target_date != local_date:
        return None

    hourly = [o for o in observations if o.when.astimezone(tz).date() == local_date and is_hourly_observation(o.when, station)]
    if len(hourly) < 3:
        return None
    latest_age_minutes = max(0.0, (now - hourly[-1].when.astimezone(timezone.utc)).total_seconds() / 60.0)
    if latest_age_minutes > MAX_OBSERVATION_AGE_MINUTES:
        return None

    values = [_round_settlement(_to_unit(o.temp_c, unit)) for o in hourly]
    current = values[-1]
    observed_max = max(values)
    cooling = 0
    for i in range(len(values)-1, 0, -1):
        if values[i] <= values[i-1]:
            cooling += 1
        else:
            break

    local_hour = local_now.hour + local_now.minute / 60.0
    if local_hour < settings.weather_lock_min_local_hour or cooling < settings.weather_lock_cooling_obs:
        return None

    floor_now = local_now.replace(minute=0, second=0, microsecond=0)
    remaining = [h for h in forecast.hours if h.when.astimezone(tz).date() == local_date and h.when.astimezone(tz) >= floor_now]
    if not remaining:
        return None

    thunderstorm = any(h.weather_code in THUNDERSTORM_CODES for h in remaining if h.weather_code is not None)
    wind_pairs = [(h.wind_direction, h.wind_speed) for h in remaining if h.wind_direction is not None and h.wind_speed is not None]
    max_wind_shift = 0.0
    max_wind_speed = max((float(speed) for _, speed in wind_pairs), default=0.0)
    for (a, _), (b, _) in zip(wind_pairs, wind_pairs[1:]):
        max_wind_shift = max(max_wind_shift, _angle_diff(float(a), float(b)))
    pressures = [float(h.pressure_msl) for h in remaining if h.pressure_msl is not None]
    pressure_swing = (max(pressures) - min(pressures)) if len(pressures) >= 2 else 0.0
    regime_shift = (max_wind_shift >= FRONT_WIND_SHIFT_DEGREES and max_wind_speed >= 15.0) or pressure_swing >= PRESSURE_SWING_HPA
    if thunderstorm or regime_shift:
        return None

    max_precip = max((float(h.precipitation_probability) for h in remaining if h.precipitation_probability is not None), default=0.0)
    max_cloud = max((float(h.cloud_cover) for h in remaining if h.cloud_cover is not None), default=0.0)
    drop = observed_max - current
    required_margin = FORECAST_MARGIN_C if unit == "C" else FORECAST_MARGIN_F

    forecast_max = None
    forecast_margin = None
    if forecast.temperature_forecast:
        future_settlement = [_round_settlement(_to_unit(h.temp_c, unit)) for h in remaining if h.temp_c is not None]
        if not future_settlement:
            return None
        forecast_max = max(future_settlement)
        forecast_margin = observed_max - forecast_max
        if forecast_margin < required_margin:
            return None
    else:
        if local_hour < max(settings.weather_lock_min_local_hour, 16.0):
            return None
        if cooling < max(settings.weather_lock_cooling_obs, 3):
            return None
        if drop < required_margin:
            return None

    p = settings.weather_lock_min_probability
    if local_hour >= 16: p += 0.010
    if local_hour >= 18: p += 0.010
    if cooling >= 3: p += 0.005
    if cooling >= 4: p += 0.005
    if forecast.temperature_forecast and forecast_margin is not None:
        if forecast_margin >= required_margin + 1: p += 0.010
        if forecast_margin >= required_margin + 2: p += 0.005
    else:
        if drop >= required_margin + 1: p += 0.005
    if drop >= required_margin: p += 0.005
    if max_precip < 30: p += 0.005
    cap = 0.985 if forecast.temperature_forecast else 0.965
    p = max(settings.weather_lock_min_probability, min(cap, p))

    provider_note = (
        "advisory forecast/timezone: Open-Meteo best match"
        if forecast.temperature_forecast
        else "advisory risk forecast: NOAA/AviationWeather TAF; no surface-temperature max assumed"
    )
    return {
        "probability": p,
        "observed_max": observed_max,
        "current": current,
        "cooling_obs": cooling,
        "observed_drop": drop,
        "local_time": local_now.isoformat(timespec="minutes"),
        "timezone": tz_name,
        "unit": unit,
        "hourly_values": values[-6:],
        "latest_observation_age_minutes": latest_age_minutes,
        "forecast_remaining_max": forecast_max,
        "forecast_margin": forecast_margin,
        "forecast_required_margin": required_margin,
        "forecast_has_temperature": forecast.temperature_forecast,
        "forecast_risk_only_reason": forecast.risk_only_reason,
        "max_precip_probability": max_precip,
        "max_cloud_cover": max_cloud,
        "max_wind_shift_degrees": max_wind_shift,
        "pressure_swing_hpa": pressure_swing,
        "thunderstorm_risk": thunderstorm,
        "regime_shift_risk": regime_shift,
        "settlement_source_verified": True,
        "settlement_source_kind": source["kind"],
        "settlement_source_url": source["url"],
        "forecast_provider": forecast.provider,
        "forecast_fetched_at": forecast.fetched_at.isoformat(),
        "source": f"Official settlement: NOAA/NWS WRH exact station; observations: AviationWeather METAR; {provider_note}",
    }
