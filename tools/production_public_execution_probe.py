"""Public read-only exchange contract readiness; never loads an account or signer.

RPC providers are listed by https://docs.polygon.technology/pos/reference/rpc-endpoints
All JSON-RPC POST bodies are restricted to ChainReader's read-only method allowlist.
"""
import json
from pathlib import Path
import subprocess
import time

from polymarket_scanner.production.chain import ChainReader, ExchangeError, JSONTransport, STANDARD_EXCHANGE, NEG_RISK_EXCHANGE


def run(output=Path("production-public-execution.json")):
    report = {"git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "observed_at": time.time(), "authenticated_calls": 0, "financial_mutations": 0, "providers": []}
    for endpoint in ("https://polygon.drpc.org", "https://polygon.publicnode.com", "https://tenderly.rpc.polygon.community"):
        transport = JSONTransport()
        reader = ChainReader(transport=transport, rpc_url=endpoint)
        row = {"endpoint": endpoint, "status": "FAILED"}
        try:
            final = reader.block("finalized")
            latest = reader.block("latest")
            fees = {exchange: reader.call_uint(exchange, "getMaxFeeRate()", [], [], block=hex(latest["number"]))
                    for exchange in (STANDARD_EXCHANGE, NEG_RISK_EXCHANGE)}
            row.update(status="PUBLIC_READ_VERIFIED", finalized=final, latest=latest, maximum_fee_bps=fees,
                       execution_fee_policy_supported=any(0 < value < 10000 for value in fees.values()))
            report["providers"].append(row)
            break
        except ExchangeError as exc:
            row["error"] = str(exc)
            report["providers"].append(row)
        finally:
            transport.close()
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))
    if not any(row["status"] == "PUBLIC_READ_VERIFIED" for row in report["providers"]):
        raise SystemExit("PUBLIC_RPC_READINESS_NOT_VERIFIED")
    if not any(row.get("execution_fee_policy_supported") is True for row in report["providers"]):
        raise SystemExit("NO_APPROVED_EXCHANGE_HAS_A_BOUNDED_FEE_POLICY")


if __name__ == "__main__":
    run()
