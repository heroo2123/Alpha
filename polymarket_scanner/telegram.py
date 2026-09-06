from __future__ import annotations

import html
import re
import time

import httpx

from .config import settings
from .models import Signal
from .store import Store


class Telegram:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id
        self.http = httpx.AsyncClient(timeout=20)

    @property
    def token_enabled(self) -> bool:
        return bool(self.token)

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    async def close(self):
        await self.http.aclose()

    async def send_to(self, chat_id: str | int, text: str, reply_markup: dict | None = None) -> None:
        if not self.token:
            return
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        r = await self.http.post(f"https://api.telegram.org/bot{self.token}/sendMessage", json=payload)
        r.raise_for_status()

    async def send(self, text: str, reply_markup: dict | None = None) -> None:
        if self.enabled:
            await self.send_to(self.chat_id, text, reply_markup)

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
            legs=m.get("legs") or []
            return (["Tap OPEN EVENT below.", f"Buy YES on ALL {len(legs)} listed outcomes using the SAME share count.", "Do not omit any outcome and do not start if any quoted leg has moved higher."], "The basket only works if the outcomes are exhaustive/mutually exclusive and every leg fills.")
        if s.detector == "weather_late_lock":
            ask=m.get("ask"); mx=m.get("observed_max"); unit=m.get("unit",""); station=m.get("station","")
            return (["Tap OPEN MARKET below.", f"Verify the official hourly table still shows a daily max of {mx}°{unit} at {station}.", f"Buy YES on the matching temperature bucket at {float(ask):.3f} or lower; if higher, SKIP.", "Hold to resolution unless you deliberately exit earlier."], "The fast observation feed is a proxy; the market Rules/official settlement source override it.")
        if s.detector == "duplicate_divergence":
            return (["Open both markets using the buttons.", "Compare the Rules, deadline and resolution source line-by-line.", "Only investigate the cheaper side if the contracts are truly equivalent."], "Similar wording does not prove identical settlement rules.")
        if s.detector == "wide_spread":
            return (["Open the market and inspect the live book.", "Do not cross the spread blindly.", "If you already want the position, consider a LIMIT order inside the spread and cancel it if information changes."], "A wide spread is not guaranteed edge.")
        return ([], None)

    async def send_signal(self, signal_id: int, s: Signal) -> None:
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
        await self.send(text, self._buttons(s))

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
        await self.send("\n".join(["💼 <b>Your marked-as-taken trades</b>", f"Trades: {st['total']} | Won: {st['won']} | Lost: {st['lost']} | Open: {st['open']}", f"Tracked stake: ${st['stake']:.2f}", f"Estimated P&amp;L (using alert entry): ${st['pnl']:.2f}"]))

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
            r = await self.http.get("http://127.0.0.1:8000/health", timeout=5)
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

        lines = [
            f"{icon} <b>Scanner status: {'HEALTHY' if ok else 'DEGRADED'}</b>",
            f"Last completed scan: <b>{html.escape(last_scan)}</b>",
            f"Markets: <b>{markets:,}</b> | Tokens: <b>{tokens:,}</b>",
            f"Polymarket WS workers: <b>{ws_workers}</b>",
            f"Weather: <b>{ready}/{stations}</b> stations ready" + (" (refreshing)" if weather_refreshing else ""),
            f"Sports live feed: {sports}",
            f"Crypto live feed: {crypto}",
        ]
        if last_error:
            lines.append(f"⚠️ Scanner error: <code>{html.escape(str(last_error)[:350])}</code>")
        if universe_error:
            lines.append(f"⚠️ Universe refresh: <code>{html.escape(str(universe_error)[:350])}</code>")
        if not last_error and not universe_error:
            lines.append("Errors: <b>none</b>")
        await self.send("\n".join(lines))

    async def poll_commands(self):
        if not self.token_enabled:
            return
        offset = int(self.store.get_state("telegram_offset", "0") or 0)
        try:
            r = await self.http.get(f"https://api.telegram.org/bot{self.token}/getUpdates", params={"offset": offset, "timeout": 1})
            data = r.json()
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
                    rows = self.store.recent(10); body = ["🧾 <b>Recent alerts</b>"] + [f"#{x['id']} {html.escape(x['detector'])} — {html.escape(x['status'])} — {html.escape(x['title'][:70])}" for x in rows]; await self.send("\n".join(body))
                elif low in {"/taken", "taken"}:
                    rows = self.store.recent_manual(10); body = ["💼 <b>Recently marked taken</b>"] + [f"Trade #{x['id']} / alert #{x['signal_id']} — ${float(x['stake']):.2f} — {html.escape(x['status'])} — {html.escape(x['title'][:60])}" for x in rows]; await self.send("\n".join(body))
                elif low.startswith("/took"):
                    mm = re.match(r"^/took\s+(\d+)\s+([0-9]+(?:\.[0-9]+)?)$", low)
                    if not mm: await self.send("Use: <code>/took ALERT_ID STAKE_USD</code> — example: <code>/took 137 50</code>.")
                    else:
                        try:
                            row = self.store.record_manual(int(mm.group(1)), float(mm.group(2))); await self.send(f"✅ Recorded trade #{row['id']} from alert #{row['signal_id']} with ${row['stake']:.2f} stake. Entry/P&amp;L tracking uses the alert's executable-cost estimate.")
                        except ValueError as exc: await self.send(f"Could not record that trade: {html.escape(str(exc))}")
                elif low in {"/help", "help", "/start"}:
                    await self.send("Commands:\n/status — live scanner health and feed status\n/stats — verified paper detector performance\n/mystats — trades you marked as taken\n/recent — recent alerts\n/taken — your recent taken trades\n/took ALERT_ID STAKE_USD — mark an alert you actually traded\n/help — this list")
            self.store.set_state("telegram_offset", str(offset))
        except Exception:
            return
