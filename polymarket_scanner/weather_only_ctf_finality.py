from __future__ import annotations

"""Read-only finalized Polymarket CTF payout evidence for paper settlement.

A Gamma market being ``closed`` and its displayed ``outcomePrices`` are not economic
settlement authority.  Polymarket outcome tokens are Conditional Tokens Framework
positions on Polygon.  ``payoutDenominator(conditionId) > 0`` is the on-chain
resolution marker and ``payoutNumerators(conditionId, i) / denominator`` is the
final payout for outcome slot ``i``.

This module performs only public JSON-RPC ``eth_call`` and ``eth_blockNumber`` reads.
It has no wallet, private key, transaction, order, cancel, approval or redemption
surface.  By default two independent public Polygon RPC endpoints must agree on the
complete binary payout vector before a result is accepted.  A caller may inject
endpoints for tests or operations; one-endpoint operation is deliberately rejected.
"""

import asyncio
import hashlib
import json
import math
import os
import time
from dataclasses import asdict, dataclass
from urllib.parse import urlparse

import httpx


CTF_FINALITY_VERSION = "weather_ctf_finality_v1_two_rpc_final_payout_vector"
POLYGON_CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
PAYOUT_DENOMINATOR_SELECTOR = "dd34de67"
PAYOUT_NUMERATOR_SELECTOR = "0504c814"
DEFAULT_POLYGON_RPC_URLS = (
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon-rpc.com",
)


class WeatherCTFFinalityError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class CTFFinalityEvidence:
    version: str
    condition_id: str
    payout_denominator: int
    payout_numerators: tuple[int, int]
    payout_vector: tuple[float, float]
    rpc_hosts: tuple[str, ...]
    rpc_block_numbers: tuple[int, ...]
    observed_at: float
    evidence_sha256: str
    finalized: bool
    settlement_authority: bool
    financial_authority: bool

    def as_dict(self) -> dict:
        return asdict(self)


