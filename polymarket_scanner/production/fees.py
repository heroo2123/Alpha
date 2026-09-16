"""Pure, explicit BUY fee policies; these are not signed exchange fee ceilings.

The published schedule formula is the official SDK 0.10.0 platform fee:
shares * fd.r * (price * (1-price)) ** fd.e.  The schedule policy relies on the
venue applying its published curve and five-decimal/minimum-fee convention.
It cannot bind future fee changes or override an actual confirmed chain fee.
"""
from __future__ import annotations

from decimal import Decimal, localcontext, ROUND_CEILING
import hashlib
import json

from .chain import ExchangeError, number, uint

ONCHAIN_BOUND = "ONCHAIN_BOUND"
EXCHANGE_PUBLISHED_SCHEDULE = "EXCHANGE_PUBLISHED_SCHEDULE"
FEE_POLICIES = frozenset((ONCHAIN_BOUND, EXCHANGE_PUBLISHED_SCHEDULE))
FEE_SOURCE = "https://clob.polymarket.com/clob-markets/"


def validate_policy(policy: object) -> str:
    if not isinstance(policy, str) or policy not in FEE_POLICIES:
        raise ExchangeError("FEE_POLICY_REQUIRED_OR_UNSUPPORTED")
    return policy


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError):
        raise ExchangeError("FEE_EVIDENCE_INVALID") from None


def make_fee_evidence(policy: str, *, token: str, condition: str, exchange: str,
                      observed_at, fd: dict, max_fee_bps: int, max_fee_block: dict,
                      maker_base_fee_bps, taker_base_fee_bps) -> dict:
    """Detach raw evidence from mutable responses and give it a stable audit ID.

    The digest establishes identity for persisted evidence, not independent
    provenance or an exchange signature. The public reader supplies provenance.
    """
    evidence = {"version": 1, "policy": validate_policy(policy),
                "source": FEE_SOURCE + condition, "token": token,
                "condition": condition, "exchange": exchange,
                "observed_at": observed_at, "fd": fd,
                "maker_base_fee_bps": maker_base_fee_bps,
                "taker_base_fee_bps": taker_base_fee_bps,
                "max_fee_bps": max_fee_bps, "max_fee_block": max_fee_block}
    encoded = _canonical(evidence)
    result = json.loads(encoded)
    result["sha256"] = hashlib.sha256(encoded).hexdigest()
    return result


def fee_requirement(snapshot: dict, limit_price, post_only: bool,
                    *, expected_policy: str | None = None) -> Decimal:
    """Return a conservative collateral-per-share submission requirement.

    For schedule fees the maximum spans EVERY possible BUY fill price up to
    the limit, including price improvement toward .5. A factor of two covers
    the documented five-decimal fee quantization for arbitrary partial fills:
    a raw fragment below the minimum rounds to zero; otherwise rounding to an
    adjacent quantum adds at most one quantum, which is at most the raw fee.
    This also covers ordinary nearest rounding at half a quantum. No exact
    tie-breaking rule, minimum fragment size or fragment count is assumed.
    This is conditional on the published rounding convention, not an onchain
    guarantee. The engine reserves the operator's full configured fee cap and
    must preserve/fault on every actual fill that breaches it.
    """
    if not isinstance(snapshot, dict) or type(post_only) is not bool:
        raise ExchangeError("FEE_EVIDENCE_INVALID")
    policy = validate_policy(snapshot.get("fee_policy"))
    if expected_policy is not None and policy != validate_policy(expected_policy):
        raise ExchangeError("FEE_POLICY_MISMATCH")
    evidence = snapshot.get("fee_evidence")
    if not isinstance(evidence, dict):
        raise ExchangeError("FEE_EVIDENCE_MISSING")
    raw = dict(evidence)
    digest = raw.pop("sha256", None)
    if digest != hashlib.sha256(_canonical(raw)).hexdigest():
        raise ExchangeError("FEE_EVIDENCE_MUTATED")
    if evidence.get("version") != 1 or evidence.get("policy") != policy:
        raise ExchangeError("FEE_POLICY_MISMATCH")
    for field in ("token", "condition", "exchange", "max_fee_bps"):
        if field not in snapshot or evidence.get(field) != snapshot[field]:
            raise ExchangeError("FEE_EVIDENCE_IDENTITY_MISMATCH")
    if evidence.get("source") != FEE_SOURCE + str(snapshot["condition"]):
        raise ExchangeError("FEE_SOURCE_UNSUPPORTED")
    if number(evidence.get("observed_at"), positive=True) != number(snapshot.get("received_at"), positive=True):
        raise ExchangeError("FEE_EVIDENCE_TIME_MISMATCH")
    block = evidence.get("max_fee_block")
    if not isinstance(block, dict) or not isinstance(block.get("hash"), str) or not block["hash"]:
        raise ExchangeError("FEE_CHAIN_EVIDENCE_MISSING")
    uint(block.get("number"))
    limit = number(limit_price, positive=True)
    if limit >= 1:
        raise ExchangeError("FEE_PRICE_INVALID")
    fee = evidence.get("fd")
    if not isinstance(fee, dict) or set(fee) != {"r", "e", "to"} or type(fee["to"]) is not bool:
        raise ExchangeError("FEE_EVIDENCE_MISSING")
    rate, exponent = number(fee["r"]), number(fee["e"])
    if rate > 1 or exponent > 4 or exponent != exponent.to_integral_value():
        raise ExchangeError("FEE_CURVE_UNSUPPORTED")
    maximum = uint(evidence.get("max_fee_bps"))
    if maximum >= 10_000:
        raise ExchangeError("EXCHANGE_FEE_MAXIMUM_INVALID")
    # The official SDK 0.10.0 MarketInfo and BUY budget calculation read the
    # platform rate/exponent from fd only. mbf/tbf are retained bps metadata;
    # there is no SDK additive term for them. Requiring zero invents a fee
    # policy that rejects ordinary fee-enabled markets. Keep their documented
    # nonnegative int64 shape and evidence, without guessing a composition or
    # treating this metadata as a contractual ceiling.
    for field in ("maker_base_fee_bps", "taker_base_fee_bps"):
        if uint(evidence.get(field)) >= 2**63:
            raise ExchangeError("FEE_METADATA_INVALID")
    with localcontext() as context:
        context.prec = 80
        context.rounding = ROUND_CEILING
        if policy == ONCHAIN_BOUND:
            if maximum == 0:
                raise ExchangeError("UNBOUNDED_EXCHANGE_FEE")
            return limit * maximum / Decimal(10_000)
        if fee["to"] is not True:
            raise ExchangeError("FEE_SCOPE_UNSUPPORTED")
        if post_only:
            return Decimal(0)
        peak = min(limit, Decimal("0.5"))
        return Decimal(2) * rate * (peak * (Decimal(1) - peak)) ** int(exponent)
