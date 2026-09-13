from __future__ import annotations

"""Bounded, restart-safe historical validation for the weather paper ledger.

The canonical runtime must not rescan months of immutable forecast history every
180-second cycle.  This helper advances a persisted SQLite cursor in bounded batches.
Changing the quarantine policy ID explicitly resets the cursor, making policy-driven
revalidation deliberate rather than accidental.

Rows are processed in primary-key order regardless of delivery state.  That is
important: an old undelivered row cannot be skipped by the cursor and later become a
valid historical position merely because delivery metadata appeared after the scan.
A row-level exception stops cursor advancement at that row so it is retried instead
of disappearing from validation.
"""

from dataclasses import dataclass


HISTORY_CURSOR_STATE_KEY = "v4_history_quarantine_cursor"
HISTORY_POLICY_STATE_KEY = "v4_history_quarantine_policy"


class BoundedHistoryError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class HistoryQuarantineReport:
    policy_id: str
    cursor_before: int
    cursor_after: int
    scanned_now: int
    quarantined_now: int
    scan_complete_at_call_end: bool
    errors: tuple[str, ...]
    bounded_work: bool = True
    financial_authority: bool = False

    def as_dict(self) -> dict:
        return {
            "policy_id": self.policy_id,
            "cursor_before": self.cursor_before,
            "cursor_after": self.cursor_after,
            "scanned_now": self.scanned_now,
            "quarantined_now": self.quarantined_now,
            "scan_complete_at_call_end": self.scan_complete_at_call_end,
            "errors": list(self.errors),
            "bounded_work": self.bounded_work,
            "financial_authority": self.financial_authority,
        }


class BoundedForecastHistoryQuarantine:
    def __init__(
        self,
        store,
        *,
        policy_id: str,
        current_execution_protocol: str,
        quarantine_reason: str,
        batch_size: int = 200,
    ) -> None:
        if not isinstance(policy_id, str) or not policy_id.strip() or policy_id != policy_id.strip():
            raise BoundedHistoryError("HISTORY_POLICY_ID_INVALID")
        if not isinstance(current_execution_protocol, str) or not current_execution_protocol.strip():
            raise BoundedHistoryError("HISTORY_EXECUTION_PROTOCOL_INVALID")
        if not isinstance(quarantine_reason, str) or not quarantine_reason.strip():
            raise BoundedHistoryError("HISTORY_QUARANTINE_REASON_INVALID")
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 1000:
            raise BoundedHistoryError("HISTORY_BATCH_SIZE_INVALID")
        self.store = store
        self.policy_id = policy_id
        self.current_execution_protocol = current_execution_protocol
        self.quarantine_reason = quarantine_reason
        self.batch_size = batch_size

    def _state(self) -> int:
        installed_policy = self.store.get_state(HISTORY_POLICY_STATE_KEY, "")
        if installed_policy != self.policy_id:
            # Reset cursor *before* publishing the new policy marker.  Both writes
            # are durable SQLite state and a crash can only cause extra revalidation,
            # never silent skipping under the new policy.
            self.store.set_state(HISTORY_CURSOR_STATE_KEY, "0")
            self.store.set_state(HISTORY_POLICY_STATE_KEY, self.policy_id)
            return 0
        raw = self.store.get_state(HISTORY_CURSOR_STATE_KEY, "0")
        try:
            cursor = int(raw)
        except (TypeError, ValueError):
            raise BoundedHistoryError("HISTORY_CURSOR_INVALID") from None
        if cursor < 0:
            raise BoundedHistoryError("HISTORY_CURSOR_INVALID")
        return cursor

    def _batch(self, cursor: int) -> list[dict]:
        with self.store._conn() as db:
            return [
                dict(row)
                for row in db.execute(
                    """
                    SELECT id,payload_json,status,telegram_message_id,telegram_sent_at,created_at
                    FROM weather_paper_signals
                    WHERE lane='weather_forecast_raw_gap' AND id>?
                    ORDER BY id
                    LIMIT ?
                    """,
                    (int(cursor), int(self.batch_size)),
                )
            ]

    @staticmethod
    def _protocol(raw_payload: object) -> str:
        import json

        if isinstance(raw_payload, dict):
            payload = raw_payload
        else:
            try:
                payload = json.loads(str(raw_payload or ""))
            except Exception:
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return str(payload.get("paper_execution_protocol_version") or "").strip()

    def run_once(self) -> HistoryQuarantineReport:
        cursor_before = self._state()
        rows = self._batch(cursor_before)
        cursor_after = cursor_before
        quarantined = 0
        scanned = 0
        errors: list[str] = []

        for row in rows:
            signal_id = int(row["id"])
            try:
                protocol = self._protocol(row.get("payload_json"))
                if protocol != self.current_execution_protocol:
                    changed = self.store.quarantine_signal(signal_id, self.quarantine_reason)
                    if changed:
                        quarantined += 1
                cursor_after = signal_id
                scanned += 1
                self.store.set_state(HISTORY_CURSOR_STATE_KEY, str(cursor_after))
            except Exception as exc:
                errors.append(f"HISTORY_SIGNAL_{signal_id}:{type(exc).__name__}")
                # Do not advance past the failed row. It must remain visible on the
                # next cycle rather than becoming an untracked validation hole.
                break

        complete = not errors and len(rows) < self.batch_size
        return HistoryQuarantineReport(
            policy_id=self.policy_id,
            cursor_before=cursor_before,
            cursor_after=cursor_after,
            scanned_now=scanned,
            quarantined_now=quarantined,
            scan_complete_at_call_end=complete,
            errors=tuple(errors),
        )
