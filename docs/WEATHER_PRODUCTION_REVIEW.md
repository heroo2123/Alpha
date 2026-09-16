# Weather evidence and strategy review

This review covers the public evidence pipeline and the retained runtime corrections.
It does not certify a funded account, deployment host, or trading profitability.
The production component is `polymarket_scanner.production.weather.WeatherPipeline`.
It composes read-only clients and pure strategy functions; it does not inherit any
versioned PAPER service or obtain signing credentials. Account authority belongs to
the separately configured execution component described in `PRODUCTION_OPERATIONS.md`.

## Finding ledger

The reproduction baseline is integration commit
`d02a335c120e9a07803e8fa378579bd09a887668`.

| Finding | Executable reproduction | Correction and regression |
|---|---|---|
| Forecast evidence could be an 800-second cache hit labelled as a fresh provider refresh | Populate the real semantic-key cache, then call the effective post-receipt method; provider is never called on the baseline | Explicit `force_refresh` at the cache owner; tests cover successful refresh and provider failure before quotes |
| F5 omitted unfamiliar temperature templates and retained only example rejection identities | Fully enumerated Gamma fixture with 25 tagged maximum-temperature events using unfamiliar wording | Inclusive weather census and complete event/reason ledger; strict parser still rejects those contracts |
| F6 new terminal message waits behind 200 historical failures | Populate real SQLite with 200 pending edits and terminalize signal 201 | Targeted durable synchronization edits the new receipt immediately; global 475-row draining remains tested |
| F6 targeted-sync correction accidentally shadowed the global-drain selector | Existing 475-row test stopped after 200 edits | Separate row identity from the optional target selector; no restart pagination dependence |
| Compact remote bucket bounds could monopolize the event loop | Three valid buckets spanning 200 million integer degrees; baseline times out in an isolated subprocess | Analytical interval proof; gap, overlap, and unsafe floating-point integer-bound regressions |
| A coherent Seattle title could bind to an Atlanta/KATL source | Both legacy and current rule/question fixtures | Reviewed city/station pairs; unknown cities and mismatches reject; city joins the semantic digest |
| F7 historical maker simulations must remain excluded | Populated historical SQLite maker order, settlement and event history | Existing non-destructive migration and separated performance tests retained and passing |

`test_weather_production_review_regressions.py` ran seven selected reproductions
against the detached baseline: **7 failed**, as expected, including the bounded
five-second resource reproduction. The corrected implementations pass those tests.
`test_weather_stage2_semantic_terminal_maker.py` also exercises the effective maker
sender after confirmed delivery, activation failure, and deferred restart recovery.
An obsolete source-substring assertion was replaced with actual SQLite/Telegram-leaf
behavior; financial and Telegram APIs remain mocked.

## Supported strategy scope

All admission paths require the reviewed strict contract grammar, station/date/unit
binding, source receipts no older than 120 seconds, and exact quotes no older than
20 seconds. Signal expiry is capped by the quote interval, source interval, and
same-day local midnight. Revalidation independently fetches Gamma and every relevant
source, recomputes the thesis, and rejects changed identity or weaker model support.
An unchanged signal payload is never substituted for current evidence.

| Strategy | Evidence and admission | Execution interpretation and invalidation |
|---|---|---|
| DIRECTIONAL | Exactly 31 GEFS daily members, reviewed rounding policy, station coordinates/timezone, future local day within three days, operator model-gap threshold | Bounded taker order; raw member frequency is uncalibrated. Changed semantics, lost support, stale weather or unavailable price invalidates |
| SAME_DAY | Exact Fahrenheit WRH population, NWS near-term path and 31 GEFS hourly paths; afternoon trend/drop and at least 30/31 supporting members | Heuristic YES position, revision-sensitive. All three sources refresh; missing elapsed-hour coverage or changed bucket support rejects |
| SOURCE_SHOCK | Latest parsed official observation newly excludes a bucket relative to all prior observations | Provisional NO position with revision risk; no manufactured win probability. Revised source support or local midnight invalidates |
| STRUCTURAL | Exact complete binary pair or complete partition; current executable books, fees, tick and minimum size | Each leg remains explicit. Quoted complete-set payout depends on all legs filling; the execution worker enforces its separately configured legging loss policy |
| MAKER | Directional forecast evidence plus a passive, on-tick bid and sufficient surrounding book liquidity | Post-only proposal, never an inferred fill. Only authenticated order/fill reconciliation creates account inventory; expiry/support loss requests cancellation |
| RESULT_LAG | Actual strict WRH first-following-publication finality evaluator and repeated exact quotes | **Current public WRH polling cannot establish exact publication-state finality.** A baseline is collected, then each unsupported opportunity is rejected with a precise finality reason; no certificate is invented |

Current named station scope includes the reviewed London, Paris, Sao Paulo,
Atlanta, Austin, Denver, Dallas, Houston, Los Angeles, Miami, Chicago, Seattle and
San Francisco pairs, plus historical Munich and NYC station bindings. Parent/child
city aliases are not silently equated. Celsius daily forecasts can be represented;
same-day/source-shock WRH observation adapters currently support Fahrenheit only.
Forecast receipt freshness does not prove model-run age: that unknown is preserved
in each forecast evidence payload. WRH/model population alignment remains explicitly
uncertified in the same-day heuristic.

Discovery distinguishes completed Gamma pagination from supported semantic coverage.
Every recognized weather-looking event has a classification and rejection reason in
`semantic_event_ledger`; the limited examples list is not the denominator. Unsupported
events remain visible in the census and cannot enter any execution path.

Signal outcomes require explicit final Gamma resolution status and a coherent
condition/token/outcome-index/payout vector. A closed market alone is insufficient.
The returned record contains observed leg payouts, never account P&L or inferred
manual fills. Actual inventory, fees, proceeds and P&L remain execution-ledger facts.

## Verification evidence

Tests use temporary databases and in-memory public/authenticated API fixtures.
The [evidence archive](review-evidence/weather-production-2026-09-16.zip) includes
the 151-file test selection, full logs/JUnit results and tested source hashes.

| Run | Result |
|---|---|
| Baseline seven defect reproductions, Python 3.11 | 7 failed as expected, 5.993 seconds |
| Corrected weather/runtime selection, Python 3.11 | 1,121 passed, 1 host-source assertion deselected, 16.94 seconds |
| Corrected weather/runtime selection, Python 3.12 | 1,121 passed, 1 host-source assertion deselected, 14.66 seconds |

Deployment/host-authority suites and dirty-checkout acceptance
are owned by the separate integration gate; their exclusion is not a claim of passing.

The earlier apparent Python 3.12 stop at 14% was incomplete console output: both
original JUnit reports contain all 1,493 test results (40 and 46 failures respectively).
The cause of that output truncation was not established. Subsequent scoped runtime
runs produced complete logs and JUnit results; no test-process termination was reproduced.

Official interfaces reviewed: [Polymarket API documentation index](https://docs.polymarket.com/llms.txt),
[market details](https://docs.polymarket.com/market-data/market-details),
[resolution](https://docs.polymarket.com/concepts/resolution), and
[Open-Meteo ensemble API](https://open-meteo.com/en/docs/ensemble-api).
The separate API reviewer checked the current official SDK's negative-risk and
order-book wire schemas. No production database, Telegram message, host service,
private credential, funded account, order submission or cancellation was used.
