from __future__ import annotations

"""Trusted-clock authority façade for the prospective WRH collector.

``WeatherWRHProspectiveCollector`` deliberately retains explicit timestamp overrides
as a deterministic lower-level test primitive.  Production calibration collection
must enter through this module instead: callers may submit a digest-validated
prospective capture, but they cannot choose registration time, tick time, the WRH
snapshot receipt time, or any financial/trading authority flag.

The clock is injected once at construction only so tests can be deterministic.  In
production the default is ``time.time``.  Every observed clock value is validated and
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


TRUSTED_WRH_COLLECTOR_AUTHORITY_VERSION = "weather_wrh_collector_authority_v1_owned_wall_clock"


class TrustedWeatherWRHCollectorClockError(WeatherWRHCollectorError):
    pass


class TrustedWeatherWRHProspectiveCollector:
    """Production-safe façade whose registration/tick timestamps are not caller-settable."""

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

    def records(self) -> list[dict]:
        """Return redacted/digest-bound research records; never raw backend payloads or tokens."""
        return self._collector.records()

    def close(self) -> None:
        self._collector.close()

    def __enter__(self) -> "TrustedWeatherWRHProspectiveCollector":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
