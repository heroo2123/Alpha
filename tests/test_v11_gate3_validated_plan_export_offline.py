"""Synthetic supplied-byte checks; no validator, runtime or provider calls."""

import hashlib
import json
import socket
import subprocess
from copy import deepcopy

import pytest

from tools.v11_multimodel_panel import canonical
from tools.v11_r09_gate3_launch import _slot_inventory
from tools.v11_gate3_validated_plan_export_offline import (
    ExportRefusal, FLAGS, ROLES, consume_accepted_export, inspect_supplied_inputs,
)


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def inputs():
    runs = {'GEFS': 1, 'IFS': 1, 'AIFS': 1}
    slots = _slot_inventory(runs)
    endpoints = []
    for provider in runs:
        for purpose in ('FIELD', 'INDEX', 'OBJECT_ID', 'METADATA', 'PROBE'):
            endpoints.append({'endpoint_id': digest([provider, purpose]),
                              'provider': provider, 'purpose': purpose,
                              'origin': 'https://example.invalid',
                              'control_domain_id': digest(provider),
                              'parser_identity': {'sha256': '5' * 64}})
    request = {'request_id': 'field_1', 'purpose': 'FIELD', 'slot_index': 0,
               'provider': 'GEFS', 'origin': 'https://example.invalid',
               'path': '/field', 'object_id': 'a' * 64, 'index_id': 'b' * 64,
               'cache_id': 'c' * 64, 'endpoint_id': endpoints[0]['endpoint_id'],
               'range_start': 0, 'range_end': 1, 'reservation_bytes': 2,
               'prerequisites': []}
    keys = [['station', 'event_high', 'rule', '2026-10-04',
             'daily_high_temperature'],
            ['station', 'event_low', 'rule', '2026-10-04',
             'daily_low_temperature']]
    cohort = {'station_id': 'station', 'station_version': 'v1',
              'latitude': 1, 'longitude': 2, 'timezone': 'UTC',
              'tzdata': {'sha256': 'd' * 64}, 'target_date': '2026-10-04',
              'events': ['HIGH', 'LOW'], 'units': 'CELSIUS', 'rounding': 'whole',
              'buckets': {'sha256': 'e' * 64}, 'rule': {'sha256': 'f' * 64},
              'settlement': {'sha256': '1' * 64},
              'metadata': {'sha256': '2' * 64},
              'selection': {'sha256': '3' * 64},
              'requested_keys': keys,
              'gate2_trial_keys': [[k[0], k[1], k[3]] for k in keys],
              'city_day': ['station', '2026-10-04']}
    manifest = {name: {} for name in (
        'identity', 'code', 'protocol', 'storage', 'cohort', 'time', 'sources',
        'runs_and_slots', 'network', 'limits', 'schedule', 'clocks_and_receipts',
        'accounting', 'runtime')}
    manifest.update(identity={'schema': 'R09_GATE3_LAUNCH_MANIFEST_V4'},
                    cohort=cohort,
                    time={'local_day_start_utc': 1, 'local_day_end_utc': 2,
                          'decision_utc': 3, 'decision_lower_utc': 4,
                          'window_start_utc': 5, 'last_acquisition_utc': 6,
                          'uncertainty_seconds': 0},
                    limits={'request_deadline_seconds': 7,
                            'max_elapsed_seconds': 8},
                    sources={p: {'dossier': {'sha256': '6' * 64},
                                  'decoder_build': {'sha256': '7' * 64}}
                             for p in runs},
                    runs_and_slots={'run_utc': runs, 'slots': slots},
                    network={'endpoints': endpoints},
                    schedule={'requests': [request], 'attempt_slots': [0],
                              'full_denominator': 2713,
                              'slot_inventory_sha256': digest(slots),
                              'reservation_total_bytes': 2},
                    runtime={'policy': {'sha256': '4' * 64},
                             'clock_policy': {'sha256': '8' * 64},
                             'purpose_plan': {p: {'requests': int(p == 'FIELD'),
                                                  'reservation_bytes':
                                                  2 if p == 'FIELD' else 0}
                                              for p in ('FIELD', 'INDEX', 'OBJECT_ID',
                                                        'METADATA', 'PROBE')}})
    pins = {'field_1': {'expected_etag': '"etag"',
                        'expected_object_bytes': 2,
                        'dependency_commit_hashes': []}}
    context = {'manifest_runtime_sha256': digest(manifest['runtime']),
               'boot_id': 'boot', 'shared_root': [1, 2], 'session_root': [1, 3],
               'budget_root': [1, 4], 'store_descriptor': {},
               'report_root': [1, 5], 'store_policy': '4' * 64,
               'clock_method': 'synthetic', 'allowed_peer_ips': []}
    precedence = ['PREREQUISITE', 'CLOCK', 'RESOURCE', 'DENIAL', 'VALIDATION',
                  'SUCCESS', 'UNSCHEDULED']
    policy = {'schema': 'ALPHA_V11_GATE3_EVENT_EXPORT_POLICY_V1',
              'manifest_sha256': digest(manifest),
              'entries': [{'requested_key': key, 'primary_provider': 'GEFS'}
                          for key in keys]}
    policy_review = {'schema': 'ALPHA_V11_GATE3_EVENT_EXPORT_POLICY_REVIEW_V1',
                     'status': 'ACCEPTED', 'event_policy_sha256': digest(policy),
                     'manifest_sha256': digest(manifest),
                     'producer_commit_oid': 'a' * 40,
                     'producer_tree_oid': 'b' * 40,
                     'reviewer_id': 'synthetic', 'reviewer_model': 'synthetic',
                     'completed_utc': 1}
    window = {'start_utc': 5, 'acquisition_end_utc': 6,
              'decision_lower_utc': 4, 'request_deadline_seconds': 7,
              'elapsed_cap_seconds': 8, 'uncertainty_cap_seconds': 0}
    projected_request = {'request_id': 'field_1', 'purpose': 'FIELD',
                         'endpoint_id': endpoints[0]['endpoint_id'],
                         'control_domain_id': endpoints[0]['control_domain_id'],
                         'origin': request['origin'], 'path': request['path'],
                         'provider': 'GEFS', 'slot_index': 0,
                         'range_start': 0, 'range_end': 1,
                         'reservation_bytes': 2,
                         'expected_etag': '"etag"', 'expected_object_bytes': 2,
                         'source_pin': '6' * 64, 'decoder_pin': '7' * 64,
                         'clock_policy_sha256': '8' * 64,
                         'validator_sha256': '5' * 64,
                         'dependency_commit_hashes': [],
                         'prerequisite_request_ids': []}
    projected_events = [{'event_id': k[1], 'side': side,
                         'primary_provider': 'GEFS',
                         'field_request_ids': ['field_1'],
                         'requested_key': k,
                         'gate2_trial_key': [k[0], k[1], k[3]]}
                        for side, k in zip(('HIGH', 'LOW'), keys)]
    review = {'schema_version': 2, 'manifest_sha256': digest(manifest),
              'window_sha256': digest(window),
              'request_schedule_sha256': digest([projected_request]),
              'event_schedule_sha256': digest(projected_events),
              'supplemental_pins_sha256': digest(pins),
              'terminal_precedence_sha256': digest(precedence),
              'runtime_context_sha256': digest(context)}
    return dict(zip(ROLES, map(canonical, (manifest, review, pins, context,
                                         precedence, policy, policy_review))))


