from __future__ import annotations

import re

_HEADER = re.compile(r"^/filled\s+(\d+)\s+(.+)$", re.I)
_LEG = re.compile(
    r"^(\d+)=([0-9]+(?:\.[0-9]+)?)@([0-9]+(?:\.[0-9]+)?)\+([0-9]+(?:\.[0-9]+)?)$"
)


def parse_structural_fill_command(text: str) -> tuple[int, list[dict]] | None:
    """Parse `/filled ALERT 1=SHARES@AVG_PRICE+FEE ...` strictly.

    Leg numbers refer to the ordered legs printed in TRADE NOW. Token/market/outcome
    identity is never accepted from Telegram text; the ledger recovers it from the
    persisted exact execution certificate. An explicit fee is required for every leg
    (use `+0` on fee-free fills) so realized cost is never silently estimated.
    """
    match = _HEADER.fullmatch(str(text or "").strip())
    if not match:
        return None
    signal_id = int(match.group(1))
    if signal_id <= 0:
        return None

    fills: list[dict] = []
    for part in match.group(2).split():
        leg = _LEG.fullmatch(part)
        if leg is None:
            return None
        index = int(leg.group(1))
        shares = float(leg.group(2))
        avg_price = float(leg.group(3))
        fee_usd = float(leg.group(4))
        if index <= 0 or shares <= 0 or not (0 < avg_price < 1) or fee_usd < 0:
            return None
        fills.append({
            "leg": index,
            "shares": shares,
            "avg_price": avg_price,
            "fee_usd": fee_usd,
        })
    return (signal_id, fills) if fills else None


def structural_fill_help() -> str:
    return (
        "Use: <code>/filled ALERT_ID 1=SHARES@AVG_PRICE+FEE 2=SHARES@AVG_PRICE+FEE ...</code>\n"
        "Example: <code>/filled 137 1=50@0.470+0.02 2=50@0.460+0.02</code>\n\n"
        "Use the leg numbers from TRADE NOW. Enter the actual equal shares, actual average fill price, "
        "and actual fee paid on each leg. For a fee-free leg enter <code>+0</code>. "
        "The bot derives token identities and total cost from the stored certificate; never type token IDs."
    )
