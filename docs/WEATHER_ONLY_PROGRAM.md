# Weather-Only Trade Scanner Program

Branch: `weather-only-trade-scanner`
Baseline: `077b09e832600cd8c8e83cd97cbcf09becd73243`
Status: architecture/opportunity map only; no deployment and no real-money promotion.
Date: 2026-09-11.

## 1. Product definition

Build a focused Polymarket weather scanner for **manual execution**. It should continuously monitor supported weather markets and notify only when there is a specific, current, realistically executable trade or limit order that has passed contract, data, price, fee, capacity and freshness checks.

Two user-facing actions are allowed:

- `🚨 WEATHER TRADE NOW` — an immediate taker-style opportunity with exact side/legs, executable price, maximum acceptable price, visible size, fees, conservative edge/payout, direct link and skip conditions.
- `🎯 WEATHER LIMIT BID` — a passive-maker opportunity with exact market/side, bid price, maximum bid, desired size, expected value basis and cancel/reprice conditions. A resting bid is **not** described as a filled trade or guaranteed profit.

No automatic order placement is part of this phase. Telegram financial delivery remains disabled until a later explicit promotion stage.

## 2. Scope

### Included

Weather/climate contracts where the bot can mechanically identify the settlement rule and obtain a sufficiently authoritative data/model source:

1. Daily highest-temperature buckets.
2. Daily lowest-temperature buckets.
3. Daily measurable-rain binary markets.
4. Monthly precipitation buckets.
5. Drought / official drought-release markets.
6. Hurricane / tropical-cyclone formation, count and landfall markets when an NHC/official adapter exists.
7. River/lake level markets when an official gauge/source adapter exists.
8. Global/record temperature releases when the named official dataset can be parsed deterministically.
9. Future weather families only after a source-specific contract adapter is added and tested.

### Excluded by default

Items that may appear under a broad Polymarket Weather/Climate label but are not meteorological/hydrological opportunities for this product, such as earthquakes, volcanoes, asteroids, pandemics and unrelated science markets.

## 3. Current public-market snapshot motivating the design

This is a dated discovery snapshot, not a frozen universe contract. On 2026-09-11 Polymarket's public Weather page exposed roughly 353 weather cards, with temperature dominating (roughly 271), plus precipitation and drought categories. Public pages also showed high-temperature, low-temperature, daily rain, monthly precipitation, drought, hurricane and global-temperature products.

Useful current examples:

- Daily temperature example: `Highest temperature in Beijing on September 11?`
  - Primary resolution: NOAA/NWS WRH time series for station ZBAA.
  - Whole-degree Celsius precision.
  - The rules explicitly allow a Weather Underground fallback if NOAA is unavailable by a deadline.
  - Revisions are considered until the first following-day datapoint is published.
- Daily rain example: `Where will it rain on September 11?`
  - Binary per city.
  - Measurable precipitation is >= 0.01 inches; trace (`T`) does not count.
  - Resolution is the NWS Daily Climate Report (CLI), with a stated publication/revision cutoff.
- Monthly precipitation example: `Precipitation in Seoul in September?`
  - Multi-bucket monthly accumulation.
  - Primary resolution is Korea Meteorological Administration monthly precipitation for Seoul.
  - The rules include a later fallback-to-another-credible-source clause.

Sources consulted for this architecture:

- https://polymarket.com/weather
- https://polymarket.com/weather/precipitation
- https://polymarket.com/weather/drought
- https://polymarket.com/event/highest-temperature-in-beijing-on-september-11-2026
- https://polymarket.com/event/where-will-it-rain-on-september-11-2026
- https://polymarket.com/event/precipitation-in-seoul-in-september
- https://docs.polymarket.com/market-data/overview
- https://docs.polymarket.com/api-reference/events/list-events-keyset-pagination
- https://docs.polymarket.com/api-reference/rate-limits

A key implication: the existing `NWS_WRH_PRIMARY_ONLY_V4_EXACT_DATE` adapter is too strict for many current daily-temperature markets because current rules explicitly contain a Weather Underground fallback. The weather-only product must model the actual **resolution decision tree**, not simply reject every contract containing a fallback.

