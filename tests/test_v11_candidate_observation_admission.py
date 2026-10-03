"""Outer candidate admission for reviewed public observation requests."""
import asyncio
from dataclasses import dataclass, replace

import httpx
import pytest

from polymarket_scanner.v11 import candidate_assembly as assembly
from polymarket_scanner.v11.candidate_runner import CandidateRunner, ObservationBatch
from polymarket_scanner.v11.collection import SourceRequest
from polymarket_scanner.v11.ecmwf_sources import V5IFSRequest
from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.model_panel import SourceIdentity
from test_v11_candidate_assembly import scoped_plan
from test_v11_candidate_runner import built
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_paper_coordinator import rig, coordinator, proposal
from test_v11_strategy_pipeline import factory
from test_v11_request_assembly import inputs, target


@dataclass(frozen=True)
class SpoofedV5(V5IFSRequest):
    event_id: str = 'event1'
    provider: str = 'ECMWF_IFS_ENS'
    revision: str = 'review'

    @property
    def __class__(self):
        return SourceRequest


class SourceSubclass(SourceRequest):
    pass


class SourceProxy:
    @property
    def __class__(self):
        return SourceRequest


def invalid_request(kind, good):
    if kind in {'v5', 'spoofed_v5'}:
        cls = SpoofedV5 if kind == 'spoofed_v5' else V5IFSRequest
        return cls(SourceIdentity('ECMWF_IFS_ENS', 'review', 'ecmwf-open-data:0p25', 'c' * 64),
                   1790553600, 3, 0, 'a' * 64)
    if kind == 'subclass':
        return SourceSubclass(**vars(good))
    if kind == 'proxy':
        return SourceProxy()
    if kind == 'plain':
        return object()
    if kind == 'mutated_source':
        request = replace(good)
        object.__setattr__(request, 'url', 'https://example.invalid/unsafe')
        return request
    raise AssertionError(kind)


def changed_batch(base, requests):
    batch = replace(base)
    object.__setattr__(batch, 'requests', requests)
    return batch


@pytest.mark.parametrize('kind', ('v5', 'spoofed_v5', 'subclass', 'proxy', 'plain', 'mutated_source'))
@pytest.mark.parametrize('mixed', (False, True))
def test_observation_batch_refuses_unreviewed_request_shapes(rig, kind, mixed):
    good = SourceRequest('NOAA_AWC', 'https://aviationweather.gov/api/data/metar',
                         rig['context'].event_id, 'OFFICIAL_OBSERVATION',
                         rig['context'].station_id, 'planned',
                         (('ids', rig['context'].station_id), ('format', 'json')))
    bad = invalid_request(kind, good)
    requests = (good, bad) if mixed else (bad,)
    if kind == 'spoofed_v5' and not isinstance(bad, SourceRequest):
        pytest.fail('spoof fixture did not reproduce isinstance admission')
    with pytest.raises(EvidenceError):
        ObservationBatch(requests, ((good.event_id, rig['context'].station_id),),
                         ('fixture',), (('fixture', ('NOAA_AWC',)),))


