from __future__ import annotations

"""Weather paper v4 settlement + operator commands.

Economic settlement is derived from the Polygon Conditional Tokens payout vector at
a single block, never from Gamma ``outcomePrices``.  Commands intentionally separate
bot health, validated open positions, settled results, no-fills, quarantined rows and
legacy/unverified evidence so the operator can understand the experiment at a glance.
"""

import asyncio
import html
import json
import math
import os
import time
from pathlib import Path

import httpx

from .settlement import CTF_EVIDENCE_KEY, CTF_FINALITY_VERSION, exact_token_payout
from .weather_only_paper_control import WeatherPaperCommandController, _age, _pct, _price, _short
from .weather_only_paper_positions_v4 import ValidatedWeatherPaperPositionStore


GAMMA = "https://gamma-api.polymarket.com"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
PAYOUT_DENOMINATOR_SELECTOR = "dd34de67"
PAYOUT_NUMERATOR_SELECTOR = "0504c814"
DEFAULT_POLYGON_RPC = "https://polygon-rpc.com"
PAPER_SETTLEMENT_V4_VERSION = "weather_paper_settlement_v4_ctf_onchain_final"
PAPER_COMMAND_V4_VERSION = "weather_paper_commands_v4_plain_accounting_health"


class PaperSettlementLookupError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _json_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _condition(value: object) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 66 or not text.startswith("0x") or any(ch not in "0123456789abcdef" for ch in text[2:]):
        raise PaperSettlementLookupError("CTF_CONDITION_ID_INVALID")
    return text


def _uint_result(value: object, code: str) -> int:
    text = str(value or "").strip().lower()
    if not text.startswith("0x"):
        raise PaperSettlementLookupError(code)
    try:
        number = int(text, 16)
    except ValueError:
        raise PaperSettlementLookupError(code) from None
    if number < 0:
        raise PaperSettlementLookupError(code)
    return number


def _abi_condition_call(selector: str, condition_id: str) -> str:
    condition = _condition(condition_id)
    return "0x" + selector + condition[2:]


def _abi_numerator_call(condition_id: str, index: int) -> str:
    if index < 0:
        raise PaperSettlementLookupError("CTF_OUTCOME_INDEX_INVALID")
    return "0x" + PAYOUT_NUMERATOR_SELECTOR + _condition(condition_id)[2:] + f"{int(index):064x}"


