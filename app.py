from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import time
from datetime import datetime, timezone

from fastapi import FastAPI

from polymarket_scanner.config import settings
from polymarket_scanner.evaluator import evaluate_signals
from polymarket_scanner.hardening import MIN_VISIBLE_NOTIONAL_USD, archive_legacy_structural_stats
from polymarket_scanner.macro import MacroClient
from polymarket_scanner.models import Market, Signal
from polymarket_scanner.polymarket import PolymarketClient, taker_fee_per_share
from polymarket_scanner.store import Store
from polymarket_scanner.streams import CryptoRTDS, LiveMarketStream, SportsStream
from polymarket_scanner.telegram import Telegram
from polymarket_scanner.weather import WeatherClient, station_from_market

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("polybot")

store = Store(settings.db_path)
legacy_structural_archived = archive_legacy_structural_stats(settings.db_path)
if legacy_structural_archived:
    log.warning("Archived %d legacy structural signals whose paper wins assumed fills", legacy_structural_archived)
poly = PolymarketClient()
weather = WeatherClient()
macro = MacroClient()
market_stream = LiveMarketStream()
sports_stream = SportsStream()
crypto_stream = CryptoRTDS()
tg = Telegram(store)
app = FastAPI(title="Polymarket Edge Scanner", version="0.3.2")
state = {
    "started": time.time(), "last_scan": None, "markets": 0, "tokens": 0, "stations": 0,
    "weather_ready_stations": 0, "weather_forecast_ready_stations": 0, "weather_refreshing": False,
    "last_error": None, "universe_error": None,
    "market_ws_workers": 0, "sports_ws": False, "crypto_rtds": False,
    "macro": {}, "last_reason": None,
    "legacy_structural_archived": legacy_structural_archived,
    "telegram_watch_dropped": 0,
    "scan_in_progress": False,
    "last_compute_seconds": None,
    "last_universe_seconds": None,
    "universe_refreshing": False,
    "settlement_in_progress": False,
    "last_settlement_seconds": None,
    "signal_processing": False,
    "signal_batches_pending": 0,
}
runner_task: asyncio.Task | None = None
telegram_task: asyncio.Task | None = None
alert_task: asyncio.Task | None = None
health_snapshot_task: asyncio.Task | None = None
signal_task: asyncio.Task | None = None
alert_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
signal_queue: asyncio.Queue[list[Signal]] = asyncio.Queue()
alert_sequence = itertools.count()


def _all_tokens(markets: list[Market]) -> list[str]:
    return list(dict.fromkeys(t for m in markets for t in m.token_ids if t))


def _weather_universe(markets: list[Market]) -> tuple[list[Market], list[str]]:
    """Compute weather subset/stations off the uvicorn event loop."""
    weather_markets = [m for m in markets if "highest temperature" in f"{m.event_title} {m.question}".lower()]
    stations = sorted({s for m in weather_markets if (s := station_from_market(m))})
    return weather_markets, stations


def _quoted_asks(s: Signal) -> list[float]:
    if len(s.token_ids) == 1:
        try:
            return [float(s.metadata.get("ask"))]
        except (TypeError, ValueError):
            return []
    if s.detector in {"binary_buy_both", "nested_threshold_arb"}:
        try:
            return [float(s.metadata.get("yes_ask")), float(s.metadata.get("no_ask"))]
        except (TypeError, ValueError):
            return []
    if s.detector == "neg_risk_underround":
        try:
            return [float(x["ask"]) for x in s.metadata.get("legs", [])]
        except Exception:
            return []
    return []


async def _fetch_weather_batch(stations: list[str]) -> dict[str, list]:
    """Fetch station observations without blocking the main scanner loop."""
    sem = asyncio.Semaphore(8)

    async def one(station: str) -> tuple[str, list]:
        async with sem:
            return station, await weather.observations(station)

    rows = await asyncio.gather(*(one(s) for s in stations), return_exceptions=True)
    out: dict[str, list] = {}
    for row in rows:
        if isinstance(row, tuple):
            station, observations = row
            out[station] = observations
    return out


