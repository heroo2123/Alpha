"""Gamma ask-side screening transformation; no HTTP client or refresh scheduler."""
from __future__ import annotations

import math
from .models import Book, Market

GAMMA_SCREENING_VERSION = "gamma_bbo_v3_page_receipts_non_executable"


def _valid_price(value: object) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return price if math.isfinite(price) and 0.0 < price < 1.0 else None


def gamma_screening_books(markets: list[Market], *, received_at: float | None = None) -> dict[str, Book]:
    """One-sided and crossed quotes are useful screening, never depth authority.

    NO quotes use binary complement inference. General same-market underround
    recall is therefore limited. The explicit receipt argument is for callers with
    an actual observation timestamp; loading a generation must never stamp 'now'.
    """
    books = {}
    for market in markets:
        labels = [str(label).strip().lower() for label in market.outcomes]
        if sorted(labels) != ["no", "yes"] or len(market.token_ids) != 2 or len(set(market.token_ids)) != 2:
            continue
        receipt = market.raw.get("_gamma_received_at", received_at)
        if not isinstance(receipt, (int, float)) or not math.isfinite(receipt):
            continue
        yes, no = market.token_ids[labels.index("yes")], market.token_ids[labels.index("no")]
        bid, ask = _valid_price(market.best_bid), _valid_price(market.best_ask)
        if ask is not None:
            books[yes] = Book(yes, [(bid, 1.0)] if bid is not None else [], [(ask, 1.0)],
                              timestamp="price-discovery", received_at=receipt, source="gamma_bbo_screening")
        if bid is not None:
            no_ask = _valid_price(1 - bid)
            if no_ask is not None:
                no_bid = _valid_price(1 - ask) if ask is not None else None
                books[no] = Book(no, [(no_bid, 1.0)] if no_bid is not None else [], [(no_ask, 1.0)],
                                 timestamp="price-discovery", received_at=receipt, source="gamma_bbo_screening")
    return books
