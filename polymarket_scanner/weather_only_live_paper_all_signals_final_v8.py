from __future__ import annotations

"""Final V8 corrective wrapper: historical operator-state migration completeness.

V1 deliberately introduced a migration cutoff so a newly installed operator-sync
layer would not unexpectedly edit old Telegram messages.  That was safe for fresh
installs but unsafe for a mature PAPER ledger: an already-delivered terminal V5
signal below the cutoff could remain actionable-looking forever.

V8 makes migration explicit and fail-closed.  Every Telegram-delivered terminal
signal receives a durable operator-sync row before ONLINE is announced, regardless
of the legacy cutoff.  The original immutable signal fingerprint is untouched.
"""

from .weather_only_live_paper_all_signals_final_v7 import (
    FINAL_ALL_PAPER_RUNTIME_V7_VERSION,
    FinalAllPaperWeatherLiveServiceV7,
    _main_async,
    main as _parent_main,
)
from .weather_only_operator_state_corrective import TERMINAL_VISIBLE_STATUSES


FINAL_ALL_PAPER_RUNTIME_V8_VERSION = (
    "final_all_paper_v8_historical_terminal_operator_sync_complete"
)


class FinalAllPaperWeatherLiveServiceV8(FinalAllPaperWeatherLiveServiceV7):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # This intentionally ignores the old operator_sync_min_signal_id_v1 cutoff.
        # ensure_operator_sync_records(None) walks every Telegram-delivered terminal
        # row and inserts only missing synchronization records.
        self._historical_operator_sync_created = int(
            self.positions.ensure_operator_sync_records()
        )

    def _historical_operator_sync_missing(self) -> int:
        placeholders = ",".join("?" for _ in TERMINAL_VISIBLE_STATUSES)
        params = tuple(sorted(TERMINAL_VISIBLE_STATUSES))
        with self.positions._conn() as db:
            row = db.execute(
                "SELECT COUNT(*) AS n FROM weather_paper_signals s "
                "LEFT JOIN weather_paper_operator_sync o ON o.signal_id=s.id "
                "WHERE s.telegram_message_id IS NOT NULL "
                f"AND s.status IN ({placeholders}) AND o.signal_id IS NULL",
                params,
            ).fetchone()
        return int(row["n"] if row is not None else 0)

    async def send_startup(self) -> None:
        # Repeat immediately before synchronization so rows terminalized during
        # construction/recovery cannot escape the migration gate.
        self._historical_operator_sync_created += int(
            self.positions.ensure_operator_sync_records()
        )
        missing = self._historical_operator_sync_missing()
        if missing != 0:
            raise RuntimeError("HISTORICAL_TERMINAL_OPERATOR_SYNC_INCOMPLETE")
        await super().send_startup()
        if self._historical_operator_sync_missing() != 0:
            raise RuntimeError("HISTORICAL_TERMINAL_OPERATOR_SYNC_INCOMPLETE")

    async def run_cycle(self) -> dict:
        self._historical_operator_sync_created += int(
            self.positions.ensure_operator_sync_records()
        )
        status = await super().run_cycle()
        missing = self._historical_operator_sync_missing()
        status["final_all_paper_runtime_v8_version"] = FINAL_ALL_PAPER_RUNTIME_V8_VERSION
        status["final_all_paper_runtime_v7_version"] = FINAL_ALL_PAPER_RUNTIME_V7_VERSION
        status["historical_terminal_operator_sync_backfill_required"] = True
        status["historical_terminal_operator_sync_created"] = int(
            self._historical_operator_sync_created
        )
        status["historical_terminal_operator_sync_missing"] = int(missing)
        status["historical_terminal_operator_sync_complete"] = missing == 0
        if missing != 0:
            status["cycle_ok"] = False
            errors = list(status.get("errors") or [])
            errors.append("HISTORICAL_TERMINAL_OPERATOR_SYNC_INCOMPLETE")
            status["errors"] = errors
        return status


async def _main(args) -> int:
    return await _main_async(args, FinalAllPaperWeatherLiveServiceV8)


def main() -> None:
    return _parent_main(service_cls=FinalAllPaperWeatherLiveServiceV8)


if __name__ == "__main__":
    main()
