import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from polymarket_scanner.execution_certificate import (
    EXECUTION_CERTIFICATE_TTL_SECONDS,
    FEE_PRECISION_QUANTUM_USD,
    build_execution_certificate,
    validate_execution_certificate,
)
from polymarket_scanner.models import Book, Signal


class _Response:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


class _HTTP:
    def __init__(self, info):
        self.info = info

    async def get(self, url):
        return _Response(self.info)


class _Poly:
    def __init__(self, info, books):
        self.http = _HTTP(info)
        self._books = books

    async def books(self, token_ids):
        return {token: self._books[token] for token in token_ids if token in self._books}


def _signal():
    return Signal(
        detector="binary_buy_both",
        confidence="ACTIONABLE",
        event_id="e1",
        market_id="m1",
        title="test",
        detail="test",
        url="https://example.com",
        edge=0.05,
        entry_cost=0.95,
        theoretical_payout=1.0,
        token_ids=["yes", "no"],
        metadata={"certification_status": "BINARY_COMPLEMENT_VERIFIED"},
    )


def _raw_market():
    return {
        "id": "m1",
        "conditionId": "c1",
        "question": "Will the test happen?",
        "slug": "will-test-happen",
        "eventSlug": "test-event",
        "clobTokenIds": ["yes", "no"],
        "outcomes": ["Yes", "No"],
    }


def _info(*, rate="0.05", exponent=1, taker_only=True, delayed=False):
    return {
        "mts": "0.01",
        "mos": "5",
        "t": [
            {"t": "yes", "o": "Yes"},
            {"t": "no", "o": "No"},
        ],
        "fd": {"r": rate, "e": exponent, "to": taker_only},
        "itode": delayed,
    }


def _books():
    return {
        "yes": Book("yes", bids=[], asks=[(0.45, 100.0)], timestamp="server-a"),
        "no": Book("no", bids=[], asks=[(0.45, 100.0)], timestamp="server-a"),
    }


def test_build_and_validate_exact_clob_v2_certificate():
    signal = _signal()
    poly = _Poly(_info(), _books())
    cert = asyncio.run(build_execution_certificate(signal, poly, [_raw_market()]))
    signal.metadata["execution_certificate"] = cert

    ok, reason, derived = validate_execution_certificate(signal)
    assert ok is True, reason
    assert derived is not None
    # Raw e=1 fee is 0.012375/share. The certificate adds one full 0.00001
    # protocol precision quantum / 5-share minimum = 0.000002/share as a safe bound.
    assert Decimal(cert["legs"][0]["raw_fee_per_share"]) == Decimal("0.012375")
    assert Decimal(cert["legs"][0]["fee_rounding_pad_per_share"]) == FEE_PRECISION_QUANTUM_USD / Decimal("5")
    assert Decimal(cert["legs"][0]["fee_per_share"]) == Decimal("0.012377")
    assert derived["cost"] == Decimal("0.924754")
    assert derived["safe_common"] == Decimal("50")
    assert cert["legs"][0]["outcome"] == "Yes"
    assert cert["legs"][1]["outcome"] == "No"
    assert cert["legs"][0]["url"].startswith("https://polymarket.com/event/test-event")


def test_fee_bearing_exponent_two_matches_official_v2_curve_plus_rounding_bound():
    signal = _signal()
    poly = _Poly(_info(exponent=2), _books())
    cert = asyncio.run(build_execution_certificate(signal, poly, [_raw_market()]))
    signal.metadata["execution_certificate"] = cert

    assert cert["legs"][0]["fee_exponent"] == "2"
    assert Decimal(cert["legs"][0]["raw_fee_per_share"]) == Decimal("0.0030628125")
    assert Decimal(cert["legs"][0]["fee_per_share"]) == Decimal("0.0030648125")
    ok, reason, _ = validate_execution_certificate(signal)
    assert ok is True, reason