def refuse(value, code):
    with pytest.raises(ExportRefusal, match=f'^{code}$'):
        inspect_supplied_inputs(value)


def rebind_manifest(raw, manifest):
    """Model a caller recomputing every hash it controls, not trusted pins."""
    raw['manifest'] = canonical(manifest)
    policy = json.loads(raw['event_policy'])
    policy['manifest_sha256'] = digest(manifest)
    raw['event_policy'] = canonical(policy)
    policy_review = json.loads(raw['event_policy_review'])
    policy_review['manifest_sha256'] = digest(manifest)
    policy_review['event_policy_sha256'] = digest(policy)
    raw['event_policy_review'] = canonical(policy_review)
    review = json.loads(raw['plan_review'])
    review['manifest_sha256'] = digest(manifest)
    raw['plan_review'] = canonical(review)


def rebind_schedule(raw, manifest, pins):
    """Recompute every caller-controlled digest touched by a schedule edit."""
    schedule = manifest['schedule']
    requests = schedule['requests']
    endpoints = {e['endpoint_id']: e for e in manifest['network']['endpoints']}
    totals = {p: {'requests': 0, 'reservation_bytes': 0}
              for p in ('FIELD', 'INDEX', 'OBJECT_ID', 'METADATA', 'PROBE')}
    projections = []
    field_ids = []
    for request in requests:
        purpose = request['purpose']
        endpoint = endpoints[request['endpoint_id']]
        source = manifest['sources'][request['provider']]
        supplemental = pins[request['request_id']]
        totals[purpose]['requests'] += 1
        totals[purpose]['reservation_bytes'] += request['reservation_bytes']
        if purpose == 'FIELD':
            field_ids.append(request['request_id'])
        projections.append({
            'request_id': request['request_id'], 'purpose': purpose,
            'endpoint_id': request['endpoint_id'],
            'control_domain_id': endpoint['control_domain_id'],
            'origin': request['origin'], 'path': request['path'],
            'provider': request['provider'] if purpose == 'FIELD' else None,
            'slot_index': request['slot_index'],
            'range_start': request['range_start'],
            'range_end': request['range_end'],
            'reservation_bytes': request['reservation_bytes'],
            'expected_etag': supplemental['expected_etag'],
            'expected_object_bytes': supplemental['expected_object_bytes'],
            'source_pin': source['dossier']['sha256'],
            'decoder_pin': source['decoder_build']['sha256'],
            'clock_policy_sha256': manifest['runtime']['clock_policy']['sha256'],
            'validator_sha256': endpoint['parser_identity']['sha256'],
            'dependency_commit_hashes': supplemental['dependency_commit_hashes'],
            'prerequisite_request_ids':
                [requests[i]['request_id'] for i in request['prerequisites']]})
    manifest['runtime']['purpose_plan'] = totals
    schedule['reservation_total_bytes'] = sum(
        request['reservation_bytes'] for request in requests)
    schedule['slot_inventory_sha256'] = digest(manifest['runs_and_slots']['slots'])
    context = json.loads(raw['runtime_context'])
    context['manifest_runtime_sha256'] = digest(manifest['runtime'])
    raw['runtime_context'] = canonical(context)
    raw['supplemental_pins'] = canonical(pins)
    rebind_manifest(raw, manifest)
    policy = json.loads(raw['event_policy'])
    events = [
        {'event_id': key[1], 'side': side,
         'primary_provider': entry['primary_provider'],
         'field_request_ids': field_ids, 'requested_key': key,
         'gate2_trial_key': trial}
        for side, key, trial, entry in zip(
            manifest['cohort']['events'], manifest['cohort']['requested_keys'],
            manifest['cohort']['gate2_trial_keys'], policy['entries'])]
    review = json.loads(raw['plan_review'])
    review['request_schedule_sha256'] = digest(projections)
    review['event_schedule_sha256'] = digest(events)
    review['supplemental_pins_sha256'] = digest(pins)
    review['runtime_context_sha256'] = digest(context)
    raw['plan_review'] = canonical(review)


