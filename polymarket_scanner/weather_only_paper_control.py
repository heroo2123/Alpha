from __future__ import annotations

"""Telegram command/control and automatic settlement for weather LIVE PAPER.

This module has no order/wallet API.  Commands inspect the isolated paper database;
settlement reads public Gamma closed-market payout vectors and maps the exact token
IDs captured by each simulated position.
"""

import asyncio
import html
import json
import math
import time
from pathlib import Path

import httpx

from .settlement import exact_token_payout
from .weather_only_paper_positions import WeatherPaperPositionStore


GAMMA = "https://gamma-api.polymarket.com"
PAPER_COMMAND_VERSION = "weather_paper_commands_v1_auto_positions_stats"
PAPER_SETTLEMENT_VERSION = "weather_paper_settlement_v1_gamma_closed_exact_token"


def _age(timestamp: object) -> str:
    try:
        ts = float(timestamp)
    except (TypeError, ValueError, OverflowError):
        return "unknown"
    if not math.isfinite(ts) or ts <= 0.0:
        return "unknown"
    seconds = max(0, int(time.time() - ts))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s ago"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m ago"


def _pct(value: object) -> str:
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return "n/a"
    return f"{100.0 * number:.1f}%" if math.isfinite(number) else "n/a"


def _price(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return "n/a"
    if not math.isfinite(number):
        return "n/a"
    if number < 0.10:
        return f"{100.0 * number:.3f}¢"
    return f"${number:.4f}"


def _short(value: object, length: int = 52) -> str:
    text = str(value or "").strip().replace("\n", " ")
    if len(text) <= length:
        return text
    return text[: max(1, length - 1)] + "…"


class PublicGammaSettlementClient:
    """Public read-only Gamma market lookup used only for objective settlement."""

    def __init__(self) -> None:
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(12.0, connect=6.0),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4, keepalive_expiry=60.0),
            headers={"User-Agent": "polymarket-weather-paper-settlement/1.0"},
            trust_env=False,
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def market_by_id(self, market_id: str) -> dict | None:
        mid = str(market_id or "").strip()
        if not mid:
            return None
        for attempt in range(3):
            try:
                response = await self.http.get(f"{GAMMA}/markets/{mid}")
            except httpx.HTTPError:
                if attempt >= 2:
                    return None
                await asyncio.sleep(0.4 * (attempt + 1))
                continue
            if response.status_code == 404:
                return None
            if response.status_code == 429 or response.status_code >= 500:
                if attempt >= 2:
                    return None
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            if response.status_code >= 400:
                return None
            try:
                body = response.json()
            except ValueError:
                return None
            return body if isinstance(body, dict) else None
        return None


