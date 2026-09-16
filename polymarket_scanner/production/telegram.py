"""Authenticated operator interaction, independent of signing credentials."""
from __future__ import annotations

import html
import json
import re
import time
from datetime import datetime, timezone

import httpx

from .config import ConfigurationError
from .io import private_json


class Telegram:
    def __init__(self, path, *, transport=None):
        config = private_json(path)
        token = config.get("token")
        if not isinstance(token, str) or not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
            raise ConfigurationError("INVALID_TELEGRAM_TOKEN")
        self.chat_id = str(config.get("chat_id", ""))
        self.operators = {str(x) for x in config.get("operator_user_ids", [])}
        if not re.fullmatch(r"-?[0-9]+", self.chat_id) or not self.operators or any(not x.isdigit() for x in self.operators):
            raise ConfigurationError("TELEGRAM_CHAT_AND_OPERATOR_IDS_REQUIRED")
        self._base = "https://api.telegram.org/bot" + token
        self.delivery_identity = {"bot_id": str(int(token.split(":", 1)[0])), "chat_id": self.chat_id}
        self.http = httpx.AsyncClient(timeout=15, trust_env=False, transport=transport)

    async def close(self):
        await self.http.aclose()

    async def request(self, method, payload):
        # No raw URLs/bodies/exceptions escape this boundary. SendMessage is not
        # automatically retried: a timeout may follow successful delivery.
        try:
            response = await self.http.post(self._base + "/" + method, json=payload)
            value = response.json()
            if not isinstance(value, dict):
                return None
            return value
        except Exception:
            return None

    async def send(self, text):
        if len(text.encode("utf-16-le")) // 2 > 4000:
            raise ConfigurationError("COMPLETE_TELEGRAM_MESSAGE_TOO_LARGE")
        value = await self.request("sendMessage", {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
        result = value.get("result") if value and value.get("ok") is True else None
        message_id = result.get("message_id") if isinstance(result, dict) else None
        return message_id if type(message_id) is int and message_id > 0 else None

    async def invalidate(self, row):
        text = ("<b>SIGNAL " + html.escape(row["status"]) + "</b>\n"
                + html.escape(str(row["reason"])) + "\nID: " + html.escape(row["id"])
                + "\nThis alert is no longer actionable. Existing fills and orders are tracked separately; cancellation is not implied.")
        value = await self.request("editMessageText", {"chat_id": self.chat_id, "message_id": row["message_id"], "text": text, "parse_mode": "HTML"})
        if value and value.get("ok") is True:
            return "EDITED"
        description = str(value.get("description", "")).lower() if value else ""
        if "message is not modified" in description:
            return "UNCHANGED"
        if "message to edit not found" in description:
            return "DELETED"
        return "EDIT_UNCERTAIN"

    async def updates(self, offset: int):
        value = await self.request("getUpdates", {"offset": offset, "timeout": 0, "limit": 25, "allowed_updates": ["message"]})
        return value["result"] if value and value.get("ok") is True and isinstance(value.get("result"), list) else []

    def authenticated(self, update: dict):
        message = update.get("message", {})
        return (str(message.get("chat", {}).get("id")) == self.chat_id
                and str(message.get("from", {}).get("id")) in self.operators
                and message.get("from", {}).get("is_bot") is False
                and not message.get("sender_chat")
                and not message.get("forward_origin") and not message.get("forward_from")
                and time.time() - 120 <= message.get("date", 0) <= time.time() + 5)


def signal_message(candidate: dict) -> str:
    lines = ["<b>LIVE WEATHER SIGNAL</b>", html.escape(candidate["title"]),
             "Strategy: " + html.escape(candidate["strategy"]),
             "Station/date: " + html.escape(candidate["station_day"])]
    for leg in candidate["legs"]:
        lines.append("BUY " + html.escape(leg["side"]) + " | token " + html.escape(leg["token"]) + "\nSnapshot/max manual price $" + html.escape(str(leg["price"])) + " | fee estimate $" + html.escape(str(leg["fee"])) + " | visible shares " + html.escape(str(leg["available"])))
    if candidate.get("uncalibrated") and candidate.get("model_frequency") is not None:
        lines.append("Model support: " + html.escape(str(candidate.get("model_frequency"))) + " — uncalibrated, not a win probability.")
    if candidate["strategy"] == "SOURCE_SHOCK":
        lines.append("Provisional official-observation exclusion. Source revisions can invalidate this thesis; finality is not established.")
    if candidate["strategy"] == "STRUCTURAL":
        lines.append("Basket quote only. Legs can fill separately; no guaranteed realized profit.")
    lines += ["Valid until " + datetime.fromtimestamp(candidate["expires"], timezone.utc).isoformat(), "Skip if price, fees, liquidity, contract or weather evidence changes.",
              "Delivery does not mean you traded. Account fills are reported separately.",
              "ID: " + html.escape(candidate["id"]), html.escape(candidate["event_url"])]
    return "\n".join(lines)
