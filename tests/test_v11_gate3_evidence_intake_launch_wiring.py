"""Synthetic, offline tests for the optional real-evidence-intake
pre-dispatch gate wired onto ``GateRuntime``
(tools/v11_r09_gate3_runtime.py's ``evidence_intake`` parameter, backed by
tools/v11_gate3_evidence_intake_guard.py's ``EvidenceIntakeGuard``).

The independent review of the merged real-evidence intake
(tools/v11_gate3_evidence_preflight_real_intake.py) found it was a
standalone, read-only status reporter with no collector/launch caller --
nothing in the actual Gate 3 launch path consulted its report before a
future transport attempt. This suite proves, against the real
``GateRuntime``/``run_attempt`` entrypoints, that the new wiring closes
that gap without touching the actual dispatch, decode, credential or
provider-authority boundary anywhere:

  * a runtime built without ``evidence_intake`` is completely unaffected
    (backward compatibility for every existing caller/test);
  * a runtime built with a guard bound to a SATISFIED record proceeds only
    to the next existing gate -- it is not itself a dispatch bypass, and an
    earlier-failing gate (window) still blocks it;
  * a guard bound to an UNSATISFIED record blocks ``run_attempt`` before
    any durable session/shared/budget mutation and before transport is ever
    reached;
  * the evidence-intake gate and a bound ``AttemptModelGuard`` are each
    independently authoritative: either one refusing blocks dispatch
    regardless of what the other would have decided;
  * malformed, tampered or internally inconsistent reports fail closed at
    construction (``EvidenceIntakeRecord.from_report`` /
    ``__post_init__``), never silently admitted;
  * the guard is stateless and deterministic -- repeated calls never
    escape into a different verdict (no retry path to a later admission);
  * composition itself refuses a wrong-typed ``evidence_intake`` outright;
  * the one sanctioned real-evidence route
    (``EvidenceIntakeGuard.from_real_intake``) round-trips a real
    ``evaluate_real_evidence``/``build_report`` outcome (synthetic fixture
    bytes only -- never the actual private root) into an admitting guard.

No network, no subprocess, no real clock, no real transport anywhere in
this module.
"""
from __future__ import annotations

import dataclasses
import hashlib

import pytest

from tools.v11_multimodel_panel import canonical
from tools.v11_r09_gate3_launch import LaunchContractError
from tools.v11_r09_gate3_offline_io import OfflineResponse, SyntheticExchange
from tools.v11_r09_gate3_runtime import (
    AbsoluteWindow, AttemptModelGuard, AttemptRequest, EvidenceIntakeGuard,
    FakeClock, FakeResourceProbe, FrozenPlan, GateRuntime, ReportSink,
    SyntheticTransport, acquire_runtime_journals,
)
from tools.v11_gate3_evidence_intake_guard import EvidenceIntakeRecord
from tools.v11_gate3_evidence_preflight_checker import FROZEN_REQUEST
from tools.v11_gate3_evidence_preflight_real_intake import (
    SCHEMA as REAL_INTAKE_SCHEMA, build_report, evaluate_real_evidence,
)
from tests.v11_gate3_preflight_synthetic_cases import (
    GOOD_CLOCK, GOOD_RESOURCES, GOOD_REVIEW_TERMINAL, RETAINED_22_REASONS,
    all_blocked_package_and_restrictions, encode_raws, good_raws,
)

MANIFEST = 'a' * 64
BOOT = 'boot-wire-evidence'
GENESIS = '7' * 64
ETAG = '"obj-1"'
ORIGIN = 'https://weather.example.invalid'
PILOT_ORIGIN = FROZEN_REQUEST['origin']
PILOT_PATH = FROZEN_REQUEST['path']
GENERATED_AT = '2026-10-02T20:06:59.507417Z'


def _satisfied_record(**overrides):
    base = dict(satisfied=True, outcome='CHECKER_SCHEMA_AND_POLICY_SATISFIED_NOT_EXECUTABLE',
                refusal_reasons=(), intake_schema=REAL_INTAKE_SCHEMA,
                generated_at_utc=GENERATED_AT)
    base.update(overrides)
    return EvidenceIntakeRecord(**base)


