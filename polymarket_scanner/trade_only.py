from __future__ import annotations

import asyncio
import html
import json
import math
from datetime import datetime, timezone
from typing import Any

from .config import settings
from .hardening import MAX_MANUAL_LEGS, MIN_VISIBLE_NOTIONAL_USD
from .models import Signal
from .polymarket import taker_fee_per_share
from .weather_calibration import WEATHER_CALIBRATION_VERSION, WEATHER_DETECTORS

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


def _json_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except Exception:
            return []
        return [str(x) for x in decoded] if isinstance(decoded, list) else []
    return []


def _delivery_market_ids(signal: Signal) -> list[str]:
    """Recover every market whose state must still be open at delivery time."""
    if signal.detector == "neg_risk_underround":
        ids = [str(x.get("market_id") or "") for x in (signal.metadata.get("legs") or []) if isinstance(x, dict)]
        return list(dict.fromkeys(x for x in ids if x))
    if signal.detector == "nested_threshold_arb":
        key = str(signal.metadata.get("fingerprint_key") or "")
        left, sep, right = key.partition(":")
        if sep and left and right:
            return [left, right]
    return [str(signal.market_id)] if signal.market_id else []


def _open_market_state(raw: object) -> bool:
    if not isinstance(raw, dict):
        return False
    if raw.get("active") is False or raw.get("closed") is True:
        return False
    if raw.get("acceptingOrders") is False or raw.get("enableOrderBook") is False:
        return False
    return True


def _weather_probability_floor(signal: Signal) -> float | None:
    """Return only a prospective empirical lower bound, never the raw heuristic."""
    if signal.detector not in WEATHER_DETECTORS:
        return None
    m = signal.metadata
    if m.get("weather_calibration_version") != WEATHER_CALIBRATION_VERSION:
        return None
    if m.get("weather_calibration_ready") is not True:
        return None
    floor = _float(m.get("calibrated_probability_lower_bound"))
    if floor is None or floor <= 0.0 or floor > 1.0:
        return None
    return floor


