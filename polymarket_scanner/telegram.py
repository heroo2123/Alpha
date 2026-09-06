from __future__ import annotations

import asyncio
import html
import logging
import re
import time

import httpx

from .config import settings
from .models import Signal
from .outbox import TelegramOutbox
from .store import Store

log = logging.getLogger("polybot.telegram")


class Telegram:
    """Telegram transport with isolated command and alert lanes.

    On the production VM commands run in a standalone process. In that mode the
    scanner never opens a Telegram alert connection: it persists signal IDs to a
    SQLite outbox and the command worker owns network delivery. This prevents
    scanner-side network quirks from losing or delaying alerts.
    """

    def __init__(self, store: Store, *, alert_delivery_owner: bool = False) -> None:
        self.store = store
        self.token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id
        self.alert_delivery_owner = alert_delivery_owner
        self.outbox = TelegramOutbox(settings.db_path)
        self.command_http = self._new_http_client()
        self.alert_http = self._new_http_client(connect_timeout=8.0)
        self.local_http = httpx.AsyncClient(timeout=5, trust_env=False)
        self._commands_registered = False
        self.last_command_poll_at: float | None = None
        self.last_command_error: str | None = None
        self.last_alert_error: str | None = None

    @staticmethod
    def _new_http_client(connect_timeout: float = 20.0) -> httpx.AsyncClient:
        limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
        timeout = httpx.Timeout(20.0, connect=connect_timeout)
        return httpx.AsyncClient(timeout=timeout, limits=limits, trust_env=False)

    async def _reset_alert_http(self) -> httpx.AsyncClient:
        old = self.alert_http
        self.alert_http = self._new_http_client(connect_timeout=8.0)
        try:
            await old.aclose()
        except Exception:
            pass
        return self.alert_http

    @property
    def token_enabled(self) -> bool:
        return bool(self.token)

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    async def close(self):
        await asyncio.gather(
            self.command_http.aclose(),
            self.alert_http.aclose(),
            self.local_http.aclose(),
            return_exceptions=True,
        )

    async def _post_message(
        self,
        client: httpx.AsyncClient,
        chat_id: str | int,
        text: str,
        reply_markup: dict | None = None,
        *,
        lane: str,
    ) -> None:
        if not self.token:
            return
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        for attempt in range(3):
            try:
                r = await client.post(url, json=payload)
                if r.status_code == 429:
                    retry_after = 1.0
                    try:
                        retry_after = float(r.json().get("parameters", {}).get("retry_after", 1))
                    except Exception:
                        pass
                    await asyncio.sleep(min(max(retry_after, 1.0), 15.0))
                    continue
                r.raise_for_status()
                if lane == "command":
                    self.last_command_error = None
                else:
                    self.last_alert_error = None
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if lane == "command":
                    self.last_command_error = repr(exc)
                else:
                    self.last_alert_error = repr(exc)
                    if (
                        client is self.alert_http
                        and isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError, httpx.RemoteProtocolError))
                    ):
                        client = await self._reset_alert_http()
                if attempt >= 2:
                    raise
                await asyncio.sleep(0.75 * (attempt + 1))

    async def send_to(self, chat_id: str | int, text: str, reply_markup: dict | None = None) -> None:
        await self._post_message(self.command_http, chat_id, text, reply_markup, lane="command")

    async def send(self, text: str, reply_markup: dict | None = None) -> None:
        """Send a command/control message through the command lane."""
        if self.enabled:
            await self.send_to(self.chat_id, text, reply_markup)

    async def send_alert(self, text: str, reply_markup: dict | None = None) -> None:
        """Send an alert using the delivery owner's known-good Telegram lane."""
        if self.enabled:
            client = self.command_http if self.alert_delivery_owner else self.alert_http
            await self._post_message(client, self.chat_id, text, reply_markup, lane="alert")

    async def ensure_command_menu(self) -> None:
        if not self.token or self._commands_registered:
            return
        commands = [
            {"command": "status", "description": "Live scanner health and feed status"},
            {"command": "stats", "description": "Verified paper detector performance"},
            {"command": "mystats", "description": "Trades you marked as taken"},
            {"command": "recent", "description": "Show the latest scanner alerts"},
            {"command": "taken", "description": "Show your recently marked trades"},
            {"command": "took", "description": "Mark an alert as actually traded"},
            {"command": "help", "description": "Show command help"},
        ]
        try:
            r = await self.command_http.post(
                f"https://api.telegram.org/bot{self.token}/setMyCommands",
                json={"commands": commands},
                timeout=10,
            )
            r.raise_for_status()
            self._commands_registered = True
        except Exception as exc:
            log.warning("Telegram command menu registration failed: %r", exc)

    def _buttons(self, s: Signal) -> dict | None:
        links = list(s.metadata.get("links") or [])
        if not links and s.url:
            links.append({"label": "OPEN MARKET", "url": s.url})
        if s.metadata.get("other_url"):
            links.append({"label": "OPEN COMPARISON", "url": s.metadata["other_url"]})
        rows = []
        for link in links[:4]:
            url = str(link.get("url") or "")
            label = str(link.get("label") or "OPEN")[:40]
            if url.startswith("https://"):
                rows.append([{"text": label, "url": url}])
        return {"inline_keyboard": rows} if rows else None

    def _fallback_steps(self, s: Signal) -> tuple[list[str], str | None]:
        m = s.metadata
        if s.detector == "binary_buy_both":
            y, n = m.get("yes_ask"), m.get("no_ask")
            return (["Tap OPEN MARKET below.", f"Buy YES at {float(y):.3f} or lower AND NO at {float(n):.3f} or lower.", "Use the SAME number of shares on both sides. Never take only one leg.", "If either ask moved above the alert price, SKIP."], "Both legs must fill at the quoted prices/size for the locked payoff to exist.")
        if s.detector == "neg_risk_underround":
            legs = m.get("legs") or []
            return (["Tap OPEN EVENT below.", f"Buy YES on ALL {len(legs)} listed outcomes using the SAME share count.", "Do not omit any outcome and do not start if any quoted leg has moved higher."], "The basket only works if the outcomes are exhaustive/mutually exclusive and every leg fills.")
        if s.detector == "weather_late_lock":
            ask = m.get("ask"); mx = m.get("observed_max"); unit = m.get("unit", ""); station = m.get("station", "")
            return (["Tap OPEN MARKET below.", f"Verify the official hourly table still shows a daily max of {mx}°{unit} at {station}.", f"Buy YES on the matching temperature bucket at {float(ask):.3f} or lower; if higher, SKIP.", "Hold to resolution unless you deliberately exit early."], "The fast observation feed is a proxy; the market Rules/official settlement source override it.")
        if s.detector == "duplicate_divergence":
            return (["Open both markets using the buttons.", "Compare the Rules, deadline and resolution source line-by-line.", "Only investigate the cheaper side if the contracts are truly equivalent."], "Similar wording does not prove identical settlement rules.")
        if s.detector == "wide_spread":
            return (["Open the market and inspect the live book.", "Do not cross the spread blindly.", "If you already want the position, consider a LIMIT order inside the spread and cancel it if information changes."], "A wide spread is not guaranteed edge.")
        return ([], None)

    async def send_signal(self, signal_id: int, s: Signal) -> None:
        # Production scanner: never perform Telegram network I/O here. The
        # standalone command worker owns delivery and drains this persistent queue.
        if not self.alert_delivery_owner and not settings.telegram_commands_in_app:
            priority = 0 if s.confidence == "ACTIONABLE" else 10
            self.outbox.enqueue_signal(signal_id, priority)
            self.last_alert_error = None
            return

        icon = "🚨" if s.confidence == "ACTIONABLE" else "👀"
        parts = [f"{icon} <b>{html.escape(s.confidence)} #{signal_id}</b>", f"<b>{html.escape(s.title)}</b>"]
        if s.edge is not None:
            label = "Estimated executable edge" if s.confidence == "ACTIONABLE" else "Observed/model gap (not certified profit)"
            parts.append(f"\n💰 <b>{label}:</b> {s.edge:.2%}")
        parts.append("\n" + html.escape(s.detail))
        steps = s.metadata.get("action_steps") or []
        fallback_risk = None
        if not steps:
            steps, fallback_risk = self._fallback_steps(s)
        if steps:
            parts.append("\n✅ <b>WHAT TO DO</b>")
            for i, step in enumerate(steps, 1):
                parts.append(f"{i}. {html.escape(str(step))}")
        risk = s.metadata.get("risk_note") or fallback_risk
        if risk:
            parts.append("\n⚠️ <b>CHECK / SKIP RULE</b>\n" + html.escape(str(risk)))
        max_notional = s.metadata.get("max_visible_notional_usd")
        if s.confidence == "ACTIONABLE" and max_notional is not None:
            parts.append(f"\n📏 <b>Quoted top-of-book capacity:</b> about ${float(max_notional):.2f} maximum at the confirmed prices. Do not size above this from this alert.")
        if s.confidence == "ACTIONABLE":
            parts.append(f"\n🧾 If you actually take this trade, send <code>/took {signal_id} 50</code> (replace 50 with your US$ stake). I’ll track your taken trades separately from paper signals.")
        text = "\n".join(parts)
        if len(text) > 3900:
            text = text[:3850] + "\n…\n(Open the event for the remaining leg details.)"

        if s.confidence != "ACTIONABLE":
            await self.send_alert(text, self._buttons(s))
            return

        attempt = 0
        while True:
            try:
                await self.send_alert(text, self._buttons(s))
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                attempt += 1
                self.last_alert_error = repr(exc)
                delay = min(30.0, 2.0 ** min(attempt, 5))
                log.warning("ACTIONABLE Telegram alert %s retry %s in %.1fs after %r", signal_id, attempt, delay, exc)
                await asyncio.sleep(delay)

    async def send_stats(self):
        st = self.store.stats()
        lines = [
            "📊 <b>Verified paper stats</b>",
            f"Actionable: {st['total']} | Won: {st['won']} | Lost: {st['lost']} | Open: {st['open']}",
            f"Resolved paper P&amp;L: ${st['pnl']:.2f}",
            "ℹ️ Structural arbitrage quote snapshots are no longer auto-counted as wins. They remain unscored unless a genuine outcome/execution result exists.",
        ]
        for r in st["by_detector"]:
            lines.append(f"• {html.escape(r['detector'])}: {r['n']} alerts, ${float(r['pnl']):.2f} resolved P&amp;L")
        await self.send("\n".join(lines))

    async def send_manual_stats(self):
        st = self.store.manual_stats()
        await self.send("\n".join([
            "💼 <b>Your marked-as-taken trades</b>",
            f"Trades: {st['total']} | Won: {st['won']} | Lost: {st['lost']} | Open: {st['open']}",
            f"Tracked stake: ${st['stake']:.2f}",
            f"Estimated P&amp;L (using alert entry): ${st['pnl']:.2f}",
        ]))

    @staticmethod
    def _age_text(timestamp: object) -> str:
        try:
            ts = float(timestamp)
        except (TypeError, ValueError):
            return "not completed yet"
        age = max(0, int(time.time() - ts))
        if age < 60:
            return f"{age}s ago"
        if age < 3600:
            return f"{age // 60}m {age % 60}s ago"
        return f"{age // 3600}h {(age % 3600) // 60}m ago"

    async def send_status(self):
        try:
            r = await self.local_http.get("http://127.0.0.1:8000/health")
            r.raise_for_status()
            st = r.json()
        except Exception as exc:
            await self.send(
                "🔴 <b>Scanner status unavailable</b>\n"
                "The Telegram command loop is alive, but the local /health endpoint could not be read.\n"
                f"Error: <code>{html.escape(type(exc).__name__)}</code>"
            )
            return

        ok = bool(st.get("ok"))
        icon = "🟢" if ok else "🔴"
        markets = int(st.get("markets") or 0)
        tokens = int(st.get("tokens") or 0)
        ws_workers = int(st.get("market_ws_workers") or 0)
        stations = int(st.get("stations") or 0)
        ready = int(st.get("weather_ready_stations") or 0)
        weather_refreshing = bool(st.get("weather_refreshing"))
        sports = "✅" if st.get("sports_ws") else "❌"
        crypto = "✅" if st.get("crypto_rtds") else "❌"
        last_scan = self._age_text(st.get("last_scan"))
        universe_error = st.get("universe_error")
        last_error = st.get("last_error")
        queue_depth = int(st.get("telegram_alert_queue") or 0)
        try:
            queue_depth += self.outbox.pending_count()
        except Exception:
            pass
        dropped = int(st.get("telegram_watch_dropped") or 0)
        scan_running = bool(st.get("scan_in_progress"))
        compute_seconds = st.get("last_compute_seconds")
        command_poll_age = self._age_text(st.get("telegram_last_command_poll"))
        command_error = st.get("telegram_command_error")
        alert_error = st.get("telegram_alert_error") or self.store.get_state("telegram_outbox_error", "")
        sports_error = st.get("sports_ws_error")

        if scan_running:
            detector_line = "Detector worker: <b>RUNNING in background</b>"
        elif compute_seconds is None:
            detector_line = "Detector worker: <b>waiting for first pass</b>"
        else:
            detector_line = f"Detector worker: last pass <b>{float(compute_seconds):.2f}s</b>"

        lines = [
            f"{icon} <b>Scanner status: {'HEALTHY' if ok else 'DEGRADED'}</b>",
            f"Last completed scan: <b>{html.escape(last_scan)}</b>",
            detector_line,
            f"Markets: <b>{markets:,}</b> | Tokens: <b>{tokens:,}</b>",
            f"Polymarket WS workers: <b>{ws_workers}</b>",
            f"Weather: <b>{ready}/{stations}</b> stations ready" + (" (refreshing)" if weather_refreshing else ""),
            f"Sports live feed: {sports}",
            f"Crypto live feed: {crypto}",
            f"Telegram command poll: <b>{html.escape(command_poll_age)}</b>",
            f"Telegram alert backlog: <b>{queue_depth}</b>" + (f" | WATCH skipped: <b>{dropped}</b>" if dropped else ""),
        ]
        if sports_error and not st.get("sports_ws"):
            lines.append(f"⚠️ Sports feed: <code>{html.escape(str(sports_error)[:250])}</code>")
        if command_error:
            lines.append(f"⚠️ Telegram command error: <code>{html.escape(str(command_error)[:250])}</code>")
        if alert_error:
            lines.append(f"⚠️ Telegram alert error: <code>{html.escape(str(alert_error)[:250])}</code>")
        if last_error:
            lines.append(f"⚠️ Scanner error: <code>{html.escape(str(last_error)[:350])}</code>")
        if universe_error:
            lines.append(f"⚠️ Universe refresh: <code>{html.escape(str(universe_error)[:350])}</code>")
        if not last_error and not universe_error and not command_error and not alert_error and not sports_error:
            lines.append("Errors: <b>none</b>")
        await self.send("\n".join(lines))

    async def poll_commands(self):
        if not self.token_enabled:
            return
        await self.ensure_command_menu()
        offset = int(self.store.get_state("telegram_offset", "0") or 0)
        try:
            r = await self.command_http.get(
                f"https://api.telegram.org/bot{self.token}/getUpdates",
                params={"offset": offset, "timeout": 1},
                timeout=5,
            )
            r.raise_for_status()
            data = r.json()
            if not data.get("ok", False):
                raise RuntimeError(f"Telegram getUpdates failed: {data!r}")
            self.last_command_poll_at = time.time()
            self.last_command_error = None
            for update in data.get("result", []):
                offset = max(offset, int(update["update_id"]) + 1)
                msg = update.get("message") or {}
                incoming_chat = str((msg.get("chat") or {}).get("id") or "")
                chat_type = str((msg.get("chat") or {}).get("type") or "")
                text = (msg.get("text") or "").strip(); low = text.lower()
                if low in {"/whoami", "whoami", "/start"} and incoming_chat and chat_type == "private" and not self.chat_id:
                    await self.send_to(incoming_chat, f"Your Telegram chat ID is: <code>{html.escape(incoming_chat)}</code>\n\nOn the Oracle VM run:\n<code>~/polymarket-edge-scanner/deploy/oracle/set-chat-id.sh {html.escape(incoming_chat)}</code>\n\nFor another deployment, set <code>TELEGRAM_CHAT_ID</code> to this exact number and restart the service.")
                    continue
                if not self.chat_id or incoming_chat != str(self.chat_id):
                    continue
                if low in {"/status", "status"}: await self.send_status()
                elif low in {"/stats", "stats"}: await self.send_stats()
                elif low in {"/mystats", "mystats"}: await self.send_manual_stats()
                elif low in {"/recent", "recent"}:
                    rows = self.store.recent(10)
                    body = ["🧾 <b>Recent alerts</b>"] + [f"#{x['id']} {html.escape(x['detector'])} — {html.escape(x['status'])} — {html.escape(x['title'][:70])}" for x in rows]
                    await self.send("\n".join(body))
                elif low in {"/taken", "taken"}:
                    rows = self.store.recent_manual(10)
                    body = ["💼 <b>Recently marked taken</b>"] + [f"Trade #{x['id']} / alert #{x['signal_id']} — ${float(x['stake']):.2f} — {html.escape(x['status'])} — {html.escape(x['title'][:60])}" for x in rows]
                    await self.send("\n".join(body))
                elif low.startswith("/took"):
                    mm = re.match(r"^/took\s+(\d+)\s+([0-9]+(?:\.[0-9]+)?)$", low)
                    if not mm:
                        await self.send("Use: <code>/took ALERT_ID STAKE_USD</code> — example: <code>/took 137 50</code>.")
                    else:
                        try:
                            row = self.store.record_manual(int(mm.group(1)), float(mm.group(2)))
                            await self.send(f"✅ Recorded trade #{row['id']} from alert #{row['signal_id']} with ${row['stake']:.2f} stake. Entry/P&amp;L tracking uses the alert's executable-cost estimate.")
                        except ValueError as exc:
                            await self.send(f"Could not record that trade: {html.escape(str(exc))}")
                elif low in {"/help", "help", "/start"}:
                    await self.send("Commands:\n/status — live scanner health and feed status\n/stats — verified paper detector performance\n/mystats — trades you marked as taken\n/recent — recent alerts\n/taken — your recent taken trades\n/took ALERT_ID STAKE_USD — mark an alert you actually traded\n/help — this list")
            self.store.set_state("telegram_offset", str(offset))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_command_error = repr(exc)
            log.warning("Telegram getUpdates/command handling failed: %r", exc)
            raise
