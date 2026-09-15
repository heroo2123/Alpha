from __future__ import annotations

"""Second independent-review corrective boundary for V5 PAPER accounting.

This layer closes two residual integrity gaps without adding any order, wallet,
signing or financial authority:

* every executable leg carries its own visible top-of-book quantity and aggregate
  capacity is recomputed as the minimum across legs; and
* the complete strong-identity precheck plus parent BEGIN IMMEDIATE admission is
  serialized with an OS file lock, so a conflicting concurrent retry cannot pass a
  stale outer check and then hit the parent's weaker existing-position fast path.

The lock is deliberately external to SQLite. It covers the entire two-stage inherited
validation/admission sequence across threads and processes on the single host.
"""

import fcntl
import os
from pathlib import Path

from .weather_only_independent_review_corrective import (
    IndependentReviewPostReceiptStore,
    _canonical_sha,
    _finite,
    _same,
)
from .weather_only_paper_positions import WeatherPaperPositionError


INDEPENDENT_REVIEW_CORRECTIVE_V2_VERSION = (
    "weather_all_paper_independent_review_corrective_v2_capacity_serialized_admission"
)


class IndependentReviewPostReceiptStoreV2(IndependentReviewPostReceiptStore):
    """Recompute capacity from exact legs and serialize full V5 admission identity."""

    @staticmethod
    def _normalized_execution(execution: dict) -> dict:
        normalized = IndependentReviewPostReceiptStore._normalized_execution(execution)
        raw_legs = execution.get("legs") if isinstance(execution, dict) else None
        if not isinstance(raw_legs, list) or len(raw_legs) != len(normalized["legs"]):
            raise WeatherPaperPositionError("V5_EXECUTION_LEGS_INVALID")

        visible_by_leg: list[float] = []
        upgraded_legs: list[dict] = []
        for raw, leg in zip(raw_legs, normalized["legs"]):
            if not isinstance(raw, dict):
                raise WeatherPaperPositionError("V5_EXECUTION_LEG_INVALID")
            visible = _finite(
                raw.get("visible_units"), "V5_EXECUTION_LEG_VISIBLE_UNITS_INVALID"
            )
            if visible <= 0.0:
                raise WeatherPaperPositionError("V5_EXECUTION_LEG_VISIBLE_UNITS_INVALID")
            upgraded = dict(leg)
            upgraded["visible_units"] = visible
            upgraded_legs.append(upgraded)
            visible_by_leg.append(visible)

        derived_visible = min(visible_by_leg)
        claimed_visible = _finite(
            execution.get("visible_units"), "V5_VISIBLE_UNITS_INVALID"
        )
        if not _same(claimed_visible, derived_visible):
            raise WeatherPaperPositionError("V5_VISIBLE_CAPACITY_LEG_MISMATCH")

        upgraded = dict(normalized)
        upgraded["visible_units"] = derived_visible
        upgraded["legs"] = upgraded_legs
        return upgraded

    @staticmethod
    def _execution_identity(execution: dict) -> str:
        return _canonical_sha(IndependentReviewPostReceiptStoreV2._normalized_execution(execution))

    def _admission_lock_path(self) -> Path:
        return self.path.with_name(self.path.name + ".v5-admission.lock")

    def admit_post_receipt_position(
        self, signal_id: int, target_stake_usd: float, execution: dict
    ) -> dict:
        lock_path = self._admission_lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            # Re-normalize only after acquiring the cross-process lock.  The inherited
            # strong signal/execution identity checks and the parent's SQLite
            # BEGIN IMMEDIATE transaction now run as one serialized admission unit.
            normalized = self._normalized_execution(execution)
            return super().admit_post_receipt_position(
                signal_id, target_stake_usd, normalized
            )
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
