from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone

from fastapi import FastAPI

from polymarket_scanner.config import settings
from polymarket_scanner.detectors import (
    binary_buy_both, duplicate_divergence, neg_risk_underround, weather_late_lock, wide_spread_watch,
)
from polymarket_scanner.detectors_v02 import (
    crypto_crossfeed_divergence, crypto_resolution_lag, nested_threshold_arbitrage, official_macro_release_lag, sports_result_lag,
)
from polymarket_scanner.macro import MacroClient
from polymarket_scanner.models import Book, Market, Signal
from polymarket_scanner.polymarket import PolymarketClient, taker_fee_per_share
from polymarket_scanner.store import Store
from polymarket_scanner.streams import CryptoRTDS, LiveMarketStream, SportsStream
from polymarket_scanner.telegram import Telegram
from polymarket_scanner.weather import WeatherClient, station_from_market

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("polybot")

store = Store(settings.db_path)
poly = PolymarketClient()
weather = WeatherClient()
macro = MacroClient()
market_stream = LiveMarketStream()
sports_stream = SportsStream()
crypto_stream = CryptoRTDS()
tg = Telegram(store)
app = FastAPI(title="Polymarket Edge Scanner", version="0.2.2")
state = {"started": time.time(), "last_scan": None, "markets": 0, "tokens": 0, "stations": 0, "last_error": None, "market_ws_workers": 0, "sports_ws": False, "crypto_rtds": False, "macro": {}, "last_reason": None}
runner_task: asyncio.Task | None = None


def _all_tokens(markets: list[Market]) -> list[str]:
    return list(dict.fromkeys(t for m in markets for t in m.token_ids if t))


def _apply_live_bbo(markets: list[Market], books: dict[str, Book]) -> None:
    for m in markets:
        y = books.get(m.yes_token or "")
        if y:
            m.best_bid = y.best_bid; m.best_ask = y.best_ask


def _quoted_asks(s: Signal) -> list[float]:
    if len(s.token_ids) == 1:
        try: return [float(s.metadata.get("ask"))]
        except (TypeError, ValueError): return []
    if s.detector in {"binary_buy_both", "nested_threshold_arb"}:
        try: return [float(s.metadata.get("yes_ask")), float(s.metadata.get("no_ask"))]
        except (TypeError, ValueError): return []
    if s.detector == "neg_risk_underround":
        try: return [float(x["ask"]) for x in s.metadata.get("legs", [])]
        except Exception: return []
    return []


async def confirm_actionable(s: Signal) -> Signal | None:
    if s.confidence != "ACTIONABLE" or not s.token_ids:
        return s
    # REST is intentionally used only for the handful of legs in a candidate
    # ACTIONABLE signal. The whole market book universe lives on WebSockets.
    fresh = await poly.books(s.token_ids)
    if any(t not in fresh or fresh[t].best_ask is None or fresh[t].best_ask_size <= 0 for t in s.token_ids):
        return None
    asks = [float(fresh[t].best_ask) for t in s.token_ids]
    quoted = _quoted_asks(s)
    if quoted and len(quoted) == len(asks) and any(a > q + 1e-9 for a, q in zip(asks, quoted)):
        return None
    fees = sum(taker_fee_per_share(a) for a in asks); cost = sum(asks) + fees
    probability = float(s.metadata.get("lock_probability", 1.0)) if len(asks) == 1 else 1.0
    edge = probability - cost if len(asks) == 1 else 1.0 - cost
    if edge < settings.actionable_min_edge:
        return None
    s.entry_cost = cost; s.edge = edge
    s.metadata["confirmed_asks"] = asks
    s.metadata["confirmed_sizes"] = [fresh[t].best_ask_size for t in s.token_ids]
    s.metadata["rest_confirmed_at"] = datetime.now(timezone.utc).isoformat()
    return s


async def settle_open_paper_trades():
    for row in store.open_directional():
        if row["detector"] in {"binary_buy_both", "neg_risk_underround", "nested_threshold_arb"}:
            continue
        m = await poly.market_by_id(str(row["market_id"]))
        if not m or not m.get("closed"):
            continue
        try:
            prices = json.loads(m.get("outcomePrices") or "[]") if isinstance(m.get("outcomePrices"), str) else (m.get("outcomePrices") or [])
            token_ids = json.loads(row.get("token_ids") or "[]")
            market_tokens = json.loads(m.get("clobTokenIds") or "[]") if isinstance(m.get("clobTokenIds"), str) else (m.get("clobTokenIds") or [])
            token = token_ids[0] if token_ids else None; idx = market_tokens.index(token) if token in market_tokens else -1
            if idx < 0 or idx >= len(prices): continue
            won = float(prices[idx]) > 0.99
            store.resolve(int(row["id"]), won, settings.paper_stake_usd)
            await tg.send(f"✅ Paper result #{row['id']}: <b>{'WON' if won else 'LOST'}</b> — {row['title']}")
        except Exception as exc:
            log.warning("settlement parse failed for %s: %s", row["id"], exc)


