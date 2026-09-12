from __future__ import annotations

"""Weather LIVE PAPER v3: future-day forecast guard + evidence quarantine.

The raw GEFS lane forecasts a *whole-day* high/low distribution.  Once the target
local day has begun, already-observed temperatures constrain that daily extreme and
a forecast-only distribution is no longer sufficient evidence.  V3 therefore:

* allows raw-GEFS candidates only while the target date is strictly in the future
  in the settlement station's local timezone;
* preserves, but quarantines from paper performance, historical raw-GEFS signals
  that were delivered on or after their target local date; and
* retains v2 automatic positions, exact-token Gamma settlement and Telegram
  commands for valid future-day research signals.

No wallet, order or cancel API is imported by this module.
"""

import argparse
import asyncio
import html
import json
import math
import time
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .weather_only_live_paper import (
    DEFAULT_FORECAST_CACHE_SECONDS,
    DEFAULT_FORECAST_RAW_GAP_MIN,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_FORECAST_EVENTS,
    _atomic_json,
)
from .weather_only_live_paper_v2 import (
    DEFAULT_PAPER_STAKE_USD,
    WeatherLivePaperV2Service,
)
from .weather_only_paper_control import (
    WeatherPaperCommandController,
    WeatherPaperSettlementEngine,
)
from .weather_only_paper_positions import (
    WEATHER_PAPER_POSITION_VERSION,
    WeatherPaperPositionStore,
)


WEATHER_LIVE_PAPER_V3_VERSION = (
    "weather_live_paper_v3_future_day_gefs_observation_blind_quarantine"
)
QUARANTINED_STATUS = "QUARANTINED"
QUARANTINE_REASON = "OBSERVATION_BLIND_GEFS_ON_OR_AFTER_TARGET_LOCAL_DATE"


