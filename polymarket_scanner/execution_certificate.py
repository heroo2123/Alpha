from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .hardening import MIN_VISIBLE_NOTIONAL_USD
from .models import Signal
from .polymarket import CLOB

EXECUTION_CERTIFICATE_VERSION = "clob_v2_exact_legs_v2"
EXECUTION_CERTIFICATE_TTL_SECONDS = 8.0
# Manual execution cannot safely claim 100% of a transient top-of-book size. Until
# depth-survival is empirically calibrated, advertise only half of simultaneously
# visible best-ask depth. This is a risk haircut, not a fill guarantee.
TOP_OF_BOOK_CAPACITY_FRACTION = Decimal("0.50")


def _json_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except Exception:
            return []
        return decoded if isinstance(decoded, list) else []
    return []


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return out if out.is_finite() else None


def _positive(value: object) -> Decimal | None:
    out = _decimal(value)
    return out if out is not None and out > 0 else None


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _market_url(raw: dict, fallback: str) -> str:
    slug = str(raw.get("slug") or "").strip()
    event_slug = str(raw.get("eventSlug") or raw.get("event_slug") or "").strip()
    events = raw.get("events") or []
    if not event_slug and isinstance(events, list) and events and isinstance(events[0], dict):
        event_slug = str(events[0].get("slug") or "").strip()
    if event_slug and slug:
        return f"https://polymarket.com/event/{event_slug}?market={slug}"
    if slug:
        return f"https://polymarket.com/market/{slug}"
    return fallback


def _tick_places(tick: Decimal) -> int:
    return max(0, -tick.normalize().as_tuple().exponent)


def _price_text(price: Decimal, tick: Decimal) -> str:
    places = _tick_places(tick)
    return f"{price:.{places}f}"


def _aligned_to_tick(price: Decimal, tick: Decimal) -> bool:
    if tick <= 0:
        return False
    try:
        return (price % tick) == 0
    except InvalidOperation:
        return False


def _token_contexts(raw_markets: list[dict], fallback_url: str) -> dict[str, dict]:
    by_token: dict[str, dict] = {}
    for raw in raw_markets:
        if not isinstance(raw, dict):
            raise ValueError("market payload is not an object")
        market_id = str(raw.get("id") or "").strip()
        condition_id = str(raw.get("conditionId") or raw.get("condition_id") or "").strip()
        question = str(raw.get("question") or "").strip()
        tokens = [str(x).strip() for x in _json_list(raw.get("clobTokenIds"))]
        outcomes = [str(x).strip() for x in _json_list(raw.get("outcomes"))]
        if not market_id or not condition_id or not question:
            raise ValueError("market identity/question/condition ID incomplete")
        if len(tokens) != len(outcomes) or not tokens or not all(tokens) or len(set(tokens)) != len(tokens):
            raise ValueError("Gamma token/outcome mapping is incomplete or ambiguous")
        url = _market_url(raw, fallback_url)
        for token, outcome in zip(tokens, outcomes):
            if token in by_token:
                raise ValueError("token appears in more than one delivery market")
            by_token[token] = {
                "market_id": market_id,
                "condition_id": condition_id,
                "question": question,
                "gamma_outcome": outcome,
                "url": url,
            }
    return by_token


async def _clob_market_info(poly, condition_id: str) -> dict:
    response = await poly.http.get(f"{CLOB}/clob-markets/{condition_id}")
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("CLOB market-info response is not an object")
    return body


