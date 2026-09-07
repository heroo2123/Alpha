from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .config import settings
from .execution_depth import build_safe_ask_ladder, plan_equal_share_bundle_limits
from .hardening import MIN_VISIBLE_NOTIONAL_USD
from .models import Signal
from .polymarket import CLOB

EXECUTION_CERTIFICATE_VERSION = "clob_v2_exact_depth_limits_v5"
EXECUTION_CERTIFICATE_TTL_SECONDS = 8.0
FEE_PRECISION_QUANTUM_USD = Decimal("0.00001")
# Advertise only half of each currently displayed ask level until depth survival is
# empirically calibrated. This is a risk haircut, not a fill guarantee.
DISPLAYED_DEPTH_CAPACITY_FRACTION = Decimal("0.50")
# Backward-compatible constant name used by older tests/callers.
TOP_OF_BOOK_CAPACITY_FRACTION = DISPLAYED_DEPTH_CAPACITY_FRACTION


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
    exponent = _decimal(fee_details.get("e"))
    taker_only = fee_details.get("to")
    if rate is None or rate < 0 or rate > 1:
        raise ValueError("CLOB fee rate missing/invalid")
    if exponent is None or exponent < 0 or exponent > 10:
        raise ValueError("CLOB fee exponent missing/invalid")
    if type(taker_only) is not bool:
        raise ValueError("CLOB taker-only fee flag missing/invalid")
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


def _raw_fee_per_share(price: Decimal, rate: Decimal, exponent: Decimal) -> Decimal:
    """Mirror Polymarket's official py-clob-client-v2 fee-curve calculation."""
    if rate == 0:
        return Decimal("0")
    if price <= 0 or price >= 1:
        raise ValueError("fee calculation price outside open interval")
    if exponent < 0 or exponent > 10:
        raise ValueError("fee calculation exponent outside supported interval")
    base = price * (Decimal("1") - price)
    try:
        fee = rate * (base ** exponent)
    except (InvalidOperation, OverflowError) as exc:
        raise ValueError("CLOB fee curve could not be evaluated") from exc
    if not fee.is_finite() or fee < 0:
        raise ValueError("CLOB fee curve produced an invalid value")
    return fee


def _conservative_fee_per_share(
    price: Decimal,
    rate: Decimal,
    exponent: Decimal,
    minimum_order_size: Decimal,
) -> tuple[Decimal, Decimal]:
    """Return raw SDK curve plus a safe upper bound for 5-decimal fee rounding."""
    raw = _raw_fee_per_share(price, rate, exponent)
    if rate == 0:
        return raw, Decimal("0")
    if minimum_order_size <= 0:
        raise ValueError("minimum order size invalid for fee precision bound")
    pad = FEE_PRECISION_QUANTUM_USD / minimum_order_size
    return raw, pad


def _unit_cost(
    price: Decimal,
    rate: Decimal,
    exponent: Decimal,
    minimum_order_size: Decimal,
) -> Decimal:
    raw, pad = _conservative_fee_per_share(price, rate, exponent, minimum_order_size)
    return price + raw + pad


def _certificate_edge_budget(signal: Signal) -> tuple[Decimal, Decimal]:
    """Return payout/probability reference and maximum all-in bundle cost.

    Structural/directional known-outcome trades require a certified $1 payout unit.
    Weather may use only its already-required prospective empirical lower bound. The
    planner spends no more than payout_reference - configured minimum edge.
    """
    if str(signal.detector).startswith("weather_"):
        payout = _decimal(signal.metadata.get("calibrated_probability_lower_bound"))
        if payout is None or payout <= 0 or payout > 1:
            raise ValueError("weather depth planning requires calibrated probability lower bound")
    else:
        payout = _decimal(signal.theoretical_payout)
        if payout != Decimal("1"):
            raise ValueError("execution depth planning requires a certified $1 payout unit")
    floor = Decimal(str(settings.actionable_min_edge))
    max_cost = payout - floor
    if max_cost <= 0:
        raise ValueError("configured edge floor leaves no executable cost budget")
    return payout, max_cost


