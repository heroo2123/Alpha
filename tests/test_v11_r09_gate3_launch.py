"""Synthetic, offline counterexamples for the Gate 3 launch boundary."""
import copy
import hashlib
import json
import os
import select
import signal
import subprocess
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import TZPATH

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
    tzbytes = next((Path(base) / 'UTC').read_bytes() for base in TZPATH
                   if (Path(base) / 'UTC').is_file())
    tzsha = hashlib.sha256(tzbytes).hexdigest()
    (objects / tzsha).write_bytes(tzbytes)
    (objects / tzsha).chmod(0o600)
    tzartifact = {'sha256': tzsha, 'byte_length': len(tzbytes),
                  'media_type': 'application/tzif'}
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
        'identity': {'schema': 'R09_GATE3_LAUNCH_MANIFEST_V3',
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
                   'tzdata': tzartifact, 'target_date': '2027-03-01',
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
                           'run_utc': runs, 'candidates': [
                               {'provider': p, 'run_utc': cycle,
                                'status': 'READY' if cycle == runs[p] else 'UNAVAILABLE',
                                'ready_upper_utc': cycle + 3600}
                               for p in runs for cycle in (runs[p],)],
                           'slots': slots},
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
                     'requests': [],
                     'observed_sizes': artifact, 'estimated_full_raw_bytes': 1469234173,
                     'reservation_total_bytes': 3145728 + 4096 + 4096 + 2097152,
                     'full_denominator': SLOT_COUNT,
                     'capture_mode': 'BOUNDED_FEASIBILITY',
                     'processing_seconds': 60, 'finalization_seconds': 60},
        'clocks_and_receipts': {'method': artifact, 'host_boot': artifact,
                                'sync_evidence': artifact, 'max_measurement_age_seconds': 60,
                                'request_start': artifact, 'body_receipt': artifact,
                                'decode_complete': artifact, 'durable_seal': artifact},
        'accounting': {'terminal_precedence': artifact, 'all_reasons': True,
                       'journal_hash_chain': True, 'no_silent_retry': True,
                       'expired_local_only': True, 'raw_partition': True,
                       'provider_eligibility': True, 'all_provider_intersection': True,
                       'diagnostics_no_credit': True}}
    payload['schedule']['requests'] = [
        request_for_slot(payload, 0, purpose, position,
                         3145728 if purpose == 'INDEX' else
                         2097152 if purpose == 'FIELD' else 4096,
                         [0, 1, 2] if purpose == 'FIELD' else [])
        for position, purpose in enumerate(('INDEX', 'OBJECT_ID', 'METADATA', 'FIELD'))]
    return payload, repo, root, start


def request_for_slot(payload, slot_index, purpose, position, reservation, prerequisites):
    provider, run, member, hour = payload['runs_and_slots']['slots'][slot_index]
    source = payload['sources'][provider]
    path = source['path_template'].format(run=run, member=member, hour=hour)
    object_id = hashlib.sha256(canonical([provider, source['origin'], path])).hexdigest()
    index_id = hashlib.sha256(canonical([object_id, 'INDEX'])).hexdigest()
    cache_id = hashlib.sha256(canonical([object_id, index_id])).hexdigest()
    return {'request_id': f'request_{position}', 'purpose': purpose,
            'slot_index': slot_index, 'provider': provider, 'origin': source['origin'],
            'path': path, 'object_id': object_id, 'index_id': index_id,
            'cache_id': cache_id, 'range_start': 0 if purpose == 'FIELD' else None,
            'range_end': reservation - 1 if purpose == 'FIELD' else None,
            'reservation_bytes': reservation, 'prerequisites': prerequisites}


def validate(payload, repo, root, start):
    return validate_manifest(canonical(payload), repo=repo, object_root=root,
                             now_utc=start - 4000)


