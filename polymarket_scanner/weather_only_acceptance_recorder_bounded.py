from __future__ import annotations

"""Bounded-source W7 recorder adapter.

This module is a narrow corrective layer over the frozen W7 recorder. It leaves the
frozen acceptance thresholds, event certification, containment checks and source-
finality semantics unchanged.

Two observer-only corrections live here:

* the WRH transport is persistent and has a bounded whole-poll deadline, so temporary
  source/network stalls cannot stretch one nominal sample indefinitely;
* the 45-minute schedule is anchored *after* the first completed sample. The frozen
  evaluator measures duration from the first sample timestamp to the last sample
  timestamp, so anchoring before sample zero can under-measure a true 45-minute
  schedule by the work time of sample zero.

A source poll that still exceeds its bound remains fail-closed: the corresponding
sample is unhealthy and no source-latency evidence is fabricated.
"""

import asyncio
import time

import httpx

from .weather_only_acceptance_recorder import (
    SAMPLE_COUNT,
    SAMPLE_INTERVAL_SECONDS,
    WeatherW7RecorderSession,
)
from .weather_only_wrh_client import NWSWRHLiveClient


WEATHER_W7_BOUNDED_RECORDER_VERSION = "weather_w7_bounded_source_poll_v2_persistent_transport_duration_guard"
W7_WRH_REQUEST_TIMEOUT_SECONDS = 3.0
W7_SOURCE_POLL_WALL_CLOCK_BUDGET_SECONDS = 20.0
W7_SOURCE_POLL_TIMEOUT_CODE = "W7_RECORDER_SOURCE_POLL_WALL_CLOCK_TIMEOUT"
W7_WRH_MAX_CONNECTIONS = 4
W7_WRH_KEEPALIVE_EXPIRY_SECONDS = 120.0

if W7_SOURCE_POLL_WALL_CLOCK_BUDGET_SECONDS >= SAMPLE_INTERVAL_SECONDS:
    raise RuntimeError("W7 bounded source-poll budget must remain below sample interval")


class WeatherW7BoundedRecorderSession(WeatherW7RecorderSession):
    """Frozen W7 recorder with bounded persistent source transport and exact cadence."""

    def __init__(self, *args, wrh=None, **kwargs) -> None:
        self._owned_wrh_http: httpx.Client | None = None
        if wrh is None:
            # Source polling performs four sequential HTTPS reads. Reusing one client
            # removes repeated DNS/TCP/TLS setup and materially reduces e2-micro tail
            # latency without changing source semantics or authority.
            self._owned_wrh_http = httpx.Client(
                headers={
                    "User-Agent": "polymarket-weather-only-w7/2.0 (+https://github.com/heroo2123/Alpha)",
                    "Accept": "*/*",
                },
                timeout=W7_WRH_REQUEST_TIMEOUT_SECONDS,
                follow_redirects=True,
                limits=httpx.Limits(
                    max_connections=W7_WRH_MAX_CONNECTIONS,
                    max_keepalive_connections=W7_WRH_MAX_CONNECTIONS,
                    keepalive_expiry=W7_WRH_KEEPALIVE_EXPIRY_SECONDS,
                ),
            )
            source_client = NWSWRHLiveClient(
                http_client=self._owned_wrh_http,
                timeout_seconds=W7_WRH_REQUEST_TIMEOUT_SECONDS,
            )
        else:
            source_client = wrh
        super().__init__(*args, wrh=source_client, **kwargs)

    async def close(self) -> None:
        try:
            await super().close()
        finally:
            if self._owned_wrh_http is not None:
                owned = self._owned_wrh_http
                self._owned_wrh_http = None
                await asyncio.to_thread(owned.close)

    async def _poll_source_update(self):
        try:
            return await asyncio.wait_for(
                super()._poll_source_update(),
                timeout=W7_SOURCE_POLL_WALL_CLOCK_BUDGET_SECONDS,
            )
        except asyncio.TimeoutError:
            self.source_poll_failure_codes.append(W7_SOURCE_POLL_TIMEOUT_CODE)
            return None, False

    async def run_frozen_window(self):
        # The evaluator measures samples[-1].observed_at - samples[0].observed_at.
        # Complete sample zero first, then schedule 90 exact 30-second intervals.
        # This preserves the 30-second cadence and guarantees the measured window is
        # not shortened by sample-zero work, without altering the 2700-second policy.
        await self.record_sample()
        schedule_origin = time.monotonic()
        for index in range(1, SAMPLE_COUNT):
            target = schedule_origin + index * SAMPLE_INTERVAL_SECONDS
            await asyncio.sleep(max(0.0, target - time.monotonic()))
            await self.record_sample()
        return self.finalize()
