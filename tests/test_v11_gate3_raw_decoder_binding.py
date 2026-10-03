"""Synthetic receipt-to-A7 handoff checks; no decoder or network execution."""
from dataclasses import replace
import hashlib
import json
import os
import threading

import pytest

from test_v11_r09_gate3_runtime import (
    _acquire, _dirs, _ok_response, _request, _runtime,
)
from tools.v11_r09_gate3_a7_decoder import Pins, source_bytes
from tools.v11_r09_gate3_launch import LaunchContractError
from tools.v11_r09_gate3_offline_io import SyntheticExchange
from tools.v11_gate3_raw_decoder_binding import RawBindingRefusal, read_raw_for_a7


BODY = b'0123456789012345678901234567890'
IDENTITY = dict(provider='ECMWF_IFS_ENS', model='ifs', initialized_at=1730000000,
                step=3, member=1, grib_signature_sha256='3' * 64,
                model_version='fixture-v1', dataset='ecmwf-open-data:0p25',
                release_evidence_sha256='4' * 64)
SOURCE = hashlib.sha256(source_bytes(IDENTITY)).hexdigest()
DECODER = '5' * 64


def _pins(raw=BODY, **changes):
    values = dict(decoder_sha256=DECODER, source_sha256=SOURCE,
                  raw_sha256=hashlib.sha256(raw).hexdigest(),
                  section_sha256={n: str(n) * 64 for n in (1, 3, 4, 5, 6)},
                  evidence_class='SYNTHETIC')
    values.update(changes)
    return Pins(**values)


def _attempt(tmp_path, *, response=None):
    request = _request(purpose='FIELD', provider='IFS', slot_index=5,
                       reservation_bytes=len(BODY), source_pin=SOURCE,
                       decoder_pin=DECODER, range_start=0,
                       range_end=len(BODY) - 1, expected_object_bytes=len(BODY))
    root = _dirs(tmp_path)
    acquired = _acquire(root)
    shared, session, budget, store = acquired.__enter__()
    response = response or _ok_response(BODY, status=206,
        headers=(('Content-Range', f'bytes 0-{len(BODY) - 1}/{len(BODY)}'),))
    exchange = SyntheticExchange({'req-1': response})
    fixture = _runtime(shared, session, budget, store, exchange, requests=(request,))
    return acquired, fixture.actual, request


def _close(acquired):
    acquired.__exit__(None, None, None)


def _read(runtime, request, *, pins=None, identity=None):
    return read_raw_for_a7(runtime=runtime, request=request,
                           a7_request=IDENTITY if identity is None else identity,
                           pins=_pins() if pins is None else pins)


def test_exact_current_success_releases_verified_bytes(tmp_path, monkeypatch):
    acquired, runtime, request = _attempt(tmp_path)
    try:
        with pytest.raises(RawBindingRefusal, match='BINDING_SUCCESS_CAPTURE'):
            _read(runtime, request)
        outcome = runtime.run_attempt(request)
        assert outcome['outcome'] == 'SUCCESS', outcome
        real_read = runtime.store.read_receipt
        calls = []

        def checked_read(receipt):
            calls.append(receipt.commit_hash)
            return real_read(receipt)

        monkeypatch.setattr(runtime.store, 'read_receipt', checked_read)
        assert _read(runtime, request) == BODY
        assert calls == [runtime.session.capture_receipts[request.request_id]
                         ['store_receipt_commit_hash']]
    finally:
        _close(acquired)


@pytest.mark.parametrize('change', ['request', 'source', 'decoder', 'raw', 'identity',
                                     'capture', 'commit', 'provenance', 'historical',
                                     'bytes', 'terminal'])
def test_substitution_or_ambiguous_custody_refuses(tmp_path, monkeypatch, change):
    acquired, runtime, request = _attempt(tmp_path)
    try:
        runtime.run_attempt(request)
        rid = request.request_id
        capture = runtime.session.capture_receipts[rid]
        receipt = next(iter(runtime.store.receipts.values()))
        pins, identity = _pins(), IDENTITY
        if change == 'request':
            request = replace(request, expected_etag='"other"')
        elif change == 'source':
            pins = _pins(source_sha256='0' * 64)
        elif change == 'decoder':
            pins = _pins(decoder_sha256='0' * 64)
        elif change == 'raw':
            pins = _pins(raw_sha256='0' * 64)
        elif change == 'identity':
            identity = {**IDENTITY, 'member': 2}
        elif change == 'capture':
            capture['raw_sha256'] = '0' * 64
        elif change == 'commit':
            capture['store_receipt_commit_hash'] = '0' * 64
        elif change == 'provenance':
            runtime.store.receipts[receipt.object_sha256] = replace(
                receipt, provenance=replace(receipt.provenance, attempt_id='other'))
        elif change == 'historical':
            runtime.store.receipts[receipt.object_sha256] = replace(
                receipt, acknowledgement='UNKNOWN')
        elif change == 'bytes':
            monkeypatch.setattr(runtime.store, 'read_receipt', lambda _: b'substituted')
        elif change == 'terminal':
            runtime.session.attempt_history[rid]['outcome'] = 'FAILED'
        with pytest.raises(RawBindingRefusal):
            _read(runtime, request, pins=pins, identity=identity)
    finally:
        _close(acquired)


