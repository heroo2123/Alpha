"""Synthetic Gamma shape, informed by bounded September 2026 public samples.

Real sampled children had ~3.8 KiB median JSON and dozens of unused fields.
Unlike the former tiny fixture this includes those fields, long identifiers,
closed children, full parent membership and dense event clusters. No live quotes
or credentials are embedded; values are deterministic synthetic data.
"""
import hashlib
import json

EVENTS = 23200
INVENTORY = 243750
ACTIVE = 195000
SELECTED = 13500


def raw_market(number: int, *, active=True, selected=False) -> dict:
    identifier = str(number + 1000000)
    digest = hashlib.sha256(identifier.encode()).hexdigest()
    row = {
        "id": identifier, "active": active, "closed": not active,
        "enableOrderBook": True, "acceptingOrders": active,
        "conditionId": "0x" + digest,
        "clobTokenIds": json.dumps([str(10**76 + number * 2), str(10**76 + number * 2 + 1)]),
        "outcomes": '["Yes", "No"]', "outcomePrices": '["0.41", "0.59"]',
        "bestBid": .40, "bestAsk": .42, "spread": .02,
        "question": "Will BTC be above $100?" if selected else f"Will fixture candidate {identifier} win?",
        "slug": "synthetic-candidate-" + digest[:24], "endDate": "2027-01-01T00:00:00Z",
        "description": ("Synthetic resolution rule: use the specified final observation, matching date and unit. "
                        "Cancelled events are not certified by this fixture. " + digest[:16] + ". ") * 9,
        "resolutionSource": "https://example.invalid/synthetic-authority",
        "updatedAt": "2026-09-09T00:00:00Z", "liquidityNum": 123.45,
        "feeSchedule": {"rate": .07, "exponent": 1, "takerOnly": True},
    }
    for key in ("image", "icon", "questionID", "marketMakerAddress", "resolvedBy", "submitted_by"):
        row[key] = "synthetic-unused-" + digest + "-" + key
    for key in ("archived", "approved", "new", "featured", "funded", "ready", "restricted", "automaticallyActive",
                "automaticallyResolved", "hasReviewedDates", "requiresTranslation", "deploying", "feesEnabled",
                "pendingDeployment", "rfqEnabled", "holdingRewardsEnabled", "clearBookOnStart", "showGmpSeries"):
        row[key] = False
    for key in ("volume", "volumeNum", "volumeClob", "volume1wk", "volume1mo", "volume1yr", "volume24hr",
                "lastTradePrice", "oneDayPriceChange", "oneWeekPriceChange", "oneMonthPriceChange", "rewardsMinSize",
                "rewardsMaxSpread", "orderMinSize", "orderPriceMinTickSize", "umaBond", "umaReward", "customLiveness"):
        row[key] = 123.456
    return row


def child_count(event_number: int) -> int:
    # 2000 dense events with 60 children reproduce the captured page-99 size
    # when requesting 25 events. Remaining events have 5 or 6 children.
    if 9000 <= event_number < 11000:
        return 60
    sparse_number = event_number if event_number < 9000 else event_number - 2000
    return 6 if sparse_number < 17750 else 5


def source_events():
    number = selected = 0
    for event_number in range(EVENTS):
        children = []
        for _ in range(child_count(event_number)):
            active = number % 5 != 4
            # Include selected rows in both sparse and dense parent clusters.
            eligible = active and (selected < 10000 or (9000 <= event_number < 11000 and selected < SELECTED))
            children.append(raw_market(number, active=active, selected=eligible))
            selected += int(eligible)
            number += 1
        yield {"id": "e" + str(event_number), "slug": "synthetic-event-" + str(event_number),
               "title": "Synthetic event", "negRisk": True, "markets": children,
               "description": "Synthetic parent rules; no guaranteed payoff assertion.",
               "tags": [{"id": "1", "label": "Fixture", "slug": "fixture"}],
               "series": [{"id": "unused", "title": "Synthetic unused Gamma metadata"}]}
    assert number == INVENTORY and selected == SELECTED
