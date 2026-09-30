"""Synthetic, offline counterexamples for the Gate 3 launch boundary."""
import copy
import hashlib
import os
import subprocess
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

from tools.v11_multimodel_panel import canonical
from tools import v11_r09_gate3_launch as launch
from tools.v11_r09_gate3_launch import (DurableBudget, LaunchContractError,
    SLOT_COUNT, _slot_inventory, validate_manifest)


def _git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args]).decode().strip()


def candidate(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    repo.mkdir()
    _git(repo, 'init', '-q')
    _git(repo, 'config', 'user.email', 'synthetic@example.invalid')
    _git(repo, 'config', 'user.name', 'Synthetic Test')
    paths = ['collector.py', 'validator.py', 'transport.py', 'decoder.py', 'clock.py']
    for name in paths:
        (repo / name).write_text('# synthetic offline fixture\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'synthetic offline fixture')
    commit = _git(repo, 'rev-parse', 'HEAD')
    tree = _git(repo, 'rev-parse', 'HEAD^{tree}')
    sha = hashlib.sha256((repo / paths[0]).read_bytes()).hexdigest()
    for constant, value in (('PINNED_ORIGINAL_COMMIT', commit),
                            ('PINNED_ORIGINAL_TREE', tree),
                            ('PINNED_ADDENDUM_COMMIT', commit),
                            ('PINNED_ADDENDUM_TREE', tree)):
        monkeypatch.setattr(launch, constant, value)
    components = {role: {'commit_oid': commit, 'tree_oid': tree, 'path': path,
                         'sha256': sha} for role, path in zip(
        ('collector', 'launch_validator', 'transport', 'decoder', 'clock_recorder'), paths)}
    root = tmp_path / 'private'
    root.mkdir(mode=0o700)
    objects = root / 'objects'
    objects.mkdir(mode=0o700)
    contents = b'synthetic artifact only'
    artifact_sha = hashlib.sha256(contents).hexdigest()
    (objects / artifact_sha).write_bytes(contents)
    (objects / artifact_sha).chmod(0o600)
    artifact = {'sha256': artifact_sha, 'byte_length': len(contents),
                'media_type': 'application/octet-stream'}
    def review_artifact(label):
        content = ('synthetic ' + label).encode()
        digest = hashlib.sha256(content).hexdigest()
        (objects / digest).write_bytes(content)
        (objects / digest).chmod(0o600)
        return {'sha256': digest, 'byte_length': len(content),
                'media_type': 'application/octet-stream'}
    monkeypatch.setattr(launch, 'PINNED_ORIGINAL_DOC', artifact_sha)
    monkeypatch.setattr(launch, 'PINNED_ADDENDUM_DOC', artifact_sha)
    start = int(datetime(2027, 2, 28, 14, tzinfo=timezone.utc).timestamp())
    runs = {p: start - 14 * 3600 for p in ('GEFS', 'IFS', 'AIFS')}
    slots = _slot_inventory(runs)
    sources = {p: {'dossier': artifact, 'release_document': artifact,
                   'licence': artifact, 'index_evidence': artifact,
                   'range_evidence': artifact, 'decoder_build': artifact,
                   'origin': f'https://{p.lower()}.example.invalid',
                   'path_template': '/fixed/{run}/{member}/{hour}',
                   'member_range': [0, 30 if p == 'GEFS' else 50],
                   'native_hours': list(range(0, 73, 6 if p == 'AIFS' else 3)),
                   'identity_pins': artifact,
                   'effective_run_start_utc': runs[p] - 3600,
                   'effective_run_end_utc': runs[p] + 3600,
                   'publication_attestation': None,
                   'publication_absence_reason': 'not published in synthetic fixture'}
               for p in runs}
    st = root.stat()
    payload = {
        'identity': {'schema': 'R09_GATE3_LAUNCH_MANIFEST_V2',
                     'pilot_id': 'synthetic_20270301', 'purpose': 'NONFINANCIAL_RESEARCH',
                     'capture_mode': 'BOUNDED_FEASIBILITY', 'created_ref': artifact,
                     'financial_authority': False, 'promotion_authority': False,
                     'host_approved': False, 'launch_authority': False},
        'code': {'object_format': 'sha1', 'components': components,
                 'dependency_lock': artifact},
        'protocol': {'original_commit_oid': commit, 'original_tree_oid': tree,
                     'original_document': artifact, 'addendum_commit_oid': commit,
                     'addendum_tree_oid': tree, 'addendum_document': artifact,
                     'reviews': [{'name': name,
                                  'report': review_artifact(name + ':report'),
                                  'terminal': review_artifact(name + ':terminal')}
                                 for name in sorted(launch.REQUIRED_REVIEW_NAMES)]},
        'storage': {'root': str(root), 'owner_uid': os.getuid(), 'mode': 0o700,
                    'directory_dev': st.st_dev, 'directory_inode': st.st_ino,
                    'layout': 'OBJECTS_AND_APPEND_ONLY_LEDGER', 'exclusive_lock': True,
                    'atomic_fsync_seal': True, 'report_reserve_bytes': 4096,
                    'no_reclamation': True},
        'cohort': {'station_id': 'SYNTHETIC_STATION', 'station_version': 'v1',
                   'latitude': 0.0, 'longitude': 0.0, 'timezone': 'UTC',
                   'tzdata': artifact, 'target_date': '2027-03-01',
                   'events': ['HIGH', 'LOW'], 'units': 'CELSIUS',
                   'rounding': 'SYNTHETIC_HALF_UP', 'buckets': artifact,
                   'rule': artifact, 'settlement': artifact,
                   'metadata': artifact, 'selection': artifact,
                   'requested_keys': [
                       ['SYNTHETIC_STATION', 'HIGH_EVENT', 'RULE', '2027-03-01',
                        'daily_high_temperature'],
                       ['SYNTHETIC_STATION', 'LOW_EVENT', 'RULE', '2027-03-01',
                        'daily_low_temperature']],
                   'gate2_trial_keys': [
                       ['SYNTHETIC_STATION', 'HIGH_EVENT', '2027-03-01'],
                       ['SYNTHETIC_STATION', 'LOW_EVENT', '2027-03-01']],
                   'city_day': ['SYNTHETIC_STATION', '2027-03-01']},
        'time': {'preregistered_utc': start - 7200, 'review_completed_utc': start - 3600,
                 'window_start_utc': start, 'last_acquisition_utc': start + 10800,
                 'decision_utc': start + 14400, 'feature_seal_upper_utc': start + 12600,
                 'decision_lower_utc': start + 14399, 'expires_utc': start + 18000,
                 'local_day_start_utc': start + 36000,
                 'local_day_end_utc': start + 36000 + 86400,
                 'uncertainty_seconds': 1},
        'sources': sources,
        'runs_and_slots': {'allowed_cycles': [0], 'max_run_age_seconds': 86400,
                           'run_selection': 'LATEST_COMPLETE_READY', 'fallback_mode': 'NONE',
                           'run_utc': runs, 'slots': slots},
        'network': {'origins': [sources[p]['origin'] for p in runs],
                    'methods': ['GET'],
                    'path_templates': {p: sources[p]['path_template'] for p in runs},
                    'purposes': ['FIELD', 'INDEX', 'OBJECT_ID', 'METADATA', 'PROBE'],
                    'index_binding': 'ETAG_IF_RANGE',
                    'anonymous': True, 'redirects': False, 'cookies': False,
                    'netrc': False, 'ambient_proxies': False, 'signed_urls': False,
                    'retries': False, 'dns_tls_policy': artifact,
                    'restriction_lineage': artifact, 'post_window_labels': False},
        'limits': {'max_requests': 3600, 'max_received_bytes': 1024 ** 3,
                   'max_elapsed_seconds': 10800, 'single_in_flight': True,
                   'min_start_interval_seconds': 2, 'request_deadline_seconds': 30,
                   'max_index_bytes': 3145728,
                   'field_bytes': {'GEFS': 2097152, 'IFS': 4194304, 'AIFS': 4194304},
                   'min_free_disk_bytes': 2 * 1024 ** 3,
                   'min_available_memory_bytes': 512 * 1024 ** 2,
                   'headers': 1024, 'metadata': 4096, 'decoded': 1024 ** 2,
                   'report_storage': 4096},
        'schedule': {'slot_inventory_sha256': hashlib.sha256(canonical(slots)).hexdigest(),
                     'attempt_slots': [0],
                     'requests': [
                         {'purpose': 'INDEX', 'slot_index': None,
                          'reservation_bytes': 3145728, 'prerequisites': []},
                         {'purpose': 'OBJECT_ID', 'slot_index': None,
                          'reservation_bytes': 4096, 'prerequisites': []},
                         {'purpose': 'METADATA', 'slot_index': None,
                          'reservation_bytes': 4096, 'prerequisites': []},
                         {'purpose': 'FIELD', 'slot_index': 0,
                          'reservation_bytes': 2097152, 'prerequisites': [0, 1, 2]}],
                     'observed_sizes': artifact, 'estimated_full_raw_bytes': 1469234173,
                     'reservation_total_bytes': 3145728 + 4096 + 4096 + 2097152,
                     'full_denominator': SLOT_COUNT,
                     'capture_mode': 'BOUNDED_FEASIBILITY'},
        'clocks_and_receipts': {'method': artifact, 'host_boot': artifact,
                                'sync_evidence': artifact, 'max_measurement_age_seconds': 60,
                                'request_start': artifact, 'body_receipt': artifact,
                                'decode_complete': artifact, 'durable_seal': artifact},
        'accounting': {'terminal_precedence': artifact, 'all_reasons': True,
                       'journal_hash_chain': True, 'no_silent_retry': True,
                       'expired_local_only': True, 'raw_partition': True,
                       'provider_eligibility': True, 'all_provider_intersection': True,
                       'diagnostics_no_credit': True}}
    return payload, repo, root, start


def validate(payload, repo, root, start):
    return validate_manifest(canonical(payload), repo=repo, object_root=root,
                             now_utc=start - 4000)


def test_exact_synthetic_candidate_and_real_git_sha1(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    assert len(payload['code']['components']['collector']['commit_oid']) == 40
    assert len(payload['runs_and_slots']['slots']) == 2713
    assert len(validate(payload, repo, root, start)) == 64


def test_reviewed_protocol_pins_match_repository():
    repo = Path(__file__).resolve().parents[1]
    assert _git(repo, 'rev-parse', '117830a^{commit}') == launch.PINNED_ORIGINAL_COMMIT
    assert _git(repo, 'rev-parse', '117830a^{tree}') == launch.PINNED_ORIGINAL_TREE
    assert _git(repo, 'rev-parse', '14c2413^{commit}') == launch.PINNED_ADDENDUM_COMMIT
    assert _git(repo, 'rev-parse', '14c2413^{tree}') == launch.PINNED_ADDENDUM_TREE
    assert hashlib.sha256((repo / 'docs/V11_R09_GATE3_COLLECTION_PROTOCOL.md').read_bytes()).hexdigest() == launch.PINNED_ORIGINAL_DOC
    assert hashlib.sha256((repo / 'docs/V11_R09_GATE3_LAUNCH_CONTRACT_ADJUDICATION.md').read_bytes()).hexdigest() == launch.PINNED_ADDENDUM_DOC


def test_shortened_denominator_rejected(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['runs_and_slots']['slots'] = payload['runs_and_slots']['slots'][:3]
    with pytest.raises(LaunchContractError, match='IMMUTABLE_2713_SLOT_DENOMINATOR'):
        validate(payload, repo, root, start)


def test_unsealed_placeholder_and_wrong_window_rejected(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['identity']['pilot_id'] = ''
    with pytest.raises(LaunchContractError, match='PILOT_ID'):
        validate(payload, repo, root, start)
    payload['identity']['pilot_id'] = 'synthetic_20270301'
    payload['time']['window_start_utc'] += 1
    with pytest.raises(LaunchContractError, match='PILOT_WINDOW_FIXED'):
        validate(payload, repo, root, start)


def test_duplicate_unknown_and_noncanonical_json_rejected(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    with pytest.raises(LaunchContractError, match='DUPLICATE_JSON_KEY'):
        validate_manifest(b'{"a":1,"a":2}', repo=repo, object_root=root, now_utc=start)
    payload['unreviewed'] = True
    with pytest.raises(LaunchContractError, match='MANIFEST_GROUP_SCHEMA'):
        validate(payload, repo, root, start)


def test_git_oid_is_not_artifact_sha_and_dirty_source_rejected(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['code']['components']['collector']['commit_oid'] = 'a' * 64
    with pytest.raises(LaunchContractError, match='GIT_COMMIT_OID'):
        validate(payload, repo, root, start)
    payload['code']['components']['collector']['commit_oid'] = _git(repo, 'rev-parse', 'HEAD')
    (repo / 'collector.py').write_text('# edited after commit\n')
    with pytest.raises(LaunchContractError, match='DIRTY_EXECUTABLE_CODE'):
        validate(payload, repo, root, start)


def test_object_symlink_and_raw_only_full_estimate_are_not_launch_schedule(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['schedule']['requests'][-1]['reservation_bytes'] = 1
    payload['schedule']['reservation_total_bytes'] -= 2097151
    with pytest.raises(LaunchContractError, match='RESERVATION_TOO_SMALL'):
        validate(payload, repo, root, start)
    payload['schedule']['requests'][-1]['reservation_bytes'] = 2097152
    payload['schedule']['reservation_total_bytes'] += 2097151
    artifact_sha = payload['identity']['created_ref']['sha256']
    object_path = root / 'objects' / artifact_sha
    data = object_path.read_bytes()
    object_path.unlink()
    outside = tmp_path / 'outside'
    outside.write_bytes(data)
    object_path.symlink_to(outside)
    with pytest.raises(OSError):
        validate(payload, repo, root, start)


def test_budget_reservation_survives_crash_and_blocks_exhausted_next_request(tmp_path):
    root = tmp_path / 'journal'
    root.mkdir(mode=0o700)
    h = 'a' * 64
    with DurableBudget(root, h, max_bytes=1) as budget:
        budget.reserve('first', 1, started_monotonic=0)
        assert budget.next_read_limit(10) == 1
        budget.consume('first', b'x')
        budget.complete('first')
    with DurableBudget(root, h, max_bytes=1) as budget:
        assert budget.received == 1
        with pytest.raises(LaunchContractError, match='NOT_ATTEMPTED_BUDGET'):
            budget.reserve('second', 1, started_monotonic=2)


def test_partial_receipt_charged_and_uncertain_reservation_held(tmp_path):
    root = tmp_path / 'journal'
    root.mkdir(mode=0o700)
    h = 'b' * 64
    with DurableBudget(root, h, max_bytes=5) as budget:
        budget.reserve('first', 4, started_monotonic=0)
        budget.consume('first', b'xx')
    with DurableBudget(root, h, max_bytes=5) as budget:
        assert budget.received == 2 and budget.reserved == 4
        with pytest.raises(LaunchContractError, match='UNCERTAIN_REQUEST_HELD'):
            budget.reserve('second', 1, started_monotonic=2)


def test_stream_stops_at_allowance_and_does_not_ignore_partial_bytes(tmp_path):
    root = tmp_path / 'journal'
    root.mkdir(mode=0o700)
    with DurableBudget(root, 'c' * 64, max_bytes=2) as budget:
        budget.reserve('first', 2, started_monotonic=0)
        budget.consume('first', b'x')
        assert budget.next_read_limit(10) == 1
        with pytest.raises(LaunchContractError, match='STREAM_ABORT_AT_ALLOWANCE'):
            budget.consume('first', b'xx')
        assert budget.received == 3
        with pytest.raises(LaunchContractError, match='NO_ACTIVE_REQUEST'):
            budget.next_read_limit(10)
    with DurableBudget(root, 'c' * 64, max_bytes=2) as budget:
        assert budget.received == 3
        with pytest.raises(LaunchContractError, match='STREAM_VIOLATION_HELD'):
            budget.reserve('second', 1, started_monotonic=2)


def test_tampered_journal_fails_closed(tmp_path):
    root = tmp_path / 'journal'
    root.mkdir(mode=0o700)
    with DurableBudget(root, 'd' * 64, max_bytes=2) as budget:
        budget.reserve('first', 2, started_monotonic=0)
    file = root / 'gate3.jsonl'
    data = file.read_bytes()
    file.write_bytes(data.replace(b'first', b'other'))
    with pytest.raises(LaunchContractError, match='JOURNAL_HASH'):
        DurableBudget(root, 'd' * 64, max_bytes=2)


def test_journal_rejects_concurrent_writer(tmp_path):
    root = tmp_path / 'journal'
    root.mkdir(mode=0o700)
    with DurableBudget(root, 'f' * 64, max_bytes=2):
        with pytest.raises(LaunchContractError, match='JOURNAL_CONCURRENT_WRITER'):
            DurableBudget(root, 'f' * 64, max_bytes=2)


def test_pacing_elapsed_and_request_count_persist_across_restart(tmp_path):
    root = tmp_path / 'journal'
    root.mkdir(mode=0o700)
    h = 'e' * 64
    with DurableBudget(root, h, max_requests=2, max_bytes=4,
                       max_elapsed_seconds=3) as budget:
        budget.reserve('one', 1, started_monotonic=100)
        budget.consume('one', b'x')
        budget.complete('one')
    with DurableBudget(root, h, max_requests=2, max_bytes=4,
                       max_elapsed_seconds=3) as budget:
        with pytest.raises(LaunchContractError, match='NOT_ATTEMPTED_BUDGET'):
            budget.reserve('two', 1, started_monotonic=101)
        budget.reserve('two', 1, started_monotonic=102)
        budget.complete('two')
        with pytest.raises(LaunchContractError, match='NOT_ATTEMPTED_BUDGET'):
            budget.reserve('three', 1, started_monotonic=104)