def test_failed_attempt_never_releases_bytes(tmp_path):
    acquired, runtime, request = _attempt(tmp_path,
        response=_ok_response(BODY, status=503))
    try:
        assert runtime.run_attempt(request)['outcome'] == 'FAILED'
        with pytest.raises(RawBindingRefusal, match='BINDING_SUCCESS_CAPTURE'):
            _read(runtime, request)
    finally:
        _close(acquired)


def test_replayed_receipt_has_unknown_acknowledgement_and_refuses(tmp_path):
    acquired, runtime, request = _attempt(tmp_path)
    try:
        assert runtime.run_attempt(request)['outcome'] == 'SUCCESS'
        assert _read(runtime, request) == BODY
    finally:
        _close(acquired)
    with _acquire(tmp_path) as (shared, session, budget, store):
        assert next(iter(store.receipts.values())).acknowledgement == 'UNKNOWN'
        runtime.shared, runtime.session, runtime.budget, runtime.store = (
            shared, session, budget, store)
        with pytest.raises(RawBindingRefusal, match='BINDING_RAW_PROVENANCE'):
            _read(runtime, request)


def test_store_read_detects_changed_body(tmp_path):
    acquired, runtime, request = _attempt(tmp_path)
    try:
        assert runtime.run_attempt(request)['outcome'] == 'SUCCESS'
        receipt = next(iter(runtime.store.receipts.values()))
        (tmp_path / 'store' / 'objects' / receipt.object_sha256).write_bytes(
            b'X' + BODY[1:])
        with pytest.raises(RawBindingRefusal, match='BINDING_READ_RECEIPT'):
            _read(runtime, request)
    finally:
        _close(acquired)


def test_missing_capture_and_retained_evidence_class_refuse(tmp_path):
    acquired, runtime, request = _attempt(tmp_path)
    try:
        assert runtime.run_attempt(request)['outcome'] == 'SUCCESS'
        with pytest.raises(RawBindingRefusal, match='BINDING_PINS'):
            _read(runtime, request, pins=_pins(evidence_class='RETAINED_REAL'))
        runtime.session.capture_receipts.pop(request.request_id)
        with pytest.raises(RawBindingRefusal, match='BINDING_SUCCESS_CAPTURE'):
            _read(runtime, request)
    finally:
        _close(acquired)


@pytest.mark.parametrize('owner', ['session', 'shared', 'budget'])
def test_closed_journal_refuses_release(tmp_path, owner):
    acquired, runtime, request = _attempt(tmp_path)
    try:
        assert runtime.run_attempt(request)['outcome'] == 'SUCCESS'
        getattr(runtime, owner).close()
        with pytest.raises(RawBindingRefusal, match='BINDING_CUSTODY'):
            _read(runtime, request)
    finally:
        _close(acquired)


@pytest.mark.parametrize('owner', ['session', 'shared', 'budget'])
def test_real_append_fsync_poison_refuses_release(tmp_path, monkeypatch, owner):
    acquired, runtime, request = _attempt(tmp_path)
    try:
        assert runtime.run_attempt(request)['outcome'] == 'SUCCESS'
        journal = getattr(runtime, owner)
        real_fsync = os.fsync

        def fail_target(fd):
            if fd == journal.fd:
                raise OSError('injected journal fsync fault')
            return real_fsync(fd)

        with monkeypatch.context() as patcher:
            patcher.setattr(os, 'fsync', fail_target)
            with pytest.raises(LaunchContractError, match='DURABILITY_UNCERTAIN'):
                if owner == 'session':
                    journal.report_completed(report_sha256='9' * 64)
                elif owner == 'shared':
                    journal.intent_open('fault', purpose=request.purpose,
                        endpoint_id=request.endpoint_id,
                        control_domain_id=request.control_domain_id,
                        manifest_sha256=runtime.manifest_sha256,
                        max_reservation_bytes=1, now_utc=50)
                else:
                    journal.reserve('fault', 1, started_monotonic=50)
        assert journal.failed is True
        with pytest.raises(RawBindingRefusal, match='BINDING_CUSTODY'):
            _read(runtime, request)
    finally:
        _close(acquired)


