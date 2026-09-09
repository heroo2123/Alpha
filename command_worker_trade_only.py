from __future__ import annotations

import asyncio
import html
import re
import time
from datetime import datetime

import httpx

import command_worker as worker
from polymarket_scanner.config import settings
from polymarket_scanner.manual_fill_commands import (
    parse_structural_fill_command,
    structural_fill_help,
)
from polymarket_scanner.manual_fills import record_structural_fills, structural_manual_stats
from polymarket_scanner.polymarket import PolymarketClient
from polymarket_scanner.store import STRUCTURAL_DETECTORS
from polymarket_scanner.telegram import Telegram
from polymarket_scanner.trade_only import (
    TradeNowPreSendInvalid,
    refresh_trade_readiness,
    send_trade_now,
)
from polymarket_scanner.weather_calibration import WEATHER_DETECTORS, apply_weather_calibration

# Do not initiate a financial Telegram request at the ragged edge of an execution
# certificate. The certificate is only 8 seconds long; retain one second of headroom
# for the Bot API request handoff. This does not claim the alert remains executable
# after delivery—the message itself still carries explicit prices/skip conditions.
TRADE_ALERT_MIN_NETWORK_REMAINING_SECONDS = 1.0
_silent_shadow_process = False  # Set by the canonical process main, not library tests.


class DeliveryRetryable(Exception):
    """Telegram explicitly did not accept the message; a fresh retry is allowed."""


class DeliveryRejected(Exception):
    """Telegram explicitly rejected the message; do not retry automatically."""


class DeliveryUncertain(Exception):
    """The request outcome is ambiguous; never auto-retry a money instruction."""


_delivery_poly: PolymarketClient | None = None


def _poly() -> PolymarketClient:
    global _delivery_poly
    if _delivery_poly is None:
        _delivery_poly = PolymarketClient()
    return _delivery_poly


def _parse_took_command(text: str) -> tuple[int, float, float] | None:
    """Parse the single-leg `/took ALERT_ID STAKE_USD ACTUAL_COST` command."""
    match = re.fullmatch(
        r"/took\s+(\d+)\s+([0-9]+(?:\.[0-9]+)?)\s+([0-9]+(?:\.[0-9]+)?)",
        text.strip(),
        flags=re.I,
    )
    if not match:
        return None
    signal_id = int(match.group(1))
    stake = float(match.group(2))
    actual_cost = float(match.group(3))
    if signal_id <= 0 or stake <= 0 or actual_cost <= 0:
        return None
    return signal_id, stake, actual_cost


def _certificate_deadline_epoch(signal) -> float | None:
    value = signal.metadata.get("trade_ready_expires_at")
    if not isinstance(value, str) or not value.strip():
        cert = signal.metadata.get("execution_certificate")
        value = cert.get("expires_at") if isinstance(cert, dict) else None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.timestamp()


