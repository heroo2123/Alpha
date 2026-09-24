# V11 public discovery and semantic census

The off-host `MarketDiscovery` worker captures anonymous Gamma keyset pages,
archives exact page bytes and event receipts, and reuses the existing strict
weather classifier. It never turns discovery into strategy certification or
broadens accepted contract grammar. The official endpoint contract is documented
at https://docs.polymarket.com/market-data/discover-markets (reviewed 2026-09-24).

Each step makes at most one scheduled GET and processes a bounded event slice.
Page/cursor, processing position, attempt identity and raw receipts survive
interruption. A reserved request without a captured response is recorded as
interrupted, not silently repeated under the same identity. Existing Gamma host
cooldowns, 429 handling and partial-success collection remain in force. The
60-second scheduled Gamma interval is unchanged: full-catalog capacity and
freshness have not been demonstrated. No live catalog was fetched for these tests.

Reports separate event hits, distinct events, duplicate revisions, weather-looking
events, strict semantic support, unsupported reasons/families and template review
clusters. Counts use first receipt per event per scan. Later changed duplicates
still pass through quarantine. Cursor loops, schema changes, stale pages, and
page/event/time bounds produce incomplete scans. Template hashes are review aids,
not approved grammar. Collection spans a recorded receipt interval; even an
exhausted traversal does not prove a simultaneous current universe. Current
universe verification always remains false in this implementation.

Strict temperature events require current raw-bound station metadata before a
rule binding is observed. Missing metadata leaves that binding unavailable and
does not manufacture rule drift. Actual unsupported or closed revisions of a
previously observed event quarantine its existing binding while preserving the
last valid rule preimage, raw rejected input and account history. Reverting to a
previous rule does not remove quarantine; protected reviewed recertification is
still required. Rule freshness follows the original receipt, not extraction time.
Original receipt sequence and causal page lineage prevent delayed page processing
from replacing newer rules, including equal-wall-time receipts.

Rule quarantine feeds the bounded PAPER cancellation intake and retires maker
research quotes. Cancellation affects existing managed passive/new-risk intents,
keeps ambiguous cash/inventory reserved and creates no order or automatic sale.
Health has a dedicated bounded intake slot; rotating operator/EVENT/rule channels
prevents starvation at the smallest nonhealth budget. Maker safety retirement runs
before optional event evaluation, so a census-held queue lock cannot postpone it.
This remains same-process PAPER integration, not the independent live guardian.

Dynamic route registration, broader semantic-family approval, source/forecast/QC
adapters, complete fresh-universe evidence and forward runtime acceptance remain
open. Discovery does not grant financial, deployment or service authority. V10
maintenance remains deferred and the host resource/isolation gate remains open.

Verification: 30 distinct new cases; related discovery/rules/cancellation/runtime/
queue/census/collector/admission/maker suite **208 passed in 14.64 s**. Full-suite
results and publication identity are recorded in V11_WORK_CHECKPOINT.md. Fixtures
are explicitly synthetic and do not establish public-source or strategy eligibility.
