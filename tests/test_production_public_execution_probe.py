"""Public readiness uses real parsers/fee checks, with all HTTP/RPC mocked."""
from datetime import date
import json

import pytest

from polymarket_scanner.production.chain import ExchangeError, STANDARD_EXCHANGE, NEG_RISK_EXCHANGE
from polymarket_scanner.production.fees import ONCHAIN_BOUND, EXCHANGE_PUBLISHED_SCHEDULE
from tools import production_public_execution_probe as probe
from test_production_exchange import CONDITION, NOW, TOKEN, Chain, Wire, context
from test_weather_final_gpt6_exact_replays import _event


def event():
    value = _event(target=date(2026, 9, 16))
    for index, market in enumerate(value["markets"]):
        market["conditionId"] = CONDITION if index == 0 else "0x" + f"{index:02x}" * 32
        market["clobTokenIds"] = [str(int(TOKEN) + index*2), str(int(TOKEN) + index*2+1)]
    return value


def setup_probe(monkeypatch, *, quotes=True, change=None):
    public = context()
    if not quotes:
        public[2][1]["asks"] = []
    if change:
        change(public)
    wire = Wire([(200, {"events": [event()], "next_cursor": None}), *public])
    chain = Chain()
    chain.fee = 0
    monkeypatch.setattr(probe, "JSONTransport", lambda: wire)
    monkeypatch.setattr(probe, "ChainReader", lambda **kwargs: chain)
    monkeypatch.setattr(probe.subprocess, "check_output", lambda *args, **kwargs: "fixture-reviewed-sha\n")
    actual = probe.sample_market
    monkeypatch.setattr(probe, "sample_market", lambda transport, reader, report: actual(transport, reader, report, clock=lambda: NOW))
    return wire


def test_full_public_probe_uses_real_strict_market_and_fee_validation_with_zero_caps(monkeypatch, tmp_path):
    wire = setup_probe(monkeypatch)
    output = tmp_path / "public.json"
    report = probe.run(output)
    assert json.loads(output.read_text()) == report
    assert report["git_sha"] == "fixture-reviewed-sha"
    assert report["authenticated_calls"] == report["financial_mutations"] == 0
    assert report["account_host_weather_edge_and_operator_limits_verified"] is False
    assert report["providers"][0]["maximum_fee_bps"] == {STANDARD_EXCHANGE: 0, NEG_RISK_EXCHANGE: 0}
    row = report["market_attempts"][0]
    assert row["status"] == "PUBLIC_MARKET_FEE_POLICY_VERIFIED"
    assert row["strict_contract"]["station"] == "KLGA"
    assert row["fee_evidence"]["fd"] == {"r": ".05", "e": "1", "to": True}
    assert row["fee_evidence"]["max_fee_bps"] == 0
    assert row["fee_policies"][ONCHAIN_BOUND] == {"supported": False, "reason": "UNBOUNDED_EXCHANGE_FEE"}
    assert row["fee_policies"][EXCHANGE_PUBLISHED_SCHEDULE]["supported"] is True
    assert row["quote_status"] == "AVAILABLE" and row["quote_price_for_minimum_size"] == "0.40"
    assert len(wire.calls) == 4
    assert all(call[0] == "GET" and not call[2].get("headers") for call in wire.calls)


def test_no_liquidity_is_reported_as_quote_skip_without_inventing_quote(monkeypatch, tmp_path):
    setup_probe(monkeypatch, quotes=False)
    report = probe.run(tmp_path / "public.json")
    row = report["market_attempts"][0]
    assert row["quote_status"] == "NO_EXECUTABLE_MINIMUM_SIZE"
    assert row["quote_price_for_minimum_size"] is None
    assert row["fee_check_buy_limit"] == "0.99"
    assert row["fee_policies"][EXCHANGE_PUBLISHED_SCHEDULE]["required_fee_per_share"] == "0.0250"


@pytest.mark.parametrize("change,code", [
    (lambda x: x[1][1].pop("fd"), "FEE_EVIDENCE_MISSING"),
    (lambda x: x[1][1].pop("mbf"), "INVALID_UINT"),
    (lambda x: x[1][1].update(tbf=100), "BASE_FEE_COMPOSITION_UNSUPPORTED"),
    (lambda x: x[2][1].update(timestamp=str((NOW-20)*1000)), "BOOK_STALE"),
])
def test_missing_or_unreviewed_fees_and_stale_books_do_not_pass_public_gate(monkeypatch, tmp_path, change, code):
    wire = setup_probe(monkeypatch, change=change)
    monkeypatch.setattr(probe, "MAX_MARKET_ATTEMPTS", 1)
    output = tmp_path / "failed.json"
    with pytest.raises(SystemExit, match="PUBLIC_MARKET_SAMPLE_ATTEMPT_CAP"):
        probe.run(output)
    report = json.loads(output.read_text())
    assert report["status"] == "FAILED"
    assert report["market_attempts"][0]["reason"] == code
    assert all(call[0] == "GET" for call in wire.calls)


def test_unsupported_weather_is_not_accepted_for_fee_coverage():
    bad = event()
    bad["description"] = "Future unreviewed resolution template"
    report = {}
    with pytest.raises(ExchangeError, match="NO_SUPPORTED_PUBLIC_MARKET"):
        probe.sample_market(Wire([(200, {"events": [bad], "next_cursor": None})]), Chain(), report, clock=lambda: NOW)
    assert sum(report["semantic_rejections"].values()) == 1
    assert report["market_attempts"] == []


def test_incomplete_public_discovery_does_not_become_complete_empty_census():
    with pytest.raises(ExchangeError, match="PUBLIC_SAMPLE_DISCOVERY_INVALID"):
        probe.sample_market(Wire([(200, {"events": []})]), Chain(), {}, clock=lambda: NOW)
