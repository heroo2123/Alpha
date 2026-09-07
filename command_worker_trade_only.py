from __future__ import annotations

import asyncio
import html
import re
import time

import httpx

import command_worker as worker
from polymarket_scanner.config import settings
from polymarket_scanner.polymarket import PolymarketClient
from polymarket_scanner.telegram import Telegram
from polymarket_scanner.trade_only import refresh_trade_readiness, send_trade_now
from polymarket_scanner.weather_calibration import WEATHER_DETECTORS, apply_weather_calibration


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
    """Parse /took ALERT_ID STAKE_USD ACTUAL_COST only.

    ACTUAL_COST is the user's genuinely executed cost per $1 payout unit. For a
    multi-leg complete-set trade it is the combined cost of all filled legs for one
    equal-share bundle. Two-argument legacy syntax is intentionally rejected.
    """
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

    For TRADE NOW alerts we do not perform blind network retries. A timeout or 5xx
    may have happened after Telegram accepted the request, so retrying could create
    a duplicate stale instruction. Only an explicit 429 (which means no message was
    accepted) is classified retryable, and the outbox will revalidate the market
    before the next attempt.

    Ordinary command/control replies may retry because duplicate /status text is not
    a trading hazard.
    """
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
    # Every delivery attempt must rebuild executable evidence from current Gamma +
    # CLOB state. Stored detector-time quotes are never trusted on a retry.
    # Weather additionally recomputes its prospective calibration from the current
    # clean resolved database here, so a stale/forged metadata flag cannot promote
    # an uncalibrated heuristic into a money instruction.
    if signal.detector in WEATHER_DETECTORS:
        await asyncio.to_thread(apply_weather_calibration, signal, self.store.path)
    if not await refresh_trade_readiness(signal, _poly()):
        reason = str(signal.metadata.get("trade_ready_reason") or "not TRADE NOW eligible")
        raise worker.AlertSuppressed(reason)
    await send_trade_now(self, signal_id, signal)


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
                    worker.log.warning("quarantined %d stale in-flight Telegram deliveries as UNCERTAIN", recovered)
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
                worker.log.warning("confirmed no-delivery for alert %s; queued for fresh revalidation: %s", item["signal_id"], safe)
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
                worker.log.error("Telegram delivery for alert %s is UNCERTAIN; not retrying: %s", item["signal_id"], safe)
                continue
            except Exception as exc:
                safe = worker._sanitize_error(exc)
                # Unknown exception after the claim is treated as ambiguous. Safety
                # wins over availability: never blindly duplicate a trade instruction.
                await asyncio.to_thread(outbox.mark_uncertain, int(item["id"]), safe)
                await asyncio.to_thread(store.set_state, "telegram_outbox_error", safe)
                worker.log.exception("unexpected alert delivery failure; quarantined as UNCERTAIN: %s", safe)
                continue

            await asyncio.to_thread(outbox.mark_sent, int(item["id"]))
            await asyncio.to_thread(store.set_state, "telegram_outbox_error", "")
            last_sent = time.monotonic()
            worker.log.info("delivered persistent TRADE NOW alert %s with Telegram receipt", item["signal_id"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            worker.log.exception("trade-only Telegram delivery loop failed: %s", worker._sanitize_error(exc))
            await asyncio.sleep(1.0)


async def _safe_manual_stats(self: Telegram) -> None:
    st = await asyncio.to_thread(self.store.manual_stats)
    lines = [
        "💼 <b>Your actual-fill trade ledger</b>",
        f"Valid trades: <b>{int(st['total'])}</b> | Won: <b>{int(st['won'])}</b> | Lost: <b>{int(st['lost'])}</b> | Partial: <b>{int(st.get('partial') or 0)}</b> | Open: <b>{int(st['open'])}</b>",
        f"Tracked stake: <b>${float(st['stake']):.2f}</b>",
        f"P&amp;L from your reported execution costs + actual settlement payouts: <b>${float(st['pnl']):.2f}</b>",
    ]
    legacy = int(st.get("legacy_excluded") or 0)
    if legacy:
        lines.append(f"Legacy alert-price rows excluded from valid P&amp;L: <b>{legacy}</b>")
    await self.send("\n".join(lines))


async def _safe_poll_commands(self: Telegram) -> None:
    """Production command parser with actual-fill-only manual accounting."""
    if not self.token_enabled:
        return
    await self.ensure_command_menu()
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

            if low in {"/whoami", "whoami", "/start"} and incoming_chat and chat_type == "private" and not self.chat_id:
                await self.send_to(
                    incoming_chat,
                    f"Your Telegram chat ID is: <code>{html.escape(incoming_chat)}</code>\n\n"
                    "Set <code>TELEGRAM_CHAT_ID</code> to this exact value and restart the command service.",
                )
                continue
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
                    f"#{x['id']} {html.escape(str(x['detector']))} — {html.escape(str(x['status']))} — {html.escape(str(x['title'])[:70])}"
                    for x in rows
                ]
                await self.send("\n".join(body))
            elif low in {"/taken", "taken"}:
                rows = await asyncio.to_thread(self.store.recent_manual, 10)
                body = ["💼 <b>Recently recorded actual-fill trades</b>"]
                for row in rows:
                    source = str(row.get("entry_source") or "")
                    if source != "USER_REPORTED_EXECUTION":
                        continue
                    body.append(
                        f"Trade #{row['id']} / alert #{row['signal_id']} — stake ${float(row['stake']):.2f} — "
                        f"actual cost {float(row['entry_cost']):.4f} — {html.escape(str(row['status']))} — "
                        f"{html.escape(str(row['title'])[:55])}"
                    )
                if len(body) == 1:
                    body.append("No valid actual-fill trades recorded yet.")
                await self.send("\n".join(body))
            elif low.startswith("/took"):
                parsed = _parse_took_command(text)
                if parsed is None:
                    await self.send(
                        "Use: <code>/took ALERT_ID STAKE_USD ACTUAL_COST</code>\n"
                        "Example: <code>/took 137 50 0.943</code>\n\n"
                        "ACTUAL_COST must be what you really paid per $1 payout unit. "
                        "For a multi-leg trade, enter the combined cost of one equal-share complete bundle. "
                        "The old two-number command is rejected because an alert quote is not an execution."
                    )
                else:
                    signal_id, stake, actual_cost = parsed
                    try:
                        row = await asyncio.to_thread(
                            self.store.record_manual,
                            signal_id,
                            stake,
                            actual_cost,
                        )
                        await self.send(
                            f"✅ Recorded actual trade #{row['id']} from alert #{row['signal_id']}.\n"
                            f"Stake: <b>${float(row['stake']):.2f}</b> | actual executed cost: <b>{float(row['entry_cost']):.4f}</b>.\n"
                            "Future P&amp;L uses this execution cost and the actual settlement payout—not the old alert quote."
                        )
                    except ValueError as exc:
                        await self.send(f"Could not record that trade: {html.escape(str(exc))}")
            elif low in {"/help", "help", "/start"}:
                await self.send(
                    "Commands:\n"
                    "/status — live scanner health and feed status\n"
                    "/stats — evidence audit for scanner detectors\n"
                    "/mystats — P&amp;L from your actual recorded fills\n"
                    "/recent — recent stored scanner alerts\n"
                    "/taken — your recent valid actual-fill trades\n"
                    "/took ALERT_ID STAKE_USD ACTUAL_COST — record a trade you really executed\n"
                    "/help — this list"
                )

        await asyncio.to_thread(self.store.set_state, "telegram_offset", str(offset))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        self.last_command_error = worker._sanitize_error(exc)
        worker.log.warning("Telegram getUpdates/command handling failed: %s", self.last_command_error)
        raise


def install_trade_only_policy() -> None:
    """Patch only the deployed trade-only process; research/test modules stay generic."""
    Telegram._post_message = _safe_post_message
    Telegram.send_signal = _guarded_send_signal
    Telegram.send_manual_stats = _safe_manual_stats
    Telegram.poll_commands = _safe_poll_commands
    worker.alert_delivery_loop = _trade_delivery_loop


async def main() -> None:
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
