"""Bounded GET-only collection; each successful provider capture commits first.

No auth, POST, Telegram, wallet, trading endpoint or retry of financial actions.
Endpoint allowlisting is deliberately narrow for the first observation pilot.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import re
import time
from typing import Callable
from urllib.parse import urlsplit
from email.utils import parsedate_to_datetime

import httpx

from .evidence import EvidenceError, EvidenceStore, KINDS, canonical, finite, identity


ALLOWED_GET_ENDPOINTS = {
    ("gamma-api.polymarket.com", "/events"),
    ("gamma-api.polymarket.com", "/events/keyset"),
    ("clob.polymarket.com", "/book"),
    ("aviationweather.gov", "/api/data/metar"),
    ("madis-data.ncep.noaa.gov", "/madisPublic1/cgi-bin/madisXmlPublicDir"),
}


@dataclass(frozen=True)
class SourceRequest:
    provider: str
    url: str
    event_id: str
    kind: str
    source_identity: str
    revision: str
    params: tuple[tuple[str, str], ...] = ()
    response_format: str = "JSON"

    def __post_init__(self):
        url = urlsplit(self.url)
        reward_market = url.hostname == "gamma-api.polymarket.com" and re.fullmatch(r"/markets/[0-9]{1,32}", url.path)
        if (url.scheme != "https" or url.username or url.password or url.query or url.fragment
                or url.port not in (None, 443) or not (reward_market or (url.hostname, url.path) in ALLOWED_GET_ENDPOINTS)):
            raise EvidenceError("PUBLIC_GET_ENDPOINT_NOT_REVIEWED")
        identity(self.provider)
        for value in (self.event_id, self.source_identity, self.revision):
            identity(value)
        if self.kind not in KINDS - {"LABEL", "FEATURES"}:
            raise EvidenceError("PUBLIC_SOURCE_KIND_INVALID")
        if len(self.params) > 24 or any(len(k) > 64 or len(v) > 512 for k, v in self.params):
            raise EvidenceError("REQUEST_PARAMS_LIMIT")
        if len(dict(self.params)) != len(self.params):
            raise EvidenceError("DUPLICATE_REQUEST_PARAMS")
        canonical(dict(self.params))
        if url.hostname == 'gamma-api.polymarket.com' and url.path == '/events/keyset':
            params = dict(self.params)
            if (self.provider != 'GAMMA_DISCOVERY' or self.kind != 'RULES'
                    or self.event_id != 'v11-discovery-catalog'
                    or set(params)-{'closed','limit','after_cursor'}
                    or params.get('closed') != 'false' or not re.fullmatch(r'[1-9][0-9]{0,2}', params.get('limit',''))
                    or int(params['limit']) > 100 or 'after_cursor' in params and not params['after_cursor']):
                raise EvidenceError('DISCOVERY_EXACT_PUBLIC_QUERY_REQUIRED')
        if reward_market:
            if (self.params or self.kind != "RULES" or self.provider != "GAMMA_REWARDS"
                    or self.source_identity != "reward-market:"+url.path.split('/')[-1]):
                raise EvidenceError("REWARD_MARKET_EXACT_PUBLIC_QUERY_REQUIRED")
        madis = url.hostname == "madis-data.ncep.noaa.gov"
        if self.response_format not in {"JSON", "MADIS_XML"} or madis != (self.response_format == "MADIS_XML"):
            raise EvidenceError("SOURCE_RESPONSE_FORMAT_INVALID")
        if madis:
            from .weather_sources import validate_madis_params
            validate_madis_params(dict(self.params))
            if self.kind != "PWS_OBSERVATION":
                raise EvidenceError("MADIS_AUXILIARY_PWS_ONLY")


class PublicCollector:
    def __init__(self, store: EvidenceStore, client: httpx.AsyncClient, *,
                 attempts: int = 2, max_response_bytes: int = 768 * 1024,
                 attempt_seconds: float = 12.0,
                 cycle_seconds: float = 30.0,
                 sleeper: Callable = asyncio.sleep):
        if type(attempts) is not int or not 1 <= attempts <= 3:
            raise EvidenceError("COLLECTOR_ATTEMPT_LIMIT")
        if type(max_response_bytes) is not int or not 1 <= max_response_bytes <= 768 * 1024:
            raise EvidenceError("COLLECTOR_BYTES_LIMIT")
        if not 0 < finite(attempt_seconds) <= 12:
            raise EvidenceError("COLLECTOR_TIME_LIMIT")
        if not 0 < finite(cycle_seconds) <= 60:
            raise EvidenceError("COLLECTOR_CYCLE_TIME_LIMIT")
        if (client.auth is not None or client.cookies or client.params
                or any(k.lower() in {"authorization", "cookie", "x-api-key", "proxy-authorization"}
                       for k in client.headers)):
            raise EvidenceError("ANONYMOUS_PUBLIC_CLIENT_REQUIRED")
        self.store, self.client = store, client
        self.attempts, self.max_bytes, self.sleeper = attempts, max_response_bytes, sleeper
        self.attempt_seconds = attempt_seconds
        self.cycle_seconds = cycle_seconds

    async def cycle(self, cycle_id: str, requests: tuple[SourceRequest, ...]) -> dict:
        identity(cycle_id, maximum=80)
        if not 1 <= len(requests) <= 16:
            raise EvidenceError("COLLECTOR_CYCLE_LIMIT")
        results = []
        deadline = time.monotonic() + self.cycle_seconds
        blocked_hosts = {}
        for index, request in enumerate(requests):
            # Sequential, bounded providers keep the single-core host optional.
            # A subsequent exception can never roll back a committed earlier source.
            started = time.monotonic()
            state, reason, capture_ids = "ABSENT", "NO_RESPONSE", ()
            attempts_used = 0
            retry_not_before = None
            for attempt in range(self.attempts):
                host = urlsplit(request.url).hostname
                if host in blocked_hosts:
                    state,reason = "RATE_LIMIT","PROVIDER_RATE_LIMIT_IN_CYCLE"
                    retry_not_before = blocked_hosts[host]
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    state,reason = "BUDGET_EXHAUSTED","COLLECTION_CYCLE_DEADLINE"
                    break
                attempts_used += 1
                try:
                    # A public response may set a session cookie. It must not be
                    # forwarded to this or another provider on the next request.
                    self.client.cookies.clear()
                    if (self.client.auth is not None or any(k.lower() in
                        {"authorization","cookie","x-api-key","proxy-authorization"}
                        for k in self.client.headers)):
                        raise EvidenceError("ANONYMOUS_CLIENT_CHANGED")
                    async with asyncio.timeout(min(self.attempt_seconds, remaining)), self.client.stream("GET", request.url, params=dict(request.params),
                                                  timeout=8.0, follow_redirects=False,
                                                  headers={"Accept-Encoding": "identity"}) as response:
                        if response.status_code == 429:
                            state, reason = "RATE_LIMIT", "HTTP_429"
                            value = response.headers.get("Retry-After", "")
                            try:
                                seconds = float(value)
                                if not 0 <= finite(seconds):
                                    raise ValueError
                                retry_not_before = self.store.clock() + seconds
                            except (ValueError, EvidenceError):
                                try:
                                    retry_not_before = max(self.store.clock(), parsedate_to_datetime(value).timestamp())
                                except (TypeError, ValueError, OverflowError):
                                    # No quota guess: scheduler requires explicit recovery
                                    # when a provider supplies no usable retry time.
                                    retry_not_before = None
                            blocked_hosts[host] = retry_not_before
                        elif response.status_code >= 500:
                            state, reason = "TRANSPORT_FAILURE", "HTTP_5XX"
                        elif response.status_code != 200:
                            state, reason = "ABSENT", "HTTP_NON_SUCCESS"
                        elif response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                            state, reason = "MALFORMED", "UNBOUNDED_DECOMPRESSION_REFUSED"
                        else:
                            chunks, size = [], 0
                            async for chunk in response.aiter_bytes():
                                size += len(chunk)
                                if size > self.max_bytes:
                                    raise ValueError("RESPONSE_BYTES_LIMIT")
                                chunks.append(chunk)
                            response_bytes = b"".join(chunks)
                            if request.response_format == "MADIS_XML":
                                # Bounded raw XML is durable before normalization;
                                # no parser runs in the transport layer.
                                body = response_bytes.decode("utf-8")
                            else:
                                body = json.loads(response_bytes)
                                if not isinstance(body, (dict, list)):
                                    raise ValueError("JSON_CONTAINER_REQUIRED")
                            record = self.store.capture(
                                f"{cycle_id}:{index}:capture", event_id=request.event_id,
                                kind=request.kind, provider=request.provider,
                                source_identity=request.source_identity, revision=request.revision,
                                payload={"response": body, "endpoint": request.url,
                                         "request_params": dict(request.params),
                                         "http_status": response.status_code,
                                         "response_format": request.response_format,
                                         "source_time_status": "NOT_YET_NORMALIZED"})
                            capture_ids = (record["id"],)
                            state, reason = "SUCCESS", "RAW_RESPONSE_PERSISTED_NOT_CERTIFIED"
                except (httpx.RequestError, TimeoutError):
                    state, reason = "TRANSPORT_FAILURE", "REQUEST_FAILED"
                except (ValueError, UnicodeError):
                    state, reason = "MALFORMED", "INVALID_OR_OVERSIZE_RESPONSE"
                # Do not retry a 429 inside this cycle or defeat a Retry-After.
                # A later scheduler must apply the provider's documented quota.
                if state != "TRANSPORT_FAILURE" or attempt == self.attempts - 1:
                    break
                # Small deterministic per-source jitter; no global random state.
                await self.sleeper(max(0,min(deadline-time.monotonic(),2.0,0.25*2**attempt+(index%7)*.031)))
            result = self.store.source_result(
                f"{cycle_id}:{index}:health", event_id=request.event_id,
                provider=request.provider, cycle_id=cycle_id, state=state, reason=reason,
                elapsed_ms=(time.monotonic() - started) * 1000, capture_ids=capture_ids,
                attempts=attempts_used, retry_not_before=retry_not_before)
            results.append({"provider": request.provider, "state": state,
                            "attempts": attempts_used, "record_id": result["id"],
                            "retry_not_before": retry_not_before,
                            "capture_ids": list(capture_ids)})
        successes = sum(r["state"] == "SUCCESS" for r in results)
        return {"cycle_id": cycle_id, "sources": results,
                "partial_success": 0 < successes < len(results),
                "financial_authority": False, "automatic_order_placement": False}
