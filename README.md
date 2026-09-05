# Polymarket Edge Scanner

A continuous **signal + audit** bot for Polymarket. It does not place orders. It scans active markets, sends Telegram alerts when a calculable or suspicious pricing inefficiency appears, and paper-tracks every ACTIONABLE signal so the detector itself can be evaluated over time.

## Detectors in v0.1

### ACTIONABLE
1. **Binary buy-both arbitrage** — confirms CLOB asks for YES and NO, includes current taker-fee estimate, and alerts when total cost is materially below $1.
2. **Neg-risk event underround** — for Polymarket multi-outcome/negative-risk events, confirms the YES ask for every outcome and alerts when buying the entire exhaustive set is below $1 after estimated fees.
3. **Weather late-day lock** — discovers active “highest temperature” events, extracts the station from the market rules, reads worldwide METAR observations from the US Aviation Weather API, applies Polymarket/NOAA hourly-observation filtering, identifies the bracket containing the observed daily max, and compares a conservative lock-probability model with the executable ask.

### WATCH
4. **Potential duplicate-market divergence** — text-similar markets with materially different probabilities. These require manual rule verification and are never paper-entered automatically.
5. **Wide/liquid spread** — high-volume markets with unusually wide spreads that may offer maker/price-discovery opportunities. Not an arbitrage claim.

## Important weather caveat

Polymarket weather resolution rules often specify NOAA's WRH Time Series Viewer and its **Show Only Hourly Data** table. v0.1 uses AviationWeather.gov METAR data as a fast, worldwide observation proxy and copies the WRH hourly timestamp rule (US NWS/FAA: minutes 51–59; other platforms: 56–04). The Telegram alert explicitly tells you to verify the NOAA WRH table before a manual trade. This is intentional: an alert is not allowed to pretend the proxy is the final settlement source.

## Paper tracking

Every ACTIONABLE alert is inserted into SQLite at the visible executable cost. Structural arbitrages are marked won immediately on the assumption all displayed legs fill at the quoted asks. Directional weather signals remain open until Gamma reports the market closed, then the bot records win/loss and P&L using `PAPER_STAKE_USD`.

Telegram commands:

- `/stats` — total alerts, won/lost/open, paper P&L, and P&L by detector.
- `/recent` — last 10 alerts.
- `/help` — command reminder.

## Deploy

The repo includes `render.yaml`. A continuously running process is important; GitHub Actions cron is not suitable for immediate market monitoring. On Render, use the Starter web service in the blueprint (free services can sleep, which defeats constant observation). The blueprint also mounts a 1 GB persistent disk at `/var/data` so the SQLite audit history survives restarts/deploys.

Set these secrets/environment variables in Render:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Optional tuning variables are shown in `.env.example`.

Run locally:

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app:app --host 0.0.0.0 --port 8000
```

Health: `GET /health`  
Stats: `GET /stats`

## Why the architecture is modular

Detectors are pure functions in `polymarket_scanner/detectors.py`; market data, weather, storage, and Telegram are separate modules. That makes it straightforward to add future external-source detectors (sports result lag, crypto index divergence, election-count lag, official-statistics releases, etc.) while retaining one alerting/audit system.

## Safety / execution assumptions

The scanner is advisory and paper-trading only. “Locked” structural edge assumes all legs can actually be filled at the displayed size before the book changes. Weather probabilities are heuristic and should be calibrated from the stored results before sizing real money. Resolution rules always override generic market semantics.
