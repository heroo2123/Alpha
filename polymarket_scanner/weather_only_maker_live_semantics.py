from __future__ import annotations

"""Independent live certification for maker PAPER trade-stream semantics.

This module is a verification tool, not a trading runtime.  It selects currently active
public CLOB token IDs from recent taker-only Data API trades, subscribes to those tokens
on the anonymous Market WebSocket, parses real ``last_trade_price`` frames through the
production maker parser, and independently correlates transaction/side identity back to
``takerOnly=true`` Data API rows.

Certification is deliberately fail closed.  A quiet observation window is not a pass;
multiple independently correlated trades are required.  A diagnostic JSON report is
written even when certification fails so lack of evidence is distinguishable from a
schema, causality, identity, or side-semantics defect.
"""

import argparse
import asyncio
import json
import math
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

import websockets

from .weather_only_maker_trade_stream import (
    MARKET_WS_URL,
    MakerTradeStreamError,
    parse_last_trade_price_message,
)


LIVE_SEMANTICS_VERSION = "weather_maker_live_semantics_v2_recent_taker_targets_diagnostic"
DATA_TRADES_URL = "https://data-api.polymarket.com/trades"
DEFAULT_MAX_TOKENS = 32
DEFAULT_MIN_TOKENS = 8
DEFAULT_OBSERVE_SECONDS = 120.0
DEFAULT_CORRELATE_SECONDS = 45.0
DEFAULT_TARGET_WS_TRADES = 8
DEFAULT_MIN_CORRELATED_TRADES = 3
DEFAULT_RECENT_LOOKBACK_SECONDS = 900
DEFAULT_FALLBACK_LOOKBACK_SECONDS = 3600


