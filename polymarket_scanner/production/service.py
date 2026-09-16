"""Continuously refreshed manual signals; no financial adapter imports."""
from __future__ import annotations

import asyncio
import html
import json
import time
from decimal import Decimal

from .config import canonical
from .io import atomic_json
from .telegram import signal_message


def bounded_reply(text):
    """Bound the final HTML/UTF-16 representation, preserving valid entities."""
    suffix = "\n[Additional details omitted; use the local export command.]"
    escaped = html.escape(str(text))
    if len(("<pre>" + escaped + "</pre>").encode("utf-16-le")) // 2 <= 3900:
        return "<pre>" + escaped + "</pre>"
    source = str(text)
    low, high = 0, len(source)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = "<pre>" + html.escape(source[:middle] + suffix) + "</pre>"
        if len(candidate.encode("utf-16-le")) // 2 <= 3900:
            low = middle
        else:
            high = middle - 1
    return "<pre>" + html.escape(source[:low] + suffix) + "</pre>"


def units(value):
    return str(Decimal(value or 0) / 1_000_000)


class SignalService:
    def __init__(self, config, store, telegram, weather):
        self.config, self.store, self.telegram, self.weather = config, store, telegram, weather
        if getattr(telegram, "delivery_identity", None) is not None:
            store.bind_telegram(telegram.delivery_identity)
        self.census = {}
        self.last_cycle = 0
        self.last_error = None

    async def sync(self, signal_id=None):
        for row in self.store.pending_sync(signal_id):
            result = await self.telegram.invalidate(row)
            self.store.sync_result(row["id"], result)

    async def terminal(self, signal_id, status, reason):
        self.store.terminal(signal_id, status, reason)
        await self.sync(signal_id)  # target cannot be starved by historical backlog

    async def deliver(self, candidate):
        if not self.store.save(candidate):
            return
        message = signal_message(candidate)
        if len(message.encode("utf-16-le")) // 2 > 4000:
            self.store.terminal(candidate["id"], "INVALIDATED", "COMPLETE_INSTRUCTIONS_EXCEED_TELEGRAM_LIMIT")
            return
        if not self.store.begin_send(candidate["id"]):
            return
        message_id = await self.telegram.send(message)
        self.store.receipt(candidate["id"], message_id)
        await self.sync(candidate["id"])  # includes expiry/invalidation during send

    def execution_status(self):
        path = self.config.execution_status_path
        if path is None:
            return {"financial_authority": False, "reason": "LIVE_SIGNALS_ONLY"}
        try:
            data = json.loads(path.read_text())
            if not isinstance(data, dict) or not 0 <= time.time() - data["updated_at"] < 30:
                raise ValueError
            if (self.config.mode != "LIVE_EXECUTION" or data.get("mode") != "LIVE_EXECUTION"
                or data.get("config_sha256") != self.config.config_sha256
                or data.get("account", {}).get("wallet") != self.config.wallet):
                return {"financial_authority": False, "reason": "EXECUTION_STATUS_CONFIGURATION_OR_ACCOUNT_MISMATCH"}
            return data
        except (OSError, ValueError, KeyError, TypeError):
            return {"financial_authority": False, "reason": "EXECUTION_WORKER_STATUS_UNAVAILABLE"}

    def status(self):
        return {"mode": self.config.mode, "updated_at": time.time(), "last_cycle": self.last_cycle,
                "last_error": self.last_error, "signals": self.store.summary(), "execution": self.execution_status(),
                "discovery": self.census, "supported_scope": "STRICT_SUPPORTED_WEATHER_SUBSET",
                "unsupported_contract_policy": "SKIP_AND_REPORT_WITHOUT_LOOSENING_PARSER"}

    def operator_report(self, command):
        signals = self.store.summary()
        execution = self.execution_status()
        account = execution.get("account", {})
        lines = ["Mode: " + self.config.mode,
                 "Automatic opening authority: " + str(execution.get("financial_authority", False)),
                 "Stop new openings: " + str(signals["stop_opening"]) + " | generation " + signals["stop_generation"],
                 "Signals: " + canonical(signals["signal_states"]),
                 "Observed signal outcomes (not trades): " + str(signals["observed_signal_outcomes"]),
                 "Telegram unknown deliveries: " + str(signals["unknown_deliveries"]) + " | pending/escalated edits: " + str(signals["operator_sync_pending_or_escalated"]),
                 "Excluded historical research rows: " + str(signals["excluded_legacy_research_rows"]) + ". Simulation remains separate."]
        if command == "/recent":
            lines += ["Recent signals — delivery never proves a manual trade:"]
            for row in self.store.recent():
                evidence = self.store.candidate(row["id"])
                lines.append(str(evidence["title"]) + " | " + evidence["strategy"] + " | " + row["status"] + " / " + row["delivery"] + " | outcome " + ("observed" if row["outcome"] else "pending") + " | " + row["id"])
        elif account:
            lines += ["Account: " + str(account.get("wallet")),
                      "Reconciled: " + str(execution.get("reconciled")) + " | fault: " + str(account.get("reconciliation_fault") or execution.get("last_error") or "none"),
                      "Confirmed fills: " + str(account.get("confirmed_fill_count", 0)),
                      "Actual cost $" + units(account.get("actual_cost_micros")) + " | actual fees $" + units(account.get("actual_fees_micros")),
                      "Reserved $" + units(account.get("reserved_micros")),
                      "Settlement claim P&L: " + ("UNVERIFIED" if account.get("settled_position_pnl_micros") is None else "$" + units(account["settled_position_pnl_micros"])),
                      "Verified redemption cash by asset: " + canonical({k: units(v) for k, v in account.get("verified_redemption_proceeds_by_asset_micros", {}).items()})]
            lines.append("Verified redemption gas (native wei; not USD P&L): " + canonical(account.get("verified_native_gas_wei", {})))
            orders = account.get("outstanding_orders", [])
            lines.append("Outstanding/unknown orders: " + str(len(orders)))
            for row in sorted(orders, key=lambda x: (x["status"] not in {"UNKNOWN", "SUBMITTING"}, x["id"]))[:12]:
                lines.append(row["status"] + " | token " + row["token"] + " | confirmed " + units(row["matched"]) + "/" + units(row["quantity"]) + " shares")
            if command == "/positions":
                positions = account.get("positions", [])
                lines.append("Actual held positions: " + str(len(positions)))
                for row in positions[:12]:
                    lines.append("Token " + row["token"] + " | held " + units(row["quantity"]) + " shares | acquired cost $" + units(row["cost"]) + " | fees $" + units(row["fees"]))
                if len(positions) > 12:
                    lines.append("Additional positions available in complete local export.")
        else:
            lines.append("Execution: " + str(execution.get("reason", "unavailable")))
        if command == "/status":
            lines += ["Supported contracts: strict reviewed subset; unknown templates skipped and counted.",
                      "Discovery census: " + canonical(self.census), "Last cycle UTC epoch: " + str(self.last_cycle)]
        return "\n".join(lines)

    async def expiry(self):
        for row in self.store.active():
            if time.time() >= row["expires"]:
                await self.terminal(row["id"], "EXPIRED", "QUOTE_VALIDITY_ENDED")
        await self.sync()

    async def commands(self):
        offset = int(self.store.state("telegram_offset", "0"))
        for update in await self.telegram.updates(offset):
            uid = update.get("update_id")
            if type(uid) is not int or uid < offset:
                continue
            if self.telegram.authenticated(update):
                command = str(update["message"].get("text", "")).strip().split(" ")[0]
                actor = str(update["message"]["from"]["id"])
                if command in {"/status", "/recent", "/positions", "/stats", "/stop", "/cancel_open", "/export"} and self.store.command(uid, actor, command):
                    if command == "/stop":
                        reply = "New openings stopped. Existing orders continue reconciliation and expiry management. Resume requires local operator recovery."
                    elif command == "/cancel_open":
                        reply = "New openings stopped; cancellation requested for managed open orders. Cancellation is not confirmed until exchange reconciliation. Existing fills remain."
                    elif command == "/export":
                        # The full export is produced by the local CLI, not silently
                        # truncated and misrepresented as a complete Telegram file.
                        reply = "Use the local export command for complete JSON evidence. /recent and /stats provide bounded views here."
                    else:
                        reply = self.operator_report(command)
                    try:
                        message_id = await self.telegram.send(bounded_reply(reply))
                    except Exception:
                        message_id = None
                    if message_id is None:
                        self.last_error = "COMMAND_REPLY_DELIVERY_UNCONFIRMED"
            self.store.set_state("telegram_offset", uid + 1)
        atomic_json(self.config.status_path, self.status())

    async def controls(self):
        await self.commands()
        await self.expiry()

    async def cycle(self):
        discovered = await self.weather.discover()
        self.census = discovered["status"]
        events = sorted(discovered["events"], key=lambda event: str(event.get("id", "")))
        cursor = self.store.state("event_cursor")
        selected = [event for event in events if str(event.get("id", "")) > cursor]
        if not selected:
            selected = events
        for event in selected[:6]:
            self.store.set_state("event_cursor", str(event.get("id", "")))
            try:
                candidates = await self.weather.evaluate(event, self.config.strategies, self.config.min_model_gap, self.config.min_structural_edge)
                for candidate in candidates:
                    await self.deliver(candidate)
            except Exception:
                self.last_error = "EVENT_EVIDENCE_REJECTED_OR_UNAVAILABLE"
        for row in self.store.active():
            if time.time() >= row["expires"]:
                await self.terminal(row["id"], "EXPIRED", "QUOTE_VALIDITY_ENDED")
                continue
            try:
                await self.weather.revalidate(self.store.candidate(row["id"]))
            except Exception:
                await self.terminal(row["id"], "INVALIDATED", "EVIDENCE_OR_MARKET_NO_LONGER_VALID")
        for row in self.store.outcome_pending():
            try:
                outcome = await self.weather.outcome(self.store.candidate(row["id"]))
                self.store.outcome(row["id"], outcome)
                if outcome:
                    await self.terminal(row["id"], "SETTLED", "OBSERVED_MARKET_OUTCOME")
            except Exception:
                self.store.outcome(row["id"], None)
        self.last_cycle = time.time()

    async def run(self, *, once=False):
        self.store.recover()
        if once:
            await self.cycle()
            await self.controls()
            return
        async def collection():
            while True:
                try:
                    await self.cycle()
                except Exception:
                    self.last_error = "DISCOVERY_OR_CYCLE_FAILED"
                await asyncio.sleep(60)
        async def controls():
            while True:
                await self.commands()
                await asyncio.sleep(1)
        async def expiry():
            while True:
                await self.expiry()
                await asyncio.sleep(1)
        await asyncio.gather(collection(), controls(), expiry())