## 4. Opportunity map

Each lane below is a separate strategy and must keep its own evidence, calibration and promotion status.

### WX1 — Complete-set underround

For an event proven to contain mutually exclusive and collectively exhaustive outcomes, fetch exact executable asks for every winning-side outcome. If one share of each outcome can be bought for less than the certain $1 payout after fees and slippage, surface a basket.

Required proof:

- exact event membership;
- all active settlement outcomes included, including tails/other where applicable;
- exactly one winning outcome under the rules;
- no unresolved cancellation/refund semantic that breaks the $1 bundle claim;
- exact CLOB asks and simultaneous visible common size;
- total taker cost + fees < $1 by a configured safety margin.

Output: `🚨 WEATHER TRADE NOW — COMPLETE SET`.

This lane is deterministic/arbitrage-like only when the semantic proof succeeds. It must never infer exhaustiveness from similar-looking bucket text alone.

### WX2 — Official-result / resolution lag

When the rule-selected official source has already made the outcome mechanically knowable but the market still trades away from settlement value, buy the correct side if enough payout remains.

Examples:

- final/controlling temperature reading after the contract's revision window has closed;
- final NWS CLI precipitation figure before Polymarket resolution;
- published drought release matching the contract;
- official NHC bulletin satisfying a hurricane-formation condition;
- finalized monthly precipitation or official climate release.

Required proof:

- exact contract adapter;
- exact source identity;
- exact controlling publication/version and cutoff;
- no remaining rule branch that could change the winner;
- fresh exact order book and capacity.

Output: `🚨 WEATHER TRADE NOW — OFFICIAL RESULT LAG`.

This is the highest-priority lane because it can rely on settlement evidence rather than a probabilistic forecast.

### WX3 — Late-day temperature lock

Use the current day's official/proxy observations, local time, remaining forecast envelope and rule precision to identify a temperature bucket whose chance of being displaced has become very small while its YES price still leaves meaningful payout.

This generalizes the existing `weather_late_lock` and `weather_friend_lock` work.

Required before promotion:

- exact contract decision tree including fallback and revision window;
- source parity evidence or an explicitly conservative proxy treatment;
- calibrated probability or a deterministic lower-bound method;
- latest observation/forecast freshness;
- exact bucket precision/rounding;
- exact CLOB price, fee and visible size.

Output after calibration: `🚨 WEATHER TRADE NOW — LATE LOCK`.

Until calibrated, it remains shadow evidence only.

### WX4 — Forecast-value mispricing

Estimate a distribution over final settlement outcomes and compare conservative probability with executable market prices.

Candidate inputs:

- official point forecasts;
- ensemble/NWP forecasts where licensing/access permits;
- observed temperature/precipitation so far;
- time remaining in the settlement day/month;
- station-specific forecast error and bias;
- cloud, wind, precipitation, front timing and diurnal state;
- rule precision and observation schedule.

For each bucket/side compute a conservative `p_lower`, not only a point probability. A trade can be promoted only if:

`p_lower - executable_cost_after_fees >= required_edge`

and model calibration for that contract family/station/horizon is acceptable.

Output: `🚨 WEATHER TRADE NOW — FORECAST EDGE`.

### WX5 — Passive value / liquidity-vacuum bid

This is the user's friend's style in its simplest form. Rather than crossing the ask, place a resting bid materially below conservative fair value in a thin bucket and wait for transient sellers or book vacuums.

The scanner must calculate:

- conservative fair-value interval;
- current spread/depth;
- proposed limit price;
- maximum bid that preserves required expected edge;
- suggested size from available bankroll/risk rules;
- expected expiry/cancel conditions;
- whether the order is still valid after a new observation/forecast/book update.

Output: `🎯 WEATHER LIMIT BID`.

A bid is never counted as profit or exposure unless an actual fill is recorded by the user or later connected account data.

### WX6 — Passive complete-set accumulation

Track hypothetical/actual fills across all exhaustive weather buckets. Cheap fills on different buckets can gradually assemble complete one-share sets.

For each event keep fill lots by outcome. The engine separates:

- **complete covered bundles** — one share of every exhaustive outcome;
- **directional remainder** — unmatched extra shares in individual outcomes.

