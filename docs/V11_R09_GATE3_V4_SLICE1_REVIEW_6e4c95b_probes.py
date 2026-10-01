"""Diagnostic reproductions for exact 6e4c95b, NOT acceptance tests.

A passing defect probe means the defect exists. Run with PYTHONPATH pointing
at the exact candidate; no transport, services, or real evidence is used.
"""
import hashlib
import importlib.util
from pathlib import Path
import pytest
from tools import v11_r09_gate3_launch as launch
from tools import v11_r09_gate3_launch_v4 as v4
from tools.v11_multimodel_panel import canonical

_spec = importlib.util.spec_from_file_location('v4_fixture', Path(v4.__file__).parents[1] / 'tests/test_v11_r09_gate3_launch_v4.py')
fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture)


def test_control_small_journal_restarts(tmp_path):
    root = tmp_path / 'budget'
    root.mkdir(mode=0o700)
    with launch.DurableBudget(root, 'a' * 64, max_bytes=1000) as budget:
        budget.reserve('one', 1000, started_monotonic=0)
        budget.consume('one', b'x')
        budget.complete('one')
    with launch.DurableBudget(root, 'a' * 64, max_bytes=1000) as budget:
        assert budget.received == budget.reserved == 1
        assert budget.in_flight is None


def test_defect_writer_valid_records_crossing_read_boundary_rejected(tmp_path):
    root = tmp_path / 'budget'
    root.mkdir(mode=0o700)
    with launch.DurableBudget(root, 'b' * 64, max_bytes=1000) as budget:
        budget.reserve('one', 1000, started_monotonic=0)
        for _ in range(700):
            budget.consume('one', b'x')
        budget.complete('one')
    raw = (root / 'gate3.jsonl').read_bytes()
    assert 2 * launch.JOURNAL_READ_CHUNK_BYTES < len(raw) < launch.JOURNAL_MAX_BYTES
    assert all(len(line) < 1024 for line in raw.splitlines(keepends=True))
    with pytest.raises(launch.LaunchContractError, match='JOURNAL_RECORD_TOO_LARGE'):
        launch.DurableBudget(root, 'b' * 64, max_bytes=1000)


def test_defect_capacity_refusal_can_complete_and_refund_unknown_bytes(tmp_path, monkeypatch):
    root = tmp_path / 'budget'
    root.mkdir(mode=0o700)
    with launch.DurableBudget(root, 'c' * 64, max_bytes=1000) as budget:
        budget.reserve('one', 1000, started_monotonic=0)
        record = {'seq': len(budget.events), 'prev': budget.prev,
                  'event': {'op': 'complete', 'key': 'one'}}
        record['hash'] = hashlib.sha256(canonical(record)).hexdigest()
        # Remaining capacity fits complete but not the larger chunk event.
        monkeypatch.setattr(launch, 'JOURNAL_MAX_BYTES', budget._journal_bytes + len(canonical(record)) + 1)
        with pytest.raises(launch.LaunchContractError, match='JOURNAL_CAPACITY_EXCEEDED'):
            budget.consume('one', b'xyz')
        assert budget.received == budget.uncertain_received_bytes == 3
        assert budget.in_flight == 'one' and not budget.failed
        budget.complete('one')
        assert budget.in_flight is None
        assert budget.received == budget.reserved == 0
        assert budget.uncertain_received_bytes == 3
    with launch.DurableBudget(root, 'c' * 64, max_bytes=1000) as budget:
        assert budget.received == budget.reserved == budget.uncertain_received_bytes == 0
        assert budget.in_flight is None


def test_defect_separate_explicit_index_endpoint_cannot_be_represented(tmp_path, monkeypatch):
    payload, repo, root, start = fixture.candidate(tmp_path, monkeypatch)
    endpoint = next(e for e in payload['network']['endpoints'] if e['provider'] == 'GEFS' and e['purpose'] == 'INDEX')
    endpoint['path_template'] += '.idx'
    endpoint['endpoint_id'] = fixture._endpoint_id(endpoint['provider'], endpoint['origin'], endpoint['path_template'], endpoint['purpose'])
    request = payload['schedule']['requests'][0]
    request['endpoint_id'] = endpoint['endpoint_id']
    request['path'] += '.idx'
    fixture._freeze_runtime(payload)
    with pytest.raises(launch.LaunchContractError, match='ENDPOINT_SOURCE_BINDING'):
        fixture.validate(payload, repo, root, start)


def test_defect_v3_time_bound_accepts_v4_infeasible_schedule(tmp_path, monkeypatch):
    payload, repo, root, start = fixture.candidate(tmp_path, monkeypatch)
    payload['limits']['max_elapsed_seconds'] = 240
    n = len(payload['schedule']['requests'])
    needed = n * 30 + (n - 1) * 2 + 60 + 60
    assert needed == 246
    assert len(fixture.validate(payload, repo, root, start)) == 64


def test_defect_v4_requires_no_design_review_reference(tmp_path, monkeypatch):
    payload, repo, root, start = fixture.candidate(tmp_path, monkeypatch)
    assert {r['name'] for r in payload['protocol']['reviews']} == launch.REQUIRED_REVIEW_NAMES
    assert len(payload['protocol']['reviews']) == 5
    assert len(fixture.validate(payload, repo, root, start)) == 64


def test_defect_report_reserve_smaller_than_design_passes(tmp_path, monkeypatch):
    payload, repo, root, start = fixture.candidate(tmp_path, monkeypatch)
    assert payload['storage']['report_reserve_bytes'] == 4096 < 16 * 1024 ** 2
    assert len(fixture.validate(payload, repo, root, start)) == 64


def test_defect_metadata_cap_above_v4_contract_passes(tmp_path, monkeypatch):
    payload, repo, root, start = fixture.candidate(tmp_path, monkeypatch)
    payload['limits']['metadata'] = 5 * 1024 ** 2
    for request in payload['schedule']['requests']:
        if request['purpose'] in ('OBJECT_ID', 'METADATA', 'PROBE'):
            request['reservation_bytes'] = payload['limits']['metadata']
    payload['schedule']['reservation_total_bytes'] = sum(r['reservation_bytes'] for r in payload['schedule']['requests'])
    fixture._freeze_runtime(payload)
    assert len(fixture.validate(payload, repo, root, start)) == 64


def test_control_purpose_shuffle_rejected_after_total_preserved(tmp_path, monkeypatch):
    payload, repo, root, start = fixture.candidate(tmp_path, monkeypatch)
    payload['runtime']['purpose_plan']['FIELD']['reservation_bytes'] -= 1
    payload['runtime']['purpose_plan']['INDEX']['reservation_bytes'] += 1
    with pytest.raises(launch.LaunchContractError, match='RUNTIME_PURPOSE_PLAN_MISMATCH'):
        fixture.validate(payload, repo, root, start)
