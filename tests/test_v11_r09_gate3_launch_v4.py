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
from tools.v11_r09_gate3_launch_v4 import (
    OBSERVATION_PHASES, PURPOSES, _render_path, validate_manifest_v4,
)


def _git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args]).decode().strip()


def _endpoint_id(provider, origin, path_spec, purpose):
    return hashlib.sha256(canonical([provider, origin, path_spec, purpose])).hexdigest()


def _control_domain_id(provider, origin):
    return hashlib.sha256(canonical([provider, origin])).hexdigest()


def _lit(value):
    return {'kind': 'LITERAL', 'value': value}


def _val(kind):
    return {'kind': kind, 'value': None}


def gefs_field_spec():
    """ge{c00|pNN}.t{HH}z.pgrb2a.0p50.f{HHH} under /gefs.{date}/{HH}/atmos/pgrb2ap5,
    exactly as evidenced by polymarket_scanner/v11/gefs_sources.py field_request()."""
    return [_lit('/gefs.'), _val('RUN_DATE_YYYYMMDD'), _lit('/'), _val('RUN_CYCLE_HH'),
            _lit('/atmos/pgrb2ap5/ge'), _val('GEFS_MEMBER_SUFFIX'), _lit('.t'),
            _val('RUN_CYCLE_HH'), _lit('z.pgrb2a.0p50.f'), _val('GEFS_HOUR_3PAD')]


def ecmwf_field_spec(suffix='.grib2'):
    """{date}/{HH}z/{model}/0p25/{stream}/{date}{HH}0000-{step}h-{stream}-{file_kind}{suffix},
    exactly as evidenced by polymarket_scanner/v11/ecmwf_sources.py ECMWFRequest.url.
    No member component anywhere: perturbed members share one object, selected
    only by byte range (see ECMWFRequest.selectors / plan_ranges)."""
    return [_lit('/'), _val('RUN_DATE_YYYYMMDD'), _lit('/'), _val('RUN_CYCLE_HHZ'),
            _lit('/forecast/0p25/'), _val('ECMWF_STREAM'), _lit('/'),
            _val('RUN_CYCLE_YYYYMMDDHH0000'), _lit('-'), _val('ECMWF_STEP_HOURS'),
            _lit('h-'), _val('ECMWF_STREAM'), _lit('-'), _val('ECMWF_FILE_KIND'),
            _lit(suffix)]


