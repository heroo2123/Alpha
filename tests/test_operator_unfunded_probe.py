import asyncio
import httpx
import pytest

import tools.operator_unfunded_probe as probe
from tools.operator_unfunded_probe import PublicMeter, strict_supported_sample


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



def test_strict_supported_sample_filters_rotates_and_wraps(monkeypatch):
    events=[
        {"id":"4","supported":False},
        {"id":"2","supported":True},
        {"id":"3","supported":True},
        {"id":"1","supported":False},
    ]
    def compile_event(event):
        if not event["supported"]:
            raise probe.StrictWeatherContractError("UNSUPPORTED")
        return object()
    monkeypatch.setattr(probe,"compile_strict_temperature_event",compile_event)
    monkeypatch.setattr(probe,"strict_contract_identity",lambda event,compiled: {"ok":True})
    assert [x["id"] for x in strict_supported_sample(events,"",limit=6)]==["2","3"]
    assert [x["id"] for x in strict_supported_sample(events,"2",limit=6)]==["3"]
    assert [x["id"] for x in strict_supported_sample(events,"9",limit=6)]==["2","3"]


def test_strict_supported_sample_respects_identity_failure_and_limit(monkeypatch):
    events=[{"id":str(i),"supported":True} for i in range(1,5)]
    monkeypatch.setattr(probe,"compile_strict_temperature_event",lambda event: object())
    def identity(event,compiled):
        if event["id"]=="2":
            raise probe.StrictWeatherContractError("IDENTITY_UNSUPPORTED")
        return {"ok":True}
    monkeypatch.setattr(probe,"strict_contract_identity",identity)
    assert [x["id"] for x in strict_supported_sample(events,"",limit=2)]==["1","3"]