def _bounded_fork_result(action, timeout=3):
    """Run a synthetic fork probe and always reap only its owned child."""
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            try:
                result = action()
            except BaseException as exc:
                result = {'error': type(exc).__name__, 'reason': str(exc)}
            os.write(write_fd, canonical(result) + b'\n')
        finally:
            os._exit(0)
    os.close(write_fd)
    try:
        assert select.select([read_fd], [], [], timeout)[0], 'owned child blocked'
        return json.loads(os.read(read_fd, 65536))
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        os.waitpid(pid, 0)
        os.close(read_fd)


@pytest.mark.parametrize('reference', ['identity', 'tzdata'])
@pytest.mark.parametrize('replacement', ['fifo', 'directory', 'symlink'])
def test_manifest_rejects_nonregular_artifact_without_blocking(
        tmp_path, monkeypatch, reference, replacement):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    assert len(validate(payload, repo, root, start)) == 64
    artifact = (payload['identity']['created_ref'] if reference == 'identity'
                else payload['cohort']['tzdata'])
    path = root / 'objects' / artifact['sha256']
    original = path.read_bytes()
    path.unlink()
    if replacement == 'fifo':
        os.mkfifo(path, 0o600)
    elif replacement == 'directory':
        path.mkdir(mode=0o700)
    else:
        path.symlink_to(root / 'objects')
    result = _bounded_fork_result(lambda: validate(payload, repo, root, start))
    assert result['error'] == 'LaunchContractError', result
    if replacement == 'directory':
        path.rmdir()
    else:
        path.unlink()
    path.write_bytes(original)
    path.chmod(0o600)
    assert len(validate(payload, repo, root, start)) == 64


def test_inherited_budget_cannot_mutate_composed_store_journal(tmp_path):
    from tools.v11_r09_gate3_offline_io import VersionedImmutableObjectStore

    journal = tmp_path / 'budget'
    journal.mkdir(mode=0o700)
    root = tmp_path / 'store'
    root.mkdir(mode=0o700)
    (root / 'objects').mkdir(mode=0o700)
    digest = 'a' * 64
    with DurableBudget(journal, digest, max_bytes=1,
                       boot_id='synthetic-boot') as budget:
        with VersionedImmutableObjectStore(
                root, manifest_sha256=digest, policy_sha256='b' * 64,
                build_id='synthetic', clock_method='synthetic',
                max_clock_age_seconds=10, host_id='synthetic-host',
                boot_id='synthetic-boot') as store:
            before = (journal / 'gate3.jsonl').read_bytes()

            def inherited():
                try:
                    try:
                        store.read_receipt(None)
                    except LaunchContractError as exc:
                        store_reason = str(exc)
                    else:
                        store_reason = 'accepted'
                    for action in (
                            lambda: budget.reserve('child', 1, started_monotonic=1),
                            lambda: budget.next_read_limit(1),
                            lambda: budget.consume('child', b'X'),
                            lambda: budget.complete('child')):
                        with pytest.raises(LaunchContractError,
                                           match='JOURNAL_OWNER_PROCESS'):
                            action()
                    return {'store_reason': store_reason,
                            'received': budget.received}
                finally:
                    budget.close()
                    store.close()

            result = _bounded_fork_result(inherited)
            assert result == {'store_reason': 'STORE_OWNER_PROCESS',
                              'received': 0}
            assert (journal / 'gate3.jsonl').read_bytes() == before
            with pytest.raises(LaunchContractError,
                               match='JOURNAL_CONCURRENT_WRITER'):
                DurableBudget(journal, digest, max_bytes=1,
                              boot_id='synthetic-boot')
            budget.reserve('parent', 1, started_monotonic=3)
            budget.consume('parent', b'Y')
            budget.complete('parent')
            assert budget.received == 1
    with DurableBudget(journal, digest, max_bytes=1,
                       boot_id='synthetic-boot') as reopened:
        assert reopened.received == 1
        with pytest.raises(LaunchContractError, match='NOT_ATTEMPTED_BUDGET'):
            reopened.reserve('second', 1, started_monotonic=5)


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
    with pytest.raises(LaunchContractError, match='ARTIFACT_LENGTH'):
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


