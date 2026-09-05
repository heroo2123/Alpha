# Polymarket Edge Scanner v0.2

Continuous **signal + audit** scanner for Polymarket. It does **not** place orders. It watches the market and external/official resolution inputs, sends Telegram alerts with exact manual actions and direct market buttons, and paper-tracks ACTIONABLE signals so every detector can be evaluated from real outcomes.

## What changed in v0.2

- **Real-time Polymarket market WebSocket** for book/BBO changes. REST is still used immediately before an ACTIONABLE alert to confirm the quoted asks and displayed size.
- **Polymarket sports WebSocket** for live/final score state.
- **Polymarket RTDS crypto feeds** for resolution-aligned crypto boundary checks plus cross-feed divergence watches.
- **Logical threshold arbitrage** between nested conditions in the same event.
- **Official BLS release lag** support for CPI/core CPI, unemployment and payroll markets when a BLS API key is configured.
- Telegram alerts now contain a numbered **WHAT TO DO** section, a clear **SKIP/CHECK** rule and inline buttons that open the exact Polymarket market/event.
- `/took ALERT_ID STAKE_USD` records trades you actually took, so `/mystats` measures your own results separately from the paper model.
- `/whoami` makes Telegram chat-ID setup possible without third-party ID bots.

## Detector tiers

### ACTIONABLE

1. **Binary buy-both**: YES + NO asks are below $1 after a conservative fee estimate.
2. **Neg-risk underround**: the complete exhaustive YES basket is below $1 after fees.
3. **Nested-threshold arbitrage**: buy the looser condition YES plus the stricter condition NO when the logical pair costs below $1.
4. **Weather late-day lock**: exact station/rules discovery + hourly-observation trend + executable bucket ask. Fast METAR data is a proxy; the alert tells you to verify the official table before execution.
5. **Sports result lag**: Polymarket sports feed reports the event ended, the result implies a specific outcome, but that outcome remains materially below $1. Official result/rules verification is required.
6. **Crypto resolution lag**: only when the market's resolution-source text can be matched to a supported Polymarket RTDS feed and a boundary tick is captured.
7. **Official BLS release lag**: only for markets whose rule/source text references BLS and only after the matching official series value is available.

### WATCH

8. **Duplicate-market divergence**: similar contracts trading far apart; compare Rules before acting.
9. **Crypto cross-feed divergence**: Chainlink/reference vs Binance-backed feed disagreement; useful for finding traders looking at the wrong source.
10. **Wide/liquid spread**: surfaces maker/price-discovery opportunities without calling them arbitrage.

## Telegram alert format

An ACTIONABLE alert contains:

- detector + alert number;
- current estimated edge;
- exact quoted ask(s);
- **WHAT TO DO** numbered steps;
- a **CHECK / SKIP RULE** explaining when not to chase the alert;
- `OPEN MARKET` / `OPEN EVENT` buttons;
- `/took <alert_id> <stake>` shortcut for recording a real trade.

Structural arbitrage alerts explicitly say to use the same number of shares on every required leg and never execute only one leg.

## Telegram commands

- `/whoami` — returns your numeric chat ID during setup.
- `/stats` — paper-model detector stats.
- `/mystats` — results for trades you explicitly recorded with `/took`.
- `/recent` — recent scanner alerts.
- `/taken` — recent manually recorded trades.
- `/took ALERT_ID STAKE_USD` — record that you acted on an alert.
- `/help` — command list.

## Deploy on Render

`render.yaml` is included. Use an always-on Starter service: sleeping services can miss short-lived opportunities. The blueprint mounts `/var/data` so SQLite history survives restarts.

For the **first deploy**, the blueprint only asks for:

- `TELEGRAM_BOT_TOKEN`

After the service is running, open your Telegram bot and send `/whoami`. The bot replies with your numeric ID. In Render, add:

- `TELEGRAM_CHAT_ID` = that exact number

Then restart/redeploy the service. This avoids using third-party chat-ID bots or putting your bot token into a browser URL.

Optional:

- `BLS_API_KEY` enables official BLS release monitoring. Add it later in Render Environment if you want that detector; the rest of the scanner works without it.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app:app --host 0.0.0.0 --port 8000
```

Health: `GET /health`  
Paper stats: `GET /stats`  
Your recorded-trade stats: `GET /mystats`

## Important execution assumptions

The scanner is advisory. “ACTIONABLE” means the detector found a mechanically defined condition and then re-checked the candidate order book through REST; it does not mean risk-free or guaranteed profit. Structural arbitrage still requires all legs to fill before the book changes. Known-result detectors still require the market's settlement Rules/source to match the observed official input. Do not chase a price above the alert's quoted limit.