class PublicFinalSettlementClient:
    """Public read-only Gamma identity + Polygon CTF finality client."""

    def __init__(self, *, rpc_url: str | None = None) -> None:
        self.rpc_url = str(rpc_url or os.getenv("POLYGON_RPC_URL") or DEFAULT_POLYGON_RPC).strip()
        if not self.rpc_url.startswith("https://"):
            raise ValueError("POLYGON_RPC_URL must be https")
        self.gamma = httpx.AsyncClient(
            timeout=httpx.Timeout(12.0, connect=6.0),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4, keepalive_expiry=60.0),
            headers={"User-Agent": "polymarket-weather-paper-finality/4.0"},
            trust_env=False,
        )
        self.rpc = httpx.AsyncClient(
            timeout=httpx.Timeout(12.0, connect=6.0),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2, keepalive_expiry=60.0),
            headers={"User-Agent": "polymarket-weather-paper-ctf/4.0"},
            trust_env=False,
        )
        self._rpc_id = 0

    async def close(self) -> None:
        await asyncio.gather(self.gamma.aclose(), self.rpc.aclose(), return_exceptions=True)

    async def _get_gamma(self, market_id: str) -> dict:
        mid = str(market_id or "").strip()
        if not mid:
            raise PaperSettlementLookupError("GAMMA_MARKET_ID_MISSING")
        last_code = "GAMMA_TRANSPORT"
        for attempt in range(3):
            try:
                response = await self.gamma.get(f"{GAMMA}/markets/{mid}")
            except httpx.TimeoutException:
                last_code = "GAMMA_TIMEOUT"
            except httpx.RequestError:
                last_code = "GAMMA_TRANSPORT"
            else:
                if response.status_code == 404:
                    raise PaperSettlementLookupError("GAMMA_MARKET_NOT_FOUND")
                if response.status_code == 429 or response.status_code >= 500:
                    last_code = f"GAMMA_HTTP_{response.status_code}"
                elif response.status_code >= 400:
                    raise PaperSettlementLookupError(f"GAMMA_HTTP_{response.status_code}")
                else:
                    try:
                        body = response.json()
                    except ValueError:
                        raise PaperSettlementLookupError("GAMMA_JSON_INVALID") from None
                    if not isinstance(body, dict):
                        raise PaperSettlementLookupError("GAMMA_MARKET_INVALID")
                    returned_id = str(body.get("id") or "").strip()
                    if returned_id and returned_id != mid:
                        raise PaperSettlementLookupError("GAMMA_MARKET_IDENTITY_MISMATCH")
                    return body
            if attempt < 2:
                await asyncio.sleep(0.4 * (attempt + 1))
        raise PaperSettlementLookupError(last_code)

    async def _rpc_call(self, method: str, params: list) -> object:
        last = "POLYGON_RPC_TRANSPORT"
        for attempt in range(3):
            self._rpc_id += 1
            payload = {"jsonrpc": "2.0", "id": self._rpc_id, "method": method, "params": params}
            try:
                response = await self.rpc.post(self.rpc_url, json=payload)
            except httpx.TimeoutException:
                last = "POLYGON_RPC_TIMEOUT"
            except httpx.RequestError:
                last = "POLYGON_RPC_TRANSPORT"
            else:
                if response.status_code == 429 or response.status_code >= 500:
                    last = f"POLYGON_RPC_HTTP_{response.status_code}"
                elif response.status_code >= 400:
                    raise PaperSettlementLookupError(f"POLYGON_RPC_HTTP_{response.status_code}")
                else:
                    try:
                        body = response.json()
                    except ValueError:
                        raise PaperSettlementLookupError("POLYGON_RPC_JSON_INVALID") from None
                    if not isinstance(body, dict) or body.get("error") is not None or "result" not in body:
                        raise PaperSettlementLookupError("POLYGON_RPC_RESULT_INVALID")
                    return body["result"]
            if attempt < 2:
                await asyncio.sleep(0.4 * (attempt + 1))
        raise PaperSettlementLookupError(last)

    async def ctf_resolution(self, condition_id: str) -> dict | None:
        condition = _condition(condition_id)
        block_hex = await self._rpc_call("eth_blockNumber", [])
        block_number = _uint_result(block_hex, "POLYGON_BLOCK_NUMBER_INVALID")
        block_tag = hex(block_number)
        denominator_raw = await self._rpc_call(
            "eth_call", [{"to": CTF_ADDRESS, "data": _abi_condition_call(PAYOUT_DENOMINATOR_SELECTOR, condition)}, block_tag]
        )
        denominator = _uint_result(denominator_raw, "CTF_DENOMINATOR_INVALID")
        if denominator == 0:
            return None
        numerators: list[int] = []
        for index in range(2):
            value = await self._rpc_call(
                "eth_call", [{"to": CTF_ADDRESS, "data": _abi_numerator_call(condition, index)}, block_tag]
            )
            numerators.append(_uint_result(value, "CTF_NUMERATOR_INVALID"))
        if any(value > denominator for value in numerators) or sum(numerators) != denominator:
            raise PaperSettlementLookupError("CTF_PAYOUT_VECTOR_INVALID")
        return {
            "version": CTF_FINALITY_VERSION,
            "condition_id": condition,
            "payout_denominator": denominator,
            "payout_numerators": numerators,
            "block_number": block_number,
            "contract": CTF_ADDRESS,
            "checked_at": time.time(),
        }

    async def finalized_market(self, market_id: str) -> dict | None:
        market = await self._get_gamma(market_id)
        condition = _condition(market.get("conditionId"))
        tokens = [str(value).strip() for value in _json_list(market.get("clobTokenIds"))]
        outcomes = [str(value).strip().lower() for value in _json_list(market.get("outcomes"))]
        if len(tokens) != 2 or len(set(tokens)) != 2 or outcomes not in (["yes", "no"], ["no", "yes"]):
            raise PaperSettlementLookupError("GAMMA_BINARY_TOKEN_MAPPING_INVALID")
        final = await self.ctf_resolution(condition)
        if final is None:
            return None
        result = dict(market)
        result[CTF_EVIDENCE_KEY] = final
        return result


