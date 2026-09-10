# Weather-Only Trade Scanner — Opportunity Map and Minimal Architecture

Branch: `weather-only-trade-scanner`
Baseline: `077b09e832600cd8c8e83cd97cbcf09becd73243`
Status: design/specification only; no deployment, no detector promotion, no financial delivery.

## Objective

Build a small, weather-only Polymarket scanner whose product is not a research firehose. It should watch only weather contracts, maintain exact contract/source semantics, read fresh order books, use official observations plus explicitly separate forecast/model inputs, and notify the user only when it has a concrete manual trade or maker instruction with enough conservative edge to justify action.

The general Astra runtime remains preserved on `main`. This branch is intentionally allowed to discard the 200k-market full-universe architecture if a weather-specific discovery path can prove sufficient recall for the supported weather families.

## Current market landscape (September 2026)

Polymarket exposes weather markets through the Weather and Daily Temperature surfaces. The current Weather/Temperature pages show hundreds of weather contracts, dominated by daily high/low temperature bucket events, with smaller precipitation and longer-horizon weather categories. The public Gamma API supports event/market filtering by `tag_slug`/`tag_id`, so this branch should discover the relevant weather families directly rather than crawling every active Polymarket event.

Important caveat: the broad `weather` tag/page also contains items that are not suitable for a meteorological trading model (for example pandemic/natural-disaster items), so tag membership alone is not sufficient authority. Every supported contract must pass a family-specific compiler.

References:
- https://polymarket.com/weather/temperature
- https://polymarket.com/climate-science/weather
- https://docs.polymarket.com/api-reference/events/list-events
- https://docs.polymarket.com/api-reference/events/list-events-keyset-pagination

## Product outputs

Only two Telegram financial instruction types are intended after shadow validation:

### 1. `WEATHER TRADE NOW`

A taker/manual order that can be executed immediately. Required fields:
- exact market/event and side;
- exact token/outcome;
- current executable ask and size;
- fee model/estimated fee;
- maximum acceptable price;
- conservative probability or deterministic payoff basis;
- expected edge and payout;
- exact capacity at current depth;
- settlement source and contract family;
- direct Polymarket link;
- explicit skip/cancel conditions;
- evidence timestamps.

### 2. `WEATHER LIMIT BID`

A passive maker instruction for the friend's vacuum strategy or spread capture. Required fields:
- exact outcome/token;
- bid price to place;
- maximum total size;
- current best bid/ask and spread;
- conservative fair-value range;
- why the bid is attractive;
- fill/inventory consequences;
- exact cancel/reprice conditions;
- expiration/time horizon;
- direct market link.

A maker instruction is never described as guaranteed profit. A partial fill can create directional inventory.

WATCH/research evidence may be stored silently but should not be sent to Telegram.

## Opportunity map

The scanner should search both YES and NO where logically equivalent. These lanes cover the practical classes of weather opportunity we want to investigate.

### A. Finalized-source / resolution-lag trade

Highest-priority informational lane.

If the market's named primary settlement source has published the final value for the contract date, and the contract compiler can map that value deterministically to one outcome, any still-open book can be checked for residual mispricing.

Examples:
- final daily high is published as 82°F; the 82–83°F YES can still be bought below its safe max price;
- a finalized precipitation value maps unambiguously to one bucket;
- an official daily minimum is finalized but the winning bucket has not fully repriced.

Required authority:
- exact source URL/adapter;
- exact date/timezone/measurement definition;
- final-vs-preliminary state modeled;
- correction/revision policy understood;
- fallback source precedence understood;
- exact bucket mapping;
- fresh CLOB execution confirmation.

This lane can become near-deterministic only when the source is actually final under the contract rules. Otherwise it remains probabilistic.

### B. Same-day observed high/low lock

The existing `weather_late_lock` idea generalized correctly.

For daily maximum temperature, once a high has already been observed, future observations can only keep or increase it. The model asks how likely the remaining local-day weather is to exceed the current maximum and cross into another bucket.

For daily minimum temperature, the symmetric problem applies: after an observed low, future observations can only keep or lower the minimum. A minimum may look locked during the day but become vulnerable again in the evening, so local-day timing and remaining forecast hours are essential.

Required inputs:
- settlement station/source proxy or exact source where available;
- full causal observation history for the local contract day;
- current observed max/min;
- forecast distribution for remaining hours;
- seasonal/time-of-day cooling/heating risk;
- source precision/rounding rules;
- fresh executable book.

The current uncalibrated heuristic must not be promoted as a probability. This branch should collect prospective outcomes and calibrate a conservative probability model before `TRADE NOW` authority.

### C. Forecast-vs-market edge

