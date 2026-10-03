"""Synthetic offline RAW custody handoff for A7 input bytes.

This checks an existing successful Gate-3 attempt. It never dispatches, decodes,
creates pins, or treats replayed store acknowledgement as current custody.
"""
from dataclasses import asdict
import hashlib

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


def read_raw_for_a7(*, runtime: GateRuntime, request: AttemptRequest,
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
    session_hashes = _heads(session)
    shared_hashes = _heads(shared)
    budget_hashes = _heads(budget)
    rid = request.request_id
    attempt = session.attempt_history.get(rid)
    capture = session.capture_receipts.get(rid)
    budget_attempt = budget.attempts.get(rid)
    _require(type(attempt) is dict and attempt.get('state') == 'TERMINAL' and
             attempt.get('outcome') == 'SUCCESS' and
             attempt.get('purpose') == request.purpose and
             attempt.get('max_reservation_bytes') == request.reservation_bytes and
             not attempt.get('denial_observed') and
             not attempt.get('overdelivered') and type(capture) is dict and
             type(budget_attempt) is dict and
             budget_attempt.get('reserved') == request.reservation_bytes and
             budget_attempt.get('finished') is True and
             budget_attempt.get('received') == attempt.get('total_delivered_bytes') and
             not budget.violated,
             'BINDING_SUCCESS_CAPTURE')

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
    return raw
