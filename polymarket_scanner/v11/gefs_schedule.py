"""Bounded request selection for an unchanged event's GEFS source plan.

A scheduled initialization is a request, never evidence of provider publication
or availability. Only subsequently received, matching GRIB bytes prove a run.
"""
from dataclasses import dataclass, replace
import math

from .evidence import EvidenceError, finite, identity
from .gefs_sources import GEFSPlan


@dataclass(frozen=True)
class GEFSRunPolicy:
    version: str
    request_lag_seconds: float

    def __post_init__(self):
        identity(self.version)
        if not 3600 <= finite(self.request_lag_seconds) <= 21600:
            raise EvidenceError('GEFS_REQUEST_LAG_BOUND')


def requested_plan(seed, policy, *, now, previous_initialization=None):
    if not isinstance(seed, GEFSPlan) or not isinstance(policy, GEFSRunPolicy):
        raise EvidenceError('GEFS_RUN_SCHEDULE_TYPED_PLAN_REQUIRED')
    now=finite(now)
    if now >= seed.window[1]:
        raise EvidenceError('GEFS_TARGET_WINDOW_ENDED')
    # A post-midnight run cannot forecast the entire local day. Do not mix an
    # observed prefix into this separately calibrated linear-day model contract.
    latest=min(math.floor((now-policy.request_lag_seconds)/21600)*21600,
               math.floor(seed.window[0]/21600)*21600)
    previous=seed.initialized_at if previous_initialization is None else finite(previous_initialization)
    if previous < seed.initialized_at:
        raise EvidenceError('GEFS_RUN_HISTORY_BEFORE_SEED')
    # Validate recovered state even when a newer request would otherwise hide
    # its invalid timestamp or unsupported coverage.
    replace(seed,initialized_at=previous)
    initialized=max(seed.initialized_at,previous,latest)
    plan=replace(seed,initialized_at=initialized)
    if not 0 <= now-initialized < plan.maximum_run_age_seconds:
        raise EvidenceError('GEFS_RUN_PLAN_STALE_OR_FUTURE')
    return plan