def _unsatisfied_record(**overrides):
    base = dict(satisfied=False, outcome='CHECKER_REFUSED_BEFORE_DISPATCH',
                refusal_reasons=('NULL_COMPLETE_LINEAGE_REVIEW',),
                intake_schema=REAL_INTAKE_SCHEMA, generated_at_utc=GENERATED_AT)
    base.update(overrides)
    return EvidenceIntakeRecord(**base)


def _report(**overrides):
    base = dict(schema='R09_GATE3_EVIDENCE_PREFLIGHT_PACKAGE_CHECKER_V1',
                intake_schema=REAL_INTAKE_SCHEMA,
                outcome='CHECKER_SCHEMA_AND_POLICY_SATISFIED_NOT_EXECUTABLE',
                eligibility='DISCOVERY_ONLY_NOT_G3E', refusal_reasons=[],
                generated_at_utc=GENERATED_AT, satisfied=True,
                execution_authority=False, provider_authority=False,
                capture_authority=False)
    base.update(overrides)
    return base


def _dirs(tmp_path):
    for name in ('shared', 'session', 'budget', 'store'):
        (tmp_path / name).mkdir(mode=0o700)
    (tmp_path / 'store' / 'objects').mkdir(mode=0o700)
    return tmp_path


def _acquire(tmp_path):
    return acquire_runtime_journals(
        shared_dir=tmp_path / 'shared', session_dir=tmp_path / 'session',
        budget_dir=tmp_path / 'budget', store_root=tmp_path / 'store',
        shared_kwargs=dict(boot_id=BOOT, genesis_review_digest=GENESIS),
        session_kwargs=dict(manifest_sha256=MANIFEST, boot_id=BOOT),
        budget_kwargs=dict(manifest_sha256=MANIFEST, max_bytes=1 << 20, boot_id=BOOT),
        store_kwargs=dict(manifest_sha256=MANIFEST, policy_sha256='b' * 64,
                           build_id='fixture', clock_method='synthetic',
                           max_clock_age_seconds=30, host_id='fixture-host',
                           boot_id=BOOT),
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


def _pilot_request(**overrides):
    base = dict(request_id='req-pilot', purpose='INDEX', endpoint_id='c' * 64,
                control_domain_id='d' * 64, origin=PILOT_ORIGIN, path=PILOT_PATH,
                provider=None, slot_index=5, range_start=None, range_end=None,
                reservation_bytes=32, expected_etag=ETAG, expected_object_bytes=None,
                source_pin='e' * 64, decoder_pin='f' * 64, clock_policy_sha256='1' * 64)
    base.update(overrides)
    return AttemptRequest(**base)


def _frozen_plan(requests, window=None):
    window = window or _window()
    raw = canonical({'schema_version': 1, 'manifest_sha256': MANIFEST,
        'window_sha256': hashlib.sha256(canonical(dataclasses.asdict(window))).hexdigest(),
        'request_schedule_sha256': hashlib.sha256(
            canonical([dataclasses.asdict(r) for r in requests])).hexdigest(),
        'event_schedule_sha256': hashlib.sha256(canonical([])).hexdigest()})
    return FrozenPlan(MANIFEST, hashlib.sha256(raw).hexdigest(), window,
                      requests, raw, (), synthetic_fixture=True)


def _ok_response(body=b'0123456789012345678901234567890', etag=ETAG):
    chunks = (body[:10], body[10:]) if len(body) > 10 else (body,)
    return OfflineResponse(200, (('Content-Length', str(len(body))), ('ETag', etag)),
                            chunks, '8.8.8.8', True)


def _build_runtime(shared, session, budget, store, tmp_path, *, requests,
                    exchange, attempt_model=None, evidence_intake=None, window=None):
    plan = _frozen_plan(requests, window)
    report_dir = tmp_path / 'report'
    report_dir.mkdir(mode=0o700, exist_ok=True)
    sink = ReportSink(report_dir)
    return GateRuntime(shared=shared, session=session, budget=budget, store=store,
        transport=SyntheticTransport(exchange), clock=_clock(), resources=_resources(),
        window=window or _window(), allowed_peer_ips=('8.8.8.8',),
        manifest_sha256=MANIFEST, plan=plan, expected_plan_sha256=plan.sha256,
        report_sink=sink, attempt_model=attempt_model, evidence_intake=evidence_intake)


# ---------------------------------------------------------------------------
# Backward compatibility: no guard configured.
# ---------------------------------------------------------------------------

def test_no_evidence_intake_is_fully_backward_compatible(tmp_path):
    tmp_path = _dirs(tmp_path)
    body = b'0123456789012345678901234567890'
    exchange = SyntheticExchange({'req-1': _ok_response(body)})
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _build_runtime(shared, session, budget, store, tmp_path,
            requests=(_request(reservation_bytes=len(body)),), exchange=exchange)
        outcome = runtime.run_attempt(_request(reservation_bytes=len(body)))
        assert outcome['outcome'] == 'SUCCESS'
        assert runtime.evidence_intake is None


# ---------------------------------------------------------------------------
# A satisfied record allows reaching the next gate -- it is not itself a
# dispatch bypass.
# ---------------------------------------------------------------------------

def test_satisfied_intake_allows_normal_dispatch(tmp_path):
    tmp_path = _dirs(tmp_path)
    body = b'0123456789012345678901234567890'
    exchange = SyntheticExchange({'req-1': _ok_response(body)})
    guard = EvidenceIntakeGuard(_satisfied_record())
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _build_runtime(shared, session, budget, store, tmp_path,
            requests=(_request(reservation_bytes=len(body)),), exchange=exchange,
            evidence_intake=guard)
        outcome = runtime.run_attempt(_request(reservation_bytes=len(body)))
        assert outcome['outcome'] == 'SUCCESS'
        assert budget.received == len(body)


def test_satisfied_intake_does_not_bypass_an_earlier_failing_gate(tmp_path):
    """Proves 'proceed only to the next existing gate, never bypassing it':
    an otherwise-admitting evidence-intake guard still lets the window gate
    refuse when the fixture clock sits before the window start."""
    tmp_path = _dirs(tmp_path)
    exchange = SyntheticExchange({})  # .take() would raise: dispatch must never be reached
    guard = EvidenceIntakeGuard(_satisfied_record())
    window = _window(start_utc=10_000, acquisition_end_utc=20_000, decision_lower_utc=21_000)
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _build_runtime(shared, session, budget, store, tmp_path,
            requests=(_request(),), exchange=exchange, evidence_intake=guard, window=window)
        outcome = runtime.run_attempt(_request())
        assert outcome['outcome'] == 'REFUSED'
        assert 'RUNTIME_CLOCK_BEFORE_WINDOW_START' in outcome['reasons']
        assert 'RUNTIME_EVIDENCE_INTAKE_NOT_SATISFIED' not in outcome['reasons']


# ---------------------------------------------------------------------------
# An unsatisfied record blocks dispatch before any durable mutation.
# ---------------------------------------------------------------------------

def test_unsatisfied_intake_blocks_before_any_durable_mutation(tmp_path):
    tmp_path = _dirs(tmp_path)
    exchange = SyntheticExchange({})  # .take() would raise: dispatch must never be reached
    guard = EvidenceIntakeGuard(_unsatisfied_record())
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _build_runtime(shared, session, budget, store, tmp_path,
            requests=(_request(),), exchange=exchange, evidence_intake=guard)
        prev_before, events_before = shared.prev, list(shared.events)
        outcome = runtime.run_attempt(_request())
        assert outcome['outcome'] == 'REFUSED'
        assert 'RUNTIME_EVIDENCE_INTAKE_NOT_SATISFIED' in outcome['reasons']
        assert budget.in_flight is None
        assert budget.count == 0
        assert session.attempt['outcome'] == 'REFUSED'
        # The shared denial ledger is completely untouched by this refusal.
        assert shared.prev == prev_before
        assert list(shared.events) == events_before


# ---------------------------------------------------------------------------
# Composed with AttemptModelGuard: each gate is independently authoritative.
# ---------------------------------------------------------------------------

def test_unsatisfied_intake_blocks_even_when_attempt_model_would_admit(tmp_path):
    tmp_path = _dirs(tmp_path)
    from tests.v11_gate3_preflight_synthetic_cases import genesis_checkpoint, good_inputs
    exchange = SyntheticExchange({})  # .take() would raise: dispatch must never be reached
    attempt_guard = AttemptModelGuard(good_inputs(), genesis_checkpoint())
    evidence_guard = EvidenceIntakeGuard(_unsatisfied_record())
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _build_runtime(shared, session, budget, store, tmp_path,
            requests=(_pilot_request(),), exchange=exchange,
            attempt_model=attempt_guard, evidence_intake=evidence_guard)
        outcome = runtime.run_attempt(_pilot_request())
        assert outcome['outcome'] == 'REFUSED'
        assert 'RUNTIME_EVIDENCE_INTAKE_NOT_SATISFIED' in outcome['reasons']
        # The evidence-intake refusal is checked first, so the attempt-model
        # guard (which would otherwise have admitted) is never even consumed.
        assert attempt_guard._consumed is False
        assert budget.count == 0


def test_attempt_model_still_refuses_even_when_evidence_intake_satisfied(tmp_path):
    """The existing attempt-model gate remains authoritative: a satisfied
    evidence-intake guard must never let a refused attempt-model guard's
    verdict be bypassed."""
    tmp_path = _dirs(tmp_path)
    from tests.v11_gate3_preflight_synthetic_cases import genesis_checkpoint, good_inputs
    exchange = SyntheticExchange({})
    attempt_guard = AttemptModelGuard(
        good_inputs(), genesis_checkpoint(unfinished_intents=('synthetic://intent/open',)))
    evidence_guard = EvidenceIntakeGuard(_satisfied_record())
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _build_runtime(shared, session, budget, store, tmp_path,
            requests=(_pilot_request(),), exchange=exchange,
            attempt_model=attempt_guard, evidence_intake=evidence_guard)
        outcome = runtime.run_attempt(_pilot_request())
        assert outcome['outcome'] == 'REFUSED'
        assert 'RUNTIME_ATTEMPT_MODEL_REFUSED' in outcome['reasons']
        assert budget.count == 0


# ---------------------------------------------------------------------------
# Composition itself refuses a wrong-typed guard outright.
# ---------------------------------------------------------------------------

def test_runtime_rejects_wrong_typed_evidence_intake(tmp_path):
    tmp_path = _dirs(tmp_path)
    exchange = SyntheticExchange({})
    with _acquire(tmp_path) as (shared, session, budget, store):
        with pytest.raises(LaunchContractError, match='RUNTIME_COMPOSITION_SHAPE'):
            _build_runtime(shared, session, budget, store, tmp_path,
                requests=(_request(),), exchange=exchange, evidence_intake=object())


# ---------------------------------------------------------------------------
# Stateless/deterministic: no retry path to a later admission.
# ---------------------------------------------------------------------------

def test_guard_require_admission_is_deterministic_across_repeated_calls():
    satisfied_guard = EvidenceIntakeGuard(_satisfied_record())
    for _ in range(5):
        satisfied_guard.require_admission()  # never raises

    unsatisfied_guard = EvidenceIntakeGuard(_unsatisfied_record())
    for _ in range(5):
        with pytest.raises(LaunchContractError, match='RUNTIME_EVIDENCE_INTAKE_NOT_SATISFIED'):
            unsatisfied_guard.require_admission()


def test_no_duplicate_dispatch_across_repeated_run_attempt_calls_on_same_request(tmp_path):
    """An unsatisfied guard refuses the same request deterministically; the
    existing request-id-reuse contract still prevents any repeated call
    from reaching transport.dispatch a second time."""
    tmp_path = _dirs(tmp_path)
    exchange = SyntheticExchange({})
    guard = EvidenceIntakeGuard(_unsatisfied_record())
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _build_runtime(shared, session, budget, store, tmp_path,
            requests=(_request(),), exchange=exchange, evidence_intake=guard)
        first = runtime.run_attempt(_request())
        assert first['outcome'] == 'REFUSED'
        with pytest.raises(LaunchContractError, match='RUNTIME_FROZEN_REQUEST_MISMATCH'):
            runtime.run_attempt(_request())
        assert budget.count == 0


# ---------------------------------------------------------------------------
# EvidenceIntakeRecord/from_report: malformed or tampered reports fail
# closed at construction, never silently admitted.
# ---------------------------------------------------------------------------

def test_from_report_rejects_missing_key():
    report = _report()
    del report['eligibility']
    with pytest.raises(LaunchContractError, match='EVIDENCE_INTAKE_REPORT_SCHEMA'):
        EvidenceIntakeRecord.from_report(report)


def test_from_report_rejects_extra_key():
    report = _report(extra_field='unexpected')
    with pytest.raises(LaunchContractError, match='EVIDENCE_INTAKE_REPORT_SCHEMA'):
        EvidenceIntakeRecord.from_report(report)


def test_from_report_rejects_non_dict():
    with pytest.raises(LaunchContractError, match='EVIDENCE_INTAKE_REPORT_SCHEMA'):
        EvidenceIntakeRecord.from_report(['not', 'a', 'dict'])


@pytest.mark.parametrize('key', ['execution_authority', 'provider_authority', 'capture_authority'])
def test_from_report_rejects_authority_claimed_true(key):
    report = _report(**{key: True})
    with pytest.raises(LaunchContractError, match='EVIDENCE_INTAKE_REPORT_AUTHORITY_FORBIDDEN'):
        EvidenceIntakeRecord.from_report(report)


def test_from_report_rejects_wrong_intake_schema():
    report = _report(intake_schema='SOME_OTHER_SCHEMA')
    with pytest.raises(LaunchContractError, match='EVIDENCE_INTAKE_RECORD_SCHEMA'):
        EvidenceIntakeRecord.from_report(report)


def test_record_rejects_satisfied_true_with_nonempty_refusal_reasons():
    with pytest.raises(LaunchContractError, match='EVIDENCE_INTAKE_RECORD_CONSISTENCY'):
        EvidenceIntakeRecord(satisfied=True, outcome='x', refusal_reasons=('SOMETHING',),
                             intake_schema=REAL_INTAKE_SCHEMA, generated_at_utc=GENERATED_AT)


def test_record_rejects_satisfied_false_with_empty_refusal_reasons():
    with pytest.raises(LaunchContractError, match='EVIDENCE_INTAKE_RECORD_CONSISTENCY'):
        EvidenceIntakeRecord(satisfied=False, outcome='x', refusal_reasons=(),
                             intake_schema=REAL_INTAKE_SCHEMA, generated_at_utc=GENERATED_AT)


def test_record_rejects_non_bool_satisfied():
    with pytest.raises(LaunchContractError, match='EVIDENCE_INTAKE_RECORD_SHAPE'):
        EvidenceIntakeRecord(satisfied=1, outcome='x', refusal_reasons=(),
                             intake_schema=REAL_INTAKE_SCHEMA, generated_at_utc=GENERATED_AT)


def test_guard_rejects_wrong_typed_record():
    with pytest.raises(LaunchContractError, match='EVIDENCE_INTAKE_GUARD_SHAPE'):
        EvidenceIntakeGuard(_report())


# ---------------------------------------------------------------------------
# The one sanctioned real-evidence route round-trips a real
# evaluate_real_evidence/build_report outcome into an admitting (or
# refusing) guard. Synthetic fixture bytes only, never the actual private
# root -- matching the real-intake module's own established test convention.
# ---------------------------------------------------------------------------

def test_from_report_round_trips_a_real_satisfied_checker_outcome():
    package_raw, restrictions_raw, protocol_raw, binding_raw = good_raws()
    result = evaluate_real_evidence(
        package_raw, restrictions_raw, protocol_raw, binding_raw,
        clock=GOOD_CLOCK, resources=GOOD_RESOURCES,
        review_terminal=dict(GOOD_REVIEW_TERMINAL))
    report = build_report(result, generated_at_utc=GOOD_CLOCK.measured_utc)
    record = EvidenceIntakeRecord.from_report(report)
    guard = EvidenceIntakeGuard(record)
    guard.require_admission()  # never raises


def test_from_report_round_trips_a_real_refused_checker_outcome():
    pkg, restrictions = all_blocked_package_and_restrictions()
    package_raw, restrictions_raw, protocol_raw, binding_raw = encode_raws(pkg, restrictions)
    result = evaluate_real_evidence(
        package_raw, restrictions_raw, protocol_raw, binding_raw,
        clock=GOOD_CLOCK, resources=GOOD_RESOURCES)
    report = build_report(result, generated_at_utc=GOOD_CLOCK.measured_utc)
    record = EvidenceIntakeRecord.from_report(report)
    assert record.satisfied is False
    assert set(record.refusal_reasons) == set(RETAINED_22_REASONS)
    guard = EvidenceIntakeGuard(record)
    with pytest.raises(LaunchContractError, match='RUNTIME_EVIDENCE_INTAKE_NOT_SATISFIED'):
        guard.require_admission()


def test_from_real_intake_builds_a_refusing_guard_from_synthetic_tmp_path_files(tmp_path):
    """Exercises ``EvidenceIntakeGuard.from_real_intake`` end-to-end, the one
    sanctioned production route, against freshly fabricated synthetic
    retained files under ``tmp_path`` -- never the actual private root."""
    import json
    from tools.v11_gate3_evidence_preflight_real_intake import NO_QUALIFIED_CLOCK_SECONDS
    from tests.v11_gate3_preflight_synthetic_cases import (
        good_binding_dict, protocol_raw_bytes,
    )

    def _ref_for(raw, path):
        return {'path': path, 'sha256': hashlib.sha256(raw).hexdigest(), 'byte_length': len(raw)}

    pkg, restrictions = all_blocked_package_and_restrictions()
    protocol_raw = protocol_raw_bytes()
    package_raw, restrictions_raw, _, _ = encode_raws(pkg, restrictions, protocol_raw)

    private_root = tmp_path / 'private'
    private_root.mkdir()
    (private_root / 'package.json').write_bytes(package_raw)
    (private_root / 'restriction-history.json').write_bytes(restrictions_raw)

    docs_dir = tmp_path / 'docs'
    docs_dir.mkdir()
    (docs_dir / 'PROTOCOL.md').write_bytes(protocol_raw)

    binding = good_binding_dict(package_raw, restrictions_raw, protocol_raw)
    binding['private_package'] = _ref_for(package_raw, str(private_root / 'package.json'))
    binding['private_restrictions'] = _ref_for(
        restrictions_raw, str(private_root / 'restriction-history.json'))
    binding['protocol'] = _ref_for(protocol_raw, '/original/authoring/path/PROTOCOL.md')
    binding_path = tmp_path / 'BINDING.json'
    binding_path.write_bytes(json.dumps(binding).encode())

    guard = EvidenceIntakeGuard.from_real_intake(repo_docs_dir=docs_dir, binding_path=binding_path)
    assert guard.record.satisfied is False
    assert set(RETAINED_22_REASONS) <= set(guard.record.refusal_reasons)
    with pytest.raises(LaunchContractError, match='RUNTIME_EVIDENCE_INTAKE_NOT_SATISFIED'):
        guard.require_admission()
