"""Read-only sample of the actual public production market/fee preflight.

This is a bounded supported-weather sample, not a complete semantic census or
account/host acceptance. No account, private signer or authenticated API is used.
RPC POST bodies use ChainReader's read-only allowlist only.
"""
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import time

from polymarket_scanner.production.chain import (
    ChainReader, ExchangeError, JSONTransport, STANDARD_EXCHANGE, NEG_RISK_EXCHANGE,
)
from polymarket_scanner.production.exchange import GAMMA, PublicMarketReader
from polymarket_scanner.production.fees import (
    ONCHAIN_BOUND, EXCHANGE_PUBLISHED_SCHEDULE, fee_requirement, make_fee_evidence,
)
from polymarket_scanner.weather_only_contract_strict import (
    StrictWeatherContractError, compile_strict_temperature_event, strict_contract_identity,
)

RPC_ENDPOINTS = ("https://polygon.drpc.org", "https://polygon.publicnode.com",
                 "https://tenderly.rpc.polygon.community")
MAX_SAMPLE_PAGES = 4
MAX_MARKET_ATTEMPTS = 12
MAX_SAMPLE_SECONDS = 150


def policy_result(snapshot, price, policy):
    """Test both explicit policies against the same measured public evidence."""
    evidence = snapshot["fee_evidence"]
    candidate = dict(snapshot, fee_policy=policy, fee_evidence=make_fee_evidence(
        policy, **{key: evidence[key] for key in (
            "token", "condition", "exchange", "observed_at", "fd", "max_fee_bps",
            "max_fee_block", "maker_base_fee_bps", "taker_base_fee_bps")}))
    try:
        return {"supported": True, "required_fee_per_share": str(fee_requirement(
            candidate, price, False, expected_policy=policy))}
    except ExchangeError as exc:
        return {"supported": False, "reason": str(exc)}


def sample_market(transport, chain, report, *, clock=time.time):
    """Require a current strict-supported weather contract and fresh fee evidence.

    Pagination/attempt/time caps terminate with an explicit probe failure. This
    sample makes no claim of census completeness or an actionable weather edge.
    """
    started, cursor, seen, attempts = clock(), None, set(), 0
    reader = PublicMarketReader(fee_policy=EXCHANGE_PUBLISHED_SCHEDULE,
                                transport=transport, chain=chain, clock=clock)
    report["market_attempts"], report["semantic_rejections"] = [], {}
    for page in range(MAX_SAMPLE_PAGES):
        if clock() - started > MAX_SAMPLE_SECONDS:
            raise ExchangeError("PUBLIC_MARKET_SAMPLE_TIME_CAP")
        params = {"active": "true", "closed": "false", "limit": 25,
                  "tag_slug": "daily-temperature"}
        if cursor:
            params["after_cursor"] = cursor
        status, envelope = transport.request("GET", GAMMA + "/events/keyset", params=params)
        if status != 200 or not isinstance(envelope, dict) or not isinstance(envelope.get("events"), list) or "next_cursor" not in envelope:
            raise ExchangeError("PUBLIC_SAMPLE_DISCOVERY_INVALID")
        report["sample_pages"] = page + 1
        for event in envelope["events"]:
            if not isinstance(event, dict):
                raise ExchangeError("PUBLIC_SAMPLE_DISCOVERY_INVALID")
            try:
                compiled = compile_strict_temperature_event(event)
                identity = strict_contract_identity(event, compiled)
            except StrictWeatherContractError as exc:
                code = str(exc.code)
                report["semantic_rejections"][code] = report["semantic_rejections"].get(code, 0) + 1
                continue
            if event.get("closed") is True or event.get("active") is False:
                continue
            for bucket in compiled.buckets:
                if not bucket.trade_open:
                    continue
                for token in (bucket.yes_token, bucket.no_token):
                    if not token:
                        continue
                    if attempts >= MAX_MARKET_ATTEMPTS:
                        raise ExchangeError("PUBLIC_MARKET_SAMPLE_ATTEMPT_CAP")
                    if clock() - started > MAX_SAMPLE_SECONDS:
                        raise ExchangeError("PUBLIC_MARKET_SAMPLE_TIME_CAP")
                    attempts += 1
                    row = {"event_id": str(event.get("id")), "condition": bucket.condition_id,
                           "token": token, "status": "FAILED"}
                    report["market_attempts"].append(row)
                    try:
                        snapshot = reader.market_snapshot(token, bucket.condition_id)
                        quantity, price = Decimal(0), None
                        for level in sorted(snapshot["book"]["asks"], key=lambda x: Decimal(x["price"])):
                            quantity += Decimal(level["size"])
                            price = Decimal(level["price"])
                            if quantity >= Decimal(snapshot["min_order_size"]):
                                break
                        quoted = price is not None and quantity >= Decimal(snapshot["min_order_size"])
                        # Missing liquidity skips an opportunity, not the fee
                        # probe. Label this diagnostic input; never invent a quote.
                        diagnostic_limit = price if quoted else Decimal(1) - Decimal(snapshot["tick_size"])
                        policies = {policy: policy_result(snapshot, diagnostic_limit, policy)
                                    for policy in (ONCHAIN_BOUND, EXCHANGE_PUBLISHED_SCHEDULE)}
                        if not policies[EXCHANGE_PUBLISHED_SCHEDULE]["supported"]:
                            raise ExchangeError("PUBLIC_SCHEDULE_POLICY_UNSUPPORTED")
                        row.update(status="PUBLIC_MARKET_FEE_POLICY_VERIFIED",
                            strict_contract=json.loads(json.dumps(identity)), tick_size=snapshot["tick_size"],
                            min_order_size=snapshot["min_order_size"],
                            quote_price_for_minimum_size=str(price) if quoted else None,
                            quote_status="AVAILABLE" if quoted else "NO_EXECUTABLE_MINIMUM_SIZE",
                            fee_check_buy_limit=str(diagnostic_limit), quoted_depth=str(quantity),
                            book_timestamp=snapshot["book_timestamp"],
                            received_at=snapshot["received_at"], exchange=snapshot["exchange"],
                            fee_evidence=snapshot["fee_evidence"], fee_policies=policies)
                        report["status"] = "PUBLIC_MARKET_FEE_POLICY_VERIFIED"
                        return
                    except ExchangeError as exc:
                        row["reason"] = str(exc)
        next_cursor = envelope["next_cursor"]
        if next_cursor in (None, ""):
            break
        if not isinstance(next_cursor, str) or len(next_cursor) > 4096 or next_cursor in seen or not envelope["events"]:
            raise ExchangeError("PUBLIC_SAMPLE_PAGINATION_INVALID")
        seen.add(next_cursor)
        cursor = next_cursor
    raise ExchangeError("NO_SUPPORTED_PUBLIC_MARKET_IN_BOUNDED_SAMPLE")