class WeatherPaperSettlementEngine:
    def __init__(self, *, store: WeatherPaperPositionStore, telegram, gamma: PublicGammaSettlementClient | None = None) -> None:
        self.store = store
        self.telegram = telegram
        self.gamma = gamma or PublicGammaSettlementClient()
        self._owns_gamma = gamma is None

    async def close(self) -> None:
        if self._owns_gamma:
            await self.gamma.close()

    @staticmethod
    def _resolution_message(position: dict) -> str:
        status = str(position.get("status") or "")
        icon = "✅" if status == "WON" else "❌" if status == "LOST" else "🟡"
        pnl = float(position.get("pnl") or 0.0)
        roi = position.get("roi")
        side = html.escape(str(position.get("side") or ""))
        title = html.escape(_short(position.get("title"), 80))
        payout = float(position.get("settlement_payout_per_unit") or 0.0)
        return "\n".join([
            f"{icon} <b>PAPER POSITION RESOLVED — {html.escape(status)}</b>",
            f"Position <b>#{int(position['id'])}</b> / signal #{int(position['signal_id'])}",
            f"<b>{title}</b>",
            f"Paper side: <b>{side}</b>",
            "",
            f"Entry cost/unit: <b>{_price(position.get('entry_cost_per_unit'))}</b>",
            f"Simulated capital used: <b>${float(position.get('capital_used') or 0.0):.2f}</b>",
            f"Final payout/unit: <b>${payout:.4f}</b>",
            f"Paper proceeds: <b>${float(position.get('proceeds') or 0.0):.2f}</b>",
            f"Paper P&amp;L: <b>{'+' if pnl >= 0 else ''}${pnl:.2f}</b>",
            f"Return on simulated capital: <b>{_pct(roi)}</b>",
            "",
            "📒 Simulated fill only — no real order was placed.",
        ])

    async def _notify_resolved(self) -> tuple[int, list[str]]:
        sent = 0
        errors: list[str] = []
        rows = await asyncio.to_thread(self.store.resolved_without_notification, 50)
        for position in rows:
            try:
                message_id = await self.telegram.send_html(self._resolution_message(position))
            except Exception as exc:
                errors.append(f"SETTLEMENT_TELEGRAM:{type(exc).__name__}")
                continue
            await asyncio.to_thread(
                self.store.mark_settlement_notified, int(position["id"]), int(message_id)
            )
            sent += 1
        return sent, errors

    async def settle_once(self) -> dict:
        positions = await asyncio.to_thread(self.store.open_positions, 500)
        market_ids = sorted({
            str(leg.get("market_id") or "")
            for position in positions
            for leg in position.get("legs") or []
            if str(leg.get("market_id") or "")
        })
        semaphore = asyncio.Semaphore(6)

        async def fetch(mid: str):
            async with semaphore:
                return mid, await self.gamma.market_by_id(mid)

        results = await asyncio.gather(*(fetch(mid) for mid in market_ids), return_exceptions=True)
        markets = {
            mid: market
            for result in results
            if isinstance(result, tuple)
            for mid, market in [result]
            if isinstance(market, dict)
        }

        resolved = 0
        checked = 0
        errors: list[str] = []
        for position in positions:
            checked += 1
            payouts: list[dict] = []
            complete = True
            for leg in position.get("legs") or []:
                mid = str(leg.get("market_id") or "")
                token = str(leg.get("token_id") or "")
                market = markets.get(mid)
                payout = exact_token_payout(token, market) if market is not None else None
                if payout is None:
                    complete = False
                    break
                payouts.append({"market_id": mid, "token_id": token, "payout": float(payout)})
            if not complete or not payouts:
                continue
            payout_per_unit = sum(float(row["payout"]) for row in payouts)
            evidence = {
                "version": PAPER_SETTLEMENT_VERSION,
                "source": "GAMMA_CLOSED_MARKET_EXACT_TOKEN_PAYOUT",
                "checked_at": time.time(),
                "legs": payouts,
                "financial_authority": False,
            }
            try:
                row = await asyncio.to_thread(
                    self.store.resolve_position,
                    int(position["id"]),
                    payout_per_unit,
                    evidence,
                )
            except Exception as exc:
                errors.append(f"SETTLEMENT:{position.get('id')}:{type(exc).__name__}")
                continue
            if row is not None:
                resolved += 1

        notified, notification_errors = await self._notify_resolved()
        errors.extend(notification_errors)
        return {
            "version": PAPER_SETTLEMENT_VERSION,
            "open_checked": checked,
            "market_ids_checked": len(market_ids),
            "resolved_now": resolved,
            "resolution_messages_sent": notified,
            "errors": errors,
        }


