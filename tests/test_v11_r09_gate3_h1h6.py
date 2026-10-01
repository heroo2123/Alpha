"""Offline regressions for the independent slice-3 H1-H6 counterexamples."""
import contextlib
import hashlib
import socket
from dataclasses import asdict, replace

import pytest

from tests import test_v11_r09_gate3_runtime as f
from tests.test_v11_r09_gate3_launch_v4 import candidate
from tools.v11_multimodel_panel import canonical
from tools.v11_r09_gate3_launch import LaunchContractError, parse_canonical
from tools.v11_r09_gate3_offline_io import SyntheticExchange
from tools.v11_r09_gate3_runtime import (
    AbsoluteWindow, AttemptRequest, FakeClock, FrozenEvent, FrozenPlan,
    GateRuntime, ReportSink, SyntheticTransport, acquire_runtime_journals,
    build_terminal_report,
)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('offline regression prohibits sockets')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)


@contextlib.contextmanager
def _validated(tmp_path, monkeypatch, *, store_age=30, events_override=None):
    tmp_path.mkdir(mode=0o700, exist_ok=True)
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    precedence = ('PREREQUISITE', 'CLOCK', 'RESOURCE', 'DENIAL',
                  'VALIDATION', 'SUCCESS', 'UNSCHEDULED')
    precedence_raw = canonical(precedence)
    (root / 'objects' / _sha(precedence_raw)).write_bytes(precedence_raw)
    (root / 'objects' / _sha(precedence_raw)).chmod(0o600)
    payload['accounting']['terminal_precedence'] = {
        'sha256': _sha(precedence_raw), 'byte_length': len(precedence_raw),
        'media_type': 'application/octet-stream'}
    dirs = tmp_path / 'journals'
    dirs.mkdir(mode=0o700)
    f._dirs(dirs)
    (dirs / 'report').mkdir(mode=0o700)
    manifest_raw = canonical(payload)
    manifest = _sha(manifest_raw)
    limits = payload['limits']
    kw = dict(shared_dir=dirs / 'shared', session_dir=dirs / 'session',
        budget_dir=dirs / 'budget', store_root=dirs / 'store',
        shared_kwargs=dict(boot_id=f.BOOT, genesis_review_digest=f.GENESIS,
                           max_requests=limits['max_requests']),
        session_kwargs=dict(manifest_sha256=manifest, boot_id=f.BOOT,
                            max_requests=limits['max_requests']),
        budget_kwargs=dict(manifest_sha256=manifest, boot_id=f.BOOT,
            max_requests=limits['max_requests'], max_bytes=limits['max_received_bytes'],
            max_elapsed_seconds=limits['max_elapsed_seconds'],
            min_start_interval_seconds=limits['min_start_interval_seconds']),
        store_kwargs=dict(manifest_sha256=manifest,
            policy_sha256=payload['runtime']['policy']['sha256'],
            build_id='fixture', clock_method='synthetic',
            max_clock_age_seconds=store_age, host_id='fixture-host', boot_id=f.BOOT))
    with acquire_runtime_journals(**kw) as journals, ReportSink(dirs / 'report') as sink:
        shared, session, budget, store = journals
        context = canonical(dict(
            manifest_runtime_sha256=_sha(canonical(payload['runtime'])),
            boot_id=f.BOOT, shared_root=list(shared.directory_identity),
            session_root=list(session.directory_identity),
            budget_root=list(budget.directory_identity),
            store_descriptor=store.descriptor_sha256,
            report_root=list(sink.directory_identity),
            store_policy=store.context['policy'], clock_method='synthetic',
            allowed_peer_ips=['8.8.8.8']))
        schedule = payload['schedule']['requests']
        ids = [item['request_id'] for item in schedule]
        pins = {rid: dict(expected_etag=f.ETAG, expected_object_bytes=None,
                          dependency_commit_hashes=()) for rid in ids}
        endpoints = {e['endpoint_id']: e for e in payload['network']['endpoints']}
        requests = []
        for item in schedule:
            ep = endpoints[item['endpoint_id']]
            source = payload['sources'][item['provider']]
            requests.append(AttemptRequest(item['request_id'], item['purpose'],
                item['endpoint_id'], ep['control_domain_id'], item['origin'], item['path'],
                item['provider'] if item['purpose'] == 'FIELD' else None,
                item['slot_index'], item['range_start'], item['range_end'],
                item['reservation_bytes'], f.ETAG, None, source['dossier']['sha256'],
                source['decoder_build']['sha256'],
                payload['runtime']['clock_policy']['sha256'],
                ep['parser_identity']['sha256'], (),
                tuple(ids[i] for i in item['prerequisites'])))
        requests = tuple(requests)
        t = payload['time']
        window = AbsoluteWindow(t['window_start_utc'], t['last_acquisition_utc'],
            t['decision_lower_utc'], limits['request_deadline_seconds'],
            limits['max_elapsed_seconds'], t['uncertainty_seconds'])
        field_ids = tuple(r.request_id for r in requests if r.purpose == 'FIELD')
        primary = next(r.provider for r in requests if r.purpose == 'FIELD')
        events = tuple(FrozenEvent(f'event-{side.lower()}', side, primary,
            field_ids, tuple(key), tuple(trial)) for side, key, trial in zip(
                payload['cohort']['events'], payload['cohort']['requested_keys'],
                payload['cohort']['gate2_trial_keys']))
        if events_override is not None:
            events = events_override(events)
        review = canonical(dict(schema_version=2, manifest_sha256=manifest,
            window_sha256=_sha(canonical(asdict(window))),
            request_schedule_sha256=_sha(canonical([asdict(r) for r in requests])),
            event_schedule_sha256=_sha(canonical([asdict(e) for e in events])),
            supplemental_pins_sha256=_sha(canonical(pins)),
            terminal_precedence_sha256=_sha(canonical(precedence)),
            runtime_context_sha256=_sha(context)))
        def make_plan():
            return FrozenPlan.from_validated_manifest(manifest_raw, repo=repo,
                object_root=root, now_utc=start - 4000, review_sha256=_sha(review),
                review_raw=review, request_pins=pins, runtime_context_raw=context,
                events=events, terminal_precedence=precedence)
        yield make_plan, journals, sink, kw, dirs, payload, start
        kw['shared_kwargs'].pop('genesis_review_digest')
        kw['shared_kwargs']['expected_history_head'] = shared.prev
        kw['session_kwargs']['expected_head'] = session.prev
        kw['store_kwargs']['expected_descriptor_sha256'] = store.descriptor_sha256


