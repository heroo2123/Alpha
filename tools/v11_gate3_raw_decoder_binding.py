"""Synthetic offline RAW custody handoff for A7 input bytes.

This checks an existing successful Gate-3 attempt. It never dispatches, decodes,
creates pins, or treats replayed store acknowledgement as current custody.
"""
from dataclasses import asdict
from contextlib import ExitStack, contextmanager
import base64
import hashlib
import json
import os

from tools.v11_multimodel_panel import canonical
from tools.v11_r09_gate3_a7_decoder import A7Refusal, Pins, source_bytes
from tools.v11_r09_gate3_launch import LaunchContractError
from tools.v11_r09_gate3_runtime import (
    AttemptRequest, GateRuntime, SyntheticTransport, _journal_event_hashes,
)
from tools.v11_r09_gate3_store_v1 import ObjectReceipt, VersionedImmutableObjectStore


class RawBindingRefusal(ValueError):
    """The supplied offline lineage cannot release RAW bytes to A7."""


def _require(ok, reason):
    if not ok:
        raise RawBindingRefusal(reason)


def _heads(journal):
    hashes = _journal_event_hashes(journal.events)
    _require(bool(hashes) and hashes[-1] == journal.prev, 'BINDING_JOURNAL_HEAD')
    return hashes


def _persisted_heads(journal, hashes):
    """Compare every bounded durable record to the current cached lineage."""
    try:
        _require(len(hashes) == len(journal.events), 'BINDING_JOURNAL_DISK')
        offset = 0
        previous = '0' * 64
        for sequence, (event, head) in enumerate(zip(journal.events, hashes)):
            record = {'seq': sequence, 'prev': previous, 'event': event, 'hash': head}
            expected = canonical(record) + b'\n'
            _require(len(expected) <= 64 * 1024, 'BINDING_JOURNAL_DISK')
            actual = os.pread(journal.fd, len(expected), offset)
            _require(actual == expected, 'BINDING_JOURNAL_DISK')
            offset += len(expected)
            previous = head
        _require(offset == journal._journal_bytes == os.fstat(journal.fd).st_size and
                 offset <= 64 * 1024 ** 2, 'BINDING_JOURNAL_DISK')
    except RawBindingRefusal:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise RawBindingRefusal('BINDING_JOURNAL_DISK') from exc


@contextmanager
def _custody_guard(session, shared, budget, store):
    """Exclude public append/close through the instant of byte handoff."""
    with ExitStack() as stack:
        for lock in (session._mutex, shared._mutex, budget._custody_mutex,
                     store._binding_mutex, store._mutex):
            _require(lock.acquire(blocking=False), 'BINDING_CUSTODY_BUSY')
            stack.callback(lock.release)
        yield


def _healthy_journals(session, shared, budget, store):
    """Recheck the live owners and pinned pathnames at each release boundary."""
    try:
        session._healthy()
        shared._healthy()
        budget._healthy()
        store._usable()
    except (LaunchContractError, OSError, TypeError, ValueError) as exc:
        raise RawBindingRefusal('BINDING_CUSTODY') from exc


def _one(events, operation, identity, value):
    matches = [(event, head) for event, head in events
               if event.get('op') == operation and event.get(identity) == value]
    _require(len(matches) == 1, 'BINDING_JOURNAL_LINK')
    return matches[0]


