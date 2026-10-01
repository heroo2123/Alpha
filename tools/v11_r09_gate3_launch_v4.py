"""Offline Gate 3 V4 launch-contract schema: endpoint table, frozen purpose
plan, and the new runtime group from the reviewed transport/runtime design
(docs/V11_R09_GATE3_TRANSPORT_RUNTIME_DESIGN.md, section 1 and section 7
slice (1)).

This module has no transport, decoder, credential or launch entrypoint,
exactly like the V3 validator it extends. A valid V4 payload is only a
candidate for a future, separately reviewed, exact-digest G3-L review. It
does not loosen, silently translate, or migrate V3; V3's own schema and
`validate_manifest` are untouched and remain independently valid in their
own offline scope. This file duplicates the retained-group checks from V3
deliberately, rather than importing or calling V3's monolithic validator,
so that a V4 change can never silently alter V3's already-reviewed behavior.
"""
from __future__ import annotations

import hashlib
import re
import stat as stat_module
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from tools.v11_multimodel_panel import canonical
from tools.v11_r09_gate3_launch import (
    FIELD_LIMITS, JOURNAL_MAX_BYTES, JOURNAL_MAX_EVENTS,
    JOURNAL_RECORD_MAX_BYTES, MAX_BYTES, PINNED_ADDENDUM_COMMIT,
    PINNED_ADDENDUM_DOC, PINNED_ADDENDUM_TREE, PINNED_ORIGINAL_COMMIT,
    PINNED_ORIGINAL_DOC, PINNED_ORIGINAL_TREE, REQUIRED_REVIEW_NAMES,
    SLOT_COUNT, SLOTS, LaunchContractError, _canonical_request_path,
    _git, _public_https_origin, _resolve_refs, _slot_inventory,
    _validate_code, _zone_from_ref, check, digest, exact, integer,
    parse_canonical, ref,
)

SCHEMA = 'R09_GATE3_LAUNCH_MANIFEST_V4'
PINNED_DESIGN_COMMIT = '7e132a02ae0fcceed5e3fea65f7b86bed4a92c23'
PINNED_DESIGN_TREE = '89332f46dfc6b2111fe331fca54467cce96a3895'
PINNED_DESIGN_DOC = '0b121fbc422208e2fa89e0b2f7362725cac60115ada966ed83b9d1ed15ac8e4a'
PINNED_DESIGN_REVIEW_REPORT = 'f91c848b8626bae718d1c07c2a50194ae303e36f16fb2bc8a776b75cca0b942b'
PINNED_DESIGN_REVIEW_TERMINAL = '3c583f854ab7f8eff7b36d83a2571b9aa80181664391d2469e12e30f10a0349c'
REPORT_RESERVE_BYTES = 16 * 1024 ** 2
METADATA_MAX_BYTES = 4 * 1024 ** 2
SESSION_JOURNAL_MAX_EVENTS = 32768
STORE_MAX_OBJECTS = 4096
STORE_MAX_EVENTS = 10000
STORE_OBJECT_MAX_BYTES = 4 * 1024 ** 2
PURPOSES = ('FIELD', 'INDEX', 'OBJECT_ID', 'METADATA', 'PROBE')
OBSERVATION_PHASES = ('request_start', 'body_receipt', 'decode_complete', 'durable_seal')
GROUPS = ('identity', 'code', 'protocol', 'storage', 'cohort', 'time',
          'sources', 'runs_and_slots', 'network', 'limits', 'schedule',
          'clocks_and_receipts', 'accounting', 'runtime')


