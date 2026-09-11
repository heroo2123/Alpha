from __future__ import annotations

"""Exact reconstruction core for the NWS WRH ``Hourly Data`` table.

Polymarket's current NWS temperature contracts resolve from the WRH Time Series
viewer rather than from api.weather.gov.  The WRH page is a client-rendered shell;
its versioned viewer script currently retrieves Synoptic Data v2 station time-series
and applies its own row-selection and display transforms.

This module freezes the exact semantics observed from the official WRH viewer script
``obs.js?v202601121730`` (SHA-256 pinned below):

* transport: Synoptic Data v2 ``/stations/timeseries`` with ``complete=1``, local
  observation timezone, and English Fahrenheit temperature units;
* ``GLOBAL-METAR`` is normalized by WRH to ``ASOS/AWOS``;
* for ASOS/AWOS Hourly Data, a row is retained when sea-level pressure is non-null;
  if pressure is null, a station-prefixed METAR row is retained as SPECI;
* if the pressure dataset is absent entirely, WRH's non-fed fallback retains minute
  51 through 59 observations;
* displayed Temp is JavaScript ``Math.round(air_temp_set_1[j])``.

A parsed snapshot is *not* a final settlement label.  WRH explicitly permits data
corrections until the contract-specific cutoff, and a later fetch cannot by itself
prove what the page showed at that cutoff.  Therefore snapshots always leave label
and correction-state authority false.  A separate transition/finality layer must
prove a prospectively captured cutoff state before calibration may consume it.
"""

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


WRH_SNAPSHOT_ADAPTER_VERSION = "nws_wrh_hourly_snapshot_v1_obsjs_202601121730"
WRH_VIEWER_SCRIPT_URL = "https://www.weather.gov/source/wrh/timeseries/obs.js?v202601121730"
WRH_VIEWER_SCRIPT_SHA256 = "46b015ad497f7165336918e50154462c4d4079d9403bb0f90359e54dfed7d11e"
WRH_SYNOPTIC_ENDPOINT = "https://api.synopticdata.com/v2/stations/timeseries"
WRH_SOURCE_ROLE = "NWS_WRH_BACKEND_SNAPSHOT_UNFINALIZED"
WRH_HOURLY_PROFILE = "WRH_ASOS_AWOS_HOURLY_DATA_V202601121730"

ROW_OFFICIAL_PRESSURE = "ASOS_OFFICIAL_PRESSURE_ROW"
ROW_SPECI = "ASOS_SPECI_ROW"
ROW_NONFED_MINUTE = "ASOS_NONFED_MINUTE_FALLBACK"


