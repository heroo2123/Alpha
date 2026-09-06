from __future__ import annotations

import re
from collections import defaultdict
from difflib import SequenceMatcher
from itertools import combinations

from .config import settings
from .models import Book, Market, Signal
from .polymarket import taker_fee_per_share
from .weather import in_bucket, lock_probability, market_unit, parse_bucket, station_from_market


def market_url(m: Market) -> str:
    return f"https://polymarket.com/event/{m.event_slug}?market={m.slug}" if m.event_slug else f"https://polymarket.com/market/{m.slug}"


def binary_buy_both(markets: list[Market], books: dict[str, Book]) -> list[Signal]:
    signals: list[Signal] = []
    for m in markets:
        if not m.yes_token or not m.no_token:
            continue
        by, bn = books.get(m.yes_token), books.get(m.no_token)
        if not by or not bn or by.best_ask is None or bn.best_ask is None:
            continue
        ay, an = by.best_ask, bn.best_ask
        fee = taker_fee_per_share(ay) + taker_fee_per_share(an)
        cost = ay + an + fee
        edge = 1.0 - cost
        if edge >= settings.actionable_min_edge:
            max_shares = min(by.best_ask_size, bn.best_ask_size)
            signals.append(Signal(
                detector="binary_buy_both",
                confidence="ACTIONABLE",
                event_id=m.event_id,
                market_id=m.id,
                title=f"Binary arbitrage: {m.question}",
                detail=f"Buy YES {ay:.3f} + NO {an:.3f}; est. taker fees {fee:.4f}; locked edge {edge:.2%}. Visible size ≈ {max_shares:.1f} shares.",
                url=market_url(m), edge=edge, entry_cost=cost, theoretical_payout=1.0,
                token_ids=[m.yes_token, m.no_token],
                metadata={"yes_ask": ay, "no_ask": an, "max_shares": max_shares, "immediate_settlement": True, "fingerprint_key": m.id},
            ))
    return signals


def neg_risk_underround(markets: list[Market], books: dict[str, Book]) -> list[Signal]:
    grouped: dict[str, list[Market]] = defaultdict(list)
    for m in markets:
        if m.event_neg_risk and m.yes_token:
            grouped[m.event_id].append(m)
    out: list[Signal] = []
    for event_id, rows in grouped.items():
        if len(rows) < 3:
            continue
        legs = []
        for m in rows:
            b = books.get(m.yes_token or "")
            if not b or b.best_ask is None:
                legs = []
                break
            legs.append((m, b.best_ask, b.best_ask_size))
        if not legs:
            continue
        raw = sum(x[1] for x in legs)
        fees = sum(taker_fee_per_share(x[1]) for x in legs)
        cost = raw + fees
        edge = 1.0 - cost
        if edge >= settings.actionable_min_edge:
            min_size = min(x[2] for x in legs)
            title = rows[0].event_title or rows[0].question
            out.append(Signal(
                detector="neg_risk_underround", confidence="ACTIONABLE", event_id=event_id, market_id=None,
                title=f"Event underround: {title}",
                detail=f"Buy YES across {len(legs)} mutually-exclusive neg-risk outcomes. Combined asks {raw:.3f}, est. fees {fees:.4f}, locked edge {edge:.2%}. Visible common size ≈ {min_size:.1f} shares.",
                url=f"https://polymarket.com/event/{rows[0].event_slug}", edge=edge, entry_cost=cost, theoretical_payout=1.0,
                token_ids=[m.yes_token for m, _, _ in legs if m.yes_token],
                metadata={"legs": [{"market_id": m.id, "ask": a, "question": m.question} for m,a,_ in legs], "max_shares": min_size, "immediate_settlement": True, "fingerprint_key": event_id},
            ))
    return out


