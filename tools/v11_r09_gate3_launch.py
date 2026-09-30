"""Offline Gate 3 launch-contract validation and durable request accounting.

This module has no transport, decoder, credential or launch entrypoint. A valid
payload is only a candidate for independent exact-digest G3-L review.
"""
from __future__ import annotations

import fcntl
import hashlib
import ipaddress
import json
import math
import os
import re
import stat
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from tools.v11_multimodel_panel import canonical

SCHEMA = 'R09_GATE3_LAUNCH_MANIFEST_V2'
SLOTS = {'GEFS': (31, 3), 'IFS': (51, 3), 'AIFS': (51, 6)}
SLOT_COUNT = 2713
MAX_BYTES = 1024 ** 3
FIELD_LIMITS = {'GEFS': 2 * 1024 ** 2, 'IFS': 4 * 1024 ** 2,
                'AIFS': 4 * 1024 ** 2}
PINNED_ORIGINAL_COMMIT = '117830a9b418cdf2a1b1f5146e074343623b8e3f'
PINNED_ORIGINAL_TREE = '07c4d72fcafb4897896e27dfbcc415e7ab078ee9'
PINNED_ORIGINAL_DOC = 'da3144c558134e7bd6a06b5c3f298740e86cee13be7c54a9932204362c269524'
PINNED_ADDENDUM_COMMIT = '14c2413c4cef63dab35a806955fcdd3ef0942f69'
PINNED_ADDENDUM_TREE = 'd37d7ad55a76902cae0536e6f4d21b9db6aeaa81'
PINNED_ADDENDUM_DOC = 'a4a2a18e83a53359e3a46cdf7cbda6031c6afec74e5d497f2cd126b1ae7b943c'
REQUIRED_REVIEW_NAMES = {'original_protocol', 'collector', 'provider_bound',
                         'gefs_ceiling', 'launch_addendum'}
GROUPS = ('identity', 'code', 'protocol', 'storage', 'cohort', 'time',
          'sources', 'runs_and_slots', 'network', 'limits', 'schedule',
          'clocks_and_receipts', 'accounting')
HEX64 = re.compile(r'[0-9a-f]{64}\Z')


class LaunchContractError(ValueError):
    pass


def check(ok, reason):
    if not ok:
        raise LaunchContractError(reason)


def exact(obj, keys, reason):
    check(type(obj) is dict and set(obj) == set(keys), reason)


def integer(value, low, high, reason):
    check(type(value) is int and low <= value <= high, reason)


def digest(value, reason):
    check(type(value) is str and HEX64.fullmatch(value) is not None, reason)


def ref(value, reason):
    exact(value, ('sha256', 'byte_length', 'media_type'), reason)
    digest(value['sha256'], reason)
    integer(value['byte_length'], 1, MAX_BYTES, reason)
    check(type(value['media_type']) is str and '/' in value['media_type'], reason)


def _pairs(items):
    result = {}
    for key, value in items:
        check(key not in result, 'DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def parse_canonical(raw):
    check(type(raw) is bytes and len(raw) <= 32 * 1024 ** 2, 'MANIFEST_BYTES_BOUND')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                           parse_constant=lambda _: check(False, 'NONFINITE_JSON'))
    except LaunchContractError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise LaunchContractError('INVALID_JSON') from exc
    try:
        check(raw == canonical(value), 'NONCANONICAL_MANIFEST')
    except ValueError as exc:
        raise LaunchContractError('NONFINITE_JSON') from exc
    return value


def _git(repo, *args):
    try:
        return subprocess.check_output(['git', '-C', str(repo), *args],
                                       stderr=subprocess.DEVNULL).decode().strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise LaunchContractError('GIT_RESOLUTION_FAILED') from exc