class WRHSourceError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _hash_payload(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finite_number(value: object, code: str) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WRHSourceError(code)
    number = float(value)
    if not math.isfinite(number):
        raise WRHSourceError(code)
    return number


def _finite_timestamp(value: object, code: str) -> float:
    number = _finite_number(value, code)
    if number < 0.0:
        raise WRHSourceError(code)
    return number


def js_math_round(value: object) -> int:
    """Replicate JavaScript Math.round for finite weather temperatures.

    JavaScript resolves exact half ties toward +infinity, unlike Python's built-in
    bankers rounding.  For finite numbers this is equivalent to ``floor(x + 0.5)``.
    Negative zero is irrelevant to whole-degree temperature bucket identity and is
    represented as integer zero.
    """
    number = _finite_number(value, "WRH_TEMPERATURE_INVALID")
    return int(math.floor(number + 0.5))


def _require_date(value: object, code: str) -> date:
    if type(value) is not date:
        raise WRHSourceError(code)
    return value


def _station_id(value: object) -> str:
    station = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{4}", station):
        raise WRHSourceError("WRH_STATION_INVALID")
    return station


def _parse_observation_time(value: object, timezone: ZoneInfo) -> tuple[str, datetime]:
    if not isinstance(value, str) or not value.strip():
        raise WRHSourceError("WRH_OBSERVATION_TIME_INVALID")
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise WRHSourceError("WRH_OBSERVATION_TIME_INVALID")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WRHSourceError("WRH_OBSERVATION_TIME_NAIVE")
    return raw, parsed.astimezone(timezone)


def _optional_text(value: object, code: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise WRHSourceError(code)
    return value


@dataclass(frozen=True, slots=True)
class WRHHourlyRow:
    observation_time_raw: str
    observation_time_local: datetime
    local_date: date
    minute: int
    row_kind: str
    raw_temp_f: float | None
    displayed_temp_f: int | None
    sea_level_pressure_dataset_present: bool
    sea_level_pressure_nonnull: bool
    metar: str | None

    def as_dict(self) -> dict:
        value = asdict(self)
        value["observation_time_local"] = self.observation_time_local.isoformat()
        value["local_date"] = self.local_date.isoformat()
        return value


@dataclass(frozen=True, slots=True)
class WRHSourceSnapshot:
    adapter: str
    source_role: str
    source_profile: str
    viewer_script_url: str
    viewer_script_sha256: str
    source_endpoint: str
    station: str
    raw_network: str
    normalized_network: str
    timezone: str
    target_date: date
    query_start_date: date
    query_end_date: date
    received_at: float
    selected_rows: tuple[WRHHourlyRow, ...]
    target_rows: tuple[WRHHourlyRow, ...]
    first_following_row: WRHHourlyRow | None
    target_display_temperatures_f: tuple[int, ...]
    target_high_f: int | None
    target_low_f: int | None
    source_payload_sha256: str
    target_state_sha256: str
    evidence_sha256: str
    transport_semantics_certified: bool = field(init=False, default=True)
    exact_wrh_snapshot: bool = field(init=False, default=True)
    correction_state_reconstructable: bool = field(init=False, default=False)
    calibration_label_authority: bool = field(init=False, default=False)
    settlement_label_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        value = asdict(self)
        value["target_date"] = self.target_date.isoformat()
        value["query_start_date"] = self.query_start_date.isoformat()
        value["query_end_date"] = self.query_end_date.isoformat()
        if self.first_following_row is not None:
            value["first_following_row"] = self.first_following_row.as_dict()
        value["selected_rows"] = [row.as_dict() for row in self.selected_rows]
        value["target_rows"] = [row.as_dict() for row in self.target_rows]
        return value


def _normalized_network(raw: object) -> tuple[str, str]:
    if not isinstance(raw, str) or not raw.strip():
        raise WRHSourceError("WRH_NETWORK_MISSING")
    original = raw.strip()
    normalized = "ASOS/AWOS" if original == "GLOBAL-METAR" else original
    if normalized != "ASOS/AWOS":
        raise WRHSourceError("WRH_NETWORK_UNSUPPORTED")
    return original, normalized


def _select_row_kind(
    *,
    station: str,
    minute: int,
    slp_dataset_present: bool,
    slp_value: object,
    metar: str | None,
) -> str | None:
    """Mirror the current WRH ASOS/AWOS Hourly Data predicate exactly."""
    if slp_dataset_present:
        if slp_value is not None:
            return ROW_OFFICIAL_PRESSURE
        if metar is not None and metar.upper().startswith(station):
            return ROW_SPECI
        return None
    if 51 <= minute <= 59:
        return ROW_NONFED_MINUTE
    return None


def _snapshot_evidence_payload(snapshot: WRHSourceSnapshot) -> dict:
    return {
        "adapter": snapshot.adapter,
        "source_role": snapshot.source_role,
        "source_profile": snapshot.source_profile,
        "viewer_script_url": snapshot.viewer_script_url,
        "viewer_script_sha256": snapshot.viewer_script_sha256,
        "source_endpoint": snapshot.source_endpoint,
        "station": snapshot.station,
        "raw_network": snapshot.raw_network,
        "normalized_network": snapshot.normalized_network,
        "timezone": snapshot.timezone,
        "target_date": snapshot.target_date.isoformat(),
        "query_start_date": snapshot.query_start_date.isoformat(),
        "query_end_date": snapshot.query_end_date.isoformat(),
        "received_at": snapshot.received_at,
        "source_payload_sha256": snapshot.source_payload_sha256,
        "target_state_sha256": snapshot.target_state_sha256,
        "selected_rows": [row.as_dict() for row in snapshot.selected_rows],
        "first_following_row": snapshot.first_following_row.as_dict() if snapshot.first_following_row else None,
    }


def parse_synoptic_wrh_hourly_snapshot(
    payload: object,
    *,
    station: str,
    target_date: date,
    query_start_date: date,
    query_end_date: date,
    received_at: float,
    viewer_script_url: str = WRH_VIEWER_SCRIPT_URL,
    viewer_script_sha256: str = WRH_VIEWER_SCRIPT_SHA256,
    source_endpoint: str = WRH_SYNOPTIC_ENDPOINT,
    requested_unit: str = "F",
    complete: int = 1,
    obtimezone: str = "local",
) -> WRHSourceSnapshot:
    """Parse one Synoptic response using the pinned WRH Hourly Data semantics.

    The query identity is explicit because payload content alone cannot prove that it
    came from the same request profile used by WRH.  Only Fahrenheit, complete local-
    timezone ASOS/AWOS snapshots are supported in this phase.
    """
    station_id = _station_id(station)
    target = _require_date(target_date, "WRH_TARGET_DATE_INVALID")
    start = _require_date(query_start_date, "WRH_QUERY_START_DATE_INVALID")
    end = _require_date(query_end_date, "WRH_QUERY_END_DATE_INVALID")
    if start > target or end < target + timedelta(days=1) or end < start:
        raise WRHSourceError("WRH_QUERY_COVERAGE_INSUFFICIENT")
    received = _finite_timestamp(received_at, "WRH_RECEIVED_AT_INVALID")

    if viewer_script_url != WRH_VIEWER_SCRIPT_URL:
        raise WRHSourceError("WRH_VIEWER_SCRIPT_URL_MISMATCH")
    if str(viewer_script_sha256 or "").lower() != WRH_VIEWER_SCRIPT_SHA256:
        raise WRHSourceError("WRH_VIEWER_SCRIPT_SHA_MISMATCH")
    if source_endpoint != WRH_SYNOPTIC_ENDPOINT:
        raise WRHSourceError("WRH_SOURCE_ENDPOINT_MISMATCH")
    if requested_unit != "F":
        raise WRHSourceError("WRH_UNIT_UNSUPPORTED")
    if type(complete) is not int or complete != 1:
        raise WRHSourceError("WRH_COMPLETE_QUERY_REQUIRED")
    if obtimezone != "local":
        raise WRHSourceError("WRH_LOCAL_TIMEZONE_QUERY_REQUIRED")

    if not isinstance(payload, dict):
        raise WRHSourceError("WRH_PAYLOAD_INVALID")
    summary = payload.get("SUMMARY")
    if not isinstance(summary, dict) or str(summary.get("RESPONSE_MESSAGE") or "") != "OK":
        raise WRHSourceError("WRH_RESPONSE_NOT_OK")
    stations = payload.get("STATION")
    if not isinstance(stations, list) or len(stations) != 1 or not isinstance(stations[0], dict):
        raise WRHSourceError("WRH_STATION_ENVELOPE_INVALID")
    source_station = stations[0]
    returned_station = _station_id(source_station.get("STID"))
    if returned_station != station_id:
        raise WRHSourceError("WRH_STATION_ID_MISMATCH")
    raw_network, normalized_network = _normalized_network(source_station.get("SHORTNAME"))

    timezone_name = str(source_station.get("TIMEZONE") or "").strip()
    if not timezone_name:
        raise WRHSourceError("WRH_TIMEZONE_MISSING")
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        raise WRHSourceError("WRH_TIMEZONE_INVALID")

    observations = source_station.get("OBSERVATIONS")
    if not isinstance(observations, dict):
        raise WRHSourceError("WRH_OBSERVATIONS_MISSING")
    times = observations.get("date_time")
    temps = observations.get("air_temp_set_1")
    if not isinstance(times, list) or not isinstance(temps, list) or not times:
        raise WRHSourceError("WRH_REQUIRED_SERIES_MISSING")
    count = len(times)
    if len(temps) != count:
        raise WRHSourceError("WRH_SERIES_LENGTH_MISMATCH")

    slp_dataset_present = "sea_level_pressure_set_1" in observations
    slp = observations.get("sea_level_pressure_set_1") if slp_dataset_present else None
    if slp_dataset_present and (not isinstance(slp, list) or len(slp) != count):
        raise WRHSourceError("WRH_SLP_SERIES_LENGTH_MISMATCH")
    metar_dataset_present = "metar_set_1" in observations
    metars = observations.get("metar_set_1") if metar_dataset_present else None
    if metar_dataset_present and (not isinstance(metars, list) or len(metars) != count):
        raise WRHSourceError("WRH_METAR_SERIES_LENGTH_MISMATCH")

    parsed_times: list[tuple[str, datetime]] = []
    seen_instants: set[str] = set()
    for raw in times:
        raw_text, local_dt = _parse_observation_time(raw, timezone)
        instant = local_dt.astimezone(ZoneInfo("UTC")).isoformat()
        if instant in seen_instants:
            raise WRHSourceError("WRH_OBSERVATION_TIME_DUPLICATE")
        seen_instants.add(instant)
        parsed_times.append((raw_text, local_dt))

    selected: list[WRHHourlyRow] = []
    for index, (raw_time, local_dt) in enumerate(parsed_times):
        raw_temp = temps[index]
        if raw_temp is None:
            temp_f = None
            displayed = None
        else:
            temp_f = _finite_number(raw_temp, "WRH_TEMPERATURE_INVALID")
            displayed = js_math_round(temp_f)
        metar = _optional_text(metars[index], "WRH_METAR_VALUE_INVALID") if metar_dataset_present else None
        slp_value = slp[index] if slp_dataset_present else None
        kind = _select_row_kind(
            station=station_id,
            minute=local_dt.minute,
            slp_dataset_present=slp_dataset_present,
            slp_value=slp_value,
            metar=metar,
        )
        if kind is None:
            continue
        selected.append(WRHHourlyRow(
            observation_time_raw=raw_time,
            observation_time_local=local_dt,
            local_date=local_dt.date(),
            minute=local_dt.minute,
            row_kind=kind,
            raw_temp_f=temp_f,
            displayed_temp_f=displayed,
            sea_level_pressure_dataset_present=slp_dataset_present,
            sea_level_pressure_nonnull=slp_value is not None,
            metar=metar,
        ))

    selected.sort(key=lambda row: row.observation_time_local.astimezone(ZoneInfo("UTC")))
    target_rows = tuple(row for row in selected if row.local_date == target)
    following_rows = tuple(row for row in selected if row.local_date > target)
    first_following = following_rows[0] if following_rows else None
    display_values = tuple(row.displayed_temp_f for row in target_rows if row.displayed_temp_f is not None)

    # Bind every source field that can affect WRH Hourly row membership or Temp cells,
    # not just the selected rows, so omitted/reordered/unselected inputs are detectable.
    source_payload_sha = _hash_payload({
        "station": returned_station,
        "shortname": raw_network,
        "timezone": timezone_name,
        "date_time": times,
        "air_temp_set_1": temps,
        "sea_level_pressure_set_1_present": slp_dataset_present,
        "sea_level_pressure_set_1": slp if slp_dataset_present else None,
        "metar_set_1_present": metar_dataset_present,
        "metar_set_1": metars if metar_dataset_present else None,
    })
    target_state_sha = _hash_payload({
        "station": station_id,
        "target_date": target.isoformat(),
        "rows": [row.as_dict() for row in target_rows],
    })

    shell = WRHSourceSnapshot(
        adapter=WRH_SNAPSHOT_ADAPTER_VERSION,
        source_role=WRH_SOURCE_ROLE,
        source_profile=WRH_HOURLY_PROFILE,
        viewer_script_url=WRH_VIEWER_SCRIPT_URL,
        viewer_script_sha256=WRH_VIEWER_SCRIPT_SHA256,
        source_endpoint=WRH_SYNOPTIC_ENDPOINT,
        station=station_id,
        raw_network=raw_network,
        normalized_network=normalized_network,
        timezone=timezone_name,
        target_date=target,
        query_start_date=start,
        query_end_date=end,
        received_at=received,
        selected_rows=tuple(selected),
        target_rows=target_rows,
        first_following_row=first_following,
        target_display_temperatures_f=display_values,
        target_high_f=max(display_values) if display_values else None,
        target_low_f=min(display_values) if display_values else None,
        source_payload_sha256=source_payload_sha,
        target_state_sha256=target_state_sha,
        evidence_sha256="0" * 64,
    )
    evidence_sha = _hash_payload(_snapshot_evidence_payload(shell))
    return WRHSourceSnapshot(
        **{
            field_name: getattr(shell, field_name)
            for field_name in shell.__dataclass_fields__
            if shell.__dataclass_fields__[field_name].init and field_name != "evidence_sha256"
        },
        evidence_sha256=evidence_sha,
    )
