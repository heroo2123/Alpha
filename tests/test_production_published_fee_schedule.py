"""Fee policy math and the real public/EOA adapter, with offline transports only."""
from copy import deepcopy
from decimal import Decimal, ROUND_DOWN, ROUND_UP, ROUND_HALF_UP, ROUND_HALF_EVEN

import pytest

from polymarket_scanner.production.chain import ExchangeError
from polymarket_scanner.production.exchange import ExchangeEOA, PublicMarketReader
from polymarket_scanner.production.fees import (
    ONCHAIN_BOUND, EXCHANGE_PUBLISHED_SCHEDULE, fee_requirement, make_fee_evidence,
)
from test_production_exchange import (
    BLOCK, CONDITION, NOW, TOKEN, Chain, Wire, client, context, prepare,
)


def snapshot(*, policy=EXCHANGE_PUBLISHED_SCHEDULE, rate=".05", exponent="1", maximum=0, taker_only=True):
    source = context()
    source[1][1]["fd"] = {"r": rate, "e": exponent, "to": taker_only}
    chain = Chain()
    chain.fee = maximum
    return PublicMarketReader(fee_policy=policy, transport=Wire(source), chain=chain,
                              clock=lambda: NOW).market_snapshot(TOKEN, CONDITION)


@pytest.mark.parametrize("limit,expected", [(".01", ".000990"), (".4", ".024"), (".5", ".025"), (".9", ".025")])
def test_schedule_bounds_entire_buy_fill_interval_including_price_improvement(limit, expected):
    sample = snapshot()
    assert sample["max_fee_bps"] == 0
    assert fee_requirement(sample, limit, False) == Decimal(expected)
    assert fee_requirement(sample, limit, True) == 0


@pytest.mark.parametrize("exponent", [0, 1, 2, 3, 4])
def test_official_sdk_general_curve_with_bounded_integer_exponent(exponent):
    sample = snapshot(exponent=str(exponent))
    assert fee_requirement(sample, ".9", False) == Decimal(2) * Decimal(".05") * Decimal(".25")**exponent


@pytest.mark.parametrize("rounding", [ROUND_DOWN, ROUND_UP, ROUND_HALF_UP, ROUND_HALF_EVEN])
@pytest.mark.parametrize("exponent", [0, 1, 2, 4])
def test_arbitrary_fragments_and_five_decimal_rounding_fit_conservative_envelope(rounding, exponent):
    sample = snapshot(exponent=str(exponent))
    required = fee_requirement(sample, ".95", False)
    quantum, total, quantity = Decimal(".00001"), Decimal(0), Decimal(0)
    for price in map(Decimal, (".001", ".01", ".4999", ".5", ".51", ".95")):
        for size in map(Decimal, (".000001", ".0004", ".0008", ".001", ".123456", "5", "73.999999")):
            raw = size * Decimal(".05") * (price*(1-price))**exponent
            # Published minimum/zero-below-minimum, with all adjacent modes.
            fee = Decimal(0) if raw < quantum else raw.quantize(quantum, rounding=rounding)
            assert fee <= size * required
            # Ordinary nearest rounding at a half quantum is also covered,
            # without claiming this is the venue's tie-breaking convention.
            assert raw.quantize(quantum, rounding=ROUND_HALF_UP) <= size * required
            total += fee
            quantity += size
    assert total <= quantity * required


def test_no_default_fee_policy_even_before_invalid_credentials_are_inspected():
    with pytest.raises(ExchangeError, match="FEE_POLICY_REQUIRED"):
        ExchangeEOA(private_key=None, api_key="", api_secret="", api_passphrase="", wallet="", signer="")


def test_strict_onchain_policy_still_rejects_zero_and_preserves_positive_math():
    with pytest.raises(ExchangeError, match="UNBOUNDED_EXCHANGE_FEE"):
        snapshot(policy=ONCHAIN_BOUND)
    sample = snapshot(policy=ONCHAIN_BOUND, maximum=200)
    assert fee_requirement(sample, ".4", False) == Decimal(".008")
    assert fee_requirement(sample, ".4", True) == Decimal(".008")


def test_explicit_zero_published_rate_is_different_from_missing_fee_evidence():
    assert fee_requirement(snapshot(rate="0"), ".9", False) == 0
    raw = context()
    raw[1][1]["fd"].pop("r")
    with pytest.raises(ExchangeError):
        PublicMarketReader(fee_policy=EXCHANGE_PUBLISHED_SCHEDULE, transport=Wire(raw),
                           chain=Chain(), clock=lambda: NOW).market_snapshot(TOKEN, CONDITION)