def mark_trade_readiness(signal: Signal) -> bool:
    """Mark a freshly REST-confirmed signal as safe for automatic Telegram delivery.

    Detector confidence is not permission to trade. A signal must belong to the
    explicit promotion registry, carry the matching semantic certificate, and have
    a very recent executable-book confirmation. P0 keeps the registry empty.

    Weather has one additional irreversible rule: a heuristic lock score can never
    be used as a money probability. Even after explicit detector promotion, weather
    requires a prospective empirical calibration sample and uses only its
    conservative lower confidence bound for the final edge calculation.
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

    cost = _float(signal.entry_cost)
    capacity = _float(m.get("max_visible_notional_usd"))
    if cost is None or cost <= 0 or cost >= 1:
        m["trade_ready_reason"] = "post-confirmation combined cost invalid"
        return False
    if capacity is None or capacity < MIN_VISIBLE_NOTIONAL_USD:
        m["trade_ready_reason"] = "visible executable capacity below manual floor"
        return False

    if signal.detector in WEATHER_DETECTORS:
        if len(signal.token_ids) != 1:
            m["trade_ready_reason"] = "weather TRADE NOW must be a single selected bucket token"
            return False
        probability_floor = _weather_probability_floor(signal)
        if probability_floor is None:
            m["trade_ready_reason"] = "weather empirical calibration gate did not pass"
            return False
        edge = probability_floor - cost
        signal.edge = edge
        m["trade_probability_basis"] = "WEATHER_EMPIRICAL_LOWER_BOUND"
        m["trade_probability"] = probability_floor
    else:
        edge = _float(signal.edge)

    if edge is None or edge < settings.actionable_min_edge:
        m["trade_ready_reason"] = "post-confirmation edge below TRADE NOW floor"
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
    if signal.detector in WEATHER_DETECTORS:
        m["trade_ready_reason"] = (
            "semantic certification + prospective empirical weather calibration lower bound + "
            "fresh open market state + executable book + fees + edge + visible size passed"
        )
    else:
        m["trade_ready_reason"] = "semantic certification + fresh open market state + executable book + fees + edge + visible size passed"
    return True


async def refresh_trade_readiness(signal: Signal, poly) -> bool:
    """Re-check market state, tokens, prices, size, fees and edge just before send.

    This is the delivery-time boundary Astra's review found missing. A retry never
    reuses the quote snapshot stored when the detector first fired. If anything is
    closed, non-tradable, missing, too expensive or too small, the alert fails
    closed and is suppressed rather than being delivered stale.

    For weather, the detector's raw lock score is deliberately ignored here. Only a
    calibration lower bound attached from the clean resolved database may supply the
    probability side of the edge calculation.
    """
    m = signal.metadata
    m["trade_ready"] = False

    if signal.confidence != "ACTIONABLE" or signal.detector not in _CERTIFICATIONS:
        m["trade_ready_reason"] = "detector is not promoted to TRADE NOW (P0 containment)"
        return False

    if signal.detector in WEATHER_DETECTORS and _weather_probability_floor(signal) is None:
        m["trade_ready_reason"] = "weather empirical calibration gate did not pass"
        return False

    market_ids = _delivery_market_ids(signal)
    if not market_ids:
        m["trade_ready_reason"] = "delivery-time market IDs unavailable"
        return False

    raw_markets = await asyncio.gather(*(poly.market_by_id(mid) for mid in market_ids))
    if any(not _open_market_state(raw) for raw in raw_markets):
        m["trade_ready_reason"] = "market closed or not accepting orders at delivery time"
        return False

    current_tokens: set[str] = set()
    for raw in raw_markets:
        if isinstance(raw, dict):
            current_tokens.update(_json_list(raw.get("clobTokenIds")))
    if current_tokens and any(str(token) not in current_tokens for token in signal.token_ids):
        m["trade_ready_reason"] = "market token mapping changed before delivery"
        return False

    fresh = await poly.books(signal.token_ids)
    if any(token not in fresh or fresh[token].best_ask is None or fresh[token].best_ask_size <= 0 for token in signal.token_ids):
        m["trade_ready_reason"] = "one or more executable asks disappeared before delivery"
        return False

    asks = [float(fresh[token].best_ask) for token in signal.token_ids]
    sizes = [float(fresh[token].best_ask_size) for token in signal.token_ids]
    if any(not math.isfinite(x) or x <= 0 or x >= 1 for x in asks):
        m["trade_ready_reason"] = "delivery-time ask invalid"
        return False
    if any(not math.isfinite(x) or x <= 0 for x in sizes):
        m["trade_ready_reason"] = "delivery-time visible size invalid"
        return False

    fees = sum(taker_fee_per_share(ask) for ask in asks)
    cost = sum(asks) + fees
    if signal.detector in WEATHER_DETECTORS:
        probability = _weather_probability_floor(signal)
        if probability is None:
            m["trade_ready_reason"] = "weather empirical calibration gate did not pass"
            return False
        edge = probability - cost
        m["trade_probability_basis"] = "WEATHER_EMPIRICAL_LOWER_BOUND"
        m["trade_probability"] = probability
    else:
        # Non-weather one-leg known-outcome adapters (e.g. terminal sports/crypto)
        # only become promotable after their own semantic certification, at which
        # point the payoff is objectively known and the probability side is 1.0.
        edge = 1.0 - cost

    common = min(sizes)
    capacity = common * cost

    signal.entry_cost = cost
    signal.edge = edge
    now = datetime.now(timezone.utc).isoformat()
    m["confirmed_asks"] = asks
    m["confirmed_sizes"] = sizes
    m["visible_common_shares"] = common
    m["max_visible_notional_usd"] = capacity
    m["rest_confirmed_at"] = now
    m["delivery_market_state_at"] = now
    m["delivery_market_ids"] = market_ids
    m["delivery_revalidated"] = True
    return mark_trade_readiness(signal)


def is_trade_ready(signal: Signal) -> bool:
    if not (
        signal.confidence == "ACTIONABLE"
        and signal.metadata.get("trade_ready") is True
        and signal.metadata.get("trade_ready_version") == TRADE_READY_VERSION
        and signal.detector in _CERTIFICATIONS
    ):
        return False
    if signal.detector in WEATHER_DETECTORS and _weather_probability_floor(signal) is None:
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
    ]
    if signal.detector in WEATHER_DETECTORS:
        probability = _weather_probability_floor(signal)
        if probability is not None:
            lines.append(f"🌦 Conservative calibrated probability floor: <b>{probability:.2%}</b>.")
    lines.extend(["", "✅ <b>EXECUTE</b>"])
    price_lines = _price_lines(signal)
    for i, text in enumerate(price_lines, 1):
        lines.append(f"{i}. {html.escape(text)}")
    lines.extend([
        f"{len(price_lines) + 1}. Use the SAME share count on every leg.",
        f"{len(price_lines) + 2}. If any live ask is now above the listed maximum or size is smaller, <b>SKIP</b> and wait for a fresh alert.",
        "",
        f"🛡 Bot checks passed: <b>{html.escape(str(m.get('certification_status') or 'certified'))}</b> + open market + fresh REST order book + fees + edge + visible size.",
        "🧾 Took it? Record your actual executed cost; alert quotes are never used as realized P&L.",
    ])
    text = "\n".join(lines)
    await tg.send_alert(text, tg._buttons(signal))
