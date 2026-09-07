from __future__ import annotations

import asyncio
import html
import math
from datetime import datetime, timezone
from typing import Any

from .config import settings
from .execution_certificate import (
    EXECUTION_CERTIFICATE_TTL_SECONDS,
    build_execution_certificate,
    validate_execution_certificate,
)
from .hardening import MAX_MANUAL_LEGS
from .models import Signal
from .weather_calibration import WEATHER_CALIBRATION_VERSION, WEATHER_DETECTORS

TRADE_READY_VERSION = "trade_now_v3_depth_limits"
TRADE_READY_TTL_SECONDS = EXECUTION_CERTIFICATE_TTL_SECONDS


class TradeNowPreSendInvalid(RuntimeError):
    """A local fail-closed condition occurred before Telegram transport was invoked."""


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
    if age < -1.0:
        return None
    return max(0.0, age)


def _delivery_market_ids(signal: Signal) -> list[str]:
    """Recover every market whose state must still be open at delivery time."""
    if signal.detector == "neg_risk_underround":
        ids = [
            str(x.get("market_id") or "")
            for x in (signal.metadata.get("legs") or [])
            if isinstance(x, dict)
        ]
        return list(dict.fromkeys(x for x in ids if x))
    if signal.detector == "nested_threshold_arb":
        key = str(signal.metadata.get("fingerprint_key") or "")
        left, sep, right = key.partition(":")
        if sep and left and right:
            return [left, right]
    return [str(signal.market_id)] if signal.market_id else []


def _open_market_state(raw: object) -> bool:
    """Promotion requires explicit current tradability; missing fields fail closed."""
    if not isinstance(raw, dict):
        return False
    return (
        raw.get("active") is True
        and raw.get("closed") is False
        and raw.get("acceptingOrders") is True
        and raw.get("enableOrderBook") is True
    )


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


def _edge_from_certificate(signal: Signal, cost: float) -> tuple[float | None, str]:
    if signal.detector in WEATHER_DETECTORS:
        probability = _weather_probability_floor(signal)
        if probability is None:
            return None, "weather empirical calibration gate did not pass"
        signal.metadata["trade_probability_basis"] = "WEATHER_EMPIRICAL_LOWER_BOUND"
        signal.metadata["trade_probability"] = probability
        return probability - cost, ""

    payout = _float(signal.theoretical_payout)
    if payout is None or abs(payout - 1.0) > 1e-12:
        return None, "non-weather TRADE NOW requires a certified $1 payout unit"
    return 1.0 - cost, ""


def _leg_count_ok(signal: Signal) -> tuple[bool, str]:
    if signal.detector == "binary_buy_both" and len(signal.token_ids) != 2:
        return False, "binary complement must have exactly two legs"
    if signal.detector == "nested_threshold_arb" and len(signal.token_ids) != 2:
        return False, "nested threshold trade must have exactly two legs"
    if signal.detector == "neg_risk_underround" and not (2 <= len(signal.token_ids) <= MAX_MANUAL_LEGS):
        return False, "neg-risk basket exceeds manual leg limit"
    if signal.detector in WEATHER_DETECTORS and len(signal.token_ids) != 1:
        return False, "weather TRADE NOW must be a single selected bucket token"
    return True, ""


def _apply_certificate_economics(signal: Signal, derived: dict) -> tuple[float, float, float, float]:
    """Overwrite detector-time execution estimates with immutable certificate values.

    This happens even when the candidate is subsequently rejected for insufficient
    edge. Audits and UI must never retain a stale detector-time price/edge after a
    newer exact CLOB certificate has been built.
    """
    cost = float(derived["cost"])
    capacity = float(derived["capacity"])
    common = float(derived["common_visible"])
    safe_common = float(derived["safe_common"])
    legs = derived["legs"]

    signal.entry_cost = cost
    m = signal.metadata
    m["confirmed_asks"] = [float(leg["ask"]) for leg in legs]
    m["confirmed_sizes"] = [float(leg["visible_best_ask_size"]) for leg in legs]
    m["visible_common_shares"] = common
    m["safe_common_shares"] = safe_common
    m["max_visible_notional_usd"] = capacity
    m["rest_confirmed_at"] = derived["checked_at"].isoformat()
    return cost, capacity, common, safe_common


