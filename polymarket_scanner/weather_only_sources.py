from __future__ import annotations

"""Weather data-role boundaries and bounded official-source adapters.

A source being official does not make it the exact settlement state named by a
Polymarket contract.  This module makes that distinction explicit in the types:

* HKO CLMMAXT/CLMMINT is an official monthly climate archive useful for historical
  model calibration, but it cannot reconstruct the Daily Extract's initial
  publication state used by current Hong Kong temperature rules.
* api.weather.gov station observations are official NWS observations, but current
  NWS documentation says those endpoints are fed through MADIS/QC and may be
  delayed, while the WRH Time Series page named by many Polymarket contracts calls
  its displayed data preliminary and subject to QC review/adjustment.  Until exact
  state/precision/correction equivalence is proven, NWS API observations are
  official *proxies* only, never settlement or calibration-label authority.

Nothing in this module places orders, sends alerts, or grants financial authority.
"""

import math
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone

import httpx

from .config import settings
from .weather_only_contracts import DAILY_HIGH, DAILY_LOW


HKO_OPEN_DATA = "https://data.weather.gov.hk/weatherAPI/opendata/opendata.php"
NWS_API = "https://api.weather.gov"
SOURCE_ROLE_VERSION = "weather_source_roles_v2_explicit_nws_proxy_boundary"
HKO_ARCHIVE_ADAPTER = "HKO_CLMMAXT_CLMMINT_MONTHLY_ARCHIVE_V1_CALIBRATION_ONLY"
NWS_OBSERVATION_PROXY_ADAPTER = "NWS_API_STATION_OBSERVATION_V1_OFFICIAL_PROXY"
NWS_OBSERVATION_PROXY_ROLE = "OFFICIAL_NWS_OBSERVATION_PROXY_ONLY"
MAX_NWS_OBSERVATIONS = 250
MAX_NWS_WINDOW_DAYS = 31
_STATION_RE = re.compile(r"^[A-Z0-9]{4}$")


class WeatherSourceError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class HistoricalTemperatureRecord:
    adapter: str
    source_organization: str
    station: str
    family: str
    day: date
    temperature_c: float
    source_role: str
    settlement_authority: bool
    initial_publication_state_reconstructable: bool

    def as_dict(self) -> dict:
        value = asdict(self)
        value["day"] = self.day.isoformat()
        return value


@dataclass(frozen=True, slots=True)
class WeatherObservationRecord:
    """One exact-station NWS API observation with deliberately limited authority."""

    adapter: str
    source_organization: str
    station: str
    observed_at: datetime
    temperature_c: float
    raw_message: str | None
    source_url: str | None
    source_role: str
    official_source: bool
    settlement_authority: bool
    calibration_label_authority: bool
    correction_state_reconstructable: bool
    financial_authority: bool

    def as_dict(self) -> dict:
        value = asdict(self)
        value["observed_at"] = self.observed_at.isoformat()
        return value


def _field_index(fields: list, *wanted: str) -> int | None:
    normalized = [str(value or "").strip().lower().replace(" ", "") for value in fields]
    targets = {value.lower().replace(" ", "") for value in wanted}
    for index, value in enumerate(normalized):
        if value in targets:
            return index
    return None