class WeatherPaperSettlementEngineV4:
    def __init__(self, *, store: ValidatedWeatherPaperPositionStore, telegram, finality: PublicFinalSettlementClient | None = None) -> None:
        self.store = store
        self.telegram = telegram
        self.finality = finality or PublicFinalSettlementClient()
        self._owns_finality = finality is None

    async def close(self) -> None:
        if self._owns_finality:
            await self.finality.close()

    @staticmethod
    def _resolution_message(position: dict) -> str:
        status = str(position.get("status") or "")
        icon = "✅" if status == "WON" else "❌" if status == "LOST" else "🟡"
        pnl = float(position.get("pnl") or 0.0)
        side = html.escape(str(position.get("side") or ""))
        title = html.escape(_short(position.get("title"), 80))
        return "\n".join([
            f"{icon} <b>PAPER RESULT — {html.escape(status)}</b>",
            f"<b>{title}</b>",
            f"Position #{int(position['id'])} | {side}",
            "",
            f"Paper money used: <b>${float(position.get('capital_used') or 0.0):.2f}</b>",
            f"Final payout received: <b>${float(position.get('proceeds') or 0.0):.2f}</b>",
            f"Result: <b>{'+' if pnl >= 0 else ''}${pnl:.2f}</b> ({_pct(position.get('roi'))})",
            "",
            "✅ Settlement proof: final on-chain CTF payout vector.",
            "📒 Paper only — no real order was placed.",
        ])

    async def _notify_resolved(self) -> tuple[int, list[str]]:
        sent = 0
        errors: list[str] = []
        rows = await asyncio.to_thread(self.store.resolved_without_notification, 50)
        for position in rows:
            try:
                message_id = await self.telegram.send_html(self._resolution_message(position))
            except Exception as exc:
                errors.append(f"SETTLEMENT_TELEGRAM:{position.get('id')}:{type(exc).__name__}")
                continue
            await asyncio.to_thread(self.store.mark_settlement_notified, int(position["id"]), int(message_id))
            sent += 1
        return sent, errors

    async def settle_once(self) -> dict:
        positions = await asyncio.to_thread(self.store.settlement_page, 200)
        market_ids = sorted({
            str(leg.get("market_id") or "")
            for position in positions for leg in position.get("legs") or []
            if str(leg.get("market_id") or "")
        })
        semaphore = asyncio.Semaphore(6)
        markets: dict[str, dict | None] = {}
        errors: list[str] = []

        async def one(mid: str) -> None:
            async with semaphore:
                try:
                    markets[mid] = await self.finality.finalized_market(mid)
                except PaperSettlementLookupError as exc:
                    errors.append(f"SETTLEMENT_LOOKUP:{mid}:{exc.code}")
                except Exception as exc:
                    errors.append(f"SETTLEMENT_LOOKUP:{mid}:{type(exc).__name__}")

        await asyncio.gather(*(one(mid) for mid in market_ids))

        resolved = 0
        pending = 0
        for position in positions:
            payouts: list[dict] = []
            complete = True
            for leg in position.get("legs") or []:
                mid = str(leg.get("market_id") or "")
                condition = str(leg.get("condition_id") or "").lower()
                token = str(leg.get("token_id") or "")
                market = markets.get(mid)
                if market is None:
                    complete = False
                    break
                if str(market.get("conditionId") or "").lower() != condition:
                    errors.append(f"SETTLEMENT_IDENTITY:{position.get('id')}:{mid}:CONDITION_MISMATCH")
                    complete = False
                    break
                payout = exact_token_payout(token, market)
                if payout is None:
                    errors.append(f"SETTLEMENT_IDENTITY:{position.get('id')}:{mid}:TOKEN_OR_VECTOR_INVALID")
                    complete = False
                    break
                final = market.get(CTF_EVIDENCE_KEY) or {}
                payouts.append({
                    "market_id": mid,
                    "condition_id": condition,
                    "token_id": token,
                    "payout": float(payout),
                    "payout_denominator": final.get("payout_denominator"),
                    "payout_numerators": final.get("payout_numerators"),
                    "block_number": final.get("block_number"),
                })
            if not complete or not payouts:
                pending += 1
                continue
            payout_per_unit = sum(float(row["payout"]) for row in payouts)
            evidence = {
                "version": PAPER_SETTLEMENT_V4_VERSION,
                "finality": "CTF_ONCHAIN_FINAL",
                "checked_at": time.time(),
                "legs": payouts,
                "financial_authority": False,
            }
            try:
                row = await asyncio.to_thread(self.store.resolve_position, int(position["id"]), payout_per_unit, evidence)
            except Exception as exc:
                errors.append(f"SETTLEMENT_APPLY:{position.get('id')}:{type(exc).__name__}")
                continue
            if row is not None:
                resolved += 1

        notified, notification_errors = await self._notify_resolved()
        errors.extend(notification_errors)
        return {
            "version": PAPER_SETTLEMENT_V4_VERSION,
            "open_checked": len(positions),
            "market_ids_checked": len(market_ids),
            "resolved_now": resolved,
            "pending_now": pending,
            "resolution_messages_sent": notified,
            "errors": errors,
            "finality_source": "POLYGON_CTF_ONCHAIN",
        }