def test_replaced_pinned_session_journal_refuses_release(tmp_path):
    acquired, runtime, request = _attempt(tmp_path)
    try:
        assert runtime.run_attempt(request)['outcome'] == 'SUCCESS'
        path = runtime.session.path / runtime.session.JOURNAL_NAME
        path.rename(path.with_name('old-session-journal'))
        path.write_bytes(b'')
        path.chmod(0o600)
        with pytest.raises(RawBindingRefusal, match='BINDING_CUSTODY'):
            _read(runtime, request)
    finally:
        _close(acquired)


@pytest.mark.parametrize('change', ['outcome', 'bytes', 'accounting'])
def test_durable_shared_close_must_agree_with_success(tmp_path, change):
    acquired, runtime, request = _attempt(tmp_path)
    try:
        real_close = runtime.shared.intent_closed

        def conflicting_close(rid, **kwargs):
            if change == 'outcome':
                kwargs['outcome'] = 'FAILED'
            elif change == 'bytes':
                kwargs['total_delivered_bytes'] = 0
            else:
                kwargs['accounting_head'] = '0' * 64
            return real_close(rid, **kwargs)

        runtime.shared.intent_closed = conflicting_close
        assert runtime.run_attempt(request)['outcome'] == 'SUCCESS'
        with pytest.raises(RawBindingRefusal, match='BINDING_CLOSURE'):
            _read(runtime, request)
    finally:
        _close(acquired)


def test_unknown_durable_transport_closure_refuses(tmp_path):
    acquired, runtime, request = _attempt(tmp_path)
    try:
        real_close = runtime.session.transport_closed

        def unknown_close(rid, **kwargs):
            evidence = json.loads(kwargs['closure_evidence_raw'])
            evidence['known_closed'] = False
            evidence['delivered_bytes'] = None
            kwargs['closure_evidence_raw'] = json.dumps(
                evidence, sort_keys=True, separators=(',', ':')).encode()
            return real_close(rid, **kwargs)

        runtime.session.transport_closed = unknown_close
        assert runtime.run_attempt(request)['outcome'] == 'SUCCESS'
        with pytest.raises(RawBindingRefusal, match='BINDING_CLOSURE'):
            _read(runtime, request)
    finally:
        _close(acquired)


@pytest.mark.parametrize('change', ['outcome', 'accounting', 'status', 'completion'])
def test_durable_session_closure_and_accounting_must_agree(tmp_path, change):
    acquired, runtime, request = _attempt(tmp_path)
    try:
        if change == 'completion':
            real_accounted = runtime.session.accounted

            def wrong_completion(rid, **kwargs):
                kwargs['completion_event_hash'] = '0' * 64
                return real_accounted(rid, **kwargs)

            runtime.session.accounted = wrong_completion
        else:
            real_close = runtime.session.transport_closed

            def conflicting_close(rid, **kwargs):
                if change == 'outcome':
                    kwargs['outcome'] = 'FAILED'
                elif change == 'accounting':
                    kwargs['accounting_head'] = '0' * 64
                else:
                    evidence = json.loads(kwargs['closure_evidence_raw'])
                    evidence['status'] = 503
                    kwargs['closure_evidence_raw'] = json.dumps(
                        evidence, sort_keys=True, separators=(',', ':')).encode()
                return real_close(rid, **kwargs)

            runtime.session.transport_closed = conflicting_close
        assert runtime.run_attempt(request)['outcome'] == 'SUCCESS'
        with pytest.raises(RawBindingRefusal, match='BINDING_CLOSURE'):
            _read(runtime, request)
    finally:
        _close(acquired)


def test_missing_required_success_flags_refuses(tmp_path):
    acquired, runtime, request = _attempt(tmp_path)
    try:
        assert runtime.run_attempt(request)['outcome'] == 'SUCCESS'
        attempt = runtime.session.attempt_history[request.request_id]
        del attempt['denial_observed']
        del attempt['overdelivered']
        with pytest.raises(RawBindingRefusal, match='BINDING_SUCCESS_CAPTURE'):
            _read(runtime, request)
    finally:
        _close(acquired)


def test_journal_closure_between_validation_and_store_read_refuses(tmp_path,
                                                                   monkeypatch):
    acquired, runtime, request = _attempt(tmp_path)
    try:
        assert runtime.run_attempt(request)['outcome'] == 'SUCCESS'
        entered = threading.Event()
        result = {}
        real_read = runtime.store.read_receipt

        def observed_read(receipt):
            entered.set()
            return real_read(receipt)

        monkeypatch.setattr(runtime.store, 'read_receipt', observed_read)

        def attempt_read():
            try:
                result['raw'] = _read(runtime, request)
            except BaseException as exc:
                result['error'] = exc

        with runtime.store._mutex:
            worker = threading.Thread(target=attempt_read)
            worker.start()
            assert entered.wait(3), 'store read was not reached'
            runtime.session.close()
        worker.join(3)
        assert not worker.is_alive()
        assert 'raw' not in result
        assert isinstance(result.get('error'), RawBindingRefusal)
    finally:
        _close(acquired)
