from __future__ import annotations

"""Trusted-clock authority façade for the prospective WRH collector.

``WeatherWRHProspectiveCollector`` deliberately retains explicit timestamp overrides
and raw persistence inspection as deterministic lower-level engineering primitives.
Production calibration collection must enter through this module instead: callers
may submit a digest-validated prospective capture, but they cannot choose registration
time, tick time, WRH snapshot receipt time, or read persisted authorized JSON as if it
were independently trusted evidence.

The trusted façade also performs a digest/structure revalidation pass over every
pending persisted capture and snapshot before each collection tick. Corruption is
made terminal for the affected capture or station/date rather than becoming a retry
loop or a source of reconstructed authority. Persisted nested prediction evidence and
SQLite metadata columns are re-bound to reconstructed objects instead of trusting
stored digest strings in isolation.

Live collection attempts are additionally throttled at the trusted boundary.  The
lower deterministic collector records source receipt time only after successful
fetches; without this wrapper a caller could otherwise hammer WRH after transport
failures.  One trusted process therefore performs at most one lower collection attempt
per frozen polling interval, regardless of success or failure.  A process restart may
permit one immediate attempt; it cannot manufacture evidence or bypass finality gates.
"""

import json
import math
import time
from datetime import date
from pathlib import Path
from typing import Callable

from .weather_only_calibration_capture import ProspectiveWeatherCalibrationCapture
from .weather_only_predictions import WeatherPredictionError, _validate_prediction
from .weather_only_wrh_client import NWSWRHLiveClient
from .weather_only_wrh_collector import (
    CAPTURE_FAILED,
    CAPTURE_PENDING,
    POLL_INTERVAL_SECONDS,
    CollectorTickReport,
    WeatherWRHCollectorError,
    WeatherWRHProspectiveCollector,
)


TRUSTED_WRH_COLLECTOR_AUTHORITY_VERSION = "weather_wrh_collector_authority_v5_nested_integrity_attempt_throttle"


