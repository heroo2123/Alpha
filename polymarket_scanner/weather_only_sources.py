from __future__ import annotations

"""Weather data-role boundaries and official historical calibration adapters.

A source being official does not make it the settlement state named by a Polymarket
contract.  In particular, HKO's CLMMAXT/CLMMINT open-data series is documented as a
monthly-updated climate dataset.  It is useful for historical forecast calibration,
but the current Hong Kong Polymarket template resolves from the Daily Extract at
its *initial publication* and ignores later revisions.  A historical archive read
therefore cannot reconstruct that initial-publication state and is never settlement
or result-lag authority here.
"""

import math
from dataclasses import asdict, dataclass
from datetime import date

import httpx

from .config import settings
from .weather_only_contracts import DAILY_HIGH, DAILY_LOW


HKO_OPEN_DATA = "https://data.weather.gov.hk/weatherAPI/opendata/opendata.php"
SOURCE_ROLE_VERSION = "weather_source_roles_v1_no_authority_leakage"
HKO_ARCHIVE_ADAPTER = "HKO_CLMMAXT_CLMMINT_MONTHLY_ARCHIVE_V1_CALIBRATION_ONLY"


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


def _field_index(fields: list, *wanted: str) -> int | None:
    normalized = [str(value or "").strip().lower().replace(" ", "") for value in fields]
    targets = {value.lower().replace(" ", "") for value in wanted}
    for index, value in enumerate(normalized):
        if value in targets:
            return index
    return None


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