Only the covered portion can be labelled structurally locked. For each newly completed bundle, calculate the actual acquisition cost of the component fill lots plus fees. If that cost is below the certain payout, report locked profit.

This directly tests the claim that many very cheap bucket fills can create a low-cost complete set. A large nominal share count alone is never treated as a $1-per-share guaranteed portfolio; losing buckets settle at zero unless they are part of a complete exhaustive bundle.

Output examples:

- `🎯 WEATHER LIMIT BID — COMPLETE-SET BUILD`
- `✅ WEATHER BUNDLE LOCKED — $X cost -> $Y certain payout before residual risks`

### WX7 — Cross-market logical inconsistency

Search weather contracts for mechanically provable relationships, for example:

- duplicate/equivalent contracts using the same settlement fact;
- nested thresholds (`>= X` must be at least as probable as `>= Y` for Y > X);
- complementary partitions;
- Celsius/Fahrenheit-equivalent thresholds only when settlement sources, dates, precision and conversion rules are identical;
- same official release represented in multiple markets.

Trade only if the logical relation is formally proven from rules and exact executable prices create an exploitable inequality after fees.

Output: `🚨 WEATHER TRADE NOW — LOGICAL ARB`.

### WX8 — Source-update repricing lag

On a new official observation, CLI, NHC advisory, drought release or gauge update, re-evaluate only affected contracts immediately. If the new data changes fair/settlement value materially and stale executable orders remain, surface them.

This is not HFT: the alert must remain valid long enough for manual execution. Candidates that disappear before confirmation are retained as latency evidence, not sent.

Output: `🚨 WEATHER TRADE NOW — DATA UPDATE`.

### WX9 — Spread / maker-rebate style opportunities

When the conservative fair-value interval is much narrower than the live spread, propose a bid/ask-improving passive order that preserves expected value. Because manual fill probability and adverse selection matter, this remains maker research until fill/markout statistics show positive realized expectancy.

Output: `🎯 WEATHER LIMIT BID — SPREAD CAPTURE`.

## 5. Market-family adapter registry

No weather market becomes trade-eligible merely because its title contains a weather word. Every supported family gets a versioned contract adapter.

Minimum adapter interface:

- `matches(event, market)`
- `parse_target_interval()`
- `parse_outcome_geometry()`
- `parse_unit_precision_rounding()`
- `parse_primary_source()`
- `parse_fallback_tree()`
- `parse_revision_cutoff()`
- `resolve_from_official_state()`
- `eligible_for_probabilistic_model()`
- `exhaustive_event_proof()`

Initial adapters, in priority order:

1. `DAILY_TEMP_NWS_WRH_WITH_WU_FALLBACK_V1`
   - high and low temperature;
   - NWS/WRH primary;
   - Weather Underground fallback explicitly modelled;
   - following-day datapoint / deadline revision cutoff;
   - whole °F/°C precision.
2. `DAILY_RAIN_NWS_CLI_V1`
   - city/station-specific CLI;
   - >= 0.01 inch yes, trace does not count;
   - local standard-time climate day;
   - last valid CLI version before rules cutoff controls.
3. `MONTHLY_PRECIP_SOURCE_SPECIFIC_V1`
   - one adapter per named authority (e.g. KMA Seoul) rather than a generic scraper;
   - exact bracket boundary policy and decimal precision.
4. `US_DROUGHT_MONITOR_RELEASE_V1`
   - official release date/version and geographic semantics.
5. `NHC_TROPICAL_CYCLONE_V1`
   - exact basin, formation/landfall/category definition and advisory authority.
6. `HYDRO_GAUGE_SOURCE_V1`
   - named official river/lake gauge, timezone, revision/publication rules.
7. `GLOBAL_TEMP_RELEASE_V1`
   - source-specific monthly/global dataset and release version.

Unsupported rule families remain visible in diagnostics but cannot produce financial alerts.

## 6. Weather-only architecture for e2-micro

The general Astra architecture failed because it continuously handled ~200k discovered markets, ~14k materialized detector candidates and unrelated detector families. Weather-only should not inherit that cost.

### Process A — `weather_scanner`