class TrustedWeatherWRHCollectorClockError(WeatherWRHCollectorError):
    pass


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


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
        self._last_collection_attempt_at: float | None = None
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

    def _fail_pending_key_text(self, station: str, target_text: str, code: str, now: float) -> int:
        """Terminally contain a key even when its persisted date text is malformed."""
        store = self._collector.store
        rows = store.db.execute(
            """
            SELECT capture_evidence_sha256 FROM wrh_collector_captures
            WHERE station = ? AND target_date = ? AND status = ?
            """,
            (station, target_text, CAPTURE_PENDING),
        ).fetchall()
        with store.db:
            store.db.execute(
                """
                UPDATE wrh_collector_captures
                SET status = ?, failure_code = ?, updated_at = ?
                WHERE station = ? AND target_date = ? AND status = ?
                """,
                (CAPTURE_FAILED, code, now, station, target_text, CAPTURE_PENDING),
            )
            store.db.execute(
                """
                UPDATE wrh_collector_keys
                SET status = 'COMPLETE', failure_code = ?, updated_at = ?
                WHERE station = ? AND target_date = ?
                """,
                (code, now, station, target_text),
            )
        return len(rows)

    def _validate_capture_row(self, row) -> None:
        """Re-bind SQLite capture metadata plus nested prediction digest to payload bytes."""
        store = self._collector.store
        capture = store.load_capture(row)
        try:
            _validate_prediction(capture.prediction)
        except WeatherPredictionError as exc:
            raise WeatherWRHCollectorError(f"PERSISTED_PREDICTION:{exc.code}") from exc

        if str(row["capture_evidence_sha256"]) != capture.capture_evidence_sha256:
            raise WeatherWRHCollectorError("PERSISTED_CAPTURE_DIGEST_COLUMN_MISMATCH")
        if str(row["station"]) != capture.rule_evidence.station:
            raise WeatherWRHCollectorError("PERSISTED_CAPTURE_STATION_COLUMN_MISMATCH")
        if str(row["target_date"]) != capture.rule_evidence.target_date.isoformat():
            raise WeatherWRHCollectorError("PERSISTED_CAPTURE_TARGET_DATE_COLUMN_MISMATCH")
        if str(row["family"]) != capture.rule_evidence.family:
            raise WeatherWRHCollectorError("PERSISTED_CAPTURE_FAMILY_COLUMN_MISMATCH")
        if str(row["capture_json"]) != _canonical_json(capture.as_dict()):
            raise WeatherWRHCollectorError("PERSISTED_CAPTURE_CANONICAL_PAYLOAD_MISMATCH")

    def _validate_snapshot_rows(self, station: str, target: date) -> None:
        """Re-bind every persisted WRH snapshot to its SQLite key/digest/receipt columns."""
        store = self._collector.store
        rows = list(store.db.execute(
            """
            SELECT station, target_date, evidence_sha256, received_at, snapshot_json
            FROM wrh_collector_snapshots
            WHERE station = ? AND target_date = ?
            ORDER BY received_at ASC, evidence_sha256 ASC
            """,
            (station, target.isoformat()),
        ).fetchall())
        snapshots = store.load_snapshots(station, target)
        if len(rows) != len(snapshots):
            raise WeatherWRHCollectorError("PERSISTED_SNAPSHOT_ROW_COUNT_MISMATCH")
        for row, snapshot in zip(rows, snapshots, strict=True):
            if str(row["station"]) != snapshot.station:
                raise WeatherWRHCollectorError("PERSISTED_SNAPSHOT_STATION_COLUMN_MISMATCH")
            if str(row["target_date"]) != snapshot.target_date.isoformat():
                raise WeatherWRHCollectorError("PERSISTED_SNAPSHOT_TARGET_DATE_COLUMN_MISMATCH")
            if str(row["evidence_sha256"]) != snapshot.evidence_sha256:
                raise WeatherWRHCollectorError("PERSISTED_SNAPSHOT_DIGEST_COLUMN_MISMATCH")
            if float(row["received_at"]) != float(snapshot.received_at):
                raise WeatherWRHCollectorError("PERSISTED_SNAPSHOT_RECEIPT_COLUMN_MISMATCH")
            if str(row["snapshot_json"]) != _canonical_json(snapshot.as_dict()):
                raise WeatherWRHCollectorError("PERSISTED_SNAPSHOT_CANONICAL_PAYLOAD_MISMATCH")

    def _integrity_preflight(self, now: float) -> int:
        """Revalidate all pending persisted lineage before live collection can advance."""
        store = self._collector.store
        failed = 0
        for key in list(store.pending_keys()):
            station = str(key["station"])
            target_text = str(key["target_date"])
            try:
                target = date.fromisoformat(target_text)
            except ValueError:
                failed += self._fail_pending_key_text(
                    station,
                    target_text,
                    "INTEGRITY_KEY_TARGET_DATE_INVALID",
                    now,
                )
                continue

            for row in list(store.pending_capture_rows(station, target)):
                digest = str(row["capture_evidence_sha256"])
                try:
                    self._validate_capture_row(row)
                except WeatherWRHCollectorError as exc:
                    store.mark_failed(digest, f"INTEGRITY_CAPTURE:{exc.code}", now)
                    failed += 1

            if not store.pending_capture_rows(station, target):
                store.complete_key_if_terminal(station, target, now)
                continue

            try:
                self._validate_snapshot_rows(station, target)
            except WeatherWRHCollectorError as exc:
                failed += self._collector._fail_pending_key(
                    station,
                    target,
                    f"INTEGRITY_SNAPSHOT:{exc.code}",
                    now,
                )
        return failed

    def _fail_all_pending(self, code: str, now: float) -> int:
        """Last-resort fail-closed containment for an integrity error racing a tick."""
        failed = 0
        store = self._collector.store
        for key in list(store.pending_keys()):
            failed += self._fail_pending_key_text(
                str(key["station"]),
                str(key["target_date"]),
                code,
                now,
            )
        return failed

    def register_capture(self, capture: ProspectiveWeatherCalibrationCapture) -> str:
        """Register one prospective capture using only the collector-owned wall clock."""
        return self._collector.register_capture(capture, registered_at=self._trusted_now())

    def _throttled_report(self, *, integrity_failed: int) -> CollectorTickReport:
        pending_key_count = len(self._collector.store.pending_keys())
        return CollectorTickReport(
            collector_version="trusted_wrapper_attempt_throttled",
            policy_id=self.authority_version,
            evaluated_station_dates=0,
            fetched_snapshots=0,
            authorized_captures=0,
            failed_captures=integrity_failed,
            deferred_station_dates=pending_key_count,
            fetch_errors=(),
            financial_authority=False,
            financial_delivery=False,
            automatic_order_placement=False,
        )

    def tick(self) -> CollectorTickReport:
        """Run one revalidated, attempt-throttled collection iteration."""
        now = self._trusted_now()
        integrity_failed = self._integrity_preflight(now)

        previous_attempt = self._last_collection_attempt_at
        if previous_attempt is not None and now - previous_attempt < POLL_INTERVAL_SECONDS:
            return self._throttled_report(integrity_failed=integrity_failed)

        # Mark before entering the lower collector so transport/parser failures are
        # throttled too.  A failed attempt is still an upstream request attempt.
        self._last_collection_attempt_at = now
        try:
            report = self._collector.tick(now=now)
        except WeatherWRHCollectorError as exc:
            # A concurrent persistence mutation between preflight and use must not
            # create a crash/retry loop that later gets interpreted as missing data.
            raced_failed = self._fail_all_pending(
                f"INTEGRITY_RUNTIME:{exc.code}",
                now,
            )
            return CollectorTickReport(
                collector_version="trusted_wrapper_fail_closed",
                policy_id=self.authority_version,
                evaluated_station_dates=0,
                fetched_snapshots=0,
                authorized_captures=0,
                failed_captures=integrity_failed + raced_failed,
                deferred_station_dates=0,
                fetch_errors=(f"INTEGRITY_RUNTIME:{exc.code}",),
                financial_authority=False,
                financial_delivery=False,
                automatic_order_placement=False,
            )
        if (
            report.financial_authority
            or report.financial_delivery
            or report.automatic_order_placement
        ):
            raise WeatherWRHCollectorError("TRUSTED_COLLECTOR_AUTHORITY_BOUNDARY_BROKEN")
        if integrity_failed == 0:
            return report
        return CollectorTickReport(
            collector_version=report.collector_version,
            policy_id=report.policy_id,
            evaluated_station_dates=report.evaluated_station_dates,
            fetched_snapshots=report.fetched_snapshots,
            authorized_captures=report.authorized_captures,
            failed_captures=report.failed_captures + integrity_failed,
            deferred_station_dates=report.deferred_station_dates,
            fetch_errors=report.fetch_errors,
            financial_authority=False,
            financial_delivery=False,
            automatic_order_placement=False,
        )

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
