import asyncio
import httpx
import pytest

from tools.operator_unfunded_probe import PublicMeter


@pytest.mark.parametrize("url,method,headers", [
    ("https://api.telegram.org/botfixture/sendMessage","GET",{}),
    ("https://clob.polymarket.com/order","POST",{}),
    ("http://api.weather.gov/stations/KNYC","GET",{}),
    ("https://clob.polymarket.com/book","GET",{"POLY_API_KEY":"fixture"}),
    ("https://clob.polymarket.com/book","GET",{"Authorization":"fixture"}),
])
def test_probe_refuses_financial_telegram_private_or_insecure_requests(url,method,headers):
    called=[]
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:called.append(r))) as client:
            with PublicMeter().installed(), pytest.raises(RuntimeError,match="REQUEST_REJECTED"):
                await client.request(method,url,headers=headers)
    asyncio.run(run())
    assert not called


def test_probe_counts_bounded_public_response_without_url_or_query():
    meter=PublicMeter(request_limit=1)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=b"public"))) as client:
            with meter.installed():
                response=await client.get("https://api.weather.gov/stations?fixture=not-recorded")
                assert response.content==b"public"
                with pytest.raises(RuntimeError,match="BUDGET_EXHAUSTED"):
                    await client.get("https://api.weather.gov/stations")
    asyncio.run(run())
    assert meter.requests==1
    assert meter.rows["api.weather.gov"]["response_bytes"]==6
    assert "not-recorded" not in str(dict(meter.rows))


def test_synchronous_wrh_requests_share_public_budget_and_guards():
    meter=PublicMeter(request_limit=1)
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=b"wrh"))) as client, meter.installed():
        assert client.get("https://www.weather.gov/wrh/timeseries").content==b"wrh"
        with pytest.raises(RuntimeError,match="BUDGET_EXHAUSTED"):
            client.get("https://www.weather.gov/wrh/timeseries")
        with pytest.raises(RuntimeError,match="REQUEST_REJECTED"):
            client.post("https://clob.polymarket.com/order")
    assert meter.rows["www.weather.gov"]["response_bytes"]==3