def _parse_clob_info(info: dict) -> dict:
    tick = _positive(info.get("mts"))
    minimum = _positive(info.get("mos"))
    fee_details = info.get("fd")
    tokens = info.get("t")
    if tick is None or tick >= 1:
        raise ValueError("CLOB minimum tick size missing/invalid")
    if minimum is None:
        raise ValueError("CLOB minimum order size missing/invalid")
    if not isinstance(tokens, list) or not tokens:
        raise ValueError("CLOB token/outcome mapping missing")

    clob_tokens: dict[str, str] = {}
    for item in tokens:
        if not isinstance(item, dict):
            raise ValueError("CLOB token mapping malformed")
        token = str(item.get("t") or "").strip()
        outcome = str(item.get("o") or "").strip()
        if not token or not outcome or token in clob_tokens:
            raise ValueError("CLOB token/outcome mapping ambiguous")
        clob_tokens[token] = outcome

    if not isinstance(fee_details, dict):
        raise ValueError("CLOB V2 fee-curve details missing")
    rate = _decimal(fee_details.get("r"))
    exponent_raw = _decimal(fee_details.get("e"))
    taker_only = fee_details.get("to")
    if rate is None or rate < 0 or rate > 1:
        raise ValueError("CLOB fee rate missing/invalid")
    if exponent_raw is None or exponent_raw < 0 or exponent_raw > 10 or exponent_raw != exponent_raw.to_integral_value():
        raise ValueError("CLOB fee exponent missing/invalid")
    if type(taker_only) is not bool:
        raise ValueError("CLOB taker-only fee flag missing/invalid")

    exponent = int(exponent_raw)
    # Current public CLOB V2 documentation publishes fee = C * rate * p * (1-p).
    # It exposes fd.e as a curve parameter but does not publish a different formula
    # for non-1 exponents. Never guess a money formula: fee-bearing e != 1 fails
    # closed until Polymarket documents the semantics or the official SDK is used.
    if rate > 0 and exponent != 1:
        raise ValueError("CLOB fee exponent is unsupported by the documented manual fee formula")
    if rate > 0 and taker_only is not True:
        raise ValueError("CLOB fee semantics are not taker-only as expected")

    return {
        "tick": tick,
        "minimum": minimum,
        "rate": rate,
        "exponent": exponent,
        "taker_only": taker_only,
        "tokens": clob_tokens,
        "taker_order_delay": bool(info.get("itode")),
    }


def _fee_per_share(price: Decimal, rate: Decimal, exponent: int) -> Decimal:
    if rate == 0:
        return Decimal("0")
    if exponent != 1:
        raise ValueError("unsupported fee exponent")
    if price <= 0 or price >= 1:
        raise ValueError("fee calculation price outside open interval")
    return rate * price * (Decimal("1") - price)


async def build_execution_certificate(signal: Signal, poly, raw_markets: list[dict]) -> dict:
    """Build a short-lived exact-leg certificate from current Gamma + CLOB V2 data.

    Detector-time quote metadata is never trusted. The certificate verifies current
    Gamma/CLOB token mapping, exact named outcome, current market parameters, current
    best ask/visible size, tick alignment, minimum order and the documented taker fee
    formula. Unknown or contradictory fields fail closed. Taker-order-delay markets
    are rejected for the current human-click product.
    """
    if not signal.token_ids or len(set(map(str, signal.token_ids))) != len(signal.token_ids):
        raise ValueError("signal token list is empty or duplicated")

    token_ctx = _token_contexts(raw_markets, signal.url)
    if any(str(token) not in token_ctx for token in signal.token_ids):
        raise ValueError("one or more purchased tokens are not mapped by current Gamma markets")

    condition_ids = sorted({token_ctx[str(token)]["condition_id"] for token in signal.token_ids})
    infos = await asyncio.gather(*(_clob_market_info(poly, cid) for cid in condition_ids))
    parsed = {cid: _parse_clob_info(info) for cid, info in zip(condition_ids, infos)}
    if any(x["taker_order_delay"] for x in parsed.values()):
        raise ValueError("market uses taker-order delay; immediate manual execution is not certified")

    # poly.books uses the CLOB batch /books endpoint for this small purchased token
    # set, so the legs are observed in one request rather than sequential GETs.
    fresh = await poly.books(signal.token_ids)
    if any(str(token) not in fresh for token in signal.token_ids):
        raise ValueError("one or more current order books are missing")

    legs: list[dict[str, Any]] = []
    for token_raw in signal.token_ids:
        token = str(token_raw)
        ctx = token_ctx[token]
        info = parsed[ctx["condition_id"]]
        clob_outcome = info["tokens"].get(token)
        gamma_outcome = str(ctx["gamma_outcome"])
        if clob_outcome is None or clob_outcome.strip().casefold() != gamma_outcome.strip().casefold():
            raise ValueError("Gamma/CLOB token-to-outcome mapping disagrees")

        book = fresh[token]
        ask = _positive(book.best_ask)
        size = _positive(book.best_ask_size)
        if ask is None or ask >= 1 or size is None:
            raise ValueError("current best ask/size is not executable")
        tick: Decimal = info["tick"]
        if not _aligned_to_tick(ask, tick):
            raise ValueError("current best ask is not aligned to the market tick")
        minimum: Decimal = info["minimum"]
        if size < minimum:
            raise ValueError("visible best-ask size is below the market minimum order size")

        fee = _fee_per_share(ask, info["rate"], info["exponent"])
        leg_cost = ask + fee
        legs.append({
            "market_id": ctx["market_id"],
            "condition_id": ctx["condition_id"],
            "token_id": token,
            "question": ctx["question"],
            "outcome": gamma_outcome,
            "ask": str(ask),
            "safe_limit": str(ask),
            "safe_limit_text": _price_text(ask, tick),
            "tick_size": str(tick),
            "minimum_order_size": str(minimum),
            "visible_best_ask_size": str(size),
            "book_timestamp": str(getattr(book, "timestamp", "") or ""),
            "fee_rate": str(info["rate"]),
            "fee_exponent": info["exponent"],
            "fee_taker_only": info["taker_only"],
            "fee_per_share": str(fee),
            "cost_per_share": str(leg_cost),
            "url": ctx["url"],
        })

    cost = sum((_decimal(leg["cost_per_share"]) or Decimal("0") for leg in legs), Decimal("0"))
    if cost <= 0 or cost >= 1:
        raise ValueError("combined exact CLOB cost is not below the $1 payout unit")

    visible_values = [_positive(leg["visible_best_ask_size"]) for leg in legs]
    minimum_values = [_positive(leg["minimum_order_size"]) for leg in legs]
    if any(x is None for x in visible_values) or any(x is None for x in minimum_values):
        raise ValueError("execution size/minimum fields became invalid")
    common_visible = min(x for x in visible_values if x is not None)
    min_bundle_shares = max(x for x in minimum_values if x is not None)
    if common_visible < min_bundle_shares:
        raise ValueError("no common equal-share size satisfies every leg minimum")

    safe_common = common_visible * TOP_OF_BOOK_CAPACITY_FRACTION
    if safe_common < min_bundle_shares:
        raise ValueError("conservative depth haircut leaves less than the minimum executable bundle")
    capacity = safe_common * cost
    minimum_notional = min_bundle_shares * cost
    if capacity < Decimal(str(MIN_VISIBLE_NOTIONAL_USD)):
        raise ValueError("conservative executable capacity is below the manual dollar floor")

    checked = datetime.now(timezone.utc)
    expires = checked + timedelta(seconds=EXECUTION_CERTIFICATE_TTL_SECONDS)
    return {
        "version": EXECUTION_CERTIFICATE_VERSION,
        "checked_at": checked.isoformat(),
        "expires_at": expires.isoformat(),
        "ttl_seconds": EXECUTION_CERTIFICATE_TTL_SECONDS,
        "legs": legs,
        "combined_cost": str(cost),
        "common_visible_shares": str(common_visible),
        "capacity_fraction": str(TOP_OF_BOOK_CAPACITY_FRACTION),
        "safe_common_shares": str(safe_common),
        "capacity_usd": str(capacity),
        "minimum_bundle_shares": str(min_bundle_shares),
        "minimum_bundle_notional_usd": str(minimum_notional),
        "depth_basis": "CURRENT_BATCH_BEST_ASK_WITH_50_PERCENT_SAFETY_HAIRCUT_UNCALIBRATED",
        "fee_basis": "DOCUMENTED_CLOB_V2_TAKER_FORMULA_RATE_X_P_X_1_MINUS_P; non-1 fee exponent rejected",
    }