@pytest.mark.parametrize('offset', [1, 60, 3599])
def test_native_cycle_requires_exact_midnight(tmp_path, monkeypatch, offset):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    for provider in payload['runs_and_slots']['run_utc']:
        payload['runs_and_slots']['run_utc'][provider] += offset
    payload['runs_and_slots']['slots'] = _slot_inventory(payload['runs_and_slots']['run_utc'])
    payload['schedule']['slot_inventory_sha256'] = hashlib.sha256(
        canonical(payload['runs_and_slots']['slots'])).hexdigest()
    with pytest.raises(LaunchContractError, match='RUN_TIME'):
        validate(payload, repo, root, start)


@pytest.mark.parametrize('mutation,reason', [
    ('cycle_bool', 'RUN_POLICY'), ('cycle_float', 'RUN_POLICY'),
    ('member_bool', 'IMMUTABLE_2713_SLOT_DENOMINATOR'),
    ('hour_float', 'IMMUTABLE_2713_SLOT_DENOMINATOR'),
    ('slot_digest', 'SLOT_DIGEST_MISMATCH'),
])
def test_native_inventory_rejects_aliases_and_digest(tmp_path, monkeypatch, mutation, reason):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    if mutation == 'cycle_bool':
        payload['runs_and_slots']['allowed_cycles'] = [False]
    elif mutation == 'cycle_float':
        payload['runs_and_slots']['allowed_cycles'] = [0.0]
    elif mutation == 'member_bool':
        payload['runs_and_slots']['slots'][0][2] = False
    elif mutation == 'hour_float':
        payload['runs_and_slots']['slots'][0][3] = 0.0
    else:
        payload['schedule']['slot_inventory_sha256'] = 'f' * 64
    with pytest.raises(LaunchContractError, match=reason):
        validate(payload, repo, root, start)


