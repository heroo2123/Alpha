from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from fastapi import FastAPI

from polymarket_scanner.config import settings
from polymarket_scanner.detectors import binary_buy_both, duplicate_divergence, neg_risk_underround, weather_late_lock, wide_spread_watch
from polymarket_scanner.polymarket import PolymarketClient
from polymarket_scanner.store import Store
from polymarket_scanner.telegram import Telegram
from polymarket_scanner.weather import WeatherClient, station_from_market

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("polybot")

store = Store(settings.db_path)
poly = PolymarketClient()
weather = WeatherClient()
tg = Telegram(store)
app = FastAPI(title="Polymarket Edge Scanner", version="0.1.0")
state = {"started": time.time(), "last_scan": None, "markets": 0, "stations": 0, "last_error": None}
runner_task: asyncio.Task | None = None


async def settle_open_paper_trades():
    for row in store.open_directional():
        if row["detector"] in {"binary_buy_both", "neg_risk_underround"}:
            continue
        m = await poly.market_by_id(str(row["market_id"]))
        if not m or not m.get("closed"):
            continue
        try:
            prices = json.loads(m.get("outcomePrices") or "[]") if isinstance(m.get("outcomePrices"), str) else (m.get("outcomePrices") or [])
            token_ids = json.loads(row.get("token_ids") or "[]")
            market_tokens = json.loads(m.get("clobTokenIds") or "[]") if isinstance(m.get("clobTokenIds"), str) else (m.get("clobTokenIds") or [])
            token = token_ids[0] if token_ids else None
            idx = market_tokens.index(token) if token in market_tokens else 0
            won = idx < len(prices) and float(prices[idx]) > 0.99
            store.resolve(int(row["id"]), won, settings.paper_stake_usd)
            await tg.send(f"✅ Paper result #{row['id']}: <b>{'WON' if won else 'LOST'}</b> — {row['title']}")
        except Exception as exc:
            log.warning("settlement parse failed for %s: %s", row["id"], exc)


async def scanner_loop():
    markets = []
    weather_cache = {}
    last_universe = 0.0
    last_weather = 0.0
    last_settle = 0.0
    while True:
        tick = time.time()
        try:
            if not markets or tick - last_universe >= settings.universe_refresh_seconds:
                markets = await poly.active_markets()
                state["markets"] = len(markets)
                last_universe = tick
                log.info("universe refreshed: %d markets", len(markets))

            tokens = []
            for m in markets:
                if m.yes_token: tokens.append(m.yes_token)
                if m.no_token: tokens.append(m.no_token)
            books = await poly.books(tokens)

            weather_markets = [m for m in markets if "highest temperature" in f"{m.event_title} {m.question}".lower()]
            stations = sorted({s for m in weather_markets if (s := station_from_market(m))})
            state["stations"] = len(stations)
            if tick - last_weather >= settings.weather_refresh_seconds:
                weather_cache = {s: await weather.observations(s) for s in stations}
                last_weather = tick

            signals = []
            signals.extend(binary_buy_both(markets, books))
            signals.extend(neg_risk_underround(markets, books))
            signals.extend(weather_late_lock(weather_markets, books, weather_cache))
            signals.extend(duplicate_divergence(markets))
            signals.extend(wide_spread_watch(markets))

            signals.sort(key=lambda x: (x.confidence != "ACTIONABLE", -(x.edge or 0)))
            for s in signals:
                signal_id = store.save_signal(s)
                if signal_id is None:
                    continue
                if s.confidence == "ACTIONABLE" and s.metadata.get("immediate_settlement"):
                    store.settle_immediate(signal_id, settings.paper_stake_usd)
                await tg.send_signal(signal_id, s)
                log.info("alert %s %s edge=%s", signal_id, s.detector, s.edge)

            if tick - last_settle >= 180:
                await settle_open_paper_trades()
                last_settle = tick
            await tg.poll_commands()
            state["last_scan"] = time.time()
            state["last_error"] = None
        except Exception as exc:
            state["last_error"] = repr(exc)
            log.exception("scanner iteration failed")
        elapsed = time.time() - tick
        await asyncio.sleep(max(1.0, settings.scan_interval_seconds - elapsed))


@app.on_event("startup")
async def startup():
    global runner_task
    if runner_task is None:
        runner_task = asyncio.create_task(scanner_loop())


@app.on_event("shutdown")
async def shutdown():
    if runner_task:
        runner_task.cancel()
    await poly.close(); await weather.close(); await tg.close()


@app.get("/health")
async def health():
    return {"ok": state["last_error"] is None, **state}


@app.get("/stats")
async def stats():
    return store.stats()
