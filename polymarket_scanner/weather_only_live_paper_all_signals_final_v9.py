from __future__ import annotations

"""Final V9 corrective wrapper for deployment-custody, operator liveness and recall freshness.

This layer is still PAPER-only. It adds no wallet, signing, order, cancellation or
financial-delivery authority. It closes three operational gaps identified by the
independent final review:

* successful historical Telegram invalidations are drained in-process rather than
  using systemd restarts as pagination;
* an explicit Telegram "message to edit not found" receipt is treated as a confirmed
  absent stale message and durably audited; and
* exhaustive untagged Gamma recall carries explicit completion/age evidence and may
  not be reused beyond the discovery layer's five-minute hard TTL.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

from .weather_only_discovery import GLOBAL_CENSUS_TTL_SECONDS
from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
    WeatherLivePaperError,
    _atomic_json,
)
from .weather_only_live_paper_all_signals_final_v8 import (
    FINAL_ALL_PAPER_RUNTIME_V8_VERSION,
    FinalAllPaperWeatherLiveServiceV8,
)
from .weather_only_live_paper_v2 import DEFAULT_PAPER_STAKE_USD
from .weather_only_operator_state_corrective import (
    OperatorStateCommandController,
    OperatorStateTelegram,
)
from .weather_only_operator_state_corrective_v4 import OperatorStatePostReceiptStoreV4
from .weather_only_paper_corrective import CorrectiveSettlementEngine, DeliveryUncertain


FINAL_ALL_PAPER_RUNTIME_V9_VERSION = (
    "final_all_paper_v9_operator_drain_deleted_message_global_recall_5m"
)
MAX_GLOBAL_RECALL_REUSE_SECONDS = GLOBAL_CENSUS_TTL_SECONDS
OPERATOR_SYNC_BATCH_SIZE = 200
OPERATOR_SYNC_MAX_BATCHES = 1_000
TELEGRAM_EDIT_APPLIED = "APPLIED"
TELEGRAM_EDIT_ABSENT = "ABSENT"


class FinalOperatorStateTelegram(OperatorStateTelegram):
    """Operator edit transport that distinguishes a confirmed missing old message."""

    async def edit_html(self, message_id: int, text: str) -> str:
        if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id <= 0:
            raise WeatherLivePaperError("PAPER_TELEGRAM_EDIT_MESSAGE_ID_INVALID")
        if len(text) > 3900:
            raise WeatherLivePaperError("PAPER_TELEGRAM_EDIT_MESSAGE_TOO_LONG")
        payload: dict[str, object] = {
            "chat_id": self.chat_id,
            "message_id": int(message_id),
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": {"inline_keyboard": []},
        }
        endpoint = f"https://api.telegram.org/bot{self.token}/editMessageText"
        for attempt in range(3):
            try:
                response = await self.http.post(endpoint, json=payload)
            except httpx.RequestError:
                if attempt >= 2:
                    raise DeliveryUncertain("PAPER_TELEGRAM_EDIT_UNCERTAIN") from None
                await asyncio.sleep(0.5 * (attempt + 1))
                continue

            if response.status_code == 429:
                try:
                    retry_after = float(
                        (response.json().get("parameters") or {}).get("retry_after", 1.0)
                    )
                except Exception:
                    retry_after = 1.0
                if attempt >= 2:
                    raise WeatherLivePaperError("PAPER_TELEGRAM_EDIT_RATE_LIMITED")
                await asyncio.sleep(min(15.0, max(1.0, retry_after)))
                continue

            if response.status_code >= 500:
                if attempt >= 2:
                    raise DeliveryUncertain("PAPER_TELEGRAM_EDIT_SERVER_UNCERTAIN")
                await asyncio.sleep(0.5 * (attempt + 1))
                continue

            if response.status_code >= 400:
                description = ""
                try:
                    body = response.json()
                    description = str(body.get("description") or "").lower()
                except Exception:
                    description = ""
                if response.status_code == 400 and "message is not modified" in description:
                    return TELEGRAM_EDIT_APPLIED
                # This exact Telegram receipt proves that the old stale alert is no
                # longer visible. Other 4xx responses remain fail-closed.
                if response.status_code == 400 and "message to edit not found" in description:
                    return TELEGRAM_EDIT_ABSENT
                raise WeatherLivePaperError(
                    f"PAPER_TELEGRAM_EDIT_HTTP_{response.status_code}"
                )

            try:
                body = response.json()
            except ValueError:
                if attempt >= 2:
                    raise DeliveryUncertain(
                        "PAPER_TELEGRAM_EDIT_RESPONSE_UNCERTAIN"
                    ) from None
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            if not isinstance(body, dict) or body.get("ok") is not True:
                if attempt >= 2:
                    raise DeliveryUncertain("PAPER_TELEGRAM_EDIT_RECEIPT_UNCERTAIN")
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            return TELEGRAM_EDIT_APPLIED

        raise WeatherLivePaperError("PAPER_TELEGRAM_EDIT_RETRY_EXHAUSTED")


class FinalOperatorStatePostReceiptStore(OperatorStatePostReceiptStoreV4):
    """V4 store plus durable audit evidence for confirmed absent Telegram messages."""

    def __init__(self, path) -> None:
        super().__init__(path)
        with self._conn() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS weather_paper_operator_absent (
                    signal_id INTEGER PRIMARY KEY,
                    telegram_message_id INTEGER NOT NULL,
                    detail TEXT NOT NULL,
                    confirmed_at REAL NOT NULL,
                    FOREIGN KEY(signal_id) REFERENCES weather_paper_signals(id)
                )
                """
            )

    def mark_operator_sync_absent(
        self,
        signal_id: int,
        telegram_message_id: int,
        detail: str = "TELEGRAM_MESSAGE_TO_EDIT_NOT_FOUND",
    ) -> None:
        sid = int(signal_id)
        mid = int(telegram_message_id)
        if sid <= 0 or mid <= 0:
            raise ValueError("operator absent identity invalid")
        # Record the explicit remote terminal receipt first. If the process crashes
        # before APPLIED is committed, the next startup safely retries and sees the
        # same explicit absence again.
        with self._conn() as db:
            db.execute(
                """
                INSERT INTO weather_paper_operator_absent(
                    signal_id,telegram_message_id,detail,confirmed_at
                ) VALUES(?,?,?,?)
                ON CONFLICT(signal_id) DO UPDATE SET
                    telegram_message_id=excluded.telegram_message_id,
                    detail=excluded.detail,
                    confirmed_at=excluded.confirmed_at
                """,
                (sid, mid, str(detail)[:500], time.time()),
            )
        super().mark_operator_sync_applied(sid)

    def operator_sync_summary(self) -> dict:
        summary = dict(super().operator_sync_summary())
        with self._conn() as db:
            absent = int(
                db.execute("SELECT COUNT(*) FROM weather_paper_operator_absent").fetchone()[0]
            )
        summary["confirmed_absent"] = absent
        summary["deleted_message_is_terminal_confirmation"] = True
        return summary