def _runtime(plan, journals, sink, start, exchange=None):
    shared, session, budget, store = journals
    return GateRuntime(shared=shared, session=session, budget=budget, store=store,
        transport=SyntheticTransport(exchange or SyntheticExchange({
            'request_0': f._ok_response(b'abcd')})),
        clock=FakeClock(f.BOOT, utc=start + 10, mono=10), resources=f._resources(),
        window=plan.window, allowed_peer_ips=('8.8.8.8',),
        manifest_sha256=plan.manifest_sha256, plan=plan,
        expected_plan_sha256=plan.sha256, report_sink=sink)


def test_h1_manifest_age_rejects_wider_store_and_stale_sample(tmp_path, monkeypatch):
    with _validated(tmp_path, monkeypatch, store_age=999) as (make, js, sink, kw, dirs, payload, start):
        plan = make()
        with pytest.raises(LaunchContractError, match='RUNTIME_MANIFEST_ACCOUNTING_CONTEXT'):
            _runtime(plan, js, sink, start)
    # A compatible store still cannot admit a measurement beyond the frozen cap.
    with _validated(tmp_path / 'second', monkeypatch, store_age=60) as (make, js, sink, kw, dirs, payload, start):
        rt = _runtime(make(), js, sink, start)
        rt.clock.set(utc=start + 100, mono=100, measured_mono=0)
        result = rt.run_attempt(rt.plan.requests[0])
        assert result['outcome'] == 'REFUSED'
        assert 'RUNTIME_CLOCK_MEASUREMENT' in result['reasons']


