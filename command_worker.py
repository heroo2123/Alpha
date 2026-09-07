from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import time

from polymarket_scanner.config import settings
from polymarket_scanner.outbox import TelegramOutbox
from polymarket_scanner.store import Store
from polymarket_scanner.telegram import Telegram

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("polybot.command_worker")


class _DatabaseHealthResponse:
    def __init__(self, data: dict) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._data


class _DatabaseHealthClient:
    """Drop-in replacement for Telegram.local_http using shared SQLite state."""

    def __init__(self, store: Store, tg: Telegram) -> None:
        self.store = store
        self.tg = tg

    async def get(self, _url: str) -> _DatabaseHealthResponse:
        raw = await asyncio.to_thread(self.store.get_state, "scanner_health_snapshot", "")
        if not raw:
            data = {
                "ok": False, "started": None, "last_scan": None, "markets": 0, "tokens": 0,
                "stations": 0, "weather_ready_stations": 0, "weather_refreshing": False,
                "market_ws_workers": 0, "sports_ws": False, "crypto_rtds": False,
                "telegram_alert_queue": 0, "telegram_watch_dropped": 0,
                "scan_in_progress": False, "last_compute_seconds": None,
                "last_error": "scanner heartbeat is warming up", "universe_error": None,
                "telegram_alert_error": None, "sports_ws_error": None, "snapshot_at": None,
            }
        else:
            try:
                decoded = json.loads(raw)
                data = dict(decoded) if isinstance(decoded, dict) else {}
            except Exception:
                data = {}
            if not data:
                data = {
                    "ok": False, "started": None, "last_scan": None, "markets": 0, "tokens": 0,
                    "stations": 0, "weather_ready_stations": 0, "weather_refreshing": False,
                    "market_ws_workers": 0, "sports_ws": False, "crypto_rtds": False,
                    "telegram_alert_queue": 0, "telegram_watch_dropped": 0,
                    "scan_in_progress": False, "last_compute_seconds": None,
                    "last_error": "scanner heartbeat record is invalid", "universe_error": None,
                    "telegram_alert_error": None, "sports_ws_error": None, "snapshot_at": None,
                }

        data = dict(data)
        data["telegram_last_command_poll"] = self.tg.last_command_poll_at
        data["telegram_command_error"] = self.tg.last_command_error

        try:
            snapshot_at = float(data.get("snapshot_at"))
        except (TypeError, ValueError):
            snapshot_at = 0.0
        age = max(0.0, time.time() - snapshot_at) if snapshot_at else float("inf")
        if age > 15.0:
            previous = data.get("last_error")
            stale = "scanner heartbeat missing timestamp" if not snapshot_at else f"scanner heartbeat stale ({age:.0f}s old)"
            data["ok"] = False
            data["last_error"] = f"{stale}; previous: {previous}" if previous else stale

        return _DatabaseHealthResponse(data)

    async def aclose(self) -> None:
        return None


class AuditTelegram(Telegram):
    """Telegram command transport whose /stats is an evidence audit, not a counter."""

    @staticmethod
    def _pct(value: object) -> str:
        if value is None:
            return "n/a"
        try:
            return f"{float(value):.1%}"
        except (TypeError, ValueError):
            return "n/a"

    async def send_stats(self):
        st = await asyncio.to_thread(self.store.stats)
        audit = st.get("audit") or {}
        detectors = st.get("detectors") or []

        resolved = int(audit.get("directional_resolved") or 0)
        won = int(audit.get("directional_won") or 0)
        lost = int(audit.get("directional_lost") or 0)
        lines = [
            "📊 <b>Signal audit — evidence, not alert count</b>",
            f"Stored alerts: <b>{int(audit.get('all_alerts') or 0):,}</b> | ACTIONABLE: <b>{int(audit.get('actionable') or 0):,}</b> | WATCH: <b>{int(audit.get('watch') or 0):,}</b>",
            f"Resolution-scoreable: <b>{int(audit.get('directional_total') or 0):,}</b> | Resolved: <b>{resolved:,}</b> | Open: <b>{int(audit.get('directional_open') or 0):,}</b>",
            f"Resolved outcomes: <b>{won}W / {lost}L</b> | Win rate: <b>{self._pct(audit.get('win_rate'))}</b>",
            f"Resolved paper P&amp;L: <b>${float(audit.get('resolved_pnl') or 0.0):.2f}</b> | Avg return/resolved signal: <b>{self._pct(audit.get('avg_resolved_return'))}</b>",
            f"Structural ACTIONABLEs: <b>{int(audit.get('structural_actionable_unverified') or 0):,}</b> execution-unverified — <b>NOT counted as profit</b>",
            f"Research-only WATCHs: <b>{int(audit.get('research_unscored') or 0):,}</b> — no P&amp;L claim",
        ]
        legacy = int(audit.get("legacy_excluded") or 0)
        if legacy:
            lines.append(f"Legacy synthetic structural rows excluded: <b>{legacy:,}</b>")
        exp_total = int(audit.get("experimental_total") or 0)
        if exp_total:
            lines.append(
                f"Friend-weather experiment: <b>{int(audit.get('experimental_resolved') or 0):,}/{exp_total:,}</b> resolved"
            )

        lines.append("\n<b>By detector</b>")
        for row in detectors[:14]:
            detector = html.escape(str(row.get("detector") or "unknown"))
            evidence = str(row.get("evidence") or "")
            n = int(row.get("n") or 0)
            actionable = int(row.get("actionable") or 0)
            watch = int(row.get("watch") or 0)
            r = int(row.get("resolved") or 0)
            rw = int(row.get("won") or 0)
            rl = int(row.get("lost") or 0)
            avg_edge = self._pct(row.get("avg_edge"))

            if evidence == "RESOLUTION_SCORED":
                lines.append(
                    f"• <b>{detector}</b> [SCORED]: {actionable} A | {r} resolved ({rw}W/{rl}L) | "
                    f"P&amp;L ${float(row.get('pnl') or 0.0):.2f} | avg return {self._pct(row.get('avg_return'))}"
                )
            elif evidence == "EXPERIMENTAL_RESOLUTION":
                lines.append(
                    f"• <b>{detector}</b> [EXPERIMENT]: {n} WATCH | {r} resolved ({rw}W/{rl}L) | "
                    f"paper P&amp;L ${float(row.get('pnl') or 0.0):.2f}"
                )
            elif evidence == "EXECUTION_UNVERIFIED":
                capacity = row.get("avg_visible_notional")
                cap_text = f" | avg quoted capacity ${float(capacity):.0f}" if capacity is not None else ""
                lines.append(
                    f"• <b>{detector}</b> [UNVERIFIED EXECUTION]: {actionable} A / {watch} W | avg quoted edge {avg_edge}{cap_text}"
                )
            else:
                lines.append(f"• <b>{detector}</b> [RESEARCH]: {n} alerts ({watch} WATCH) | unscored")

        if len(detectors) > 14:
            lines.append(f"… {len(detectors) - 14} smaller detector groups omitted from this Telegram view.")

        lines.extend([
            "\nℹ️ <b>Interpretation</b>",
            "SCORED = the selected outcome later resolved WIN/LOSS.",
            "EXPERIMENT = outcome can be scored, but the detector is not yet promoted to ACTIONABLE.",
            "UNVERIFIED EXECUTION = quote math may be valid, but we do not pretend every leg filled.",
            "RESEARCH = discovery/noise monitor; alert count is not evidence of profit.",
        ])
        text = "\n".join(lines)
        if len(text) > 3900:
            text = text[:3850] + "\n…\n(Report truncated; the database retains the full audit.)"
        await self.send(text)