def path_spec_for(provider, purpose):
    if provider == 'GEFS':
        return gefs_field_spec() if purpose == 'FIELD' else [
            _lit('/gefs/'), _val('RUN_DATE_YYYYMMDD'), _lit('/'), _lit(purpose.lower())]
    if purpose == 'INDEX':
        return ecmwf_field_spec(suffix='.index')
    if purpose == 'FIELD':
        return ecmwf_field_spec()
    return [_lit('/'), _val('RUN_DATE_YYYYMMDD'), _lit('/'), _lit(purpose.lower())]


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
    design_refs = {key: review_artifact('design:' + key)
                   for key in ('document', 'report', 'terminal')}
    for constant, value in (('PINNED_DESIGN_COMMIT', commit),
                            ('PINNED_DESIGN_TREE', tree),
                            ('PINNED_DESIGN_DOC', design_refs['document']['sha256']),
                            ('PINNED_DESIGN_REVIEW_REPORT', design_refs['report']['sha256']),
                            ('PINNED_DESIGN_REVIEW_TERMINAL', design_refs['terminal']['sha256'])):
        monkeypatch.setattr(launch_v4, constant, value)
    start = int(datetime(2027, 2, 28, 14, tzinfo=timezone.utc).timestamp())
    runs = {p: start - 14 * 3600 for p in ('GEFS', 'IFS', 'AIFS')}
    slots = _slot_inventory(runs)
    sources = {p: {'dossier': artifact, 'release_document': artifact,
                   'licence': artifact, 'index_evidence': artifact,
                   'range_evidence': artifact, 'decoder_build': artifact,
                   'origin': f'https://{p.lower()}.example.invalid',
                   'path_spec': path_spec_for(p, 'FIELD'),
                   'member_range': [0, 30 if p == 'GEFS' else 50],
                   'native_hours': list(range(0, 73, 6 if p == 'AIFS' else 3)),
                   'identity_pins': artifact,
                   'effective_run_start_utc': runs[p] - 3600,
                   'effective_run_end_utc': runs[p] + 3600,
                   'publication_attestation': None,
                   'publication_absence_reason': 'not published in synthetic fixture'}
               for p in runs}
    for provider, source in sources.items():
        domain = review_artifact(provider + ':control-domain')
        source['control_domain'] = domain
        source['purpose_mappings'] = {
            purpose: {'origin': source['origin'],
                      'path_spec': (source['path_spec'] if purpose == 'FIELD'
                                    else path_spec_for(provider, purpose)),
                      'control_domain_id': domain['sha256'],
                      'mapping_evidence': review_artifact(provider + ':' + purpose + ':mapping'),
                      'validator': review_artifact(provider + ':' + purpose + ':validator'),
                      'response_contract': review_artifact(provider + ':' + purpose + ':contract')}
            for purpose in PURPOSES}
    endpoints = []
    for provider in ('GEFS', 'IFS', 'AIFS'):
        origin = sources[provider]['origin']
        for purpose in PURPOSES:
            mapping = sources[provider]['purpose_mappings'][purpose]
            mapped_origin = mapping['origin']
            spec = mapping['path_spec']
            endpoints.append({
                'endpoint_id': _endpoint_id(provider, mapped_origin, spec, purpose),
                'provider': provider, 'control_domain_id': mapping['control_domain_id'],
                'origin': mapped_origin, 'method': 'GET', 'purpose': purpose,
                'path_spec': spec, 'dossier': artifact,
                'access_reference': mapping['mapping_evidence'],
                'response_contract': mapping['response_contract'],
                'parser_identity': mapping['validator']})
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
                                 for name in sorted(launch.REQUIRED_REVIEW_NAMES)],
                     'reviewed_design': {'commit_oid': commit, 'tree_oid': tree,
                                         **design_refs}},
        'storage': {'root': str(root), 'owner_uid': os.getuid(), 'mode': 0o700,
                    'directory_dev': st.st_dev, 'directory_inode': st.st_ino,
                    'layout': 'OBJECTS_AND_APPEND_ONLY_LEDGER', 'exclusive_lock': True,
                    'atomic_fsync_seal': True, 'report_reserve_bytes': launch_v4.REPORT_RESERVE_BYTES,
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
                    'path_specs': {p: sources[p]['path_spec'] for p in runs},
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
                   'report_storage': launch_v4.REPORT_RESERVE_BYTES},
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
                    'clock_policy': artifact,
                    'resource_bounds': {
                        'session_journal_max_bytes': launch.JOURNAL_MAX_BYTES,
                        'denial_journal_max_bytes': launch.JOURNAL_MAX_BYTES,
                        'store_journal_max_bytes': launch.JOURNAL_MAX_BYTES,
                        'journal_record_max_bytes': launch.JOURNAL_RECORD_MAX_BYTES,
                        'session_journal_max_events': launch_v4.SESSION_JOURNAL_MAX_EVENTS,
                        'denial_journal_max_events': launch_v4.SESSION_JOURNAL_MAX_EVENTS,
                        'store_max_events': launch_v4.STORE_MAX_EVENTS,
                        'store_object_max_bytes': launch_v4.STORE_OBJECT_MAX_BYTES,
                        'store_max_objects': launch_v4.STORE_MAX_OBJECTS,
                        'descriptor_max_bytes': 4096,
                        'clock_record_max_bytes': 16384,
                        'receipt_max_dependencies': 256,
                        'max_body_chunks_per_request': 32,
                        'report_reserve_bytes': launch_v4.REPORT_RESERVE_BYTES,
                        'required_store_objects': 9,
                        'local_storage_quota_bytes': 1024 ** 3}}}
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
    field_mapping = source['purpose_mappings']['FIELD']
    field_path = _render_path(field_mapping['path_spec'], origin=field_mapping['origin'],
                              provider=provider, run_utc=run, member=member, hour=hour)
    mapping = source['purpose_mappings'][purpose]
    index_mapping = source['purpose_mappings']['INDEX']
    index_path = _render_path(index_mapping['path_spec'], origin=index_mapping['origin'],
                              provider=provider, run_utc=run, member=member, hour=hour)
    object_mapping = source['purpose_mappings']['OBJECT_ID']
    object_path = _render_path(object_mapping['path_spec'], origin=object_mapping['origin'],
                               provider=provider, run_utc=run, member=member, hour=hour)
    object_id = hashlib.sha256(canonical([
        provider, source['origin'], field_path,
        source['purpose_mappings']['FIELD']['mapping_evidence']['sha256'],
        object_mapping['origin'], object_path,
        object_mapping['mapping_evidence']['sha256']])).hexdigest()
    index_id = hashlib.sha256(canonical([
        object_id, index_mapping['origin'], index_path,
        index_mapping['mapping_evidence']['sha256']])).hexdigest()
    cache_id = hashlib.sha256(canonical([object_id, index_id])).hexdigest()
    endpoint_id = _endpoint_id(provider, mapping['origin'], mapping['path_spec'], purpose)
    path = _render_path(mapping['path_spec'], origin=mapping['origin'],
                        provider=provider, run_utc=run, member=member, hour=hour)
    return {'request_id': f'request_{position}', 'purpose': purpose,
            'slot_index': slot_index, 'provider': provider, 'origin': mapping['origin'],
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


def _rebind_endpoints_and_requests(payload):
    """Build honest endpoint and request identities from changed source mappings."""
    endpoints = []
    for provider, source in payload['sources'].items():
        for purpose in PURPOSES:
            mapping = source['purpose_mappings'][purpose]
            endpoints.append({
                'endpoint_id': _endpoint_id(provider, mapping['origin'],
                                            mapping['path_spec'], purpose),
                'provider': provider, 'control_domain_id': mapping['control_domain_id'],
                'origin': mapping['origin'], 'method': 'GET', 'purpose': purpose,
                'path_spec': mapping['path_spec'], 'dossier': source['dossier'],
                'access_reference': mapping['mapping_evidence'],
                'response_contract': mapping['response_contract'],
                'parser_identity': mapping['validator']})
    payload['network']['endpoints'] = endpoints
    payload['network']['origins'] = list(dict.fromkeys(e['origin'] for e in endpoints))
    requests = payload['schedule']['requests']
    payload['schedule']['requests'] = [
        request_for_slot(payload, request['slot_index'], request['purpose'], index,
                         request['reservation_bytes'], request['prerequisites'])
        for index, request in enumerate(requests)]
    _freeze_runtime(payload)


def test_explicit_separate_index_and_metadata_paths_and_related_origin_pass(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    source = payload['sources']['GEFS']
    source['purpose_mappings']['INDEX']['path_spec'] = [
        _lit('/index/'), _val('RUN_DATE_YYYYMMDD'), _lit('/'), _val('RUN_CYCLE_HH'),
        _lit('/ge'), _val('GEFS_MEMBER_SUFFIX'), _lit('.f'), _val('GEFS_HOUR_3PAD'), _lit('.idx')]
    source['purpose_mappings']['OBJECT_ID']['path_spec'] = [
        _lit('/object/'), _val('RUN_DATE_YYYYMMDD'), _lit('/'), _val('RUN_CYCLE_HH'),
        _lit('/ge'), _val('GEFS_MEMBER_SUFFIX'), _lit('.f'), _val('GEFS_HOUR_3PAD'), _lit('.json')]
    source['purpose_mappings']['METADATA']['origin'] = 'https://official.example.invalid'
    source['purpose_mappings']['METADATA']['path_spec'] = [_lit('/metadata/event')]
    _rebind_endpoints_and_requests(payload)
    assert payload['schedule']['requests'][0]['path'].endswith('.idx')
    assert payload['schedule']['requests'][2]['origin'] == 'https://official.example.invalid'
    assert payload['schedule']['requests'][3]['path'] != payload['schedule']['requests'][0]['path']
    assert len(validate(payload, repo, root, start)) == 64


def test_implicit_index_suffix_without_reviewed_mapping_refuses(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['schedule']['requests'][0]['path'] += '.idx'
    _freeze_runtime(payload)
    with pytest.raises(LaunchContractError, match='SCHEDULE_OBJECT_BINDING'):
        validate(payload, repo, root, start)


def test_missing_or_substituted_purpose_mapping_refuses(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['sources']['GEFS']['purpose_mappings'].pop('INDEX')
    with pytest.raises(LaunchContractError, match='SOURCE_PURPOSE_MAPPINGS'):
        validate(payload, repo, root, start)
    (tmp_path / 'second').mkdir()
    payload, repo, root, start = candidate(tmp_path / 'second', monkeypatch)
    payload['sources']['GEFS']['purpose_mappings']['INDEX']['validator'] = (
        payload['sources']['GEFS']['purpose_mappings']['FIELD']['validator'])
    with pytest.raises(LaunchContractError, match='ENDPOINT_SOURCE_BINDING'):
        validate(payload, repo, root, start)


def test_unapproved_origin_or_wrong_control_domain_refuses(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['network']['endpoints'][0]['origin'] = 'https://unmapped.example.invalid'
    with pytest.raises(LaunchContractError, match='ENDPOINT_SOURCE_BINDING'):
        validate(payload, repo, root, start)
    (tmp_path / 'second').mkdir()
    payload, repo, root, start = candidate(tmp_path / 'second', monkeypatch)
    payload['network']['endpoints'][0]['control_domain_id'] = 'f' * 64
    with pytest.raises(LaunchContractError, match='CONTROL_DOMAIN_DERIVATION'):
        validate(payload, repo, root, start)


@pytest.mark.parametrize('interval,feasible', [(2, 246), (40, 360)])
def test_v4_conservative_post_close_timing_boundary(tmp_path, monkeypatch,
                                                     interval, feasible):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['limits']['min_start_interval_seconds'] = interval
    payload['limits']['max_elapsed_seconds'] = feasible
    assert len(validate(payload, repo, root, start)) == 64
    payload['limits']['max_elapsed_seconds'] = feasible - 1
    with pytest.raises(LaunchContractError, match='SCHEDULE_TIME_FEASIBILITY'):
        validate(payload, repo, root, start)


def test_v4_report_reserve_and_storage_boundaries(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    assert len(validate(payload, repo, root, start)) == 64
    payload['storage']['report_reserve_bytes'] -= 1
    with pytest.raises(LaunchContractError, match='REPORT_RESERVE'):
        validate(payload, repo, root, start)
    payload['storage']['report_reserve_bytes'] += 1
    payload['limits']['report_storage'] -= 1
    with pytest.raises(LaunchContractError, match='REPORT_STORAGE_BOUND'):
        validate(payload, repo, root, start)
    payload['limits']['report_storage'] += 1
    resources = payload['runtime']['resource_bounds']
    required = (4 * launch.JOURNAL_MAX_BYTES +
                payload['storage']['report_reserve_bytes'] +
                resources['required_store_objects'] * launch_v4.STORE_OBJECT_MAX_BYTES +
                payload['limits']['decoded'] +
                payload['schedule']['reservation_total_bytes'])
    resources['local_storage_quota_bytes'] = required
    assert len(validate(payload, repo, root, start)) == 64
    resources['local_storage_quota_bytes'] -= 1
    with pytest.raises(LaunchContractError, match='RUNTIME_STORAGE_QUOTA'):
        validate(payload, repo, root, start)


def test_v4_metadata_header_and_resource_caps(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['limits']['headers'] = 4096
    assert len(validate(payload, repo, root, start)) == 64
    payload['limits']['headers'] = 4097
    with pytest.raises(LaunchContractError, match='HEADER_BOUND'):
        validate(payload, repo, root, start)
    payload['limits']['headers'] = 4096
    payload['limits']['metadata'] = launch_v4.METADATA_MAX_BYTES + 1
    with pytest.raises(LaunchContractError, match='METADATA_BOUND'):
        validate(payload, repo, root, start)
    payload['limits']['metadata'] = launch_v4.METADATA_MAX_BYTES
    for request in payload['schedule']['requests']:
        if request['purpose'] in ('OBJECT_ID', 'METADATA'):
            request['reservation_bytes'] = launch_v4.METADATA_MAX_BYTES
    payload['schedule']['reservation_total_bytes'] = sum(
        r['reservation_bytes'] for r in payload['schedule']['requests'])
    _freeze_runtime(payload)
    assert len(validate(payload, repo, root, start)) == 64
    payload['runtime']['resource_bounds']['session_journal_max_events'] += 1
    with pytest.raises(LaunchContractError, match='RUNTIME_RESOURCE_BOUNDS'):
        validate(payload, repo, root, start)


def test_v4_reviewed_design_binding_required_and_pinned(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    assert len(validate(payload, repo, root, start)) == 64
    design = payload['protocol'].pop('reviewed_design')
    with pytest.raises(LaunchContractError, match='PROTOCOL_SCHEMA'):
        validate(payload, repo, root, start)
    payload['protocol']['reviewed_design'] = design
    design['report'] = payload['protocol']['reviews'][0]['report']
    with pytest.raises(LaunchContractError, match='DESIGN_REVIEW_PIN_MISMATCH'):
        validate(payload, repo, root, start)


# --- Typed path-component representation: provider-mapping correction ----
# docs/V11_GATE3_LAUNCH_READINESS_AUDIT_20261001.md section 2 found that the
# prior `{run}/{member}/{hour}`-format mechanism could not express real GEFS
# control-vs-perturbed/padded naming or ECMWF's member-less multiplexed
# perturbed-file paths. These tests pin the corrected renderer against the
# exact layouts evidenced in polymarket_scanner/v11/gefs_sources.py
# field_request() and polymarket_scanner/v11/ecmwf_sources.py
# ECMWFRequest.url/selectors, and check the new rejection paths.

GEFS_ORIGIN = 'https://gefs.example.invalid'
IFS_ORIGIN = 'https://ifs.example.invalid'
RUN_UTC = int(datetime(2027, 3, 1, 0, tzinfo=timezone.utc).timestamp())


def test_gefs_field_path_uses_control_vs_perturbed_naming_and_padding():
    spec = gefs_field_spec()
    control = _render_path(spec, origin=GEFS_ORIGIN, provider='GEFS',
                           run_utc=RUN_UTC, member=0, hour=3)
    perturbed_5 = _render_path(spec, origin=GEFS_ORIGIN, provider='GEFS',
                               run_utc=RUN_UTC, member=5, hour=3)
    perturbed_30 = _render_path(spec, origin=GEFS_ORIGIN, provider='GEFS',
                                run_utc=RUN_UTC, member=30, hour=72)
    assert control == '/gefs.20270301/00/atmos/pgrb2ap5/gec00.t00z.pgrb2a.0p50.f003'
    assert perturbed_5 == '/gefs.20270301/00/atmos/pgrb2ap5/gep05.t00z.pgrb2a.0p50.f003'
    assert perturbed_30 == '/gefs.20270301/00/atmos/pgrb2ap5/gep30.t00z.pgrb2a.0p50.f072'
    # Control and every distinct perturbed member/hour are genuinely distinct
    # objects for GEFS (no multiplexing): each is its own file.
    assert len({control, perturbed_5, perturbed_30}) == 3


def test_ecmwf_field_path_distinguishes_control_but_multiplexes_perturbed_members():
    run = datetime.fromtimestamp(RUN_UTC, timezone.utc)
    prefix = f'/{run:%Y%m%d}/{run:%H}z/forecast/0p25/'
    cycle = f'{run:%Y%m%d%H}0000'
    spec = ecmwf_field_spec()
    control = _render_path(spec, origin=IFS_ORIGIN, provider='IFS',
                           run_utc=RUN_UTC, member=0, hour=3)
    member_1 = _render_path(spec, origin=IFS_ORIGIN, provider='IFS',
                            run_utc=RUN_UTC, member=1, hour=3)
    member_50 = _render_path(spec, origin=IFS_ORIGIN, provider='IFS',
                             run_utc=RUN_UTC, member=50, hour=3)
    assert control == f'{prefix}oper/{cycle}-3h-oper-fc.grib2'
    assert member_1 == f'{prefix}enfo/{cycle}-3h-enfo-ef.grib2'
    # Every perturbed member (1..50) renders the identical path: the real
    # adapter fetches one shared file per run/step and selects each member's
    # bytes only via its own Range request against a shared index -- the
    # multiplexed object identity the launch-readiness audit required.
    assert member_1 == member_50
    assert control != member_1
    aifs_control = _render_path(spec, origin=IFS_ORIGIN, provider='AIFS',
                                run_utc=RUN_UTC, member=0, hour=6)
    aifs_perturbed = _render_path(spec, origin=IFS_ORIGIN, provider='AIFS',
                                  run_utc=RUN_UTC, member=1, hour=6)
    assert aifs_control == f'{prefix}enfo/{cycle}-6h-enfo-cf.grib2'
    assert aifs_perturbed == f'{prefix}enfo/{cycle}-6h-enfo-pf.grib2'


def test_ecmwf_index_path_is_explicit_sibling_not_derived_suffix():
    field_path = _render_path(ecmwf_field_spec(), origin=IFS_ORIGIN, provider='IFS',
                              run_utc=RUN_UTC, member=1, hour=3)
    index_path = _render_path(ecmwf_field_spec(suffix='.index'), origin=IFS_ORIGIN,
                              provider='IFS', run_utc=RUN_UTC, member=1, hour=3)
    assert field_path.endswith('.grib2') and index_path.endswith('.index')
    assert field_path[:-len('.grib2')] == index_path[:-len('.index')]


def test_multiplexed_ecmwf_perturbed_members_share_object_identity_in_full_schedule(
        tmp_path, monkeypatch):
    """End-to-end: two different IFS perturbed-member FIELD requests at the
    same run/hour legitimately share object_id/index_id/cache_id (and may
    therefore share one set of overhead prerequisites), while keeping
    distinct request_id/slot_index/byte range. This is the real-world
    shared-file/distinct-Range-request shape, not a validator bug."""
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    slots = payload['runs_and_slots']['slots']
    ifs_run = payload['runs_and_slots']['run_utc']['IFS']
    idx_m1 = slots.index(['IFS', ifs_run, 1, 0])
    idx_m2 = slots.index(['IFS', ifs_run, 2, 0])
    payload['schedule']['attempt_slots'] = [0, idx_m1, idx_m2]
    requests, position, overhead_by_slot = [], 0, {}
    for slot, purpose, reservation in (
            (0, 'INDEX', 3145728), (0, 'OBJECT_ID', 4096), (0, 'METADATA', 4096),
            (idx_m1, 'INDEX', 3145728), (idx_m1, 'OBJECT_ID', 4096), (idx_m1, 'METADATA', 4096)):
        req = request_for_slot(payload, slot, purpose, position, reservation, [])
        requests.append(req)
        overhead_by_slot.setdefault(slot, []).append(position)
        position += 1
    field0 = request_for_slot(payload, 0, 'FIELD', position, 2097152, overhead_by_slot[0])
    requests.append(field0); position += 1
    field_m1 = request_for_slot(payload, idx_m1, 'FIELD', position, 4194304,
                                overhead_by_slot[idx_m1])
    requests.append(field_m1); position += 1
    field_m2 = request_for_slot(payload, idx_m2, 'FIELD', position, 4194304,
                                overhead_by_slot[idx_m1])
    requests.append(field_m2); position += 1
    payload['schedule']['requests'] = requests
    payload['schedule']['reservation_total_bytes'] = sum(
        r['reservation_bytes'] for r in requests)
    _freeze_runtime(payload)
    count = len(requests)
    graph_nodes, aggregates = count, 0
    while graph_nodes > 1:
        graph_nodes = (graph_nodes + 255) // 256
        aggregates += graph_nodes
    payload['runtime']['resource_bounds']['required_store_objects'] = 2 * count + aggregates
    assert field_m1['object_id'] == field_m2['object_id']
    assert field_m1['index_id'] == field_m2['index_id']
    assert field_m1['cache_id'] == field_m2['cache_id']
    assert field_m1['path'] == field_m2['path']
    assert field_m1['request_id'] != field_m2['request_id']
    assert field_m1['slot_index'] != field_m2['slot_index']
    assert field_m1['object_id'] != field0['object_id']
    assert len(validate(payload, repo, root, start)) == 64


def test_gefs_only_kind_rejected_in_ecmwf_mapping(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['sources']['IFS']['purpose_mappings']['FIELD']['path_spec'] = [
        _lit('/x/'), _val('GEFS_MEMBER_SUFFIX')]
    with pytest.raises(LaunchContractError, match='SOURCE_MAPPING_PATH'):
        validate(payload, repo, root, start)


def test_unknown_path_component_kind_rejected(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['sources']['GEFS']['purpose_mappings']['FIELD']['path_spec'] = [
        _lit('/x/'), {'kind': 'ARBITRARY_FORMAT_SUBSTITUTION', 'value': None}]
    with pytest.raises(LaunchContractError, match='SOURCE_MAPPING_PATH'):
        validate(payload, repo, root, start)


def test_literal_component_cannot_encode_path_traversal(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['sources']['GEFS']['purpose_mappings']['METADATA']['path_spec'] = [
        _lit('/metadata/../secret')]
    with pytest.raises(LaunchContractError, match='SOURCE_MAPPING_PATH'):
        validate(payload, repo, root, start)


def test_non_literal_component_cannot_carry_asserted_value(tmp_path, monkeypatch):
    """A kind's rendering is always the fixed reviewed function, never a
    caller-supplied override: a non-LITERAL component's 'value' must be null."""
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    payload['sources']['GEFS']['purpose_mappings']['FIELD']['path_spec'][1] = {
        'kind': 'RUN_DATE_YYYYMMDD', 'value': '99999999'}
    with pytest.raises(LaunchContractError, match='SOURCE_MAPPING_PATH'):
        validate(payload, repo, root, start)


def test_required_kind_count_enforced_for_evidenced_gefs_field_path(tmp_path, monkeypatch):
    """GEFS FIELD must render both the directory and 't..z' cycle hour (two
    RUN_CYCLE_HH occurrences); dropping one must not silently validate a
    shorter, unevidenced layout."""
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    spec = payload['sources']['GEFS']['purpose_mappings']['FIELD']['path_spec']
    payload['sources']['GEFS']['purpose_mappings']['FIELD']['path_spec'] = [
        c for c in spec if c['kind'] != 'RUN_CYCLE_HH'] + [_val('RUN_CYCLE_HH')]
    with pytest.raises(LaunchContractError, match='SOURCE_MAPPING_PATH_KIND_COUNT'):
        validate(payload, repo, root, start)


def test_required_kind_count_enforced_for_evidenced_ecmwf_field_path(tmp_path, monkeypatch):
    """ECMWF FIELD must render the stream literal twice (directory and
    filename); a mapping that only renders it once does not match the
    evidenced layout and must refuse, not silently validate."""
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    spec = payload['sources']['IFS']['purpose_mappings']['FIELD']['path_spec']
    trimmed, seen = [], False
    for component in spec:
        if component['kind'] == 'ECMWF_STREAM' and seen:
            continue
        if component['kind'] == 'ECMWF_STREAM':
            seen = True
        trimmed.append(component)
    payload['sources']['IFS']['purpose_mappings']['FIELD']['path_spec'] = trimmed
    with pytest.raises(LaunchContractError, match='SOURCE_MAPPING_PATH_KIND_COUNT'):
        validate(payload, repo, root, start)


def test_field_mapping_path_spec_must_match_source_top_level_spec(tmp_path, monkeypatch):
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    # Same evidenced kind multiset (so this fails on identity, not on the
    # required-kind-count check), but a relabelled literal directory: proves
    # the per-purpose FIELD mapping cannot silently diverge from the
    # provider's own top-level path_spec.
    diverged = list(gefs_field_spec())
    diverged[0] = _lit('/gefs-mirror.')
    payload['sources']['GEFS']['purpose_mappings']['FIELD']['path_spec'] = diverged
    with pytest.raises(LaunchContractError, match='FIELD_SOURCE_MAPPING'):
        validate(payload, repo, root, start)
