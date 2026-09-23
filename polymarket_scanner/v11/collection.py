"""Bounded GET-only collection; each successful provider capture commits first.

No auth, POST, Telegram, wallet, trading endpoint or retry of financial actions.
Endpoint allowlisting is deliberately narrow for the first observation pilot.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import time
from typing import Callable
from urllib.parse import urlsplit

import httpx

from .evidence import EvidenceError, EvidenceStore, KINDS, canonical, finite, identity


ALLOWED_GET_ENDPOINTS = {
    ("gamma-api.polymarket.com", "/events"),
    ("clob.polymarket.com", "/book"),
    ("aviationweather.gov", "/api/data/metar"),
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

    def __post_init__(self):
        url = urlsplit(self.url)
        if (url.scheme != "https" or url.username or url.password or url.query or url.fragment
                or url.port not in (None, 443) or (url.hostname, url.path) not in ALLOWED_GET_ENDPOINTS):
            raise EvidenceError("PUBLIC_GET_ENDPOINT_NOT_REVIEWED")
        identity(self.provider)
        for value in (self.event_id, self.source_identity, self.revision):
            identity(value)
        if self.kind not in KINDS - {"LABEL", "FEATURES"}:
            raise EvidenceError("PUBLIC_SOURCE_KIND_INVALID")
        if len(self.params) > 16 or any(len(k) > 64 or len(v) > 512 for k, v in self.params):
            raise EvidenceError("REQUEST_PARAMS_LIMIT")
        if len(dict(self.params)) != len(self.params):
            raise EvidenceError("DUPLICATE_REQUEST_PARAMS")
        canonical(dict(self.params))


class PublicCollector:
    def __init__(self, store: EvidenceStore, client: httpx.AsyncClient, *,
                 attempts: int = 2, max_response_bytes: int = 768 * 1024,
                 attempt_seconds: float = 12.0,
                 sleeper: Callable = asyncio.sleep):
        if type(attempts) is not int or not 1 <= attempts <= 3:
            raise EvidenceError("COLLECTOR_ATTEMPT_LIMIT")
        if type(max_response_bytes) is not int or not 1 <= max_response_bytes <= 768 * 1024:
            raise EvidenceError("COLLECTOR_BYTES_LIMIT")
        if not 0 < finite(attempt_seconds) <= 12:
            raise EvidenceError("COLLECTOR_TIME_LIMIT")
        if (client.auth is not None or client.cookies or client.params
                or any(k.lower() in {"authorization", "cookie", "x-api-key", "proxy-authorization"}
                       for k in client.headers)):
            raise EvidenceError("ANONYMOUS_PUBLIC_CLIENT_REQUIRED")
        self.store, self.client = store, client
        self.attempts, self.max_bytes, self.sleeper = attempts, max_response_bytes, sleeper
        self.attempt_seconds = attempt_seconds

    async def cycle(self, cycle_id: str, requests: tuple[SourceRequest, ...]) -> dict:
        identity(cycle_id, maximum=80)
        if not 1 <= len(requests) <= 16:
            raise EvidenceError("COLLECTOR_CYCLE_LIMIT")
        results = []
        for index, request in enumerate(requests):
            # Sequential, bounded providers keep the single-core host optional.
            # A subsequent exception can never roll back a committed earlier source.
            started = time.monotonic()
            state, reason, capture_ids = "ABSENT", "NO_RESPONSE", ()
            attempts_used = 0
            for attempt in range(self.attempts):
                attempts_used += 1
                try:
                    async with asyncio.timeout(self.attempt_seconds), self.client.stream("GET", request.url, params=dict(request.params),
                                                  timeout=8.0, follow_redirects=False,
                                                  headers={"Accept-Encoding": "identity"}) as response:
                        if response.status_code == 429:
                            state, reason = "RATE_LIMIT", "HTTP_429"
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
                            body = json.loads(b"".join(chunks))
                            if not isinstance(body, (dict, list)):
                                raise ValueError("JSON_CONTAINER_REQUIRED")
                            record = self.store.capture(
                                f"{cycle_id}:{index}:capture", event_id=request.event_id,
                                kind=request.kind, provider=request.provider,
                                source_identity=request.source_identity, revision=request.revision,
                                payload={"response": body, "endpoint": request.url,
                                         "request_params": dict(request.params),
                                         "http_status": response.status_code,
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
                await self.sleeper(min(2.0, 0.25 * 2**attempt + (index % 7) * 0.031))
            result = self.store.source_result(
                f"{cycle_id}:{index}:health", event_id=request.event_id,
                provider=request.provider, cycle_id=cycle_id, state=state, reason=reason,
                elapsed_ms=(time.monotonic() - started) * 1000, capture_ids=capture_ids,
                attempts=attempts_used)
            results.append({"provider": request.provider, "state": state,
                            "attempts": attempts_used, "record_id": result["id"],
                            "capture_ids": list(capture_ids)})
        successes = sum(r["state"] == "SUCCESS" for r in results)
        return {"cycle_id": cycle_id, "sources": results,
                "partial_success": 0 < successes < len(results),
                "financial_authority": False, "automatic_order_placement": False}