class PlainWeatherPaperCommandControllerV4(WeatherPaperCommandController):
    """Human-first command replies; validated economics are never mixed with legacy rows."""

    def __init__(self, *, telegram, store: ValidatedWeatherPaperPositionStore, status_path: str | Path, paper_stake_usd: float) -> None:
        super().__init__(telegram=telegram, store=store, status_path=status_path, paper_stake_usd=paper_stake_usd)
        self.store: ValidatedWeatherPaperPositionStore = store

    async def register_menu(self) -> None:
        commands = [
            {"command": "status", "description": "Is the bot healthy right now?"},
            {"command": "stats", "description": "Validated paper account summary"},
            {"command": "positions", "description": "What paper trades are open now?"},
            {"command": "history", "description": "What won/lost/finished?"},
            {"command": "skipped", "description": "No-fill, quarantined and unverified rows"},
            {"command": "recent", "description": "Latest signals and what happened to them"},
            {"command": "help", "description": "Explain the commands"},
        ]
        response = await self.http.post(f"{self.endpoint}/setMyCommands", json={"commands": commands})
        response.raise_for_status()

    def _health(self) -> tuple[bool, str, dict]:
        status = self._read_status()
        try:
            finished = float(status.get("finished_at"))
        except (TypeError, ValueError, OverflowError):
            finished = 0.0
        interval = float(status.get("expected_cycle_interval_seconds") or 180.0)
        age = time.time() - finished if finished > 0 else float("inf")
        freshness_limit = max(600.0, 2.5 * interval)
        errors = list(status.get("errors") or [])
        settlement_errors = list((status.get("paper_settlement") or {}).get("errors") or [])
        healthy = bool(status.get("cycle_ok")) and age <= freshness_limit and not settlement_errors
        if age > freshness_limit:
            reason = f"last complete cycle is {_age(finished)}"
        elif settlement_errors:
            reason = "settlement/tracking source has errors"
        elif errors:
            reason = "last cycle reported errors"
        else:
            reason = "scanner, tracker and command loop are current"
        return healthy, reason, status

    def _status_text(self) -> str:
        healthy, reason, status = self._health()
        stats = self.store.stats()
        icon = "🟢" if healthy else "🔴"
        return "\n".join([
            f"{icon} <b>WEATHER PAPER BOT: {'HEALTHY' if healthy else 'NEEDS ATTENTION'}</b>",
            f"Why: {html.escape(reason)}",
            f"Last full cycle: <b>{html.escape(_age(status.get('finished_at')))}</b>",
            "",
            "<b>What is happening now</b>",
            f"Open validated paper trades: <b>{stats['open']}</b> using <b>${stats['open_capital']:.2f}</b>",
            f"Finished validated trades: <b>{stats['resolved']}</b>",
            f"Skipped/no-fill: <b>{stats['no_fill']}</b>",
            f"Quarantined: <b>{stats['quarantined']}</b>",
            f"Old/unverified rows: <b>{stats['unverified']}</b>",
            "",
            "🚫 Real trading: <b>OFF</b>",
            "Use /positions for open trades and /stats for money/results.",
        ])

    def _stats_text(self) -> str:
        stats = self.store.stats()
        return "\n".join([
            "📊 <b>VALIDATED PAPER ACCOUNT</b>",
            "Only v4-validated trades count below.",
            "",
            f"🟦 OPEN NOW: <b>{stats['open']}</b> trades | <b>${stats['open_capital']:.2f}</b> paper money currently at risk",
            f"🏁 FINISHED: <b>{stats['resolved']}</b> trades — <b>{stats['won']} won / {stats['lost']} lost / {stats['partial']} partial</b>",
            f"⏭ SKIPPED / NO FILL: <b>{stats['no_fill']}</b>",
            "",
            f"Money used in finished trades: <b>${stats['resolved_capital']:.2f}</b>",
            f"Money paid back by finished trades: <b>${stats['resolved_proceeds']:.2f}</b>",
            f"REALIZED PAPER PROFIT/LOSS: <b>{'+' if stats['pnl'] >= 0 else ''}${stats['pnl']:.2f}</b>",
            f"Return on finished paper capital: <b>{_pct(stats.get('resolved_roi'))}</b>",
            f"Win rate (full wins/losses only): <b>{_pct(stats.get('win_rate'))}</b>",
            "",
            "<b>Excluded from those numbers</b>",
            f"🧯 Quarantined: {stats['quarantined']}",
            f"❓ Old/unverified: {stats['unverified']}",
            "These stay in the database for audit but cannot make the strategy look better or worse.",
        ])

    def _positions_text(self) -> str:
        rows = self.store.open_positions(10, validated_only=True)
        stats = self.store.stats()
        lines = [f"📂 <b>OPEN PAPER TRADES — {stats['open']} total</b>"]
        if not rows:
            lines.append("No validated paper trades are open right now.")
            return "\n".join(lines)
        for row in rows:
            lines.extend([
                "",
                f"🟦 <b>#{int(row['id'])} — {html.escape(str(row['side']))}</b>",
                html.escape(_short(row.get("title"), 76)),
                f"Paper money in trade: <b>${float(row.get('capital_used') or 0.0):.2f}</b>",
                f"Entry cost per share/set: <b>{_price(row.get('entry_cost_per_unit'))}</b>",
                f"Quantity: <b>{float(row.get('filled_units') or 0.0):.3f}</b>",
                f"Opened: {html.escape(_age(row.get('paper_fill_at') or row.get('opened_at')))}",
            ])
        if stats["open"] > len(rows):
            lines.append(f"\nShowing first {len(rows)} of {stats['open']} open trades.")
        return "\n".join(lines)

    def _history_text(self) -> str:
        rows = [row for row in self.store.recent_positions(30, resolved_only=True) if row.get("validation_state") == "VALIDATED"][:10]
        lines = ["🏁 <b>RECENT VALIDATED RESULTS</b>"]
        if not rows:
            lines.append("No validated paper trade has reached final on-chain settlement yet.")
            return "\n".join(lines)
        for row in rows:
            status = str(row.get("status") or "")
            icon = "✅" if status == "WON" else "❌" if status == "LOST" else "🟡"
            pnl = float(row.get("pnl") or 0.0)
            lines.extend([
                "",
                f"{icon} <b>#{int(row['id'])} {html.escape(status)}</b> — {html.escape(str(row.get('side') or ''))}",
                html.escape(_short(row.get("title"), 72)),
                f"Used ${float(row.get('capital_used') or 0.0):.2f} → got ${float(row.get('proceeds') or 0.0):.2f}",
                f"P&amp;L: <b>{'+' if pnl >= 0 else ''}${pnl:.2f}</b> ({_pct(row.get('roi'))})",
            ])
        return "\n".join(lines)

    def _skipped_text(self) -> str:
        stats = self.store.stats()
        with self.store._conn() as db:
            rows = [dict(row) for row in db.execute(
                """
                SELECT id,status,validation_state,validation_reason,no_fill_reason,title
                FROM weather_paper_positions
                WHERE status IN ('NO_FILL','QUARANTINED','UNVERIFIED') OR validation_state!='VALIDATED'
                ORDER BY id DESC LIMIT 10
                """
            )]
        lines = [
            "⏭ <b>SKIPPED / EXCLUDED PAPER ROWS</b>",
            f"No fill: <b>{stats['no_fill']}</b> | Quarantined: <b>{stats['quarantined']}</b> | Unverified: <b>{stats['unverified']}</b>",
        ]
        for row in rows:
            reason = row.get("validation_reason") or row.get("no_fill_reason") or row.get("status")
            lines.append(f"• #{row['id']} <b>{html.escape(str(row['status']))}</b> — {html.escape(_short(reason, 55))}")
        return "\n".join(lines)

    def _recent_text(self) -> str:
        rows = self.store.recent_signals(10)
        lines = ["🧾 <b>LATEST SIGNALS — WHAT HAPPENED</b>"]
        if not rows:
            lines.append("No signals stored yet.")
            return "\n".join(lines)
        for row in rows:
            position = str(row.get("position_status") or "NOT_TRACKED")
            validation = str(row.get("validation_state") or "UNVERIFIED")
            label = "TAKEN" if position == "OPEN" and validation == "VALIDATED" else position
            lines.append(
                f"• Signal #{row['id']} {html.escape(str(row.get('side') or 'BASKET'))} @ {_price(row.get('entry_cost'))} → <b>{html.escape(label)}</b>"
            )
        return "\n".join(lines)

    @staticmethod
    def _help_text() -> str:
        return "\n".join([
            "🤖 <b>WEATHER PAPER COMMANDS</b>",
            "/status — is the bot healthy right now?",
            "/stats — money used, open risk, wins/losses and realized P&amp;L",
            "/positions — paper trades currently open",
            "/history — recently finalized wins/losses",
            "/skipped — no-fills, quarantined and old/unverified rows",
            "/recent — latest signals and whether each was taken/skipped",
            "/help — this explanation",
            "",
            "Validated statistics never include quarantined or unverifiable legacy rows.",
            "No real orders are placed.",
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
        elif command in {"/skipped", "skipped"}:
            body = self._skipped_text()
        elif command in {"/recent", "recent"}:
            body = self._recent_text()
        elif command in {"/took", "took", "/filled", "filled"}:
            body = "📒 Paper positions are created automatically from the frozen v4 execution snapshot. Use /positions or /stats."
        else:
            body = self._help_text()
        await self.telegram.send_html(body)