def validate_execution_certificate(
    signal: Signal,
    *,
    now: datetime | None = None,
) -> tuple[bool, str, dict | None]:
    """Independently recompute immutable leg arithmetic and certificate freshness."""
    cert = signal.metadata.get("execution_certificate")
    if not isinstance(cert, dict) or cert.get("version") != EXECUTION_CERTIFICATE_VERSION:
        return False, "exact CLOB V2 execution certificate missing", None

    checked = _parse_time(cert.get("checked_at"))
    expires = _parse_time(cert.get("expires_at"))
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if checked is None or expires is None:
        return False, "execution certificate timestamps missing/invalid", None
    if current < checked - timedelta(seconds=1):
        return False, "execution certificate is future-dated", None
    if current > expires:
        return False, "execution certificate expired", None
    expected_expiry = checked + timedelta(seconds=EXECUTION_CERTIFICATE_TTL_SECONDS)
    if abs((expires - expected_expiry).total_seconds()) > 0.001:
        return False, "execution certificate expiry arithmetic invalid", None
    ttl = _decimal(cert.get("ttl_seconds"))
    if ttl != Decimal(str(EXECUTION_CERTIFICATE_TTL_SECONDS)):
        return False, "execution certificate TTL field invalid", None

    legs = cert.get("legs")
    if not isinstance(legs, list) or len(legs) != len(signal.token_ids) or not legs:
        return False, "execution leg count does not match purchased token list", None

    seen: set[str] = set()
    recomputed = Decimal("0")
    visible_sizes: list[Decimal] = []
    minimums: list[Decimal] = []
    for leg, expected_token in zip(legs, signal.token_ids):
        if not isinstance(leg, dict):
            return False, "execution leg record malformed", None
        token = str(leg.get("token_id") or "")
        if token != str(expected_token) or not token or token in seen:
            return False, "execution token order/identity mismatch", None
        seen.add(token)
        if not str(leg.get("market_id") or "").strip() or not str(leg.get("condition_id") or "").strip():
            return False, "execution leg identity is incomplete", None
        if not str(leg.get("question") or "").strip() or not str(leg.get("outcome") or "").strip():
            return False, "execution leg lacks exact question/outcome label", None
        if not str(leg.get("url") or "").strip():
            return False, "execution leg lacks direct market URL", None

        ask = _positive(leg.get("ask"))
        limit = _positive(leg.get("safe_limit"))
        tick = _positive(leg.get("tick_size"))
        rate = _decimal(leg.get("fee_rate"))
        exponent_raw = _decimal(leg.get("fee_exponent"))
        taker_only = leg.get("fee_taker_only")
        fee = _decimal(leg.get("fee_per_share"))
        leg_cost = _positive(leg.get("cost_per_share"))
        visible = _positive(leg.get("visible_best_ask_size"))
        minimum = _positive(leg.get("minimum_order_size"))
        if None in {ask, limit, tick, rate, exponent_raw, fee, leg_cost, visible, minimum}:
            return False, "execution leg contains missing/nonfinite numeric fields", None
        assert ask is not None and limit is not None and tick is not None
        assert rate is not None and exponent_raw is not None and fee is not None
        assert leg_cost is not None and visible is not None and minimum is not None
        if exponent_raw != exponent_raw.to_integral_value():
            return False, "execution fee exponent is non-integral", None
        exponent = int(exponent_raw)
        if type(taker_only) is not bool or (rate > 0 and taker_only is not True):
            return False, "execution taker-fee semantics invalid", None
        try:
            expected_fee = _fee_per_share(ask, rate, exponent)
        except ValueError:
            return False, "execution fee curve is unsupported", None
        if ask >= 1 or limit != ask or fee != expected_fee or leg_cost != ask + fee or not _aligned_to_tick(limit, tick):
            return False, "execution price/fee/limit arithmetic failed", None
        if visible < minimum:
            return False, "execution leg visible size below minimum order", None
        recomputed += leg_cost
        visible_sizes.append(visible)
        minimums.append(minimum)

    declared_cost = _decimal(cert.get("combined_cost"))
    if declared_cost is None or declared_cost != recomputed or recomputed <= 0 or recomputed >= 1:
        return False, "execution combined cost does not equal exact leg sum", None

    common = min(visible_sizes)
    min_bundle = max(minimums)
    declared_common = _decimal(cert.get("common_visible_shares"))
    capacity_fraction = _decimal(cert.get("capacity_fraction"))
    safe_common = _decimal(cert.get("safe_common_shares"))
    capacity = _decimal(cert.get("capacity_usd"))
    minimum_notional = _decimal(cert.get("minimum_bundle_notional_usd"))
    declared_min_bundle = _decimal(cert.get("minimum_bundle_shares"))
    if None in {declared_common, capacity_fraction, safe_common, capacity, minimum_notional, declared_min_bundle}:
        return False, "execution capacity fields missing", None
    assert declared_common is not None and capacity_fraction is not None and safe_common is not None
    assert capacity is not None and minimum_notional is not None and declared_min_bundle is not None
    if declared_common != common or capacity_fraction != TOP_OF_BOOK_CAPACITY_FRACTION:
        return False, "execution visible-depth fields are inconsistent", None
    if declared_min_bundle != min_bundle:
        return False, "execution minimum bundle field is inconsistent", None
    if safe_common != common * TOP_OF_BOOK_CAPACITY_FRACTION:
        return False, "execution depth haircut arithmetic failed", None
    if safe_common < min_bundle or capacity != safe_common * recomputed:
        return False, "execution capacity/minimum bundle is not internally consistent", None
    if minimum_notional != min_bundle * recomputed:
        return False, "execution minimum notional arithmetic failed", None
    if capacity < Decimal(str(MIN_VISIBLE_NOTIONAL_USD)):
        return False, "execution capacity below manual dollar floor", None

    return True, "fresh exact named CLOB V2 legs + tick + minimum + documented fee formula + conservative depth passed", {
        "cost": recomputed,
        "common_visible": common,
        "safe_common": safe_common,
        "capacity": capacity,
        "minimum_bundle_shares": min_bundle,
        "minimum_notional": minimum_notional,
        "checked_at": checked,
        "expires_at": expires,
        "legs": legs,
    }
