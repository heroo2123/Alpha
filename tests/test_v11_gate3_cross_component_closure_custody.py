"""Cross-component regression: transport byte-accounting x RAW custody/
closure, exercised through the real offline ``GateRuntime.run_attempt``
entrypoint (tools/v11_r09_gate3_runtime.py).

This suite does NOT re-test any single component in isolation -- that is
already covered elsewhere:
  * tests/test_v11_r09_gate3_runtime.py already proves, unit-style, that an
    ambiguous/unaccounted closure raises ``RUNTIME_EOF_UNPROVEN`` or
    ``RUNTIME_UNACCOUNTED_CLOSURE_BYTES`` and charges the outstanding bytes
    (see ``test_contract_stream_bytes_delivered_during_close_are_charged_
    and_held`` and neighbors);
  * tests/test_v11_r09_gate3_store_v1.py already proves the store's own
    ``seal_with_provenance``/custody contract in isolation;
  * tests/test_v11_gate3_evidence_intake_launch_wiring.py already proves the
    request-id-reuse/frozen-plan-mismatch contract for repeated attempts.

What is new here is the *combination*, across one durable restart boundary:
one synthetic attempt whose transport delivers real body bytes and then
reports one additional, never-read byte during ``stream.close()`` --an
ambiguous closure that is neither a clean EOF+closure proof nor a byte-
count violation large enough to poison the budget outright. Against that
single scripted attempt, this test asserts, together, in one place:

  (a) durable byte accounting: ``DurableBudget.received`` ends up exactly
      equal to the bytes actually observed (the read body plus the one
      late-closure byte) -- nothing silently lost or double-counted, and
      the budget is held open (``in_flight`` still set) rather than
      poisoned (``violated`` stays False, since the outstanding byte still
      fits inside the reservation);
  (b) RAW custody: ``store.seal_with_provenance`` is never called and
      ``store.receipts`` stays empty -- the undelivered-closure bytes are
      never released into the store as a sealed/complete RAW object;
  (c) durable restart-hold across a real restart: after closing and
      reopening every durable primitive (shared/session/budget/store) from
      disk, attempting the *next*, correctly-ordered request in the frozen
      plan on a fresh ``GateRuntime`` never reaches a SUCCESS/ELIGIBLE
      outcome either -- it is refused outright (``SESSION_LEDGER_
      ATTEMPT_OPEN_HELD``) before any further budget reservation or store
      mutation, because the first attempt's session state survived the
      reopen as durably non-terminal (``DISPATCHED``). This is the actual
      ambiguous-closure restart-hold mechanism; passing the *same* stuck
      request back would instead only exercise the unrelated plan-ordering/
      request-id-reuse checks, which are already covered elsewhere.

No network, no subprocess, no real clock, no real transport anywhere in
this module. This is still a fully synthetic/offline model: it proves the
*wiring* between the already-reviewed runtime accounting and the already-
reviewed store custody contract behaves consistently under one scripted
ambiguous-closure scenario and one real process-level reopen -- it grants
no real-world qualification, no provider/network evidence, and does not by
itself prove every other ambiguous-closure shape behaves identically.
"""
from __future__ import annotations

import hashlib
import socket
from dataclasses import asdict

import pytest

from tools.v11_multimodel_panel import canonical
from tools.v11_r09_gate3_launch import LaunchContractError
from tools.v11_r09_gate3_offline_io import OfflineResponse, SyntheticExchange
from tools.v11_r09_gate3_runtime import (
    AbsoluteWindow, AttemptRequest, FakeClock, FakeResourceProbe, FrozenPlan,
    GateRuntime, ReportSink, ResponseHead, ResponseStream, StreamSnapshot,
    SyntheticTransport, acquire_runtime_journals,
)

MANIFEST = 'a' * 64
BOOT = 'boot-cross-component-closure-custody'
GENESIS = '7' * 64
ETAG = '"obj-1"'
ORIGIN = 'https://weather.example.invalid'