class GuardedWeatherPaperPositionStore(WeatherPaperPositionStore):
    """Position store that excludes quarantined research from performance evidence."""

    def ensure_sent_positions(self, target_stake_usd: float) -> list[dict]:
        with self._conn() as db:
            rows = [dict(row) for row in db.execute(
                """
                SELECT s.id
                FROM weather_paper_signals s
                LEFT JOIN weather_paper_positions p ON p.signal_id=s.id
                WHERE s.telegram_message_id IS NOT NULL
                  AND s.status='OPEN'
                  AND p.id IS NULL
                ORDER BY s.id
                """
            )]
        created: list[dict] = []
        for row in rows:
            position = self.ensure_position_for_signal(int(row["id"]), target_stake_usd)
            if position is not None:
                created.append(position)
        return created

    def quarantine_signal(self, signal_id: int, reason: str = QUARANTINE_REASON) -> bool:
        sid = int(signal_id)
        why = str(reason or QUARANTINE_REASON)
        with self._conn() as db:
            signal = db.execute(
                "SELECT id,status FROM weather_paper_signals WHERE id=?", (sid,)
            ).fetchone()
            if not signal:
                return False
            already = str(signal["status"] or "") == QUARANTINED_STATUS
            db.execute(
                "UPDATE weather_paper_signals SET status=? WHERE id=?",
                (QUARANTINED_STATUS, sid),
            )
            # Preserve the original quote, fill quantity and any settlement columns
            # for audit.  Status alone removes the row from open/resolved evidence.
            db.execute(
                """
                UPDATE weather_paper_positions
                SET status=?, no_fill_reason=?
                WHERE signal_id=? AND status!=?
                """,
                (QUARANTINED_STATUS, why, sid, QUARANTINED_STATUS),
            )
            return not already

    def stats(self) -> dict:
        with self._conn() as db:
            row = db.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) AS open_n,
                    SUM(CASE WHEN status='NO_FILL' THEN 1 ELSE 0 END) AS no_fill,
                    SUM(CASE WHEN status='QUARANTINED' THEN 1 ELSE 0 END) AS quarantined,
                    SUM(CASE WHEN status='WON' THEN 1 ELSE 0 END) AS won,
                    SUM(CASE WHEN status='LOST' THEN 1 ELSE 0 END) AS lost,
                    SUM(CASE WHEN status='RESOLVED_PARTIAL' THEN 1 ELSE 0 END) AS partial,
                    SUM(CASE WHEN status IN ('OPEN','WON','LOST','RESOLVED_PARTIAL')
                             THEN capital_used ELSE 0 END) AS capital_all,
                    SUM(CASE WHEN status='OPEN' THEN capital_used ELSE 0 END) AS open_capital,
                    SUM(CASE WHEN status IN ('WON','LOST','RESOLVED_PARTIAL')
                             THEN capital_used ELSE 0 END) AS resolved_capital,
                    SUM(CASE WHEN status IN ('WON','LOST','RESOLVED_PARTIAL')
                             THEN proceeds ELSE 0 END) AS resolved_proceeds,
                    SUM(CASE WHEN status IN ('WON','LOST','RESOLVED_PARTIAL')
                             THEN pnl ELSE 0 END) AS pnl
                FROM weather_paper_positions
                """
            ).fetchone()
            lanes = [dict(value) for value in db.execute(
                """
                SELECT lane,COUNT(*) AS total,
                       SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) AS open_n,
                       SUM(CASE WHEN status='QUARANTINED' THEN 1 ELSE 0 END) AS quarantined,
                       SUM(CASE WHEN status='WON' THEN 1 ELSE 0 END) AS won,
                       SUM(CASE WHEN status='LOST' THEN 1 ELSE 0 END) AS lost,
                       SUM(CASE WHEN status='RESOLVED_PARTIAL' THEN 1 ELSE 0 END) AS partial,
                       SUM(CASE WHEN status IN ('WON','LOST','RESOLVED_PARTIAL')
                                THEN pnl ELSE 0 END) AS pnl
                FROM weather_paper_positions GROUP BY lane ORDER BY lane
                """
            )]
        data = dict(row) if row else {}
        won = int(data.get("won") or 0)
        lost = int(data.get("lost") or 0)
        partial = int(data.get("partial") or 0)
        resolved = won + lost + partial
        resolved_capital = float(data.get("resolved_capital") or 0.0)
        pnl = float(data.get("pnl") or 0.0)
        return {
            "version": WEATHER_PAPER_POSITION_VERSION,
            "total": int(data.get("total") or 0),
            "open": int(data.get("open_n") or 0),
            "no_fill": int(data.get("no_fill") or 0),
            "quarantined": int(data.get("quarantined") or 0),
            "resolved": resolved,
            "won": won,
            "lost": lost,
            "partial": partial,
            "win_rate": (won / (won + lost)) if won + lost else None,
            "capital_all": float(data.get("capital_all") or 0.0),
            "open_capital": float(data.get("open_capital") or 0.0),
            "resolved_capital": resolved_capital,
            "resolved_proceeds": float(data.get("resolved_proceeds") or 0.0),
            "pnl": pnl,
            "resolved_roi": (pnl / resolved_capital) if resolved_capital > 0.0 else None,
            "by_lane": lanes,
            "financial_authority": False,
            "automatic_order_placement": False,
        }


class GuardedWeatherPaperCommandController(WeatherPaperCommandController):
    def _status_text(self) -> str:
        base = super()._status_text()
        count = int(self.store.stats().get("quarantined") or 0)
        return base + f"\n🧯 Quarantined invalid/obsolete paper positions: <b>{count}</b>"

    def _stats_text(self) -> str:
        base = super()._stats_text()
        count = int(self.store.stats().get("quarantined") or 0)
        return (
            base
            + f"\n\n🧯 <b>Quarantined/excluded:</b> {count}"
            + "\nThese rows remain in the audit DB but do not count toward wins, losses, capital, P&amp;L or ROI."
        )


class WeatherLivePaperV3Service(WeatherLivePaperV2Service):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        # Replace v2's generic tracker with the quarantine-aware store while keeping
        # the same isolated database and read-only settlement/command transports.
        self.positions = GuardedWeatherPaperPositionStore(self.db_path)
        self.settlement.store = self.positions
        old_commands = self.commands
        self.commands = GuardedWeatherPaperCommandController(
            telegram=self.telegram,
            store=self.positions,
            status_path=self.status_path,
            paper_stake_usd=self.paper_stake_usd,
        )
        self._superseded_commands = old_commands
        self._forecast_same_day_suppressed_total = 0
        self._forecast_history_quarantined_total = 0
        self._quarantine_errors: list[str] = []

    async def close(self) -> None:
        try:
            await self._superseded_commands.close()
        finally:
            await super().close()

    def _now_epoch(self) -> float:
        return time.time()

    async def _station_metadata_for_compiled(self, compiled):
        station_id = str(compiled.station_hint or "").strip().upper()
        if not station_id:
            return None
        metadata = self._station_cache.get(station_id)
        if metadata is None:
            metadata = await self.station_client.station(station_id)
            self._station_cache[station_id] = metadata
        return metadata

    @staticmethod
    def _local_date(epoch: float, timezone_name: str) -> date:
        try:
            zone = ZoneInfo(str(timezone_name))
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise ValueError("invalid station timezone") from None
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).astimezone(zone).date()

    async def _forecast_candidate(self, event: dict, compiled) -> dict | None:
        # Raw daily GEFS describes the entire local calendar day's extreme.  It is
        # invalid to compare that unconditional forecast to a live bucket after the
        # day has started without first conditioning on observations already seen.
        try:
            metadata = await self._station_metadata_for_compiled(compiled)
            if metadata is None:
                return None
            local_today = self._local_date(self._now_epoch(), str(metadata.timezone))
        except Exception:
            return None
        if compiled.target_date <= local_today:
            self._forecast_same_day_suppressed_total += 1
            return None
        return await super()._forecast_candidate(event, compiled)

    def _forecast_signal_rows(self) -> list[dict]:
        with self.positions._conn() as db:
            return [dict(row) for row in db.execute(
                """
                SELECT id,payload_json,created_at,telegram_sent_at,status
                FROM weather_paper_signals
                WHERE lane='weather_forecast_raw_gap'
                  AND telegram_message_id IS NOT NULL
                  AND status!=?
                ORDER BY id
                """,
                (QUARANTINED_STATUS,),
            )]

    async def quarantine_observation_blind_history(self) -> dict:
        rows = await asyncio.to_thread(self._forecast_signal_rows)
        quarantined = 0
        errors: list[str] = []
        for row in rows:
            try:
                payload = json.loads(str(row.get("payload_json") or "{}"))
                if not isinstance(payload, dict):
                    continue
                target = date.fromisoformat(str(payload.get("target_date") or ""))
                station = str(payload.get("station") or "").strip().upper()
                if not station:
                    continue
                metadata = self._station_cache.get(station)
                if metadata is None:
                    metadata = await self.station_client.station(station)
                    self._station_cache[station] = metadata
                raw_sent = row.get("telegram_sent_at")
                sent_at = float(raw_sent if raw_sent is not None else row.get("created_at"))
                if not math.isfinite(sent_at):
                    continue
                sent_local_date = self._local_date(sent_at, str(metadata.timezone))
                if target <= sent_local_date:
                    changed = await asyncio.to_thread(
                        self.positions.quarantine_signal, int(row["id"]), QUARANTINE_REASON
                    )
                    if changed:
                        quarantined += 1
            except Exception as exc:
                errors.append(f"QUARANTINE_SIGNAL_{row.get('id')}:{type(exc).__name__}")
        self._forecast_history_quarantined_total += quarantined
        self._quarantine_errors = errors[-20:]
        return {"quarantined_now": quarantined, "errors": errors}

    async def send_startup(self) -> int:
        release = self.release_sha()
        text = "\n".join([
            "🟢 <b>WEATHER PAPER BOT ONLINE — GUARDED FORECAST MODE</b>",
            "",
            "Real Polymarket books + weather research are live.",
            "Every valid Telegram signal is automatically tracked as a simulated position.",
            f"Target paper stake: <b>${self.paper_stake_usd:.2f}</b>, capped by captured visible top-of-book quantity.",
            "",
            "🛡 <b>Forecast integrity rule:</b> raw whole-day GEFS alerts are FUTURE-DAY ONLY.",
            "Same-day daily high/low forecasts are suppressed until observations-so-far are integrated.",
            "Previously delivered same-day raw-GEFS signals are preserved but quarantined from P&amp;L statistics.",
            "",
            "Commands: <code>/status</code> <code>/stats</code> <code>/positions</code> <code>/history</code> <code>/recent</code> <code>/help</code>",
            "🚫 <b>NO REAL ORDERS</b> — no wallet/order/cancel authority exists.",
            f"Release: <code>{html.escape(release[:12])}</code>",
        ])
        return await self.telegram.send_html(text)

    async def run_cycle(self) -> dict:
        quarantine = await self.quarantine_observation_blind_history()
        status = await super().run_cycle()
        position_stats = await asyncio.to_thread(self.positions.stats)
        status = dict(status)
        status.update({
            "version": WEATHER_LIVE_PAPER_V3_VERSION,
            "forecast_policy": "RAW_GEFS_FUTURE_LOCAL_DATE_ONLY",
            "forecast_same_day_suppressed_total": self._forecast_same_day_suppressed_total,
            "forecast_history_quarantined_total": self._forecast_history_quarantined_total,
            "forecast_quarantine_last": quarantine,
            "paper_position_stats": position_stats,
            "financial_delivery": False,
            "financial_authority": False,
            "automatic_order_placement": False,
            "wallet_or_order_api_loaded": False,
        })
        if self._quarantine_errors:
            status["errors"] = list(status.get("errors") or []) + self._quarantine_errors
            status["cycle_ok"] = False
        _atomic_json(self.status_path, status)
        return status

    async def loop(self) -> None:
        await self.send_startup()
        await self.quarantine_observation_blind_history()
        await asyncio.to_thread(self.positions.ensure_sent_positions, self.paper_stake_usd)
        command_task = asyncio.create_task(self.commands.loop())
        try:
            while True:
                started = time.monotonic()
                try:
                    await self.run_cycle()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    status = {
                        "version": WEATHER_LIVE_PAPER_V3_VERSION,
                        "mode": "LIVE_PAPER_RESEARCH",
                        "release_sha": self.release_sha(),
                        "finished_at": time.time(),
                        "cycle_ok": False,
                        "errors": [f"UNHANDLED:{type(exc).__name__}"],
                        "forecast_policy": "RAW_GEFS_FUTURE_LOCAL_DATE_ONLY",
                        "financial_delivery": False,
                        "financial_authority": False,
                        "automatic_order_placement": False,
                        "wallet_or_order_api_loaded": False,
                    }
                    _atomic_json(self.status_path, status)
                elapsed = time.monotonic() - started
                await asyncio.sleep(max(0.0, self.interval_seconds - elapsed))
        finally:
            command_task.cancel()
            await asyncio.gather(command_task, return_exceptions=True)


async def _main(args) -> int:
    service = WeatherLivePaperV3Service(
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
            await service.quarantine_observation_blind_history()
            await asyncio.to_thread(service.positions.ensure_sent_positions, service.paper_stake_usd)
            status = await service.run_cycle()
            print(json.dumps(status, sort_keys=True, indent=2))
            return 0 if status.get("cycle_ok") else 2
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
