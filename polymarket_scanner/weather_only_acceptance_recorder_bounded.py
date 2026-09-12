from __future__ import annotations

"""Bounded-source W7 recorder adapter.

This module is a narrow corrective layer over the frozen W7 recorder.  It leaves the
frozen acceptance policy, sampling cadence, event certification, containment checks,
and source-finality semantics unchanged.  Its only purpose is to keep a temporarily
unreachable WRH transport from stretching a nominal 45-minute acceptance run by tens
of minutes.

The underlying WRH client already has per-request timeouts, but one source snapshot
performs several sequential HTTP requests and the frozen recorder retries transport
errors.  On an IPv6-only host with an IPv4-only backend those bounded waits can add up
far beyond one 30-second sample period.  Here the whole source-poll operation receives
an additional wall-clock budget.  Exceeding it is fail-closed: the sample is marked
unhealthy by the base recorder and no source latency evidence is fabricated.
"""

import asyncio

from .weather_only_acceptance_recorder import (
    SAMPLE_INTERVAL_SECONDS,
    WeatherW7RecorderSession,
)
from .weather_only_wrh_client import NWSWRHLiveClient


WEATHER_W7_BOUNDED_RECORDER_VERSION = "weather_w7_bounded_source_poll_v1"
W7_WRH_REQUEST_TIMEOUT_SECONDS = 5.0
W7_SOURCE_POLL_WALL_CLOCK_BUDGET_SECONDS = 12.0
W7_SOURCE_POLL_TIMEOUT_CODE = "W7_RECORDER_SOURCE_POLL_WALL_CLOCK_TIMEOUT"

if W7_SOURCE_POLL_WALL_CLOCK_BUDGET_SECONDS >= SAMPLE_INTERVAL_SECONDS:
    raise RuntimeError("W7 bounded source-poll budget must remain below sample interval")


class WeatherW7BoundedRecorderSession(WeatherW7RecorderSession):
    """Frozen W7 recorder with only an outer wall-clock bound around source polling."""

    def __init__(self, *args, wrh=None, **kwargs) -> None:
        # Keep injected test clients untouched.  Production W7 uses a shorter request
        # timeout than the generic live WRH client so abandoned worker threads also
        # finish promptly after an outer asyncio timeout.
        source_client = wrh or NWSWRHLiveClient(timeout_seconds=W7_WRH_REQUEST_TIMEOUT_SECONDS)
        super().__init__(*args, wrh=source_client, **kwargs)

    async def _poll_source_update(self):
        try:
            return await asyncio.wait_for(
                super()._poll_source_update(),
                timeout=W7_SOURCE_POLL_WALL_CLOCK_BUDGET_SECONDS,
            )
        except asyncio.TimeoutError:
            self.source_poll_failure_codes.append(W7_SOURCE_POLL_TIMEOUT_CODE)
            return None, False
