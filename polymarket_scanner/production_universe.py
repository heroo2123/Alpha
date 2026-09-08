from __future__ import annotations

"""Bounded production universe derived from the complete Gamma keyset walk.

The production VM must prove that it traversed the entire active Gamma universe, but
it does not need to retain every market as a Python object. This module keeps only
markets that can enter one of the *existing* production detector lanes. It is not a
liquidity/ranking filter: every retained/excluded decision follows a necessary or
conservative prerequisite of code that already exists in the scanner.

Raw discovery completeness and retained/materialized coverage are reported
separately. Hitting either hard cap fails closed.
"""

import os
import re
from collections.abc import Callable, Iterable

from .detectors_v02 import threshold
from .models import Market
from .polymarket import PolymarketClient, UniverseIncompleteError, _f

PRODUCTION_UNIVERSE_FILTER_VERSION = "detector_eligible_v3_neg_risk_and_sports_scope"
DISCOVERY_HARD_CAP = max(1, int(os.getenv("PRODUCTION_DISCOVERY_HARD_CAP", "250000")))
MATERIALIZED_HARD_CAP = max(1, int(os.getenv("PRODUCTION_MATERIALIZED_HARD_CAP", "30000")))
MAX_MANUAL_NEG_RISK_LEGS = 6

_SPORT_TAGS = {
    "sports", "sport", "nba", "nfl", "mlb", "nhl", "wnba", "ncaa", "ncaab", "ncaaf",
    "soccer", "football", "tennis", "ufc", "mma", "boxing", "f1", "formula-1",
    "golf", "cricket", "esports",
}
_SPORTS_UNSUPPORTED = re.compile(
    r"\b(?:spread|handicap|over|under|o/u|total|set|period|quarter|half|inning|map|round|"
    r"series|race\s+to|margin|first\s+to|game\s+\d|set\s+\d|period\s+\d|"
    r"regulation|overtime|extra\s+time|shootout|penalt(?:y|ies)|draw\s+no\s+bet|dnb|"
    r"two[-\s]+way)\b",
    re.I,
)
_SIGNED_LINE = re.compile(r"(?:^|[\s(])[-+]\d+(?:\.\d+)?(?:[\s)]|$)")
_CRYPTO_ASSET = re.compile(r"\b(?:bitcoin|btc|ethereum|eth|solana|sol|xrp)\b", re.I)
_OTHER = re.compile(r"\bother\b", re.I)
_OTHER_RULE_A = re.compile(
    r"(?:resolve|resolves|resolved|resolution)[^.!]{0,120}\bother\b",
    re.I,
)
_OTHER_RULE_B = re.compile(
    r"\bother\b[^.!]{0,120}(?:resolve|resolves|resolved|resolution)",
    re.I,
)


def _flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _json_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        import json
        try:
            parsed = json.loads(value)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _strict_binary_raw(market: dict) -> bool:
    outcomes = [str(x).strip().lower() for x in _json_list(market.get("outcomes"))]
    tokens = [str(x).strip() for x in _json_list(market.get("clobTokenIds"))]
    return bool(
        len(outcomes) == 2
        and sorted(outcomes) == ["no", "yes"]
        and len(tokens) == 2
        and len(set(tokens)) == 2
        and all(tokens)
        and str(market.get("conditionId") or "").strip()
    )


def _market_open_for_execution(market: dict) -> bool:
    return bool(
        _flag(market.get("active", True))
        and not _flag(market.get("closed", False))
        and _flag(market.get("acceptingOrders"))
        and _flag(market.get("enableOrderBook"))
    )


def _neg_risk_precertifiable_parent(event: dict) -> bool:
    """Keep only parents that can satisfy the existing hardened V3 proof."""
    if not _flag(event.get("negRisk") or event.get("enableNegRisk")):
        return False
    if event.get("negRiskAugmented") is True:
        return False

    children = [row for row in (event.get("markets") or []) if isinstance(row, dict)]
    if not (3 <= len(children) <= MAX_MANUAL_NEG_RISK_LEGS):
        return False

    parent = str(
        event.get("negRiskMarketID")
        or event.get("negRiskMarketId")
        or ""
    ).strip()
    if not parent:
        return False

    child_ids: list[str] = []
    other_count = 0
    for child in children:
        child_id = str(child.get("id") or "").strip()
        if not child_id:
            return False
        child_ids.append(child_id)

        if type(child.get("active")) is not bool or type(child.get("closed")) is not bool:
            return False
        if child.get("active") is not True or child.get("closed") is not False:
            return False
        if not _strict_binary_raw(child):
            return False

        child_parent = str(
            child.get("negRiskMarketID")
            or child.get("negRiskMarketId")
            or parent
        ).strip()
        if child_parent != parent:
            return False

        child_text = f"{child.get('question') or ''} {child.get('slug') or ''}"
        if _OTHER.search(child_text):
            other_count += 1

    if len(child_ids) != len(set(child_ids)) or other_count != 1:
        return False

    rules = (
        f"{event.get('description') or ''} "
        f"{children[0].get('description') or ''}"
    )
    return bool(_OTHER_RULE_A.search(rules) or _OTHER_RULE_B.search(rules))


