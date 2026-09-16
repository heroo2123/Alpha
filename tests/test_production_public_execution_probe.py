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
        market["conditionId"] = CONDITION if index == 1 else "0x" + f"{index:02x}" * 32
        offset = 0 if index == 1 else (index+1)*2
        market["clobTokenIds"] = [str(int(TOKEN) + offset), str(int(TOKEN) + offset+1)]
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
    (lambda x: x[1][1].update(tbf=-1), "INVALID_UINT"),
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


@pytest.mark.parametrize("split_pages", [False, True])
def test_stale_first_event_cannot_monopolize_attempts_and_starve_fresh_later_event(monkeypatch, split_pages):
    first, second = event(), event()
    second["id"] = "later-event"
    second["slug"] = "later-event"
    condition, token = "0x" + "45"*32, str(int(TOKEN)+1000)
    second["markets"][1].update(conditionId=condition, clobTokenIds=[token, str(int(token)+1)])
    stale, fresh = context(), context()
    stale[2][1]["timestamp"] = str((NOW-30)*1000)
    fresh[0][1][0].update(conditionId=condition, clobTokenIds=[token, str(int(token)+1)])
    fresh[1][1].update(t=[{"t": token}], mbf=1000, tbf=1000, oas=180)
    fresh[2][1].update(asset_id=token, market=condition)
    discovery = [(200, {"events": [first, second], "next_cursor": None})]
    if split_pages:
        discovery = [(200, {"events": [first], "next_cursor": "next"}),
                     (200, {"events": [second], "next_cursor": None})]
    wire = Wire(discovery + stale + fresh)
    monkeypatch.setattr(probe, "MAX_MARKET_ATTEMPTS", 2)
    chain = Chain()
    chain.fee = 0
    report = {}
    probe.sample_market(wire, chain, report, clock=lambda: NOW)
    assert report["supported_events_in_sample"] == 2
    attempts = report["market_attempts"]
    assert [row["event_id"] for row in attempts] == [first["id"], "later-event"]
    assert attempts[0]["reason"] == "BOOK_STALE"
    assert attempts[0]["public_fee_diagnostics"]["clob"]["fd"]["r"] == ".05"
    assert attempts[1]["status"] == "PUBLIC_MARKET_FEE_POLICY_VERIFIED"
    assert attempts[1]["public_fee_diagnostics"]["clob"] == {
        "fd": {"r": ".05", "e": "1", "to": True}, "mbf": 1000, "tbf": 1000,
        "mos": "5", "mts": ".01", "oas": 180}
    assert attempts[1]["minimum_order_age_seconds"] == 180
    assert attempts[1]["fee_policies"][EXCHANGE_PUBLISHED_SCHEDULE]["required_fee_per_share"] == "0.024000"
    assert all(call[0] == "GET" for call in wire.calls)


def test_public_numeric_diagnostics_retained_on_failed_validation_and_whitelisted(monkeypatch, tmp_path):
    def change(source):
        source[0][1][0].update(feesEnabled=True, feeType="weather_fees", feeSchedule={
            "rate": .05, "exponent": 1, "takerOnly": True, "rebateRate": .25,
            "irrelevant_field": "NEVER_EXPORT"}, extra="NEVER_EXPORT")
        source[1][1].update(mbf=1000, tbf=1000, oas=180, extra="NEVER_EXPORT")
        source[2][1]["timestamp"] = str((NOW-30)*1000)
    setup_probe(monkeypatch, change=change)
    monkeypatch.setattr(probe, "MAX_MARKET_ATTEMPTS", 1)
    output = tmp_path / "failed.json"
    with pytest.raises(SystemExit):
        probe.run(output)
    report = json.loads(output.read_text())
    diag = report["market_attempts"][0]["public_fee_diagnostics"]
    assert diag["clob"]["mbf"] == diag["clob"]["tbf"] == 1000
    assert diag["gamma"]["feesEnabled"] is True
    assert diag["gamma"]["feeSchedule"]["rate"] == .05
    assert diag["observed_at"] == NOW and diag["condition"] == CONDITION
    assert "NEVER_EXPORT" not in output.read_text()


def test_public_probe_share_depth_without_conservative_notional_is_quote_skip(monkeypatch, tmp_path):
    def change(source):
        source[2][1]["asks"] = [{"price": ".4", "size": "5"}]
    setup_probe(monkeypatch, change=change)
    report = probe.run(tmp_path / "minimum.json")
    row = report["market_attempts"][0]
    assert row["quote_status"] == "NO_EXECUTABLE_MINIMUM_SIZE"
    assert row["quote_price_for_minimum_size"] is None
    assert row["min_order_size"] == row["minimum_buy_notional"] == "5"
    assert row["minimum_size_policy"] == "REQUIRE_BOTH_SHARES_AND_BUY_NOTIONAL"
    assert row["fee_policies"][EXCHANGE_PUBLISHED_SCHEDULE]["supported"] is True


def test_public_probe_walks_depth_until_both_share_and_notional_minima_fit(monkeypatch, tmp_path):
    def change(source):
        source[2][1]["asks"] = [{"price": ".4", "size": "5"}, {"price": ".5", "size": "5"}]
    setup_probe(monkeypatch, change=change)
    report = probe.run(tmp_path / "minimum.json")
    row = report["market_attempts"][0]
    assert row["quote_status"] == "AVAILABLE"
    assert row["quote_price_for_minimum_size"] == "0.5"
    assert row["quoted_depth"] == "10"