@pytest.fixture(autouse=True)
def _deny_socket_connections(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError('this suite must never connect a real socket')
    monkeypatch.setattr(socket.socket, 'connect', denied)


class _PhantomCloseStream(ResponseStream):
    """One eager chunk delivered and fully read normally, then one
    additional, never-read byte revealed only inside ``close()`` -- an
    ambiguous closure (the stream's own cumulative accounting contradicts
    itself at closure time), not a clean EOF+closure proof and not a
    bytes-exceed-budget violation."""

    def __init__(self, response):
        self._response = response
        self._chunk = b''.join(response.chunks)
        self._read = False
        self._phantom_added = False

    @property
    def head(self):
        r = self._response
        return ResponseHead(r.status, r.headers, r.peer_ip, r.tls_verified, r.redirects)

    def snapshot(self):
        delivered = len(self._chunk) + (1 if self._phantom_added else 0)
        return StreamSnapshot(
            delivered_bytes=delivered, prefetched_bytes=0,
            read_bytes=len(self._chunk) if self._read else 0,
            read_count=1 if self._read else 0, known_chunk_count=None,
            eof_confirmed=self._read)

    def read(self, *, maximum_bytes, remaining_seconds):
        assert maximum_bytes > 0 and remaining_seconds > 0
        if self._read:
            return None
        self._read = True
        return self._chunk

    def close(self, *, remaining_seconds, discard_prefetched=False):
        assert remaining_seconds > 0
        # The boundary now reveals one more byte arrived during closure that
        # was never exposed via read()/snapshot() before this call returned.
        self._phantom_added = True
        return True


class _PhantomCloseTransport(SyntheticTransport):
    def __init__(self, exchange, stream):
        super().__init__(exchange)
        self._stream = stream

    def dispatch(self, request, *, deadline_monotonic, remaining_seconds):
        super().dispatch(request, deadline_monotonic=deadline_monotonic,
                         remaining_seconds=remaining_seconds)
        return self._stream


def _dirs(tmp_path):
    for name in ('shared', 'session', 'budget', 'store'):
        (tmp_path / name).mkdir(mode=0o700)
    (tmp_path / 'store' / 'objects').mkdir(mode=0o700)
    return tmp_path


def _acquire(tmp_path, *, history_head=None, store_head=None, store_descriptor=None):
    return acquire_runtime_journals(
        shared_dir=tmp_path / 'shared', session_dir=tmp_path / 'session',
        budget_dir=tmp_path / 'budget', store_root=tmp_path / 'store',
        shared_kwargs=dict(boot_id=BOOT,
                           genesis_review_digest=GENESIS if history_head is None else None,
                           expected_history_head=history_head),
        session_kwargs=dict(manifest_sha256=MANIFEST, boot_id=BOOT),
        budget_kwargs=dict(manifest_sha256=MANIFEST, max_bytes=1 << 20, boot_id=BOOT),
        store_kwargs=dict(manifest_sha256=MANIFEST, policy_sha256='b' * 64,
                           build_id='fixture', clock_method='synthetic',
                           max_clock_age_seconds=30, host_id='fixture-host',
                           boot_id=BOOT, expected_head=store_head,
                           expected_descriptor_sha256=store_descriptor),
    )


def _window(**overrides):
    base = dict(start_utc=0, acquisition_end_utc=1000, decision_lower_utc=1100)
    base.update(overrides)
    return AbsoluteWindow(**base)


def _clock(**overrides):
    base = dict(boot_id=BOOT, utc=10, mono=10, uncertainty=0.05)
    base.update(overrides)
    return FakeClock(**base)


def _resources(**overrides):
    base = dict(free_disk_bytes=3 * 1024 ** 3, available_memory_bytes=1024 ** 3)
    base.update(overrides)
    return FakeResourceProbe(**base)


def _request(**overrides):
    base = dict(request_id='req-1', purpose='INDEX', endpoint_id='c' * 64,
                control_domain_id='d' * 64, origin=ORIGIN, path='/fixed/index',
                provider=None, slot_index=5, range_start=None, range_end=None,
                reservation_bytes=32, expected_etag=ETAG, expected_object_bytes=None,
                source_pin='e' * 64, decoder_pin='f' * 64, clock_policy_sha256='1' * 64)
    base.update(overrides)
    return AttemptRequest(**base)


def _frozen_plan(requests, window=None):
    window = window or _window()
    raw = canonical({'schema_version': 1, 'manifest_sha256': MANIFEST,
        'window_sha256': hashlib.sha256(canonical(asdict(window))).hexdigest(),
        'request_schedule_sha256': hashlib.sha256(
            canonical([asdict(r) for r in requests])).hexdigest(),
        'event_schedule_sha256': hashlib.sha256(canonical([])).hexdigest()})
    return FrozenPlan(MANIFEST, hashlib.sha256(raw).hexdigest(), window,
                      requests, raw, (), synthetic_fixture=True)


def _ok_response(body, etag=ETAG):
    return OfflineResponse(200, (('Content-Length', str(len(body))), ('ETag', etag)),
                            (body,), '8.8.8.8', True)


def _build_runtime(shared, session, budget, store, tmp_path, *, requests, transport,
                    report_dir_name='report'):
    plan = _frozen_plan(requests)
    report_dir = tmp_path / report_dir_name
    report_dir.mkdir(mode=0o700, exist_ok=True)
    sink = ReportSink(report_dir)
    runtime = GateRuntime(shared=shared, session=session, budget=budget, store=store,
        transport=transport, clock=_clock(), resources=_resources(),
        window=_window(), allowed_peer_ips=('8.8.8.8',),
        manifest_sha256=MANIFEST, plan=plan, expected_plan_sha256=plan.sha256,
        report_sink=sink)
    return runtime, sink


def test_ambiguous_closure_holds_accounting_and_custody_durably_across_reopen(
        tmp_path, monkeypatch):
    root = _dirs(tmp_path)
    body = b'0123456789'
    response = _ok_response(body)
    exchange = SyntheticExchange({'req-1': response})
    stream = _PhantomCloseStream(response)
    first_request = _request(reservation_bytes=20)
    second_request = _request(request_id='req-2', reservation_bytes=20, slot_index=6)

    seal_calls = []

    with _acquire(root) as (shared, session, budget, store):
        monkeypatch.setattr(store, 'seal_with_provenance',
            lambda *a, **k: seal_calls.append((a, k)) or pytest.fail(
                'seal_with_provenance must never be reached for an ambiguous closure'))
        transport = _PhantomCloseTransport(exchange, stream)
        runtime, sink = _build_runtime(shared, session, budget, store, root,
            requests=(first_request, second_request), transport=transport)

        with pytest.raises(LaunchContractError, match='RUNTIME_UNACCOUNTED_CLOSURE_BYTES'):
            runtime.run_attempt(first_request)

        # (a) durable byte accounting: exactly the observed bytes (the 10
        # read bytes plus the one late-closure byte), held open rather than
        # poisoned -- the outstanding byte still fits inside the reservation.
        assert budget.received == len(body) + 1 == 11
        assert budget.in_flight == 'req-1'
        assert budget.violated is False

        # (b) RAW custody: no seal call, no sealed object.
        assert seal_calls == []
        assert store.receipts == {}
        assert session.attempt['state'] == 'DISPATCHED'
        assert shared.open_intent is not None

        history_head = shared.prev
        store_head = (store._seq, store._head)
        store_descriptor = store.descriptor_sha256
        budget_received_before_reopen = budget.received
        sink.close()

    # Close and fully reopen every durable primitive from disk -- not the
    # same in-memory objects -- before re-attempting the very same request.
    with _acquire(root, history_head=history_head, store_head=store_head,
                  store_descriptor=store_descriptor) as (shared, session, budget, store):
        monkeypatch.setattr(store, 'seal_with_provenance',
            lambda *a, **k: pytest.fail(
                'seal_with_provenance must never be reached on reattempt either'))
        assert budget.received == budget_received_before_reopen
        assert budget.in_flight == 'req-1'
        assert store.receipts == {}

        # A fresh runtime over the reopened journals. Deliberately attempt
        # the *next*, correctly-ordered request (req-2, not the stuck req-1)
        # so the frozen-plan ordering check (``_check_plan_request``) is
        # satisfied and the attempt reaches ``session.attempt_intent`` --
        # that is the only way to actually exercise the restart-hold this
        # test claims to prove, rather than the unrelated plan-ordering/
        # request-id-reuse checks a repeat of req-1 would hit instead.
        working_stream_exchange = SyntheticExchange({})
        transport = SyntheticTransport(working_stream_exchange)
        runtime, sink = _build_runtime(shared, session, budget, store, root,
            requests=(first_request, second_request), transport=transport)
        try:
            # (c) durable restart-hold across the reopen: even a different,
            # plan-valid request never reaches SUCCESS/ELIGIBLE -- the
            # session's own ledger refuses it before any further budget
            # reservation or transport dispatch (``working_stream_exchange``
            # would raise on ``.take()`` if dispatch were ever reached),
            # because req-1's attempt survived the reopen as non-terminal.
            with pytest.raises(LaunchContractError, match='SESSION_LEDGER_ATTEMPT_OPEN_HELD'):
                runtime.run_attempt(second_request)
        finally:
            sink.close()

        assert session.attempt['request_id'] == 'req-1'
        assert session.attempt['state'] == 'DISPATCHED'
        assert budget.received == budget_received_before_reopen
        assert budget.in_flight == 'req-1'
        assert store.receipts == {}
        assert working_stream_exchange._used == set()
