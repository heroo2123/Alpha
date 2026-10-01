"""Synthetic, offline counterexamples for the Gate 3 V4 launch schema:
ordered endpoint table, frozen purpose plan, and the new runtime group.
Mirrors the V3 fixture in tests/test_v11_r09_gate3_launch.py but stays
self-contained so this slice's tests do not depend on V3's test module.
"""
import hashlib
import os
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import TZPATH

import pytest

from tools.v11_multimodel_panel import canonical
from tools import v11_r09_gate3_launch as launch
from tools import v11_r09_gate3_launch_v4 as launch_v4
from tools.v11_r09_gate3_launch import LaunchContractError, SLOT_COUNT, _slot_inventory
from tools.v11_r09_gate3_launch_v4 import OBSERVATION_PHASES, PURPOSES, validate_manifest_v4


def _git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args]).decode().strip()


def _endpoint_id(provider, origin, path_template, purpose):
    return hashlib.sha256(canonical([provider, origin, path_template, purpose])).hexdigest()


def _control_domain_id(provider, origin):
    return hashlib.sha256(canonical([provider, origin])).hexdigest()


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
        monkeypatch.setattr(launch_v4, constant, value)
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
        rdigest = hashlib.sha256(content).hexdigest()
        (objects / rdigest).write_bytes(content)
        (objects / rdigest).chmod(0o600)
        return {'sha256': rdigest, 'byte_length': len(content),
                'media_type': 'application/octet-stream'}
    monkeypatch.setattr(launch, 'PINNED_ORIGINAL_DOC', artifact_sha)
    monkeypatch.setattr(launch, 'PINNED_ADDENDUM_DOC', artifact_sha)
    monkeypatch.setattr(launch_v4, 'PINNED_ORIGINAL_DOC', artifact_sha)
    monkeypatch.setattr(launch_v4, 'PINNED_ADDENDUM_DOC', artifact_sha)
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
    endpoints = []
    for provider in ('GEFS', 'IFS', 'AIFS'):
        origin = sources[provider]['origin']
        template = sources[provider]['path_template']
        for purpose in PURPOSES:
            endpoints.append({
                'endpoint_id': _endpoint_id(provider, origin, template, purpose),
                'provider': provider, 'control_domain_id': _control_domain_id(provider, origin),
                'origin': origin, 'method': 'GET', 'purpose': purpose,
                'path_template': template, 'dossier': artifact,
                'access_reference': artifact, 'response_contract': artifact,
                'parser_identity': artifact})
    first_use_origins = []
    for entry in endpoints:
        if entry['origin'] not in first_use_origins:
            first_use_origins.append(entry['origin'])
    st = root.stat()
    payload = {
        'identity': {'schema': launch_v4.SCHEMA,
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
        'network': {'origins': first_use_origins,
                    'methods': ['GET'],
                    'path_templates': {p: sources[p]['path_template'] for p in runs},
                    'purposes': list(PURPOSES),
                    'index_binding': 'ETAG_IF_RANGE',
                    'anonymous': True, 'redirects': False, 'cookies': False,
                    'netrc': False, 'ambient_proxies': False, 'signed_urls': False,
                    'retries': False, 'dns_tls_policy': artifact,
                    'restriction_lineage': artifact, 'post_window_labels': False,
                    'endpoints': endpoints},
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
        'clocks_and_receipts': {
            'preregistration': {'method': artifact, 'host_boot': artifact,
                                 'sync_evidence': artifact, 'max_measurement_age_seconds': 60},
            'observation_schema': {'phases': list(OBSERVATION_PHASES),
                                    'uncertainty_seconds_cap': 1,
                                    'cutoff_utc': start + 12600}},
        'accounting': {'terminal_precedence': artifact, 'all_reasons': True,
                       'journal_hash_chain': True, 'no_silent_retry': True,
                       'expired_local_only': True, 'raw_partition': True,
                       'provider_eligibility': True, 'all_provider_intersection': True,
                       'diagnostics_no_credit': True},
        'runtime': {'policy': artifact, 'schedule_digest': '0' * 64,
                    'denial_root': {'descriptor': artifact, 'expected_history_head': '0' * 64},
                    'session_root': artifact, 'report_root': artifact,
                    'purpose_plan': {p: {'requests': 0, 'reservation_bytes': 0}
                                     for p in PURPOSES},
                    'journal_bounds': {'max_bytes': launch.JOURNAL_MAX_BYTES,
                                       'record_max_bytes': launch.JOURNAL_RECORD_MAX_BYTES,
                                       'max_events': launch.JOURNAL_MAX_EVENTS},
                    'clock_policy': artifact}}
    payload['schedule']['requests'] = [
        request_for_slot(payload, 0, purpose, position,
                         3145728 if purpose == 'INDEX' else
                         2097152 if purpose == 'FIELD' else 4096,
                         [0, 1, 2] if purpose == 'FIELD' else [])
        for position, purpose in enumerate(('INDEX', 'OBJECT_ID', 'METADATA', 'FIELD'))]
    _freeze_runtime(payload)
    return payload, repo, root, start


def request_for_slot(payload, slot_index, purpose, position, reservation, prerequisites):
    provider, run, member, hour = payload['runs_and_slots']['slots'][slot_index]
    source = payload['sources'][provider]
    path = source['path_template'].format(run=run, member=member, hour=hour)
    object_id = hashlib.sha256(canonical([provider, source['origin'], path])).hexdigest()
    index_id = hashlib.sha256(canonical([object_id, 'INDEX'])).hexdigest()
    cache_id = hashlib.sha256(canonical([object_id, index_id])).hexdigest()
    endpoint_id = _endpoint_id(provider, source['origin'], source['path_template'], purpose)
    return {'request_id': f'request_{position}', 'purpose': purpose,
            'slot_index': slot_index, 'provider': provider, 'origin': source['origin'],
            'path': path, 'object_id': object_id, 'index_id': index_id,
            'cache_id': cache_id, 'endpoint_id': endpoint_id,
            'range_start': 0 if purpose == 'FIELD' else None,
            'range_end': reservation - 1 if purpose == 'FIELD' else None,
            'reservation_bytes': reservation, 'prerequisites': prerequisites}


def _freeze_runtime(payload):
    """Recompute the frozen purpose plan and schedule digest from the
    actual schedule, exactly as an honest candidate builder must: the
    validator never trusts these, so a test fixture that faked them would
    only be testing itself."""
    from collections import defaultdict
    totals = defaultdict(lambda: {'requests': 0, 'reservation_bytes': 0})
    for request in payload['schedule']['requests']:
        totals[request['purpose']]['requests'] += 1
        totals[request['purpose']]['reservation_bytes'] += request['reservation_bytes']
    payload['runtime']['purpose_plan'] = {
        p: dict(totals.get(p, {'requests': 0, 'reservation_bytes': 0})) for p in PURPOSES}
    payload['runtime']['schedule_digest'] = hashlib.sha256(
        canonical(payload['schedule'])).hexdigest()


def validate(payload, repo, root, start):
    return validate_manifest_v4(canonical(payload), repo=repo, object_root=root,
                                now_utc=start - 4000)


def test_valid_v4_candidate_passes(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    assert len(validate(payload, repo, root, start)) == 64


def test_v3_schema_literal_rejected(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['identity']['schema'] = 'R09_GATE3_LAUNCH_MANIFEST_V3'
    with pytest.raises(LaunchContractError, match='IDENTITY_MODE'):
        validate(payload, repo, root, start)


def test_endpoint_table_requires_full_provider_purpose_coverage(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['network']['endpoints'].pop()
    with pytest.raises(LaunchContractError, match='ENDPOINT_TABLE_COVERAGE'):
        validate(payload, repo, root, start)


def test_endpoint_id_cannot_be_asserted_independent_of_derivation(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['network']['endpoints'][0]['endpoint_id'] = 'f' * 64
    with pytest.raises(LaunchContractError, match='ENDPOINT_ID_DERIVATION'):
        validate(payload, repo, root, start)


def test_duplicate_endpoint_id_rejected(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    first, second = payload['network']['endpoints'][0], payload['network']['endpoints'][1]
    second['endpoint_id'] = first['endpoint_id']
    with pytest.raises(LaunchContractError, match='ENDPOINT_ID_DERIVATION'):
        validate(payload, repo, root, start)


def test_endpoint_origin_must_match_bound_source_not_a_lookalike(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    entry = payload['network']['endpoints'][0]
    entry['origin'] = 'https://gefs.example.invalid.attacker.example'
    with pytest.raises(LaunchContractError, match='ENDPOINT_SOURCE_BINDING'):
        validate(payload, repo, root, start)


def test_network_allowlist_must_equal_first_use_order(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['network']['origins'] = list(reversed(payload['network']['origins']))
    with pytest.raises(LaunchContractError, match='NETWORK_ALLOWLIST_ORDER'):
        validate(payload, repo, root, start)


def test_schedule_request_endpoint_id_must_match_its_own_purpose(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    field_request = payload['schedule']['requests'][-1]
    assert field_request['purpose'] == 'FIELD'
    wrong_purpose_endpoint = next(
        e for e in payload['network']['endpoints']
        if e['provider'] == field_request['provider'] and e['purpose'] == 'INDEX')
    field_request['endpoint_id'] = wrong_purpose_endpoint['endpoint_id']
    with pytest.raises(LaunchContractError, match='SCHEDULE_ENDPOINT_BINDING'):
        validate(payload, repo, root, start)


def test_schedule_request_endpoint_id_cannot_alias_object_identity(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    request = payload['schedule']['requests'][0]
    request['endpoint_id'] = request['object_id']
    with pytest.raises(LaunchContractError, match='SCHEDULE_ENDPOINT_BINDING'):
        validate(payload, repo, root, start)


def test_purpose_plan_cannot_be_inflated_by_caller(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['runtime']['purpose_plan']['FIELD']['reservation_bytes'] += 1
    with pytest.raises(LaunchContractError, match='RUNTIME_PURPOSE_PLAN_MISMATCH'):
        validate(payload, repo, root, start)


def test_purpose_plan_cannot_hide_unused_overhead_in_field(tmp_path, monkeypatch):
    """Design requirement: unused overhead/field headroom cannot expand
    another purpose. Moving one real FIELD request's accounted bytes into
    INDEX's bucket (while leaving totals summing correctly) must still fail,
    because each purpose's own bucket is independently recomputed."""
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    plan = payload['runtime']['purpose_plan']
    moved = 100
    plan['FIELD']['reservation_bytes'] -= moved
    plan['INDEX']['reservation_bytes'] += moved
    with pytest.raises(LaunchContractError, match='RUNTIME_PURPOSE_PLAN_MISMATCH'):
        validate(payload, repo, root, start)


def test_runtime_schedule_digest_must_match_actual_schedule(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['runtime']['schedule_digest'] = 'f' * 64
    with pytest.raises(LaunchContractError, match='RUNTIME_SCHEDULE_DIGEST'):
        validate(payload, repo, root, start)


def test_runtime_journal_bounds_must_match_fixed_code_constants(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['runtime']['journal_bounds']['max_bytes'] += 1
    with pytest.raises(LaunchContractError, match='RUNTIME_JOURNAL_BOUNDS_MISMATCH'):
        validate(payload, repo, root, start)
    payload['runtime']['journal_bounds']['max_bytes'] -= 1
    assert len(validate(payload, repo, root, start)) == 64
    payload['runtime']['journal_bounds']['max_events'] -= 1
    with pytest.raises(LaunchContractError, match='RUNTIME_JOURNAL_BOUNDS_MISMATCH'):
        validate(payload, repo, root, start)


def test_clock_schema_separates_preregistration_from_observation_phases(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['clocks_and_receipts']['observation_schema']['phases'] = [
        'request_start', 'body_receipt', 'decode_complete']
    with pytest.raises(LaunchContractError, match='CLOCK_FOUR_PHASES'):
        validate(payload, repo, root, start)


def test_clock_observation_uncertainty_must_match_time_group(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['clocks_and_receipts']['observation_schema']['uncertainty_seconds_cap'] = 0.5
    with pytest.raises(LaunchContractError, match='CLOCK_UNCERTAINTY_CAP'):
        validate(payload, repo, root, start)


def test_clock_observation_cutoff_must_match_feature_seal_upper(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['clocks_and_receipts']['observation_schema']['cutoff_utc'] += 1
    with pytest.raises(LaunchContractError, match='CLOCK_CUTOFF'):
        validate(payload, repo, root, start)


def test_clock_schema_cannot_carry_fabricated_per_attempt_refs(tmp_path, monkeypatch):
    """V3's per-attempt clock fields (request_start/body_receipt/etc as refs)
    must not reappear in V4: those are runtime-produced records, never
    preregistered artifacts, so the group schema has no slot for them."""
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['clocks_and_receipts']['observation_schema']['request_start'] = \
        payload['clocks_and_receipts']['preregistration']['method']
    with pytest.raises(LaunchContractError, match='CLOCK_OBSERVATION_SCHEMA'):
        validate(payload, repo, root, start)


def test_runtime_group_missing_rejected(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    del payload['runtime']
    with pytest.raises(LaunchContractError, match='MANIFEST_GROUP_SCHEMA'):
        validate(payload, repo, root, start)


def test_denial_root_history_head_must_be_hex_digest(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['runtime']['denial_root']['expected_history_head'] = 'not-a-digest'
    with pytest.raises(LaunchContractError, match='RUNTIME_DENIAL_HISTORY_HEAD'):
        validate(payload, repo, root, start)
