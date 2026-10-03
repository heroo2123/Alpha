"""Synthetic, offline coverage for the V5 IFS request representation."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from polymarket_scanner.v11.ecmwf_sources import (
    ECMWFCollector, ECMWFRequest, V5IFSRequest, access_state, plan_ranges,
)
from polymarket_scanner.v11.collection import PublicCollector, SourceRequest
from polymarket_scanner.v11.evidence import EvidenceError, canonical
from polymarket_scanner.v11.model_panel import SourceIdentity
from polymarket_scanner.v11.observation_runtime import ObservationRuntime, ScheduledCollector


RUN = datetime(2026, 9, 28, tzinfo=timezone.utc).timestamp()
IFS = SourceIdentity('ECMWF_IFS_ENS', 'test-release-v1', 'ecmwf-open-data:0p25', 'c'*64)


def request(step=3, member=0, source=IFS):
    return V5IFSRequest(source, RUN, step, member, 'a'*64)


def row(req, offset):
    return (canonical(dict(req.selectors, _offset=offset, _length=16)) + '\n').encode()


def test_full_native_ifs_inventory_and_exact_member_index_ranges():
    assert len(range(0, 73, 3)) * 51 == 1275
    assert (len(range(0, 73, 3)) - len(range(0, 73, 6))) * 51 == 612
    for step in range(0, 73, 3):
        requests = tuple(request(step, member) for member in range(51))
        control = requests[0]
        assert control.selectors['type'] == 'fc'
        assert control.url.endswith(f'-{step}h-oper-fc.grib2')
        assert plan_ranges(row(control, 0), (control,))[0].header == 'bytes=0-15'
        perturbed = requests[1:]
        assert {req.url for req in perturbed} == {perturbed[0].url}
        assert perturbed[0].url.endswith(f'-{step}h-enfo-ef.grib2')
        assert {req.selectors['number'] for req in perturbed} == {str(n) for n in range(1, 51)}
        ranges = plan_ranges(b''.join(row(req, i*16) for i, req in enumerate(perturbed)), perturbed)
        assert tuple(r.header for r in ranges) == tuple(f'bytes={i*16}-{i*16+15}' for i in range(50))
        assert all(req.identity['adapter'] == 'alpha_v11_ecmwf_ifs_v5_offline_1' for req in requests)


@pytest.mark.parametrize('step', [-3, 1, 4, 73, 75, True, 3.0])
def test_v5_rejects_non_native_hour(step):
    with pytest.raises(EvidenceError, match='V5_IFS_STEP_BOUND'):
        request(step)


def test_v5_is_source_bound_and_legacy_request_stays_six_hour():
    with pytest.raises(EvidenceError, match='ECMWF_STEP_BOUND'):
        ECMWFRequest(IFS, RUN, 3, 0, 'a'*64)
    assert ECMWFRequest(IFS, RUN, 6, 0, 'a'*64).identity['adapter'] == 'alpha_v11_ecmwf_open_v2'
    for source in (replace(IFS, provider='ECMWF_AIFS_ENS'),
                   replace(IFS, dataset='other')):
        with pytest.raises(EvidenceError, match='V5_IFS_SOURCE_IDENTITY'):
            request(source=source)


def test_v5_index_selection_fails_closed_on_missing_or_duplicate_member():
    control = request(3, 0)
    with pytest.raises(EvidenceError, match='ECMWF_REQUESTED_FIELD_NOT_AVAILABLE'):
        plan_ranges(row(request(3, 1), 0), (control,))
    with pytest.raises(EvidenceError, match='ECMWF_DUPLICATE_FIELD'):
        plan_ranges(row(control, 0) + row(control, 16), (control,))


def test_v5_cannot_enter_generic_collector():
    req = request()
    with pytest.raises(EvidenceError, match='V5_IFS_OFFLINE_ONLY'):
        access_state(req, now=RUN+3600)
    seen = []
    transport = httpx.MockTransport(lambda r: seen.append(r))
    with pytest.raises(EvidenceError, match='V5_IFS_OFFLINE_ONLY'):
        asyncio.run(ECMWFCollector(SimpleNamespace(clock=lambda: RUN+3600),
                                   transport=transport).collect(req, None, 'synthetic'))
    assert not seen


@pytest.mark.parametrize('timestamp', [0, -0.0, -1, True, None, '0', [],
    float('nan'), float('inf'), float('-inf'), 253402300800, 1e300, 10**400,
    RUN + 1, RUN + .5])
def test_v5_timestamp_refuses_invalid_values_with_domain_error(timestamp):
    with pytest.raises(EvidenceError, match='^V5_IFS_INITIALIZATION_CYCLE$'):
        V5IFSRequest(IFS, timestamp, 3, 0, 'a'*64)


def test_v5_timestamp_bounds_and_integer_valued_float():
    far_cycle = datetime(9999, 12, 31, tzinfo=timezone.utc).timestamp()
    assert V5IFSRequest(IFS, far_cycle, 72, 50, 'a'*64).initialized_at == far_cycle
    assert V5IFSRequest(IFS, int(RUN), 3, 0, 'a'*64).identity == request().identity


class ShapedV5(V5IFSRequest):
    # The prior PublicCollector bypass only needed these public request fields.
    response_format = 'JSON'
    params = ()
    event_id = 'event1'
    kind = 'MODEL'
    provider = 'ECMWF_IFS_ENS'
    source_identity = 'synthetic'
    revision = 'synthetic'


class SpoofedV5(ShapedV5):
    @property
    def __class__(self):
        return SourceRequest


class SourceRequestSubclass(SourceRequest):
    pass


def test_all_generic_dispatch_entries_refuse_v5_before_transport_or_store(tmp_path):
    seen = []
    transport = httpx.MockTransport(lambda req: (seen.append(req), httpx.Response(404))[1])
    store = SimpleNamespace(path=tmp_path/'unused.sqlite', clock=lambda: RUN+3600)
    valid = SourceRequest('book', 'https://clob.polymarket.com/book', 'event1',
                          'BOOK', 'token:yes', 'response', (('token_id', 'yes'),))

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            public = PublicCollector(store, client)
            scheduled = ScheduledCollector(public)
            observed = ObservationRuntime(scheduled)
            for bad in (request(), ShapedV5(IFS, RUN, 3, 0, 'a'*64),
                        SpoofedV5(IFS, RUN, 3, 0, 'a'*64)):
                for batch in ((bad,), (valid, bad), (bad, valid)):
                    for entry in (
                        lambda: public.cycle('cycle', batch),
                        lambda: scheduled.cycle('cycle', batch),
                        lambda: scheduled._cycle('cycle', batch),
                        lambda: observed.cycle('cycle', batch,
                            station_by_event={'event1': 'station'}, strategies=('strategy',)),
                    ):
                        with pytest.raises(EvidenceError, match='^V5_IFS_OFFLINE_ONLY$'):
                            await entry()
            for bad in (SimpleNamespace(url=valid.url, response_format='JSON', params=()),
                        object(), SourceRequestSubclass('book', valid.url, 'event1',
                            'BOOK', 'token:yes', 'response'),
                        ShapedV5(IFS, RUN, 3, 0, 'a'*64)):
                expected = 'V5_IFS_OFFLINE_ONLY' if isinstance(bad, V5IFSRequest) else 'SOURCE_REQUEST_REQUIRED'
                with pytest.raises(EvidenceError, match=f'^{expected}$'):
                    await public.cycle('invalid', (bad,))
            altered = SourceRequest('book', 'https://clob.polymarket.com/book',
                                    'event1', 'BOOK', 'token:yes', 'response')
            object.__setattr__(altered, 'url', 'https://example.invalid/book')
            with pytest.raises(EvidenceError, match='^PUBLIC_GET_ENDPOINT_NOT_REVIEWED$'):
                await public.cycle('altered', (altered,))

    asyncio.run(run())
    if seen or (tmp_path/'unused.sqlite.collector.lock').exists():
        raise AssertionError('refused request reached transport or scheduler store')


def test_ecmwf_collector_refuses_v5_before_clock_callback():
    calls = []
    store = SimpleNamespace(clock=lambda: calls.append('clock'))
    transport = httpx.MockTransport(lambda req: calls.append('transport'))
    with pytest.raises(EvidenceError, match='^V5_IFS_OFFLINE_ONLY$'):
        asyncio.run(ECMWFCollector(store, transport=transport).collect(request(), None, 'synthetic'))
    if calls:
        raise AssertionError('refused V5 request invoked callback or transport')