One Python process owns:

- narrow weather catalog discovery;
- contract adapter classification;
- CLOB market data for weather tokens;
- official weather-source watchers;
- incremental detectors;
- shadow evidence and manual alert preparation;
- local health endpoint.

There is **no complete 200k-market Gamma walk** in the steady-state weather runtime.

Discovery order:

1. resolve the current Weather/Daily Temperature/Precipitation/etc. tag/category identifiers from Gamma;
2. query only those tagged/series events using supported Gamma filters;
3. cross-check title/category/rules with weather adapter matching;
4. deduplicate event/market IDs;
5. periodically run a low-frequency public-search/category sanity query to detect taxonomy drift;
6. fail individual unsupported contracts closed rather than fail the whole scanner.

Polymarket documentation explicitly supports event discovery by tag and public market-data endpoints, so weather-only discovery can be bounded without traversing every active event.

### Process B — `weather_command`

Keep Telegram command polling/status separate if desired. Financial alert enqueue stays independently gated. This process may be removed later if commands are not useful, saving memory.

### State

Use the existing accounting database for signals/manual fills, plus weather-specific tables for:

- contract registry and adapter version;
- source observations/publications;
- model forecasts and calibration lineage;
- order-book snapshots relevant to candidate decisions;
- maker virtual/actual orders and fill lots;
- complete-set inventory accounting;
- candidate lifecycle and rejection reason.

Do not store the broad general-market universe.

### Market-data tiers

To cover all weather contracts without overloading the VM:

- **Hot:** same-day temperature/rain, official-release-imminent contracts, active maker candidates. Subscribe to websocket tokens, bounded hot set.
- **Warm:** near-term precipitation/drought/hurricane/hydrology. Batch CLOB books every 15-60 seconds.
- **Cold:** long-dated climate contracts. Refresh metadata/books every few minutes unless a source event or price movement promotes them to warm/hot.

Every `TRADE NOW` candidate is rebuilt from fresh exact CLOB order books immediately before send. Gamma prices are discovery/screening only.

### Incremental computation

Do not rescan every weather market on every tick.

Triggers:

- CLOB book changed -> recompute that event's structural/value/maker lanes.
- new weather observation -> recompute contracts attached to that station.
- new official release -> recompute contracts attached to that release.
- forecast refresh -> recompute only affected station/date contracts.
- catalog change -> add/remove only changed contracts.

This should eliminate the general scanner's 36.986-second all-detector pass.

## 7. Conservative probability and calibration policy

Deterministic lanes (complete-set semantic arbitrage and final official-result lag) do not need a forecast probability model, but they still require exact contract semantics and execution evidence.

Probabilistic lanes do.

For every forecast model version retain:

- station/source/family;
- horizon to settlement;
- forecast inputs as known at decision time;
- predicted outcome distribution;
- realized settlement outcome;
- Brier/log score and calibration bins;
- market price at the same timestamp;
- hypothetical execution price and capacity;
- later markouts;
- model version/hash.

Promotion gate should use a conservative lower confidence bound or empirically calibrated shrinkage, not raw model confidence. The current uncalibrated heuristic lock score may collect evidence but cannot justify `TRADE NOW` by itself.

## 8. Exact execution gate

Immediately before any financial alert:

1. contract adapter still valid and rule text/hash unchanged;
2. official-data evidence still current;
3. candidate source timestamp is causal and within freshness limit;
4. exact CLOB book fetched for every required token;
5. exact requested quantity can be filled at/below max price;
6. current fee schedule applied;
7. edge remains above lane-specific minimum after fees and slippage;
8. event has not closed/stopped accepting orders;
9. no generation/catalog/source revision occurred during confirmation;
10. candidate is not duplicate/already acted on;
11. Telegram message is rebuilt from confirmed state.

## 9. Alert contract

### `🚨 WEATHER TRADE NOW`

Must contain:

- strategy lane;
- city/station/source and target date/interval;
- exact market/outcome/side or all basket legs;
- direct Polymarket link(s);
- current executable ask(s);
- maximum acceptable price(s);
- recommended maximum size based on visible common capacity;
- estimated fees;
- conservative probability or deterministic payout proof;
- expected edge / guaranteed spread where appropriate;
- controlling official source and timestamp;
- exact skip/cancel conditions;
- candidate expiry time.

