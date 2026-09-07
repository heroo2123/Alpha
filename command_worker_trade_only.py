from __future__ import annotations

import asyncio

import httpx

import command_worker as worker
from polymarket_scanner.telegram import Telegram
from polymarket_scanner.trade_only import is_trade_ready, send_trade_now


async def _safe_post_message(
    self: Telegram,
    client: httpx.AsyncClient,
    chat_id: str | int,
    text: str,
    reply_markup: dict | None = None,
    *,
    lane: str,
) -> int | None:
    """Production Bot API send: success requires Telegram's message receipt.

    This replaces the legacy behavior where three HTTP 429 responses could fall
    out of the retry loop and be mistaken for a successful send. Errors raised
    from this boundary contain no token-bearing request URL.
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
    for attempt in range(3):
        try:
            response = await client.post(url, json=payload)
            if response.status_code == 429:
                retry_after = 1.0
                try:
                    body = response.json()
                    retry_after = float((body.get("parameters") or {}).get("retry_after", 1))
                except Exception:
                    retry_after = 1.0
                retry_after = max(1.0, retry_after)
                if attempt >= 2:
                    raise RuntimeError(f"Telegram rate limited; retry_after={retry_after:.0f}s")
                await asyncio.sleep(min(retry_after, 15.0))
                continue

            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict) or body.get("ok") is not True:
                raise RuntimeError("Telegram API rejected sendMessage")
            result = body.get("result")
            message_id = result.get("message_id") if isinstance(result, dict) else None
            if type(message_id) is not int:
                raise RuntimeError("Telegram success response lacked message_id receipt")

            if lane == "command":
                self.last_command_error = None
            else:
                self.last_alert_error = None
            return message_id
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            safe = worker._sanitize_error(exc)
            if lane == "command":
                self.last_command_error = safe
            else:
                self.last_alert_error = safe
            if attempt >= 2:
                raise RuntimeError(f"Telegram {lane} send failed: {safe}") from None
            await asyncio.sleep(0.75 * (attempt + 1))

    # Defensive: the loop must never terminate as an apparent success.
    raise RuntimeError(f"Telegram {lane} send exhausted retries")


async def _guarded_send_signal(self: Telegram, signal_id: int, signal) -> None:
    # Old WATCH/actionable rows may still exist in the persistent outbox from prior
    # builds. During P0 containment no detector is promoted. Mark such rows as
    # SUPPRESSED through the command worker instead of pretending they were SENT.
    if not is_trade_ready(signal):
        raise worker.AlertSuppressed("not TRADE NOW eligible under current P0 containment policy")
    await send_trade_now(self, signal_id, signal)


# Production command/delivery process owns Telegram transport. Keep the base module
# usable for tests/research, but make the deployed worker's send boundary strict.
Telegram._post_message = _safe_post_message
Telegram.send_signal = _guarded_send_signal


if __name__ == "__main__":
    asyncio.run(worker.main())
