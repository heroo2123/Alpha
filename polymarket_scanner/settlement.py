from __future__ import annotations

"""Fail-closed exact-token settlement helpers.

Gamma ``closed`` and ``outcomePrices`` are market lifecycle/price data, not final
cash-settlement proof.  A token payout is returned only when the market carries a
same-condition on-chain Conditional Tokens payout vector whose denominator is
non-zero and whose complete numerator vector is valid.
"""

import json
import math


CTF_FINALITY_VERSION = "ctf_onchain_condition_payout_v1"
CTF_EVIDENCE_KEY = "_ctf_final_resolution"


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


def _strict_uint(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isdigit():
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


def _normalized_condition(value: object) -> str | None:
    text = str(value or "").strip().lower()
    if len(text) != 66 or not text.startswith("0x"):
        return None
    if any(ch not in "0123456789abcdef" for ch in text[2:]):
        return None
    return text


def final_payout_vector(market: dict) -> tuple[tuple[str, ...], tuple[float, ...]] | None:
    """Return token IDs and final payouts only from verified CTF evidence."""
    if not isinstance(market, dict):
        return None
    market_condition = _normalized_condition(market.get("conditionId"))
    if market_condition is None:
        return None

    tokens = tuple(str(value).strip() for value in _json_list(market.get("clobTokenIds")))
    outcomes = tuple(str(value).strip().lower() for value in _json_list(market.get("outcomes")))
    if len(tokens) != 2 or len(set(tokens)) != 2 or not all(tokens):
        return None
    if outcomes != ("yes", "no") and outcomes != ("no", "yes"):
        return None
    if len(outcomes) != len(tokens):
        return None

    evidence = market.get(CTF_EVIDENCE_KEY)
    if not isinstance(evidence, dict):
        return None
    if str(evidence.get("version") or "") != CTF_FINALITY_VERSION:
        return None
    evidence_condition = _normalized_condition(evidence.get("condition_id"))
    if evidence_condition != market_condition:
        return None
    denominator = _strict_uint(evidence.get("payout_denominator"))
    numerators_raw = evidence.get("payout_numerators")
    if denominator is None or denominator <= 0 or not isinstance(numerators_raw, list):
        return None
    if len(numerators_raw) != len(tokens):
        return None
    numerators: list[int] = []
    for raw in numerators_raw:
        parsed = _strict_uint(raw)
        if parsed is None or parsed > denominator:
            return None
        numerators.append(parsed)
    if sum(numerators) != denominator:
        return None
    block_number = _strict_uint(evidence.get("block_number"))
    if block_number is None or block_number <= 0:
        return None

    payouts = tuple(value / denominator for value in numerators)
    if any(not math.isfinite(value) or value < 0.0 or value > 1.0 for value in payouts):
        return None
    return tokens, payouts


def exact_token_payout(token_id: str, market: dict) -> float | None:
    """Return final payout for one exact token, never an indicative price."""
    token = str(token_id or "").strip()
    if not token:
        return None
    resolved = final_payout_vector(market)
    if resolved is None:
        return None
    tokens, payouts = resolved
    if tokens.count(token) != 1:
        return None
    return payouts[tokens.index(token)]


def selected_token_payout(signal_row: dict, market: dict) -> float | None:
    if not isinstance(signal_row, dict):
        return None
    signal_tokens = [str(x) for x in _json_list(signal_row.get("token_ids"))]
    if len(signal_tokens) != 1:
        return None
    return exact_token_payout(signal_tokens[0], market)