@pytest.mark.parametrize("field,value,code", [
    ("fd", {"r": ".05", "e": "1.5", "to": True}, "FEE_CURVE_UNSUPPORTED"),
    ("fd", {"r": ".05", "e": "5", "to": True}, "FEE_CURVE_UNSUPPORTED"),
    ("fd", {"r": ".05", "e": "1", "to": False}, "FEE_SCOPE_UNSUPPORTED"),
    ("fd", {"r": ".05", "e": "1", "to": "true"}, "FEE_EVIDENCE_MISSING"),
    ("fd", {"r": ".05", "e": "1", "to": True, "future_fee": 1}, "FEE_EVIDENCE_MISSING"),
    ("mbf", 1, "BASE_FEE_COMPOSITION_UNSUPPORTED"),
    ("tbf", 1, "BASE_FEE_COMPOSITION_UNSUPPORTED"),
    ("mbf", None, "INVALID_UINT"),
])
def test_unsupported_or_missing_schedule_never_becomes_zero(field, value, code):
    source = context()
    source[1][1][field] = value
    with pytest.raises(ExchangeError, match=code):
        PublicMarketReader(fee_policy=EXCHANGE_PUBLISHED_SCHEDULE, transport=Wire(source),
                           chain=Chain(), clock=lambda: NOW).market_snapshot(TOKEN, CONDITION)


@pytest.mark.parametrize("change,code", [
    (lambda x: x.update(fee_policy=ONCHAIN_BOUND), "FEE_POLICY_MISMATCH"),
    (lambda x: x.update(token=str(int(TOKEN)+1)), "FEE_EVIDENCE_IDENTITY_MISMATCH"),
    (lambda x: x.update(condition="another-condition"), "FEE_EVIDENCE_IDENTITY_MISMATCH"),
    (lambda x: x.update(received_at=NOW+1), "FEE_EVIDENCE_TIME_MISMATCH"),
    (lambda x: x["fee_evidence"]["fd"].update(r="0"), "FEE_EVIDENCE_MUTATED"),
])
def test_policy_identity_time_and_raw_evidence_are_bound(change, code):
    sample = snapshot()
    change(sample)
    with pytest.raises(ExchangeError, match=code):
        fee_requirement(sample, ".4", False, expected_policy=EXCHANGE_PUBLISHED_SCHEDULE)


def test_prepared_wire_records_detached_fee_evidence_and_rejects_cap_before_signing():
    chain = Chain()
    chain.fee = 0
    wire = Wire(context())
    adapter = client(wire, chain, fee_policy=EXCHANGE_PUBLISHED_SCHEDULE)
    order = prepare(adapter, fee_cap=".03")
    assert order["fee_bound"] == "0.024000"
    assert order["fee_evidence"] == order["market"]["fee_evidence"]
    assert order["fee_policy"] == EXCHANGE_PUBLISHED_SCHEDULE
    assert "fee_evidence" not in order["payload"]
    assert all(call[0] == "GET" for call in wire.calls)
    with pytest.raises(ExchangeError, match="EXCHANGE_FEE_BOUND_EXCEEDS_OPERATOR_CAP"):
        prepare(client(Wire(context()), chain, fee_policy=EXCHANGE_PUBLISHED_SCHEDULE), fee_cap=".023999")


def test_schedule_change_is_refetched_before_signing_no_stale_preflight_fallback():
    later = context()
    later[1][1]["fd"]["r"] = ".1"
    wire = Wire(context() + later)
    chain = Chain()
    chain.fee = 0
    adapter = client(wire, chain, fee_policy=EXCHANGE_PUBLISHED_SCHEDULE)
    assert fee_requirement(adapter.market_snapshot(TOKEN, CONDITION), ".4", False) < Decimal(".03")
    with pytest.raises(ExchangeError, match="EXCHANGE_FEE_BOUND_EXCEEDS_OPERATOR_CAP"):
        prepare(adapter, fee_cap=".03")
    assert len(wire.calls) == 6 and all(x[0] == "GET" for x in wire.calls)


def test_maker_schedule_zero_requires_actual_post_only_signed_wire():
    chain = Chain()
    chain.fee = 0
    order = prepare(client(Wire(context()), chain, fee_policy=EXCHANGE_PUBLISHED_SCHEDULE),
                    order_type="GTD", post_only=True, price=".39", expiration=NOW+180, fee_cap="0")
    assert order["fee_bound"] == "0" and order["payload"]["postOnly"] is True


def test_prepared_fee_evidence_mutation_stops_before_http_post():
    wire = Wire(context())
    adapter = client(wire)
    order = prepare(adapter)
    order["fee_evidence"] = deepcopy(order["fee_evidence"])
    order["fee_evidence"]["fd"]["r"] = "0"
    with pytest.raises(ExchangeError, match="PREPARED_FEE_EVIDENCE_MUTATED"):
        adapter.submit(order)
    assert len(wire.calls) == 3


def test_evidence_builder_detaches_the_raw_payload():
    raw = {"r": ".05", "e": "1", "to": True}
    proof = make_fee_evidence(EXCHANGE_PUBLISHED_SCHEDULE, token=TOKEN, condition=CONDITION,
        exchange="fixture", observed_at=NOW, fd=raw, max_fee_bps=0,
        max_fee_block={"number": 100, "hash": BLOCK}, maker_base_fee_bps=0, taker_base_fee_bps=0)
    raw["r"] = "0"
    assert proof["fd"]["r"] == ".05"