def _condition_id(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text.startswith("0x") or len(text) != 66:
        raise WeatherCTFFinalityError("CTF_CONDITION_ID_INVALID")
    try:
        int(text[2:], 16)
    except ValueError:
        raise WeatherCTFFinalityError("CTF_CONDITION_ID_INVALID") from None
    return text


def _rpc_urls(value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    if value is None:
        env = str(os.getenv("WEATHER_PAPER_POLYGON_RPC_URLS") or "").strip()
        raw = tuple(part.strip() for part in env.split(",") if part.strip()) if env else DEFAULT_POLYGON_RPC_URLS
    else:
        raw = tuple(str(part).strip() for part in value if str(part).strip())
    out: list[str] = []
    hosts: set[str] = set()
    for url in raw:
        try:
            parsed = urlparse(url)
        except Exception:
            raise WeatherCTFFinalityError("CTF_RPC_URL_INVALID") from None
        host = (parsed.hostname or "").lower()
        if parsed.scheme.lower() != "https" or not host:
            raise WeatherCTFFinalityError("CTF_RPC_URL_INVALID")
        if host in hosts:
            continue
        hosts.add(host)
        out.append(url.rstrip("/"))
    if len(out) < 2:
        raise WeatherCTFFinalityError("CTF_INDEPENDENT_RPC_QUORUM_REQUIRED")
    return tuple(out)


def _word(value: int) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WeatherCTFFinalityError("CTF_ABI_ARGUMENT_INVALID")
    return f"{value:064x}"


def _denominator_call(condition_id: str) -> str:
    return "0x" + PAYOUT_DENOMINATOR_SELECTOR + condition_id[2:]


def _numerator_call(condition_id: str, index: int) -> str:
    return "0x" + PAYOUT_NUMERATOR_SELECTOR + condition_id[2:] + _word(index)


def _decode_uint256(value: object, code: str) -> int:
    text = str(value or "").strip().lower()
    if not text.startswith("0x") or len(text) > 66:
        raise WeatherCTFFinalityError(code)
    try:
        number = int(text[2:] or "0", 16)
    except ValueError:
        raise WeatherCTFFinalityError(code) from None
    if number < 0 or number >= 2**256:
        raise WeatherCTFFinalityError(code)
    return number


def _digest(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class PolygonCTFFinalityClient:
    def __init__(
        self,
        *,
        rpc_urls: tuple[str, ...] | list[str] | None = None,
        timeout_seconds: float = 12.0,
    ) -> None:
        self.rpc_urls = _rpc_urls(rpc_urls)
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout) or not 2.0 <= timeout <= 30.0:
            raise ValueError("timeout_seconds must be in 2..30")
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(8.0, timeout)),
            limits=httpx.Limits(max_connections=max(4, 2 * len(self.rpc_urls)), max_keepalive_connections=len(self.rpc_urls)),
            trust_env=False,
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def _rpc(self, url: str, method: str, params: list) -> object:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        try:
            response = await self.http.post(url, json=payload)
        except httpx.HTTPError:
            raise WeatherCTFFinalityError("CTF_RPC_TRANSPORT") from None
        if response.status_code != 200:
            raise WeatherCTFFinalityError("CTF_RPC_HTTP_ERROR")
        try:
            body = response.json()
        except ValueError:
            raise WeatherCTFFinalityError("CTF_RPC_RESPONSE_INVALID") from None
        if not isinstance(body, dict) or body.get("error") is not None or "result" not in body:
            raise WeatherCTFFinalityError("CTF_RPC_RESPONSE_INVALID")
        return body.get("result")

    async def _endpoint_vector(self, url: str, condition_id: str) -> dict:
        host = (urlparse(url).hostname or "").lower()
        denominator_raw, numerator0_raw, numerator1_raw, block_raw = await asyncio.gather(
            self._rpc(url, "eth_call", [{"to": POLYGON_CTF_ADDRESS, "data": _denominator_call(condition_id)}, "latest"]),
            self._rpc(url, "eth_call", [{"to": POLYGON_CTF_ADDRESS, "data": _numerator_call(condition_id, 0)}, "latest"]),
            self._rpc(url, "eth_call", [{"to": POLYGON_CTF_ADDRESS, "data": _numerator_call(condition_id, 1)}, "latest"]),
            self._rpc(url, "eth_blockNumber", []),
        )
        denominator = _decode_uint256(denominator_raw, "CTF_DENOMINATOR_INVALID")
        numerators = (
            _decode_uint256(numerator0_raw, "CTF_NUMERATOR_INVALID"),
            _decode_uint256(numerator1_raw, "CTF_NUMERATOR_INVALID"),
        )
        block = _decode_uint256(block_raw, "CTF_BLOCK_NUMBER_INVALID")
        return {
            "host": host,
            "denominator": denominator,
            "numerators": numerators,
            "block": block,
        }

    async def finalized_binary_payout(self, condition_id: object) -> CTFFinalityEvidence | None:
        condition = _condition_id(condition_id)
        results = await asyncio.gather(
            *(self._endpoint_vector(url, condition) for url in self.rpc_urls),
            return_exceptions=True,
        )
        if any(isinstance(result, BaseException) for result in results):
            raise WeatherCTFFinalityError("CTF_RPC_QUORUM_UNAVAILABLE")
        rows = [dict(result) for result in results if isinstance(result, dict)]
        if len(rows) != len(self.rpc_urls):
            raise WeatherCTFFinalityError("CTF_RPC_QUORUM_UNAVAILABLE")
        signatures = {(row["denominator"], tuple(row["numerators"])) for row in rows}
        if len(signatures) != 1:
            raise WeatherCTFFinalityError("CTF_RPC_PAYOUT_DISAGREEMENT")
        denominator, numerators = next(iter(signatures))
        denominator = int(denominator)
        numerators = tuple(int(value) for value in numerators)
        if denominator == 0:
            return None
        if denominator < 0 or len(numerators) != 2:
            raise WeatherCTFFinalityError("CTF_PAYOUT_VECTOR_INVALID")
        if any(value < 0 or value > denominator for value in numerators):
            raise WeatherCTFFinalityError("CTF_PAYOUT_VECTOR_INVALID")
        if sum(numerators) != denominator:
            raise WeatherCTFFinalityError("CTF_PAYOUT_VECTOR_INVALID")
        payout = tuple(value / denominator for value in numerators)
        observed = time.time()
        evidence_payload = {
            "version": CTF_FINALITY_VERSION,
            "contract": POLYGON_CTF_ADDRESS.lower(),
            "condition_id": condition,
            "denominator": denominator,
            "numerators": numerators,
            "rpc_hosts": tuple(row["host"] for row in rows),
            "rpc_blocks": tuple(int(row["block"]) for row in rows),
        }
        return CTFFinalityEvidence(
            version=CTF_FINALITY_VERSION,
            condition_id=condition,
            payout_denominator=denominator,
            payout_numerators=numerators,
            payout_vector=(float(payout[0]), float(payout[1])),
            rpc_hosts=tuple(row["host"] for row in rows),
            rpc_block_numbers=tuple(int(row["block"]) for row in rows),
            observed_at=observed,
            evidence_sha256=_digest(evidence_payload),
            finalized=True,
            settlement_authority=True,
            financial_authority=False,
        )