def test_replay_and_all_seven_roles():
    raw = inputs()
    first = inspect_supplied_inputs(raw)
    second = inspect_supplied_inputs(dict(reversed(list(raw.items()))))
    assert first == second
    assert len(first.event_identities) == 2
    assert b'event_high' in first.event_identities[0]
    assert b'event_low' in first.event_identities[1]
    assert all(value is False for value in FLAGS.values() if type(value) is bool)
    assert FLAGS['g3l'] == 'NO_GO'
    for role in ROLES:
        refuse({k: v for k, v in raw.items() if k != role}, 'VPE_SCHEMA')


@pytest.mark.parametrize('role,mutate,code', [
    ('manifest', lambda x: x['schedule']['requests'][0].update(object_id='e' * 64),
     'VPE_PROJECTION_MISMATCH'),
    ('manifest', lambda x: x['schedule']['attempt_slots'].clear(),
     'VPE_PROJECTION_MISMATCH'),
    ('manifest', lambda x: x['cohort']['events'].pop(),
     'VPE_PROJECTION_MISMATCH'),
    ('event_policy', lambda x: x['entries'].pop(), 'VPE_REVIEW_NOT_ACCEPTED'),
    ('event_policy_review', lambda x: x.update(status='PENDING'),
     'VPE_REVIEW_NOT_ACCEPTED'),
])
def test_substitution_refused(role, mutate, code):
    raw = inputs()
    value = json.loads(raw[role])
    mutate(value)
    raw[role] = canonical(value)
    refuse(raw, code)