def _validate_code(code, repo):
    exact(code, ('object_format', 'components', 'dependency_lock'), 'CODE_SCHEMA')
    fmt = _git(repo, 'rev-parse', '--show-object-format')
    check(code['object_format'] == fmt and fmt in ('sha1', 'sha256'), 'GIT_OBJECT_FORMAT')
    oid_len = 40 if fmt == 'sha1' else 64
    exact(code['components'], ('collector', 'launch_validator', 'transport',
                               'decoder', 'clock_recorder'), 'CODE_COMPONENT_SET')
    dirty = subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain',
                                     '--untracked-files=all']).decode().splitlines()
    for name, item in code['components'].items():
        exact(item, ('commit_oid', 'tree_oid', 'path', 'sha256'), 'CODE_COMPONENT_SCHEMA')
        oid, tree, path = item['commit_oid'], item['tree_oid'], item['path']
        check(type(oid) is str and len(oid) == oid_len and
              re.fullmatch('[0-9a-f]+', oid) is not None, 'GIT_COMMIT_OID')
        check(type(tree) is str and len(tree) == oid_len and
              re.fullmatch('[0-9a-f]+', tree) is not None, 'GIT_TREE_OID')
        check(_git(repo, 'cat-file', '-t', oid) == 'commit' and
              _git(repo, 'rev-parse', f'{oid}^{{tree}}') == tree, 'GIT_COMMIT_TREE')
        check(type(path) is str and path and not path.startswith('/') and
              '..' not in Path(path).parts, 'CODE_PATH')
        check(not any(line[3:] == path for line in dirty), 'DIRTY_EXECUTABLE_CODE')
        content = subprocess.check_output(['git', '-C', str(repo), 'show', f'{oid}:{path}'],
                                          stderr=subprocess.DEVNULL)
        digest(item['sha256'], 'CODE_SOURCE_SHA256')
        check(hashlib.sha256(content).hexdigest() == item['sha256'], 'CODE_SOURCE_MISMATCH')
        check((Path(repo) / path).read_bytes() == content, 'CODE_WORKTREE_MISMATCH')
    ref(code['dependency_lock'], 'DEPENDENCY_LOCK_REF')


def _resolve_refs(value, root):
    """All artifact triples must exist as immutable digest-named private objects."""
    if type(value) is dict:
        if set(value) == {'sha256', 'byte_length', 'media_type'}:
            ref(value, 'ARTIFACT_REF')
            object_dir = root / 'objects'
            check(object_dir.is_dir() and not object_dir.is_symlink() and
                  stat.S_IMODE(object_dir.stat().st_mode) == 0o700,
                  'OBJECT_DIRECTORY')
            fd = os.open(object_dir / value['sha256'], os.O_RDONLY | os.O_NOFOLLOW)
            try:
                st = os.fstat(fd)
                check(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and
                      stat.S_IMODE(st.st_mode) == 0o600 and st.st_nlink == 1 and
                      st.st_size == value['byte_length'],
                      'ARTIFACT_LENGTH')
                h = hashlib.sha256()
                while chunk := os.read(fd, 1024 ** 2):
                    h.update(chunk)
                check(h.hexdigest() == value['sha256'], 'ARTIFACT_DIGEST')
            finally:
                os.close(fd)
        else:
            for child in value.values():
                _resolve_refs(child, root)
    elif type(value) is list:
        for child in value:
            _resolve_refs(child, root)


def _slot_inventory(runs):
    return [[provider, runs[provider], member, hour]
            for provider, (members, cadence) in SLOTS.items()
            for member in range(members) for hour in range(0, 73, cadence)]


def _public_https_origin(value):
    if type(value) is not str:
        return False
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
            parsed.password or parsed.path not in ('', '/') or parsed.query or
            parsed.fragment or parsed.hostname.lower() in ('localhost', 'localhost.localdomain')):
        return False
    try:
        return ipaddress.ip_address(parsed.hostname).is_global
    except ValueError:
        return '.' in parsed.hostname and not parsed.hostname.endswith('.local')


