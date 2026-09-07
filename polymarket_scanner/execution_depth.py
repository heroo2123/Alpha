from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Callable, Iterable


def _d(value: object) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError("depth value is missing/invalid")
    try:
        out = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("depth value is not numeric") from exc
    if not out.is_finite():
        raise ValueError("depth value is nonfinite")
    return out


def aligned_to_tick(price: Decimal, tick: Decimal) -> bool:
    if tick <= 0:
        return False
    try:
        return price % tick == 0
    except InvalidOperation:
        return False


def build_safe_ask_ladder(
    asks: Iterable[tuple[object, object]],
    *,
    tick: Decimal,
    capacity_fraction: Decimal,
    unit_cost: Callable[[Decimal], Decimal],
) -> list[dict]:
    """Aggregate visible asks into a conservative, tick-valid depth ladder.

    ``capacity_fraction`` is applied independently to every displayed level. This is
    not a fill guarantee; it deliberately advertises less depth than is visible.
    The returned ladder is sorted from cheapest to most expensive and carries both
    visible and haircut cumulative sizes.
    """
    if tick <= 0 or tick >= 1:
        raise ValueError("invalid market tick")
    if capacity_fraction <= 0 or capacity_fraction > 1:
        raise ValueError("invalid depth capacity fraction")

    aggregated: dict[Decimal, Decimal] = {}
    for raw_price, raw_size in asks:
        price = _d(raw_price)
        size = _d(raw_size)
        if price <= 0 or price >= 1:
            raise ValueError("ask price outside open interval")
        if size <= 0:
            continue
        if not aligned_to_tick(price, tick):
            raise ValueError("ask price is not tick aligned")
        aggregated[price] = aggregated.get(price, Decimal("0")) + size

    if not aggregated:
        raise ValueError("order book has no executable ask depth")

    cumulative_visible = Decimal("0")
    cumulative_safe = Decimal("0")
    ladder: list[dict] = []
    for price in sorted(aggregated):
        visible = aggregated[price]
        safe = visible * capacity_fraction
        cost = unit_cost(price)
        if not cost.is_finite() or cost <= 0:
            raise ValueError("depth unit cost is invalid")
        cumulative_visible += visible
        cumulative_safe += safe
        ladder.append({
            "price": price,
            "visible_size": visible,
            "safe_size": safe,
            "cumulative_visible_size": cumulative_visible,
            "cumulative_safe_size": cumulative_safe,
            "unit_cost": cost,
        })
    return ladder


def plan_equal_share_bundle_limits(
    ladders: list[list[dict]],
    *,
    max_bundle_cost: Decimal,
    minimum_bundle_shares: Decimal,
) -> dict:
    """Choose conservative per-leg price caps and a common equal-share capacity.

    Worst-case economics are evaluated at each leg's *limit price*, not at the
    average displayed fill price. Therefore if every equal-share leg fills at or
    below its certified cap, combined cost remains no greater than the returned
    ``combined_limit_cost`` even if better levels disappear before manual execution.

    The planner greedily expands only current capacity bottlenecks and remembers the
    best common capacity achieved. It is intentionally conservative rather than an
    optimizer: failure to find a larger plan can only understate capacity.
    """
    if not ladders or any(not ladder for ladder in ladders):
        raise ValueError("every purchased leg needs ask depth")
    if max_bundle_cost <= 0:
        raise ValueError("maximum bundle cost must be positive")
    if minimum_bundle_shares <= 0:
        raise ValueError("minimum bundle shares must be positive")

    indices = [0 for _ in ladders]

    def snapshot() -> dict:
        rows = [ladder[index] for ladder, index in zip(ladders, indices)]
        combined = sum((row["unit_cost"] for row in rows), Decimal("0"))
        safe_common = min(row["cumulative_safe_size"] for row in rows)
        visible_common = min(row["cumulative_visible_size"] for row in rows)
        return {
            "indices": tuple(indices),
            "combined_limit_cost": combined,
            "safe_common_shares": safe_common,
            "visible_common_shares": visible_common,
            "legs": rows,
        }

    current = snapshot()
    if current["combined_limit_cost"] > max_bundle_cost:
        raise ValueError("current top-of-book limit cost is already above the edge floor")

    best = current if current["safe_common_shares"] >= minimum_bundle_shares else None

    # At most one move per additional depth level, so this cannot loop forever.
    remaining_moves = sum(max(0, len(ladder) - 1) for ladder in ladders)
    for _ in range(remaining_moves):
        current = snapshot()
        common = current["safe_common_shares"]
        candidates: list[tuple[Decimal, int]] = []
        for leg_index, (ladder, index) in enumerate(zip(ladders, indices)):
            if index + 1 >= len(ladder):
                continue
            row = ladder[index]
            # Raising a non-bottleneck cap cannot improve common executable shares.
            if row["cumulative_safe_size"] > common:
                continue
            next_row = ladder[index + 1]
            cost_increase = next_row["unit_cost"] - row["unit_cost"]
            if cost_increase < 0:
                # A higher limit with a lower all-in cost would contradict the
                # conservative price-cap interpretation; fail closed.
                raise ValueError("depth unit cost decreases at a higher ask price")
            candidates.append((cost_increase, leg_index))

        if not candidates:
            break

        moved = False
        for _increase, leg_index in sorted(candidates, key=lambda item: (item[0], item[1])):
            trial_indices = list(indices)
            trial_indices[leg_index] += 1
            trial_rows = [
                ladder[index]
                for ladder, index in zip(ladders, trial_indices)
            ]
            trial_cost = sum((row["unit_cost"] for row in trial_rows), Decimal("0"))
            if trial_cost <= max_bundle_cost:
                indices[leg_index] += 1
                moved = True
                break
        if not moved:
            break

        candidate = snapshot()
        if candidate["safe_common_shares"] >= minimum_bundle_shares:
            if best is None or candidate["safe_common_shares"] > best["safe_common_shares"]:
                best = candidate
            elif (
                candidate["safe_common_shares"] == best["safe_common_shares"]
                and candidate["combined_limit_cost"] < best["combined_limit_cost"]
            ):
                best = candidate

    if best is None:
        raise ValueError("conservative displayed depth cannot satisfy every leg minimum")
    return best