class FinalAllPaperWeatherLiveServiceV9(FinalAllPaperWeatherLiveServiceV8):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

        self._v9_superseded_telegram = self.telegram
        self._v9_superseded_settlement = self.settlement
        self._v9_superseded_commands = self.commands

        self.telegram = FinalOperatorStateTelegram(
            token=self._v9_superseded_telegram.token,
            chat_id=self._v9_superseded_telegram.chat_id,
        )
        self.positions = FinalOperatorStatePostReceiptStore(self.db_path)
        self._v9_recovery = self.positions.reconcile_v5_after_restart()
        self._historical_operator_sync_created += int(
            self.positions.ensure_operator_sync_records()
        )
        self.settlement = CorrectiveSettlementEngine(
            store=self.positions, telegram=self.telegram
        )
        self.commands = OperatorStateCommandController(
            telegram=self.telegram,
            store=self.positions,
            maker_store=self.maker_store,
            status_path=self.status_path,
            paper_stake_usd=self.paper_stake_usd,
        )

    async def close(self) -> None:
        await asyncio.gather(
            self._v9_superseded_settlement.close(),
            self._v9_superseded_commands.close(),
            self._v9_superseded_telegram.close(),
            return_exceptions=True,
        )
        await super().close()

    async def _sync_operator_messages(self, *, signal_id: int | None = None) -> dict:
        # Backfill all historical terminal rows, then drain every successful batch in
        # this process. This removes systemd restart count from migration correctness.
        if signal_id is None:
            await asyncio.to_thread(self.positions.ensure_operator_sync_records)
        errors: list[str] = []
        processed = 0
        absent = 0
        batches = 0

        while batches < OPERATOR_SYNC_MAX_BATCHES:
            rows = await asyncio.to_thread(
                self.positions.pending_operator_sync, OPERATOR_SYNC_BATCH_SIZE,
                signal_id=signal_id,
            )
            if not rows:
                break
            batches += 1
            batch_failed = False
            for row in rows:
                row_signal_id = int(row["signal_id"])
                message_id = int(row["telegram_message_id"])
                try:
                    outcome = await self.telegram.edit_html(
                        message_id,
                        str(row["message_text"]),
                    )
                    if outcome == TELEGRAM_EDIT_ABSENT:
                        await asyncio.to_thread(
                            self.positions.mark_operator_sync_absent,
                            row_signal_id,
                            message_id,
                        )
                        absent += 1
                    else:
                        await asyncio.to_thread(
                            self.positions.mark_operator_sync_applied, row_signal_id
                        )
                    processed += 1
                except Exception as exc:
                    code = getattr(exc, "code", type(exc).__name__)
                    await asyncio.to_thread(
                        self.positions.mark_operator_sync_failed,
                        row_signal_id,
                        str(code),
                    )
                    errors.append(f"OPERATOR_SYNC:{row_signal_id}:{code}")
                    batch_failed = True
            # Do not hammer a deterministic/ambiguous failure repeatedly inside one
            # startup. Successful batches continue until the backlog is exhausted.
            if batch_failed or signal_id is not None:
                break
        else:
            errors.append("OPERATOR_SYNC_DRAIN_BATCH_CAP_EXCEEDED")

        summary = await asyncio.to_thread(self.positions.operator_sync_summary)
        if signal_id is not None:
            # The immediate transition's result is independent of older backlogs.
            summary["healthy"] = not errors
        summary.update(
            {
                "errors": errors,
                "processed_this_pass": processed,
                "confirmed_absent_this_pass": absent,
                "batches_this_pass": batches,
                "restart_pagination_required": False,
            }
        )
        return summary

    async def run_cycle(self) -> dict:
        status = dict(await super().run_cycle())
        recall = dict(status.get("global_weather_recall") or {})
        completed = recall.get("census_completed_at")
        age = recall.get("age_seconds")
        max_reuse = recall.get("max_reuse_seconds")
        valid_numbers = (
            isinstance(completed, (int, float))
            and not isinstance(completed, bool)
            and isinstance(age, (int, float))
            and not isinstance(age, bool)
            and isinstance(max_reuse, (int, float))
            and not isinstance(max_reuse, bool)
        )
        fresh = bool(
            recall.get("complete") is True
            and valid_numbers
            and float(completed) > 0.0
            and 0.0 <= float(age) <= MAX_GLOBAL_RECALL_REUSE_SECONDS
            and 0.0 < float(max_reuse) <= MAX_GLOBAL_RECALL_REUSE_SECONDS
        )
        status.update(
            {
                "final_all_paper_runtime_v9_version": FINAL_ALL_PAPER_RUNTIME_V9_VERSION,
                "final_all_paper_runtime_v8_version": FINAL_ALL_PAPER_RUNTIME_V8_VERSION,
                "operator_sync_restart_pagination_required": False,
                "operator_deleted_message_terminal_confirmation": True,
                "global_weather_recall_max_reuse_seconds": MAX_GLOBAL_RECALL_REUSE_SECONDS,
                "global_weather_recall_certified_at": completed if valid_numbers else None,
                "global_weather_recall_age_seconds": age if valid_numbers else None,
                "global_weather_recall_fresh": fresh,
                "financial_delivery": False,
                "financial_authority": False,
                "automatic_order_placement": False,
                "wallet_or_order_api_loaded": False,
            }
        )
        if not fresh:
            status["cycle_ok"] = False
            status["operator_all_lanes_healthy"] = False
            errors = list(status.get("errors") or [])
            if "GLOBAL_WEATHER_RECALL_STALE" not in errors:
                errors.append("GLOBAL_WEATHER_RECALL_STALE")
            status["errors"] = errors
        _atomic_json(self.status_path, status)
        return status


async def _main(args) -> int:
    service = FinalAllPaperWeatherLiveServiceV9(
        db_path=args.db,
        status_path=args.status,
        release_file=args.release_file,
        interval_seconds=args.interval_seconds,
        forecast_cache_seconds=args.forecast_cache_seconds,
        forecast_raw_gap_min=args.forecast_raw_gap_min,
        max_forecast_events=args.max_forecast_events,
        paper_stake_usd=args.paper_stake_usd,
    )
    try:
        if args.once:
            await service.send_startup()
            status = await service.run_cycle()
            print(json.dumps(status, sort_keys=True, indent=2))
            return 0 if status.get("operator_all_lanes_healthy") is True else 2
        await service.loop()
        return 0
    finally:
        await service.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--release-file", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--forecast-cache-seconds", type=float, default=DEFAULT_FORECAST_CACHE_SECONDS)
    parser.add_argument("--forecast-raw-gap-min", type=float, default=DEFAULT_FORECAST_RAW_GAP_MIN)
    parser.add_argument("--max-forecast-events", type=int, default=DEFAULT_MAX_FORECAST_EVENTS)
    parser.add_argument("--paper-stake-usd", type=float, default=DEFAULT_PAPER_STAKE_USD)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
