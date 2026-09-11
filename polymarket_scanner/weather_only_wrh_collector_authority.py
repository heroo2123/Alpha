from __future__ import annotations

"""Trusted-clock authority façade for the prospective WRH collector.

``WeatherWRHProspectiveCollector`` deliberately retains explicit timestamp overrides
and raw persistence inspection as deterministic lower-level engineering primitives.
Production calibration collection must enter through this module instead: callers
may submit a digest-validated prospective capture, but they cannot choose registration
time, tick time, WRH snapshot receipt time, or read persisted authorized JSON as if it
were independently trusted evidence.

The clock is injected once at construction only so tests can be deterministic. In
production the default is ``time.time``. Every observed clock value is validated and
clock regression fails closed for the lifetime of the process.
"""

import math
import time
from pathlib import Path
from typing import Callable

from .weather_only_calibration_capture import ProspectiveWeatherCalibrationCapture
from .weather_only_wrh_client import NWSWRHLiveClient
from .weather_only_wrh_collector import (
    CollectorTickReport,
    WeatherWRHCollectorError,
    WeatherWRHProspectiveCollector,
)


TRUSTED_WRH_COLLECTOR_AUTHORITY_VERSION = "weather_wrh_collector_authority_v2_owned_clock_no_raw_authorized_reads"


class TrustedWeatherWRHCollectorClockError(WeatherWRHCollectorError):
    pass


class TrustedWeatherWRHProspectiveCollector:
    """Production-safe façade whose timing and evidence authority are not caller-settable."""

    financial_authority = False
    financial_delivery = False
    automatic_order_placement = False

    def __init__(
        self,
        *,
        db_path: str | Path,
        client: NWSWRHLiveClient | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")
        self._clock: Callable[[], float] = clock or time.time
        self._last_clock_value: float | None = None
        self._collector = WeatherWRHProspectiveCollector(db_path=db_path, client=client)

    @property
    def authority_version(self) -> str:
        return TRUSTED_WRH_COLLECTOR_AUTHORITY_VERSION

    def _trusted_now(self) -> float:
        raw = self._clock()
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise TrustedWeatherWRHCollectorClockError("TRUSTED_COLLECTOR_CLOCK_INVALID")
        value = float(raw)
        if not math.isfinite(value) or value < 0.0:
            raise TrustedWeatherWRHCollectorClockError("TRUSTED_COLLECTOR_CLOCK_INVALID")
        previous = self._last_clock_value
        if previous is not None and value < previous:
            raise TrustedWeatherWRHCollectorClockError("TRUSTED_COLLECTOR_CLOCK_REGRESSION")
        self._last_clock_value = value
        return value

    def register_capture(self, capture: ProspectiveWeatherCalibrationCapture) -> str:
        """Register one prospective capture using only the collector-owned wall clock."""
        return self._collector.register_capture(capture, registered_at=self._trusted_now())

    def tick(self) -> CollectorTickReport:
        """Run one collection iteration using only the collector-owned wall clock."""
        report = self._collector.tick(now=self._trusted_now())
        if (
            report.financial_authority
            or report.financial_delivery
            or report.automatic_order_placement
        ):
            raise WeatherWRHCollectorError("TRUSTED_COLLECTOR_AUTHORITY_BOUNDARY_BROKEN")
        return report

    def diagnostic_status(self) -> list[dict]:
        """Expose only non-authoritative status metadata from persistence.

        Persisted settlement/authorized JSON is intentionally reduced to presence
        booleans here. A future calibration sample reader must rehydrate the frozen
        capture + snapshots and recompute the strict authority gate; it must not trust
        serialized ``authorized_json`` directly.
        """
        result: list[dict] = []
        for row in self._collector.records():
            result.append({
                "capture_evidence_sha256": row["capture_evidence_sha256"],
                "station": row["station"],
                "target_date": row["target_date"],
                "family": row["family"],
                "registered_at": row["registered_at"],
                "status": row["status"],
                "failure_code": row["failure_code"],
                "settlement_evidence_present": "settlement_evidence" in row,
                "authorized_evidence_present": "authorized" in row,
                "updated_at": row["updated_at"],
                "financial_authority": False,
            })
        return result

    def close(self) -> None:
        self._collector.close()

    def __enter__(self) -> "TrustedWeatherWRHProspectiveCollector":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