async def command_loop(tg: Telegram) -> None:
    while True:
        try:
            await tg.poll_commands()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Telegram command poll failed: %r", exc)
            await asyncio.sleep(1.0)
        else:
            await asyncio.sleep(0.10)


async def alert_delivery_loop(store: Store, tg: Telegram, outbox: TelegramOutbox) -> None:
    """Drain scanner alerts through a dedicated Telegram transport."""
    last_sent = 0.0
    while True:
        try:
            item = await asyncio.to_thread(outbox.next_due)
            if not item:
                await asyncio.sleep(0.20)
                continue

            signal_row = await asyncio.to_thread(store.get_signal, int(item["signal_id"]))
            if not signal_row:
                await asyncio.to_thread(outbox.mark_failed, int(item["id"]), "signal row missing")
                await asyncio.sleep(0.25)
                continue

            signal = outbox.signal_from_row(signal_row)
            interval = (
                settings.telegram_actionable_min_interval_seconds
                if signal.confidence == "ACTIONABLE"
                else settings.telegram_watch_min_interval_seconds
            )
            wait_for = interval - (time.monotonic() - last_sent)
            if wait_for > 0:
                await asyncio.sleep(wait_for)

            try:
                await tg.send_signal(int(item["signal_id"]), signal)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await asyncio.to_thread(outbox.mark_failed, int(item["id"]), repr(exc))
                await asyncio.to_thread(store.set_state, "telegram_outbox_error", repr(exc))
                log.warning("persistent Telegram alert %s failed: %r", item["signal_id"], exc)
                await asyncio.sleep(0.25)
                continue

            await asyncio.to_thread(outbox.mark_sent, int(item["id"]))
            await asyncio.to_thread(store.set_state, "telegram_outbox_error", "")
            last_sent = time.monotonic()
            log.info("delivered persistent Telegram alert %s", item["signal_id"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Telegram outbox delivery loop failed: %r", exc)
            await asyncio.sleep(1.0)


async def main() -> None:
    """Run commands and alerts as isolated lanes outside the scanner process."""
    store = Store(settings.db_path)
    outbox = TelegramOutbox(settings.db_path)

    # Two Telegram instances deliberately create two independent connection pools.
    # An alert timeout can therefore never consume or poison the command lane.
    command_tg = AuditTelegram(store, alert_delivery_owner=False)
    alert_tg = Telegram(store, alert_delivery_owner=True)
    if not command_tg.token_enabled:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    await command_tg.local_http.aclose()
    command_tg.local_http = _DatabaseHealthClient(store, command_tg)  # type: ignore[assignment]

    log.info("standalone Telegram worker started with isolated command lane + persistent alert outbox + evidence audit stats")
    command_task = asyncio.create_task(command_loop(command_tg))
    alert_task = asyncio.create_task(alert_delivery_loop(store, alert_tg, outbox))
    try:
        await asyncio.gather(command_task, alert_task)
    finally:
        command_task.cancel()
        alert_task.cancel()
        await asyncio.gather(command_task, alert_task, return_exceptions=True)
        await asyncio.gather(command_tg.close(), alert_tg.close(), return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