def test_canonical_and_bounds():
    raw = inputs()
    raw['event_policy_review'] += b'\n'
    refuse(raw, 'VPE_CANONICAL_BYTES')
    raw = inputs()
    raw['event_policy_review'] = b'{' + b'"x":0,' * 5000 + b'"x":0}'
    refuse(raw, 'VPE_INPUT_BOUNDS')
    raw = inputs()
    raw['event_policy_review'] = b'{"x":1,"x":2}'
    refuse(raw, 'VPE_CANONICAL_BYTES')


def test_self_supplied_pin_and_no_socket(monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect',
                        lambda *args: pytest.fail('socket access'))
    monkeypatch.setattr(subprocess, 'Popen',
                        lambda *args, **kwargs: pytest.fail('subprocess access'))
    raw = inputs()
    inspect_supplied_inputs(raw)
    body = canonical({'schema': 'fake'})
    with pytest.raises(ExportRefusal, match='VPE_EXTERNAL_TRUST_UNAVAILABLE'):
        consume_accepted_export(export_raw=body, custody_raw=body,
                                acceptance_raw=body,
                                authenticated_pin_channel={'export_sha256':
                                                           hashlib.sha256(body).hexdigest()})
    with pytest.raises(ExportRefusal, match='VPE_EXTERNAL_TRUST_UNAVAILABLE'):
        consume_accepted_export(export_raw=body, custody_raw=body,
                                acceptance_raw=body, authenticated_pin_channel=object())


def test_rehashed_omission_is_only_an_untrusted_snapshot():
    raw = inputs()
    manifest = json.loads(raw['manifest'])
    manifest['cohort']['events'].pop()
    manifest['cohort']['requested_keys'].pop()
    manifest['cohort']['gate2_trial_keys'].pop()
    raw['manifest'] = canonical(manifest)
    policy = json.loads(raw['event_policy'])
    policy['manifest_sha256'] = digest(manifest)
    policy['entries'].pop()
    raw['event_policy'] = canonical(policy)
    policy_review = json.loads(raw['event_policy_review'])
    policy_review['manifest_sha256'] = digest(manifest)
    policy_review['event_policy_sha256'] = digest(policy)
    raw['event_policy_review'] = canonical(policy_review)
    review = json.loads(raw['plan_review'])
    review['manifest_sha256'] = digest(manifest)
    review['event_schedule_sha256'] = digest([{
        'event_id': 'event_high', 'side': 'HIGH', 'primary_provider': 'GEFS',
        'field_request_ids': ['field_1'],
        'requested_key': manifest['cohort']['requested_keys'][0],
        'gate2_trial_key': manifest['cohort']['gate2_trial_keys'][0]}])
    raw['plan_review'] = canonical(review)
    snapshot = inspect_supplied_inputs(raw)
    assert len(snapshot.event_identities) == 1
    assert snapshot.bundle_sha256 != inspect_supplied_inputs(inputs()).bundle_sha256
    with pytest.raises(ExportRefusal, match='VPE_EXTERNAL_TRUST_UNAVAILABLE'):
        consume_accepted_export(export_raw=canonical({'snapshot': snapshot.bundle_sha256}),
                                custody_raw=canonical({}), acceptance_raw=canonical({}),
                                authenticated_pin_channel={'bundle': snapshot.bundle_sha256})


def test_malformed_nested_shape_refuses_with_code():
    raw = inputs()
    policy = json.loads(raw['event_policy'])
    policy['entries'][0] = []
    raw['event_policy'] = canonical(policy)
    policy_review = json.loads(raw['event_policy_review'])
    policy_review['event_policy_sha256'] = digest(policy)
    raw['event_policy_review'] = canonical(policy_review)
    refuse(raw, 'VPE_EVENT_IDENTITY')