def test_h3_direct_constructor_cannot_replace_manifest_precedence(tmp_path, monkeypatch):
    with _validated(tmp_path, monkeypatch) as (make, js, sink, kw, dirs, payload, start):
        plan = make()
        reversed_order = tuple(reversed(plan.terminal_precedence))
        review = parse_canonical(plan.review_raw)
        review['terminal_precedence_sha256'] = _sha(canonical(reversed_order))
        raw = canonical(review)
        with pytest.raises(LaunchContractError, match='RUNTIME_TERMINAL_PRECEDENCE_EVIDENCE'):
            replace(plan, terminal_precedence=reversed_order,
                    review_raw=raw, review_sha256=_sha(raw))


def test_h4_requested_events_survive_empty_acquisition(tmp_path, monkeypatch):
    with _validated(tmp_path, monkeypatch) as (make, js, sink, kw, dirs, payload, start):
        plan = make()
        rt = _runtime(plan, js, sink, start)
        assert rt.finalize_report()['classification'] == 'COMPLETE'
        report = parse_canonical((dirs / 'report' / ReportSink.REPORT_FILE_NAME)
                                 .read_bytes().rstrip(b'\n'))
        assert [e['side'] for e in report['requested_events']] == ['HIGH', 'LOW']
        assert [e['requested_key'] for e in report['requested_events']] == payload['cohort']['requested_keys']
        assert [e['gate2_trial_key'] for e in report['requested_events']] == payload['cohort']['gate2_trial_keys']
        assert all(e['classification'] == 'FAILED_PREREQUISITE' for e in report['requested_events'])
        assert report['all_provider_intersection']['planned_event_count'] == 2
        with pytest.raises(LaunchContractError, match='RUNTIME_MANIFEST_EVENT_COHORT'):
            replace(plan, events=())
        wrong = replace(plan.events[0], requested_key=tuple(reversed(plan.events[0].requested_key)))
        with pytest.raises(LaunchContractError, match='RUNTIME_MANIFEST_EVENT_KEYS'):
            replace(plan, events=(wrong, plan.events[1]))


def test_h5_validated_same_boot_continuation_uses_verified_current_head(tmp_path, monkeypatch):
    with _validated(tmp_path, monkeypatch) as (make, js, sink, kw, dirs, payload, start):
        plan = make()
        rt = _runtime(plan, js, sink, start)
        assert rt.run_attempt(plan.requests[0])['outcome'] == 'SUCCESS'
    with acquire_runtime_journals(**kw) as js, ReportSink(dirs / 'report') as sink:
        assert js[0].open_intent is None and js[2].in_flight is None
        rt = _runtime(plan, js, sink, start)
        assert rt.plan.sha256 == plan.sha256
        assert js[0].expected_history_head == js[0].prev
    bad_kw = dict(kw, shared_kwargs=dict(kw['shared_kwargs'],
                                        expected_history_head='0' * 64))
    with pytest.raises(LaunchContractError, match='SHARED_LEDGER_LINEAGE_HEAD_MISMATCH'):
        with acquire_runtime_journals(**bad_kw):
            pass


@pytest.mark.parametrize('mode', ['clock_nan', 'clock_raises', 'clock_bad_digest',
                                  'duplicate_retry', 'denial_write'])
