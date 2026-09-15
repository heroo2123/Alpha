from __future__ import annotations

"""Immutable-fingerprint retry semantics for operator-visible invalidations."""

import json
import math
import time

from .weather_only_operator_state_corrective import (
    MAX_RETRIES_PER_BASE_FINGERPRINT,
    OPERATOR_SYNC_APPLIED,
    RETRYABLE_AFTER_VISIBLE_INVALIDATION,
    RETRY_COOLDOWN_SECONDS,
    OperatorStatePostReceiptStore,
    _sha,
)
from .weather_only_operator_state_corrective_v2 import OperatorStatePostReceiptStoreV2
from .weather_only_paper_positions import WeatherPaperPositionError, _payload
from .weather_only_paper_post_receipt import PostReceiptWeatherPaperStore


OPERATOR_STATE_CORRECTIVE_V3_VERSION = (
    "weather_operator_state_v3_immutable_base_fingerprint_retry_identity"
)


class OperatorStatePostReceiptStoreV3(OperatorStatePostReceiptStoreV2):
    """Release dedupe by deriving a retry identity, never by rewriting old evidence."""

    def save_signal(self, **kwargs):
        updated = dict(kwargs)
        base = self._final_fingerprint(updated)
        if not base:
            return PostReceiptWeatherPaperStore.save_signal(self, **updated)

        now = time.time()
        with self._conn() as db:
            guard = db.execute(
                "SELECT invalidations,next_retry_at FROM weather_paper_retry_guard "
                "WHERE base_fingerprint=?",
                (base,),
            ).fetchone()
        fingerprint = base
        if guard is not None:
            invalidations = int(guard["invalidations"])
            retry_at = float(guard["next_retry_at"])
            if invalidations >= MAX_RETRIES_PER_BASE_FINGERPRINT or now < retry_at:
                return None
            retry_generation = invalidations + 1
            fingerprint = "retry:" + _sha(
                {
                    "base_fingerprint": base,
                    "retry_generation": retry_generation,
                }
            )
            payload = updated.get("payload")
            if not isinstance(payload, dict):
                raise WeatherPaperPositionError("PAPER_STORE_SIGNAL_IDENTITY_INVALID")
            payload = dict(payload)
            payload["operator_retry_base_fingerprint"] = base
            payload["operator_retry_generation"] = retry_generation
            updated["payload"] = payload
        updated["fingerprint"] = fingerprint
        # The source-shock episode identity was already computed above. Call the
        # common V5 store directly so no parent layer rewrites a retry fingerprint.
        return PostReceiptWeatherPaperStore.save_signal(self, **updated)

    def ensure_operator_sync_records(self, *, signal_ids: list[int] | None = None) -> int:
        created = OperatorStatePostReceiptStore.ensure_operator_sync_records(
            self, signal_ids=signal_ids
        )
        wanted = None if signal_ids is None else {int(value) for value in signal_ids}
        with self._conn() as db:
            rows = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT o.signal_id,s.payload_json
                    FROM weather_paper_operator_sync o
                    JOIN weather_paper_signals s ON s.id=o.signal_id
                    """
                )
            ]
            for row in rows:
                sid = int(row["signal_id"])
                if wanted is not None and sid not in wanted:
                    continue
                payload = _payload(row.get("payload_json"))
                root = str(payload.get("operator_retry_base_fingerprint") or "").strip()
                if root:
                    db.execute(
                        "UPDATE weather_paper_operator_sync SET base_fingerprint=? "
                        "WHERE signal_id=?",
                        (root, sid),
                    )
        return created

    def mark_operator_sync_applied(self, signal_id: int) -> None:
        sid = int(signal_id)
        now = time.time()
        with self._conn() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                sync = db.execute(
                    "SELECT * FROM weather_paper_operator_sync WHERE signal_id=?",
                    (sid,),
                ).fetchone()
                signal = db.execute(
                    "SELECT status FROM weather_paper_signals WHERE id=?",
                    (sid,),
                ).fetchone()
                if sync is None or signal is None:
                    raise WeatherPaperPositionError("OPERATOR_SYNC_SIGNAL_NOT_FOUND")

                status = str(signal["status"] or "")
                released = int(sync["fingerprint_released"] or 0)
                base = str(sync["base_fingerprint"] or "").strip()
                if not base:
                    raise WeatherPaperPositionError("OPERATOR_SYNC_BASE_FINGERPRINT_MISSING")
                if released == 0 and status in RETRYABLE_AFTER_VISIBLE_INVALIDATION:
                    guard = db.execute(
                        "SELECT invalidations FROM weather_paper_retry_guard "
                        "WHERE base_fingerprint=?",
                        (base,),
                    ).fetchone()
                    invalidations = int(guard["invalidations"]) if guard else 0
                    if invalidations < MAX_RETRIES_PER_BASE_FINGERPRINT:
                        db.execute(
                            """
                            INSERT INTO weather_paper_retry_guard(
                                base_fingerprint,invalidations,next_retry_at,updated_at
                            ) VALUES(?,?,?,?)
                            ON CONFLICT(base_fingerprint) DO UPDATE SET
                                invalidations=excluded.invalidations,
                                next_retry_at=excluded.next_retry_at,
                                updated_at=excluded.updated_at
                            """,
                            (
                                base,
                                invalidations + 1,
                                now + RETRY_COOLDOWN_SECONDS,
                                now,
                            ),
                        )
                        released = 1

                db.execute(
                    """
                    UPDATE weather_paper_operator_sync
                    SET state=?,attempts=attempts+1,last_error=NULL,updated_at=?,
                        applied_at=?,fingerprint_released=?
                    WHERE signal_id=?
                    """,
                    (OPERATOR_SYNC_APPLIED, now, now, released, sid),
                )
                db.execute("COMMIT")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
