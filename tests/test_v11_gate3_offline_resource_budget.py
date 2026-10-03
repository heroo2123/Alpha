"""Synthetic arithmetic checks for the proposal-only Gate 3 calculator."""

import copy
import socket
import tracemalloc
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tools.v11_r09_gate3_launch import DurableBudget
from tools.v11_r09_gate3_runtime import CapacityPlan
from tools.v11_r09_gate3_runtime import SyntheticTransport
from tools.v11_gate3_offline_resource_budget import calculate_offline_resource_budget


def fixture():
    requests = [
        {'request_id': 'index', 'purpose': 'INDEX', 'reservation_bytes': 3},
        {'request_id': 'field', 'purpose': 'FIELD', 'provider': 'GEFS',
         'reservation_bytes': 5},
    ]
    # V4's required object formula for two requests is 2*2 + one root.
    quota = 4 * 64 * 1024**2 + 16 * 1024**2 + 5 * 4194304 + 64 * 1024**2 + 8
    manifest = {
        'identity': {'schema': 'R09_GATE3_LAUNCH_MANIFEST_V4'},
        'cohort': {'events': ['HIGH']},
        'limits': {'max_requests': 3600, 'max_received_bytes': 100,
                   'max_elapsed_seconds': 10800, 'request_deadline_seconds': 30,
                   'min_start_interval_seconds': 2, 'decoded': 64 * 1024**2},
        'schedule': {'requests': requests, 'reservation_total_bytes': 8,
                     'processing_seconds': 60, 'finalization_seconds': 60},
        'storage': {'report_reserve_bytes': 16 * 1024**2},
        'runtime': {
            'purpose_plan': {p: {'requests': int(p in ('INDEX', 'FIELD')),
                                'reservation_bytes': 3 if p == 'INDEX' else
                                5 if p == 'FIELD' else 0}
                             for p in ('FIELD', 'INDEX', 'OBJECT_ID', 'METADATA', 'PROBE')},
            'resource_bounds': {'report_reserve_bytes': 16 * 1024**2,
                                'max_body_chunks_per_request': 32,
                                'store_object_max_bytes': 4194304,
                                'journal_record_max_bytes': 65536,
                                'required_store_objects': 5,
                                'local_storage_quota_bytes': quota,
                                'store_max_objects': 4096,
                                'store_max_events': 10000,
                                'session_journal_max_events': 32768,
                                'denial_journal_max_events': 32768}},
    }
    plan = {'mode': 'OFFLINE_PROPOSAL',
            'requests': [{k: r[k] for k in ('request_id', 'purpose', 'reservation_bytes')}
                         for r in requests],
            'events': [{'field_request_ids': ['field']}]}
    return manifest, plan


def test_arithmetic_parity_and_separate_unknown_budgets():
    check = unittest.TestCase()
    manifest, plan = fixture()
    before = copy.deepcopy((manifest, plan))
    with (patch.object(socket, 'socket', side_effect=AssertionError('socket opened')),
          patch.object(DurableBudget, 'reserve', side_effect=AssertionError('account changed')),
          patch.object(SyntheticTransport, 'dispatch',
                       side_effect=AssertionError('provider dispatched'))):
        result = calculate_offline_resource_budget(manifest, plan)
    expected = CapacityPlan.for_requests(
        (SimpleNamespace(reservation_bytes=3), SimpleNamespace(reservation_bytes=5)),
        (SimpleNamespace(field_request_ids=('field',)),))
    check.assertEqual(result['runtime_capacity'], vars(expected))
    check.assertEqual(result['event_binding'],
                      'MATCHES_SUPPLIED_V4_MANIFEST_FIELD_EXPANSION')
    check.assertIs(result['capacity_covers_frozen_schedule'], True)
    check.assertEqual(result['purpose_budgets']['INDEX'],
                      {'requests': 1, 'reservation_bytes': 3})
    check.assertEqual(result['purpose_budgets']['PROBE'],
                      {'requests': 0, 'reservation_bytes': 0})
    check.assertEqual(result['unallocated_received_bytes'], 92)
    check.assertEqual(result['unallocated_request_count'], 3598)
    check.assertEqual(result['unallocated_elapsed_seconds'], 10800 - 182)
    check.assertEqual(result['unknown_delivered_bytes_range'], [0, 8])
    check.assertEqual(result['unknown_delivered_bytes_by_purpose']['FIELD'], [0, 5])
    check.assertEqual(result['serial_seconds_upper'], 182)
    check.assertEqual(result['v4_quota_formula_bytes'], result['v4_local_storage_quota_bytes'])
    check.assertTrue(all(result['fresh_ceiling_checks'].values()))
    check.assertEqual(result['existing_occupancy'], 'UNKNOWN')
    check.assertEqual(result['live_host_resources'], 'UNKNOWN')
    check.assertIs(result['execution_authority'], False)
    check.assertIs(result['provider_authority'], False)
    check.assertIs(result['resource_qualification'], False)
    check.assertEqual(result['g3l'], 'NO_GO')
    check.assertEqual(result['qualification_credit'], 0)
    check.assertEqual((manifest, plan), before)


