from __future__ import annotations

import json
import math


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


def selected_token_payout(signal_row: dict, market: dict) -> float | None:
    """Return the final payout of the exact token selected by a stored signal.

    Fail closed unless Gamma says the market is closed, the stored token maps
    unambiguously into the market's current CLOB token vector, and the corresponding
    payout is finite and inside [0, 1]. This preserves disputed/partial outcomes such
    as 0.5 instead of coercing every non-1 result into a full loss.
    """
    if not isinstance(signal_row, dict) or not isinstance(market, dict):
        return None
    if market.get("closed") is not True:
        return None

    signal_tokens = [str(x) for x in _json_list(signal_row.get("token_ids"))]
    market_tokens = [str(x) for x in _json_list(market.get("clobTokenIds"))]
    prices = _json_list(market.get("outcomePrices"))
    if len(signal_tokens) != 1 or not market_tokens or len(market_tokens) != len(prices):
        return None

    token = signal_tokens[0]
    if market_tokens.count(token) != 1:
        return None
    idx = market_tokens.index(token)
    try:
        payout = float(prices[idx])
    except (TypeError, ValueError, OverflowError, IndexError):
        return None
    if not math.isfinite(payout) or payout < 0.0 or payout > 1.0:
        return None
    return payout
