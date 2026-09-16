"""Read-only sample of the actual public production market/fee preflight.

This is a bounded supported-weather sample, not a complete semantic census or
account/host acceptance. No account, private signer or authenticated API is used.
RPC POST bodies use ChainReader's read-only allowlist only.
"""
from collections import deque
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import time

from polymarket_scanner.production.chain import (
    ChainReader, ExchangeError, JSONTransport, STANDARD_EXCHANGE, NEG_RISK_EXCHANGE,
)
from polymarket_scanner.production.exchange import GAMMA, PublicMarketReader, validate_buy_minimum
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


def _sample_candidates(transport, report, started, clock):
    """Collect a bounded sample before spending the book-attempt budget.

    One event's long bucket list must not starve every later supported event.
    This is deliberately a sample; truncation is reported, never called a census.
    """
    cursor, cursors, events, queues = None, set(), set(), deque()
    report["sample_discovery_truncated"] = False
    for page in range(MAX_SAMPLE_PAGES):
        if clock() - started > MAX_SAMPLE_SECONDS:
            raise ExchangeError("PUBLIC_MARKET_SAMPLE_TIME_CAP")
        params = {"active": "true", "closed": "false", "limit": 25,
                  "tag_slug": "daily-temperature"}
        if cursor:
            params["after_cursor"] = cursor
        status, envelope = transport.request("GET", GAMMA + "/events/keyset", params=params)
        if status != 200 or not isinstance(envelope, dict) or not isinstance(envelope.get("events"), list) or len(envelope["events"]) > 25 or "next_cursor" not in envelope:
            raise ExchangeError("PUBLIC_SAMPLE_DISCOVERY_INVALID")
        report["sample_pages"] = page + 1
        for event in envelope["events"]:
            if not isinstance(event, dict) or not event.get("id"):
                raise ExchangeError("PUBLIC_SAMPLE_DISCOVERY_INVALID")
            event_id = str(event["id"])
            if event_id in events:
                continue
            events.add(event_id)
            try:
                compiled = compile_strict_temperature_event(event)
                identity = strict_contract_identity(event, compiled)
            except StrictWeatherContractError as exc:
                code = str(exc.code)
                report["semantic_rejections"][code] = report["semantic_rejections"].get(code, 0) + 1
                continue
            if event.get("closed") is True or event.get("active") is False:
                continue
            if len(compiled.buckets) > 100:
                report["semantic_rejections"]["CONTRACT_BUCKET_CAP"] = report["semantic_rejections"].get("CONTRACT_BUCKET_CAP", 0) + 1
                continue
            # Central buckets tend to have useful books, but this is solely a
            # sampling order: no odds, weather thesis or profitability is inferred.
            ordered = sorted(enumerate(compiled.buckets),
                key=lambda pair: (abs(pair[0] - (len(compiled.buckets)-1)/2), pair[0]))
            choices = deque((bucket.condition_id, token) for _, bucket in ordered
                            if bucket.trade_open for token in (bucket.yes_token, bucket.no_token) if token)
            if choices:
                queues.append((event_id, identity, choices))
        next_cursor = envelope["next_cursor"]
        if next_cursor in (None, ""):
            return queues
        if not isinstance(next_cursor, str) or len(next_cursor) > 4096 or next_cursor in cursors or not envelope["events"]:
            raise ExchangeError("PUBLIC_SAMPLE_PAGINATION_INVALID")
        cursors.add(next_cursor)
        cursor = next_cursor
    report["sample_discovery_truncated"] = True
    return queues


def sample_market(transport, chain, report, *, clock=time.time):
    """Use the real public preflight, with one attempt per event per round."""
    started, attempts = clock(), 0
    reader = PublicMarketReader(fee_policy=EXCHANGE_PUBLISHED_SCHEDULE,
                                transport=transport, chain=chain, clock=clock)
    report["market_attempts"], report["semantic_rejections"] = [], {}
    queues = _sample_candidates(transport, report, started, clock)
    report["supported_events_in_sample"] = len(queues)
    while queues:
        if attempts >= MAX_MARKET_ATTEMPTS:
            raise ExchangeError("PUBLIC_MARKET_SAMPLE_ATTEMPT_CAP")
        if clock() - started > MAX_SAMPLE_SECONDS:
            raise ExchangeError("PUBLIC_MARKET_SAMPLE_TIME_CAP")
        event_id, identity, choices = queues.popleft()
        condition, token = choices.popleft()
        if choices:
            queues.append((event_id, identity, choices))
        attempts += 1
        row = {"event_id": event_id, "condition": condition,
               "token": token, "status": "FAILED"}
        report["market_attempts"].append(row)
        try:
            snapshot = reader.market_snapshot(token, condition)
            quantity, price, quoted = Decimal(0), None, False
            for level in sorted(snapshot["book"]["asks"], key=lambda x: Decimal(x["price"])):
                quantity += Decimal(level["size"])
                price = Decimal(level["price"])
                try:
                    validate_buy_minimum(snapshot, quantity, price)
                except ExchangeError as exc:
                    if str(exc) not in {"ORDER_SIZE_INVALID", "BUY_NOTIONAL_BELOW_CONSERVATIVE_MINIMUM"}:
                        raise
                else:
                    quoted = True
                    break
            # Missing liquidity skips an opportunity, not the fee probe. Label
            # this diagnostic input explicitly; never invent a quote.
            diagnostic_limit = price if quoted else Decimal(1) - Decimal(snapshot["tick_size"])
            policies = {policy: policy_result(snapshot, diagnostic_limit, policy)
                        for policy in (ONCHAIN_BOUND, EXCHANGE_PUBLISHED_SCHEDULE)}
            if not policies[EXCHANGE_PUBLISHED_SCHEDULE]["supported"]:
                raise ExchangeError("PUBLIC_SCHEDULE_POLICY_UNSUPPORTED")
            row.update(status="PUBLIC_MARKET_FEE_POLICY_VERIFIED",
                strict_contract=json.loads(json.dumps(identity)), tick_size=snapshot["tick_size"],
                min_order_size=snapshot["min_order_size"],
                minimum_buy_notional=snapshot["minimum_buy_notional"],
                minimum_size_policy=snapshot["minimum_size_policy"],
                minimum_order_age_seconds=snapshot["minimum_order_age_seconds"],
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
        finally:
            # These public whitelisted fields explain schema failures even when
            # the book is stale; no account or authenticated response is present.
            row["public_fee_diagnostics"] = reader.last_fee_diagnostics
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