async def _wait_for_feeds(timeout: float) -> set[str]:
    pairs = {"market": market_stream.changed, "sports": sports_stream.changed, "crypto": crypto_stream.changed}
    tasks = {name: asyncio.create_task(ev.wait()) for name, ev in pairs.items()}
    done, pending = await asyncio.wait(tasks.values(), timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    flags = {name for name, task in tasks.items() if task in done}
    for task in pending: task.cancel()
    if pending: await asyncio.gather(*pending, return_exceptions=True)
    for name in flags: pairs[name].clear()
    return flags


async def _notify_started() -> None:
    if not tg.enabled:
        return
    try:
        await tg.send("🟢 <b>Polymarket Edge Scanner is online</b>\nLive feeds are starting. Use /stats or /help any time.")
    except Exception as exc:
        log.warning("Telegram startup notification failed: %s", exc)


async def scanner_loop():
    markets: list[Market] = []; weather_cache: dict[str, list] = {}
    last_universe = last_weather = last_macro = last_settle = last_watch = last_tg = 0.0
    while True:
        try:
            tick = time.time(); universe_refreshed = weather_refreshed = macro_refreshed = False
            if not markets or tick - last_universe >= settings.universe_refresh_seconds:
                markets = await poly.active_markets(); tokens = _all_tokens(markets)
                # Do NOT POST the entire ~20k-token universe to /books here.
                # Initial/full orderbooks arrive from the public market WebSocket;
                # subsequent universe changes are incremental WS subscriptions.
                await market_stream.configure(tokens)
                state["markets"] = len(markets); state["tokens"] = len(tokens); last_universe = tick; universe_refreshed = True
                log.info("universe refreshed: %d markets / %d tokens", len(markets), len(tokens))

            weather_markets = [m for m in markets if "highest temperature" in f"{m.event_title} {m.question}".lower()]
            stations = sorted({s for m in weather_markets if (s := station_from_market(m))}); state["stations"] = len(stations)
            if tick - last_weather >= settings.weather_refresh_seconds:
                weather_cache = {s: await weather.observations(s) for s in stations}; last_weather = tick; weather_refreshed = True
            if macro.enabled and tick - last_macro >= settings.macro_refresh_seconds:
                await macro.refresh(); last_macro = tick; macro_refreshed = True

            flags = {"fallback"}
            if not universe_refreshed:
                flags = await _wait_for_feeds(settings.scan_interval_seconds) or {"fallback"}
                if flags & {"market", "crypto"}:
                    await asyncio.sleep(settings.websocket_debounce_seconds); market_stream.changed.clear(); crypto_stream.changed.clear()

            books = market_stream.snapshot(); _apply_live_bbo(markets, books)
            signals: list[Signal] = []; fast_market = universe_refreshed or bool(flags & {"market", "fallback"})
            if fast_market:
                signals.extend(binary_buy_both(markets, books)); signals.extend(neg_risk_underround(markets, books)); signals.extend(nested_threshold_arbitrage(markets, books))
            if fast_market or weather_refreshed: signals.extend(weather_late_lock(weather_markets, books, weather_cache))
            if fast_market or "sports" in flags: signals.extend(sports_result_lag(markets, books, sports_stream.snapshot()))
            if fast_market or "crypto" in flags: signals.extend(crypto_resolution_lag(markets, books, crypto_stream))
            if fast_market or macro_refreshed: signals.extend(official_macro_release_lag(markets, books, macro))
            if universe_refreshed or tick - last_watch >= 60:
                signals.extend(crypto_crossfeed_divergence(markets, crypto_stream)); signals.extend(duplicate_divergence(markets)); signals.extend(wide_spread_watch(markets)); last_watch = tick

            signals.sort(key=lambda x: (x.confidence != "ACTIONABLE", -(x.edge or 0)))
            for s in signals:
                s = await confirm_actionable(s)
                if s is None: continue
                signal_id = store.save_signal(s)
                if signal_id is None: continue
                if s.confidence == "ACTIONABLE" and s.metadata.get("immediate_settlement"): store.settle_immediate(signal_id, settings.paper_stake_usd)
                await tg.send_signal(signal_id, s); log.info("alert %s %s edge=%s", signal_id, s.detector, s.edge)

            if tick - last_settle >= 120: await settle_open_paper_trades(); last_settle = tick
            if tick - last_tg >= 2: await tg.poll_commands(); last_tg = tick
            state["last_scan"] = time.time(); state["last_reason"] = sorted(flags); state["market_ws_workers"] = market_stream.connected_workers; state["sports_ws"] = sports_stream.connected; state["crypto_rtds"] = crypto_stream.connected; state["macro"] = macro.status(); state["last_error"] = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state["last_error"] = repr(exc); log.exception("scanner iteration failed"); await asyncio.sleep(2)


@app.on_event("startup")
async def startup():
    global runner_task
    await sports_stream.start(); await crypto_stream.start()
    if runner_task is None: runner_task = asyncio.create_task(scanner_loop())
    asyncio.create_task(_notify_started())


@app.on_event("shutdown")
async def shutdown():
    if runner_task:
        runner_task.cancel(); await asyncio.gather(runner_task, return_exceptions=True)
    await market_stream.close(); await sports_stream.close(); await crypto_stream.close(); await poly.close(); await weather.close(); await macro.close(); await tg.close()


@app.get("/health")
async def health(): return {"ok": state["last_error"] is None, **state}

@app.get("/stats")
async def stats(): return store.stats()

@app.get("/mystats")
async def mystats(): return store.manual_stats()

@app.get("/recent")
async def recent(): return store.recent(20)