@pytest.mark.parametrize('kind', ('spoofed_v5', 'subclass', 'proxy', 'plain', 'mutated_source'))
@pytest.mark.parametrize('mixed', (False, True))
@pytest.mark.parametrize('backward_clock', (False, True))
def test_candidate_construction_and_invocation_refuse_mutated_batches_without_effects(
        rig, monkeypatch, kind, mixed, backward_clock):
    paper = coordinator(rig)
    intent = proposal(rig, units='2')
    paper.coordinate('reserve', (intent,))
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: pytest.fail('HTTP called')),
                                     trust_env=False) as client:
            runner = built(rig, monkeypatch, client, observations=True)
            good = runner.observation_batch.requests[0]
            bad = invalid_request(kind, good)
            requests = (good, bad) if mixed else (bad,)
            batch = changed_batch(runner.observation_batch, requests)
            lock = runner.store.path.with_name(runner.store.path.name + '.candidate.lock')
            if lock.exists():
                pytest.fail('candidate lock existed before admission')
            before = paper._head()
            if backward_clock:
                rig['now'][0] -= 10
                rig['mono'][0] += 1

            def effect(*args, **kwargs):
                pytest.fail('candidate effect before admission')

            with monkeypatch.context() as guard:
                guard.setattr(runner.store, 'get', effect)
                guard.setattr(runner.store, 'latest', effect)
                guard.setattr(runner.store, 'clock', effect)
                guard.setattr(runner.runtime, 'tick', effect)
                with pytest.raises(EvidenceError):
                    CandidateRunner(runner.runtime, runner.policy, census=runner.census,
                                    discovery=runner.discovery, audits=runner.audits,
                                    observation=runner.observation, observation_batch=batch)
                object.__setattr__(runner, 'observation_batch', batch)
                with pytest.raises(EvidenceError):
                    await runner.run('invalid-batch')
            if lock.exists():
                pytest.fail('candidate lock created for invalid batch')
            after = paper._head()
            if after != before or paper._state(after)['intents'][intent.proposal_id]['status'] != 'RESERVED':
                pytest.fail('candidate changed synthetic intent before admission')
    asyncio.run(check())


def test_candidate_refuses_valid_postconstruction_batch_change_before_store(rig, monkeypatch):
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: pytest.fail('HTTP called')),
                                     trust_env=False) as client:
            runner = built(rig, monkeypatch, client, observations=True)
            object.__setattr__(runner.observation_batch, 'station_by_event',
                               ((rig['context'].event_id, 'changed-station'),))
            with monkeypatch.context() as guard:
                guard.setattr(runner.store, 'get', lambda *args: pytest.fail('store accessed'))
                with pytest.raises(EvidenceError, match='CANDIDATE_OBSERVATION_PLAN_CHANGED_REVIEW_REQUIRED'):
                    await runner.run('changed-plan')
            if runner.store.path.with_name(runner.store.path.name + '.candidate.lock').exists():
                pytest.fail('candidate lock created for changed plan')
    asyncio.run(check())


def test_candidate_assembly_refuses_mutated_batch_before_store(factory, monkeypatch):
    rig = factory('FUTURE_FORECAST')
    lane = assembly.TemperatureLane('temperature', inputs(rig), (target(rig),),
                                    'fixture', rig['request'].valuation_policy, 10.)
    cfg = scoped_plan(rig, lane)
    good = SourceRequest('NOAA_AWC', 'https://aviationweather.gov/api/data/metar',
                         rig['context'].event_id, 'OFFICIAL_OBSERVATION',
                         rig['context'].station_id, 'planned',
                         (('ids', rig['context'].station_id), ('format', 'json')))
    batch = ObservationBatch((good,), ((good.event_id, rig['context'].station_id),),
                             ('fixture',), (('fixture', ('NOAA_AWC',)),))
    cfg = replace(cfg, observation=batch)
    object.__setattr__(batch, 'requests', (SpoofedV5(
        SourceIdentity('ECMWF_IFS_ENS', 'review', 'ecmwf-open-data:0p25', 'c' * 64),
        1790553600, 3, 0, 'a' * 64),))
    with pytest.raises(EvidenceError, match='V5_IFS_OFFLINE_ONLY'):
        replace(cfg, observation=batch)
    async def check():
        async with httpx.AsyncClient(trust_env=False) as client:
            with monkeypatch.context() as guard:
                guard.setattr(assembly, 'PublicCollector', lambda *args: pytest.fail('collector constructed'))
                guard.setattr(rig['store'], 'get', lambda *args: pytest.fail('store accessed'))
                with pytest.raises(EvidenceError, match='V5_IFS_OFFLINE_ONLY'):
                    assembly.assemble_candidate(rig['store'], client, cfg, generation='invalid')
                object.__setattr__(batch, 'requests', (good,))
                object.__setattr__(batch, 'station_by_event', ((good.event_id, 'changed-station'),))
                with pytest.raises(EvidenceError, match='CANDIDATE_OBSERVATION_ROUTE_MISMATCH'):
                    assembly.assemble_candidate(rig['store'], client, cfg, generation='invalid-route')
    asyncio.run(check())