def test_h2_error_paths_retain_eager_bytes_and_restrictions(tmp_path, monkeypatch, mode):
    f._dirs(tmp_path)
    clock = f._clock()
    headers = (('Retry-After', '1200'),)
    status = 503
    if mode == 'duplicate_retry':
        status = 200
        headers += (('Retry-After', '1300'),)
    response = f._ok_response(b'abcd', status=status, headers=headers)
    class TransportWithFault(SyntheticTransport):
        def dispatch(self, request, **kwargs):
            result = super().dispatch(request, **kwargs)
            if mode == 'clock_nan':
                clock.set(utc=float('nan'))
            if mode == 'clock_raises':
                def broken(phase):
                    raise LaunchContractError('INJECTED_CLOCK_UNAVAILABLE')
                monkeypatch.setattr(clock, 'evidence', broken)
            if mode == 'clock_bad_digest':
                original = clock.evidence
                def invalid(phase):
                    ev = original(phase)
                    return replace(ev, reading=replace(ev.reading,
                        evidence_sha256='0' * 64))
                monkeypatch.setattr(clock, 'evidence', invalid)
            return result
    with f._acquire(tmp_path) as (shared, session, budget, store):
        rt = f._runtime(shared, session, budget, store,
            SyntheticExchange({'req-1': response}), clock=clock)
        rt.transport = TransportWithFault(rt.transport._exchange)
        if mode == 'denial_write':
            original = shared._append
            def fail_denial(event):
                if event['op'] == 'denial_observed':
                    raise OSError('injected denial write failure')
                return original(event)
            monkeypatch.setattr(shared, '_append', fail_denial)
        with pytest.raises((LaunchContractError, OSError)):
            rt.run_attempt(f._request(reservation_bytes=4))
        assert budget.received == 4 and budget.in_flight == 'req-1'
        assert shared.open_intent is not None
        assert shared.denials[f._request().control_domain_id]['cooldown_until'] is None
        assert shared.events[-1]['op'] in ('denial_observed', 'restriction_unresolved')
        rt.report_sink.close()
    with f._acquire(tmp_path) as (shared, session, budget, store):
        assert budget.received == 4 and budget.in_flight == 'req-1'
        assert shared.denials[f._request().control_domain_id]['cooldown_until'] is None


def test_h6_prerequisite_refusal_keeps_independent_stale_clock_reason(tmp_path):
    f._dirs(tmp_path)
    requests = (f._request(), f._request(request_id='req-2', purpose='FIELD',
        provider='GEFS', slot_index=0, reservation_bytes=4,
        prerequisite_request_ids=('req-1',)))
    with f._acquire(tmp_path) as (shared, session, budget, store):
        rt = f._runtime(shared, session, budget, store, SyntheticExchange({}),
            clock=f._clock(utc=100, mono=100, measured_mono=0), requests=requests)
        assert rt.run_attempt(requests[0])['reason'] == 'RUNTIME_CLOCK_MEASUREMENT'
        second = rt.run_attempt(requests[1])
        assert {'RUNTIME_PREREQUISITE_MISSING', 'RUNTIME_CLOCK_MEASUREMENT'} <= set(second['reasons'])
        report = build_terminal_report(plan=rt.plan, session=session,
            budget=budget, shared=shared, store=store)
        assert {'RUNTIME_PREREQUISITE_MISSING', 'RUNTIME_CLOCK_MEASUREMENT'} <= set(report['rows'][0]['reasons'])
        assert report['attempted_request_ids'] == []


def test_h2_session_denial_write_failure_still_charges_delivered_bytes(tmp_path, monkeypatch):
    f._dirs(tmp_path)
    response = f._ok_response(b'abcd', status=503,
                              headers=(('Retry-After', '1200'),))
    with f._acquire(tmp_path) as (shared, session, budget, store):
        rt = f._runtime(shared, session, budget, store,
                        SyntheticExchange({'req-1': response}))
        original = session._append
        def fail_denial(event):
            if event['op'] == 'denial':
                raise OSError('injected session denial write failure')
            return original(event)
        monkeypatch.setattr(session, '_append', fail_denial)
        with pytest.raises(OSError, match='injected session denial write failure'):
            rt.run_attempt(f._request(reservation_bytes=4))
        assert budget.received == 4 and budget.in_flight == 'req-1'
        assert shared.open_intent is not None
        assert shared.denials[f._request().control_domain_id]['status'] == '503'
        rt.report_sink.close()
    with f._acquire(tmp_path) as (shared, session, budget, store):
        assert budget.received == 4 and budget.in_flight == 'req-1'
        assert shared.denials[f._request().control_domain_id]['status'] == '503'
