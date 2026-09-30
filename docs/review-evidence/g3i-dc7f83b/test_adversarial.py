"""Independent offline reproductions for dc7f83b; passing defect tests prove gaps."""
import hashlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
import pytest

CANDIDATE = Path('/tmp/alpha-v11-r09-gate3-strict-offline-20260930')
sys.path.insert(0, str(CANDIDATE))
from tools import v11_r09_gate3_launch as launch
from tools.v11_multimodel_panel import canonical
spec = importlib.util.spec_from_file_location('author_fixture', CANDIDATE / 'tests/test_v11_r09_gate3_launch.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def accepted(payload, repo, root, start):
    result = fixture.validate(payload, repo, root, start)
    assert result == hashlib.sha256(canonical(payload)).hexdigest()


def test_defect_nonzero_cycle_minutes_and_seconds_accepted(tmp_path, monkeypatch):
    p, repo, root, start = fixture.candidate(tmp_path, monkeypatch)
    for provider in p['runs_and_slots']['run_utc']:
        p['runs_and_slots']['run_utc'][provider] += 3599
    p['runs_and_slots']['slots'] = launch._slot_inventory(p['runs_and_slots']['run_utc'])
    p['schedule']['slot_inventory_sha256'] = hashlib.sha256(canonical(p['runs_and_slots']['slots'])).hexdigest()
    accepted(p, repo, root, start)


def test_defect_boolean_native_inventory_accepted(tmp_path, monkeypatch):
    p, repo, root, start = fixture.candidate(tmp_path, monkeypatch)
    p['runs_and_slots']['allowed_cycles'] = [False]
    p['runs_and_slots']['slots'][0][2:] = [False, False]
    # True equals member 1; False equals hour 0 under Python equality.
    p['runs_and_slots']['slots'][25][2:] = [True, False]
    accepted(p, repo, root, start)
    assert hashlib.sha256(canonical(p['runs_and_slots']['slots'])).hexdigest() != p['schedule']['slot_inventory_sha256']


def test_defect_impossible_pacing_schedule_accepted(tmp_path, monkeypatch):
    p, repo, root, start = fixture.candidate(tmp_path, monkeypatch)
    p['limits']['min_start_interval_seconds'] = 10801
    p['limits']['request_deadline_seconds'] = 1
    assert (len(p['schedule']['requests']) - 1) * 10801 > p['limits']['max_elapsed_seconds']
    accepted(p, repo, root, start)


def test_defect_cross_provider_unbound_shared_overhead_accepted(tmp_path, monkeypatch):
    p, repo, root, start = fixture.candidate(tmp_path, monkeypatch)
    p['schedule']['attempt_slots'] += [775, 2050]
    for index in [775, 2050]:
        p['schedule']['requests'].append({'purpose': 'FIELD', 'slot_index': index,
            'reservation_bytes': 4194304, 'prerequisites': [0, 1, 2]})
        p['schedule']['reservation_total_bytes'] += 4194304
    # No provider/object/cache identity exists for any overhead request.
    accepted(p, repo, root, start)


def test_defect_unknown_overhead_prerequisite_accepted(tmp_path, monkeypatch):
    p, repo, root, start = fixture.candidate(tmp_path, monkeypatch)
    p['schedule']['requests'][0]['prerequisites'] = ['nonexistent', 999999, True]
    accepted(p, repo, root, start)


def test_defect_empty_paths_accepted(tmp_path, monkeypatch):
    p, repo, root, start = fixture.candidate(tmp_path, monkeypatch)
    for provider in p['sources']:
        p['sources'][provider]['path_template'] = ''
        p['network']['path_templates'][provider] = ''
    accepted(p, repo, root, start)


def test_defect_timezone_artifact_not_used(tmp_path, monkeypatch):
    p, repo, root, start = fixture.candidate(tmp_path, monkeypatch)
    tzref = p['cohort']['tzdata'].copy()
    assert (root / 'objects' / tzref['sha256']).read_bytes() == b'synthetic artifact only'
    p['cohort']['timezone'] = 'America/New_York'
    p['time']['local_day_start_utc'] += 5 * 3600
    p['time']['local_day_end_utc'] += 5 * 3600
    accepted(p, repo, root, start)
    assert p['cohort']['tzdata'] == tzref


def test_defect_fsync_failure_allows_undercharged_next_request(tmp_path, monkeypatch):
    root = tmp_path / 'journal'
    root.mkdir(mode=0o700)
    with launch.DurableBudget(root, 'a' * 64, max_bytes=1) as b:
        b.reserve('one', 1, started_monotonic=0)
        real_fsync = os.fsync
        def failed_fsync(fd):
            raise OSError('synthetic disk I/O failure after body receipt')
        with monkeypatch.context() as m:
            m.setattr(launch.os, 'fsync', failed_fsync)
            with pytest.raises(OSError):
                b.consume('one', b'x')
        assert b.received == 0  # one actual byte was delivered
        b.complete('one')
        b.reserve('two', 1, started_monotonic=2)
        b.consume('two', b'y')
        b.complete('two')
        assert b.received == 1  # two actual bytes with a one-byte budget
    with pytest.raises(launch.LaunchContractError, match='JOURNAL_SEQUENCE'):
        launch.DurableBudget(root, 'a' * 64, max_bytes=1)


def test_defect_short_write_allows_reservation_after_torn_journal(tmp_path, monkeypatch):
    root = tmp_path / 'journal'
    root.mkdir(mode=0o700)
    with launch.DurableBudget(root, 'b' * 64, max_bytes=2) as b:
        real_write = os.write
        with monkeypatch.context() as m:
            m.setattr(launch.os, 'write', lambda fd, data: real_write(fd, data[:10]))
            with pytest.raises(launch.LaunchContractError, match='JOURNAL_SHORT_WRITE'):
                b.reserve('one', 1, started_monotonic=0)
        b.reserve('two', 1, started_monotonic=2)
        assert b.in_flight == 'two'
    with pytest.raises(launch.LaunchContractError):
        launch.DurableBudget(root, 'b' * 64, max_bytes=2)


def test_defect_existing_hardlink_journal_modified(tmp_path):
    root = tmp_path / 'journal'
    root.mkdir(mode=0o700)
    external = tmp_path / 'unrelated_file'
    external.touch(mode=0o600)
    os.link(external, root / 'gate3.jsonl')
    with launch.DurableBudget(root, 'c' * 64):
        pass
    assert external.stat().st_size > 0
    assert external.stat().st_nlink == 2


def test_control_actual_crash_keeps_reservation_and_boot_change_rejected(tmp_path):
    root = tmp_path / 'journal'
    root.mkdir(mode=0o700)
    code = "from tools.v11_r09_gate3_launch import DurableBudget; import os; b=DurableBudget(%r, 'd'*64, max_bytes=2, boot_id='boot-one'); b.reserve('one',2,started_monotonic=0); b.consume('one',b'x'); os._exit(17)" % str(root)
    proc = subprocess.run([sys.executable, '-c', code], cwd=CANDIDATE)
    assert proc.returncode == 17
    with launch.DurableBudget(root, 'd' * 64, max_bytes=2, boot_id='boot-one') as b:
        assert b.received == 1 and b.reserved == 2
        with pytest.raises(launch.LaunchContractError, match='UNCERTAIN_REQUEST_HELD'):
            b.reserve('two', 1, started_monotonic=2)
    with pytest.raises(launch.LaunchContractError, match='JOURNAL_IDENTITY_MISMATCH'):
        launch.DurableBudget(root, 'd' * 64, max_bytes=2, boot_id='boot-two')


def test_control_actual_sha256_git_code_resolution(tmp_path):
    repo = tmp_path / 'sha256_repo'
    repo.mkdir()
    fixture._git(repo, 'init', '-q', '--object-format=sha256')
    fixture._git(repo, 'config', 'user.email', 'synthetic@example.invalid')
    fixture._git(repo, 'config', 'user.name', 'Independent Offline Review')
    (repo / 'source.py').write_bytes(b'# offline\n')
    fixture._git(repo, 'add', 'source.py')
    fixture._git(repo, 'commit', '-qm', 'synthetic')
    item = {'commit_oid': fixture._git(repo, 'rev-parse', 'HEAD'),
            'tree_oid': fixture._git(repo, 'rev-parse', 'HEAD^{tree}'),
            'path': 'source.py', 'sha256': hashlib.sha256(b'# offline\n').hexdigest()}
    assert len(item['commit_oid']) == 64
    launch._validate_code({'object_format': 'sha256', 'components': {k: item for k in
        ['collector', 'launch_validator', 'transport', 'decoder', 'clock_recorder']},
        'dependency_lock': {'sha256': 'e'*64, 'byte_length': 1, 'media_type': 'application/octet-stream'}}, repo)