Directional pre-event/intra-event trade.

Estimate `P(outcome)` from one or more weather forecasts plus live observations, then compare the conservative probability bound with executable YES and NO prices after taker fees.

For YES:
`edge = conservative_P_yes - executable_yes_cost - fee`

For NO:
`edge = conservative_P_no - executable_no_cost - fee`

Only alert when the conservative lower bound, not the point forecast, clears the minimum edge. Forecast disagreement should widen uncertainty rather than be averaged away.

Initial priority should be daily temperature because the contract families repeat every day and can accumulate calibration data quickly. Precipitation can be added only with a family-specific measurement/settlement adapter.

### D. Complete-bucket underround

Structural lane for an event whose outcome buckets are proven mutually exclusive and exhaustive under the actual contract rules.

If buying one YES share in every bucket costs less than the one winning $1 redemption after all applicable taker fees, there is a locked gross structural payoff provided every leg can be filled and the resolution rules guarantee exactly one winning bucket.

For size `q`:
`locked_profit = q * (1 - sum(executable_cost_per_bucket))`

Capacity is the minimum simultaneously executable depth across all required legs, not displayed midpoint volume.

Must fail closed on:
- missing edge bucket;
- overlapping/gapped bucket definitions;
- ambiguous rounding;
- fallback/cancellation rules that can break exactly-one-winner semantics;
- stale/missing leg books;
- inability to fill all legs at or below the certified total cost.

This lane does not need a weather forecast if contract semantics and prices alone prove the payoff.

### E. Same-market YES+NO complement underround

For any binary weather child market, buying one YES and one NO forms a complete binary set. If both executable asks plus fees total below $1, the pair has a mechanically locked gross spread before execution/settlement risk.

This is independent of the weather forecast, but it belongs in this branch because only weather child markets are scanned.

The CLOB must be checked immediately before alerting and simultaneous executable capacity must be reported.

### F. Cross-contract logical arbitrage

Only for relationships the contract compiler can prove.

Examples that may become certifiable:
- nested temperature thresholds on the same station/date;
- a threshold and a bucket set referring to exactly the same official variable;
- constraints between daily high and daily low when sources/date/timezones are identical;
- duplicate contracts with exactly equivalent resolution semantics.

No title/string similarity is sufficient. The source, station, date, measurement, unit, precision, fallback and interval must all match the logical proof.

### G. Official-data shock / stale-book trade

When a new official observation or forecast update materially changes the set of feasible/probable buckets, compare the newly recomputed conservative value with the live book immediately.

Examples:
- the station has just printed a new daily high that invalidates all lower maximum buckets;
- a new minimum observation invalidates higher minimum buckets;
- a source update crosses a precipitation threshold.

This is a latency lane. It should react to source changes and then fetch exact CLOB depth; it should not poll the whole market universe quickly.

### H. Contract-rule / rounding edge

Some weather markets use different stations, precision, rounding, date windows or finalization rules. Traders can price the headline rather than the exact resolution rule.

The scanner should model, where explicitly supported:
- Fahrenheit vs Celsius;
- one-degree vs two-degree buckets;
- source decimal precision;
- explicit rounding/truncation;
- local-calendar-day boundaries;
- station identity;
- finalization/revision delay;
- fallback-to-edge-bucket clauses.

This is not a separate forecast; it is correct contract interpretation applied to any of the directional/structural lanes.

### I. Maker vacuum / deep-limit accumulation

This is the friend's strategy and deserves a separate engine rather than being forced into a taker detector.

The scanner maintains a fair-value distribution across all buckets and watches the full order book. It looks for temporary liquidity vacuums where a resting bid can be placed materially below conservative value.

Example output:
`PLACE LIMIT BID 0.20 on 80–81°F; conservative fair 0.38–0.46; max $12; cancel if new forecast shifts median by >=2°F, official max enters another bucket, or local time passes HH:MM.`

The expected edge is conditional on fill; no fill means no trade. Once a fill occurs, inventory is tracked by event/bucket/side/average cost.

The engine must distinguish:
- fair-value maker bid;
- spread-capture bid;
- complete-set accumulation bid;
- purely speculative lowball bid.

### J. Maker complete-set accumulation

Place small passive bids across mutually exclusive/exhaustive buckets so that a fully filled complete set would cost below $1.

Critical distinction: a target basket can be profitable while a partially filled basket is directional. The tracker therefore needs:
- per-bucket desired price/size;
- actual/manual fills;
- remaining unfilled legs;
- current worst-case completion cost;
- guaranteed payoff only after a complete certified set exists;
- cancel/rebalance instructions when the book or weather state changes.