class WeatherPaperCommandController:
    """Private-chat Telegram command loop for the weather paper bot."""

    def __init__(
        self,
        *,
        telegram,
        store: WeatherPaperPositionStore,
        status_path: str | Path,
        paper_stake_usd: float,
    ) -> None:
        self.telegram = telegram
        self.store = store
        self.status_path = Path(status_path)
        self.paper_stake_usd = float(paper_stake_usd)
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=5.0),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=1, keepalive_expiry=30.0),
            trust_env=False,
        )

    async def close(self) -> None:
        await self.http.aclose()

    @property
    def endpoint(self) -> str:
        return f"https://api.telegram.org/bot{self.telegram.token}"

    async def register_menu(self) -> None:
        commands = [
            {"command": "status", "description": "Weather bot health + paper tracker"},
            {"command": "stats", "description": "Paper wins, losses, P&L and ROI"},
            {"command": "positions", "description": "Open simulated positions"},
            {"command": "history", "description": "Recently resolved paper positions"},
            {"command": "recent", "description": "Recent Telegram signals"},
            {"command": "help", "description": "Show all weather paper commands"},
        ]
        response = await self.http.post(f"{self.endpoint}/setMyCommands", json={"commands": commands})
        response.raise_for_status()

    async def bootstrap_offset(self) -> None:
        if self.store.get_state("telegram_command_offset", ""):
            return
        # Discard stale updates from the old inactive command worker. Only commands
        # sent after this paper controller starts should execute.
        response = await self.http.get(
            f"{self.endpoint}/getUpdates", params={"offset": -1, "limit": 1, "timeout": 0}
        )
        response.raise_for_status()
        body = response.json()
        rows = body.get("result") if isinstance(body, dict) and body.get("ok") is True else []
        offset = int(rows[-1]["update_id"]) + 1 if isinstance(rows, list) and rows else 0
        self.store.set_state("telegram_command_offset", offset)

    def _read_status(self) -> dict:
        try:
            value = json.loads(self.status_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    def _status_text(self) -> str:
        status = self._read_status()
        stats = self.store.stats()
        healthy = bool(status.get("cycle_ok"))
        icon = "🟢" if healthy else "🔴"
        lines = [
            f"{icon} <b>WEATHER PAPER BOT — {'HEALTHY' if healthy else 'DEGRADED'}</b>",
            f"Last completed scan: <b>{html.escape(_age(status.get('finished_at')))}</b>",
            f"Last cycle time: <b>{float(status.get('cycle_seconds') or 0.0):.1f}s</b>",
            f"Weather events discovered: <b>{int(status.get('discovered_weather_events') or 0)}</b>",
            f"Forecast events evaluated: <b>{int(status.get('forecast_events_evaluated') or 0)}</b>",
            f"Signals sent last cycle: <b>{int(status.get('telegram_sent_count') or 0)}</b>",
            "",
            "📒 <b>Automatic paper tracker</b>",
            f"Target per signal: <b>${self.paper_stake_usd:.2f}</b>, capped by captured visible top-of-book size",
            f"Open positions: <b>{int(stats['open'])}</b> | Resolved: <b>{int(stats['resolved'])}</b> | No-fill: <b>{int(stats['no_fill'])}</b>",
            f"Resolved: <b>{int(stats['won'])}W / {int(stats['lost'])}L</b> | Realized paper P&amp;L: <b>${float(stats['pnl']):+.2f}</b>",
            f"Resolved ROI: <b>{_pct(stats.get('resolved_roi'))}</b>",
            "",
            "🤖 Telegram commands: <b>ONLINE</b>",
            "🚫 Real orders: <b>DISABLED</b>",
        ]
        errors = status.get("errors") or []
        if errors:
            lines.append("⚠️ Last errors: <code>" + html.escape(", ".join(str(x) for x in errors)[:500]) + "</code>")
        return "\n".join(lines)

    def _stats_text(self) -> str:
        stats = self.store.stats()
        lines = [
            "📊 <b>WEATHER PAPER PERFORMANCE</b>",
            f"Tracked positions: <b>{int(stats['total'])}</b>",
            f"Open: <b>{int(stats['open'])}</b> | Resolved: <b>{int(stats['resolved'])}</b> | No-fill: <b>{int(stats['no_fill'])}</b>",
            f"Results: <b>{int(stats['won'])}W / {int(stats['lost'])}L / {int(stats['partial'])} partial</b>",
            f"Win rate (full W/L only): <b>{_pct(stats.get('win_rate'))}</b>",
            "",
            f"Capital in resolved simulations: <b>${float(stats['resolved_capital']):.2f}</b>",
            f"Resolved proceeds: <b>${float(stats['resolved_proceeds']):.2f}</b>",
            f"Realized paper P&amp;L: <b>${float(stats['pnl']):+.2f}</b>",
            f"Resolved ROI: <b>{_pct(stats.get('resolved_roi'))}</b>",
            f"Capital currently open: <b>${float(stats['open_capital']):.2f}</b>",
            "",
            f"Sizing rule: target <b>${self.paper_stake_usd:.2f}</b> per Telegram signal, capped to the exact visible top-of-book quantity captured with the alert.",
            "No slippage/queue fill beyond that captured top level is invented.",
        ]
        if stats.get("by_lane"):
            lines.append("\n<b>By signal lane</b>")
            for row in stats["by_lane"]:
                lines.append(
                    f"• {html.escape(str(row.get('lane') or 'unknown'))}: "
                    f"{int(row.get('total') or 0)} positions | "
                    f"{int(row.get('won') or 0)}W/{int(row.get('lost') or 0)}L | "
                    f"P&amp;L ${float(row.get('pnl') or 0.0):+.2f}"
                )
        return "\n".join(lines)

    def _positions_text(self) -> str:
        rows = self.store.open_positions(10)
        lines = ["📂 <b>OPEN PAPER POSITIONS</b>"]
        if not rows:
            lines.append("No simulated positions are currently open.")
            return "\n".join(lines)
        for row in rows:
            lines.extend([
                "",
                f"<b>#{int(row['id'])}</b> / signal #{int(row['signal_id'])} — <b>{html.escape(str(row['side']))}</b>",
                html.escape(_short(row.get("title"), 65)),
                f"Entry: <b>{_price(row.get('entry_cost_per_unit'))}</b> | capital: <b>${float(row.get('capital_used') or 0.0):.2f}</b>",
                f"Opened: {html.escape(_age(row.get('opened_at')))}",
            ])
        return "\n".join(lines)

    def _history_text(self) -> str:
        rows = self.store.recent_positions(10, resolved_only=True)
        lines = ["🏁 <b>RECENT PAPER RESULTS</b>"]
        if not rows:
            lines.append("No paper positions have resolved yet.")
            return "\n".join(lines)
        for row in rows:
            pnl = float(row.get("pnl") or 0.0)
            lines.extend([
                "",
                f"<b>#{int(row['id'])} {html.escape(str(row['status']))}</b> — {html.escape(str(row['side']))}",
                html.escape(_short(row.get("title"), 62)),
                f"Capital ${float(row.get('capital_used') or 0.0):.2f} → P&amp;L <b>{'+' if pnl >= 0 else ''}${pnl:.2f}</b> ({_pct(row.get('roi'))})",
            ])
        return "\n".join(lines)

    def _recent_text(self) -> str:
        rows = self.store.recent_signals(10)
        lines = ["🧾 <b>RECENT WEATHER SIGNALS</b>"]
        if not rows:
            lines.append("No signals stored yet.")
            return "\n".join(lines)
        for row in rows:
            lines.append(
                f"#{int(row['id'])} {html.escape(str(row.get('side') or 'BASKET'))} "
                f"@ {_price(row.get('entry_cost'))} — paper position: "
                f"<b>{html.escape(str(row.get('position_status') or 'NOT OPENED'))}</b>"
            )
        return "\n".join(lines)

    @staticmethod
    def _help_text() -> str:
        return "\n".join([
            "🤖 <b>WEATHER PAPER COMMANDS</b>",
            "/status — bot health, last scan and tracker status",
            "/stats — wins/losses, paper P&amp;L and ROI",
            "/positions — currently open simulated positions",
            "/history — recently resolved positions",
            "/recent — latest Telegram signals and tracking state",
            "/help — this list",
            "",
            "Every delivered signal is automatically paper-taken. You do not need /took or /filled in this mode.",
        ])

    async def _handle(self, text: str) -> None:
        low = str(text or "").strip().lower()
        first = low.split(maxsplit=1)[0] if low else ""
        command = first.split("@", 1)[0]
        if command in {"/status", "status"}:
            body = self._status_text()
        elif command in {"/stats", "stats", "/mystats", "mystats"}:
            body = self._stats_text()
        elif command in {"/positions", "positions", "/open", "open", "/taken", "taken"}:
            body = self._positions_text()
        elif command in {"/history", "history"}:
            body = self._history_text()
        elif command in {"/recent", "recent"}:
            body = self._recent_text()
        elif command in {"/took", "took", "/filled", "filled"}:
            body = (
                "📒 <b>Paper mode auto-tracks every delivered signal.</b>\n"
                "You do not need to mark trades manually. Use /positions or /stats to see the simulated ledger."
            )
        else:
            body = self._help_text()
        await self.telegram.send_html(body)

    async def poll_once(self) -> None:
        offset = int(self.store.get_state("telegram_command_offset", "0") or 0)
        response = await self.http.get(
            f"{self.endpoint}/getUpdates",
            params={"offset": offset, "timeout": 2, "allowed_updates": '["message"]'},
            timeout=6,
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or body.get("ok") is not True:
            raise RuntimeError("PAPER_TELEGRAM_GETUPDATES_REJECTED")
        self.store.set_state("telegram_command_last_poll_at", time.time())
        self.store.set_state("telegram_command_last_error", "")
        for update in body.get("result") or []:
            update_id = int(update.get("update_id") or 0)
            if update_id >= offset:
                offset = update_id + 1
                # A command response is informational; prefer at-most-once command
                # execution over repeated replies if the response send later fails.
                self.store.set_state("telegram_command_offset", offset)
            message = update.get("message") or {}
            incoming_chat = str((message.get("chat") or {}).get("id") or "")
            if not self.telegram.chat_id or incoming_chat != str(self.telegram.chat_id):
                continue
            text = str(message.get("text") or "").strip()
            if text:
                await self._handle(text)

    async def loop(self) -> None:
        try:
            await self.register_menu()
            await self.bootstrap_offset()
        except Exception as exc:
            self.store.set_state("telegram_command_last_error", type(exc).__name__)
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.store.set_state("telegram_command_last_error", type(exc).__name__)
                await asyncio.sleep(1.0)
            else:
                await asyncio.sleep(0.10)