async def _safe_post_message(
    self: Telegram,
    client: httpx.AsyncClient,
    chat_id: str | int,
    text: str,
    reply_markup: dict | None = None,
    *,
    lane: str,
) -> int | None:
    """Production Bot API send with an explicit delivery receipt boundary.

    TRADE NOW performs one network attempt. Timeout/5xx/unreadable success is
    UNCERTAIN because Telegram may already have accepted the instruction. Only an
    explicit 429 is retryable, and the outbox revalidates before the next attempt.
    Command/control messages may retry because duplicating status/help text is not a
    trading hazard.
    """
    if lane == "alert" and _silent_shadow_process:
        raise DeliveryRejected("financial Telegram delivery disabled in silent shadow")
    if not self.token:
        return None
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    url = f"https://api.telegram.org/bot{self.token}/sendMessage"
    attempts = 3 if lane == "command" else 1
    for attempt in range(attempts):
        if lane == "alert":
            deadline = getattr(self, "_trade_alert_not_after_epoch", None)
            if deadline is not None:
                try:
                    remaining = float(deadline) - time.time()
                except (TypeError, ValueError, OverflowError):
                    remaining = -1.0
                if remaining < TRADE_ALERT_MIN_NETWORK_REMAINING_SECONDS:
                    self.last_alert_error = (
                        "TRADE NOW execution certificate expired/too close to expiry before Telegram request"
                    )
                    raise worker.AlertSuppressed(self.last_alert_error)

        try:
            response = await client.post(url, json=payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            safe = worker._sanitize_error(exc)
            if lane == "alert":
                self.last_alert_error = safe
                raise DeliveryUncertain(f"Telegram alert transport outcome uncertain: {safe}") from None
            self.last_command_error = safe
            if attempt >= attempts - 1:
                raise RuntimeError(f"Telegram command send failed: {safe}") from None
            await asyncio.sleep(0.75 * (attempt + 1))
            continue

        if response.status_code == 429:
            retry_after = 1.0
            try:
                body = response.json()
                retry_after = float((body.get("parameters") or {}).get("retry_after", 1))
            except Exception:
                retry_after = 1.0
            retry_after = max(1.0, retry_after)
            if lane == "alert":
                self.last_alert_error = f"Telegram rate limited; retry_after={retry_after:.0f}s"
                raise DeliveryRetryable(self.last_alert_error)
            if attempt >= attempts - 1:
                raise RuntimeError(f"Telegram command rate limited; retry_after={retry_after:.0f}s")
            await asyncio.sleep(min(retry_after, 15.0))
            continue

        if response.status_code >= 500:
            safe = f"Telegram HTTP {response.status_code}"
            if lane == "alert":
                self.last_alert_error = safe
                raise DeliveryUncertain(safe)
            if attempt >= attempts - 1:
                raise RuntimeError(f"Telegram command send failed: {safe}")
            await asyncio.sleep(0.75 * (attempt + 1))
            continue

        if response.status_code >= 400:
            safe = f"Telegram HTTP {response.status_code}"
            if lane == "alert":
                self.last_alert_error = safe
                raise DeliveryRejected(safe)
            response.raise_for_status()

        try:
            body = response.json()
        except Exception as exc:
            safe = worker._sanitize_error(exc)
            if lane == "alert":
                self.last_alert_error = safe
                raise DeliveryUncertain("Telegram returned an unreadable success response") from None
            if attempt >= attempts - 1:
                raise RuntimeError(f"Telegram command response decode failed: {safe}") from None
            await asyncio.sleep(0.75 * (attempt + 1))
            continue

        if not isinstance(body, dict) or body.get("ok") is not True:
            if lane == "alert":
                self.last_alert_error = "Telegram API rejected sendMessage"
                raise DeliveryRejected(self.last_alert_error)
            if attempt >= attempts - 1:
                raise RuntimeError("Telegram API rejected sendMessage")
            await asyncio.sleep(0.75 * (attempt + 1))
            continue

        result = body.get("result")
        message_id = result.get("message_id") if isinstance(result, dict) else None
        if type(message_id) is not int:
            if lane == "alert":
                self.last_alert_error = "Telegram success response lacked message_id receipt"
                raise DeliveryUncertain(self.last_alert_error)
            if attempt >= attempts - 1:
                raise RuntimeError("Telegram success response lacked message_id receipt")
            await asyncio.sleep(0.75 * (attempt + 1))
            continue

        if lane == "command":
            self.last_command_error = None
        else:
            self.last_alert_error = None
        return message_id

    raise RuntimeError(f"Telegram {lane} send exhausted retries")


async def _guarded_send_signal(self: Telegram, signal_id: int, signal) -> None:
    if signal.detector in WEATHER_DETECTORS:
        await asyncio.to_thread(apply_weather_calibration, signal, self.store.path)
    if not await refresh_trade_readiness(signal, _poly()):
        reason = str(signal.metadata.get("trade_ready_reason") or "not TRADE NOW eligible")
        raise worker.AlertSuppressed(reason)

    deadline = _certificate_deadline_epoch(signal)
    if deadline is None:
        raise worker.AlertSuppressed("TRADE NOW certificate has no valid delivery expiry")
    self._trade_alert_not_after_epoch = deadline
    try:
        await send_trade_now(self, signal_id, signal)
    except TradeNowPreSendInvalid as exc:
        raise worker.AlertSuppressed(str(exc)) from None
    finally:
        self._trade_alert_not_after_epoch = None


async def _trade_delivery_loop(store, tg: Telegram, outbox) -> None:
    """Single-claim, fail-closed TRADE NOW delivery loop."""
    last_sent = 0.0
    last_recovery = 0.0
    while True:
        try:
            now = time.monotonic()
            if now - last_recovery >= 5.0:
                recovered = await asyncio.to_thread(outbox.recover_abandoned_claims, 60.0)
                if recovered:
                    worker.log.warning(
                        "quarantined %d stale in-flight Telegram deliveries as UNCERTAIN", recovered
                    )
                last_recovery = now

            item = await asyncio.to_thread(outbox.next_due)
            if not item:
                await asyncio.sleep(0.20)
                continue

            interval = (
                settings.telegram_actionable_min_interval_seconds
                if int(item.get("priority") or 10) == 0
                else settings.telegram_watch_min_interval_seconds
            )
            wait_for = interval - (time.monotonic() - last_sent)
            if wait_for > 0:
                await asyncio.sleep(wait_for)

            if not await asyncio.to_thread(outbox.claim, int(item["id"])):
                continue
            signal_row = await asyncio.to_thread(store.get_signal, int(item["signal_id"]))
            if not signal_row:
                await asyncio.to_thread(outbox.mark_suppressed, int(item["id"]), "signal row missing")
                continue

            signal = outbox.signal_from_row(signal_row)
            try:
                await tg.send_signal(int(item["signal_id"]), signal)
            except asyncio.CancelledError:
                raise
            except worker.AlertSuppressed as exc:
                reason = worker._sanitize_error(exc) or "suppressed by safety policy"
                await asyncio.to_thread(outbox.mark_suppressed, int(item["id"]), reason)
                await asyncio.to_thread(store.set_state, "telegram_outbox_error", "")
                worker.log.info("suppressed persistent Telegram alert %s: %s", item["signal_id"], reason)
                continue
            except DeliveryRetryable as exc:
                safe = worker._sanitize_error(exc)
                await asyncio.to_thread(outbox.mark_failed, int(item["id"]), safe)
                await asyncio.to_thread(store.set_state, "telegram_outbox_error", safe)
                worker.log.warning(
                    "confirmed no-delivery for alert %s; queued for fresh revalidation: %s",
                    item["signal_id"],
                    safe,
                )
                continue
            except DeliveryRejected as exc:
                safe = worker._sanitize_error(exc)
                await asyncio.to_thread(outbox.mark_suppressed, int(item["id"]), safe)
                await asyncio.to_thread(store.set_state, "telegram_outbox_error", safe)
                worker.log.warning("Telegram rejected alert %s; terminally suppressed: %s", item["signal_id"], safe)
                continue
            except DeliveryUncertain as exc:
                safe = worker._sanitize_error(exc)
                await asyncio.to_thread(outbox.mark_uncertain, int(item["id"]), safe)
                await asyncio.to_thread(store.set_state, "telegram_outbox_error", safe)
                worker.log.error(
                    "Telegram delivery for alert %s is UNCERTAIN; not retrying: %s",
                    item["signal_id"],
                    safe,
                )
                continue
            except Exception as exc:
                safe = worker._sanitize_error(exc)
                await asyncio.to_thread(outbox.mark_uncertain, int(item["id"]), safe)
                await asyncio.to_thread(store.set_state, "telegram_outbox_error", safe)
                worker.log.exception(
                    "unexpected alert delivery failure; quarantined as UNCERTAIN: %s", safe
                )
                continue

            await asyncio.to_thread(outbox.mark_sent, int(item["id"]))
            await asyncio.to_thread(store.set_state, "telegram_outbox_error", "")
            last_sent = time.monotonic()
            worker.log.info(
                "delivered persistent TRADE NOW alert %s with Telegram receipt", item["signal_id"]
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            worker.log.exception(
                "trade-only Telegram delivery loop failed: %s", worker._sanitize_error(exc)
            )
            await asyncio.sleep(1.0)


async def _safe_manual_stats(self: Telegram) -> None:
    directional, structural = await asyncio.gather(
        asyncio.to_thread(self.store.manual_stats),
        asyncio.to_thread(structural_manual_stats, self.store),
    )
    lines = [
        "💼 <b>Your user-reported execution ledger</b>",
        "",
        "<b>Directional / single-leg fills</b>",
        f"Valid: <b>{int(directional['total'])}</b> | Won: <b>{int(directional['won'])}</b> | "
        f"Lost: <b>{int(directional['lost'])}</b> | Partial: <b>{int(directional.get('partial') or 0)}</b> | "
        f"Open: <b>{int(directional['open'])}</b>",
        f"Tracked stake: <b>${float(directional['stake']):.2f}</b> | "
        f"P&amp;L: <b>${float(directional['pnl']):.2f}</b>",
        "",
        "<b>Structural per-leg fills</b>",
        f"Recorded: <b>{int(structural['total_recorded'])}</b> | Certified-policy conforming: "
        f"<b>{int(structural['conforming_total'])}</b> | Open: <b>{int(structural['open'])}</b> | "
        f"Resolved: <b>{int(structural['resolved'])}</b>",
        f"Actual conforming cash cost: <b>${float(structural['cash_cost']):.2f}</b> | "
        f"P&amp;L: <b>${float(structural['pnl']):.2f}</b>",
        f"Nonconforming real executions excluded from strategy evidence: "
        f"<b>{int(structural['nonconforming_excluded'])}</b>",
        "ℹ️ Structural fills are user-reported per leg, <b>not exchange-verified</b>. "
        "P&amp;L is recorded only after every exact token has a final payout.",
    ]
    legacy = int(directional.get("legacy_excluded") or 0)
    if legacy:
        lines.append(f"Legacy alert-price rows excluded from valid directional P&amp;L: <b>{legacy}</b>")
    await self.send("\n".join(lines))


async def _ensure_trade_only_command_menu(self: Telegram) -> None:
    """Register the production command menu, including exact structural fills."""
    if not self.token or self._commands_registered:
        return
    commands = [
        {"command": "status", "description": "Live scanner health and feed status"},
        {"command": "stats", "description": "Verified scanner evidence audit"},
        {"command": "mystats", "description": "Your user-reported execution ledger"},
        {"command": "recent", "description": "Recent stored scanner alerts"},
        {"command": "taken", "description": "Recent directional actual-fill trades"},
        {"command": "took", "description": "Record a directional/single-leg fill"},
        {"command": "filled", "description": "Record every leg of a structural fill"},
        {"command": "help", "description": "Show command help"},
    ]
    try:
        response = await self.command_http.post(
            f"https://api.telegram.org/bot{self.token}/setMyCommands",
            json={"commands": commands},
            timeout=10,
        )
        response.raise_for_status()
        self._commands_registered = True
    except Exception as exc:
        worker.log.warning(
            "Telegram command menu registration failed: %s", worker._sanitize_error(exc)
        )


async def _safe_poll_commands(self: Telegram) -> None:
    """Authenticated production command parser with exact-fill accounting."""
    if not self.token_enabled:
        return
    await _ensure_trade_only_command_menu(self)
    offset = int(await asyncio.to_thread(self.store.get_state, "telegram_offset", "0") or 0)
    try:
        response = await self.command_http.get(
            f"https://api.telegram.org/bot{self.token}/getUpdates",
            params={"offset": offset, "timeout": 1},
            timeout=5,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok", False):
            raise RuntimeError("Telegram getUpdates returned ok=false")
        self.last_command_poll_at = time.time()
        self.last_command_error = None

        for update in data.get("result", []):
            offset = max(offset, int(update["update_id"]) + 1)
            msg = update.get("message") or {}
            incoming_chat = str((msg.get("chat") or {}).get("id") or "")
            chat_type = str((msg.get("chat") or {}).get("type") or "")
            text = str(msg.get("text") or "").strip()
            low = text.lower()

            if (
                low in {"/whoami", "whoami", "/start"}
                and incoming_chat
                and chat_type == "private"
                and not self.chat_id
            ):
                await self.send_to(
                    incoming_chat,
                    f"Your Telegram chat ID is: <code>{html.escape(incoming_chat)}</code>\n\n"
                    "Set <code>TELEGRAM_CHAT_ID</code> to this exact value and restart the command service.",
                )
                continue
            # Every accounting command, including /filled, is behind the exact same
            # configured private chat-ID gate as the rest of production control.
            if not self.chat_id or incoming_chat != str(self.chat_id):
                continue

            if low in {"/status", "status"}:
                await self.send_status()
            elif low in {"/stats", "stats"}:
                await self.send_stats()
            elif low in {"/mystats", "mystats"}:
                await self.send_manual_stats()
            elif low in {"/recent", "recent"}:
                rows = await asyncio.to_thread(self.store.recent, 10)
                body = ["🧾 <b>Recent alerts</b>"] + [
                    f"#{x['id']} {html.escape(str(x['detector']))} — "
                    f"{html.escape(str(x['status']))} — {html.escape(str(x['title'])[:70])}"
                    for x in rows
                ]
                await self.send("\n".join(body))
            elif low in {"/taken", "taken"}:
                rows = await asyncio.to_thread(self.store.recent_manual, 10)
                body = ["💼 <b>Recently recorded directional/single-leg fills</b>"]
                for row in rows:
                    if str(row.get("entry_source") or "") != "USER_REPORTED_EXECUTION":
                        continue
                    body.append(
                        f"Trade #{row['id']} / alert #{row['signal_id']} — stake ${float(row['stake']):.2f} — "
                        f"actual cost {float(row['entry_cost']):.4f} — {html.escape(str(row['status']))} — "
                        f"{html.escape(str(row['title'])[:55])}"
                    )
                if len(body) == 1:
                    body.append("No valid directional/single-leg fills recorded yet.")
                await self.send("\n".join(body))
            elif low.startswith("/filled"):
                parsed = parse_structural_fill_command(text)
                if parsed is None:
                    await self.send(structural_fill_help())
                    continue
                signal_id, fills = parsed
                try:
                    row = await asyncio.to_thread(
                        record_structural_fills,
                        self.store,
                        signal_id,
                        fills,
                    )
                except ValueError as exc:
                    await self.send(f"Could not record structural fills: {html.escape(str(exc))}")
                    continue

                conforming = bool(row["within_cert_limits"] and row["within_cert_capacity"])
                if conforming:
                    evidence = (
                        "✅ Fill stayed within the alert's certified MAX prices and safe capacity. "
                        "It is eligible for the prospective certified-policy execution sample."
                    )
                else:
                    evidence = (
                        "⚠️ The real execution was recorded, but it exceeded a certified MAX price and/or safe capacity. "
                        "It is excluded from certified-policy strategy evidence."
                    )
                await self.send(
                    f"✅ Recorded structural trade #{row['id']} from alert #{row['signal_id']} with "
                    f"<b>{len(row['legs'])}</b> exact legs.\n"
                    f"Equal shares: <b>{float(row['shares']):.4f}</b> | actual cash paid incl. reported fees: "
                    f"<b>${float(row['total_cash_cost']):.4f}</b> | actual bundle cost: "
                    f"<b>{float(row['bundle_cost']):.6f}</b>.\n"
                    f"{evidence}\n"
                    "P&amp;L remains OPEN until every exact token is objectively resolved. "
                    "This is user-reported fill evidence, not exchange-verified execution."
                )
            elif low.startswith("/took"):
                parsed = _parse_took_command(text)
                if parsed is None:
                    await self.send(
                        "Use for directional/single-leg trades only: "
                        "<code>/took ALERT_ID STAKE_USD ACTUAL_COST</code>\n"
                        "Example: <code>/took 137 50 0.943</code>\n\n"
                        "For a structural/multi-leg TRADE NOW use <code>/filled</code> so every actual leg is recorded."
                    )
                    continue
                signal_id, stake, actual_cost = parsed
                signal_row = await asyncio.to_thread(self.store.get_signal, signal_id)
                if signal_row and str(signal_row.get("detector") or "") in STRUCTURAL_DETECTORS:
                    await self.send(
                        "Structural alerts cannot use <code>/took</code>. A combined number cannot prove every leg filled.\n\n"
                        + structural_fill_help()
                    )
                    continue
                try:
                    row = await asyncio.to_thread(
                        self.store.record_manual,
                        signal_id,
                        stake,
                        actual_cost,
                    )
                    await self.send(
                        f"✅ Recorded actual directional/single-leg trade #{row['id']} from alert #{row['signal_id']}.\n"
                        f"Stake: <b>${float(row['stake']):.2f}</b> | actual executed cost: "
                        f"<b>{float(row['entry_cost']):.4f}</b>.\n"
                        "Future P&amp;L uses this execution cost and the actual settlement payout—not the old alert quote."
                    )
                except ValueError as exc:
                    await self.send(f"Could not record that trade: {html.escape(str(exc))}")
            elif low in {"/help", "help", "/start"}:
                await self.send(
                    "Commands:\n"
                    "/status — live scanner health and feed status\n"
                    "/stats — evidence audit for scanner detectors\n"
                    "/mystats — your user-reported execution ledger\n"
                    "/recent — recent stored scanner alerts\n"
                    "/taken — recent directional/single-leg fills\n"
                    "/took ALERT_ID STAKE_USD ACTUAL_COST — record a directional/single-leg fill\n"
                    "/filled ALERT_ID 1=SHARES@AVG_PRICE+FEE ... — record every structural leg\n"
                    "/help — this list"
                )

        await asyncio.to_thread(self.store.set_state, "telegram_offset", str(offset))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        self.last_command_error = worker._sanitize_error(exc)
        worker.log.warning(
            "Telegram getUpdates/command handling failed: %s", self.last_command_error
        )
        raise


def install_trade_only_policy() -> None:
    """Patch only the deployed trade-only process; research/test modules stay generic."""
    Telegram._post_message = _safe_post_message
    Telegram.send_signal = _guarded_send_signal
    Telegram.send_manual_stats = _safe_manual_stats
    Telegram.poll_commands = _safe_poll_commands
    worker.alert_delivery_loop = _trade_delivery_loop


async def main() -> None:
    global _silent_shadow_process
    from polymarket_scanner.trade_only import promoted_detectors
    if promoted_detectors():
        raise RuntimeError("silent-shadow command worker requires zero detector promotions")
    _silent_shadow_process = True
    install_trade_only_policy()
    try:
        await worker.main()
    finally:
        global _delivery_poly
        if _delivery_poly is not None:
            await _delivery_poly.close()
            _delivery_poly = None


if __name__ == "__main__":
    asyncio.run(main())