This is the mathematically precise form of the user's description of accumulating cheap temperature shares. Buying $1,000 face value of losing buckets does not create $1,000 realizable value; only the eventual winning shares pay $1 unless a complete structural set locks redemption.

### K. Passive spread capture / maker rebate lane

Polymarket currently documents zero maker trading fees and category-dependent taker fees; Weather is listed with a 0.05 fee-rate parameter and makers may be eligible for rebates. Market-specific parameters must still be queried from CLOB market info at runtime.

A weather-only maker engine may quote inside unusually wide spreads when:
- fair value is stable enough;
- expected spread/rebate exceeds inventory/adverse-selection risk;
- the resulting position remains within a small event-level inventory cap.

This is a market-making strategy, not a guaranteed arbitrage. It should remain shadow-only until fill/adverse-selection statistics are measured.

References:
- https://docs.polymarket.com/trading/fees
- https://docs.polymarket.com/api-reference/markets/get-clob-market-info
- https://docs.polymarket.com/api-reference/rebates/get-current-rebated-fees-for-a-maker

## Scope by contract family

### Phase 1 — daily temperature only

Build first because it has high repetition, many active contracts, strong station/date structure, and direct relevance to the original idea.

Supported families should be versioned adapters, not one generic parser:
- daily high temperature;
- daily low temperature;
- US NOAA/NWS-style stations;
- Hong Kong Observatory-style daily extracts;
- other recurring source families only after their finalization/precision/fallback rules are independently encoded.

The current broad `weather_contracts.py` adapter is deliberately NWS-WRH-only and rejects legitimate other source families. Reuse its fail-closed philosophy, not its single-source limitation.

### Phase 2 — short-horizon precipitation/rain

Add event families only when measurement variable, station/area, interval, units, accumulation convention and source finalization are exact.

### Phase 3 — longer-horizon weather/climate events

Monthly precipitation, hurricanes, records and similar contracts may be supported later. They have different data-generating processes and should not share the daily-temperature probability model.

## Minimal weather-only runtime architecture

The e2-micro target should run a small event-driven weather stack rather than the general Astra universe stack.

### 1. Weather Discovery

Use Gamma tag-filtered endpoints (`tag_slug`/`tag_id`, plus direct known recurring series/tags) to discover weather events only. Perform periodic low-frequency reconciliation and deduplicate by event/market/condition/token IDs.

Discovery should prove recall for the *supported family*, not for all 200k Polymarket markets. A daily-temperature adapter can require that all children of each selected event are loaded so bucket completeness can be certified.

Expected scale: hundreds of markets, not ~14k materialized candidates from a 200k-market traversal.

### 2. Contract Compiler

Compile each event into a typed `WeatherContract` containing:
- family (`daily_high`, `daily_low`, `precipitation`, ...);
- station/location;
- timezone;
- target interval/date;
- unit and precision;
- bucket intervals and proof of gaps/overlaps;
- primary resolution source;
- fallback source/rule;
- finalization/revision rule;
- exactly-one-outcome proof if applicable;
- supported/unsupported reason code.

Unsupported contracts remain visible in metrics but cannot generate financial instructions.

### 3. Weather Data Adapters

Separate settlement authority from prediction inputs.

`SettlementSourceAdapter`:
- reads the source named in the rules where technically possible;
- tracks preliminary/final/corrected state;
- produces normalized observations/final values with provenance.

`ForecastAdapter`:
- may use NWS/Open-Meteo/aviation/model feeds;
- is explicitly predictive, never silently promoted to settlement authority;
- records issued-at and valid-at timestamps.

### 4. Weather Probability Engine

For each contract family, generate a distribution over final outcomes, not just a single forecast temperature.

For temperature:
- pre-event model distribution;
- intraday conditioning on observations;
- current max/min as hard path constraints;
- remaining-hour forecast uncertainty;
- source rounding/precision;
- historical residual calibration by city/station, horizon and season where enough evidence exists.

Return point probability plus a conservative lower/upper confidence bound. Financial decisions use the conservative bound.

### 5. Weather Book Engine

Subscribe only to weather token IDs through the CLOB market websocket. Maintain local top-of-book and bounded depth.

For an actionable candidate, refresh exact CLOB books immediately before generating the instruction. Displayed Polymarket midpoint is never execution authority.

The public CLOB supports single and batched order-book reads; runtime market info also provides tick size, minimum size and fee parameters.

References:
- https://docs.polymarket.com/concepts/prices-orderbook
- https://docs.polymarket.com/api-reference/market-data/get-order-book
- https://docs.polymarket.com/api-reference/market-data/get-order-books-request-body

### 6. Opportunity Engine