def mark_trade_readiness(signal: Signal) -> bool:
    """Authorize only a fresh, self-validating exact CLOB execution certificate."""
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
        m["trade_ready_reason"] = "detector semantic certification did not pass"
        return False

    legs_ok, reason = _leg_count_ok(signal)
    if not legs_ok:
        m["trade_ready_reason"] = reason
        return False

    cert_ok, cert_reason, derived = validate_execution_certificate(signal)
    if not cert_ok or derived is None:
        m["trade_ready_reason"] = cert_reason
        return False

    try:
        cost, capacity, common, safe_common = _apply_certificate_economics(signal, derived)
    except (TypeError, ValueError, OverflowError, KeyError):
        signal.edge = None
        m["trade_ready_reason"] = "execution certificate derived values could not be canonicalized"
        return False
    if not all(math.isfinite(x) for x in (cost, capacity, common, safe_common)):
        signal.edge = None
        m["trade_ready_reason"] = "execution certificate derived nonfinite values"
        return False

    edge, reason = _edge_from_certificate(signal, cost)
    if edge is None or not math.isfinite(edge):
        signal.edge = None
        m["trade_ready_reason"] = reason or "post-certificate edge invalid"
        return False

    # Store the fresh economics before deciding whether they are good enough. A
    # rejection must not leave the older detector-time edge behind.
    signal.edge = edge
    if edge < settings.actionable_min_edge:
        m["trade_ready_reason"] = "post-certificate edge below TRADE NOW floor"
        return False

    m["trade_ready"] = True
    m["trade_ready_created_at"] = derived["checked_at"].isoformat()
    m["trade_ready_expires_at"] = derived["expires_at"].isoformat()
    m["trade_ready_expires_in_seconds"] = TRADE_READY_TTL_SECONDS
    if signal.detector in WEATHER_DETECTORS:
        m["trade_ready_reason"] = (
            "semantic certification + prospective empirical weather lower bound + "
            "fresh exact CLOB V2 legs + documented fees + conservative depth passed"
        )
    else:
        m["trade_ready_reason"] = (
            "semantic certification + fresh exact CLOB V2 legs + documented fees + "
            "conservative depth passed"
        )
    return True


