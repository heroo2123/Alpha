from __future__ import annotations

"""Unified signal/position facade for the v4 corrective paper runtime.

The original paper design intentionally split the signal ledger and position ledger
into two classes backed by the same SQLite file.  V4 needs an atomic-looking operator
surface without changing the legacy classes, so this facade initializes both schemas
and delegates signal persistence explicitly while retaining the stricter v4 position
rules.
"""

from pathlib import Path

from .weather_only_paper_corrective import CorrectiveWeatherPaperPositionStore
from .weather_only_paper_positions import _json, _payload
from .weather_only_paper_store import WeatherPaperStore


class CorrectiveWeatherPaperStore(CorrectiveWeatherPaperPositionStore):
    def __init__(self, path: str | Path) -> None:
        # Signal schema must exist before v4 stats/status can query it, even in a
        # pristine database with no signals yet.
        self.signal_store = WeatherPaperStore(path)
        super().__init__(path)

    def save_signal(self, **kwargs):
        return self.signal_store.save_signal(**kwargs)

    def mark_telegram_sent(self, signal_id: int, message_id: int, sent_at: float | None = None) -> None:
        self.signal_store.mark_telegram_sent(signal_id, message_id, sent_at=sent_at)

    def ensure_position_for_signal(self, signal_id: int, target_stake_usd: float):
        # Preserve the original captured capacity before the parent computes the
        # remaining capacity available after prior v4 fills.  The latter may update
        # ask_size for the inherited fill primitive; the former remains immutable
        # provenance for offline replay.
        with self._conn() as db:
            row = db.execute(
                "SELECT payload_json FROM weather_paper_signals WHERE id=?",
                (int(signal_id),),
            ).fetchone()
            if row:
                payload = _payload(row["payload_json"])
                if "captured_ask_size_original" not in payload and "ask_size" in payload:
                    payload["captured_ask_size_original"] = payload["ask_size"]
                    db.execute(
                        "UPDATE weather_paper_signals SET payload_json=? WHERE id=?",
                        (_json(payload), int(signal_id)),
                    )
        return super().ensure_position_for_signal(signal_id, target_stake_usd)
