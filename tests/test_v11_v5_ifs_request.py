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
from polymarket_scanner.v11.evidence import EvidenceError, canonical
from polymarket_scanner.v11.model_panel import SourceIdentity


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