def test_latest_ready_run_selected_at_conservative_bound(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    for item in payload['runs_and_slots']['candidates']:
        item['ready_upper_utc'] = payload['time']['decision_lower_utc'] + 1
    with pytest.raises(LaunchContractError, match='RUN_TIME'):
        validate(payload, repo, root, start)


def test_schedule_rejects_cross_provider_reuse_and_unknown_prerequisites(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['schedule']['attempt_slots'].append(775)
    payload['schedule']['requests'].append(request_for_slot(
        payload, 775, 'FIELD', 4, 4194304, [0, 1, 2]))
    payload['schedule']['reservation_total_bytes'] += 4194304
    with pytest.raises(LaunchContractError, match='FIELD_PREREQUISITES'):
        validate(payload, repo, root, start)
    (tmp_path / 'second').mkdir()
    payload, repo, root, start = candidate(tmp_path / 'second', monkeypatch)
    payload['schedule']['requests'][0]['prerequisites'] = [999999]
    with pytest.raises(LaunchContractError, match='SCHEDULE_PREREQUISITES'):
        validate(payload, repo, root, start)


def test_schedule_rejects_empty_paths_wrong_object_and_impossible_pacing(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['sources']['GEFS']['path_template'] = ''
    payload['network']['path_templates']['GEFS'] = ''
    with pytest.raises(LaunchContractError, match='SOURCE_ORIGIN'):
        validate(payload, repo, root, start)
    payload['sources']['GEFS']['path_template'] = '/fixed/{run}/{member}/{hour}'
    payload['network']['path_templates']['GEFS'] = payload['sources']['GEFS']['path_template']
    payload['schedule']['requests'][0]['object_id'] = '0' * 64
    with pytest.raises(LaunchContractError, match='SCHEDULE_OBJECT_BINDING'):
        validate(payload, repo, root, start)
    payload['schedule']['requests'][0]['object_id'] = payload['schedule']['requests'][1]['object_id']
    payload['limits']['min_start_interval_seconds'] = 10801
    payload['limits']['request_deadline_seconds'] = 1
    with pytest.raises(LaunchContractError, match='SCHEDULE_TIME_FEASIBILITY'):
        validate(payload, repo, root, start)


def test_field_range_and_reservation_cannot_exceed_provider_cap(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    field = payload['schedule']['requests'][-1]
    field['range_end'] += 1
    field['reservation_bytes'] += 1
    payload['schedule']['reservation_total_bytes'] += 1
    with pytest.raises(LaunchContractError, match='FIELD_PROVIDER_LIMIT'):
        validate(payload, repo, root, start)
    field['range_end'] -= 1
    with pytest.raises(LaunchContractError, match='RESERVATION_TOO_SMALL'):
        validate(payload, repo, root, start)


@pytest.mark.parametrize('template', [
    '//outside.example/{run}/{member}/{hour}',
    '/fixed/%2e%2e/{run}/{member}/{hour}',
    '/fixed/../{run}/{member}/{hour}',
    '/fixed//{run}/{member}/{hour}',
])
def test_schedule_rejects_origin_escape_paths(tmp_path, monkeypatch, template):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['sources']['GEFS']['path_template'] = template
    payload['network']['path_templates']['GEFS'] = template
    for position, request in enumerate(payload['schedule']['requests']):
        replacement = request_for_slot(payload, 0, request['purpose'], position,
                                       request['reservation_bytes'], request['prerequisites'])
        request.update(replacement)
    with pytest.raises(LaunchContractError, match='SOURCE_ORIGIN'):
        validate(payload, repo, root, start)


def test_pinned_timezone_bytes_govern_local_day(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['cohort']['timezone'] = 'America/New_York'
    payload['time']['local_day_start_utc'] += 5 * 3600
    payload['time']['local_day_end_utc'] += 5 * 3600
    with pytest.raises(LaunchContractError, match='PINNED_TZDATA_ZONE_MISMATCH'):
        validate(payload, repo, root, start)
    nybytes = next((Path(base) / 'America/New_York').read_bytes() for base in TZPATH
                   if (Path(base) / 'America/New_York').is_file())
    nysha = hashlib.sha256(nybytes).hexdigest()
    (root / 'objects' / nysha).write_bytes(nybytes)
    (root / 'objects' / nysha).chmod(0o600)
    payload['cohort']['tzdata'] = {'sha256': nysha, 'byte_length': len(nybytes),
                                   'media_type': 'application/tzif'}
    assert len(validate(payload, repo, root, start)) == 64
    payload['cohort']['timezone'] = 'UTC'
    tzref = payload['cohort']['tzdata']
    (root / 'objects' / tzref['sha256']).write_bytes(b'synthetic artifact only')
    with pytest.raises(LaunchContractError, match='ARTIFACT_LENGTH|ARTIFACT_DIGEST'):
        validate(payload, repo, root, start)


@pytest.mark.parametrize('zone,summer,winter', [
    ('America/New_York', -4, -5), ('Asia/Kathmandu', 5.75, 5.75),
])
def test_pinned_timezone_handles_dst_and_fractional_offsets(tmp_path, zone, summer, winter):
    root = tmp_path / 'private'
    objects = root / 'objects'
    objects.mkdir(parents=True, mode=0o700)
    data = next((Path(base) / zone).read_bytes() for base in TZPATH
                if (Path(base) / zone).is_file())
    sha = hashlib.sha256(data).hexdigest()
    (objects / sha).write_bytes(data)
    (objects / sha).chmod(0o600)
    tz = launch._zone_from_ref({'timezone': zone,
        'tzdata': {'sha256': sha, 'byte_length': len(data),
                   'media_type': 'application/tzif'}}, root)
    assert datetime(2027, 7, 1, tzinfo=tz).utcoffset().total_seconds() == summer * 3600
    assert datetime(2027, 1, 1, tzinfo=tz).utcoffset().total_seconds() == winter * 3600


@pytest.mark.parametrize('boundary', ['reserve', 'chunk', 'violation', 'complete'])
@pytest.mark.parametrize('fault', ['short_write', 'fsync'])
def test_journal_failure_poison_and_restart(tmp_path, monkeypatch, boundary, fault):
    root = tmp_path / 'journal'
    root.mkdir(mode=0o700)
    with DurableBudget(root, 'a' * 64, max_bytes=4) as budget:
        if boundary != 'reserve':
            budget.reserve('one', 2, started_monotonic=0)
        if boundary == 'complete':
            budget.consume('one', b'x')
        with monkeypatch.context() as m:
            if fault == 'short_write':
                real_write = os.write
                m.setattr(launch.os, 'write', lambda fd, data: real_write(fd, data[:10]))
            else:
                m.setattr(launch.os, 'fsync', lambda fd: (_ for _ in ()).throw(
                    OSError('synthetic fsync failure')))
            reason = ('JOURNAL_SHORT_WRITE_DURABILITY_UNCERTAIN'
                      if fault == 'short_write' else 'JOURNAL_DURABILITY_UNCERTAIN')
            with pytest.raises(LaunchContractError, match=reason):
                if boundary == 'reserve':
                    budget.reserve('one', 2, started_monotonic=0)
                elif boundary == 'chunk':
                    budget.consume('one', b'x')
                elif boundary == 'violation':
                    budget.consume('one', b'xxx')
                else:
                    budget.complete('one')
        assert budget.failed
        assert budget.uncertain_received_bytes == (1 if boundary == 'chunk' else
                                                   3 if boundary == 'violation' else 0)
        for action in (lambda: budget.reserve('two', 1, started_monotonic=2),
                       lambda: budget.next_read_limit(1),
                       lambda: budget.consume('one', b'x'),
                       lambda: budget.complete('one')):
            with pytest.raises(LaunchContractError, match='JOURNAL_DURABILITY_UNCERTAIN'):
                action()
    if fault == 'short_write':
        with pytest.raises(LaunchContractError):
            DurableBudget(root, 'a' * 64, max_bytes=4)
    else:
        with DurableBudget(root, 'a' * 64, max_bytes=4) as reopened:
            if boundary in ('chunk', 'violation'):
                assert reopened.received >= (1 if boundary == 'chunk' else 3)
            if boundary != 'complete':
                with pytest.raises(LaunchContractError,
                                   match='UNCERTAIN_REQUEST_HELD|STREAM_VIOLATION_HELD'):
                    reopened.reserve('two', 1, started_monotonic=2)


@pytest.mark.parametrize('name', ['gate3.lock', 'gate3.jsonl'])
def test_journal_rejects_hardlink_and_wrong_mode_without_modifying_target(tmp_path, name):
    root = tmp_path / 'journal'
    root.mkdir(mode=0o700)
    target = tmp_path / 'unrelated'
    target.write_bytes(b'unchanged')
    target.chmod(0o600)
    os.link(target, root / name)
    with pytest.raises(LaunchContractError, match='JOURNAL_FILE_IDENTITY'):
        DurableBudget(root, 'b' * 64)
    assert target.read_bytes() == b'unchanged'
    (root / name).unlink()
    (root / name).write_bytes(b'')
    (root / name).chmod(0o644)
    with pytest.raises(LaunchContractError, match='JOURNAL_FILE_IDENTITY'):
        DurableBudget(root, 'b' * 64)


def test_journal_rejects_ancestor_symlink_and_closes_failed_constructor(tmp_path):
    root = tmp_path / 'private'
    root.mkdir(mode=0o700)
    link = tmp_path / 'alias'
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(LaunchContractError, match='JOURNAL_PATH_SYMLINK'):
        DurableBudget(link, 'c' * 64)
    (root / 'gate3.jsonl').write_bytes(b'torn')
    (root / 'gate3.jsonl').chmod(0o600)
    before = len(os.listdir('/proc/self/fd'))
    with pytest.raises(LaunchContractError, match='JOURNAL_TORN_RECORD'):
        DurableBudget(root, 'c' * 64)
    assert len(os.listdir('/proc/self/fd')) == before


def test_journal_rejects_nonregular_file_and_constructor_fsync_cleanup(tmp_path, monkeypatch):
    root = tmp_path / 'journal'
    root.mkdir(mode=0o700)
    os.mkfifo(root / 'gate3.lock', 0o600)
    with pytest.raises(LaunchContractError, match='JOURNAL_FILE_IDENTITY'):
        DurableBudget(root, 'd' * 64)
    (root / 'gate3.lock').unlink()
    before = len(os.listdir('/proc/self/fd'))
    with monkeypatch.context() as m:
        m.setattr(launch.os, 'fsync', lambda fd: (_ for _ in ()).throw(
            OSError('synthetic directory fsync failure')))
        with pytest.raises(OSError, match='synthetic directory fsync failure'):
            DurableBudget(root, 'd' * 64)
    assert len(os.listdir('/proc/self/fd')) == before


def test_journal_poisoned_if_directory_loses_private_mode(tmp_path):
    root = tmp_path / 'journal'
    root.mkdir(mode=0o700)
    with DurableBudget(root, 'e' * 64) as budget:
        root.chmod(0o755)
        with pytest.raises(LaunchContractError, match='JOURNAL_DIRECTORY_IDENTITY'):
            budget.reserve('one', 1, started_monotonic=0)
        root.chmod(0o700)
        with pytest.raises(LaunchContractError, match='JOURNAL_DURABILITY_UNCERTAIN'):
            budget.reserve('one', 1, started_monotonic=0)


def test_delivered_bytes_remain_known_when_privacy_fails_mid_request(tmp_path):
    root = tmp_path / 'journal'
    root.mkdir(mode=0o700)
    with DurableBudget(root, 'f' * 64, max_bytes=2) as budget:
        budget.reserve('one', 2, started_monotonic=0)
        root.chmod(0o755)
        with pytest.raises(LaunchContractError, match='JOURNAL_DIRECTORY_IDENTITY'):
            budget.consume('one', b'x')
        assert budget.failed
        assert budget.received == 1 and budget.uncertain_received_bytes == 1
        assert budget.reserved == 2 and budget.in_flight == 'one'
        root.chmod(0o700)
        with pytest.raises(LaunchContractError, match='JOURNAL_DURABILITY_UNCERTAIN'):
            budget.complete('one')
        with pytest.raises(LaunchContractError, match='JOURNAL_DURABILITY_UNCERTAIN'):
            budget.reserve('two', 1, started_monotonic=2)


def test_budget_rejects_oversized_record_before_write(tmp_path):
    """A single journal record cannot exceed the fixed per-record cap."""
    root = tmp_path / 'journal'
    root.mkdir(mode=0o700)
    with DurableBudget(root, 'a' * 64, max_bytes=1) as budget:
        huge_key = 'k' * (launch.JOURNAL_RECORD_MAX_BYTES + 1)
        with pytest.raises(LaunchContractError, match='JOURNAL_RECORD_TOO_LARGE'):
            budget.reserve(huge_key, 1, started_monotonic=0)
        # The rejected oversized record was never written; the journal is
        # still usable with an ordinary-sized key afterward.
        budget.reserve('ordinary', 1, started_monotonic=0)


def test_budget_rejects_total_journal_bytes_past_fixed_cap(tmp_path, monkeypatch):
    """The fixed total-journal-bytes cap is independent of max_bytes/max_requests."""
    root = tmp_path / 'journal'
    root.mkdir(mode=0o700)
    with DurableBudget(root, 'b' * 64, max_bytes=1024 ** 2) as budget:
        budget.reserve('one', 1, started_monotonic=0)
        budget.consume('one', b'x')
        budget.complete('one')
        # Freeze the cap at exactly the current on-disk size: there is no
        # room left for even one more record, regardless of byte/request
        # budget headroom.
        monkeypatch.setattr(launch, 'JOURNAL_MAX_BYTES', budget._journal_bytes)
        with pytest.raises(LaunchContractError, match='JOURNAL_CAPACITY_EXCEEDED'):
            budget.reserve('two', 1, started_monotonic=2)
    # Replaying the already-compliant history (exactly at, not over, the cap)
    # still succeeds; the cap blocks new growth, not reading past data.
    with DurableBudget(root, 'b' * 64, max_bytes=1024 ** 2) as restarted:
        assert restarted.in_flight is None and restarted.received == 1
        with pytest.raises(LaunchContractError, match='JOURNAL_CAPACITY_EXCEEDED'):
            restarted.reserve('two', 1, started_monotonic=2)


def test_budget_rejects_event_count_past_fixed_cap(tmp_path, monkeypatch):
    """Tiny chunks must consume event/record capacity, not just byte budget.

    Exhausting the fixed event cap is treated like any other journal-append
    failure: the delivered bytes become uncertain-but-received (never
    silently dropped), and the in-flight reservation is stuck held, exactly
    like other incomplete-reservation holds. It is not a durability fault
    (self.failed stays False) because the cap is fixed and deterministic:
    the same journal replays to the same stuck point on every restart.
    """
    root = tmp_path / 'journal'
    root.mkdir(mode=0o700)
    monkeypatch.setattr(launch, 'JOURNAL_MAX_EVENTS', 3)
    with DurableBudget(root, 'c' * 64, max_bytes=1024 ** 2) as budget:
        # events[0] is 'init'; this 'reserve' is events[1].
        budget.reserve('first', 100, started_monotonic=0)
        budget.consume('first', b'x')  # events[2]; plenty of byte allowance remains
        with pytest.raises(LaunchContractError, match='JOURNAL_EVENT_CAPACITY'):
            budget.consume('first', b'x')
        assert budget.received == 2 and budget.uncertain_received_bytes == 1
        assert not budget.failed, 'capacity exhaustion is a clean refusal, not a durability fault'
        assert budget.in_flight == 'first'
        with pytest.raises(LaunchContractError, match='JOURNAL_EVENT_CAPACITY'):
            budget.complete('first')
    with DurableBudget(root, 'c' * 64, max_bytes=1024 ** 2) as budget:
        assert budget.in_flight == 'first' and budget.received == 1
        with pytest.raises(LaunchContractError, match='UNCERTAIN_REQUEST_HELD'):
            budget.reserve('second', 1, started_monotonic=4)


def test_replay_rejects_oversized_file_and_unterminated_record_without_full_read(
        tmp_path, monkeypatch):
    """Bounded replay must fail from a sparse file's size alone, never reading it."""
    root = tmp_path / 'journal'
    root.mkdir(mode=0o700)
    with DurableBudget(root, 'd' * 64, max_bytes=1) as budget:
        budget.reserve('one', 1, started_monotonic=0)
    monkeypatch.setattr(launch, 'JOURNAL_MAX_BYTES', 10)
    with pytest.raises(LaunchContractError, match='JOURNAL_CAPACITY_EXCEEDED'):
        DurableBudget(root, 'd' * 64, max_bytes=1)
    monkeypatch.undo()
    # A gigantic sparse (mostly unwritten) file must still be refused purely
    # from its reported size, never by attempting to read it into memory.
    huge = root / 'gate3.jsonl'
    huge_size = launch.JOURNAL_MAX_BYTES + 1
    with open(huge, 'r+b') as fh:
        fh.truncate(huge_size)
    assert huge.stat().st_size == huge_size
    with pytest.raises(LaunchContractError, match='JOURNAL_CAPACITY_EXCEEDED'):
        DurableBudget(root, 'd' * 64, max_bytes=1)


def test_replay_rejects_unterminated_record_past_record_cap(tmp_path):
    """A torn/forged record with no newline cannot grow past the record cap."""
    root = tmp_path / 'journal'
    root.mkdir(mode=0o700)
    with DurableBudget(root, 'e' * 64, max_bytes=1) as budget:
        budget.reserve('one', 1, started_monotonic=0)
    journal = root / 'gate3.jsonl'
    with open(journal, 'ab') as fh:
        fh.write(b'{' + b'x' * (launch.JOURNAL_RECORD_MAX_BYTES + 1))
    with pytest.raises(LaunchContractError, match='JOURNAL_RECORD_TOO_LARGE'):
        DurableBudget(root, 'e' * 64, max_bytes=1)
