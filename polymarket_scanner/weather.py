from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from .config import settings
from .models import Market

AWC = "https://aviationweather.gov/api/data/metar"

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

STATION_TZ = {
    "KLGA": "America/New_York", "KJFK": "America/New_York", "KATL": "America/New_York",
    "KBOS": "America/New_York", "KDCA": "America/New_York", "KPHL": "America/New_York",
    "KORD": "America/Chicago", "KDAL": "America/Chicago", "KHOU": "America/Chicago",
    "KDEN": "America/Denver", "KPHX": "America/Phoenix", "KLAX": "America/Los_Angeles",
    "KSEA": "America/Los_Angeles", "KSFO": "America/Los_Angeles", "KPDX": "America/Los_Angeles",
    "EGLC": "Europe/London", "EGLL": "Europe/London", "LFPG": "Europe/Paris",
    "EDDF": "Europe/Berlin", "EHAM": "Europe/Amsterdam", "WMKK": "Asia/Kuala_Lumpur",
    "WSSS": "Asia/Singapore", "VHHH": "Asia/Hong_Kong", "RJTT": "Asia/Tokyo",
    "RKSI": "Asia/Seoul", "YSSY": "Australia/Sydney",
}


@dataclass(slots=True)
class Observation:
    when: datetime
    temp_c: float
    raw: str


def station_from_market(market: Market) -> str | None:
    text = " ".join([market.description, market.resolution_source, market.question, market.event_title])
    match = re.search(r"[?&]site=([A-Za-z0-9]{4})", text)
    if match:
        return match.group(1).upper()
    for code in re.findall(r"\b[A-Z]{4}\b", text):
        if code[0] in "KEYLRVW":
            return code
    return None


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


class WeatherClient:
    def __init__(self) -> None:
        self.http = httpx.AsyncClient(timeout=settings.request_timeout, headers={"User-Agent": "polymarket-edge-scanner/0.1"})

    async def close(self) -> None:
        await self.http.aclose()

    async def observations(self, station: str, hours: int = 30) -> list[Observation]:
        try:
            r = await self.http.get(AWC, params={"ids": station, "format": "json", "hours": hours})
            if r.status_code == 204:
                return []
            r.raise_for_status()
            out: list[Observation] = []
            for row in r.json():
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
            return sorted(out, key=lambda x: x.when)
        except Exception:
            return []


def lock_probability(market: Market, observations: list[Observation], station: str, now: datetime | None = None) -> dict | None:
    if not observations:
        return None
    now = now or datetime.now(timezone.utc)
    unit = market_unit(market)
    tz_name = STATION_TZ.get(station)
    tz = ZoneInfo(tz_name) if tz_name else timezone.utc
    local_now = now.astimezone(tz)
    local_date = local_now.date()
    target_date = market_observation_date(market, local_now)
    if target_date is not None and target_date != local_date:
        return None
    hourly = [o for o in observations if o.when.astimezone(tz).date() == local_date and is_hourly_observation(o.when, station)]
    if len(hourly) < 3:
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

    p = 0.50
    hour = local_now.hour + local_now.minute / 60.0
    if hour >= 14: p += 0.15
    if hour >= 16: p += 0.12
    if hour >= 18: p += 0.08
    if cooling >= 1: p += 0.05
    if cooling >= 2: p += 0.05
    if cooling >= 3: p += 0.03
    drop = observed_max - current
    if drop >= 1: p += 0.03
    if drop >= 2: p += 0.03
    if drop >= 4: p += 0.03
    if current >= observed_max:
        p -= 0.10
    p = max(0.50, min(0.995, p))
    return {
        "probability": p,
        "observed_max": observed_max,
        "current": current,
        "cooling_obs": cooling,
        "local_time": local_now.isoformat(timespec="minutes"),
        "unit": unit,
        "hourly_values": values[-6:],
        "source": "AviationWeather METAR proxy for NOAA WRH hourly observations",
    }