def test_fractional_fee_exponent_is_supported_and_self_validating():
    signal = _signal()
    poly = _Poly(_info(exponent="1.5"), _books())
    cert = asyncio.run(build_execution_certificate(signal, poly, [_raw_market()]))
    signal.metadata["execution_certificate"] = cert

    assert cert["legs"][0]["fee_exponent"] == "1.5"
    ok, reason, _ = validate_execution_certificate(signal)
    assert ok is True, reason


def test_zero_fee_market_gets_no_rounding_pad():
    signal = _signal()
    poly = _Poly(_info(rate="0", exponent=0), _books())
    cert = asyncio.run(build_execution_certificate(signal, poly, [_raw_market()]))
    signal.metadata["execution_certificate"] = cert

    assert Decimal(cert["legs"][0]["raw_fee_per_share"]) == 0
    assert Decimal(cert["legs"][0]["fee_rounding_pad_per_share"]) == 0
    assert Decimal(cert["legs"][0]["fee_per_share"]) == 0
    ok, reason, _ = validate_execution_certificate(signal)
    assert ok is True, reason


def test_taker_delay_fails_closed():
    signal = _signal()
    poly = _Poly(_info(delayed=True), _books())
    with pytest.raises(ValueError, match="taker-order delay"):
        asyncio.run(build_execution_certificate(signal, poly, [_raw_market()]))


def test_gamma_clob_outcome_disagreement_fails_closed():
    signal = _signal()
    info = _info()
    info["t"][1]["o"] = "Maybe"
    poly = _Poly(info, _books())
    with pytest.raises(ValueError, match="mapping disagrees"):
        asyncio.run(build_execution_certificate(signal, poly, [_raw_market()]))


def test_missing_leg_book_fails_closed():
    signal = _signal()
    books = _books()
    del books["no"]
    poly = _Poly(_info(), books)
    with pytest.raises(ValueError, match="order books are missing"):
        asyncio.run(build_execution_certificate(signal, poly, [_raw_market()]))


def test_tampered_fee_is_rejected_after_build():
    signal = _signal()
    poly = _Poly(_info(), _books())
    cert = asyncio.run(build_execution_certificate(signal, poly, [_raw_market()]))
    cert["legs"][0]["fee_per_share"] = "0"
    signal.metadata["execution_certificate"] = cert

    ok, reason, _ = validate_execution_certificate(signal)
    assert ok is False
    assert "arithmetic" in reason


def test_tampered_rounding_bound_is_rejected_after_build():
    signal = _signal()
    poly = _Poly(_info(), _books())
    cert = asyncio.run(build_execution_certificate(signal, poly, [_raw_market()]))
    cert["legs"][0]["fee_rounding_pad_per_share"] = "0"
    signal.metadata["execution_certificate"] = cert

    ok, reason, _ = validate_execution_certificate(signal)
    assert ok is False
    assert "rounding bound" in reason


def test_expired_certificate_is_rejected_after_build():
    signal = _signal()
    poly = _Poly(_info(), _books())
    cert = asyncio.run(build_execution_certificate(signal, poly, [_raw_market()]))
    signal.metadata["execution_certificate"] = cert

    checked = datetime.fromisoformat(cert["checked_at"])
    now = checked + timedelta(seconds=EXECUTION_CERTIFICATE_TTL_SECONDS + 0.1)
    ok, reason, _ = validate_execution_certificate(signal, now=now)
    assert ok is False
    assert "expired" in reason


def test_future_certificate_is_rejected():
    signal = _signal()
    poly = _Poly(_info(rate="0", exponent=0), _books())
    cert = asyncio.run(build_execution_certificate(signal, poly, [_raw_market()]))
    future = datetime.now(timezone.utc) + timedelta(minutes=5)
    cert["checked_at"] = future.isoformat()
    cert["expires_at"] = (future + timedelta(seconds=EXECUTION_CERTIFICATE_TTL_SECONDS)).isoformat()
    signal.metadata["execution_certificate"] = cert

    ok, reason, _ = validate_execution_certificate(signal)
    assert ok is False
    assert "future" in reason