def _read_raw_guarded(*, runtime: GateRuntime, request: AttemptRequest,
                    a7_request: dict, pins: Pins) -> bytes:
    """Return verified current-session RAW bytes for a separately pinned A7 call.

    The caller supplies A7 request identity and pins independently. The returned
    bytes and pins still need A7's own preflight; this is no decode or launch gate.
    """
    _require(type(runtime) is GateRuntime and type(request) is AttemptRequest and
             type(pins) is Pins and type(runtime.transport) is SyntheticTransport and
             runtime.plan.synthetic_fixture is True, 'BINDING_SYNTHETIC_ONLY')
    try:
        pins.validate()
        a7_source_sha256 = hashlib.sha256(source_bytes(a7_request)).hexdigest()
    except A7Refusal as exc:
        raise RawBindingRefusal('BINDING_PINS') from exc
    _require(pins.evidence_class == 'SYNTHETIC' and
             request.purpose == 'FIELD' and type(a7_request) is dict and
             a7_request.get('provider') ==
             {'IFS': 'ECMWF_IFS_ENS', 'AIFS': 'ECMWF_AIFS_ENS'}.get(request.provider) and
             a7_source_sha256 == pins.source_sha256 and
             pins.source_sha256 == request.source_pin and
             pins.decoder_sha256 == request.decoder_pin,
             'BINDING_PINS')
    _require(sum(r == request for r in runtime.plan.requests) == 1 and
             runtime.plan.manifest_sha256 == runtime.manifest_sha256,
             'BINDING_REQUEST')

    session, shared, budget, store = (runtime.session, runtime.shared,
                                      runtime.budget, runtime.store)
    _require(type(store) is VersionedImmutableObjectStore and
             store.report.classification == 'VALID' and
             session.manifest == runtime.manifest_sha256 and
             store.context['manifest'] == runtime.manifest_sha256 and
             not session.overdelivery_poisoned,
             'BINDING_CUSTODY')
    _healthy_journals(session, shared, budget, store)
    session_hashes = _heads(session)
    shared_hashes = _heads(shared)
    budget_hashes = _heads(budget)
    for journal, hashes in ((session, session_hashes), (shared, shared_hashes),
                            (budget, budget_hashes)):
        _persisted_heads(journal, hashes)
    custody_heads = (session.prev, shared.prev, budget.prev, store._head)
    rid = request.request_id
    attempt = session.attempt_history.get(rid)
    capture = session.capture_receipts.get(rid)
    budget_attempt = budget.attempts.get(rid)
    _require(type(attempt) is dict and attempt.get('state') == 'TERMINAL' and
             attempt.get('outcome') == 'SUCCESS' and
             attempt.get('purpose') == request.purpose and
             attempt.get('max_reservation_bytes') == request.reservation_bytes and
             attempt.get('denial_observed') is False and
             attempt.get('overdelivered') is False and type(capture) is dict and
             type(budget_attempt) is dict and
             budget_attempt.get('reserved') == request.reservation_bytes and
             budget_attempt.get('finished') is True and
             type(budget_attempt.get('received')) is int and
             budget_attempt.get('received') == attempt.get('total_delivered_bytes') and
             not budget.violated,
             'BINDING_SUCCESS_CAPTURE')

    session_events = list(zip(session.events, session_hashes))
    shared_events = list(zip(shared.events, shared_hashes))
    budget_events = list(zip(budget.events, budget_hashes))
    reserved, reserved_head = _one(budget_events, 'reserve', 'key', rid)
    budget_reserved, _ = _one(session_events, 'budget_reserved', 'request_id', rid)
    shared_open, _ = _one(shared_events, 'intent_open', 'request_id', rid)
    transport_closed, _ = _one(session_events, 'transport_closed', 'request_id', rid)
    accounted, _ = _one(session_events, 'accounted', 'request_id', rid)
    completion, completion_head = _one(budget_events, 'complete', 'key', rid)
    shared_close, _ = _one(shared_events, 'intent_closed', 'request_id', rid)
    completion_index = next(i for i, (event, _) in enumerate(budget_events)
                            if event is completion)
    shared_close_index = next(i for i, (event, _) in enumerate(shared_events)
                              if event is shared_close)
    _require(completion_index > 0 and shared_close_index > 0,
             'BINDING_CLOSURE')
    accounting_head = budget_events[completion_index - 1][1]
    denial_head = shared_events[shared_close_index - 1][1]
    try:
        encoded = transport_closed['closure_evidence_raw_b64']
        raw_closure = base64.b64decode(encoded, validate=True)
        closure = json.loads(raw_closure)
    except (KeyError, ValueError, TypeError) as exc:
        raise RawBindingRefusal('BINDING_CLOSURE') from exc
    _require(type(closure) is dict, 'BINDING_CLOSURE')
    _require(set(closure) == {'adapter', 'known_closed', 'status', 'headers',
              'prefetched_bytes', 'delivered_bytes', 'read_bytes',
              'chunks_consumed', 'chunk_count', 'deadline_monotonic',
              'closure_clock_sha256'} and
             canonical(closure) == raw_closure and
             hashlib.sha256(raw_closure).hexdigest() ==
                 transport_closed.get('closure_evidence_sha256'),
             'BINDING_CLOSURE')
    header_pairs = closure['headers']
    _require(type(header_pairs) is list and
             all(type(pair) is list and len(pair) == 2 and
                 all(type(item) is str for item in pair) for pair in header_pairs),
             'BINDING_CLOSURE')
    headers = {name.lower(): value for name, value in header_pairs}
    _require(len(headers) == len(header_pairs), 'BINDING_CLOSURE')
    closure_index = next(i for i, (event, _) in enumerate(session_events)
                         if event is transport_closed)
    closure_clock = (session_events[closure_index - 1][0]
                     if closure_index > 0 else None)
    delivered = attempt['total_delivered_bytes']
    accounted_bytes = sum(event['bytes'] for event, _ in budget_events
                          if event.get('key') == rid and
                          event.get('op') in ('chunk', 'eager_delivery'))
    _require(type(delivered) is int and delivered > 0 and
             accounted_bytes == delivered and
             not any(event.get('key') == rid and event.get('op') == 'violation'
                     for event, _ in budget_events) and
             not any(event.get('request_id') == rid and event.get('op') == 'denial'
                     for event, _ in session_events) and
             reserved.get('bytes') == request.reservation_bytes and
             budget_reserved.get('reserve_event_hash') == reserved_head and
             attempt.get('reserve_event_hash') == reserved_head and
             shared_open.get('purpose') == request.purpose and
             shared_open.get('endpoint_id') == request.endpoint_id and
             shared_open.get('control_domain_id') == request.control_domain_id and
             shared_open.get('manifest_sha256') == runtime.manifest_sha256 and
             shared_open.get('max_reservation_bytes') == request.reservation_bytes and
             transport_closed.get('outcome') == 'OK' and
             shared_close.get('outcome') == 'OK' and
             transport_closed.get('total_delivered_bytes') == delivered and
             shared_close.get('total_delivered_bytes') == delivered and
             closure.get('adapter') == 'SyntheticResponseStream' and
             closure.get('known_closed') is True and
             closure.get('status') == 206 and
             headers.get('content-length') == str(delivered) and
             headers.get('etag') == request.expected_etag and
             headers.get('content-range') ==
                 f'bytes {request.range_start}-{request.range_end}/'
                 f'{request.expected_object_bytes}' and
             headers.get('content-encoding', 'identity').lower() == 'identity' and
             'transfer-encoding' not in headers and
             'retry-after' not in headers and
             type(closure.get('delivered_bytes')) is int and
             closure['delivered_bytes'] == delivered and
             type(closure.get('prefetched_bytes')) is int and
             closure['prefetched_bytes'] == delivered and
             type(closure.get('read_bytes')) is int and
             closure['read_bytes'] == delivered and
             type(closure.get('chunks_consumed')) is int and
             type(closure.get('chunk_count')) is int and
             closure['chunks_consumed'] == closure['chunk_count'] and
             closure['chunk_count'] > 0 and
             closure.get('deadline_monotonic') == attempt.get('deadline_monotonic') and
             type(closure_clock) is dict and
             closure_clock.get('op') == 'clock_observed' and
             closure_clock.get('phase') == 'body_receipt' and
             closure_clock.get('evidence_sha256') ==
                 closure.get('closure_clock_sha256') and
             closure_clock.get('monotonic') ==
                 transport_closed.get('closure_monotonic') and
             transport_closed.get('closure_monotonic') == attempt.get('closure_monotonic') and
             transport_closed.get('closure_evidence_sha256') ==
                 attempt.get('closure_evidence_sha256') and
             transport_closed.get('denial_history_head') == denial_head and
             shared_close.get('denial_history_head') == denial_head and
             transport_closed.get('accounting_head') == accounting_head and
             shared_close.get('accounting_head') == accounting_head and
             accounted.get('completion_event_hash') == completion_head and
             attempt.get('completion_event_hash') == completion_head and
             completion.get('key') == rid,
             'BINDING_CLOSURE')

    intents = [event for event in session.events
               if event.get('op') == 'attempt_intent' and event.get('request_id') == rid]
    terminal = [(event, head) for event, head in zip(session.events, session_hashes)
                if event.get('op') == 'terminal' and event.get('request_id') == rid]
    witnessed = [event for event in session.events
                 if event.get('op') == 'object_witnessed' and event.get('request_id') == rid]
    captures = [event['record'] for event in session.events
                if event.get('op') == 'capture_receipt' and
                event['record'].get('request_id') == rid]
    closed = [(event, head) for event, head in zip(shared.events, shared_hashes)
              if event.get('op') == 'intent_closed' and event.get('request_id') == rid]
    completed = [(event, head) for event, head in zip(budget.events, budget_hashes)
                 if event.get('op') == 'complete' and event.get('key') == rid]
    _require(len(intents) == len(terminal) == len(witnessed) == len(captures) ==
             len(closed) == len(completed) == 1 and
             intents[0].get('purpose') == request.purpose and
             intents[0].get('endpoint_id') == request.endpoint_id and
             intents[0].get('range_start') == request.range_start and
             intents[0].get('range_end') == request.range_end and
             intents[0].get('validator_sha256') == request.validator_sha256 and
             intents[0].get('max_reservation_bytes') == request.reservation_bytes and
             terminal[0][0].get('outcome') == 'SUCCESS' and
             captures[0] == capture and
             capture.get('session_terminal_head') == terminal[0][1] and
             capture.get('shared_head') == closed[0][1] and
             capture.get('budget_head') == completed[0][1],
             'BINDING_JOURNAL_LINK')

    deps = list(request.dependency_commit_hashes)
    prior_ids = [r.request_id for r in runtime.plan.requests]
    for prior_id in request.prerequisite_request_ids:
        prior = session.capture_receipts.get(prior_id)
        _require(prior_id in prior_ids and prior_ids.index(prior_id) < prior_ids.index(rid)
                 and type(prior) is dict and prior.get('outcome') == 'SUCCESS' and
                 session.attempt_history.get(prior_id, {}).get('outcome') == 'SUCCESS' and
                 prior.get('store_receipt_commit_hash') is not None,
                 'BINDING_DEPENDENCY')
        deps.append(prior['store_receipt_commit_hash'])
    commit = capture.get('store_receipt_commit_hash')
    _require(capture.get('version') == 1 and
             capture.get('manifest') == runtime.manifest_sha256 and
             capture.get('plan_sha256') == runtime.plan.sha256 and
             capture.get('request_sha256') ==
             hashlib.sha256(canonical(asdict(request))).hexdigest() and
             capture.get('request_id') == rid and
             capture.get('purpose') == request.purpose and
             capture.get('endpoint_id') == request.endpoint_id and
             capture.get('source_pin') == request.source_pin and
             capture.get('decoder_pin') == request.decoder_pin and
             capture.get('outcome') == 'SUCCESS' and
             capture.get('dependencies') == deps and
             capture.get('prerequisite_request_ids') ==
             list(request.prerequisite_request_ids) and
             capture.get('known_delivered_bytes') ==
             attempt.get('total_delivered_bytes') and
             capture.get('deadline_monotonic') == attempt.get('deadline_monotonic') and
             capture.get('closure_monotonic') == attempt.get('closure_monotonic') and
             capture.get('closure_evidence_sha256') ==
             attempt.get('closure_evidence_sha256') and
             type(commit) is str and witnessed[0].get('store_receipt_commit_hash') == commit,
             'BINDING_CAPTURE_MISMATCH')

    receipts = [r for r in store.receipts.values() if r.commit_hash == commit]
    _require(len(receipts) == 1 and type(receipts[0]) is ObjectReceipt,
             'BINDING_STORE_COMMIT')
    receipt = receipts[0]
    provenance = receipt.provenance
    _require(receipt.acknowledgement == 'ACKNOWLEDGED_THIS_SESSION' and
             receipt.object_seal_witnessed is True and
             receipt.historical_feature_eligible is False and
             provenance.kind == 'RAW' and provenance.request_id == rid and
             provenance.attempt_id == rid and provenance.dependencies == () and
             provenance.source_pin == request.source_pin and
             provenance.decoder_pin == request.decoder_pin and
             provenance.clock_policy_sha256 == request.clock_policy_sha256 and
             capture.get('raw_sha256') == receipt.object_sha256 == pins.raw_sha256 and
             capture.get('clock_evidence_sha256') ==
             [e.reading.evidence_sha256 for e in receipt.clocks] and
             receipt.length == capture['known_delivered_bytes'],
             'BINDING_RAW_PROVENANCE')
    try:
        raw = store.read_receipt(receipt)
    except LaunchContractError as exc:
        raise RawBindingRefusal('BINDING_READ_RECEIPT') from exc
    _require(type(raw) is bytes and len(raw) == receipt.length and
             hashlib.sha256(raw).hexdigest() == receipt.object_sha256,
             'BINDING_RAW_BYTES')
    _healthy_journals(session, shared, budget, store)
    _require((session.prev, shared.prev, budget.prev, store._head) == custody_heads and
             _heads(session) == session_hashes and
             _heads(shared) == shared_hashes and
             _heads(budget) == budget_hashes,
             'BINDING_CUSTODY_CHANGED')
    for journal, hashes in ((session, session_hashes), (shared, shared_hashes),
                            (budget, budget_hashes)):
        _persisted_heads(journal, hashes)
    return raw


def read_raw_for_a7(*, runtime: GateRuntime, request: AttemptRequest,
                    a7_request: dict, pins: Pins) -> bytes:
    _require(type(runtime) is GateRuntime, 'BINDING_SYNTHETIC_ONLY')
    with _custody_guard(runtime.session, runtime.shared, runtime.budget,
                        runtime.store):
        return _read_raw_guarded(runtime=runtime, request=request,
                                 a7_request=a7_request, pins=pins)