def validate_manifest(raw, *, repo, object_root, now_utc):
    """Validate a private candidate offline; return its digest, never permission."""
    payload = parse_canonical(raw)
    exact(payload, GROUPS, 'MANIFEST_GROUP_SCHEMA')
    ident = payload['identity']
    exact(ident, ('schema', 'pilot_id', 'purpose', 'capture_mode', 'created_ref',
                  'financial_authority', 'promotion_authority', 'host_approved',
                  'launch_authority'), 'IDENTITY_SCHEMA')
    check(ident['schema'] == SCHEMA and ident['purpose'] == 'NONFINANCIAL_RESEARCH' and
          ident['capture_mode'] == 'BOUNDED_FEASIBILITY', 'IDENTITY_MODE')
    check(type(ident['pilot_id']) is str and re.fullmatch('[A-Za-z0-9_-]{8,80}',
          ident['pilot_id']) and 'test' not in ident['pilot_id'].lower(), 'PILOT_ID')
    check(all(ident[k] is False for k in ('financial_authority', 'promotion_authority',
          'host_approved', 'launch_authority')), 'SELF_AUTHORITY_FORBIDDEN')
    ref(ident['created_ref'], 'CREATION_REF')
    _validate_code(payload['code'], repo)

    protocol = payload['protocol']
    exact(protocol, ('original_commit_oid', 'original_tree_oid', 'original_document',
                     'addendum_commit_oid', 'addendum_tree_oid', 'addendum_document',
                     'reviews'), 'PROTOCOL_SCHEMA')
    check(type(protocol['reviews']) is list and len(protocol['reviews']) == 5,
          'PROTOCOL_REVIEWS_REQUIRED')
    for key in ('original_document', 'addendum_document'):
        ref(protocol[key], 'PROTOCOL_REF')
    review_names = set()
    report_hashes = set()
    terminal_hashes = set()
    for review in protocol['reviews']:
        exact(review, ('name', 'report', 'terminal'), 'PROTOCOL_REVIEW_SCHEMA')
        review_names.add(review['name'])
        ref(review['report'], 'PROTOCOL_REVIEW_REF')
        ref(review['terminal'], 'PROTOCOL_TERMINAL_REF')
        report_hashes.add(review['report']['sha256'])
        terminal_hashes.add(review['terminal']['sha256'])
    check(review_names == REQUIRED_REVIEW_NAMES and len(report_hashes) == 5 and
          len(terminal_hashes) == 5 and not (report_hashes & terminal_hashes),
          'PROTOCOL_REVIEW_SET')
    oid_len = 40 if payload['code']['object_format'] == 'sha1' else 64
    for key, kind in (('original_commit_oid', 'commit'), ('original_tree_oid', 'tree'),
                      ('addendum_commit_oid', 'commit'), ('addendum_tree_oid', 'tree')):
        oid = protocol[key]
        check(type(oid) is str and len(oid) == oid_len and
              _git(repo, 'cat-file', '-t', oid) == kind, 'PROTOCOL_GIT_OID')
    check(_git(repo, 'rev-parse', protocol['original_commit_oid'] + '^{tree}') ==
          protocol['original_tree_oid'] and
          _git(repo, 'rev-parse', protocol['addendum_commit_oid'] + '^{tree}') ==
          protocol['addendum_tree_oid'], 'PROTOCOL_COMMIT_TREE')
    check((protocol['original_commit_oid'], protocol['original_tree_oid'],
           protocol['original_document']['sha256']) ==
          (PINNED_ORIGINAL_COMMIT, PINNED_ORIGINAL_TREE, PINNED_ORIGINAL_DOC) and
          (protocol['addendum_commit_oid'], protocol['addendum_tree_oid'],
           protocol['addendum_document']['sha256']) ==
          (PINNED_ADDENDUM_COMMIT, PINNED_ADDENDUM_TREE, PINNED_ADDENDUM_DOC),
          'PROTOCOL_PIN_MISMATCH')

    storage = payload['storage']
    exact(storage, ('root', 'owner_uid', 'mode', 'directory_dev', 'directory_inode',
                    'layout', 'exclusive_lock', 'atomic_fsync_seal', 'report_reserve_bytes',
                    'no_reclamation'), 'STORAGE_SCHEMA')
    root = Path(storage['root'])
    check(root.is_absolute() and root.resolve() == root and root == Path(object_root).resolve(),
          'PRIVATE_ROOT_IDENTITY')
    check(not root.is_symlink() and not root.is_relative_to(Path(repo).resolve()),
          'STORAGE_PATH_FORBIDDEN')
    protected_inputs = Path('/home/alphaadmin/AlphaV11_Private/Alpha_V11_Codex_Private_Inputs')
    check(not any(part.lower() in ('.git', 'v10', 'axiomtrade', 'credentials')
                  for part in root.parts)
          and not root.is_relative_to(Path('/etc/alpha-v11'))
          and not root.is_relative_to(Path('/var/lib/alpha-v11'))
          and not root.is_relative_to(protected_inputs),
          'STORAGE_PROTECTED_PATH')
    st = root.stat()
    check(stat.S_ISDIR(st.st_mode) and st.st_uid == storage['owner_uid'] and
          stat.S_IMODE(st.st_mode) == storage['mode'] and
          (st.st_dev, st.st_ino) == (storage['directory_dev'], storage['directory_inode']),
          'STORAGE_DIRECTORY_IDENTITY')
    check(storage['mode'] == 0o700 and storage['layout'] == 'OBJECTS_AND_APPEND_ONLY_LEDGER' and
          storage['exclusive_lock'] is True and storage['atomic_fsync_seal'] is True and
          storage['no_reclamation'] is True, 'STORAGE_POLICY')
    integer(storage['report_reserve_bytes'], 1, MAX_BYTES, 'REPORT_RESERVE')

    cohort = payload['cohort']
    exact(cohort, ('station_id', 'station_version', 'latitude', 'longitude', 'timezone',
                   'tzdata', 'target_date', 'events', 'units', 'rounding', 'buckets',
                   'rule', 'settlement', 'metadata',
                   'selection', 'requested_keys', 'gate2_trial_keys', 'city_day'),
          'COHORT_SCHEMA')
    check(type(cohort['station_id']) is str and cohort['station_id'] and
          type(cohort['station_version']) is str and cohort['station_version'], 'STATION_ID')
    for key, lo, hi in (('latitude', -90, 90), ('longitude', -180, 180)):
        check(type(cohort[key]) in (int, float) and lo <= cohort[key] <= hi,
              'COORDINATES')
    try:
        tz = ZoneInfo(cohort['timezone'])
        target = date.fromisoformat(cohort['target_date'])
    except (KeyError, ValueError, TypeError) as exc:
        raise LaunchContractError('COHORT_TIMEZONE_DATE') from exc
    check(target.isoformat() == cohort['target_date'], 'TARGET_DATE_FORMAT')
    for key in ('tzdata', 'rule', 'settlement', 'metadata', 'selection', 'buckets'):
        ref(cohort[key], 'COHORT_ARTIFACT_REF')
    check(cohort['units'] == 'CELSIUS' and type(cohort['rounding']) is str and
          cohort['rounding'], 'COHORT_UNITS_ROUNDING')
    check(type(cohort['events']) is list and 1 <= len(cohort['events']) <= 2 and
          set(cohort['events']) <= {'HIGH', 'LOW'} and
          len(set(cohort['events'])) == len(cohort['events']), 'COHORT_EVENTS')
    check(type(cohort['requested_keys']) is list and len(cohort['requested_keys']) ==
          len(cohort['events']) and type(cohort['gate2_trial_keys']) is list and
          len(cohort['gate2_trial_keys']) == len(cohort['events']) and
          cohort['city_day'] == [cohort['station_id'], cohort['target_date']],
          'COHORT_KEY_MAPPING')
    for event, key, trial in zip(cohort['events'], cohort['requested_keys'],
                                 cohort['gate2_trial_keys']):
        family = 'daily_high_temperature' if event == 'HIGH' else 'daily_low_temperature'
        check(type(key) is list and len(key) == 5 and
              key[0] == cohort['station_id'] and key[3] == cohort['target_date'] and
              key[4] == family and all(type(x) is str and x for x in key) and
              trial == [key[0], key[1], key[3]], 'COHORT_TRIAL_MAPPING')

    times = payload['time']
    exact(times, ('preregistered_utc', 'review_completed_utc', 'window_start_utc',
                  'last_acquisition_utc', 'decision_utc', 'feature_seal_upper_utc',
                  'decision_lower_utc', 'expires_utc', 'local_day_start_utc',
                  'local_day_end_utc', 'uncertainty_seconds'), 'TIME_SCHEMA')
    def stamp(key):
        value = times[key]
        check(type(value) is int and value > 0, 'TIME_UTC_INTEGER')
        return datetime.fromtimestamp(value, timezone.utc)
    start = stamp('window_start_utc')
    end = stamp('last_acquisition_utc')
    decision = stamp('decision_utc')
    day_start = datetime.combine(target, datetime.min.time(), tz).astimezone(timezone.utc)
    day_end = (datetime.combine(target, datetime.min.time(), tz) +
               timedelta(days=1)).astimezone(timezone.utc)
    check(start == datetime.combine((target - timedelta(days=1)),
          datetime.min.time(), timezone.utc) + timedelta(hours=14) and
          end == start + timedelta(hours=3) and decision == start + timedelta(hours=4),
          'PILOT_WINDOW_FIXED')
    check(stamp('local_day_start_utc') == day_start and
          stamp('local_day_end_utc') == day_end and decision < day_start,
          'LOCAL_DAY_BOUNDARY')
    check(stamp('preregistered_utc') <= stamp('review_completed_utc') < start and
          stamp('feature_seal_upper_utc') <= stamp('decision_lower_utc') <= decision and
          end <= stamp('feature_seal_upper_utc') and
          stamp('expires_utc') >= end and stamp('expires_utc') > datetime.fromtimestamp(now_utc, timezone.utc),
          'CAUSAL_TIME_ORDER')
    check(type(times['uncertainty_seconds']) in (int, float) and
          0 <= times['uncertainty_seconds'] <= 1, 'CLOCK_UNCERTAINTY')

    sources = payload['sources']
    exact(sources, SLOTS, 'SOURCE_SET')
    for provider, source in sources.items():
        exact(source, ('dossier', 'release_document', 'licence', 'index_evidence',
                       'range_evidence', 'decoder_build', 'origin', 'path_template',
                       'publication_attestation', 'publication_absence_reason',
                       'member_range', 'native_hours', 'identity_pins',
                       'effective_run_start_utc', 'effective_run_end_utc'),
              'SOURCE_SCHEMA')
        for key in ('dossier', 'release_document', 'licence', 'index_evidence',
                    'range_evidence', 'decoder_build', 'identity_pins'):
            ref(source[key], 'SOURCE_REF')
        members, cadence = SLOTS[provider]
        check(source['member_range'] == [0, members - 1] and
              source['native_hours'] == list(range(0, 73, cadence)),
              'SOURCE_NATIVE_COHORT')
        check(type(source['effective_run_start_utc']) is int and
              type(source['effective_run_end_utc']) is int and
              source['effective_run_start_utc'] <= source['effective_run_end_utc'],
              'SOURCE_EFFECTIVE_INTERVAL')
        check(_public_https_origin(source['origin']) and
              type(source['path_template']) is str and
              '..' not in source['path_template'], 'SOURCE_ORIGIN')
        if source['publication_attestation'] is None:
            check(type(source['publication_absence_reason']) is str and
                  bool(source['publication_absence_reason']), 'PUBLICATION_ABSENCE_REASON')
        else:
            ref(source['publication_attestation'], 'PUBLICATION_REF')
    runs = payload['runs_and_slots']
    exact(runs, ('allowed_cycles', 'max_run_age_seconds', 'run_selection',
                 'fallback_mode', 'run_utc', 'slots'), 'RUN_SCHEMA')
    check(runs['allowed_cycles'] == [0] and runs['max_run_age_seconds'] == 86400 and
          runs['run_selection'] == 'LATEST_COMPLETE_READY' and
          runs['fallback_mode'] == 'NONE', 'RUN_POLICY')
    exact(runs['run_utc'], SLOTS, 'RUN_PROVIDER_SET')
    for provider, run in runs['run_utc'].items():
        check(type(run) is int and datetime.fromtimestamp(run, timezone.utc).hour == 0 and
              0 < decision.timestamp() - run <= 86400 and
              sources[provider]['effective_run_start_utc'] <= run <=
              sources[provider]['effective_run_end_utc'], 'RUN_TIME')
    expected = _slot_inventory(runs['run_utc'])
    check(type(runs['slots']) is list and runs['slots'] == expected and
          len(expected) == SLOT_COUNT, 'IMMUTABLE_2713_SLOT_DENOMINATOR')

    network = payload['network']
    exact(network, ('origins', 'methods', 'path_templates', 'purposes', 'index_binding',
                    'anonymous', 'redirects', 'cookies', 'netrc', 'ambient_proxies',
                    'signed_urls', 'retries', 'dns_tls_policy', 'restriction_lineage',
                    'post_window_labels'), 'NETWORK_SCHEMA')
    check(network['origins'] == [sources[p]['origin'] for p in SLOTS] and
          network['methods'] == ['GET'] and network['anonymous'] is True and
          all(network[k] is False for k in ('redirects', 'cookies', 'netrc',
          'ambient_proxies', 'signed_urls', 'retries', 'post_window_labels')),
          'NETWORK_POLICY')
    check(network['path_templates'] == {p: sources[p]['path_template'] for p in SLOTS} and
          network['purposes'] == ['FIELD', 'INDEX', 'OBJECT_ID', 'METADATA', 'PROBE'] and
          network['index_binding'] == 'ETAG_IF_RANGE', 'NETWORK_REQUEST_SHAPE')
    for key in ('dns_tls_policy', 'restriction_lineage'):
        ref(network[key], 'NETWORK_REF')

    limits = payload['limits']
    exact(limits, ('max_requests', 'max_received_bytes', 'max_elapsed_seconds',
                   'single_in_flight', 'min_start_interval_seconds', 'request_deadline_seconds',
                   'max_index_bytes', 'field_bytes', 'min_free_disk_bytes',
                   'min_available_memory_bytes', 'headers', 'metadata', 'decoded',
                   'report_storage'), 'LIMIT_SCHEMA')
    integer(limits['max_requests'], 1, 3600, 'REQUEST_CAP')
    integer(limits['max_received_bytes'], 1, MAX_BYTES, 'BODY_CAP')
    integer(limits['max_elapsed_seconds'], 1, 10800, 'ELAPSED_CAP')
    check(limits['single_in_flight'] is True and
          limits['min_start_interval_seconds'] >= 2 and
          0 < limits['request_deadline_seconds'] <= 30, 'REQUEST_PACING')
    integer(limits['max_index_bytes'], 1, 3145728, 'INDEX_CAP')
    exact(limits['field_bytes'], SLOTS, 'FIELD_LIMIT_SET')
    for provider, bound in limits['field_bytes'].items():
        integer(bound, 1, FIELD_LIMITS[provider], 'FIELD_CAP')
    check(limits['min_free_disk_bytes'] >= 2 * 1024 ** 3 and
          limits['min_available_memory_bytes'] >= 512 * 1024 ** 2,
          'RESOURCE_MINIMUM')
    for key in ('headers', 'metadata', 'decoded', 'report_storage'):
        integer(limits[key], 1, MAX_BYTES, 'RESOURCE_BOUND')

    schedule = payload['schedule']
    exact(schedule, ('slot_inventory_sha256', 'attempt_slots', 'requests',
                     'observed_sizes', 'estimated_full_raw_bytes', 'reservation_total_bytes',
                     'full_denominator', 'capture_mode'), 'SCHEDULE_SCHEMA')
    digest(schedule['slot_inventory_sha256'], 'SLOT_DIGEST')
    check(hashlib.sha256(canonical(expected)).hexdigest() ==
          schedule['slot_inventory_sha256'], 'SLOT_DIGEST_MISMATCH')
    check(schedule['full_denominator'] == SLOT_COUNT and
          schedule['capture_mode'] == ident['capture_mode'], 'SCHEDULE_DENOMINATOR')
    check(type(schedule['attempt_slots']) is list and
          bool(schedule['attempt_slots']) and
          all(type(i) is int and 0 <= i < SLOT_COUNT for i in schedule['attempt_slots']) and
          len(set(schedule['attempt_slots'])) == len(schedule['attempt_slots']),
          'ATTEMPT_SUBSET')
    check(type(schedule['requests']) is list and 0 < len(schedule['requests']) <=
          limits['max_requests'], 'REQUEST_SCHEDULE')
    total = 0
    field_slots = []
    prior_overhead = {}
    field_started = False
    for position, request in enumerate(schedule['requests']):
        exact(request, ('purpose', 'slot_index', 'reservation_bytes', 'prerequisites'),
              'SCHEDULE_REQUEST_SCHEMA')
        check(request['purpose'] in ('FIELD', 'INDEX', 'OBJECT_ID', 'METADATA', 'PROBE') and
              type(request['prerequisites']) is list, 'SCHEDULE_PURPOSE')
        if request['purpose'] == 'FIELD':
            field_started = True
            check(request['slot_index'] in schedule['attempt_slots'], 'FIELD_OUTSIDE_SUBSET')
            field_slots.append(request['slot_index'])
            check(all(type(i) is int and i in prior_overhead for i in
                      request['prerequisites']) and
                  {'INDEX', 'OBJECT_ID', 'METADATA'} <=
                  {prior_overhead[i] for i in request['prerequisites']},
                  'FIELD_PREREQUISITES')
            provider = expected[request['slot_index']][0]
            minimum = limits['field_bytes'][provider]
        else:
            check(not field_started, 'OVERHEAD_MUST_PRECEDE_FIELD')
            check(request['slot_index'] is None, 'NONFIELD_SLOT')
            minimum = limits['max_index_bytes'] if request['purpose'] == 'INDEX' else limits['metadata']
            prior_overhead[position] = request['purpose']
        integer(request['reservation_bytes'], minimum, MAX_BYTES, 'RESERVATION_TOO_SMALL')
        total += request['reservation_bytes']
    check(total == schedule['reservation_total_bytes'] and
          total <= limits['max_received_bytes'] and
          schedule['estimated_full_raw_bytes'] >= 1469234173,
          'SCHEDULE_FEASIBILITY')
    check(field_slots == schedule['attempt_slots'] and
          len(field_slots) == len(set(field_slots)), 'ATTEMPT_ORDER_PARTITION')
    ref(schedule['observed_sizes'], 'OBSERVED_SIZE_REF')
    check(len(schedule['requests']) * limits['request_deadline_seconds'] <=
          limits['max_elapsed_seconds'], 'SCHEDULE_TIME_FEASIBILITY')

    clocks = payload['clocks_and_receipts']
    exact(clocks, ('method', 'host_boot', 'sync_evidence', 'max_measurement_age_seconds',
                   'request_start', 'body_receipt', 'decode_complete', 'durable_seal'),
          'CLOCK_SCHEMA')
    for key in ('method', 'host_boot', 'sync_evidence', 'request_start',
                'body_receipt', 'decode_complete', 'durable_seal'):
        ref(clocks[key], 'CLOCK_EVIDENCE_REF')
    integer(clocks['max_measurement_age_seconds'], 1, 3600, 'CLOCK_EVIDENCE_AGE')
    accounting = payload['accounting']
    exact(accounting, ('terminal_precedence', 'all_reasons', 'journal_hash_chain',
                       'no_silent_retry', 'expired_local_only', 'raw_partition',
                       'provider_eligibility', 'all_provider_intersection',
                       'diagnostics_no_credit'), 'ACCOUNTING_SCHEMA')
    ref(accounting['terminal_precedence'], 'TERMINAL_PRECEDENCE_REF')
    check(all(accounting[k] is True for k in ('all_reasons', 'journal_hash_chain',
          'no_silent_retry', 'expired_local_only', 'raw_partition',
          'provider_eligibility', 'all_provider_intersection', 'diagnostics_no_credit')),
          'ACCOUNTING_POLICY')
    _resolve_refs(payload, root)
    return hashlib.sha256(raw).hexdigest()