def _aware_datetime(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise WeatherSourceError("NWS_OBSERVATION_TIMESTAMP_INVALID")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise WeatherSourceError("NWS_OBSERVATION_TIMESTAMP_INVALID")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WeatherSourceError("NWS_OBSERVATION_TIMESTAMP_NAIVE")
    return parsed.astimezone(timezone.utc)


def _station_from_nws_url(value: object) -> str | None:
    text = str(value or "").strip()
    match = re.search(r"/stations/([A-Za-z0-9]{4})(?:/|$)", text)
    return match.group(1).upper() if match else None


def parse_nws_station_observation(
    payload: object,
    *,
    requested_station: str,
) -> WeatherObservationRecord:
    """Parse an api.weather.gov observation without upgrading it to settlement truth.

    Required evidence is intentionally strict: exact four-character station identity,
    timezone-aware source timestamp, finite temperature, and explicit Celsius unit.
    A correct parse proves only that this is an official NWS API observation.
    """
    station = str(requested_station or "").strip().upper()
    if not _STATION_RE.fullmatch(station):
        raise WeatherSourceError("NWS_STATION_INVALID")
    if not isinstance(payload, dict) or payload.get("type") != "Feature":
        raise WeatherSourceError("NWS_OBSERVATION_ENVELOPE_INVALID")
    properties = payload.get("properties")
    if not isinstance(properties, dict):
        raise WeatherSourceError("NWS_OBSERVATION_PROPERTIES_INVALID")

    actual_station = _station_from_nws_url(properties.get("station"))
    if actual_station != station:
        raise WeatherSourceError("NWS_OBSERVATION_STATION_MISMATCH")
    observed_at = _aware_datetime(properties.get("timestamp"))

    temperature = properties.get("temperature")
    if not isinstance(temperature, dict):
        raise WeatherSourceError("NWS_OBSERVATION_TEMPERATURE_INVALID")
    if str(temperature.get("unitCode") or "").strip() != "wmoUnit:degC":
        raise WeatherSourceError("NWS_OBSERVATION_TEMPERATURE_UNIT_UNSUPPORTED")
    value = temperature.get("value")
    if value is None or isinstance(value, bool):
        raise WeatherSourceError("NWS_OBSERVATION_TEMPERATURE_MISSING")
    try:
        temp_c = float(value)
    except (TypeError, ValueError, OverflowError):
        raise WeatherSourceError("NWS_OBSERVATION_TEMPERATURE_INVALID")
    if not math.isfinite(temp_c):
        raise WeatherSourceError("NWS_OBSERVATION_TEMPERATURE_INVALID")

    raw = properties.get("rawMessage")
    raw_message = str(raw).strip() if isinstance(raw, str) and raw.strip() else None
    source_url = str(payload.get("id") or "").strip() or None

    return WeatherObservationRecord(
        adapter=NWS_OBSERVATION_PROXY_ADAPTER,
        source_organization="National Weather Service",
        station=station,
        observed_at=observed_at,
        temperature_c=temp_c,
        raw_message=raw_message,
        source_url=source_url,
        source_role=NWS_OBSERVATION_PROXY_ROLE,
        official_source=True,
        settlement_authority=False,
        calibration_label_authority=False,
        correction_state_reconstructable=False,
        financial_authority=False,
    )


def parse_hko_climate_json(
    payload: object,
    *,
    station: str,
    family: str,
) -> list[HistoricalTemperatureRecord]:
    if family not in {DAILY_HIGH, DAILY_LOW}:
        raise WeatherSourceError("HKO_FAMILY_UNSUPPORTED")
    if not isinstance(payload, dict):
        raise WeatherSourceError("HKO_ARCHIVE_ENVELOPE_INVALID")
    fields = payload.get("fields")
    data = payload.get("data")
    if not isinstance(fields, list) or not isinstance(data, list):
        raise WeatherSourceError("HKO_ARCHIVE_SCHEMA_INVALID")
    year_i = _field_index(fields, "Year")
    month_i = _field_index(fields, "Month")
    day_i = _field_index(fields, "Day")
    temp_i = _field_index(fields, "Temperature(C)", "Temperature (C)", "Temperature")
    if None in {year_i, month_i, day_i, temp_i}:
        raise WeatherSourceError("HKO_ARCHIVE_FIELDS_UNSUPPORTED")

    out: list[HistoricalTemperatureRecord] = []
    seen: set[date] = set()
    for row in data:
        if not isinstance(row, list):
            raise WeatherSourceError("HKO_ARCHIVE_ROW_INVALID")
        needed = max(year_i, month_i, day_i, temp_i)
        if len(row) <= needed:
            raise WeatherSourceError("HKO_ARCHIVE_ROW_INVALID")
        try:
            when = date(int(row[year_i]), int(row[month_i]), int(row[day_i]))
            temp = float(row[temp_i])
        except (TypeError, ValueError, OverflowError):
            # Missing-value legends use non-numeric markers; they are unavailable
            # calibration days, not temperatures to coerce.
            continue
        if not math.isfinite(temp):
            continue
        if when in seen:
            raise WeatherSourceError("HKO_ARCHIVE_DUPLICATE_DAY")
        seen.add(when)
        out.append(HistoricalTemperatureRecord(
            adapter=HKO_ARCHIVE_ADAPTER,
            source_organization="Hong Kong Observatory",
            station=str(station).upper(),
            family=family,
            day=when,
            temperature_c=temp,
            source_role="HISTORICAL_MODEL_CALIBRATION_ONLY",
            settlement_authority=False,
            initial_publication_state_reconstructable=False,
        ))
    out.sort(key=lambda row: row.day)
    return out


class NWSObservationProxyClient:
    """Bounded official NWS API observations; never WRH settlement authority."""

    def __init__(self) -> None:
        self.http = httpx.AsyncClient(
            timeout=settings.request_timeout,
            limits=httpx.Limits(max_connections=3, max_keepalive_connections=2),
            headers={
                "User-Agent": "polymarket-weather-only-scanner/0.2 (+https://github.com/heroo2123/Alpha)",
                "Accept": "application/geo+json",
            },
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def observations(
        self,
        *,
        station: str,
        start: datetime,
        end: datetime,
        limit: int = 100,
    ) -> list[WeatherObservationRecord]:
        station_id = str(station or "").strip().upper()
        if not _STATION_RE.fullmatch(station_id):
            raise WeatherSourceError("NWS_STATION_INVALID")
        if start.tzinfo is None or start.utcoffset() is None or end.tzinfo is None or end.utcoffset() is None:
            raise WeatherSourceError("NWS_WINDOW_TIMESTAMP_NAIVE")
        start_utc = start.astimezone(timezone.utc)
        end_utc = end.astimezone(timezone.utc)
        if end_utc <= start_utc:
            raise WeatherSourceError("NWS_WINDOW_INVALID")
        if (end_utc - start_utc).total_seconds() > MAX_NWS_WINDOW_DAYS * 86400:
            raise WeatherSourceError("NWS_WINDOW_CAP")
        if isinstance(limit, bool) or not 1 <= int(limit) <= MAX_NWS_OBSERVATIONS:
            raise WeatherSourceError("NWS_OBSERVATION_LIMIT_INVALID")

        params = {
            "start": start_utc.isoformat().replace("+00:00", "Z"),
            "end": end_utc.isoformat().replace("+00:00", "Z"),
            "limit": int(limit),
        }
        try:
            response = await self.http.get(f"{NWS_API}/stations/{station_id}/observations", params=params)
        except httpx.TimeoutException:
            raise WeatherSourceError("NWS_OBSERVATION_TIMEOUT")
        except httpx.RequestError:
            raise WeatherSourceError("NWS_OBSERVATION_TRANSPORT")
        if response.status_code >= 400:
            raise WeatherSourceError("NWS_OBSERVATION_HTTP_STATUS")
        try:
            payload = response.json()
        except Exception:
            raise WeatherSourceError("NWS_OBSERVATION_JSON_INVALID")
        if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
            raise WeatherSourceError("NWS_OBSERVATION_COLLECTION_INVALID")
        features = payload.get("features")
        if not isinstance(features, list):
            raise WeatherSourceError("NWS_OBSERVATION_COLLECTION_INVALID")
        if len(features) > int(limit) or len(features) > MAX_NWS_OBSERVATIONS:
            raise WeatherSourceError("NWS_OBSERVATION_RESPONSE_CAP")

        rows = [parse_nws_station_observation(row, requested_station=station_id) for row in features]
        rows.sort(key=lambda row: row.observed_at)
        if len({row.observed_at for row in rows}) != len(rows):
            raise WeatherSourceError("NWS_OBSERVATION_DUPLICATE_TIMESTAMP")
        return rows


class HKOClimateArchiveClient:
    """Official monthly climate archive; never the live Daily Extract authority."""

    def __init__(self) -> None:
        self.http = httpx.AsyncClient(
            timeout=settings.request_timeout,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
            headers={"User-Agent": "polymarket-weather-only-scanner/0.1 (+github)"},
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def daily_extremes(
        self,
        *,
        station: str,
        year: int,
        family: str,
        month: int | None = None,
    ) -> list[HistoricalTemperatureRecord]:
        if family == DAILY_HIGH:
            data_type = "CLMMAXT"
        elif family == DAILY_LOW:
            data_type = "CLMMINT"
        else:
            raise WeatherSourceError("HKO_FAMILY_UNSUPPORTED")
        if not (1884 <= int(year) <= 2100):
            raise WeatherSourceError("HKO_YEAR_INVALID")
        if month is not None and not 1 <= int(month) <= 12:
            raise WeatherSourceError("HKO_MONTH_INVALID")
        params: dict[str, object] = {
            "dataType": data_type,
            "station": str(station).upper(),
            "year": int(year),
            "rformat": "json",
        }
        if month is not None:
            params["month"] = int(month)
        try:
            response = await self.http.get(HKO_OPEN_DATA, params=params)
        except httpx.TimeoutException:
            raise WeatherSourceError("HKO_ARCHIVE_TIMEOUT")
        except httpx.RequestError:
            raise WeatherSourceError("HKO_ARCHIVE_TRANSPORT")
        if response.status_code >= 400:
            raise WeatherSourceError("HKO_ARCHIVE_HTTP_STATUS")
        try:
            payload = response.json()
        except Exception:
            raise WeatherSourceError("HKO_ARCHIVE_JSON_INVALID")
        return parse_hko_climate_json(
            payload,
            station=str(station).upper(),
            family=family,
        )