@pytest.mark.parametrize('mutation,code', [
    (lambda m: m['schedule']['requests'].clear(), 'VPE_SCHEDULE_COMPLETENESS'),
    (lambda m: m['schedule']['attempt_slots'].clear(), 'VPE_PURPOSE_BUDGET'),
    (lambda m: m['schedule']['requests'][0].update(slot_index=1),
     'VPE_PURPOSE_BUDGET'),
    (lambda m: m['cohort']['requested_keys'][1].__setitem__(1, 'event_high'),
     'VPE_EVENT_IDENTITY'),
])
def test_rehashed_schedule_and_event_substitution_refused(mutation, code):
    raw = inputs()
    manifest = json.loads(raw['manifest'])
    mutation(manifest)
    rebind_manifest(raw, manifest)
    refuse(raw, code)


def test_policy_provider_substitution_refused_after_self_rehash():
    raw = inputs()
    policy = json.loads(raw['event_policy'])
    policy['entries'][0]['primary_provider'] = 'IFS'
    raw['event_policy'] = canonical(policy)
    review = json.loads(raw['event_policy_review'])
    review['event_policy_sha256'] = digest(policy)
    raw['event_policy_review'] = canonical(review)
    refuse(raw, 'VPE_EVENT_IDENTITY')


def test_duplicate_field_slot_refused_after_full_rehash():
    raw = inputs()
    manifest = json.loads(raw['manifest'])
    pins = json.loads(raw['supplemental_pins'])
    second = deepcopy(manifest['schedule']['requests'][0])
    second['request_id'] = 'field_2'
    manifest['schedule']['requests'].append(second)
    manifest['schedule']['attempt_slots'] = [0, 0]
    pins['field_2'] = deepcopy(pins['field_1'])
    rebind_schedule(raw, manifest, pins)
    refuse(raw, 'VPE_SCHEDULE_COMPLETENESS')


@pytest.mark.parametrize('mutate', [
    lambda m: m['runs_and_slots']['slots'][0].__setitem__(2, False),
    lambda m: m['runs_and_slots']['slots'][0].__setitem__(2, 0.0),
    lambda m: m['schedule'].update(full_denominator=2713.0),
    lambda m: m['schedule'].update(full_denominator=True),
    lambda m: m['schedule'].update(attempt_slots=[False]),
    lambda m: m['schedule'].update(attempt_slots=[0.0]),
])
def test_slot_types_refused_after_full_rehash(mutate):
    raw = inputs()
    manifest = json.loads(raw['manifest'])
    mutate(manifest)
    rebind_schedule(raw, manifest, json.loads(raw['supplemental_pins']))
    refuse(raw, 'VPE_SCHEDULE_COMPLETENESS')


@pytest.mark.parametrize('external_count,refused', [(255, False), (256, True)])
def test_combined_dependency_limit_after_full_rehash(external_count, refused):
    raw = inputs()
    manifest = json.loads(raw['manifest'])
    pins = json.loads(raw['supplemental_pins'])
    field = manifest['schedule']['requests'][0]
    index = deepcopy(field)
    index.update(request_id='index_0', purpose='INDEX',
                 endpoint_id=manifest['network']['endpoints'][1]['endpoint_id'],
                 range_start=None, range_end=None)
    manifest['schedule']['requests'].insert(0, index)
    field['prerequisites'] = [0]
    pins['index_0'] = deepcopy(pins['field_1'])
    pins['field_1']['dependency_commit_hashes'] = [
        f'{i:064x}' for i in range(external_count)]
    rebind_schedule(raw, manifest, pins)
    if refused:
        refuse(raw, 'VPE_SCHEDULE_COMPLETENESS')
    else:
        assert len(inspect_supplied_inputs(raw).event_identities) == 2


@pytest.mark.parametrize('mutate', [
    lambda m: m.update(identity=[]),
    lambda m: m.update(identity=None),
    lambda m: m.update(schedule=[]),
    lambda m: m['network']['endpoints'].__setitem__(0, []),
])
def test_malformed_nested_shapes_refuse_after_self_rehash(mutate):
    raw = inputs()
    manifest = json.loads(raw['manifest'])
    mutate(manifest)
    rebind_manifest(raw, manifest)
    refuse(raw, 'VPE_SCHEMA')