class DurableBudget:
    """Exclusive, append-only hash journal. Unfinished reservations stay charged.

    ``next_read_limit`` is the maximum bytes a future transport may ask for.
    ``consume`` journals every returned chunk before the caller can use it.
    The journal never grants network permission and does not open a socket.
    """
    def __init__(self, directory, manifest_sha256, *, max_requests=3600,
                 max_bytes=MAX_BYTES, max_elapsed_seconds=10800,
                 min_start_interval_seconds=2, boot_id='synthetic-boot'):
        digest(manifest_sha256, 'BUDGET_MANIFEST_DIGEST')
        integer(max_requests, 1, 3600, 'BUDGET_REQUEST_LIMIT')
        integer(max_bytes, 1, MAX_BYTES, 'BUDGET_BYTE_LIMIT')
        integer(max_elapsed_seconds, 1, 10800, 'BUDGET_ELAPSED_LIMIT')
        check(type(min_start_interval_seconds) in (int, float) and
              math.isfinite(min_start_interval_seconds) and
              min_start_interval_seconds >= 2, 'BUDGET_START_INTERVAL')
        check(type(boot_id) is str and boot_id, 'BUDGET_BOOT_ID')
        path = Path(directory)
        check(not path.is_symlink() and path.is_dir(), 'JOURNAL_DIRECTORY')
        self.dir_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        directory_stat = os.fstat(self.dir_fd)
        if directory_stat.st_uid != os.getuid() or stat.S_IMODE(directory_stat.st_mode) != 0o700:
            os.close(self.dir_fd)
            raise LaunchContractError('JOURNAL_DIRECTORY_PRIVATE_MODE')
        self.lock_fd = os.open('gate3.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                               0o600, dir_fd=self.dir_fd)
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self.lock_fd)
            os.close(self.dir_fd)
            raise LaunchContractError('JOURNAL_CONCURRENT_WRITER') from exc
        self.fd = os.open('gate3.jsonl', os.O_CREAT | os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW,
                          0o600, dir_fd=self.dir_fd)
        self.manifest = manifest_sha256
        self.max_requests = max_requests
        self.max_bytes = max_bytes
        self.max_elapsed_seconds = max_elapsed_seconds
        self.min_start_interval_seconds = min_start_interval_seconds
        self.boot_id = boot_id
        self.events = []
        self.prev = '0' * 64
        try:
            self._replay()
            if not self.events:
                self._append({'op': 'init', 'manifest': manifest_sha256,
                              'max_requests': max_requests, 'max_bytes': max_bytes,
                              'max_elapsed_seconds': max_elapsed_seconds,
                              'min_start_interval_seconds': min_start_interval_seconds,
                              'boot_id': boot_id})
            else:
                init = self.events[0]
                check(init == {'op': 'init', 'manifest': manifest_sha256,
                               'max_requests': max_requests, 'max_bytes': max_bytes,
                               'max_elapsed_seconds': max_elapsed_seconds,
                               'min_start_interval_seconds': min_start_interval_seconds,
                               'boot_id': boot_id}, 'JOURNAL_IDENTITY_MISMATCH')
            self._state()
        except BaseException:
            self.close()
            raise

    def _replay(self):
        with os.fdopen(os.dup(self.fd), 'rb') as reader:
            reader.seek(0)
            for line in reader:
                check(line.endswith(b'\n'), 'JOURNAL_TORN_RECORD')
                record = parse_canonical(line[:-1])
                exact(record, ('seq', 'prev', 'event', 'hash'), 'JOURNAL_RECORD_SCHEMA')
                check(record['seq'] == len(self.events) and record['prev'] == self.prev,
                      'JOURNAL_SEQUENCE')
                expected = hashlib.sha256(canonical({k: record[k] for k in
                    ('seq', 'prev', 'event')})).hexdigest()
                check(record['hash'] == expected, 'JOURNAL_HASH')
                self.prev = expected
                self.events.append(record['event'])

    def _append(self, event):
        record = {'seq': len(self.events), 'prev': self.prev, 'event': event}
        record['hash'] = hashlib.sha256(canonical(record)).hexdigest()
        data = canonical(record) + b'\n'
        written = os.write(self.fd, data)
        check(written == len(data), 'JOURNAL_SHORT_WRITE')
        os.fsync(self.fd)
        self.prev = record['hash']
        self.events.append(event)

    def _state(self):
        self.attempts = {}
        self.count = 0
        self.received = 0
        self.reserved = 0
        self.in_flight = None
        self.violated = False
        self.window_started = None
        self.last_started = None
        for event in self.events[1:]:
            op = event['op']
            key = event.get('key')
            if op == 'reserve':
                check(key not in self.attempts and self.in_flight is None,
                      'JOURNAL_DUPLICATE_OR_OVERLAP')
                self.attempts[key] = {'reserved': event['bytes'], 'received': 0,
                                      'finished': False}
                self.count += 1
                self.reserved += event['bytes']
                self.in_flight = key
                if self.window_started is None:
                    self.window_started = event['started_monotonic']
                self.last_started = event['started_monotonic']
            elif op == 'chunk':
                check(self.in_flight == key, 'JOURNAL_CHUNK_WITHOUT_RESERVATION')
                self.attempts[key]['received'] += event['bytes']
                self.received += event['bytes']
            elif op == 'complete':
                check(self.in_flight == key, 'JOURNAL_COMPLETE_WITHOUT_RESERVATION')
                attempt = self.attempts[key]
                check(attempt['received'] <= attempt['reserved'], 'JOURNAL_OVER_RESERVATION')
                self.reserved -= attempt['reserved'] - attempt['received']
                attempt['finished'] = True
                self.in_flight = None
            elif op == 'violation':
                check(self.in_flight == key and not self.violated,
                      'JOURNAL_DUPLICATE_VIOLATION')
                self.received += event['bytes']
                self.violated = True
            else:
                raise LaunchContractError('JOURNAL_UNKNOWN_EVENT')
        check(self.count <= self.max_requests and self.reserved <= self.max_bytes and
              (self.received <= self.max_bytes or self.violated), 'JOURNAL_BUDGET_EXCEEDED')

    def reserve(self, key, bytes_required, *, started_monotonic):
        check(type(key) is str and key and key not in self.attempts,
              'REQUEST_KEY_REUSE')
        integer(bytes_required, 1, self.max_bytes, 'REQUEST_RESERVATION')
        check(type(started_monotonic) in (int, float) and
              math.isfinite(started_monotonic) and started_monotonic >= 0,
              'REQUEST_MONOTONIC_TIME')
        check(not self.violated, 'STREAM_VIOLATION_HELD')
        check(self.in_flight is None, 'UNCERTAIN_REQUEST_HELD')
        check(self.window_started is None or
              started_monotonic - self.window_started < self.max_elapsed_seconds,
              'NOT_ATTEMPTED_BUDGET')
        check(self.last_started is None or
              started_monotonic - self.last_started >= self.min_start_interval_seconds,
              'NOT_ATTEMPTED_BUDGET')
        check(self.count < self.max_requests and
              self.reserved + bytes_required <= self.max_bytes,
              'NOT_ATTEMPTED_BUDGET')
        self._append({'op': 'reserve', 'key': key, 'bytes': bytes_required,
                      'started_monotonic': started_monotonic})
        self._state()

    def next_read_limit(self, maximum_chunk):
        check(self.in_flight is not None and not self.violated, 'NO_ACTIVE_REQUEST')
        integer(maximum_chunk, 1, MAX_BYTES, 'READ_CHUNK_BOUND')
        attempt = self.attempts[self.in_flight]
        return min(maximum_chunk, attempt['reserved'] - attempt['received'],
                   self.max_bytes - self.received)

    def consume(self, key, body):
        check(self.in_flight == key and type(body) is bytes, 'UNEXPECTED_BODY_CHUNK')
        allowance = self.next_read_limit(max(len(body), 1))
        if len(body) > allowance:
            # Bytes already delivered by a faulty transport still count, even
            # though the caller must abort and can never resume this request.
            self._append({'op': 'violation', 'key': key, 'bytes': len(body)})
            self._state()
            raise LaunchContractError('STREAM_ABORT_AT_ALLOWANCE')
        self._append({'op': 'chunk', 'key': key, 'bytes': len(body)})
        self._state()

    def complete(self, key):
        check(self.in_flight == key and not self.violated, 'NO_ACTIVE_REQUEST')
        self._append({'op': 'complete', 'key': key})
        self._state()

    def close(self):
        os.close(self.fd)
        os.close(self.lock_fd)
        os.close(self.dir_fd)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
