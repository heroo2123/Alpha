from __future__ import annotations

"""Read-only Synoptic/CWOP PWS adapter for same-day weather research.

This provider is deliberately diagnostic-only. Official WRH settlement-station
observations remain Layer 1 authority. Synoptic/CWOP observations cannot replace
official observations, reweight the three-layer probability, create Telegram trade
alerts, enter validated P&L, settle positions, or grant financial authority.

Network work is bounded in time and bytes. Provider-controlled compressed responses
are rejected from headers before body iteration so HTTPX cannot expand a compressed
payload before the application byte limit is applied.
"""

import asyncio
import json
import math
import os
import time
from dataclasses import asdict, dataclass

import httpx

from .weather_only_pws import (
    DEFAULT_PWS_COLLECTION_DEADLINE_SECONDS,
    DEFAULT_PWS_CONTRADICTION_C,
    DEFAULT_PWS_CONTRADICTION_F,
    DEFAULT_PWS_IDENTITY_LOCATION_TOLERANCE_KM,
    DEFAULT_PWS_MAX_ACCEPTED,
    DEFAULT_PWS_MAX_AGE_SECONDS,
    DEFAULT_PWS_MAX_CANDIDATES,
    DEFAULT_PWS_MAX_DISTANCE_KM,
    DEFAULT_PWS_MAX_FUTURE_SKEW_SECONDS,
    DEFAULT_PWS_MAX_RESPONSE_BYTES,
    DEFAULT_PWS_REQUEST_DEADLINE_SECONDS,
    PWS_STATUS_AUTH_ERROR,
    PWS_STATUS_AVAILABLE,
    PWS_STATUS_MALFORMED_RESPONSE,
    PWS_STATUS_NO_FRESH_QC,
    PWS_STATUS_PROVIDER_ERROR,
    PWS_STATUS_RESPONSE_TOO_LARGE,
    PWS_STATUS_TIMEOUT,
    PWS_STATUS_TRANSPORT_ERROR,
    PWS_STATUS_UNCONFIGURED,
    PWS_STATUS_UNSUPPORTED_CONTENT_ENCODING,
    PWSDiagnosticSnapshot,
    PWSError,
    PWSStationAttempt,
    _build_snapshot,
    _epoch,
    _finite,
    _haversine_km,
    _latitude,
    _longitude,
    _positive,
    _unit,
)


SYNOPTIC_PWS_SOURCE = "SYNOPTIC_CWOP_PWS"
SYNOPTIC_CWOP_NETWORK_ID = "65"
SYNOPTIC_TOKEN_ENV = "SYNOPTIC_PWS_TOKEN"
SYNOPTIC_BASE_URL = "https://api.synopticdata.com"
SYNOPTIC_LATEST_PATH = "/v2/stations/latest"
SYNOPTIC_QC_CHECKS = "synopticlabs"
MILES_PER_KM = 0.621371192237334


@dataclass(frozen=True, slots=True)
class SynopticPWSObservation:
    """Normalized PWS observation compatible with the generic PWS snapshot contract."""

    station_id: str
    discovery_latitude: float
    discovery_longitude: float
    observation_latitude: float
    observation_longitude: float
    requested_distance_km: float
    identity_location_delta_km: float
    observed_at: float
    provider_epoch_text: str | None
    provider_obs_time_utc: str | None
    received_at: float
    unit: str
    temperature: float
    qc_status: int
    provider_sensor_id: str
    source: str = SYNOPTIC_PWS_SOURCE
    settlement_authority: bool = False
    financial_authority: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def _strict_response_code(value: object) -> int:
    if isinstance(value, bool) or value is None:
        raise PWSError("PWS_SYNOPTIC_RESPONSE_CODE_INVALID")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text and text.lstrip("-").isdigit():
            return int(text)
    raise PWSError("PWS_SYNOPTIC_RESPONSE_CODE_INVALID")


def _truthy_restricted(value: object) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, int) and not isinstance(value, bool):
        return value != 0
    return False