### `🎯 WEATHER LIMIT BID`

Must contain:

- market/outcome/side;
- suggested bid and hard maximum bid;
- desired/maximum size;
- fair-value interval and basis;
- current spread/depth;
- cancel/reprice triggers;
- how long the bid remains valid;
- whether it contributes to a complete-set inventory target.

## 10. No-profit-overstatement rules

- A cheap share is not profit merely because its face value is $1.
- Nominal face value of mutually exclusive losing buckets is not portfolio value.
- Complete-set profit is claimed only for the portion of inventory covering every exhaustive outcome.
- Resting maker orders are not fills.
- Forecast edge is expected value, not guaranteed profit.
- Gamma BBO is not execution authority.
- Historical backtest profit cannot use quotes that were not actually executable at the modeled size.
- Resolution-lag claims require the contract's controlling publication/version, not a correlated proxy.

## 11. Initial engineering sequence

### Stage W1 — Catalog + contract census

Build a weather-only discovery command that retrieves current weather events without a full active-universe walk and outputs:

- event/market/token counts;
- family classification;
- source host/adapter candidate;
- unsupported-rule reason;
- outcome geometry/exhaustiveness candidate;
- liquidity/spread summaries.

Acceptance: complete agreement with an independently sampled Polymarket Weather page/category census for the same timestamp, within documented taxonomy limitations.

### Stage W2 — Daily temperature/rain contract adapters

Implement and adversarially test:

- NWS WRH + Weather Underground fallback decision tree;
- high and low temperature;
- whole-unit precision and bucket tails;
- first-following-day datapoint/revision cutoff;
- NWS CLI rain contract and revision cutoff.

No forecast promotion yet.

### Stage W3 — Weather orderbook + structural lanes

Implement:

- exact weather-token CLOB books;
- complete-set semantic proof and underround detector;
- logical-equivalence/threshold detector;
- exact capacity/fees;
- source-update candidate recheck.

These lanes can potentially earn promotion before probabilistic models because they rely on structural/settlement facts.

### Stage W4 — Observations + deterministic result/late-lock evidence

Build source-specific observation/release watchers and prospective settlement-lag evidence. Validate primary/fallback parity and revision timing.

### Stage W5 — Maker/vacuum engine

Implement virtual maker orders, fill simulation using actual trade/book progression, per-outcome inventory, complete-set accounting, cancellation logic and post-fill markouts.

### Stage W6 — Forecast model/calibration

Build station/family/horizon-specific probabilistic models and prospective calibration. Only promote after empirical gates are frozen and passed.

### Stage W7 — e2-micro silent-shadow acceptance

Only after offline tests and a realistic weather-scale fixture pass. Acceptance focuses on weather-specific workload rather than the rejected general-Astra 200k-market workload.

## 12. Initial resource targets

These are engineering targets, not permission to weaken correctness:

- no broad 200k-market traversal in steady state;
- materialized registry limited to weather contracts only;
- hot websocket tokens bounded dynamically;
- normal incremental evaluation target <2 seconds;
- source-update-to-confirmed-candidate target <5 seconds when network permits;
- process swap 0;
- host MemAvailable >=128 MiB;
- no financial delivery during engineering/shadow stages;
- exact CLOB confirmation mandatory for all financial candidates.

If the actual weather catalog still cannot fit these targets, reduce hot subscriptions/polling frequency by tier rather than dropping weather contracts silently.

## 13. Frozen decisions for the branch

- `main` remains the preserved general Astra experiment and is not modified by weather-only development.
- The weather branch is a separate product, not a detector promotion inside the failed general runtime.
- No real-money automation.
- Manual alerts only after explicit later promotion.
- Structural and official-result lanes are prioritized over forecast speculation.
- Passive maker/vacuum opportunities are first-class, not an afterthought.
- Unsupported contract rules fail closed and remain visible in diagnostics.
- Current `weather_late_lock` / `weather_friend_lock` code is reusable research evidence but is not assumed calibrated or production-safe.