class MakerLiveSemanticsError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _json_get(url: str, params: dict[str, object], *, timeout: float = 20.0):
    target = str(url) + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        target,
        headers={
            "User-Agent": "weather-maker-live-semantics/2.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise MakerLiveSemanticsError("LIVE_SEMANTICS_HTTP_STATUS")
        try:
            return json.loads(response.read().decode("utf-8", errors="strict"))
        except (UnicodeError, json.JSONDecodeError):
            raise MakerLiveSemanticsError("LIVE_SEMANTICS_RESPONSE_JSON_INVALID") from None


def _canonical_condition(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text != value or not text.startswith("0x") or len(text) != 66:
        return None
    body = text[2:]
    if any(ch.lower() not in "0123456789abcdef" for ch in body):
        return None
    return "0x" + body.lower()


def _canonical_tx_hash(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text != value or not text.startswith("0x") or len(text) != 66:
        return None
    body = text[2:]
    if any(ch.lower() not in "0123456789abcdef" for ch in body):
        return None
    return "0x" + body.lower()


def select_recent_trade_targets(rows: object, *, max_tokens: int) -> dict[str, str]:
    """Return token -> condition IDs in provider recency order.

    Selection intentionally depends only on public taker-trade activity.  It does not
    treat Data API trade rows as fill evidence for the virtual maker order; the rows
    merely choose tokens likely to emit fresh WebSocket trades during the live gate.
    """
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
        raise MakerLiveSemanticsError("LIVE_SEMANTICS_TOKEN_LIMIT_INVALID")
    if not isinstance(rows, list):
        raise MakerLiveSemanticsError("LIVE_SEMANTICS_RECENT_TRADES_SHAPE_INVALID")

    selected: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        token = str(row.get("asset") or "").strip()
        condition = _canonical_condition(row.get("conditionId"))
        side = str(row.get("side") or "").strip().upper()
        if not token or not token.isdigit() or condition is None or side not in {"BUY", "SELL"}:
            continue
        existing = selected.get(token)
        if existing is not None and existing != condition:
            raise MakerLiveSemanticsError("LIVE_SEMANTICS_TOKEN_CONDITION_CONFLICT")
        selected.setdefault(token, condition)
        if len(selected) >= max_tokens:
            break
    return selected


def correlation_sides(observed: dict, rows: object) -> set[str]:
    """Return taker-only Data API sides matching one exact observed WS trade identity."""
    if not isinstance(observed, dict) or not isinstance(rows, list):
        raise MakerLiveSemanticsError("LIVE_SEMANTICS_CORRELATION_INPUT_INVALID")
    tx = _canonical_tx_hash(observed.get("transaction_hash"))
    condition = _canonical_condition(observed.get("condition_id"))
    token = str(observed.get("token_id") or "").strip()
    if tx is None or condition is None or not token:
        raise MakerLiveSemanticsError("LIVE_SEMANTICS_OBSERVED_IDENTITY_INVALID")

    sides: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _canonical_tx_hash(row.get("transactionHash")) != tx:
            continue
        if str(row.get("asset") or "").strip() != token:
            continue
        if _canonical_condition(row.get("conditionId")) != condition:
            continue
        side = str(row.get("side") or "").strip().upper()
        if side in {"BUY", "SELL"}:
            sides.add(side)
    return sides


async def _observe_ws_trades(
    token_to_condition: dict[str, str],
    *,
    observe_seconds: float,
    target_ws_trades: int,
) -> tuple[set[str], list[dict], int, bool]:
    desired = sorted(token_to_condition)
    covered: set[str] = set()
    pong_seen = False
    messages = 0
    parsed: dict[tuple[str, str], dict] = {}
    deadline = time.monotonic() + observe_seconds
    next_ping = time.monotonic()

    async with websockets.connect(
        MARKET_WS_URL,
        ping_interval=None,
        close_timeout=5,
        open_timeout=15,
        max_size=2 * 1024 * 1024,
        max_queue=256,
    ) as ws:
        await ws.send(
            json.dumps(
                {
                    "assets_ids": desired,
                    "type": "market",
                    "custom_feature_enabled": False,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )

        while time.monotonic() < deadline and len(parsed) < target_ws_trades:
            now_mono = time.monotonic()
            if now_mono >= next_ping:
                await ws.send("PING")
                next_ping = now_mono + 10.0
            timeout = max(0.05, min(1.0, deadline - now_mono))
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                continue
            messages += 1
            received_at = time.time()
            if raw == "PONG" or raw == b"PONG":
                pong_seen = True
                continue
            if isinstance(raw, bytes):
                try:
                    raw = raw.decode("utf-8", errors="strict")
                except UnicodeError:
                    raise MakerLiveSemanticsError("LIVE_SEMANTICS_WS_ENCODING_INVALID") from None
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                raise MakerLiveSemanticsError("LIVE_SEMANTICS_WS_JSON_INVALID") from None
            rows = body if isinstance(body, list) else [body]
            if not all(isinstance(row, dict) for row in rows):
                raise MakerLiveSemanticsError("LIVE_SEMANTICS_WS_ROW_SHAPE_INVALID")

            for row in rows:
                event_type = str(row.get("event_type") or "")
                if event_type == "book":
                    token = str(row.get("asset_id") or "").strip()
                    if token not in token_to_condition:
                        continue
                    condition = _canonical_condition(row.get("market"))
                    if condition != token_to_condition[token]:
                        raise MakerLiveSemanticsError("LIVE_SEMANTICS_BOOK_IDENTITY_MISMATCH")
                    if not isinstance(row.get("bids"), list) or not isinstance(row.get("asks"), list):
                        raise MakerLiveSemanticsError("LIVE_SEMANTICS_BOOK_SCHEMA_DRIFT")
                    covered.add(token)
                    continue

                if event_type != "last_trade_price":
                    continue
                token = str(row.get("asset_id") or "").strip()
                if token not in token_to_condition or token not in covered:
                    continue
                condition = _canonical_condition(row.get("market"))
                if condition != token_to_condition[token]:
                    raise MakerLiveSemanticsError("LIVE_SEMANTICS_TRADE_IDENTITY_MISMATCH")

                # Use the actual production parser so required metadata, identity and
                # receipt/execution causality are certified rather than reimplemented.
                trade = parse_last_trade_price_message(row, received_at=received_at)
                if trade is None:
                    raise MakerLiveSemanticsError("LIVE_SEMANTICS_PRODUCTION_PARSER_IGNORED_TRADE")
                tx = _canonical_tx_hash(row.get("transaction_hash"))
                if tx is None:
                    raise MakerLiveSemanticsError("LIVE_SEMANTICS_TX_IDENTITY_INVALID")
                key = (tx, trade.token_id)
                parsed.setdefault(
                    key,
                    {
                        "transaction_hash": tx,
                        "token_id": trade.token_id,
                        "condition_id": condition,
                        "side": str(trade.aggressor_side),
                        "price": float(trade.price),
                        "size": float(trade.shares),
                        "executed_at": float(trade.effective_executed_at),
                        "received_at": float(trade.received_at),
                        "trade_id": trade.trade_id,
                    },
                )

    return covered, list(parsed.values()), messages, pong_seen


def _correlate(
    observed: list[dict],
    *,
    correlate_seconds: float,
) -> tuple[dict[tuple[str, str], dict], list[dict]]:
    deadline = time.monotonic() + correlate_seconds
    correlated: dict[tuple[str, str], dict] = {}
    mismatches: list[dict] = []
    while time.monotonic() < deadline:
        for item in observed:
            key = (str(item["transaction_hash"]), str(item["token_id"]))
            if key in correlated:
                continue
            start = max(0, int(math.floor(float(item["executed_at"]))) - 10)
            end = int(math.ceil(time.time())) + 10
            rows = _json_get(
                DATA_TRADES_URL,
                {
                    "market": item["condition_id"],
                    "takerOnly": "true",
                    "start": start,
                    "end": end,
                    "limit": 1000,
                },
            )
            if not isinstance(rows, list):
                raise MakerLiveSemanticsError("LIVE_SEMANTICS_CORRELATION_RESPONSE_SHAPE_INVALID")
            sides = correlation_sides(item, rows)
            if not sides:
                continue
            if sides != {str(item["side"])}:
                mismatches.append(
                    {
                        "ws_side": str(item["side"]),
                        "data_api_taker_sides": sorted(sides),
                    }
                )
                continue
            correlated[key] = {
                "side": str(item["side"]),
                "condition_id": str(item["condition_id"]),
            }
        if mismatches or len(correlated) >= len(observed):
            break
        time.sleep(2.0)
    return correlated, mismatches


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run_live_certification(
    *,
    report_path: Path,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    min_tokens: int = DEFAULT_MIN_TOKENS,
    observe_seconds: float = DEFAULT_OBSERVE_SECONDS,
    correlate_seconds: float = DEFAULT_CORRELATE_SECONDS,
    target_ws_trades: int = DEFAULT_TARGET_WS_TRADES,
    min_correlated_trades: int = DEFAULT_MIN_CORRELATED_TRADES,
) -> int:
    report: dict = {
        "version": LIVE_SEMANTICS_VERSION,
        "certified": False,
        "failure_code": None,
        "selection_source": "DATA_API_RECENT_TAKER_TRADES",
        "public_market_ws_url": MARKET_WS_URL,
        "data_api_url": DATA_TRADES_URL,
        "requested_max_tokens": int(max_tokens),
        "required_min_tokens": int(min_tokens),
        "target_ws_trades": int(target_ws_trades),
        "required_correlated_trades": int(min_correlated_trades),
        "selected_tokens": 0,
        "covered_tokens": 0,
        "messages_observed": 0,
        "production_parsed_unique_trade_frames": 0,
        "data_api_taker_only_correlations": 0,
        "side_mismatch_count": 0,
        "observed_side_counts": {},
        "correlated_side_counts": {},
        "receipt_minus_execution_min_seconds": None,
        "receipt_minus_execution_max_seconds": None,
        "authenticated": False,
        "actual_fill_authority": False,
        "financial_authority": False,
        "automatic_order_placement": False,
    }

    try:
        if any(isinstance(value, bool) for value in (
            max_tokens,
            min_tokens,
            target_ws_trades,
            min_correlated_trades,
        )):
            raise MakerLiveSemanticsError("LIVE_SEMANTICS_ARGUMENT_INVALID")
        if not (0 < min_tokens <= max_tokens <= 32):
            raise MakerLiveSemanticsError("LIVE_SEMANTICS_ARGUMENT_INVALID")
        if not (0 < min_correlated_trades <= target_ws_trades):
            raise MakerLiveSemanticsError("LIVE_SEMANTICS_ARGUMENT_INVALID")
        if observe_seconds <= 0.0 or correlate_seconds <= 0.0:
            raise MakerLiveSemanticsError("LIVE_SEMANTICS_ARGUMENT_INVALID")

        now = int(time.time())
        recent = _json_get(
            DATA_TRADES_URL,
            {
                "takerOnly": "true",
                "start": max(0, now - DEFAULT_RECENT_LOOKBACK_SECONDS),
                "end": now + 5,
                "limit": 1000,
            },
        )
        selected = select_recent_trade_targets(recent, max_tokens=max_tokens)
        if len(selected) < min_tokens:
            fallback = _json_get(
                DATA_TRADES_URL,
                {
                    "takerOnly": "true",
                    "start": max(0, now - DEFAULT_FALLBACK_LOOKBACK_SECONDS),
                    "end": now + 5,
                    "limit": 1000,
                },
            )
            selected = select_recent_trade_targets(fallback, max_tokens=max_tokens)
        report["selected_tokens"] = len(selected)
        if len(selected) < min_tokens:
            raise MakerLiveSemanticsError("LIVE_SEMANTICS_INSUFFICIENT_ACTIVE_TOKENS")

        covered, observed, messages, pong_seen = asyncio.run(
            _observe_ws_trades(
                selected,
                observe_seconds=float(observe_seconds),
                target_ws_trades=int(target_ws_trades),
            )
        )
        report["covered_tokens"] = len(covered)
        report["messages_observed"] = int(messages)
        report["application_ping_pong"] = bool(pong_seen)
        report["production_parsed_unique_trade_frames"] = len(observed)
        report["observed_side_counts"] = dict(
            sorted(Counter(str(item["side"]) for item in observed).items())
        )
        skews = [
            float(item["received_at"]) - float(item["executed_at"])
            for item in observed
        ]
        if skews:
            report["receipt_minus_execution_min_seconds"] = min(skews)
            report["receipt_minus_execution_max_seconds"] = max(skews)
        if not pong_seen:
            raise MakerLiveSemanticsError("LIVE_SEMANTICS_HEARTBEAT_MISSING")
        if not covered:
            raise MakerLiveSemanticsError("LIVE_SEMANTICS_BOOK_COVERAGE_MISSING")
        if not observed:
            raise MakerLiveSemanticsError("LIVE_SEMANTICS_NO_POST_COVERAGE_TRADES")

        correlated, mismatches = _correlate(
            observed,
            correlate_seconds=float(correlate_seconds),
        )
        report["data_api_taker_only_correlations"] = len(correlated)
        report["side_mismatch_count"] = len(mismatches)
        report["correlated_side_counts"] = dict(
            sorted(Counter(str(item["side"]) for item in correlated.values()).items())
        )
        if mismatches:
            raise MakerLiveSemanticsError("LIVE_SEMANTICS_TAKER_SIDE_MISMATCH")
        if len(correlated) < min_correlated_trades:
            raise MakerLiveSemanticsError("LIVE_SEMANTICS_INSUFFICIENT_CORRELATED_TRADES")

        report["certified"] = True
        report["side_semantics"] = "WS_SIDE_MATCHES_DATA_API_TAKER_SIDE"
        return_code = 0
    except MakerTradeStreamError as exc:
        report["failure_code"] = exc.code
        return_code = 2
    except MakerLiveSemanticsError as exc:
        report["failure_code"] = exc.code
        return_code = 2
    except Exception as exc:
        report["failure_code"] = f"UNEXPECTED:{type(exc).__name__}"
        return_code = 2
    finally:
        report["finished_at"] = time.time()
        _write_report(report_path, report)
        print(json.dumps(report, sort_keys=True))

    return return_code


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--min-tokens", type=int, default=DEFAULT_MIN_TOKENS)
    parser.add_argument("--observe-seconds", type=float, default=DEFAULT_OBSERVE_SECONDS)
    parser.add_argument("--correlate-seconds", type=float, default=DEFAULT_CORRELATE_SECONDS)
    parser.add_argument("--target-ws-trades", type=int, default=DEFAULT_TARGET_WS_TRADES)
    parser.add_argument(
        "--min-correlated-trades", type=int, default=DEFAULT_MIN_CORRELATED_TRADES
    )
    args = parser.parse_args()
    raise SystemExit(
        run_live_certification(
            report_path=args.report,
            max_tokens=args.max_tokens,
            min_tokens=args.min_tokens,
            observe_seconds=args.observe_seconds,
            correlate_seconds=args.correlate_seconds,
            target_ws_trades=args.target_ws_trades,
            min_correlated_trades=args.min_correlated_trades,
        )
    )


if __name__ == "__main__":
    main()
