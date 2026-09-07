from __future__ import annotations

import html
import math
from datetime import datetime, timezone
from typing import Any

from .config import settings
from .hardening import MAX_MANUAL_LEGS, MIN_VISIBLE_NOTIONAL_USD
from .models import Signal

TRADE_READY_VERSION = "trade_now_v1"
TRADE_READY_TTL_SECONDS = 8.0

# P0 containment policy (2026-09-07): no detector is promoted to real-money
# TRADE NOW until its semantic, execution, delivery, accounting and evidence gates
# are independently satisfied. Research/experimental candidates may still be
# discovered, stored and scored in the background, but none may reach Telegram as
# an execution instruction while this registry is empty.
_CERTIFICATIONS: dict[str, str] = {}


def promoted_detectors() -> tuple[str, ...]:
    """Return the exact detector IDs currently authorized for TRADE NOW."""
    return tuple(sorted(_CERTIFICATIONS))


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _confirmation_age_seconds(value: object, now: datetime | None = None) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    current = now or datetime.now(timezone.utc)
    age = (current.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
    # A future timestamp is not evidence of freshness; only tolerate tiny clock
    # jitter. Anything materially future-dated fails closed.
    if age < -1.0:
        return None
    return max(0.0, age)


def mark_trade_readiness(signal: Signal) -> bool:
    """Mark a freshly REST-confirmed signal as safe for automatic Telegram delivery.

    This is deliberately stricter than detector confidence. Research/experimental
    signals remain stored and scoreable, but Telegram is reserved for signals whose
    semantic structure was certified by code AND whose executable order book was
    refreshed immediately before persistence.

    During P0 containment the promotion registry is intentionally empty, so every
    signal fails closed before any real-money delivery permission is created.
    """
    m = signal.metadata
    m["trade_ready"] = False
    m["trade_ready_version"] = TRADE_READY_VERSION

    if signal.confidence != "ACTIONABLE":
        m["trade_ready_reason"] = "research/experimental signal"
        return False

    required_cert = _CERTIFICATIONS.get(signal.detector)
    if required_cert is None:
        m["trade_ready_reason"] = "detector is not promoted to TRADE NOW (P0 containment)"
        return False
    if m.get("certification_status") != required_cert:
        m["trade_ready_reason"] = "detector certification did not pass"
        return False

    confirmation_age = _confirmation_age_seconds(m.get("rest_confirmed_at"))
    if confirmation_age is None:
        m["trade_ready_reason"] = "valid REST confirmation timestamp missing"
        return False
    if confirmation_age > TRADE_READY_TTL_SECONDS:
        m["trade_ready_reason"] = "REST confirmation already expired"
        return False

    asks = list(m.get("confirmed_asks") or [])
    sizes = list(m.get("confirmed_sizes") or [])
    if len(asks) != len(signal.token_ids) or len(sizes) != len(signal.token_ids) or not signal.token_ids:
        m["trade_ready_reason"] = "confirmed leg count does not match signal"
        return False
    asks_f = [_float(x) for x in asks]
    sizes_f = [_float(x) for x in sizes]
    if any(x is None for x in asks_f) or any(x is None for x in sizes_f):
        m["trade_ready_reason"] = "confirmed price/size data invalid or nonfinite"
        return False
    if any(x <= 0 or x >= 1 for x in asks_f if x is not None) or any(x <= 0 for x in sizes_f if x is not None):
        m["trade_ready_reason"] = "confirmed price/size data not executable"
        return False

    edge = _float(signal.edge)
    cost = _float(signal.entry_cost)
    capacity = _float(m.get("max_visible_notional_usd"))
    if edge is None or edge < settings.actionable_min_edge:
        m["trade_ready_reason"] = "post-confirmation edge below TRADE NOW floor"
        return False
    if cost is None or cost <= 0 or cost >= 1:
        m["trade_ready_reason"] = "post-confirmation combined cost invalid"
        return False
    if capacity is None or capacity < MIN_VISIBLE_NOTIONAL_USD:
        m["trade_ready_reason"] = "visible executable capacity below manual floor"
        return False

    if signal.detector == "binary_buy_both" and len(signal.token_ids) != 2:
        m["trade_ready_reason"] = "binary complement must have exactly two legs"
        return False
    if signal.detector == "nested_threshold_arb" and len(signal.token_ids) != 2:
        m["trade_ready_reason"] = "nested threshold trade must have exactly two legs"
        return False
    if signal.detector == "neg_risk_underround" and not (2 <= len(signal.token_ids) <= MAX_MANUAL_LEGS):
        m["trade_ready_reason"] = "neg-risk basket exceeds manual leg limit"
        return False

    m["trade_ready"] = True
    m["trade_ready_created_at"] = datetime.now(timezone.utc).isoformat()
    m["trade_ready_expires_in_seconds"] = TRADE_READY_TTL_SECONDS
    m["trade_ready_reason"] = "semantic certification + fresh executable book + edge + capacity passed"
    return True


def is_trade_ready(signal: Signal) -> bool:
    if not (
        signal.confidence == "ACTIONABLE"
        and signal.metadata.get("trade_ready") is True
        and signal.metadata.get("trade_ready_version") == TRADE_READY_VERSION
        and signal.detector in _CERTIFICATIONS
    ):
        return False
    age = _confirmation_age_seconds(signal.metadata.get("rest_confirmed_at"))
    return age is not None and age <= TRADE_READY_TTL_SECONDS


def _price_lines(signal: Signal) -> list[str]:
    asks = [_float(x) for x in (signal.metadata.get("confirmed_asks") or [])]
    if any(x is None for x in asks):
        return []
    clean = [float(x) for x in asks if x is not None]
    if signal.detector == "binary_buy_both" and len(clean) == 2:
        return [f"Buy YES ≤ {clean[0]:.3f}", f"Buy NO ≤ {clean[1]:.3f}"]
    if signal.detector == "nested_threshold_arb" and len(clean) == 2:
        return [f"Leg 1 ≤ {clean[0]:.3f}", f"Leg 2 ≤ {clean[1]:.3f}"]
    if signal.detector == "neg_risk_underround":
        return [f"Leg {i} YES ≤ {ask:.3f}" for i, ask in enumerate(clean, 1)]
    return [f"Entry ≤ {clean[0]:.3f}"] if clean else []


async def send_trade_now(tg, signal_id: int, signal: Signal) -> None:
    """Send the compact execution-ready message. Non-ready signals are silent."""
    if not is_trade_ready(signal):
        return

    m = signal.metadata
    edge = _float(signal.edge) or 0.0
    cost = _float(signal.entry_cost) or 0.0
    capacity = _float(m.get("max_visible_notional_usd")) or 0.0
    common = _float(m.get("visible_common_shares")) or 0.0

    lines = [
        f"🚨 <b>TRADE NOW #{signal_id}</b>",
        f"<b>{html.escape(signal.title)}</b>",
        "",
        f"💰 Post-fee edge: <b>{edge:.2%}</b>",
        f"💵 Combined confirmed cost: <b>{cost:.4f}</b> per $1 payout",
        f"📏 Visible capacity: <b>about ${capacity:.2f}</b> | common size {common:.2f} shares",
        f"⏱ Certificate expires <b>{TRADE_READY_TTL_SECONDS:.0f}s</b> after REST confirmation.",
        "",
        "✅ <b>EXECUTE</b>",
    ]
    price_lines = _price_lines(signal)
    for i, text in enumerate(price_lines, 1):
        lines.append(f"{i}. {html.escape(text)}")
    lines.extend([
        f"{len(price_lines) + 1}. Use the SAME share count on every leg.",
        f"{len(price_lines) + 2}. If any live ask is now above the listed maximum or size is smaller, <b>SKIP</b> and wait for a fresh alert.",
        "",
        f"🛡 Bot checks passed: <b>{html.escape(str(m.get('certification_status') or 'certified'))}</b> + fresh REST order book + fees + edge + visible size.",
        f"🧾 Took it? Send <code>/took {signal_id} 50</code> (replace 50 with your US$ stake).",
    ])
    text = "\n".join(lines)
    await tg.send_alert(text, tg._buttons(signal))