Pure, cheap detectors over the small compiled universe:
- `weather_result_lag`;
- `weather_observed_lock`;
- `weather_forecast_edge`;
- `weather_complete_bucket_underround`;
- `weather_binary_pair_underround`;
- `weather_cross_logic`;
- `weather_source_shock`;
- `weather_maker_vacuum`;
- `weather_maker_complete_set`;
- `weather_spread_capture`.

Each detector emits a candidate with an evidence class (`DETERMINISTIC`, `CALIBRATED_PROBABILISTIC`, `MAKER_CONDITIONAL`, `RESEARCH_ONLY`).

### 7. Final Trade Authority

A single final gate rechecks:
- contract adapter still valid;
- source/forecast timestamps fresh;
- exact CLOB price/depth fresh;
- market still accepting orders;
- fee parameters fresh;
- capacity above minimum;
- conservative edge above lane-specific threshold;
- candidate has not become stale;
- no conflicting source update occurred.

Only then can the future production version format Telegram `WEATHER TRADE NOW` or `WEATHER LIMIT BID`.

Initially this gate must force silent shadow regardless of detector output.

### 8. Inventory / maker tracker

For passive strategies maintain event-level state:
- hypothetical/manual order IDs;
- desired bids;
- user-confirmed fills or connected read-only position/fill data if later authorized;
- shares per bucket/side;
- average acquisition cost;
- complete-set count;
- worst-case completion cost;
- mark-to-market exit capacity;
- directional residual exposure.

Do not infer fills merely because the market traded through a price; shadow backtests should distinguish touched, queue-aware simulated and confirmed fills.

### 9. Persistence and Telegram

Reuse the hardened concepts from Astra:
- SQLite WAL/account backup discipline;
- atomic signal/outbox state;
- secret-safe logs;
- `SENDING -> UNCERTAIN` delivery semantics;
- exact release/dependency attestation.

But use a weather-only schema namespace/strategy version so evidence is not mixed with the old general scanner.

## Resource target for e2-micro

The design target is intentionally small:
- no full 200k-market keyset crawl;
- no sports/crypto/macro clients;
- no general structural/duplicate/wide-spread pass over 14k markets;
- websocket subscriptions only for supported weather tokens;
- low-frequency Gamma tag reconciliation;
- event-driven reevaluation on book/weather changes;
- expensive model/calibration work performed offline or cached, not every scan.

Initial engineering gates before any VM deployment:
- supported active weather markets discovered in a bounded tag-filtered walk;
- all children retained for selected bucket events;
- no unsupported contract can reach final trade authority;
- detector pass p95 target <1 second in realistic weather-scale fixtures;
- RSS target materially below the prior scanner, with explicit hard bounds;
- zero financial delivery in shadow mode.

## Profit and alert policy

No strategy is promoted merely because it looks plausible. A future lane earns `TRADE NOW` authority only after the evidence appropriate to that lane exists:

- deterministic structural/result-lag lanes: mechanical proof + executable-price/fill simulation + contract-source certification;
- probabilistic lanes: prospective calibration, Brier/log-loss/reliability evidence, conservative probability bounds and net-of-fee shadow P&L;
- maker lanes: realistic fill model, adverse-selection analysis, inventory caps, cancel latency, queue assumptions and net-of-fee/rebate P&L.

The Telegram objective is precision over recall. It is acceptable to miss marginal trades; it is not acceptable to label an uncalibrated or semantically ambiguous opportunity `TRADE NOW`.

## Immediate engineering sequence

1. Replace full-universe production discovery on this branch with tag/series-scoped weather discovery and produce a live inventory report of all active weather families.
2. Build typed daily-high and daily-low contract compilers, preserving all event children and resolution-rule provenance.
3. Add source-adapter registry (start with the best-understood recurring source families) and explicit unsupported reasons.
4. Build a compact weather-only CLOB book cache and exact pre-alert book refresh.
5. Port/refactor observed-lock and friend-style maker logic into the new evidence classes; do not reuse their uncalibrated scores as probabilities.
6. Implement deterministic structural lanes before forecast trading because they require less statistical inference.
7. Add prospective shadow ledger for forecast/lock/maker calibration.
8. Run realistic offline scale/resource tests. Only if those pass should the weather branch get its own separately authorized e2-micro silent-shadow deployment.

## Non-goals for the first release

- no automatic order placement;
- no real-money execution;
- no promotion inherited from general Astra;
- no claim that generic weather-tag membership is a certified contract;
- no treating displayed midpoint as executable price;
- no treating an official proxy feed as the settlement source unless the contract actually names it;
- no guarantee that forecast/maker strategies are profitable before prospective evidence.