def _health_snapshot() -> dict:
    """Build the live status record shared with the standalone command worker."""
    return {
        "ok": state["last_error"] is None,
        **state,
        "snapshot_at": time.time(),
        "market_ws_workers": market_stream.connected_workers,
        "sports_ws": sports_stream.connected,
        "crypto_rtds": crypto_stream.connected,
        "market_ws_last_message": market_stream.last_message_at,
        "sports_ws_last_message": sports_stream.last_message_at,
        "crypto_rtds_last_message": crypto_stream.last_message_at,
        "telegram_alert_queue": alert_queue.qsize(),
        "telegram_command_mode": "in_app" if settings.telegram_commands_in_app else "external_service",
        "telegram_last_command_poll": tg.last_command_poll_at if settings.telegram_commands_in_app else None,
        "telegram_command_error": tg.last_command_error if settings.telegram_commands_in_app else None,
        "telegram_alert_error": tg.last_alert_error,
    }


async def health_snapshot_loop() -> None:
    """Persist scanner health without involving the scanner HTTP server."""
    while True:
        try:
            snapshot = _health_snapshot()
            payload = json.dumps(snapshot, separators=(",", ":"), default=str)
            await asyncio.to_thread(store.set_state, "scanner_health_snapshot", payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("health snapshot persistence failed: %r", exc)
        await asyncio.sleep(2.0)


async def confirm_actionable(s: Signal) -> Signal | None:
    """REST-confirm every ACTIONABLE leg immediately before alerting."""
    if s.confidence != "ACTIONABLE" or not s.token_ids:
        return s
    fresh = await poly.books(s.token_ids)
    if any(t not in fresh or fresh[t].best_ask is None or fresh[t].best_ask_size <= 0 for t in s.token_ids):
        return None
    asks = [float(fresh[t].best_ask) for t in s.token_ids]
    quoted = _quoted_asks(s)
    if quoted and len(quoted) == len(asks) and any(a > q + 1e-9 for a, q in zip(asks, quoted)):
        return None
    fees = sum(taker_fee_per_share(a) for a in asks)
    cost = sum(asks) + fees
    probability = float(s.metadata.get("lock_probability", 1.0)) if len(asks) == 1 else 1.0
    edge = probability - cost if len(asks) == 1 else 1.0 - cost
    if edge < settings.actionable_min_edge:
        return None

    sizes = [float(fresh[t].best_ask_size) for t in s.token_ids]
    common_shares = min(sizes)
    max_visible_notional = common_shares * cost
    if max_visible_notional < MIN_VISIBLE_NOTIONAL_USD:
        log.info("skip actionable %s: only $%.2f simultaneously visible at confirmed asks", s.detector, max_visible_notional)
        return None

    s.entry_cost = cost
    s.edge = edge
    s.metadata["confirmed_asks"] = asks
    s.metadata["confirmed_sizes"] = sizes
    s.metadata["visible_common_shares"] = common_shares
    s.metadata["max_visible_notional_usd"] = max_visible_notional
    s.metadata["rest_confirmed_at"] = datetime.now(timezone.utc).isoformat()
    return s


def enqueue_alert(signal_id: int, s: Signal) -> bool:
    """Queue Telegram delivery without ever blocking the scanner."""
    if not tg.enabled:
        return False
    if s.confidence != "ACTIONABLE" and alert_queue.qsize() >= settings.telegram_watch_backlog_limit:
        state["telegram_watch_dropped"] = int(state.get("telegram_watch_dropped") or 0) + 1
        log.warning("Telegram WATCH backlog full; suppressing delivery of signal %s (%s)", signal_id, s.detector)
        return False
    priority = 0 if s.confidence == "ACTIONABLE" else 10
    alert_queue.put_nowait((priority, next(alert_sequence), signal_id, s))
    return True


async def telegram_alert_loop() -> None:
    """Dedicated rate-limited signal sender, independent from command handling."""
    last_sent = 0.0
    while True:
        priority, _seq, signal_id, s = await alert_queue.get()
        try:
            interval = (
                settings.telegram_actionable_min_interval_seconds
                if priority == 0
                else settings.telegram_watch_min_interval_seconds
            )
            wait_for = interval - (time.monotonic() - last_sent)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            await tg.send_signal(signal_id, s)
            last_sent = time.monotonic()
            log.info("delivered Telegram alert %s %s edge=%s", signal_id, s.detector, s.edge)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Telegram alert delivery failed for %s: %r", signal_id, exc)
        finally:
            alert_queue.task_done()


async def signal_processing_loop() -> None:
    """Verify/persist detector output away from the time-critical scanner loop.

    ACTIONABLE candidates are never intentionally dropped. WATCH candidates are
    pre-trimmed by the scanner because they are research leads, not trades.
    """
    while True:
        batch = await signal_queue.get()
        state["signal_batches_pending"] = signal_queue.qsize()
        state["signal_processing"] = True
        try:
            watch_queued = 0
            for s in batch:
                try:
                    s = await confirm_actionable(s)
                    if s is None:
                        continue
                    signal_id = await asyncio.to_thread(store.save_signal, s)
                    if signal_id is None:
                        continue
                    if s.confidence != "ACTIONABLE" and watch_queued >= settings.telegram_watch_per_scan_limit:
                        state["telegram_watch_dropped"] = int(state.get("telegram_watch_dropped") or 0) + 1
                        queued = False
                    else:
                        queued = enqueue_alert(signal_id, s)
                        if queued and s.confidence != "ACTIONABLE":
                            watch_queued += 1
                    log.info("saved signal %s %s edge=%s telegram_queued=%s", signal_id, s.detector, s.edge, queued)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning("signal post-processing failed for %s: %r", getattr(s, "detector", "unknown"), exc)
        finally:
            state["signal_processing"] = False
            signal_queue.task_done()
            state["signal_batches_pending"] = signal_queue.qsize()


def queue_detector_output(signals: list[Signal]) -> None:
    """Keep every ACTIONABLE candidate; retain only strongest WATCH leads per pass."""
    ordered = sorted(
        signals,
        key=lambda s: (0 if s.confidence == "ACTIONABLE" else 1, -(float(s.edge) if s.edge is not None else -1.0)),
    )
    actionables = [s for s in ordered if s.confidence == "ACTIONABLE"]
    watches = [s for s in ordered if s.confidence != "ACTIONABLE"]
    watch_keep = max(settings.telegram_watch_per_scan_limit, settings.telegram_watch_per_scan_limit * 2)
    kept_watches = watches[:watch_keep]
    dropped = max(0, len(watches) - len(kept_watches))
    if dropped:
        state["telegram_watch_dropped"] = int(state.get("telegram_watch_dropped") or 0) + dropped
    batch = actionables + kept_watches
    if batch:
        signal_queue.put_nowait(batch)
        state["signal_batches_pending"] = signal_queue.qsize()


async def settle_open_paper_trades():
    """Resolve closed directional paper signals without serial network latency."""
    rows = [
        row for row in await asyncio.to_thread(store.open_directional)
        if row["detector"] not in {"binary_buy_both", "neg_risk_underround", "nested_threshold_arb"}
    ]
    if not rows:
        return

    sem = asyncio.Semaphore(8)

    async def fetch(row: dict):
        async with sem:
            return row, await poly.market_by_id(str(row["market_id"]))

    results = await asyncio.gather(*(fetch(row) for row in rows), return_exceptions=True)
    for result in results:
        if not isinstance(result, tuple):
            continue
        row, m = result
        if not m or not m.get("closed"):
            continue
        try:
            prices = json.loads(m.get("outcomePrices") or "[]") if isinstance(m.get("outcomePrices"), str) else (m.get("outcomePrices") or [])
            token_ids = json.loads(row.get("token_ids") or "[]")
            market_tokens = json.loads(m.get("clobTokenIds") or "[]") if isinstance(m.get("clobTokenIds"), str) else (m.get("clobTokenIds") or [])
            token = token_ids[0] if token_ids else None
            idx = market_tokens.index(token) if token in market_tokens else -1
            if idx < 0 or idx >= len(prices):
                continue
            won = float(prices[idx]) > 0.99
            await asyncio.to_thread(store.resolve, int(row["id"]), won, settings.paper_stake_usd)
            try:
                await tg.send(f"✅ Paper result #{row['id']}: <b>{'WON' if won else 'LOST'}</b> — {row['title']}")
            except Exception as exc:
                log.warning("paper-result Telegram notification failed for %s: %r", row["id"], exc)
        except Exception as exc:
            log.warning("settlement parse failed for %s: %s", row["id"], exc)


async def _wait_for_feeds(timeout: float) -> set[str]:
    pairs = {"market": market_stream.changed, "sports": sports_stream.changed, "crypto": crypto_stream.changed}
    tasks = {name: asyncio.create_task(ev.wait()) for name, ev in pairs.items()}
    done, pending = await asyncio.wait(tasks.values(), timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    flags = {name for name, task in tasks.items() if task in done}
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    for name in flags:
        pairs[name].clear()
    return flags


async def _notify_started() -> None:
    if not tg.enabled:
        return
    try:
        await tg.send("🟢 <b>Polymarket Edge Scanner is online</b>\nLive feeds are starting. Use /status, /stats or /help any time.")
    except Exception as exc:
        log.warning("Telegram startup notification failed: %s", exc)


async def telegram_command_loop() -> None:
    """Single-process fallback command loop."""
    while True:
        try:
            await tg.poll_commands()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Telegram command poll failed: %s", exc)
        await asyncio.sleep(0.25)


async def _prepare_universe(refreshed_markets: list[Market]) -> tuple[list[Market], list[str], list[Market], list[str]]:
    if not refreshed_markets:
        raise RuntimeError("Gamma discovery returned an empty active universe")
    tokens = await asyncio.to_thread(_all_tokens, refreshed_markets)
    weather_markets, stations = await asyncio.to_thread(_weather_universe, refreshed_markets)
    return refreshed_markets, tokens, weather_markets, stations


async def scanner_loop():
    markets: list[Market] = []
    weather_markets: list[Market] = []
    stations: list[str] = []
    weather_cache: dict[str, list] = {}
    weather_task: asyncio.Task | None = None
    settlement_task: asyncio.Task | None = None
    universe_task: asyncio.Task | None = None
    settlement_started = 0.0
    universe_started = 0.0
    last_universe = last_weather = last_macro = last_settle = last_watch = 0.0

    while True:
        try:
            tick = time.time()
            universe_refreshed = weather_refreshed = macro_refreshed = False

            if weather_task is not None and weather_task.done():
                try:
                    weather_cache = weather_task.result()
                    state["weather_ready_stations"] = sum(bool(v) for v in weather_cache.values())
                    state["weather_forecast_ready_stations"] = sum(
                        bool(v) and getattr(v, "forecast", None) is not None for v in weather_cache.values()
                    )
                    weather_refreshed = True
                except Exception as exc:
                    log.warning("weather batch refresh failed: %s", exc)
                weather_task = None
                state["weather_refreshing"] = False

            if settlement_task is not None and settlement_task.done():
                try:
                    settlement_task.result()
                except Exception as exc:
                    log.warning("background settlement pass failed: %r", exc)
                state["settlement_in_progress"] = False
                state["last_settlement_seconds"] = round(time.time() - settlement_started, 3)
                settlement_task = None

            # Initial universe must exist before scanning. Every later Gamma refresh
            # runs in the background so discovery latency cannot freeze live scans.
            if not markets:
                universe_started = time.time()
                refreshed_markets = await poly.active_markets()
                markets, tokens, weather_markets, stations = await _prepare_universe(refreshed_markets)
                await market_stream.configure(tokens)
                state["markets"] = len(markets)
                state["tokens"] = len(tokens)
                state["stations"] = len(stations)
                last_universe = tick
                universe_refreshed = True
                state["universe_error"] = None
                state["last_universe_seconds"] = round(time.time() - universe_started, 3)
                log.info("initial universe loaded: %d markets / %d tokens / %d weather stations", len(markets), len(tokens), len(stations))

            if universe_task is not None and universe_task.done():
                state["universe_refreshing"] = False
                try:
                    refreshed_markets = universe_task.result()
                    prepared_markets, tokens, prepared_weather, prepared_stations = await _prepare_universe(refreshed_markets)
                except Exception as exc:
                    state["universe_error"] = repr(exc)
                    log.warning("background universe refresh failed; keeping %d existing markets: %r", len(markets), exc)
                else:
                    markets = prepared_markets
                    weather_markets = prepared_weather
                    stations = prepared_stations
                    await market_stream.configure(tokens)
                    state["markets"] = len(markets)
                    state["tokens"] = len(tokens)
                    state["stations"] = len(stations)
                    state["universe_error"] = None
                    universe_refreshed = True
                    log.info("background universe refresh applied: %d markets / %d tokens / %d weather stations", len(markets), len(tokens), len(stations))
                finally:
                    state["last_universe_seconds"] = round(time.time() - universe_started, 3)
                    universe_task = None

            if universe_task is None and tick - last_universe >= settings.universe_refresh_seconds:
                universe_started = time.time()
                universe_task = asyncio.create_task(poly.active_markets())
                last_universe = tick
                state["universe_refreshing"] = True

            if weather_task is None and (not weather_cache or tick - last_weather >= settings.weather_refresh_seconds):
                weather_task = asyncio.create_task(_fetch_weather_batch(stations))
                last_weather = tick
                state["weather_refreshing"] = True

            if macro.enabled and tick - last_macro >= settings.macro_refresh_seconds:
                await macro.refresh()
                last_macro = tick
                macro_refreshed = True

            flags = {"fallback"}
            if not universe_refreshed:
                flags = await _wait_for_feeds(settings.scan_interval_seconds) or {"fallback"}
                if flags & {"market", "crypto"}:
                    await asyncio.sleep(settings.websocket_debounce_seconds)
                    market_stream.changed.clear()
                    crypto_stream.changed.clear()

            books = market_stream.snapshot()
            fast_market = universe_refreshed or bool(flags & {"market", "fallback"})
            run_watch = universe_refreshed or tick - last_watch >= 60
            sports_cache = sports_stream.snapshot()

            compute_started = time.time()
            state["scan_in_progress"] = True
            try:
                signals = await asyncio.to_thread(
                    evaluate_signals,
                    markets,
                    books,
                    weather_markets,
                    weather_cache,
                    sports_cache,
                    crypto_stream,
                    macro,
                    fast_market=fast_market,
                    weather_refreshed=weather_refreshed,
                    sports_trigger="sports" in flags,
                    crypto_trigger="crypto" in flags,
                    macro_refreshed=macro_refreshed,
                    run_watch=run_watch,
                )
            finally:
                state["scan_in_progress"] = False
                state["last_compute_seconds"] = round(time.time() - compute_started, 3)

            if run_watch:
                last_watch = tick

            # Detector completion is the scanner heartbeat. Verification, storage,
            # and Telegram delivery continue independently in their own workers.
            queue_detector_output(signals)
            state["last_scan"] = time.time()
            state["last_reason"] = sorted(flags)
            state["market_ws_workers"] = market_stream.connected_workers
            state["sports_ws"] = sports_stream.connected
            state["crypto_rtds"] = crypto_stream.connected
            state["macro"] = macro.status()
            state["last_error"] = None

            if settlement_task is None and tick - last_settle >= 120:
                settlement_started = time.time()
                settlement_task = asyncio.create_task(settle_open_paper_trades())
                state["settlement_in_progress"] = True
                last_settle = tick

        except asyncio.CancelledError:
            state["scan_in_progress"] = False
            for task in (weather_task, settlement_task, universe_task):
                if task is not None:
                    task.cancel()
            await asyncio.gather(
                *(t for t in (weather_task, settlement_task, universe_task) if t is not None),
                return_exceptions=True,
            )
            raise
        except Exception as exc:
            state["scan_in_progress"] = False
            state["last_error"] = repr(exc)
            log.exception("scanner iteration failed")
            await asyncio.sleep(2)


@app.on_event("startup")
async def startup():
    global runner_task, telegram_task, alert_task, health_snapshot_task, signal_task
    await sports_stream.start()
    await crypto_stream.start()
    if runner_task is None:
        runner_task = asyncio.create_task(scanner_loop())
    if settings.telegram_commands_in_app and telegram_task is None:
        telegram_task = asyncio.create_task(telegram_command_loop())
    elif not settings.telegram_commands_in_app:
        log.info("in-process Telegram command polling disabled; external command worker owns getUpdates")
    if alert_task is None:
        alert_task = asyncio.create_task(telegram_alert_loop())
    if signal_task is None:
        signal_task = asyncio.create_task(signal_processing_loop())
    if health_snapshot_task is None:
        health_snapshot_task = asyncio.create_task(health_snapshot_loop())
    asyncio.create_task(_notify_started())


@app.on_event("shutdown")
async def shutdown():
    for task in (health_snapshot_task, signal_task, alert_task, telegram_task, runner_task):
        if task:
            task.cancel()
    tasks = [t for t in (health_snapshot_task, signal_task, alert_task, telegram_task, runner_task) if t]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    await market_stream.close()
    await sports_stream.close()
    await crypto_stream.close()
    await poly.close()
    await weather.close()
    await macro.close()
    await tg.close()


@app.get("/health")
async def health():
    return _health_snapshot()


@app.get("/stats")
async def stats():
    return await asyncio.to_thread(store.stats)


@app.get("/mystats")
async def mystats():
    return await asyncio.to_thread(store.manual_stats)


@app.get("/recent")
async def recent():
    return await asyncio.to_thread(store.recent, 20)