def validate_manifest_v4(raw, *, repo, object_root, now_utc):
    """Validate a private V4 candidate offline; return its digest, never permission.

    Retains every V3 cohort/source/code/review/clock/safety/ceiling check
    for the groups V4 does not change (identity, code, protocol, storage,
    cohort, time, runs_and_slots, limits, accounting). Changes only
    sources/network (ordered endpoint table), schedule (endpoint binding),
    clocks_and_receipts (preregistration vs. per-attempt observation
    schema), and adds the new closed `runtime` group with the frozen
    per-purpose request/byte plan.
    """
    payload = parse_canonical(raw)
    exact(payload, GROUPS, 'MANIFEST_GROUP_SCHEMA')
    ident = payload['identity']
    exact(ident, ('schema', 'pilot_id', 'purpose', 'capture_mode', 'created_ref',
                  'financial_authority', 'promotion_authority', 'host_approved',
                  'launch_authority'), 'IDENTITY_SCHEMA')
    check(ident['schema'] == SCHEMA and ident['purpose'] == 'NONFINANCIAL_RESEARCH' and
          ident['capture_mode'] == 'BOUNDED_FEASIBILITY', 'IDENTITY_MODE')
    check(type(ident['pilot_id']) is str and
          re.fullmatch('[A-Za-z0-9_-]{8,80}', ident['pilot_id']) and
          'test' not in ident['pilot_id'].lower(), 'PILOT_ID')
    check(all(ident[k] is False for k in ('financial_authority', 'promotion_authority',
          'host_approved', 'launch_authority')), 'SELF_AUTHORITY_FORBIDDEN')
    ref(ident['created_ref'], 'CREATION_REF')
    _validate_code(payload['code'], repo)

    protocol = payload['protocol']
    exact(protocol, ('original_commit_oid', 'original_tree_oid', 'original_document',
                     'addendum_commit_oid', 'addendum_tree_oid', 'addendum_document',
                     'reviews', 'reviewed_design'), 'PROTOCOL_SCHEMA')
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
    design = protocol['reviewed_design']
    exact(design, ('commit_oid', 'tree_oid', 'document', 'report', 'terminal'),
          'DESIGN_REVIEW_SCHEMA')
    for key in ('document', 'report', 'terminal'):
        ref(design[key], 'DESIGN_REVIEW_REF')
    check((design['commit_oid'], design['tree_oid'],
           design['document']['sha256'], design['report']['sha256'],
           design['terminal']['sha256']) ==
          (PINNED_DESIGN_COMMIT, PINNED_DESIGN_TREE, PINNED_DESIGN_DOC,
           PINNED_DESIGN_REVIEW_REPORT, PINNED_DESIGN_REVIEW_TERMINAL) and
          _git(repo, 'cat-file', '-t', design['commit_oid']) == 'commit' and
          _git(repo, 'rev-parse', design['commit_oid'] + '^{tree}') ==
          design['tree_oid'] and
          not ({design[k]['sha256'] for k in ('report', 'terminal')} &
               (report_hashes | terminal_hashes)) and
          design['report']['sha256'] != design['terminal']['sha256'],
          'DESIGN_REVIEW_PIN_MISMATCH')

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
    check(stat_module.S_ISDIR(st.st_mode) and st.st_uid == storage['owner_uid'] and
          stat_module.S_IMODE(st.st_mode) == storage['mode'] and
          (st.st_dev, st.st_ino) == (storage['directory_dev'], storage['directory_inode']),
          'STORAGE_DIRECTORY_IDENTITY')
    check(storage['mode'] == 0o700 and storage['layout'] == 'OBJECTS_AND_APPEND_ONLY_LEDGER' and
          storage['exclusive_lock'] is True and storage['atomic_fsync_seal'] is True and
          storage['no_reclamation'] is True, 'STORAGE_POLICY')
    integer(storage['report_reserve_bytes'], REPORT_RESERVE_BYTES, MAX_BYTES, 'REPORT_RESERVE')

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
        target = date.fromisoformat(cohort['target_date'])
    except (KeyError, ValueError, TypeError) as exc:
        raise LaunchContractError('COHORT_TIMEZONE_DATE') from exc
    tz = _zone_from_ref(cohort, root)
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
                       'effective_run_start_utc', 'effective_run_end_utc',
                       'control_domain', 'purpose_mappings'),
              'SOURCE_SCHEMA')
        for key in ('dossier', 'release_document', 'licence', 'index_evidence',
                    'range_evidence', 'decoder_build', 'identity_pins'):
            ref(source[key], 'SOURCE_REF')
        members, cadence = SLOTS[provider]
        check(type(source['member_range']) is list and
              all(type(x) is int for x in source['member_range']) and
              source['member_range'] == [0, members - 1] and
              type(source['native_hours']) is list and
              all(type(x) is int for x in source['native_hours']) and
              source['native_hours'] == list(range(0, 73, cadence)),
              'SOURCE_NATIVE_COHORT')
        check(type(source['effective_run_start_utc']) is int and
              type(source['effective_run_end_utc']) is int and
              source['effective_run_start_utc'] <= source['effective_run_end_utc'],
              'SOURCE_EFFECTIVE_INTERVAL')
        ref(source['control_domain'], 'CONTROL_DOMAIN_REF')
        mappings = source['purpose_mappings']
        exact(mappings, PURPOSES, 'SOURCE_PURPOSE_MAPPINGS')
        for purpose, mapping in mappings.items():
            exact(mapping, ('origin', 'path_template', 'control_domain_id',
                            'mapping_evidence', 'validator', 'response_contract'), 'SOURCE_MAPPING_SCHEMA')
            for key in ('mapping_evidence', 'validator', 'response_contract'):
                ref(mapping[key], 'SOURCE_MAPPING_REF')
            check(mapping['control_domain_id'] == source['control_domain']['sha256'],
                  'CONTROL_DOMAIN_BINDING')
            mapping_template = mapping['path_template']
            check(_public_https_origin(mapping['origin']) and
                  type(mapping_template) is str and
                  _canonical_request_path(mapping['origin'], mapping_template) and
                  all(mapping_template.count(token) <= 1 for token in
                      ('{run}', '{member}', '{hour}')) and
                  '{' not in mapping_template.replace('{run}', '').replace(
                      '{member}', '').replace('{hour}', '') and
                  '}' not in mapping_template.replace('{run}', '').replace(
                      '{member}', '').replace('{hour}', '') and
                  re.fullmatch(r'[A-Za-z0-9_/{}/.-]+', mapping_template) is not None,
                  'SOURCE_MAPPING_PATH')
        check(mappings['FIELD']['origin'] == source['origin'] and
              mappings['FIELD']['path_template'] == source['path_template'],
              'FIELD_SOURCE_MAPPING')
        template = source['path_template']
        check(_public_https_origin(source['origin']) and
              type(source['path_template']) is str and
              _canonical_request_path(source['origin'], template) and
              template.count('{run}') == 1 and
              template.count('{member}') == 1 and
              template.count('{hour}') == 1 and
              '..' not in template and
              '{' not in template.replace('{run}', '').replace('{member}', '').replace('{hour}', '') and
              '}' not in template.replace('{run}', '').replace('{member}', '').replace('{hour}', '') and
              re.fullmatch(r'[A-Za-z0-9_/{}/.-]+', template) is not None,
              'SOURCE_ORIGIN')
        if source['publication_attestation'] is None:
            check(type(source['publication_absence_reason']) is str and
                  bool(source['publication_absence_reason']), 'PUBLICATION_ABSENCE_REASON')
        else:
            ref(source['publication_attestation'], 'PUBLICATION_REF')

    runs = payload['runs_and_slots']
    exact(runs, ('allowed_cycles', 'max_run_age_seconds', 'run_selection',
                 'fallback_mode', 'run_utc', 'candidates', 'slots'), 'RUN_SCHEMA')
    check(type(runs['allowed_cycles']) is list and
          len(runs['allowed_cycles']) == 1 and type(runs['allowed_cycles'][0]) is int and
          runs['allowed_cycles'][0] == 0 and type(runs['max_run_age_seconds']) is int and
          runs['max_run_age_seconds'] == 86400 and
          runs['run_selection'] == 'LATEST_COMPLETE_READY' and
          runs['fallback_mode'] == 'NONE', 'RUN_POLICY')
    exact(runs['run_utc'], SLOTS, 'RUN_PROVIDER_SET')
    lower = stamp('decision_lower_utc').timestamp()
    first_day = datetime.fromtimestamp(lower - 86400, timezone.utc).date()
    last_day = datetime.fromtimestamp(lower, timezone.utc).date()
    candidates = []
    day = first_day
    while day <= last_day:
        cycle = int(datetime.combine(day, datetime.min.time(), timezone.utc).timestamp())
        if lower - 86400 <= cycle <= lower:
            candidates.append(cycle)
        day += timedelta(days=1)
    check(type(runs['candidates']) is list and
          len(runs['candidates']) == len(candidates) * len(SLOTS), 'RUN_CANDIDATES')
    by_provider = {p: [] for p in SLOTS}
    for item in runs['candidates']:
        exact(item, ('provider', 'run_utc', 'status', 'ready_upper_utc'),
              'RUN_CANDIDATE_SCHEMA')
        p, cycle = item['provider'], item['run_utc']
        check(type(p) is str and p in SLOTS and type(cycle) is int and
              cycle in candidates and item['status'] in ('READY', 'INCOMPLETE', 'UNAVAILABLE') and
              type(item['ready_upper_utc']) is int and
              item['ready_upper_utc'] > cycle,
              'RUN_CANDIDATE_VALUE')
        by_provider[p].append(item)
    for provider, run in runs['run_utc'].items():
        inventory = by_provider[provider]
        check(len(inventory) == len(candidates) and
              {item['run_utc'] for item in inventory} == set(candidates),
              'RUN_CANDIDATES')
        eligible = [item['run_utc'] for item in inventory if
                    item['status'] == 'READY' and item['ready_upper_utc'] <= lower]
        check(type(run) is int and run in candidates and eligible and
              run == max(eligible) and
              sources[provider]['effective_run_start_utc'] <= run <=
              sources[provider]['effective_run_end_utc'], 'RUN_TIME')
    expected = _slot_inventory(runs['run_utc'])
    check(type(runs['slots']) is list and
          all(type(slot) is list and len(slot) == 4 and
              type(slot[0]) is str and all(type(x) is int for x in slot[1:])
              for slot in runs['slots']) and runs['slots'] == expected and
          len(expected) == SLOT_COUNT, 'IMMUTABLE_2713_SLOT_DENOMINATOR')

    network = payload['network']
    exact(network, ('origins', 'methods', 'path_templates', 'purposes', 'index_binding',
                    'anonymous', 'redirects', 'cookies', 'netrc', 'ambient_proxies',
                    'signed_urls', 'retries', 'dns_tls_policy', 'restriction_lineage',
                    'post_window_labels', 'endpoints'), 'NETWORK_SCHEMA')
    check(network['methods'] == ['GET'] and network['anonymous'] is True and
          all(network[k] is False for k in ('redirects', 'cookies', 'netrc',
          'ambient_proxies', 'signed_urls', 'retries', 'post_window_labels')),
          'NETWORK_POLICY')
    check(network['path_templates'] == {p: sources[p]['path_template'] for p in SLOTS} and
          network['purposes'] == list(PURPOSES) and
          network['index_binding'] == 'ETAG_IF_RANGE', 'NETWORK_REQUEST_SHAPE')
    for key in ('dns_tls_policy', 'restriction_lineage'):
        ref(network[key], 'NETWORK_REF')

    endpoints = network['endpoints']
    check(type(endpoints) is list and bool(endpoints), 'ENDPOINT_TABLE_SCHEMA')
    endpoint_ids = set()
    endpoints_by_id = {}
    combos = set()
    first_use_origins = []
    for entry in endpoints:
        exact(entry, ('endpoint_id', 'provider', 'control_domain_id', 'origin', 'method',
                      'purpose', 'path_template', 'dossier', 'access_reference',
                      'response_contract', 'parser_identity'), 'ENDPOINT_SCHEMA')
        check(entry['provider'] in SLOTS and entry['purpose'] in PURPOSES and
              entry['method'] == 'GET', 'ENDPOINT_VALUE')
        source = sources[entry['provider']]
        mapping = source['purpose_mappings'][entry['purpose']]
        check(entry['origin'] == mapping['origin'] and
              entry['path_template'] == mapping['path_template'] and
              entry['dossier'] == source['dossier'] and
              entry['access_reference'] == mapping['mapping_evidence'] and
              entry['parser_identity'] == mapping['validator'] and
              entry['response_contract'] == mapping['response_contract'],
              'ENDPOINT_SOURCE_BINDING')
        expected_id = hashlib.sha256(canonical(
            [entry['provider'], entry['origin'], entry['path_template'],
             entry['purpose']])).hexdigest()
        check(entry['endpoint_id'] == expected_id and entry['endpoint_id'] not in endpoint_ids,
              'ENDPOINT_ID_DERIVATION')
        endpoint_ids.add(entry['endpoint_id'])
        expected_domain = mapping['control_domain_id']
        check(entry['control_domain_id'] == expected_domain, 'CONTROL_DOMAIN_DERIVATION')
        for key in ('dossier', 'access_reference', 'response_contract', 'parser_identity'):
            ref(entry[key], 'ENDPOINT_EVIDENCE_REF')
        combos.add((entry['provider'], entry['purpose']))
        if entry['origin'] not in first_use_origins:
            first_use_origins.append(entry['origin'])
        endpoints_by_id[entry['endpoint_id']] = entry
    check(len(endpoints) == len(SLOTS) * len(PURPOSES) and
          combos == {(p, pu) for p in SLOTS for pu in PURPOSES},
          'ENDPOINT_TABLE_COVERAGE')
    check(network['origins'] == first_use_origins, 'NETWORK_ALLOWLIST_ORDER')

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
          type(limits['min_start_interval_seconds']) is int and
          limits['min_start_interval_seconds'] >= 2 and
          type(limits['request_deadline_seconds']) is int and
          0 < limits['request_deadline_seconds'] <= 30, 'REQUEST_PACING')
    integer(limits['max_index_bytes'], 1, 3145728, 'INDEX_CAP')
    exact(limits['field_bytes'], SLOTS, 'FIELD_LIMIT_SET')
    for provider, bound in limits['field_bytes'].items():
        integer(bound, 1, FIELD_LIMITS[provider], 'FIELD_CAP')
    check(limits['min_free_disk_bytes'] >= 2 * 1024 ** 3 and
          limits['min_available_memory_bytes'] >= 512 * 1024 ** 2,
          'RESOURCE_MINIMUM')
    integer(limits['headers'], 1, 4096, 'HEADER_BOUND')
    integer(limits['metadata'], 1, METADATA_MAX_BYTES, 'METADATA_BOUND')
    integer(limits['decoded'], 1, MAX_BYTES, 'DECODED_BOUND')
    integer(limits['report_storage'], REPORT_RESERVE_BYTES, MAX_BYTES,
            'REPORT_STORAGE_BOUND')
    check(limits['report_storage'] <= storage['report_reserve_bytes'],
          'REPORT_RESERVE_INSUFFICIENT')

    schedule = payload['schedule']
    exact(schedule, ('slot_inventory_sha256', 'attempt_slots', 'requests',
                     'observed_sizes', 'estimated_full_raw_bytes', 'reservation_total_bytes',
                     'full_denominator', 'capture_mode', 'processing_seconds',
                     'finalization_seconds'), 'SCHEDULE_SCHEMA')
    digest(schedule['slot_inventory_sha256'], 'SLOT_DIGEST')
    check(hashlib.sha256(canonical(runs['slots'])).hexdigest() ==
          schedule['slot_inventory_sha256'], 'SLOT_DIGEST_MISMATCH')
    check(type(schedule['full_denominator']) is int and
          schedule['full_denominator'] == SLOT_COUNT and
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
    request_ids = set()
    field_started = False
    purpose_totals = defaultdict(lambda: {'requests': 0, 'reservation_bytes': 0})
    for position, request in enumerate(schedule['requests']):
        exact(request, ('request_id', 'purpose', 'slot_index', 'provider', 'origin',
                        'path', 'object_id', 'index_id', 'cache_id', 'endpoint_id',
                        'range_start', 'range_end', 'reservation_bytes', 'prerequisites'),
              'SCHEDULE_REQUEST_SCHEMA')
        check(type(request['purpose']) is str and
              request['purpose'] in PURPOSES and
              type(request['prerequisites']) is list and
              all(type(i) is int and 0 <= i < position for i in request['prerequisites']) and
              len(set(request['prerequisites'])) == len(request['prerequisites']),
              'SCHEDULE_PREREQUISITES')
        check(type(request['request_id']) is str and
              re.fullmatch(r'[A-Za-z0-9_-]{1,80}', request['request_id']) and
              request['request_id'] not in request_ids, 'SCHEDULE_REQUEST_ID')
        request_ids.add(request['request_id'])
        index = request['slot_index']
        check(type(index) is int and index in schedule['attempt_slots'],
              'SCHEDULE_SLOT_BINDING')
        provider, run, member, hour = expected[index]
        source = sources[provider]
        field_path = source['path_template'].format(run=run, member=member, hour=hour)
        mapping = source['purpose_mappings'][request['purpose']]
        request_path = mapping['path_template'].format(
            run=run, member=member, hour=hour)
        index_mapping = source['purpose_mappings']['INDEX']
        index_path = index_mapping['path_template'].format(
            run=run, member=member, hour=hour)
        object_mapping = source['purpose_mappings']['OBJECT_ID']
        object_path = object_mapping['path_template'].format(
            run=run, member=member, hour=hour)
        object_id = hashlib.sha256(canonical([
            provider, source['origin'], field_path,
            source['purpose_mappings']['FIELD']['mapping_evidence']['sha256'],
            object_mapping['origin'], object_path,
            object_mapping['mapping_evidence']['sha256']])).hexdigest()
        index_id = hashlib.sha256(canonical(
            [object_id, index_mapping['origin'], index_path,
             index_mapping['mapping_evidence']['sha256']])).hexdigest()
        cache_id = hashlib.sha256(canonical([object_id, index_id])).hexdigest()
        check(request['provider'] == provider and
              request['origin'] == mapping['origin'] and
              request['path'] == request_path and
              request['object_id'] == object_id and
              request['index_id'] == index_id and request['cache_id'] == cache_id,
              'SCHEDULE_OBJECT_BINDING')
        endpoint = endpoints_by_id.get(request['endpoint_id'])
        check(endpoint is not None and endpoint['provider'] == provider and
              endpoint['purpose'] == request['purpose'] and
              endpoint['origin'] == request['origin'] and
              request['endpoint_id'] not in (request['object_id'], request['index_id'],
                                             request['cache_id']),
              'SCHEDULE_ENDPOINT_BINDING')
        if request['purpose'] == 'FIELD':
            field_started = True
            field_slots.append(index)
            check({'INDEX', 'OBJECT_ID', 'METADATA'} <=
                  {prior_overhead[i]['purpose'] for i in request['prerequisites']
                   if i in prior_overhead} and
                  all(i in prior_overhead and
                      all(prior_overhead[i][k] == request[k] for k in
                          ('provider', 'object_id', 'index_id', 'cache_id'))
                      for i in request['prerequisites']),
                  'FIELD_PREREQUISITES')
            minimum = limits['field_bytes'][provider]
            integer(request['range_start'], 0, MAX_BYTES, 'FIELD_RANGE')
            integer(request['range_end'], request['range_start'], MAX_BYTES,
                    'FIELD_RANGE')
            check(request['range_end'] - request['range_start'] + 1 <= minimum,
                  'FIELD_PROVIDER_LIMIT')
            integer(request['reservation_bytes'], minimum, minimum,
                    'RESERVATION_TOO_SMALL')
            check(request['range_end'] - request['range_start'] + 1 <=
                  request['reservation_bytes'], 'FIELD_RANGE_RESERVATION')
        else:
            check(not field_started, 'OVERHEAD_MUST_PRECEDE_FIELD')
            check(request['range_start'] is None and request['range_end'] is None and
                  not request['prerequisites'], 'OVERHEAD_REQUEST_SHAPE')
            minimum = limits['max_index_bytes'] if request['purpose'] == 'INDEX' else limits['metadata']
            integer(request['reservation_bytes'], minimum, minimum,
                    'RESERVATION_TOO_SMALL')
            prior_overhead[position] = request
        purpose_totals[request['purpose']]['requests'] += 1
        purpose_totals[request['purpose']]['reservation_bytes'] += request['reservation_bytes']
        total += request['reservation_bytes']
    check(type(schedule['reservation_total_bytes']) is int and
          type(schedule['estimated_full_raw_bytes']) is int and
          total == schedule['reservation_total_bytes'] and
          total <= limits['max_received_bytes'] and
          schedule['estimated_full_raw_bytes'] >= 1469234173,
          'SCHEDULE_FEASIBILITY')
    check(field_slots == schedule['attempt_slots'] and
          len(field_slots) == len(set(field_slots)), 'ATTEMPT_ORDER_PARTITION')
    ref(schedule['observed_sizes'], 'OBSERVED_SIZE_REF')
    integer(schedule['processing_seconds'], max(60, len(field_slots)), 10800,
            'SCHEDULE_PROCESSING')
    integer(schedule['finalization_seconds'], 60, 10800,
            'SCHEDULE_FINALIZATION')
    count = len(schedule['requests'])
    serial_seconds = (count * limits['request_deadline_seconds'] +
                      (count - 1) * limits['min_start_interval_seconds'] +
                      schedule['processing_seconds'] + schedule['finalization_seconds'])
    check(serial_seconds <=
          limits['max_elapsed_seconds'], 'SCHEDULE_TIME_FEASIBILITY')

    clocks = payload['clocks_and_receipts']
    exact(clocks, ('preregistration', 'observation_schema'), 'CLOCK_SCHEMA')
    prereg = clocks['preregistration']
    exact(prereg, ('method', 'host_boot', 'sync_evidence', 'max_measurement_age_seconds'),
          'CLOCK_PREREGISTRATION_SCHEMA')
    for key in ('method', 'host_boot', 'sync_evidence'):
        ref(prereg[key], 'CLOCK_EVIDENCE_REF')
    integer(prereg['max_measurement_age_seconds'], 1, 3600, 'CLOCK_EVIDENCE_AGE')
    obs = clocks['observation_schema']
    exact(obs, ('phases', 'uncertainty_seconds_cap', 'cutoff_utc'),
          'CLOCK_OBSERVATION_SCHEMA')
    check(obs['phases'] == list(OBSERVATION_PHASES), 'CLOCK_FOUR_PHASES')
    check(type(obs['uncertainty_seconds_cap']) in (int, float) and
          0 <= obs['uncertainty_seconds_cap'] <= 1 and
          obs['uncertainty_seconds_cap'] == times['uncertainty_seconds'],
          'CLOCK_UNCERTAINTY_CAP')
    check(type(obs['cutoff_utc']) is int and
          obs['cutoff_utc'] == times['feature_seal_upper_utc'], 'CLOCK_CUTOFF')

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

    runtime = payload['runtime']
    exact(runtime, ('policy', 'schedule_digest', 'denial_root', 'session_root',
                    'report_root', 'purpose_plan', 'journal_bounds', 'clock_policy',
                    'resource_bounds'),
          'RUNTIME_SCHEMA')
    ref(runtime['policy'], 'RUNTIME_POLICY_REF')
    ref(runtime['clock_policy'], 'RUNTIME_CLOCK_POLICY_REF')
    check(runtime['schedule_digest'] == hashlib.sha256(canonical(schedule)).hexdigest(),
          'RUNTIME_SCHEDULE_DIGEST')
    exact(runtime['denial_root'], ('descriptor', 'expected_history_head'),
          'RUNTIME_DENIAL_ROOT_SCHEMA')
    ref(runtime['denial_root']['descriptor'], 'RUNTIME_DENIAL_ROOT_REF')
    digest(runtime['denial_root']['expected_history_head'], 'RUNTIME_DENIAL_HISTORY_HEAD')
    ref(runtime['session_root'], 'RUNTIME_SESSION_ROOT_REF')
    ref(runtime['report_root'], 'RUNTIME_REPORT_ROOT_REF')
    # The frozen purpose plan: independently recomputed from the actual
    # schedule, never trusted from the manifest's own claim. Unused overhead
    # or field headroom in one purpose can never expand another purpose,
    # subset or schedule, because the plan must equal this exact recount.
    expected_plan = {purpose: dict(purpose_totals.get(
        purpose, {'requests': 0, 'reservation_bytes': 0})) for purpose in PURPOSES}
    check(type(runtime['purpose_plan']) is dict and
          set(runtime['purpose_plan']) == set(PURPOSES), 'RUNTIME_PURPOSE_PLAN_SCHEMA')
    for purpose, plan in runtime['purpose_plan'].items():
        exact(plan, ('requests', 'reservation_bytes'), 'RUNTIME_PURPOSE_PLAN_SCHEMA')
        integer(plan['requests'], 0, limits['max_requests'], 'RUNTIME_PURPOSE_PLAN_SCHEMA')
        integer(plan['reservation_bytes'], 0, MAX_BYTES, 'RUNTIME_PURPOSE_PLAN_SCHEMA')
    check(runtime['purpose_plan'] == expected_plan, 'RUNTIME_PURPOSE_PLAN_MISMATCH')
    check(sum(p['requests'] for p in runtime['purpose_plan'].values()) == count and
          sum(p['reservation_bytes'] for p in runtime['purpose_plan'].values()) == total,
          'RUNTIME_PURPOSE_PLAN_TOTAL')
    # The journal caps a manifest declares must equal the code's own fixed
    # caps exactly: a manifest can never claim, and therefore never widen,
    # a looser bound than what DurableBudget actually enforces.
    check(runtime['journal_bounds'] == {'max_bytes': JOURNAL_MAX_BYTES,
          'record_max_bytes': JOURNAL_RECORD_MAX_BYTES,
          'max_events': JOURNAL_MAX_EVENTS}, 'RUNTIME_JOURNAL_BOUNDS_MISMATCH')

    resources = runtime['resource_bounds']
    exact(resources, ('session_journal_max_bytes', 'denial_journal_max_bytes',
                      'store_journal_max_bytes', 'journal_record_max_bytes',
                      'session_journal_max_events', 'denial_journal_max_events',
                      'store_max_events', 'store_object_max_bytes',
                      'store_max_objects', 'descriptor_max_bytes',
                      'clock_record_max_bytes', 'receipt_max_dependencies',
                      'max_body_chunks_per_request', 'report_reserve_bytes', 'required_store_objects',
                      'local_storage_quota_bytes'), 'RUNTIME_RESOURCE_SCHEMA')
    graph_nodes = count
    aggregates = 0
    while graph_nodes > 1:
        graph_nodes = (graph_nodes + 255) // 256
        aggregates += graph_nodes
    expected_objects = 2 * count + aggregates
    fixed_resources = {
        'session_journal_max_bytes': JOURNAL_MAX_BYTES,
        'denial_journal_max_bytes': JOURNAL_MAX_BYTES,
        'store_journal_max_bytes': JOURNAL_MAX_BYTES,
        'journal_record_max_bytes': JOURNAL_RECORD_MAX_BYTES,
        'session_journal_max_events': SESSION_JOURNAL_MAX_EVENTS,
        'denial_journal_max_events': SESSION_JOURNAL_MAX_EVENTS,
        'store_max_events': STORE_MAX_EVENTS,
        'store_object_max_bytes': STORE_OBJECT_MAX_BYTES,
        'store_max_objects': STORE_MAX_OBJECTS,
        'descriptor_max_bytes': 4096,
        'clock_record_max_bytes': 16384,
        'receipt_max_dependencies': 256,
        'max_body_chunks_per_request': 32,
        'report_reserve_bytes': storage['report_reserve_bytes'],
        'required_store_objects': expected_objects,
    }
    check(all(resources.get(k) == v and type(resources.get(k)) is int
              for k, v in fixed_resources.items()), 'RUNTIME_RESOURCE_BOUNDS')
    check(expected_objects <= STORE_MAX_OBJECTS and
          count * 4 + 1 <= STORE_MAX_EVENTS and
          count * 8 + 1 <= SESSION_JOURNAL_MAX_EVENTS and
          count * (2 + resources['max_body_chunks_per_request']) + 1 <=
          JOURNAL_MAX_EVENTS,
          'RUNTIME_RESOURCE_EVENT_CAPACITY')
    prospective_bytes = (4 * JOURNAL_MAX_BYTES +
                         storage['report_reserve_bytes'] +
                         expected_objects * STORE_OBJECT_MAX_BYTES +
                         limits['decoded'] + total)
    integer(resources['local_storage_quota_bytes'], prospective_bytes,
            2 ** 63 - 1, 'RUNTIME_STORAGE_QUOTA')

    _resolve_refs(payload, root)
    return hashlib.sha256(raw).hexdigest()