def _expected_unit_label(unit: str) -> set[str]:
    if unit == "F":
        return {"f", "fahrenheit", "degrees fahrenheit", "degree fahrenheit"}
    return {"c", "celsius", "degrees celsius", "degree celsius"}


def _pick_temperature_observation(observations: object) -> tuple[str, dict] | None:
    if not isinstance(observations, dict):
        return None
    valid: list[tuple[str, dict]] = []
    for key, row in observations.items():
        key_text = str(key)
        if not key_text.startswith("air_temp") or not isinstance(row, dict):
            continue
        if row.get("date_time") in (None, "") or row.get("value") is None:
            continue
        valid.append((key_text, row))
    if not valid:
        return None
    for key, row in valid:
        if key == "air_temp_value_1":
            return key, row
    if len(valid) == 1:
        return valid[0]
    # Multiple non-primary sensors are ambiguous. Do not silently select one.
    return None


class SynopticCWOPPWSClient:
    """Bounded one-request Synoptic Latest client restricted to CWOP network 65."""

    def __init__(
        self,
        *,
        token: str | None = None,
        http: httpx.AsyncClient | None = None,
        base_url: str = SYNOPTIC_BASE_URL,
        max_distance_km: float = DEFAULT_PWS_MAX_DISTANCE_KM,
        identity_location_tolerance_km: float = DEFAULT_PWS_IDENTITY_LOCATION_TOLERANCE_KM,
        max_age_seconds: float = DEFAULT_PWS_MAX_AGE_SECONDS,
        max_future_skew_seconds: float = DEFAULT_PWS_MAX_FUTURE_SKEW_SECONDS,
        max_candidates: int = DEFAULT_PWS_MAX_CANDIDATES,
        max_accepted: int = DEFAULT_PWS_MAX_ACCEPTED,
        max_response_bytes: int = DEFAULT_PWS_MAX_RESPONSE_BYTES,
        request_deadline_seconds: float = DEFAULT_PWS_REQUEST_DEADLINE_SECONDS,
        collection_deadline_seconds: float = DEFAULT_PWS_COLLECTION_DEADLINE_SECONDS,
    ) -> None:
        self.token = str(
            os.environ.get(SYNOPTIC_TOKEN_ENV, "") if token is None else token
        ).strip()
        self.base_url = str(base_url).rstrip("/")
        self.max_distance_km = _positive(max_distance_km, "PWS_MAX_DISTANCE_INVALID")
        self.identity_location_tolerance_km = _positive(
            identity_location_tolerance_km, "PWS_IDENTITY_LOCATION_TOLERANCE_INVALID"
        )
        self.max_age_seconds = _positive(max_age_seconds, "PWS_MAX_AGE_INVALID")
        self.max_future_skew_seconds = _finite(
            max_future_skew_seconds, "PWS_MAX_FUTURE_SKEW_INVALID"
        )
        self.max_candidates = int(max_candidates)
        self.max_accepted = int(max_accepted)
        self.max_response_bytes = int(max_response_bytes)
        self.request_deadline_seconds = _positive(
            request_deadline_seconds, "PWS_REQUEST_DEADLINE_INVALID"
        )
        self.collection_deadline_seconds = _positive(
            collection_deadline_seconds, "PWS_COLLECTION_DEADLINE_INVALID"
        )
        if self.max_future_skew_seconds < 0.0:
            raise PWSError("PWS_CLIENT_LIMIT_INVALID")
        if not 1 <= self.max_candidates <= 10 or not 1 <= self.max_accepted <= self.max_candidates:
            raise PWSError("PWS_CLIENT_LIMIT_INVALID")
        if not 1 <= self.max_response_bytes <= 4 * 1024 * 1024:
            raise PWSError("PWS_CLIENT_LIMIT_INVALID")
        self._owns_http = http is None
        self.http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(5.0, connect=4.0, read=5.0, write=5.0, pool=4.0),
            limits=httpx.Limits(
                max_connections=2,
                max_keepalive_connections=1,
                keepalive_expiry=60.0,
            ),
            trust_env=False,
            follow_redirects=False,
        )

    async def close(self) -> None:
        if self._owns_http:
            await self.http.aclose()

    def _snapshot(self, **kwargs) -> PWSDiagnosticSnapshot:
        return _build_snapshot(
            max_distance_km=self.max_distance_km,
            identity_location_tolerance_km=self.identity_location_tolerance_km,
            max_age_seconds=self.max_age_seconds,
            max_future_skew_seconds=self.max_future_skew_seconds,
            max_response_bytes=self.max_response_bytes,
            request_deadline_seconds=self.request_deadline_seconds,
            collection_deadline_seconds=self.collection_deadline_seconds,
            **kwargs,
        )

    async def _json(self, params: dict[str, object]) -> tuple[str, object | None]:
        request_params = dict(params)
        request_params["token"] = self.token
        try:
            async with asyncio.timeout(self.request_deadline_seconds):
                async with self.http.stream(
                    "GET",
                    f"{self.base_url}{SYNOPTIC_LATEST_PATH}",
                    params=request_params,
                    headers={"Accept-Encoding": "identity"},
                ) as response:
                    if response.status_code in {401, 403}:
                        return PWS_STATUS_AUTH_ERROR, None
                    if response.status_code >= 400:
                        return PWS_STATUS_PROVIDER_ERROR, None

                    content_encoding = response.headers.get("content-encoding", "").strip().lower()
                    if content_encoding not in {"", "identity"}:
                        return PWS_STATUS_UNSUPPORTED_CONTENT_ENCODING, None
                    transfer_encoding = response.headers.get("transfer-encoding", "").strip().lower()
                    if transfer_encoding:
                        transfer_tokens = [
                            token.strip() for token in transfer_encoding.split(",") if token.strip()
                        ]
                        if any(token not in {"identity", "chunked"} for token in transfer_tokens):
                            return PWS_STATUS_UNSUPPORTED_CONTENT_ENCODING, None

                    content_length = response.headers.get("content-length")
                    if content_length not in (None, ""):
                        try:
                            declared_length = int(str(content_length).strip())
                        except ValueError:
                            return PWS_STATUS_MALFORMED_RESPONSE, None
                        if declared_length < 0:
                            return PWS_STATUS_MALFORMED_RESPONSE, None
                        if declared_length > self.max_response_bytes:
                            return PWS_STATUS_RESPONSE_TOO_LARGE, None

                    payload = bytearray()
                    if response.is_stream_consumed:
                        try:
                            buffered = response.content
                        except httpx.ResponseNotRead:
                            return PWS_STATUS_TRANSPORT_ERROR, None
                        if len(buffered) > self.max_response_bytes:
                            return PWS_STATUS_RESPONSE_TOO_LARGE, None
                        payload.extend(buffered)
                    else:
                        async for chunk in response.aiter_raw():
                            if len(payload) + len(chunk) > self.max_response_bytes:
                                return PWS_STATUS_RESPONSE_TOO_LARGE, None
                            payload.extend(chunk)
            try:
                return "OK", json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return PWS_STATUS_MALFORMED_RESPONSE, None
        except TimeoutError:
            return PWS_STATUS_TIMEOUT, None
        except httpx.HTTPError:
            return PWS_STATUS_TRANSPORT_ERROR, None

    def _request_params(self, lat: float, lon: float, unit: str) -> dict[str, object]:
        miles = self.max_distance_km * MILES_PER_KM
        within_minutes = max(1, int(math.ceil(self.max_age_seconds / 60.0)))
        units = "english,temp|F" if unit == "F" else "metric,temp|C"
        return {
            "network": SYNOPTIC_CWOP_NETWORK_ID,
            "radius": f"{lat:.6f},{lon:.6f},{miles:.3f}",
            "limit": self.max_candidates,
            "vars": "air_temp",
            "within": within_minutes,
            "status": "active",
            "units": units,
            "timeformat": "%s",
            "output": "json",
            "qc": "on",
            "qc_remove_data": "on",
            "qc_flags": "on",
            "qc_checks": SYNOPTIC_QC_CHECKS,
        }

    async def fetch_snapshot(
        self,
        *,
        latitude: float,
        longitude: float,
        unit: str,
    ) -> PWSDiagnosticSnapshot:
        lat = _latitude(latitude)
        lon = _longitude(longitude)
        normalized_unit = _unit(unit)
        if not self.token:
            return self._snapshot(
                status=PWS_STATUS_UNCONFIGURED,
                configured=False,
                latitude=lat,
                longitude=lon,
                unit=normalized_unit,
                received_at=time.time(),
            )
        try:
            async with asyncio.timeout(self.collection_deadline_seconds):
                return await self._fetch_configured(lat, lon, normalized_unit)
        except TimeoutError:
            return self._snapshot(
                status=PWS_STATUS_TIMEOUT,
                configured=True,
                latitude=lat,
                longitude=lon,
                unit=normalized_unit,
                received_at=time.time(),
            )

    async def _fetch_configured(
        self, lat: float, lon: float, normalized_unit: str
    ) -> PWSDiagnosticSnapshot:
        status, body = await self._json(self._request_params(lat, lon, normalized_unit))
        received_at = time.time()
        if status != "OK":
            return self._snapshot(
                status=status,
                configured=True,
                latitude=lat,
                longitude=lon,
                unit=normalized_unit,
                received_at=received_at,
            )
        if not isinstance(body, dict):
            return self._snapshot(
                status=PWS_STATUS_MALFORMED_RESPONSE,
                configured=True,
                latitude=lat,
                longitude=lon,
                unit=normalized_unit,
                received_at=received_at,
            )

        summary = body.get("SUMMARY")
        if not isinstance(summary, dict):
            response_status = PWS_STATUS_MALFORMED_RESPONSE
            rows: list[object] = []
        else:
            try:
                response_code = _strict_response_code(summary.get("RESPONSE_CODE"))
            except PWSError:
                response_code = -999999
            if response_code == 1:
                response_status = "OK"
            elif response_code == 2:
                response_status = PWS_STATUS_NO_FRESH_QC
            elif response_code == 200:
                response_status = PWS_STATUS_AUTH_ERROR
            elif response_code == -999999:
                response_status = PWS_STATUS_MALFORMED_RESPONSE
            else:
                response_status = PWS_STATUS_PROVIDER_ERROR
            station_rows = body.get("STATION")
            rows = station_rows if isinstance(station_rows, list) else []

        if response_status != "OK":
            return self._snapshot(
                status=response_status,
                configured=True,
                latitude=lat,
                longitude=lon,
                unit=normalized_unit,
                received_at=received_at,
            )

        units = body.get("UNITS")
        if not isinstance(units, dict):
            return self._snapshot(
                status=PWS_STATUS_MALFORMED_RESPONSE,
                configured=True,
                latitude=lat,
                longitude=lon,
                unit=normalized_unit,
                received_at=received_at,
            )
        returned_unit = str(units.get("air_temp") or "").strip().lower()
        if returned_unit not in _expected_unit_label(normalized_unit):
            return self._snapshot(
                status=PWS_STATUS_MALFORMED_RESPONSE,
                configured=True,
                latitude=lat,
                longitude=lon,
                unit=normalized_unit,
                received_at=received_at,
            )

        accepted: list[SynopticPWSObservation] = []
        attempts: list[PWSStationAttempt] = []
        seen: set[str] = set()
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            station_id = str(raw.get("STID") or "").strip().upper()
            if not station_id or station_id in seen:
                continue
            seen.add(station_id)
            try:
                station_lat = _latitude(raw.get("LATITUDE"))
                station_lon = _longitude(raw.get("LONGITUDE"))
                distance = _haversine_km(lat, lon, station_lat, station_lon)
            except PWSError as exc:
                # Invalid coordinates cannot be represented faithfully in attempt metadata.
                continue
            base_attempt = dict(
                station_id=station_id,
                discovery_latitude=station_lat,
                discovery_longitude=station_lon,
                discovery_distance_km=distance,
                observation_latitude=station_lat,
                observation_longitude=station_lon,
                identity_location_delta_km=0.0,
            )
            if str(raw.get("MNET_ID") or "").strip() != SYNOPTIC_CWOP_NETWORK_ID:
                attempts.append(PWSStationAttempt(outcome="REJECT_NETWORK", **base_attempt))
                continue
            if str(raw.get("STATUS") or "").strip().upper() != "ACTIVE":
                attempts.append(PWSStationAttempt(outcome="REJECT_INACTIVE", **base_attempt))
                continue
            if _truthy_restricted(raw.get("RESTRICTED")):
                attempts.append(PWSStationAttempt(outcome="REJECT_RESTRICTED", **base_attempt))
                continue
            if distance > self.max_distance_km:
                attempts.append(PWSStationAttempt(outcome="PWS_OBSERVATION_DISTANCE_EXCEEDED", **base_attempt))
                continue

            selected = _pick_temperature_observation(raw.get("OBSERVATIONS"))
            if selected is None:
                attempts.append(PWSStationAttempt(outcome="REJECT_TEMPERATURE_MISSING_OR_AMBIGUOUS", **base_attempt))
                continue
            sensor_id, observation = selected
            try:
                observed_at = _epoch(observation.get("date_time"))
                temperature = _finite(observation.get("value"), "PWS_TEMPERATURE_INVALID")
                age = received_at - observed_at
                if age > self.max_age_seconds:
                    raise PWSError("PWS_OBSERVATION_STALE")
                if age < -self.max_future_skew_seconds:
                    raise PWSError("PWS_OBSERVATION_FROM_FUTURE")
            except PWSError as exc:
                attempts.append(PWSStationAttempt(outcome=exc.code, **base_attempt))
                continue

            attempts.append(PWSStationAttempt(outcome="ACCEPTED", **base_attempt))
            accepted.append(
                SynopticPWSObservation(
                    station_id=station_id,
                    discovery_latitude=station_lat,
                    discovery_longitude=station_lon,
                    observation_latitude=station_lat,
                    observation_longitude=station_lon,
                    requested_distance_km=distance,
                    identity_location_delta_km=0.0,
                    observed_at=observed_at,
                    provider_epoch_text=str(observation.get("date_time")),
                    provider_obs_time_utc=None,
                    received_at=received_at,
                    unit=normalized_unit,
                    temperature=temperature,
                    qc_status=1,
                    provider_sensor_id=sensor_id,
                )
            )

        final_received_at = time.time()
        still_fresh: list[SynopticPWSObservation] = []
        for observation in accepted:
            age = final_received_at - observation.observed_at
            if age > self.max_age_seconds or age < -self.max_future_skew_seconds:
                attempts.append(
                    PWSStationAttempt(
                        station_id=observation.station_id,
                        discovery_latitude=observation.discovery_latitude,
                        discovery_longitude=observation.discovery_longitude,
                        discovery_distance_km=observation.requested_distance_km,
                        outcome="REJECT_FINAL_TEMPORAL_WINDOW",
                        observation_latitude=observation.observation_latitude,
                        observation_longitude=observation.observation_longitude,
                        identity_location_delta_km=0.0,
                    )
                )
                continue
            still_fresh.append(observation)

        still_fresh.sort(key=lambda row: (row.requested_distance_km, row.station_id))
        still_fresh = still_fresh[: self.max_accepted]
        final_status = PWS_STATUS_AVAILABLE if still_fresh else PWS_STATUS_NO_FRESH_QC
        return self._snapshot(
            status=final_status,
            configured=True,
            latitude=lat,
            longitude=lon,
            unit=normalized_unit,
            received_at=final_received_at,
            observations=tuple(still_fresh),
            attempts=tuple(attempts),
        )
