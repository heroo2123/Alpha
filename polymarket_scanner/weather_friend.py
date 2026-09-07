from __future__ import annotations

from collections import defaultdict

from .config import settings
from .models import Book, Market, Signal
from .polymarket import taker_fee_per_share
from .weather import in_bucket, lock_probability, market_unit, parse_bucket, station_from_market


FRIEND_STYLE_MIN_ASK = 0.90
FRIEND_STYLE_MAX_ASK = 0.975
FRIEND_STYLE_MIN_LOCK_PROBABILITY = 0.955
FRIEND_STYLE_MIN_NET_PAYOUT_LEFT = 0.015
FRIEND_STYLE_MODEL_VERSION = "friend_uncalibrated_v1"


def _market_url(m: Market) -> str:
    return f"https://polymarket.com/event/{m.event_slug}?market={m.slug}" if m.event_slug else f"https://polymarket.com/market/{m.slug}"


def friend_style_weather_lock(
    markets: list[Market],
    books: dict[str, Book],
    weather_cache: dict[str, list],
) -> list[Signal]:
    """Surface late-day high-lock trades that are too small for the main EV gate.

    This lane intentionally does NOT weaken ``weather_late_lock`` ACTIONABLE rules.
    It exists for the manual style the user described: late in the station's local
    day, the official daily high appears locked, but the matching YES bucket still
    trades around 90-97.5 cents. These remain research experiments because the
    current lock score is a heuristic rather than a historically calibrated
    probability model. Every new row is explicitly versioned so later calibration
    uses only clean prospective evidence from the current rules/source boundary.
    """
    grouped: dict[str, list[Market]] = defaultdict(list)
    for m in markets:
        if "highest temperature" in f"{m.event_title} {m.question}".lower():
            grouped[m.event_id].append(m)

    out: list[Signal] = []
    for rows in grouped.values():
        station = station_from_market(rows[0])
        if not station:
            continue
        observations = weather_cache.get(station) or []
        info = lock_probability(rows[0], observations, station)
        if not info:
            continue

        lock_p = float(info.get("probability") or 0.0)
        if lock_p < FRIEND_STYLE_MIN_LOCK_PROBABILITY:
            continue
        if int(info.get("cooling_obs") or 0) < 3:
            continue

        winner = None
        for market in rows:
            bounds = parse_bucket(market.question, market_unit(market))
            if bounds != (None, None) and in_bucket(float(info["observed_max"]), bounds):
                winner = market
                break
        if not winner or not winner.yes_token:
            continue

        book = books.get(winner.yes_token)
        if not book or book.best_ask is None:
            continue
        ask = float(book.best_ask)
        if ask < FRIEND_STYLE_MIN_ASK or ask > FRIEND_STYLE_MAX_ASK:
            continue

        fee = taker_fee_per_share(ask)
        net_cost = ask + fee
        model_edge = lock_p - net_cost
        # The normal ACTIONABLE weather detector already owns candidates that clear
        # the production EV gate. Avoid duplicate alerts here.
        if model_edge >= settings.actionable_min_edge:
            continue

        payout_left = 1.0 - net_cost
        if payout_left < FRIEND_STYLE_MIN_NET_PAYOUT_LEFT:
            continue

        unit = str(info.get("unit") or "")
        observed_max = float(info["observed_max"])
        current = float(info["current"])
        cooling_obs = int(info.get("cooling_obs") or 0)
        drop = float(info.get("observed_drop") or 0.0)
        source_url = str(info.get("settlement_source_url") or "")
        local_time = str(info.get("local_time") or "")
        forecast_provider = str(info.get("forecast_provider") or "risk forecast")

        detail = (
            f"Late-day high-lock WATCH at {station}: official observed high {observed_max:.0f}°{unit}, "
            f"current {current:.0f}°{unit}, {cooling_obs} consecutive non-rising official-hourly observations, "
            f"drop from high {drop:.0f}°{unit}. Local time {local_time}. "
            f"Matching YES ask {ask:.3f}; est. fee/share {fee:.4f}; payout remaining after estimated fee "
            f"{payout_left:.2%}. Heuristic lock score {lock_p:.1%}; heuristic model edge {model_edge:.2%}. "
            f"This is intentionally a WATCH, not a certified trade, because the score is not yet historically calibrated."
        )

        out.append(Signal(
            detector="weather_friend_lock",
            confidence="WATCH",
            event_id=winner.event_id,
            market_id=winner.id,
            title=f"Friend-style late weather lock: {winner.event_title}",
            detail=detail,
            url=_market_url(winner),
            edge=payout_left,
            entry_cost=net_cost,
            theoretical_payout=1.0,
            token_ids=[winner.yes_token],
            metadata={
                "station": station,
                "ask": ask,
                "lock_probability": lock_p,
                "model_edge": model_edge,
                "net_payout_left": payout_left,
                "observed_max": observed_max,
                "current": current,
                "cooling_obs": cooling_obs,
                "observed_drop": drop,
                "unit": unit,
                "settlement_source_verified": True,
                "settlement_source_url": source_url,
                "forecast_provider": forecast_provider,
                "weather_model_version": FRIEND_STYLE_MODEL_VERSION,
                "experimental_resolution": True,
                "fingerprint_key": f"{winner.id}:{observed_max}:friend",
                "action_steps": [
                    "Open the Polymarket market and the official settlement source before considering anything.",
                    f"Verify the official source still shows {observed_max:.0f}°{unit} as the daily high at {station}, and confirm no newer observation exceeded it.",
                    f"Confirm the latest temperature remains below/equal to the high and the cooling sequence still holds ({cooling_obs} non-rising hourly observations in this alert).",
                    f"Check the live YES ask. This WATCH was based on {ask:.3f}; if the remaining payout has mostly disappeared, skip it.",
                ],
                "risk_note": (
                    "Friend-style late-lock WATCH only. Do not treat the displayed heuristic score as calibrated certainty. "
                    "The official settlement source and any later/revised observation override this alert."
                ),
            },
        ))

    return out
