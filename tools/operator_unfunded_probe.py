"""Bounded, account-free public collection measurement; never starts a service.

Run from a clean exact Git checkout. Output contains aggregate metrics, not URLs,
query strings, headers, market payloads, Telegram data or account credentials.
"""
import argparse
import asyncio
from collections import defaultdict
from contextlib import contextmanager
from decimal import Decimal
import json
from pathlib import Path
import resource
import subprocess
import time
from unittest.mock import patch

import httpx

PUBLIC_HOSTS = frozenset({"gamma-api.polymarket.com", "clob.polymarket.com",
    "api.weather.gov", "www.weather.gov", "api.synopticdata.com",
    "ensemble-api.open-meteo.com"})


class CountedStream(httpx.AsyncByteStream):
    def __init__(self, stream, row):
        self.stream, self.row = stream, row

    async def __aiter__(self):
        async for chunk in self.stream:
            self.row["response_bytes"] += len(chunk)
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


class CountedSyncStream(httpx.SyncByteStream):
    def __init__(self,stream,row):
        self.stream,self.row=stream,row

    def __iter__(self):
        for chunk in self.stream:
            self.row["response_bytes"]+=len(chunk)
            yield chunk

    def close(self):
        self.stream.close()


class PublicMeter:
    def __init__(self, request_limit=2000):
        self.request_limit, self.requests = request_limit, 0
        self.rows = defaultdict(lambda: {"requests":0, "response_bytes":0,
            "header_latency_seconds":0, "errors":0, "statuses":{}})

    def start(self,request):
        # Disallow even public POST endpoints; this probe only needs GETs.
        if (request.method not in {"GET", "HEAD"} or request.url.scheme != "https"
                or request.url.host not in PUBLIC_HOSTS or request.url.port not in (None,443)
                or any(k.lower().startswith(("authorization", "poly_")) for k in request.headers)):
            raise RuntimeError("PUBLIC_PROBE_REQUEST_REJECTED")
        if self.requests >= self.request_limit:
            raise RuntimeError("PUBLIC_PROBE_REQUEST_BUDGET_EXHAUSTED")
        self.requests += 1
        row = self.rows[request.url.host]
        row["requests"] += 1
        return row

    @contextmanager
    def installed(self):
        original, original_sync = httpx.AsyncClient.send, httpx.Client.send
        async def measured(client, request, **kwargs):
            row=self.start(request)
            started = time.monotonic()
            try:
                response = await original(client, request, **dict(kwargs, stream=True))
            except Exception:
                row["errors"] += 1
                raise
            finally:
                row["header_latency_seconds"] += time.monotonic() - started
            key = str(response.status_code)
            row["statuses"][key] = row["statuses"].get(key,0) + 1
            if response.is_stream_consumed:
                row["response_bytes"] += len(response.content)
            else:
                response.stream = CountedStream(response.stream, row)
            if not kwargs.get("stream", False):
                await response.aread()
            return response
        def measured_sync(client,request,**kwargs):
            row=self.start(request)
            started=time.monotonic()
            try:
                response=original_sync(client,request,**dict(kwargs,stream=True))
                key=str(response.status_code)
                row["statuses"][key]=row["statuses"].get(key,0)+1
                if response.is_stream_consumed:
                    row["response_bytes"]+=len(response.content)
                else:
                    response.stream=CountedSyncStream(response.stream,row)
                if not kwargs.get("stream",False):
                    response.read()
                return response
            except Exception:
                row["errors"]+=1
                raise
            finally:
                row["header_latency_seconds"]+=time.monotonic()-started
        with patch.object(httpx.AsyncClient, "send", measured), patch.object(httpx.Client,"send",measured_sync):
            yield self


def release(expected):
    root = Path(__file__).resolve().parents[1]
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
    sha, tree = git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")
    if expected != sha or git("status", "--porcelain", "--untracked-files=normal"):
        raise RuntimeError("CLEAN_EXPECTED_RELEASE_REQUIRED")
    return {"sha":sha, "tree":tree}


async def collect(cycles, interval, meter):
    from polymarket_scanner.production.weather import WeatherPipeline, STRATEGIES
    weather = WeatherPipeline()
    records, cursor = [], ""
    try:
        with meter.installed():
            for index in range(cycles):
                row = {"cycle":index+1, "started_at":time.time(), "evaluated":0, "candidates":0}
                try:
                    async with asyncio.timeout(900):
                        found = await weather.discover()
                        row["census"] = found["status"]
                        events = sorted(found["events"], key=lambda x:str(x.get("id","")))
                        selected = [x for x in events if str(x.get("id",""))>cursor] or events
                        for event in selected[:6]:
                            cursor = str(event.get("id", ""))
                            candidates = await weather.evaluate(event, STRATEGIES, Decimal(".08"), Decimal(".02"))
                            row["evaluated"] += 1
                            row["candidates"] += len(candidates)
                        row["rejection_count"] = len(weather.last_rejections)
                except Exception as exc:
                    row["error_type"] = type(exc).__name__
                row["ended_at"] = time.time()
                records.append(row)
                if index+1 < cycles:
                    await asyncio.sleep(interval)
    finally:
        await weather.close()
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cycles", type=int, default=1, choices=range(1,6))
    parser.add_argument("--interval", type=int, default=60, choices=range(60,601))
    args = parser.parse_args()
    identity = release(args.expected_sha)
    meter, started = PublicMeter(), time.monotonic()
    before = resource.getrusage(resource.RUSAGE_SELF)
    rows = asyncio.run(collect(args.cycles, args.interval, meter))
    after = resource.getrusage(resource.RUSAGE_SELF)
    report = {"schema":"alpha-unfunded-collection-v1", "release":identity,
        "scope":"public source collection only; no host, account, trading or Telegram acceptance",
        "cycles":rows, "http":dict(meter.rows), "request_limit":meter.request_limit,
        "wall_seconds":time.monotonic()-started, "cpu_user_seconds":after.ru_utime-before.ru_utime,
        "cpu_system_seconds":after.ru_stime-before.ru_stime,
        "peak_rss_bytes":after.ru_maxrss*1024, "block_input_operations":after.ru_inblock-before.ru_inblock,
        "block_output_operations":after.ru_oublock-before.ru_oublock,
        "interpretation":"Linux process RSS; API bytes exclude transport overhead. Repeat on selected host alongside each service. No availability/performance guarantee."}
    encoded = (json.dumps(report, sort_keys=True, indent=2, allow_nan=False)+"\n").encode()
    with args.output.open("xb") as stream:
        stream.write(encoded)
    print(json.dumps({"sha":identity["sha"], "cycles":len(rows), "requests":meter.requests,
        "peak_rss_bytes":report["peak_rss_bytes"], "report_bytes":len(encoded)}))
    return 1 if any("error_type" in row for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