def _serialize_depth(levels: list[dict], stop_index: int) -> list[dict]:
    out = []
    for level in levels[: stop_index + 1]:
        out.append({
            "price": str(level["price"]),
            "visible_size": str(level["visible_size"]),
            "safe_size": str(level["safe_size"]),
            "cumulative_visible_size": str(level["cumulative_visible_size"]),
            "cumulative_safe_size": str(level["cumulative_safe_size"]),
        })
    return out


async def build_execution_certificate(signal: Signal, poly, raw_markets: list[dict]) -> dict:
    """Build a short-lived exact-leg certificate from current Gamma + CLOB V2 data.

    Each purchased leg is identified by exact token/outcome, current CLOB parameters
    and one current batch of full order books. Capacity uses multiple displayed ask
    levels with a 50% haircut, but economics are certified at each leg's worst-case
    *limit price*, not at average depth. Thus disappearing better levels cannot make
    a fill at or below the advertised limits violate the certificate's cost bound.

    Multi-leg manual execution remains non-atomic. This certificate proves price/depth
    conditions at one instant; it does not claim to solve partial-fill/unwind risk.
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

    fresh = await poly.books(signal.token_ids)
    if any(str(token) not in fresh for token in signal.token_ids):
        raise ValueError("one or more current order books are missing")

    payout_reference, max_bundle_cost = _certificate_edge_budget(signal)
    prelim: list[dict[str, Any]] = []
    ladders: list[list[dict]] = []
    minimums: list[Decimal] = []

    for token_raw in signal.token_ids:
        token = str(token_raw)
        ctx = token_ctx[token]
        info = parsed[ctx["condition_id"]]
        clob_outcome = info["tokens"].get(token)
        gamma_outcome = str(ctx["gamma_outcome"])
        if clob_outcome is None or clob_outcome.strip().casefold() != gamma_outcome.strip().casefold():
            raise ValueError("Gamma/CLOB token-to-outcome mapping disagrees")

        book = fresh[token]
        tick: Decimal = info["tick"]
        minimum: Decimal = info["minimum"]
        ladder = build_safe_ask_ladder(
            book.asks,
            tick=tick,
            capacity_fraction=DISPLAYED_DEPTH_CAPACITY_FRACTION,
            unit_cost=lambda price, info=info, minimum=minimum: _unit_cost(
                price, info["rate"], info["exponent"], minimum
            ),
        )
        top = ladder[0]
        ask = top["price"]
        if book.best_ask is None or _decimal(book.best_ask) != ask:
            raise ValueError("reconstructed depth best ask disagrees with Book best ask")

        ladders.append(ladder)
        minimums.append(minimum)
        prelim.append({
            "token": token,
            "ctx": ctx,
            "info": info,
            "book": book,
            "ladder": ladder,
        })

    min_bundle_shares = max(minimums)
    plan = plan_equal_share_bundle_limits(
        ladders,
        max_bundle_cost=max_bundle_cost,
        minimum_bundle_shares=min_bundle_shares,
    )

    legs: list[dict[str, Any]] = []
    combined_top_cost = Decimal("0")
    for leg_index, item in enumerate(prelim):
        token = item["token"]
        ctx = item["ctx"]
        info = item["info"]
        book = item["book"]
        ladder = item["ladder"]
        chosen_index = int(plan["indices"][leg_index])
        chosen = ladder[chosen_index]
        top = ladder[0]
        ask: Decimal = top["price"]
        limit: Decimal = chosen["price"]
        minimum: Decimal = info["minimum"]

        ask_raw_fee, ask_pad = _conservative_fee_per_share(
            ask, info["rate"], info["exponent"], minimum
        )
        ask_fee = ask_raw_fee + ask_pad
        ask_cost = ask + ask_fee
        combined_top_cost += ask_cost

        raw_fee, rounding_pad = _conservative_fee_per_share(
            limit, info["rate"], info["exponent"], minimum
        )
        fee = raw_fee + rounding_pad
        limit_cost = limit + fee
        legs.append({
            "market_id": ctx["market_id"],
            "condition_id": ctx["condition_id"],
            "token_id": token,
            "question": ctx["question"],
            "outcome": str(ctx["gamma_outcome"]),
            "ask": str(ask),
            "ask_fee_per_share": str(ask_fee),
            "ask_cost_per_share": str(ask_cost),
            "safe_limit": str(limit),
            "safe_limit_text": _price_text(limit, info["tick"]),
            "tick_size": str(info["tick"]),
            "minimum_order_size": str(minimum),
            "visible_best_ask_size": str(top["visible_size"]),
            "visible_depth_to_limit": str(chosen["cumulative_visible_size"]),
            "safe_depth_to_limit": str(chosen["cumulative_safe_size"]),
            "depth_levels_to_limit": _serialize_depth(ladder, chosen_index),
            "book_timestamp": str(getattr(book, "timestamp", "") or ""),
            "fee_rate": str(info["rate"]),
            "fee_exponent": str(info["exponent"]),
            "fee_taker_only": info["taker_only"],
            "raw_fee_per_share": str(raw_fee),
            "fee_rounding_pad_per_share": str(rounding_pad),
            "fee_per_share": str(fee),
            "cost_per_share": str(limit_cost),
            "url": ctx["url"],
        })

    cost: Decimal = plan["combined_limit_cost"]
    if cost <= 0 or cost > max_bundle_cost or cost >= payout_reference:
        raise ValueError("combined conservative limit cost is outside the certified edge budget")

    safe_common: Decimal = plan["safe_common_shares"]
    common_visible: Decimal = plan["visible_common_shares"]
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
        "payout_reference": str(payout_reference),
        "configured_edge_floor": str(Decimal(str(settings.actionable_min_edge))),
        "max_bundle_cost": str(max_bundle_cost),
        "combined_top_cost": str(combined_top_cost),
        "combined_cost": str(cost),
        "common_visible_shares": str(common_visible),
        "capacity_fraction": str(DISPLAYED_DEPTH_CAPACITY_FRACTION),
        "safe_common_shares": str(safe_common),
        "capacity_usd": str(capacity),
        "minimum_bundle_shares": str(min_bundle_shares),
        "minimum_bundle_notional_usd": str(minimum_notional),
        "depth_basis": "CURRENT_BATCH_MULTI_LEVEL_ASK_DEPTH_WITH_50_PERCENT_PER_LEVEL_HAIRCUT_AND_WORST_CASE_LIMIT_COST",
        "fee_basis": "OFFICIAL_PY_CLOB_CLIENT_V2_CURVE_PLUS_CONSERVATIVE_5_DECIMAL_PROTOCOL_ROUNDING_BOUND",
        "execution_atomicity": "MANUAL_MULTI_LEG_NON_ATOMIC" if len(legs) > 1 else "MANUAL_SINGLE_LEG",
        "partial_fill_policy": "CERTIFICATE_INVALID_AFTER_ANY_PARTIAL_EXECUTION; no unwind economics certified",
    }


def _validate_depth_leg(
    leg: dict,
    *,
    ask: Decimal,
    limit: Decimal,
    tick: Decimal,
    minimum: Decimal,
) -> tuple[bool, str, Decimal | None, Decimal | None]:
    rows = leg.get("depth_levels_to_limit")
    if not isinstance(rows, list) or not rows:
        return False, "execution depth ladder missing", None, None
    cumulative_visible = Decimal("0")
    cumulative_safe = Decimal("0")
    prior_price: Decimal | None = None
    first_visible: Decimal | None = None
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            return False, "execution depth level malformed", None, None
        price = _positive(row.get("price"))
        visible = _positive(row.get("visible_size"))
        safe = _positive(row.get("safe_size"))
        declared_visible = _positive(row.get("cumulative_visible_size"))
        declared_safe = _positive(row.get("cumulative_safe_size"))
        if None in {price, visible, safe, declared_visible, declared_safe}:
            return False, "execution depth level contains invalid values", None, None
        assert price is not None and visible is not None and safe is not None
        assert declared_visible is not None and declared_safe is not None
        if price >= 1 or not _aligned_to_tick(price, tick):
            return False, "execution depth price invalid/tick-misaligned", None, None
        if prior_price is not None and price <= prior_price:
            return False, "execution depth prices are not strictly increasing", None, None
        if safe != visible * DISPLAYED_DEPTH_CAPACITY_FRACTION:
            return False, "execution depth haircut arithmetic failed", None, None
        cumulative_visible += visible
        cumulative_safe += safe
        if declared_visible != cumulative_visible or declared_safe != cumulative_safe:
            return False, "execution depth cumulative arithmetic failed", None, None
        if index == 0:
            if price != ask:
                return False, "execution depth first level does not equal current ask", None, None
            first_visible = visible
        prior_price = price
    if prior_price != limit:
        return False, "execution depth final level does not equal safe limit", None, None
    if cumulative_safe < minimum:
        return False, "execution leg haircut depth is below its minimum order", None, None
    declared_depth_visible = _positive(leg.get("visible_depth_to_limit"))
    declared_depth_safe = _positive(leg.get("safe_depth_to_limit"))
    declared_best_visible = _positive(leg.get("visible_best_ask_size"))
    if (
        declared_depth_visible != cumulative_visible
        or declared_depth_safe != cumulative_safe
        or declared_best_visible != first_visible
    ):
        return False, "execution declared depth fields are inconsistent", None, None
    return True, "", cumulative_visible, cumulative_safe


def validate_execution_certificate(
    signal: Signal,
    *,
    now: datetime | None = None,
) -> tuple[bool, str, dict | None]:
    """Independently recompute immutable limit/depth arithmetic and freshness."""
    cert = signal.metadata.get("execution_certificate")
    if not isinstance(cert, dict) or cert.get("version") != EXECUTION_CERTIFICATE_VERSION:
        return False, "exact CLOB V2 depth execution certificate missing", None

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

    try:
        expected_payout, expected_max_cost = _certificate_edge_budget(signal)
    except ValueError as exc:
        return False, str(exc), None
    declared_payout = _decimal(cert.get("payout_reference"))
    declared_floor = _decimal(cert.get("configured_edge_floor"))
    declared_max_cost = _decimal(cert.get("max_bundle_cost"))
    if (
        declared_payout != expected_payout
        or declared_floor != Decimal(str(settings.actionable_min_edge))
        or declared_max_cost != expected_max_cost
    ):
        return False, "execution edge-budget fields are inconsistent", None

    seen: set[str] = set()
    recomputed_limit_cost = Decimal("0")
    recomputed_top_cost = Decimal("0")
    visible_depths: list[Decimal] = []
    safe_depths: list[Decimal] = []
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
        exponent = _decimal(leg.get("fee_exponent"))
        taker_only = leg.get("fee_taker_only")
        fee = _decimal(leg.get("fee_per_share"))
        leg_cost = _positive(leg.get("cost_per_share"))
        minimum = _positive(leg.get("minimum_order_size"))
        if None in {ask, limit, tick, rate, exponent, fee, leg_cost, minimum}:
            return False, "execution leg contains missing/nonfinite numeric fields", None
        assert ask is not None and limit is not None and tick is not None
        assert rate is not None and exponent is not None and fee is not None
        assert leg_cost is not None and minimum is not None
        if ask >= 1 or limit >= 1 or limit < ask:
            return False, "execution ask/limit ordering is invalid", None
        if not _aligned_to_tick(ask, tick) or not _aligned_to_tick(limit, tick):
            return False, "execution ask/limit tick alignment failed", None
        if exponent < 0 or exponent > 10:
            return False, "execution fee exponent outside supported interval", None
        if type(taker_only) is not bool or (rate > 0 and taker_only is not True):
            return False, "execution taker-fee semantics invalid", None

        try:
            expected_raw, expected_pad = _conservative_fee_per_share(limit, rate, exponent, minimum)
            expected_ask_raw, expected_ask_pad = _conservative_fee_per_share(ask, rate, exponent, minimum)
        except ValueError:
            return False, "execution fee curve is unsupported", None
        expected_fee = expected_raw + expected_pad
        expected_cost = limit + expected_fee
        expected_ask_cost = ask + expected_ask_raw + expected_ask_pad
        if _decimal(leg.get("raw_fee_per_share")) != expected_raw:
            return False, "execution raw limit fee arithmetic failed", None
        if _decimal(leg.get("fee_rounding_pad_per_share")) != expected_pad:
            return False, "execution fee rounding bound arithmetic failed", None
        if fee != expected_fee or leg_cost != expected_cost:
            return False, "execution limit fee/cost arithmetic failed", None
        if _decimal(leg.get("ask_fee_per_share")) != expected_ask_raw + expected_ask_pad:
            return False, "execution current-ask fee arithmetic failed", None
        if _decimal(leg.get("ask_cost_per_share")) != expected_ask_cost:
            return False, "execution current-ask cost arithmetic failed", None

        depth_ok, depth_reason, visible_depth, safe_depth = _validate_depth_leg(
            leg, ask=ask, limit=limit, tick=tick, minimum=minimum
        )
        if not depth_ok or visible_depth is None or safe_depth is None:
            return False, depth_reason, None

        recomputed_limit_cost += expected_cost
        recomputed_top_cost += expected_ask_cost
        visible_depths.append(visible_depth)
        safe_depths.append(safe_depth)
        minimums.append(minimum)

    declared_cost = _decimal(cert.get("combined_cost"))
    declared_top_cost = _decimal(cert.get("combined_top_cost"))
    if declared_cost != recomputed_limit_cost or recomputed_limit_cost <= 0:
        return False, "execution combined limit cost does not equal conservative leg sum", None
    if declared_top_cost != recomputed_top_cost or recomputed_top_cost <= 0:
        return False, "execution combined current-ask cost is inconsistent", None
    if recomputed_limit_cost > expected_max_cost or recomputed_limit_cost >= expected_payout:
        return False, "execution advertised limits spend more than the certified edge budget", None
    if recomputed_top_cost > recomputed_limit_cost:
        return False, "execution current-ask cost exceeds advertised worst-case limits", None

    common_visible = min(visible_depths)
    safe_common = min(safe_depths)
    min_bundle = max(minimums)
    declared_common = _decimal(cert.get("common_visible_shares"))
    capacity_fraction = _decimal(cert.get("capacity_fraction"))
    declared_safe_common = _decimal(cert.get("safe_common_shares"))
    capacity = _decimal(cert.get("capacity_usd"))
    minimum_notional = _decimal(cert.get("minimum_bundle_notional_usd"))
    declared_min_bundle = _decimal(cert.get("minimum_bundle_shares"))
    if None in {declared_common, capacity_fraction, declared_safe_common, capacity, minimum_notional, declared_min_bundle}:
        return False, "execution capacity fields missing", None
    assert declared_common is not None and capacity_fraction is not None and declared_safe_common is not None
    assert capacity is not None and minimum_notional is not None and declared_min_bundle is not None
    if declared_common != common_visible or capacity_fraction != DISPLAYED_DEPTH_CAPACITY_FRACTION:
        return False, "execution visible-depth fields are inconsistent", None
    if declared_safe_common != safe_common:
        return False, "execution safe common depth is inconsistent", None
    if declared_min_bundle != min_bundle:
        return False, "execution minimum bundle field is inconsistent", None
    if safe_common < min_bundle or capacity != safe_common * recomputed_limit_cost:
        return False, "execution capacity/minimum bundle is not internally consistent", None
    if minimum_notional != min_bundle * recomputed_limit_cost:
        return False, "execution minimum notional arithmetic failed", None
    if capacity < Decimal(str(MIN_VISIBLE_NOTIONAL_USD)):
        return False, "execution capacity below manual dollar floor", None

    return True, "fresh exact named CLOB V2 legs + multi-level haircut depth + worst-case limit economics + fee precision passed", {
        "cost": recomputed_limit_cost,
        "top_cost": recomputed_top_cost,
        "common_visible": common_visible,
        "safe_common": safe_common,
        "capacity": capacity,
        "minimum_bundle_shares": min_bundle,
        "minimum_notional": minimum_notional,
        "payout_reference": expected_payout,
        "max_bundle_cost": expected_max_cost,
        "checked_at": checked,
        "expires_at": expires,
        "legs": legs,
    }
