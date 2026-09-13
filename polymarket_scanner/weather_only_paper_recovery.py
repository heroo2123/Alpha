from __future__ import annotations

"""Crash-safe paper delivery admission and restart reconciliation.

A Telegram attempt is an economic experiment event even when no real order exists.
This store therefore reserves one station/day *before* a message can be sent, keeps an
uncertain reservation after a crash, and never treats an abandoned in-flight attempt
as if nothing happened. Settlement-message and interrupted-accounting uncertainty are
tracked separately.
"""

import json
import math
import time

from .weather_only_paper_corrective import PAPER_EXECUTION_PROTOCOL_V4
from .weather_only_paper_facade import CorrectiveWeatherPaperStore
from .weather_only_paper_positions import WeatherPaperPositionError, _json, _payload


RECOVERY_VERSION = "weather_paper_crash_recovery_v2_partial_fill_guard"
_ACTIVE_RESERVATION_STATES = {"PENDING", "ACKNOWLEDGED", "UNCERTAIN"}


class CrashSafeWeatherPaperStore(CorrectiveWeatherPaperStore):
    def __init__(self, path) -> None:
        super().__init__(path)
        self._migrate_recovery()

    def _migrate_recovery(self) -> None:
        with self._conn() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS weather_paper_station_day_reservations (
                    station TEXT NOT NULL,
                    target_date TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    signal_id INTEGER,
                    state TEXT NOT NULL,
                    reason TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(station,target_date),
                    UNIQUE(decision_id)
                );
                CREATE INDEX IF NOT EXISTS idx_weather_paper_reservation_state
                    ON weather_paper_station_day_reservations(state,updated_at);
                """
            )

    @staticmethod
    def _reservation_identity(payload: dict) -> tuple[str, str, str] | None:
        if payload.get("paper_execution_protocol_version") != PAPER_EXECUTION_PROTOCOL_V4:
            return None
        station = str(payload.get("station") or "").strip().upper()
        target_date = str(payload.get("target_date") or "").strip()
        decision_id = str(payload.get("decision_id") or "").strip()
        if not station or not target_date or not decision_id:
            raise WeatherPaperPositionError("V4_STATION_DAY_RESERVATION_IDENTITY_MISSING")
        return station, target_date, decision_id

    def _reserve_before_signal(self, payload: dict) -> tuple[bool, bool]:
        """Return (allowed, newly_created)."""
        identity = self._reservation_identity(payload)
        if identity is None:
            return True, False
        station, target_date, decision_id = identity
        now = time.time()
        with self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM weather_paper_station_day_reservations "
                "WHERE station=? AND target_date=?",
                (station, target_date),
            ).fetchone()
            if row is not None:
                state = str(row["state"] or "")
                if state in _ACTIVE_RESERVATION_STATES:
                    # Same immutable decision may reach signal fingerprint
                    # deduplication. A different decision may not send for this day.
                    return str(row["decision_id"] or "") == decision_id, False
                db.execute(
                    "DELETE FROM weather_paper_station_day_reservations "
                    "WHERE station=? AND target_date=?",
                    (station, target_date),
                )
            db.execute(
                """
                INSERT INTO weather_paper_station_day_reservations(
                    station,target_date,decision_id,signal_id,state,reason,created_at,updated_at
                ) VALUES(?,?,?,NULL,'PENDING',NULL,?,?)
                """,
                (station, target_date, decision_id, now, now),
            )
        return True, True

    def _release_new_reservation(self, payload: dict, reason: str) -> None:
        identity = self._reservation_identity(payload)
        if identity is None:
            return
        station, target_date, decision_id = identity
        with self._conn() as db:
            db.execute(
                """
                UPDATE weather_paper_station_day_reservations
                SET state='RELEASED',reason=?,updated_at=?
                WHERE station=? AND target_date=? AND decision_id=? AND signal_id IS NULL
                """,
                (str(reason), time.time(), station, target_date, decision_id),
            )

    def _link_reservation(self, payload: dict, signal_id: int) -> None:
        identity = self._reservation_identity(payload)
        if identity is None:
            return
        station, target_date, decision_id = identity
        with self._conn() as db:
            cur = db.execute(
                """
                UPDATE weather_paper_station_day_reservations
                SET signal_id=?,updated_at=?
                WHERE station=? AND target_date=? AND decision_id=?
                """,
                (int(signal_id), time.time(), station, target_date, decision_id),
            )
            if cur.rowcount != 1:
                raise WeatherPaperPositionError("V4_STATION_DAY_RESERVATION_LOST")

    def save_signal(self, **kwargs):
        payload = kwargs.get("payload")
        if not isinstance(payload, dict):
            return super().save_signal(**kwargs)
        allowed, newly_created = self._reserve_before_signal(payload)
        if not allowed:
            return None
        try:
            signal_id = super().save_signal(**kwargs)
        except BaseException:
            if newly_created:
                self._release_new_reservation(payload, "SIGNAL_PERSIST_FAILED_BEFORE_SEND")
            raise
        if signal_id is not None:
            self._link_reservation(payload, int(signal_id))
        elif newly_created:
            # Fingerprint duplicate: no network send began, so a brand-new reservation
            # must not remain stuck merely because save_signal returned None.
            self._release_new_reservation(payload, "SIGNAL_FINGERPRINT_DUPLICATE")
        return signal_id

    def _set_reservation_state_for_signal(
        self, signal_id: int, state: str, reason: str | None = None
    ) -> None:
        with self._conn() as db:
            db.execute(
                """
                UPDATE weather_paper_station_day_reservations
                SET state=?,reason=?,updated_at=?
                WHERE signal_id=?
                """,
                (str(state), reason, time.time(), int(signal_id)),
            )

    def sync_reservation_for_station_day(self, station: str, target_date: str) -> None:
        station_id = str(station or "").strip().upper()
        day = str(target_date or "").strip()
        if not station_id or not day:
            return
        with self._conn() as db:
            row = db.execute(
                """
                SELECT r.signal_id,s.status,s.telegram_message_id
                FROM weather_paper_station_day_reservations r
                LEFT JOIN weather_paper_signals s ON s.id=r.signal_id
                WHERE r.station=? AND r.target_date=?
                """,
                (station_id, day),
            ).fetchone()
        if row is None or row["signal_id"] is None:
            return
        signal_id = int(row["signal_id"])
        status = str(row["status"] or "")
        receipt = row["telegram_message_id"]
        if status in {"DELIVERY_UNCERTAIN", "PAPER_ACCOUNTING_UNCERTAIN"}:
            self._set_reservation_state_for_signal(signal_id, "UNCERTAIN", status)
        elif receipt is not None or status in {"ACKNOWLEDGED", "PAPER_ACCOUNTING_ERROR"}:
            self._set_reservation_state_for_signal(signal_id, "ACKNOWLEDGED", status)
        elif status in {"DELIVERY_FAILED", "EXPIRED", "ABANDONED_PRE_SEND"}:
            self._set_reservation_state_for_signal(signal_id, "RELEASED", status)

    def _restore_original_visible_capacity(self, signal_id: int) -> None:
        """Undo a pre-crash temporary ask-size reduction before idempotent recovery."""
        with self._conn() as db:
            row = db.execute(
                "SELECT payload_json FROM weather_paper_signals WHERE id=?",
                (int(signal_id),),
            ).fetchone()
            if not row:
                return
            payload = _payload(row["payload_json"])
            original = payload.get("captured_ask_size_original")
            if original is None or isinstance(original, bool):
                return
            try:
                value = float(original)
            except (TypeError, ValueError, OverflowError):
                return
            if not math.isfinite(value) or value < 0.0:
                return
            payload["ask_size"] = value
            db.execute(
                "UPDATE weather_paper_signals SET payload_json=? WHERE id=?",
                (_json(payload), int(signal_id)),
            )

    def reconcile_crash_states(self) -> dict:
        """Classify abandoned in-flight work before a restarted service can scan."""
        pending_uncertain: list[int] = []
        acknowledged_to_fill: list[int] = []
        accounting_uncertain: list[int] = []
        presend_abandoned: list[int] = []
        settlement_uncertain = 0
        now = time.time()

        with self._conn() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM weather_paper_signals ORDER BY id"
            )]
            positions = {
                int(row["signal_id"]): dict(row)
                for row in db.execute(
                    "SELECT signal_id,validation_state,status FROM weather_paper_positions "
                    "WHERE signal_id IS NOT NULL"
                )
            }
            for row in rows:
                payload = _payload(row.get("payload_json"))
                if payload.get("paper_execution_protocol_version") != PAPER_EXECUTION_PROTOCOL_V4:
                    continue
                sid = int(row["id"])
                status = str(row.get("status") or "")
                receipt = row.get("telegram_message_id")
                position = positions.get(sid)
                if position is not None and str(position.get("validation_state") or "") != "VALIDATED":
                    # A process died after the inherited row insert but before the v4
                    # transaction committed final validation/capacity identity. Preserve
                    # it as evidence; never invent or rewrite a clean fill afterward.
                    db.execute(
                        "UPDATE weather_paper_signals SET status='PAPER_ACCOUNTING_UNCERTAIN' WHERE id=?",
                        (sid,),
                    )
                    accounting_uncertain.append(sid)
                    continue
                if status == "OPEN" and receipt is None:
                    # save_signal completed, but PENDING_DELIVERY was never reached;
                    # no network send can have started on this control path.
                    db.execute(
                        "UPDATE weather_paper_signals SET status='ABANDONED_PRE_SEND' WHERE id=?",
                        (sid,),
                    )
                    presend_abandoned.append(sid)
                elif status == "PENDING_DELIVERY" and receipt is None:
                    # The process may have died after remote acceptance but before
                    # persisting the receipt. Never retry automatically.
                    db.execute(
                        "UPDATE weather_paper_signals SET status='DELIVERY_UNCERTAIN' WHERE id=?",
                        (sid,),
                    )
                    pending_uncertain.append(sid)
                elif status == "PENDING_DELIVERY" and receipt is not None:
                    db.execute(
                        "UPDATE weather_paper_signals SET status='ACKNOWLEDGED' WHERE id=?",
                        (sid,),
                    )
                    if position is None:
                        acknowledged_to_fill.append(sid)
                elif status == "ACKNOWLEDGED" and position is None:
                    acknowledged_to_fill.append(sid)

            settlement_uncertain = int(db.execute(
                """
                SELECT COUNT(*) FROM weather_paper_positions
                WHERE settlement_notification_state='SENDING'
                """
            ).fetchone()[0])
            if settlement_uncertain:
                db.execute(
                    """
                    UPDATE weather_paper_positions
                    SET settlement_notification_state='UNCERTAIN'
                    WHERE settlement_notification_state='SENDING'
                    """
                )

            # Reservation recovery follows durable signal evidence.
            db.execute(
                """
                UPDATE weather_paper_station_day_reservations
                SET state='RELEASED',reason='ABANDONED_PRE_SEND',updated_at=?
                WHERE signal_id IN (
                    SELECT id FROM weather_paper_signals WHERE status='ABANDONED_PRE_SEND'
                )
                """,
                (now,),
            )
            db.execute(
                """
                UPDATE weather_paper_station_day_reservations
                SET state='UNCERTAIN',reason='CRASH_DURING_DELIVERY',updated_at=?
                WHERE signal_id IN (
                    SELECT id FROM weather_paper_signals WHERE status='DELIVERY_UNCERTAIN'
                )
                """,
                (now,),
            )
            db.execute(
                """
                UPDATE weather_paper_station_day_reservations
                SET state='UNCERTAIN',reason='CRASH_DURING_PAPER_ACCOUNTING',updated_at=?
                WHERE signal_id IN (
                    SELECT id FROM weather_paper_signals WHERE status='PAPER_ACCOUNTING_UNCERTAIN'
                )
                """,
                (now,),
            )
            db.execute(
                """
                UPDATE weather_paper_station_day_reservations
                SET state='ACKNOWLEDGED',reason='DURABLE_TELEGRAM_RECEIPT',updated_at=?
                WHERE signal_id IN (
                    SELECT id FROM weather_paper_signals
                    WHERE status='ACKNOWLEDGED' AND telegram_message_id IS NOT NULL
                )
                """,
                (now,),
            )
            # A reservation created before signal linkage cannot have sent anything.
            db.execute(
                """
                UPDATE weather_paper_station_day_reservations
                SET state='RELEASED',reason='NO_LINKED_DELIVERY_ATTEMPT',updated_at=?
                WHERE signal_id IS NULL AND state='PENDING'
                """,
                (now,),
            )

        for sid in pending_uncertain:
            signal = self._load_signal(sid) or {}
            payload = _payload(signal.get("payload_json"))
            self.record_decision(
                decision_id=str(payload.get("decision_id") or signal.get("fingerprint") or sid),
                event_id=str(signal.get("event_id") or ""),
                market_id=str(signal.get("market_id") or "") or None,
                side=str(signal.get("side") or "") or None,
                outcome="DELIVERY_UNCERTAIN",
                reason="PROCESS_RESTART_WITH_INFLIGHT_DELIVERY",
            )
        for sid in accounting_uncertain:
            signal = self._load_signal(sid) or {}
            payload = _payload(signal.get("payload_json"))
            self.record_decision(
                decision_id=str(payload.get("decision_id") or signal.get("fingerprint") or sid),
                event_id=str(signal.get("event_id") or ""),
                market_id=str(signal.get("market_id") or "") or None,
                side=str(signal.get("side") or "") or None,
                outcome="ACCOUNTING_UNCERTAIN",
                reason="PROCESS_RESTART_WITH_PARTIAL_UNVERIFIED_POSITION",
            )

        fill_recovered = 0
        fill_errors: list[str] = []
        for sid in sorted(set(acknowledged_to_fill)):
            try:
                self._restore_original_visible_capacity(sid)
                position = self.ensure_position_for_signal(sid, 0.0)
                if position is not None:
                    fill_recovered += 1
            except Exception as exc:
                code = getattr(exc, "code", type(exc).__name__)
                self.set_signal_status(sid, "PAPER_ACCOUNTING_UNCERTAIN")
                self._set_reservation_state_for_signal(
                    sid, "UNCERTAIN", f"RECOVERY_ACCOUNTING:{code}"
                )
                fill_errors.append(f"{sid}:{code}")

        return {
            "version": RECOVERY_VERSION,
            "delivery_uncertain_recovered": len(pending_uncertain),
            "accounting_uncertain_recovered": len(accounting_uncertain) + len(fill_errors),
            "presend_abandoned_released": len(presend_abandoned),
            "acknowledged_fills_recovered": fill_recovered,
            "settlement_notification_uncertain_recovered": settlement_uncertain,
            "fill_errors": fill_errors,
            "automatic_resend": False,
            "financial_authority": False,
        }

    def stats(self) -> dict:
        data = dict(super().stats())
        signal_uncertain = int(data.get("delivery_uncertain") or 0)
        with self._conn() as db:
            settlement_uncertain = int(db.execute(
                """
                SELECT COUNT(*) FROM weather_paper_positions
                WHERE validation_state='VALIDATED'
                  AND settlement_notification_state='UNCERTAIN'
                """
            ).fetchone()[0])
            accounting_uncertain = int(db.execute(
                "SELECT COUNT(*) FROM weather_paper_signals WHERE status='PAPER_ACCOUNTING_UNCERTAIN'"
            ).fetchone()[0])
            reservation_uncertain = int(db.execute(
                """
                SELECT COUNT(*) FROM weather_paper_station_day_reservations
                WHERE state='UNCERTAIN'
                """
            ).fetchone()[0])
        data.update({
            "telegram_delivery_uncertain": signal_uncertain,
            "settlement_notification_uncertain": settlement_uncertain,
            "paper_accounting_uncertain": accounting_uncertain,
            "station_day_uncertain_reservations": reservation_uncertain,
            "delivery_uncertain": signal_uncertain + settlement_uncertain,
            "uncertainty_total": signal_uncertain + settlement_uncertain + accounting_uncertain,
            "crash_recovery_version": RECOVERY_VERSION,
        })
        return data
