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


def exact_token_payout(token_id: str, market: dict) -> float | None:
    """Return the final payout of one exact CLOB token from a closed Gamma market.

    This is the primitive used for both directional settlement and prospective
    per-leg structural execution settlement. It fails closed on an open market,
    ambiguous/missing token mapping, malformed payout vector, nonfinite value or a
    payout outside [0,1]. Disputed/partial payouts such as 0.5 are preserved exactly.
    """
    if not isinstance(market, dict) or market.get("closed") is not True:
        return None
    token = str(token_id or "")
    if not token:
        return None

    market_tokens = [str(x) for x in _json_list(market.get("clobTokenIds"))]
    prices = _json_list(market.get("outcomePrices"))
    if not market_tokens or len(market_tokens) != len(prices) or market_tokens.count(token) != 1:
        return None
    idx = market_tokens.index(token)
    try:
        payout = float(prices[idx])
    except (TypeError, ValueError, OverflowError, IndexError):
        return None
    if not math.isfinite(payout) or payout < 0.0 or payout > 1.0:
        return None
    return payout


def selected_token_payout(signal_row: dict, market: dict) -> float | None:
    """Return the final payout of the exact single token selected by a signal."""
    if not isinstance(signal_row, dict):
        return None
    signal_tokens = [str(x) for x in _json_list(signal_row.get("token_ids"))]
    if len(signal_tokens) != 1:
        return None
    return exact_token_payout(signal_tokens[0], market)