def weather_late_lock(markets: list[Market], books: dict[str, Book], weather_cache: dict[str, list]) -> list[Signal]:
    out: list[Signal] = []
    grouped: dict[str, list[Market]] = defaultdict(list)
    for m in markets:
        text = f"{m.event_title} {m.question}".lower()
        if "highest temperature" in text:
            grouped[m.event_id].append(m)
    for _, rows in grouped.items():
        station = station_from_market(rows[0])
        if not station:
            continue
        obs = weather_cache.get(station) or []
        info = lock_probability(rows[0], obs, station)
        if not info or info["probability"] < settings.weather_lock_min_probability:
            continue
        winner = None
        for m in rows:
            bounds = parse_bucket(m.question, market_unit(m))
            if bounds != (None, None) and in_bucket(info["observed_max"], bounds):
                winner = m
                break
        if not winner or not winner.yes_token:
            continue
        b = books.get(winner.yes_token)
        if not b or b.best_ask is None:
            continue
        ask = b.best_ask
        fee = taker_fee_per_share(ask)
        net_cost = ask + fee
        edge = info["probability"] - net_cost
        if ask >= settings.weather_market_price_ceiling or edge < settings.actionable_min_edge:
            continue
        margin = info["forecast_margin"]
        unit = info["unit"]
        detail = (
            f"{station}: exact {info['settlement_source_kind']} settlement station verified. "
            f"Official-hourly observed max {info['observed_max']:.0f}°{unit}; current {info['current']:.0f}°; "
            f"recent {info['hourly_values']}; latest hourly observation {info['latest_observation_age_minutes']:.0f} min old. "
            f"Local time {info['local_time']} ({info['timezone']}). Remaining-day {info['forecast_provider']} max "
            f"{info['forecast_remaining_max']:.0f}°{unit}, {margin:.0f}° below the observed high; "
            f"max precip probability {info['max_precip_probability']:.0f}%, max cloud {info['max_cloud_cover']:.0f}%; "
            f"thunderstorm/front-regime gates clear. Model lock probability {info['probability']:.1%}. "
            f"Matching bucket ask {ask:.3f}, est. fee/share {fee:.4f}, model edge {edge:.2%}."
        )
        out.append(Signal(
            detector="weather_late_lock", confidence="ACTIONABLE", event_id=winner.event_id, market_id=winner.id,
            title=f"Weather high-lock candidate: {winner.event_title}",
            detail=detail,
            url=market_url(winner), edge=edge, entry_cost=net_cost, theoretical_payout=1.0,
            token_ids=[winner.yes_token],
            metadata={
                "station": station,
                "ask": ask,
                "lock_probability": info["probability"],
                "observed_max": info["observed_max"],
                "unit": unit,
                "forecast_remaining_max": info["forecast_remaining_max"],
                "forecast_margin": margin,
                "timezone": info["timezone"],
                "settlement_source_verified": True,
                "settlement_source_url": info["settlement_source_url"],
                "forecast_provider": info["forecast_provider"],
                "source_note": info["source"],
                "fingerprint_key": f"{winner.id}:{info['observed_max']}",
                "action_steps": [
                    "Tap OPEN MARKET below.",
                    f"Open the market Rules and verify the settlement station is still {station} on the NOAA/NWS WRH time-series source.",
                    f"Confirm the official hourly table still shows a daily high of {info['observed_max']:.0f}°{unit} and no newer observation has exceeded it.",
                    f"Confirm the remaining-day forecast still stays comfortably below that high; this alert saw a max of {info['forecast_remaining_max']:.0f}°{unit}.",
                    f"Buy YES on the matching bucket at {ask:.3f} or lower. If the ask moved higher or any verification changed, SKIP.",
                ],
                "risk_note": "The forecast is advisory, not the settlement source. Skip if the official WRH table, station/date/rules, or remaining-day weather no longer match the alert. A forecast can be wrong.",
            },
        ))
    return out


def _norm(s: str) -> str:
    s = re.sub(r"\b(?:will|the|a|an|on|in|by|to|of|for|be|is)\b", " ", s.lower())
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(s.split())


def duplicate_divergence(markets: list[Market]) -> list[Signal]:
    """Find likely duplicate contracts without repeatedly normalizing every pair.

    The old implementation normalized both questions inside all ~61k pair checks.
    On a tiny e2-micro that could pin the event loop for tens of seconds. We now
    normalize once, use SequenceMatcher's cheap upper-bound filters first, and only
    run the full ratio for pairs that can still meet the configured threshold.
    """
    candidates = [m for m in markets if m.liquidity >= settings.min_market_liquidity and m.best_ask is not None]
    candidates = sorted(candidates, key=lambda x: x.liquidity, reverse=True)[:350]
    prepared = [(m, _norm(m.question)) for m in candidates]
    out: list[Signal] = []

    for (a, na), (b, nb) in combinations(prepared, 2):
        if a.event_id == b.event_id or not na or not nb:
            continue
        sm = SequenceMatcher(None, na, nb, autojunk=False)
        if sm.real_quick_ratio() < settings.duplicate_similarity_threshold:
            continue
        if sm.quick_ratio() < settings.duplicate_similarity_threshold:
            continue
        sim = sm.ratio()
        if sim < settings.duplicate_similarity_threshold:
            continue
        pa = a.outcome_prices[0] if a.outcome_prices else a.best_ask
        pb = b.outcome_prices[0] if b.outcome_prices else b.best_ask
        if pa is None or pb is None:
            continue
        div = abs(pa - pb)
        if div >= settings.watch_min_divergence:
            out.append(Signal(
                detector="duplicate_divergence", confidence="WATCH", event_id=a.event_id, market_id=a.id,
                title="Potential duplicate-market divergence",
                detail=f"Similarity {sim:.1%}; implied YES prices differ by {div:.1%}. A: {a.question} ({pa:.3f}) | B: {b.question} ({pb:.3f}). Confirm resolution rules are truly equivalent before acting.",
                url=market_url(a), edge=None, entry_cost=None, theoretical_payout=None,
                token_ids=[x for x in [a.yes_token, b.yes_token] if x],
                metadata={"other_url": market_url(b), "similarity": sim, "divergence": div, "fingerprint_key": f"{min(a.id,b.id)}:{max(a.id,b.id)}"},
            ))
            if len(out) >= 10:
                break
    return out


def wide_spread_watch(markets: list[Market]) -> list[Signal]:
    out: list[Signal] = []
    for m in markets:
        spread = m.raw.get("spread")
        try:
            spread = float(spread)
        except (TypeError, ValueError):
            if m.best_bid is None or m.best_ask is None:
                continue
            spread = m.best_ask - m.best_bid
        if spread >= settings.wide_spread_threshold and m.volume_24h >= settings.wide_spread_min_volume_24h:
            out.append(Signal(
                detector="wide_spread", confidence="WATCH", event_id=m.event_id, market_id=m.id,
                title=f"Wide liquid spread: {m.question}",
                detail=f"Spread {spread:.1%}; bid {m.best_bid}; ask {m.best_ask}; 24h volume ≈ ${m.volume_24h:,.0f}. Possible maker/price-discovery opportunity, not guaranteed arbitrage.",
                url=market_url(m), edge=None, entry_cost=None, theoretical_payout=None,
                token_ids=[x for x in [m.yes_token] if x], metadata={"spread": spread, "fingerprint_key": m.id},
            ))
    return sorted(out, key=lambda s: s.metadata.get("spread", 0), reverse=True)[:10]
