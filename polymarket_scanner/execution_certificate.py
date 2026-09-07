from __future__ import annotations

import asyncio
import json
import math
from decimal import Decimal, InvalidOperation
from typing import Any

from .hardening import MIN_VISIBLE_NOTIONAL_USD
from .models import Signal
from .polymarket import CLOB

EXECUTION_CERTIFICATE_VERSION = "clob_v2_exact_legs_v1"
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


def _token_contexts(raw_markets: list[dict], fallback_url: str) -> tuple[dict[str, dict], dict[str, dict]]:
    by_token: dict[str, dict] = {}
    by_condition: dict[str, dict] = {}
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
        by_condition[condition_id] = raw
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
    return by_token, by_condition


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
    if rate is None or rate < 0 or rate > 1:
        raise ValueError("CLOB fee rate missing/invalid")
    if exponent_raw is None or exponent_raw < 0 or exponent_raw > 10 or exponent_raw != exponent_raw.to_integral_value():
        raise ValueError("CLOB fee exponent missing/unsupported")
    exponent = int(exponent_raw)
    return {
        "tick": tick,
        "minimum": minimum,
        "rate": rate,
        "exponent": exponent,
        "tokens": clob_tokens,
        "taker_order_delay": bool(info.get("itode")),
    }


def _fee_per_share(price: Decimal, rate: Decimal, exponent: int) -> Decimal:
    if rate == 0:
        return Decimal("0")
    base = price * (Decimal("1") - price)
    if base <= 0:
        raise ValueError("fee calculation price outside open interval")
    return rate * (base ** exponent)


async def build_execution_certificate(signal: Signal, poly, raw_markets: list[dict]) -> dict:
    """Build immutable named CLOB V2 legs from current market info + current books.

    The certificate is deliberately independent of detector-time quote metadata. It
    verifies Gamma/CLOB token mapping, exact named outcome, fee curve, tick, minimum
    order, current best ask and visible size. Unknown or contradictory fields fail
    closed. Taker-order-delay markets are rejected for the current human-click
    product because delayed matching breaks the immediate-price assumption.
    """
    if not signal.token_ids or len(set(map(str, signal.token_ids))) != len(signal.token_ids):
        raise ValueError("signal token list is empty or duplicated")

    token_ctx, conditions = _token_contexts(raw_markets, signal.url)
    if any(str(token) not in token_ctx for token in signal.token_ids):
        raise ValueError("one or more purchased tokens are not mapped by current Gamma markets")

    condition_ids = sorted({token_ctx[str(token)]["condition_id"] for token in signal.token_ids})
    infos = await asyncio.gather(*(_clob_market_info(poly, cid) for cid in condition_ids))
    parsed = {cid: _parse_clob_info(info) for cid, info in zip(condition_ids, infos)}
    if any(x["taker_order_delay"] for x in parsed.values()):
        raise ValueError("market uses taker-order delay; immediate manual execution is not certified")

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
            "fee_rate": str(info["rate"]),
            "fee_exponent": info["exponent"],
            "fee_per_share": str(fee),
            "cost_per_share": str(leg_cost),
            "url": ctx["url"],
        })

    cost = sum((_decimal(leg["cost_per_share"]) or Decimal("0") for leg in legs), Decimal("0"))
    if cost <= 0 or cost >= 1:
        raise ValueError("combined exact CLOB cost is not below the $1 payout unit")
    common_visible = min(_positive(leg["visible_best_ask_size"]) for leg in legs)
    min_bundle_shares = max(_positive(leg["minimum_order_size"]) for leg in legs)
    if common_visible is None or min_bundle_shares is None or common_visible < min_bundle_shares:
        raise ValueError("no common equal-share size satisfies every leg minimum")

    safe_common = common_visible * TOP_OF_BOOK_CAPACITY_FRACTION
    # The haircut itself must still leave a legally executable equal-share bundle.
    if safe_common < min_bundle_shares:
        raise ValueError("conservative depth haircut leaves less than the minimum executable bundle")
    capacity = safe_common * cost
    minimum_notional = min_bundle_shares * cost
    if capacity < Decimal(str(MIN_VISIBLE_NOTIONAL_USD)):
        raise ValueError("conservative executable capacity is below the manual dollar floor")

    certificate = {
        "version": EXECUTION_CERTIFICATE_VERSION,
        "legs": legs,
        "combined_cost": str(cost),
        "common_visible_shares": str(common_visible),
        "capacity_fraction": str(TOP_OF_BOOK_CAPACITY_FRACTION),
        "safe_common_shares": str(safe_common),
        "capacity_usd": str(capacity),
        "minimum_bundle_shares": str(min_bundle_shares),
        "minimum_bundle_notional_usd": str(minimum_notional),
        "depth_basis": "CURRENT_BEST_ASK_ONLY_WITH_50_PERCENT_SAFETY_HAIRCUT_UNCALIBRATED",
    }
    return certificate


def validate_execution_certificate(signal: Signal) -> tuple[bool, str, dict | None]:
    """Independently recompute totals from immutable leg records at the final gate."""
    cert = signal.metadata.get("execution_certificate")
    if not isinstance(cert, dict) or cert.get("version") != EXECUTION_CERTIFICATE_VERSION:
        return False, "exact CLOB V2 execution certificate missing", None
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
        if not str(leg.get("question") or "").strip() or not str(leg.get("outcome") or "").strip():
            return False, "execution leg lacks exact question/outcome label", None
        ask = _positive(leg.get("ask"))
        limit = _positive(leg.get("safe_limit"))
        tick = _positive(leg.get("tick_size"))
        fee = _decimal(leg.get("fee_per_share"))
        leg_cost = _positive(leg.get("cost_per_share"))
        visible = _positive(leg.get("visible_best_ask_size"))
        minimum = _positive(leg.get("minimum_order_size"))
        if None in {ask, limit, tick, fee, leg_cost, visible, minimum}:
            return False, "execution leg contains missing/nonfinite numeric fields", None
        assert ask is not None and limit is not None and tick is not None and fee is not None and leg_cost is not None and visible is not None and minimum is not None
        if ask >= 1 or limit != ask or fee < 0 or leg_cost != ask + fee or not _aligned_to_tick(limit, tick):
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
    safe_common = _decimal(cert.get("safe_common_shares"))
    capacity = _decimal(cert.get("capacity_usd"))
    minimum_notional = _decimal(cert.get("minimum_bundle_notional_usd"))
    if safe_common is None or capacity is None or minimum_notional is None:
        return False, "execution capacity fields missing", None
    if safe_common != common * TOP_OF_BOOK_CAPACITY_FRACTION:
        return False, "execution depth haircut arithmetic failed", None
    if safe_common < min_bundle or capacity != safe_common * recomputed:
        return False, "execution capacity/minimum bundle is not internally consistent", None
    if minimum_notional != min_bundle * recomputed:
        return False, "execution minimum notional arithmetic failed", None
    if capacity < Decimal(str(MIN_VISIBLE_NOTIONAL_USD)):
        return False, "execution capacity below manual dollar floor", None

    return True, "exact named CLOB V2 legs + tick + minimum + fee curve + conservative depth passed", {
        "cost": recomputed,
        "common_visible": common,
        "safe_common": safe_common,
        "capacity": capacity,
        "minimum_bundle_shares": min_bundle,
        "minimum_notional": minimum_notional,
        "legs": legs,
    }
