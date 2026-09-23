import asyncio

import httpx
import pytest

from polymarket_scanner.v11.collection import PublicCollector, SourceRequest
from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore


def request(provider="book", **kwargs):
    values = dict(provider=provider, url="https://clob.polymarket.com/book", event_id="event1",
                  kind="BOOK", source_identity="token:yes", revision="response", params=(("token_id","yes"),))
    values.update(kwargs)
    return SourceRequest(**values)


@pytest.fixture
def store(tmp_path):
    tmp_path.chmod(0o700)
    return EvidenceStore(tmp_path/"evidence.sqlite", "V11_PAPER")


def test_partial_success_commits_before_failed_provider_and_retry_is_measured(store):
    attempts = []

    def transport(req):
        attempts.append(req.url.params["token_id"])
        if req.url.params["token_id"] == "yes":
            return httpx.Response(200,json={"bids":[],"asks":[]})
        raise httpx.ConnectError("synthetic provider failure", request=req)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            collector = PublicCollector(store,client,sleeper=lambda _: asyncio.sleep(0))
            return await collector.cycle("cycle1",(request(),request("second",params=(("token_id","no"),))))

    result = asyncio.run(run())
    assert result["partial_success"] and attempts == ["yes","no","no"]
    assert store.get("cycle1:0:capture")["body"]["payload"]["response"] == {"bids":[],"asks":[]}
    assert store.get("cycle1:1:health")["body"]["attempts"] == 2
    assert result["financial_authority"] is False


@pytest.mark.parametrize("status, body, expected",[(429,b"", "RATE_LIMIT"),
    (302,b"", "ABSENT"), (200,b"bad json", "MALFORMED"),
    (200,b'{"too_large":"payload"}', "MALFORMED")])
def test_bad_responses_and_rate_limit_never_create_capture_or_retry(store,status,body,expected):
    calls = []

    def transport(req):
        calls.append(req)
        return httpx.Response(status, content=body, headers={"Retry-After":"3600", "Location":"https://unreviewed.invalid"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await PublicCollector(store,client,max_response_bytes=10).cycle("cycle",(request(),))

    result=asyncio.run(run())
    assert len(calls)==1 and result["sources"][0]["state"]==expected
    assert store.causal_inputs("event1",10**12)==[]


def test_total_deadline_bounds_slow_response(store):
    async def transport(req):
        await asyncio.sleep(1)
        return httpx.Response(200,json={})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await PublicCollector(store,client,attempts=1,attempt_seconds=.01).cycle("cycle",(request(),))

    result=asyncio.run(run())
    assert result["sources"][0]["state"]=="TRANSPORT_FAILURE"
    assert store.get("cycle:0:health")["body"]["elapsed_ms"] < 500


@pytest.mark.parametrize("url",["http://clob.polymarket.com/book", "https://clob.polymarket.com/order",
    "https://user:pass@clob.polymarket.com/book", "https://clob.polymarket.com/book?token_id=yes",
    "https://clob.polymarket.com.evil.invalid/book"])
def test_endpoint_boundary(url):
    with pytest.raises(EvidenceError,match="ENDPOINT"):
        request(url=url)


def test_credentials_and_secret_query_fields_are_refused(store):
    async def run():
        async with httpx.AsyncClient(headers={"Authorization":"synthetic secret"}) as client:
            with pytest.raises(EvidenceError,match="ANONYMOUS"):
                PublicCollector(store,client)
    asyncio.run(run())
    with pytest.raises(EvidenceError,match="SENSITIVE"):
        request(params=(("api_key","synthetic secret"),))


def test_provider_cookie_is_not_forwarded(store):
    cookies=[]
    def transport(req):
        cookies.append(req.headers.get('cookie'))
        return httpx.Response(200,json={},headers={'Set-Cookie':'session=fixture; Path=/'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await PublicCollector(store,client).cycle('cookies',(request(),request('second')))
    asyncio.run(run())
    assert cookies==[None,None]
