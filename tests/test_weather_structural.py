from __future__ import annotations

from polymarket_scanner.models import Book, Market
from polymarket_scanner.weather_structural import (
    prove_daily_temperature_partition,
    screen_complete_set_underround,
)


def rules() -> str:
    return (
        "The target date is September 11, 2026. "
        "If NOAA is unavailable by 11:59 PM ET on September 12, 2026, Weather Underground Daily Observations will be used. "
        "The result becomes final when the first datapoint for the following day is published. "
        "Revisions count until before the first following-day datapoint. "
        "If no data is available by the deadline, the lowest bracket wins."
    )


def raw_child(mid: str, question: str) -> dict:
    return {
        "id": mid,
        "question": question,
        "slug": f"bucket-{mid}",
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "outcomes": '["Yes","No"]',
        "clobTokenIds": f'["{mid}-yes","{mid}-no"]',
        "description": rules(),
        "resolutionSource": "https://www.weather.gov/wrh/timeseries?site=ZBAA",
    }


def materialize(event: dict, child: dict) -> Market:
    return Market(
        id=child["id"],
        event_id=event["id"],
        event_slug=event["slug"],
        event_title=event["title"],
        event_neg_risk=False,
        question=child["question"],
        slug=child["slug"],
        condition_id=f"condition-{child['id']}",
        outcomes=["Yes", "No"],
        token_ids=[f"{child['id']}-yes", f"{child['id']}-no"],
        outcome_prices=[0.5, 0.5],
        best_bid=None,
        best_ask=None,
        liquidity=100.0,
        volume_24h=10.0,
        active=True,
        closed=False,
        end_date="2026-09-12T23:59:00Z",
        description=rules(),
        resolution_source="https://www.weather.gov/wrh/timeseries?site=ZBAA",
        category="Weather",
    )


def complete_event():
    children = [
        raw_child("1", "25°C or below"),
        raw_child("2", "26°C"),
        raw_child("3", "27°C"),
        raw_child("4", "28°C or higher"),
    ]
    event = {
        "id": "event-1",
        "slug": "highest-temp-zbaa-sep-11",
        "title": "Highest temperature in Beijing on September 11?",
        "description": rules(),
        "resolutionSource": "https://www.weather.gov/wrh/timeseries?site=ZBAA",
        "markets": children,
    }
    return event, [materialize(event, child) for child in children]


def test_proves_complete_integer_temperature_partition_from_full_parent_membership():
    event, markets = complete_event()
    proof = prove_daily_temperature_partition(event, markets)
    assert proof is not None
    assert proof.event_id == "event-1"
    assert proof.contract.primary.station == "ZBAA"
    assert proof.guaranteed_yes_payout_per_complete_set == 1.0
    assert [(leg.lower, leg.upper) for leg in proof.legs] == [
        (None, 25), (26, 26), (27, 27), (28, None)
    ]
    assert [leg.yes_token for leg in proof.legs] == ["1-yes", "2-yes", "3-yes", "4-yes"]


def test_partition_proof_fails_if_materializer_omitted_a_parent_child():
    event, markets = complete_event()
    assert prove_daily_temperature_partition(event, markets[:-1]) is None


def test_partition_proof_fails_on_numeric_gap_even_when_membership_matches():
    event, markets = complete_event()
    event["markets"][2]["question"] = "28°C"
    event["markets"][3]["question"] = "29°C or higher"
    markets[2].question = "28°C"
    markets[3].question = "29°C or higher"
    assert prove_daily_temperature_partition(event, markets) is None


def test_partition_proof_fails_on_unmodeled_refund_semantics():
    event, markets = complete_event()
    event["description"] += " The market may be refunded in an exceptional case."
    assert prove_daily_temperature_partition(event, markets) is None


def test_complete_set_screen_reports_common_size_and_estimated_spread_but_not_execution_certificate():
    event, markets = complete_event()
    proof = prove_daily_temperature_partition(event, markets)
    assert proof is not None
    books = {
        "1-yes": Book("1-yes", [], [(0.10, 12.0)]),
        "2-yes": Book("2-yes", [], [(0.18, 8.0)]),
        "3-yes": Book("3-yes", [], [(0.22, 10.0)]),
        "4-yes": Book("4-yes", [], [(0.15, 7.0)]),
    }
    screen = screen_complete_set_underround(proof, books, minimum_spread_per_bundle=0.10)
    assert screen is not None
    assert screen.raw_ask_cost_per_bundle == 0.65
    assert screen.estimated_total_cost_per_bundle < 0.70
    assert screen.estimated_locked_spread_per_bundle > 0.30
    assert screen.common_best_ask_shares == 7.0
    assert screen.screening_only is True
    assert screen.requires_exact_fee_authority is True


def test_complete_set_screen_rejects_missing_leg_book():
    event, markets = complete_event()
    proof = prove_daily_temperature_partition(event, markets)
    assert proof is not None
    books = {
        "1-yes": Book("1-yes", [], [(0.10, 12.0)]),
        "2-yes": Book("2-yes", [], [(0.18, 8.0)]),
        "3-yes": Book("3-yes", [], [(0.22, 10.0)]),
    }
    assert screen_complete_set_underround(proof, books) is None


def test_complete_set_screen_rejects_bundle_without_required_spread():
    event, markets = complete_event()
    proof = prove_daily_temperature_partition(event, markets)
    assert proof is not None
    books = {
        "1-yes": Book("1-yes", [], [(0.25, 12.0)]),
        "2-yes": Book("2-yes", [], [(0.25, 8.0)]),
        "3-yes": Book("3-yes", [], [(0.25, 10.0)]),
        "4-yes": Book("4-yes", [], [(0.25, 7.0)]),
    }
    assert screen_complete_set_underround(proof, books, minimum_spread_per_bundle=0.01) is None