MUTATIONS = [
    lambda m, p: p.update(mode='LIVE'),
    lambda m, p: p['requests'][0].update(reservation_bytes=True),
    lambda m, p: m['limits'].update(max_received_bytes=True),
    lambda m, p: p['requests'][0].update(reservation_bytes=2**63),
    lambda m, p: p['requests'].append(p['requests'][0]),
    lambda m, p: p['events'][0].update(field_request_ids=['missing']),
    lambda m, p: m['runtime']['purpose_plan']['INDEX'].update(reservation_bytes=4),
    lambda m, p: m['runtime']['resource_bounds'].update(local_storage_quota_bytes=0),
    lambda m, p: m['runtime']['resource_bounds'].update(local_storage_quota_bytes=2**63),
    lambda m, p: m['schedule'].update(processing_seconds=2**63),
    lambda m, p: m.update(identity=[]),
    lambda m, p: p['requests'][0].update(purpose=[]),
    lambda m, p: m['schedule']['requests'][0].update(reservation_bytes=True),
]
def test_refuses_invalid_or_unbounded_projection():
    for mutate in MUTATIONS:
        manifest, plan = fixture()
        mutate(manifest, plan)
        with unittest.TestCase().assertRaises(ValueError):
            calculate_offline_resource_budget(manifest, plan)


def test_fresh_runtime_ceiling_is_reported_without_qualification():
    check = unittest.TestCase()
    manifest, plan = fixture()
    # A large accepted-size V4 proposal can exceed fresh runtime journal
    # capacity. The diagnostic reports this without granting an allocation.
    source = manifest['schedule']['requests'][1]
    source['reservation_bytes'] = 2_000_000
    plan['requests'][1]['reservation_bytes'] = 2_000_000
    manifest['schedule']['reservation_total_bytes'] = 2_000_003
    manifest['limits']['max_received_bytes'] = 2_000_003
    manifest['runtime']['purpose_plan']['FIELD']['reservation_bytes'] = 2_000_000
    manifest['runtime']['resource_bounds']['local_storage_quota_bytes'] += 1_999_995
    # A single request remains under every fresh journal ceiling.
    result = calculate_offline_resource_budget(manifest, plan)
    check.assertIs(result['resource_qualification'], False)
    check.assertGreater(result['minimum_free_disk_for_fresh_plan_bytes'], 2 * 1024**3)


def test_runtime_journal_ceiling_is_visible_and_never_admission():
    check = unittest.TestCase()
    manifest, plan = fixture()
    n = 60
    rows = [{'request_id': f'index-{i}', 'purpose': 'INDEX',
             'reservation_bytes': 3} for i in range(n - 1)] + [
             {'request_id': 'field', 'purpose': 'FIELD', 'provider': 'GEFS',
              'reservation_bytes': 5}]
    manifest['schedule']['requests'] = rows
    body = 3 * (n - 1) + 5
    manifest['schedule']['reservation_total_bytes'] = body
    manifest['limits']['max_received_bytes'] = body
    manifest['runtime']['purpose_plan']['INDEX'] = {
        'requests': n - 1, 'reservation_bytes': 3 * (n - 1)}
    manifest['runtime']['purpose_plan']['FIELD'] = {
        'requests': 1, 'reservation_bytes': 5}
    objects = 2 * n + 1
    bounds = manifest['runtime']['resource_bounds']
    bounds['required_store_objects'] = objects
    bounds['local_storage_quota_bytes'] = (
        4 * 64 * 1024**2 + 16 * 1024**2 + objects * 4194304 +
        64 * 1024**2 + body)
    plan['requests'] = [
        {k: row[k] for k in ('request_id', 'purpose', 'reservation_bytes')}
        for row in rows]
    result = calculate_offline_resource_budget(manifest, plan)
    check.assertFalse(result['fresh_ceiling_checks']['session_journal_bytes'])
    check.assertIs(result['resource_qualification'], False)
    check.assertEqual(result['g3l'], 'NO_GO')


def test_omitted_event_is_refused():
    check = unittest.TestCase()
    manifest, plan = fixture()
    plan['events'] = []
    with check.assertRaises(ValueError):
        calculate_offline_resource_budget(manifest, plan)


