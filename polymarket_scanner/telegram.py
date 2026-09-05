from __future__ import annotations

import html

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
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    async def close(self):
        await self.http.aclose()

    async def send(self, text: str) -> None:
        if not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        await self.http.post(url, json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})

    async def send_signal(self, signal_id: int, s: Signal) -> None:
        icon = "🚨" if s.confidence == "ACTIONABLE" else "👀"
        edge = f"\n<b>Model edge:</b> {s.edge:.2%}" if s.edge is not None else ""
        text = (f"{icon} <b>{html.escape(s.confidence)} #{signal_id}</b>\n"
                f"<b>{html.escape(s.title)}</b>{edge}\n\n"
                f"{html.escape(s.detail)}\n\n"
                f"<a href=\"{html.escape(s.url)}\">Open on Polymarket</a>")
        other = s.metadata.get("other_url")
        if other:
            text += f"\n<a href=\"{html.escape(other)}\">Open comparison market</a>"
        await self.send(text)

    async def send_stats(self):
        st = self.store.stats()
        lines = [f"📊 <b>Polymarket scanner paper stats</b>", f"Actionable: {st['total']} | Won: {st['won']} | Lost: {st['lost']} | Open: {st['open']}", f"Paper P&amp;L: ${st['pnl']:.2f}"]
        for r in st["by_detector"]:
            lines.append(f"• {html.escape(r['detector'])}: {r['n']} alerts, ${float(r['pnl']):.2f}")
        await self.send("\n".join(lines))

    async def poll_commands(self):
        if not self.enabled:
            return
        offset = int(self.store.get_state("telegram_offset", "0") or 0)
        try:
            r = await self.http.get(f"https://api.telegram.org/bot{self.token}/getUpdates", params={"offset": offset, "timeout": 1})
            data = r.json()
            for update in data.get("result", []):
                offset = max(offset, int(update["update_id"]) + 1)
                msg = update.get("message") or {}
                if str((msg.get("chat") or {}).get("id")) != str(self.chat_id):
                    continue
                text = (msg.get("text") or "").strip().lower()
                if text in {"/stats", "stats"}:
                    await self.send_stats()
                elif text in {"/recent", "recent"}:
                    rows = self.store.recent(10)
                    body = ["🧾 <b>Recent alerts</b>"] + [f"#{x['id']} {html.escape(x['detector'])} — {html.escape(x['status'])} — {html.escape(x['title'][:70])}" for x in rows]
                    await self.send("\n".join(body))
                elif text in {"/help", "help", "/start"}:
                    await self.send("Commands: /stats, /recent, /help. Every ACTIONABLE alert is paper-tracked automatically; WATCH alerts are informational only.")
            self.store.set_state("telegram_offset", str(offset))
        except Exception:
            return
