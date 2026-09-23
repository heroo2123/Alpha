"""Size-aware observation measurements. Quotes are never fills or real P&L."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math

from .evidence import EvidenceError, EvidenceStore, finite


MARKOUT_SECONDS = (1, 5, 30, 120, 600)


def amount(value: object) -> Decimal:
    if isinstance(value, bool):
        raise EvidenceError("INVALID_DECIMAL")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise EvidenceError("INVALID_DECIMAL") from None
    if not result.is_finite() or result < 0:
        raise EvidenceError("INVALID_DECIMAL")
    return result


@dataclass(frozen=True)
class ExecutableDepth:
    requested_units: Decimal
    visible_units: Decimal
    gross_value: Decimal
    net_value: Decimal | None
    full_depth: bool
    fee_status: str


def executable_depth(levels: list, units: object, *, direction: str,
                     fee_per_share: object | None) -> ExecutableDepth:
    """Integrate asks for acquisition or bids for sale; unknown fees stay unknown."""
    wanted = amount(units)
    if wanted <= 0 or direction not in {"ACQUIRE", "SELL"}:
        raise EvidenceError("DEPTH_REQUEST_INVALID")
    if not isinstance(levels, list) or len(levels) > 1000:
        raise EvidenceError("DEPTH_LEVEL_LIMIT")
    parsed = []
    for level in levels:
        if not isinstance(level, dict) or set(level) != {"price", "size"}:
            raise EvidenceError("DEPTH_LEVEL_INVALID")
        price, size = amount(level["price"]), amount(level["size"])
        if not 0 < price < 1 or size <= 0:
            raise EvidenceError("DEPTH_LEVEL_INVALID")
        parsed.append((price, size))
    prices = [p for p, _ in parsed]
    if len(set(prices)) != len(prices):
        raise EvidenceError("DUPLICATE_DEPTH_PRICE")
    parsed.sort(reverse=direction == "SELL")
    filled, gross = Decimal(0), Decimal(0)
    for price, size in parsed:
        take = min(wanted - filled, size)
        filled += take
        gross += price * take
        if filled == wanted:
            break
    fee = None if fee_per_share is None else amount(fee_per_share) * filled
    net = None if fee is None else gross + fee if direction == "ACQUIRE" else gross - fee
    return ExecutableDepth(wanted, filled, gross, net, filled == wanted,
                           "UNKNOWN" if fee is None else "EXPLICIT_INPUT_NOT_VENUE_ATTESTATION")


def measure_markout(store: EvidenceStore, decision_id: str, *, token_id: str,
                    units: object, entry_cost_per_share: object, horizon_seconds: int,
                    tolerance_seconds: float, as_of: float, fee_per_share: object | None = None) -> dict:
    """First eligible received book within a declared horizon window, no interpolation.

    This produces a depth-valued markout, not a simulated/actual execution record.
    Absent or partial bid depth cannot produce a fully realizable P&L estimate.
    """
    if horizon_seconds not in MARKOUT_SECONDS or not 0 <= finite(tolerance_seconds) <= 60:
        raise EvidenceError("MARKOUT_WINDOW_INVALID")
    as_of = finite(as_of)
    decision = store.get(decision_id)
    if decision["kind"] != "DECISION":
        raise EvidenceError("DECISION_REQUIRED")
    start = decision["body"]["recorded_at"] + horizon_seconds
    result = {"decision_id": decision_id, "horizon_seconds": horizon_seconds,
              "target_at": start, "status": "UNKNOWN", "reason": "NO_CAUSAL_BOOK",
              "markout_per_share": None, "financial_authority": False,
              "measurement_class": "OBSERVED_DEPTH_COUNTERFACTUAL_NOT_FILL",
              "trading_pnl": None}
    if as_of < start:
        return dict(result, reason="HORIZON_NOT_REACHED")
    entry = amount(entry_cost_per_share)
    page = 0
    # Bounded archive has an explicit row ceiling, so replay cannot grow unbounded.
    for _ in range((store.limits.max_records + 199) // 200):
        rows = store.causal_inputs(decision["event_id"], min(as_of, start + tolerance_seconds),
                                   after_seq=page, limit=200)
        if not rows:
            break
        page = rows[-1]["seq"]
        for row in rows:
            body = row["body"]
            if row["kind"] != "BOOK" or body["received_at"] < start:
                continue
            book = body["payload"]
            if book.get("token_id") != token_id:
                continue
            # A delayed old book cannot represent a fresh horizon mark.
            if body["observed_at"] is None or not start <= body["observed_at"] <= body["received_at"]:
                continue
            if book.get("stream_healthy") is not True:
                return dict(result, reason="BOOK_STREAM_NOT_HEALTHY", book_id=row["id"])
            bids, asks = book.get("bids"), book.get("asks")
            if not bids or not asks:
                return dict(result, reason="MISSING_BOOK_SIDE", book_id=row["id"])
            buy = executable_depth(asks, units, direction="ACQUIRE", fee_per_share=fee_per_share)
            sell = executable_depth(bids, units, direction="SELL", fee_per_share=fee_per_share)
            if max(amount(x["price"]) for x in bids) >= min(amount(x["price"]) for x in asks):
                return dict(result, reason="CROSSED_OR_LOCKED_BOOK", book_id=row["id"])
            details = dict(result, book_id=row["id"], book_sha256=row["sha256"],
                           received_at=body["received_at"], visible_units=str(sell.visible_units),
                           gross_bid_value=str(sell.gross_value), gross_ask_value=str(buy.gross_value),
                           evidence_class=body["evidence_class"])
            if not sell.full_depth:
                return dict(details, reason="INSUFFICIENT_EXIT_DEPTH")
            if sell.net_value is None:
                return dict(details, reason="EXIT_FEES_UNKNOWN")
            return dict(details, status="MEASURED", reason="FULL_VISIBLE_DEPTH_ESTIMATE",
                        markout_per_share=str(sell.net_value / sell.requested_units - entry))
    return result


def score_binary(probabilities: list[float], outcomes: list[int], *, groups: list[str]) -> dict:
    """Describe a declared target; never label it calibrated just because scored."""
    if not probabilities or len(probabilities) != len(outcomes) or len(groups) != len(outcomes):
        raise EvidenceError("SCORE_INPUT_MISMATCH")
    for p, y in zip(probabilities, outcomes):
        if not 0 <= finite(p) <= 1 or type(y) is not int or y not in (0, 1):
            raise EvidenceError("SCORE_INPUT_INVALID")
    brier = sum((p - y)**2 for p, y in zip(probabilities, outcomes)) / len(outcomes)
    impossible = any(p == 0 and y == 1 or p == 1 and y == 0 for p, y in zip(probabilities, outcomes))
    log_loss = None if impossible else -sum(math.log(p if y else 1-p) for p, y in zip(probabilities, outcomes)) / len(outcomes)
    return {"n_predictions": len(outcomes), "n_declared_groups": len(set(groups)),
            "independence_validated": False,
            "brier": brier, "log_loss": log_loss, "log_loss_infinite": impossible,
            "calibration_status": "SCORED_NOT_CALIBRATED"}