def two_field_fixture():
    manifest, plan = fixture()
    second = {'request_id': 'field-2', 'purpose': 'FIELD', 'provider': 'GEFS',
              'reservation_bytes': 7}
    manifest['schedule']['requests'].append(second)
    plan['requests'].append({k: second[k] for k in
                             ('request_id', 'purpose', 'reservation_bytes')})
    manifest['schedule']['reservation_total_bytes'] += 7
    manifest['limits']['max_received_bytes'] += 7
    manifest['runtime']['purpose_plan']['FIELD'] = {
        'requests': 2, 'reservation_bytes': 12}
    bounds = manifest['runtime']['resource_bounds']
    bounds['required_store_objects'] = 7
    bounds['local_storage_quota_bytes'] += 2 * 4194304 + 7
    plan['events'][0]['field_request_ids'].append('field-2')
    manifest['cohort']['events'].append('LOW')
    plan['events'].append({'field_request_ids': ['field', 'field-2']})
    return manifest, plan


def test_exact_event_expansion_refuses_mutations():
    check = unittest.TestCase()
    manifest, plan = two_field_fixture()
    result = calculate_offline_resource_budget(manifest, plan)
    check.assertEqual(result['event_binding'],
                      'MATCHES_SUPPLIED_V4_MANIFEST_FIELD_EXPANSION')
    check.assertIs(result['capacity_covers_frozen_schedule'], True)
    expected = CapacityPlan.for_requests(
        tuple(SimpleNamespace(reservation_bytes=r['reservation_bytes'])
              for r in plan['requests']),
        tuple(SimpleNamespace(field_request_ids=tuple(e['field_request_ids']))
              for e in plan['events']))
    check.assertEqual(result['runtime_capacity'], vars(expected))
    cases = (
        lambda p: p['events'].pop(),                         # omitted event
        lambda p: p['events'][0]['field_request_ids'].pop(),  # omitted member
        lambda p: p['events'][0]['field_request_ids'].reverse(),
        lambda p: p['events'][0]['field_request_ids'].__setitem__(1, 'field'),
        lambda p: p['events'][0]['field_request_ids'].__setitem__(1, 'unknown'),
        lambda p: p['events'][0].update(field_request_ids=('field', 'field-2')),
        lambda p: p['events'][0].update(field_request_ids=['field', 1]),
        lambda p: p['events'][0].update(extra='unbound'),
        lambda p: p['events'].append({'field_request_ids': ['field', 'field-2']}),
    )
    for mutate in cases:
        candidate = copy.deepcopy(plan)
        mutate(candidate)
        with check.subTest(mutate=mutate), check.assertRaises(ValueError):
            calculate_offline_resource_budget(manifest, candidate)
    for bad in ([], ['HIGH', 'HIGH'], ['HIGH', 'LOW', 'HIGH'],
                ['HIGH', 1], 'HIGH'):
        candidate = copy.deepcopy(manifest)
        candidate['cohort']['events'] = bad
        with check.subTest(cohort=bad), check.assertRaises(ValueError):
            calculate_offline_resource_budget(candidate, plan)


def test_oversized_mapping_refuses_before_copying_keys():
    manifest, plan = fixture()
    plan.update({f'extra{i}': None for i in range(4096)})
    tracemalloc.start()
    try:
        with unittest.TestCase().assertRaises(ValueError):
            calculate_offline_resource_budget(manifest, plan)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    # Reject from the length check before creating a set of attacker keys.
    unittest.TestCase().assertLess(peak, 64 * 1024)

    manifest, plan = fixture()
    manifest['runtime']['purpose_plan'].update(
        {f'extra{i}': None for i in range(4096)})
    tracemalloc.start()
    try:
        with unittest.TestCase().assertRaises(ValueError):
            calculate_offline_resource_budget(manifest, plan)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    unittest.TestCase().assertLess(peak, 64 * 1024)


class OfflineResourceBudgetTests(unittest.TestCase):
    def test_parity(self):
        test_arithmetic_parity_and_separate_unknown_budgets()

    def test_refusal(self):
        test_refuses_invalid_or_unbounded_projection()

    def test_fresh_ceiling(self):
        test_fresh_runtime_ceiling_is_reported_without_qualification()

    def test_journal_ceiling(self):
        test_runtime_journal_ceiling_is_visible_and_never_admission()

    def test_event_binding(self):
        test_omitted_event_is_refused()

    def test_exact_event_expansion(self):
        test_exact_event_expansion_refuses_mutations()

    def test_oversized_mapping(self):
        test_oversized_mapping_refuses_before_copying_keys()