def _tag_names(value: object) -> set[str]:
    out: set[str] = set()
    for tag in value if isinstance(value, list) else []:
        if isinstance(tag, dict):
            raw = tag.get("slug") or tag.get("label") or tag.get("name")
        else:
            raw = tag
        text = str(raw or "").strip().lower()
        if text:
            out.add(text)
    return out


def _sports_metadata_scope(event: dict, market: dict) -> bool:
    category = str(
        market.get("category")
        or event.get("category")
        or ""
    ).strip().lower()
    tags = _tag_names(event.get("tags")) | _tag_names(market.get("tags"))
    return "sport" in category or bool(tags & _SPORT_TAGS)


def _sports_candidate(
    event: dict,
    market: dict,
    *,
    live_sports_slugs: set[str] | None = None,
) -> bool:
    event_slug = str(event.get("slug") or "").strip()
    feed_scope = bool(event_slug and live_sports_slugs and event_slug in live_sports_slugs)
    if not (_sports_metadata_scope(event, market) or feed_scope):
        return False
    if not _strict_binary_raw(market):
        return False

    question = str(market.get("question") or "")
    scope = f"{question} {event.get('title') or ''} {market.get('slug') or ''}"
    if "win" not in question.lower() or re.search(r"\bdraw\b", question, re.I):
        return False
    if _SPORTS_UNSUPPORTED.search(scope) or _SIGNED_LINE.search(question):
        return False

    raw_type = str(
        market.get("sportsMarketType")
        or market.get("sports_market_type")
        or market.get("marketType")
        or ""
    ).strip().lower()
    return not raw_type or raw_type in {
        "moneyline", "money_line", "match winner", "match_winner", "winner"
    }


def _crypto_candidate(event: dict, market: dict) -> bool:
    text = (
        f"{event.get('title') or ''} {market.get('question') or ''} "
        f"{market.get('description') or event.get('description') or ''} "
        f"{market.get('resolutionSource') or event.get('resolutionSource') or ''}"
    ).lower()
    if not _CRYPTO_ASSET.search(text):
        return False
    slug_text = f"{event.get('slug') or ''} {market.get('slug') or ''}".lower()
    return bool(
        "chainlink" in text
        or "30-second" in text
        or "30 second" in text
        or "60-second" in text
        or "60 second" in text
        or "up or down" in text
        or "updown" in slug_text
    )


def _wide_spread_candidate(market: dict) -> bool:
    spread = _f(market.get("spread"), None)
    if spread is None:
        bid = _f(market.get("bestBid"), None)
        ask = _f(market.get("bestAsk"), None)
        if bid is None or ask is None:
            return False
        spread = ask - bid
    volume = _f(
        market.get("volume24hr")
        or market.get("volume24hrClob")
        or market.get("volumeNum"),
        0.0,
    )
    return bool(spread >= 0.10 and volume >= 2000.0)


def _crossed_binary_candidate(market: dict) -> bool:
    if not _strict_binary_raw(market):
        return False
    bid = _f(market.get("bestBid"), None)
    ask = _f(market.get("bestAsk"), None)
    return bool(bid is not None and ask is not None and ask < bid)


def market_matches_existing_detector(
    event: dict,
    market: dict,
    *,
    live_sports_slugs: set[str] | None = None,
) -> bool:
    """Conservative pre-materialisation gate for current production detectors."""
    if not _market_open_for_execution(market):
        return False

    question = str(market.get("question") or "")
    text = (
        f"{event.get('title') or ''} {question} "
        f"{market.get('description') or event.get('description') or ''} "
        f"{market.get('resolutionSource') or event.get('resolutionSource') or ''}"
    ).lower()

    if threshold(question) is not None:
        return True
    if "highest temperature" in text:
        return True
    if _crypto_candidate(event, market):
        return True
    if _sports_candidate(event, market, live_sports_slugs=live_sports_slugs):
        return True
    if "bls.gov" in text or "bureau of labor statistics" in text:
        return True
    if _wide_spread_candidate(market):
        return True
    if _crossed_binary_candidate(market):
        return True
    return False