def run(output=Path("production-public-execution.json")):
    report = {"git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "observed_at": time.time(), "status": "FAILED", "authenticated_calls": 0,
              "financial_mutations": 0, "providers": [],
              "scope": "BOUNDED_STRICT_WEATHER_PUBLIC_MARKET_FEE_SAMPLE",
              "account_host_weather_edge_and_operator_limits_verified": False}
    verified = False
    try:
        for endpoint in RPC_ENDPOINTS:
            transport = JSONTransport()
            reader = ChainReader(transport=transport, rpc_url=endpoint)
            row = {"endpoint": endpoint, "status": "FAILED"}
            report["providers"].append(row)
            try:
                final = reader.block("finalized")
                latest = reader.block("latest")
                fees = {exchange: reader.call_uint(exchange, "getMaxFeeRate()", [], [], block=hex(latest["number"]))
                        for exchange in (STANDARD_EXCHANGE, NEG_RISK_EXCHANGE)}
                row.update(status="PUBLIC_READ_VERIFIED", finalized=final, latest=latest,
                           maximum_fee_bps=fees,
                           zero_maximum_means="NO_ONCHAIN_FEE_BOUND_NOT_ZERO_FEE")
                verified = True
                sample_market(transport, reader, report)
                break
            except ExchangeError as exc:
                row["error"] = str(exc)
                if verified:
                    # Another RPC cannot fix missing/unsupported CLOB evidence.
                    raise
            finally:
                transport.close()
        if not verified:
            raise ExchangeError("PUBLIC_RPC_READINESS_NOT_VERIFIED")
    except ExchangeError as exc:
        report["error"] = str(exc)
    finally:
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps(report, sort_keys=True))
    if report["status"] != "PUBLIC_MARKET_FEE_POLICY_VERIFIED":
        raise SystemExit(report.get("error", "PUBLIC_EXECUTION_PREFLIGHT_NOT_VERIFIED"))
    return report


if __name__ == "__main__":
    run()