async def refresh_trade_readiness(signal: Signal, poly) -> bool:
    """Rebuild all execution evidence from current Gamma + CLOB immediately before send.

    Every delivery attempt starts from current market state and a new short-lived
    exact-leg certificate. A stale certificate is deleted before any network work;
    retries therefore cannot reuse an old permission to trade.
    """
    m = signal.metadata
    m["trade_ready"] = False
    m.pop("execution_certificate", None)

    if signal.confidence != "ACTIONABLE":
        m["trade_ready_reason"] = "research/experimental signal"
        return False

    required_cert = _CERTIFICATIONS.get(signal.detector)
    if required_cert is None:
        m["trade_ready_reason"] = "detector is not promoted to TRADE NOW (P0 containment)"
        return False
    if m.get("certification_status") != required_cert:
        m["trade_ready_reason"] = "detector semantic certification did not pass"
        return False

    legs_ok, reason = _leg_count_ok(signal)
    if not legs_ok:
        m["trade_ready_reason"] = reason
        return False

    if signal.detector in WEATHER_DETECTORS and _weather_probability_floor(signal) is None:
        signal.edge = None
        m["trade_ready_reason"] = "weather empirical calibration gate did not pass"
        return False

    market_ids = _delivery_market_ids(signal)
    if not market_ids:
        m["trade_ready_reason"] = "delivery-time market IDs unavailable"
        return False

    raw_markets = await asyncio.gather(*(poly.market_by_id(mid) for mid in market_ids))
    if any(not _open_market_state(raw) for raw in raw_markets):
        m["trade_ready_reason"] = "market state is closed, non-tradable, or incomplete at delivery time"
        return False

    try:
        certificate = await build_execution_certificate(
            signal,
            poly,
            [raw for raw in raw_markets if isinstance(raw, dict)],
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        m["trade_ready_reason"] = f"exact CLOB execution certificate failed: {exc}"
        return False

    m["execution_certificate"] = certificate
    m["delivery_market_state_at"] = certificate["checked_at"]
    m["delivery_market_ids"] = market_ids
    m["delivery_revalidated"] = True
    return mark_trade_readiness(signal)


def is_trade_ready(signal: Signal) -> bool:
    if not (
        signal.confidence == "ACTIONABLE"
        and signal.metadata.get("trade_ready") is True
        and signal.metadata.get("trade_ready_version") == TRADE_READY_VERSION
        and signal.detector in _CERTIFICATIONS
        and signal.metadata.get("certification_status") == _CERTIFICATIONS.get(signal.detector)
    ):
        return False

    legs_ok, _ = _leg_count_ok(signal)
    if not legs_ok:
        return False
    cert_ok, _, derived = validate_execution_certificate(signal)
    if not cert_ok or derived is None:
        return False

    cost = float(derived["cost"])
    edge, _ = _edge_from_certificate(signal, cost)
    return edge is not None and math.isfinite(edge) and edge >= settings.actionable_min_edge


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%H:%M:%S UTC")


async def send_trade_now(tg, signal_id: int, signal: Signal) -> None:
    """Render and send only from the still-fresh exact execution certificate.

    Every exception raised before tg.send_alert() is explicitly classified as local
    pre-send suppression. Transport exceptions are allowed to escape unchanged so
    the delivery worker can distinguish retryable, rejected and uncertain outcomes.
    """
    try:
        if not is_trade_ready(signal):
            raise TradeNowPreSendInvalid("TRADE NOW certificate expired or became invalid before send")

        cert_ok, cert_reason, derived = validate_execution_certificate(signal)
        if not cert_ok or derived is None:
            raise TradeNowPreSendInvalid(cert_reason)

        cost = float(derived["cost"])
        top_cost = float(derived["top_cost"])
        capacity = float(derived["capacity"])
        safe_common = float(derived["safe_common"])
        minimum_bundle = float(derived["minimum_bundle_shares"])
        legs = derived["legs"]
        edge, reason = _edge_from_certificate(signal, cost)
        if edge is None:
            raise TradeNowPreSendInvalid(reason)

        total_fee = sum(float(leg["fee_per_share"]) for leg in legs)
        lines = [
            f"🚨 <b>TRADE NOW #{signal_id}</b>",
            f"<b>{html.escape(signal.title)}</b>",
            "",
            f"💰 Worst-case post-fee edge at MAX prices: <b>{edge:.2%}</b>",
            f"💵 Current combined ask cost: <b>{top_cost:.4f}</b> per $1 payout unit",
            f"🛑 Maximum certified combined cost: <b>{cost:.4f}</b> at the listed MAX prices",
            f"🧾 Worst-case taker fee model: <b>{total_fee:.5f}</b> per equal-share bundle",
            f"📏 Conservative capacity: <b>about ${capacity:.2f}</b> | max {safe_common:.2f} equal shares",
            f"📦 Minimum executable equal-share bundle: <b>{minimum_bundle:.2f} shares</b>",
            f"⏱ Checked <b>{_format_utc(derived['checked_at'])}</b> | expires <b>{_format_utc(derived['expires_at'])}</b>",
        ]
        if signal.detector in WEATHER_DETECTORS:
            probability = _weather_probability_floor(signal)
            if probability is not None:
                lines.append(f"🌦 Conservative calibrated probability floor: <b>{probability:.2%}</b>")

        lines.extend(["", "✅ <b>EXECUTE EXACTLY THESE LEGS</b>"])
        for i, leg in enumerate(legs, 1):
            outcome = html.escape(str(leg["outcome"]))
            question = html.escape(str(leg["question"]))
            ask_text = html.escape(str(leg["ask"]))
            # Render from the validator-checked numeric field, never from a separate
            # presentation string that could disagree with the certified limit.
            limit_text = html.escape(str(leg["safe_limit"]))
            safe_depth = float(leg["safe_depth_to_limit"])
            fee = float(leg["fee_per_share"])
            url = html.escape(str(leg["url"]), quote=True)
            lines.append(
                f"{i}. <b>BUY {outcome}</b> — current ask <b>{ask_text}</b> | MAX <b>{limit_text}</b> — {question}"
            )
            lines.append(
                f"   Safe depth to MAX: {safe_depth:.2f} shares (50% displayed-depth haircut) | "
                f"worst-case fee {fee:.5f}/share | <a href=\"{url}\">open market</a>"
            )

        if len(legs) > 1:
            lines.append(f"{len(legs) + 1}. Use the <b>SAME share count</b> on every leg.")
            lines.append(
                "⚠️ Manual multi-leg execution is non-atomic. Do not start unless every leg is available; "
                "after any partial fill this certificate no longer applies and no unwind economics are certified."
            )
        lines.extend([
            "",
            "🛑 <b>SKIP THE WHOLE TRADE</b> if any current ask is above its MAX, safe depth to MAX is below your intended equal-share size, the market is paused/closed, or any leg cannot be filled.",
            "🛡 This alert was rebuilt from current Gamma identity/state + one current CLOB book batch + current CLOB V2 market parameters.",
            "🧾 Took it? Record your actual executed cost; alert quotes are never used as realized P&amp;L.",
        ])
        text = "\n".join(lines)
        buttons = tg._buttons(signal)

        # Rendering itself consumes time. Recheck the short-lived certificate at the
        # last synchronous boundary before handing the request to Telegram transport.
        if not is_trade_ready(signal):
            raise TradeNowPreSendInvalid("TRADE NOW certificate expired while rendering the alert")
    except asyncio.CancelledError:
        raise
    except TradeNowPreSendInvalid:
        raise
    except Exception as exc:
        raise TradeNowPreSendInvalid(
            f"TRADE NOW failed locally before Telegram transport: {type(exc).__name__}"
        ) from exc

    # Do not wrap transport exceptions below this line. Once network I/O starts the
    # worker must preserve the exact delivery state (retryable/rejected/uncertain).
    await tg.send_alert(text, buttons)