class ProductionPolymarketClient(PolymarketClient):
    """Complete Gamma discovery with bounded detector-eligible materialisation."""

    def __init__(
        self,
        *,
        sports_slug_provider: Callable[[], Iterable[str]] | None = None,
    ) -> None:
        super().__init__()
        self._sports_slug_provider = sports_slug_provider
        self._sports_slug_snapshot: set[str] = set()
        self._discovered_market_count = 0
        self._materialized_market_count = 0
        self._keyset_page_count = 0

    def _current_sports_slugs(self) -> set[str]:
        provider = getattr(self, "_sports_slug_provider", None)
        if provider is None:
            return set()
        try:
            return {str(value).strip() for value in provider() if str(value).strip()}
        except Exception:
            return set()

    def universe_status(self, *, now: float | None = None) -> dict:
        status = super().universe_status(now=now)
        status.update({
            "filter_version": PRODUCTION_UNIVERSE_FILTER_VERSION,
            "discovered_market_count": self._discovered_market_count,
            "materialized_market_count": self._materialized_market_count,
            "discovery_hard_cap": DISCOVERY_HARD_CAP,
            "materialized_hard_cap": MATERIALIZED_HARD_CAP,
            "keyset_pages": self._keyset_page_count,
            "sports_slug_snapshot_count": len(getattr(self, "_sports_slug_snapshot", set())),
        })
        return status

    def _append_detector_eligible_events(
        self,
        out: list[Market],
        events: list[dict],
        seen_market_ids: set[str],
    ) -> None:
        sports_slugs = getattr(self, "_sports_slug_snapshot", set())
        for event in events:
            if not isinstance(event, dict):
                continue
            children = [row for row in (event.get("markets") or []) if isinstance(row, dict)]
            self._discovered_market_count += sum(
                1
                for row in children
                if _flag(row.get("active", True)) and not _flag(row.get("closed", False))
            )
            if self._discovered_market_count > DISCOVERY_HARD_CAP:
                self._fetch_reason = (
                    f"raw Gamma market count exceeded production discovery cap {DISCOVERY_HARD_CAP}"
                )
                raise UniverseIncompleteError(self._fetch_reason)

            if _neg_risk_precertifiable_parent(event):
                selected_event = event
            else:
                selected = [
                    row for row in children
                    if market_matches_existing_detector(
                        event,
                        row,
                        live_sports_slugs=sports_slugs,
                    )
                ]
                if not selected:
                    continue
                selected_event = dict(event)
                selected_event["markets"] = selected

            super()._append_events(out, [selected_event], seen_market_ids)
            if len(out) > MATERIALIZED_HARD_CAP:
                self._fetch_reason = (
                    f"detector-eligible materialized market count exceeded production cap {MATERIALIZED_HARD_CAP}"
                )
                raise UniverseIncompleteError(self._fetch_reason)

    async def _fetch_active_markets(self) -> list[Market]:
        markets: list[Market] = []
        seen: set[str] = set()
        self._sports_slug_snapshot = self._current_sports_slugs()
        self._discovered_market_count = 0
        self._materialized_market_count = 0
        self._keyset_page_count = 0
        self._fetch_complete = False
        self._fetch_reason = "production Gamma keyset fetch in progress"

        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            events, next_cursor = await self._event_keyset_page(cursor)
            self._keyset_page_count += 1
            self._append_detector_eligible_events(markets, events, seen)

            if next_cursor is None:
                break
            if next_cursor == cursor or next_cursor in seen_cursors:
                self._fetch_reason = "Gamma keyset pagination repeated a cursor before exhaustion"
                raise UniverseIncompleteError(self._fetch_reason)
            seen_cursors.add(next_cursor)
            cursor = next_cursor
            if self._keyset_page_count >= 5000:
                self._fetch_reason = "Gamma keyset pagination exceeded 5000 pages without exhaustion"
                raise UniverseIncompleteError(self._fetch_reason)

        if not markets:
            self._fetch_reason = "complete Gamma walk produced no detector-eligible markets"
            raise UniverseIncompleteError(self._fetch_reason)

        self._materialized_market_count = len(markets)
        self._fetch_complete = True
        self._fetch_reason = (
            "Gamma active-event keyset exhausted naturally; detector-eligible production subset materialized"
        )
        return markets
