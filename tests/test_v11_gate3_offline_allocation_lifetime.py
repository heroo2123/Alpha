"""Synthetic checks only; no host, allocator, provider or network interaction."""

from dataclasses import replace
import copy
import hashlib
import tracemalloc
import unittest

from tests import test_v11_gate3_offline_resource_budget as resource_tests
from tools.v11_gate3_offline_allocation_lifetime import (
    Domain, FRESH_CHECKS, Obligation, REQUIRED, reconcile,
)


def fixture():
    # A fabricated stand-in for the already validated calculator result. It
    # tests reconciliation behavior, not authenticity of manifest validation.
    budget = {
        'mode': 'OFFLINE_PROPOSAL',
        'manifest_validation': 'V4_VALIDATED_EXACT_BYTES',
        'event_binding': 'MATCHES_VALIDATED_V4_MANIFEST_FIELD_EXPANSION',
        'capacity_covers_frozen_schedule': True,
        'validated_manifest_sha256': 'a' * 64,
        'v4_quota_formula_bytes': 100,
        'v4_local_storage_quota_bytes': 100,
        'runtime_capacity': {'disk_bytes': 80, 'memory_bytes': 60},
        'fresh_ceiling_checks': dict.fromkeys(FRESH_CHECKS, True),
        'resource_qualification': False, 'execution_authority': False,
        'provider_authority': False, 'qualification_credit': 0,
        'g3l': 'NO_GO',
        'existing_occupancy': 'UNKNOWN', 'live_host_resources': 'UNKNOWN',
    }
    rows = []
    for category, (medium, _) in REQUIRED.items():
        size = (100 if category == 'v4_envelope' else
                80 if category == 'runtime_envelope' else
                60 if medium == 'MEMORY' else 1)
        rows.append(Obligation(
            category=category, domain='disk' if medium == 'DISK' else 'memory',
            owner='synthetic-custodian', backing_claim='claim-' + category,
            start=1, end=10, maximum_bytes=size,
            maximum_inodes=1 if medium == 'DISK' else 0,
            materialized_bytes=0, materialized_inodes=0,
            outstanding_bytes=size, outstanding_inodes=1 if medium == 'DISK' else 0,
            materialized_at=None, outstanding_at=6))
    disk_bytes = sum(r.maximum_bytes for r in rows if r.domain == 'disk')
    disk_inodes = sum(r.maximum_inodes for r in rows if r.domain == 'disk')
    domains = [Domain('disk', 'DISK', 'disk-pool', disk_bytes, disk_inodes),
               Domain('memory', 'MEMORY', 'memory-pool', 60, 0)]
    return budget, rows, domains


class AllocationLifetimeTests(unittest.TestCase):
    def test_validated_calculator_contract_at_exact_disk_boundary(self):
        # Reuse the canonical V4 bytes and validation context from the calculator
        # suite; the allocation claims and ceilings remain synthetic assertions.
        source = resource_tests.ValidatedBytesResourceBudgetTests
        source.setUpClass()
        try:
            budget = source('test_valid_fixture_exact_digest_and_zero_authority').estimate()
            expected_digest = hashlib.sha256(source.raw).hexdigest()
        finally:
            source.tearDownClass()

        _, rows, _ = fixture()
        envelopes = {
            'v4_envelope': budget['v4_quota_formula_bytes'],
            'runtime_envelope': budget['runtime_capacity']['disk_bytes'],
            'parent_child_memory': budget['runtime_capacity']['memory_bytes'],
        }
        rows = [replace(row, maximum_bytes=envelopes.get(row.category, row.maximum_bytes),
                        outstanding_bytes=envelopes.get(row.category, row.outstanding_bytes))
                for row in rows]
        disk_rows = [row for row in rows if row.domain == 'disk']
        disk_bytes = sum(row.maximum_bytes for row in disk_rows)
        disk_inodes = sum(row.maximum_inodes for row in disk_rows)
        domains = [Domain('disk', 'DISK', 'disk-pool', disk_bytes, disk_inodes),
                   Domain('memory', 'MEMORY', 'memory-pool',
                          envelopes['parent_child_memory'], 0)]

        self.assertEqual(budget['validated_manifest_sha256'], expected_digest)
        result = reconcile(budget, rows, domains, snapshot=5)
        self.assertEqual(result['status'], 'UNQUALIFIED')
        self.assertEqual(result['manifest_sha256'], expected_digest)
        self.assertEqual(result['lifetime_peaks']['disk']['bytes'], disk_bytes)
        self.assertEqual(result['lifetime_peaks']['disk']['inodes'], disk_inodes)
        self.assertEqual(result['g3l'], 'NO_GO')
        self.assertEqual(result['qualification_credit'], 0)
        for key in ('resource_qualification', 'execution_authority',
                    'provider_authority', 'capture_authority', 'host_authority',
                    'selected_window_evidence'):
            self.assertIs(result[key], False)

        for field in ('peak_bytes_ceiling', 'peak_inodes_ceiling'):
            with self.subTest(ceiling=field), self.assertRaisesRegex(
                    ValueError, 'overlapping lifetimes'):
                lowered = replace(domains[0], **{field: getattr(domains[0], field) - 1})
                reconcile(budget, rows, [lowered, domains[1]], snapshot=5)

        for mutate in (
                lambda b: b.update(manifest_validation='DECLARED'),
                lambda b: b['fresh_ceiling_checks'].update(store_objects=False)):
            changed = copy.deepcopy(budget)
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                reconcile(changed, rows, domains, snapshot=5)

    def test_full_coverage_partial_materialization_and_zero_authority(self):
        budget, rows, domains = fixture()
        envelope = rows[0]
        rows[0] = replace(envelope, materialized_bytes=40, outstanding_bytes=60,
                          materialized_inodes=1, outstanding_inodes=0,
                          materialized_at=4)
        result = reconcile(budget, rows, domains, snapshot=5)
        self.assertEqual(result['status'], 'UNQUALIFIED')
        self.assertEqual(result['g3l'], 'NO_GO')
        self.assertEqual(result['qualification_credit'], 0)
        self.assertFalse(result['resource_qualification'])
        self.assertFalse(result['execution_authority'])
        self.assertFalse(result['provider_authority'])
        self.assertFalse(result['capture_authority'])
        self.assertFalse(result['host_authority'])
        self.assertFalse(result['selected_window_evidence'])
        self.assertEqual(result['lifetime_peaks']['disk']['bytes'],
                         domains[0].peak_bytes_ceiling)
        self.assertEqual(result['lifetime_peaks']['disk']
                         ['outstanding_after_snapshot']['bytes'],
                         domains[0].peak_bytes_ceiling - 40)

    def test_missing_categories_and_budget_envelopes_refuse(self):
        budget, rows, domains = fixture()
        with self.assertRaisesRegex(ValueError, 'missing allocation categories'):
            reconcile(budget, rows[:-1], domains, snapshot=5)
        rows[0] = replace(rows[0], maximum_bytes=99, outstanding_bytes=99)
        with self.assertRaisesRegex(ValueError, 'budget envelope'):
            reconcile(budget, rows, domains, snapshot=5)

    def test_duplicate_backing_and_pool_alias_refuse(self):
        budget, rows, domains = fixture()
        rows[1] = replace(rows[1], backing_claim=rows[0].backing_claim)
        with self.assertRaisesRegex(ValueError, 'duplicate backing'):
            reconcile(budget, rows, domains, snapshot=5)
        budget, rows, domains = fixture()
        domains.append(Domain('disk-alias', 'DISK', 'disk-pool', 1000, 10))
        with self.assertRaisesRegex(ValueError, 'backing pool'):
            reconcile(budget, rows, domains, snapshot=5)

    def test_snapshot_order_and_unreconciled_partial_allocation_refuse(self):
        budget, rows, domains = fixture()
        for changed in (
                replace(rows[2], outstanding_at=5),
                replace(rows[2], materialized_bytes=1, outstanding_bytes=0,
                        materialized_at=6, outstanding_at=None),
                replace(rows[2], materialized_bytes=1, outstanding_bytes=0,
                        materialized_at=4, outstanding_at=None),
                replace(rows[2], end=5)):
            candidate = rows.copy()
            candidate[2] = changed
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                reconcile(budget, candidate, domains, snapshot=5)

    def test_overlapping_peak_checked_for_bytes_and_inodes(self):
        budget, rows, domains = fixture()
        disk = domains[0]
        with self.assertRaisesRegex(ValueError, 'overlapping lifetimes'):
            reconcile(budget, rows,
                      [replace(disk, peak_bytes_ceiling=disk.peak_bytes_ceiling - 1),
                       domains[1]], snapshot=5)
        with self.assertRaisesRegex(ValueError, 'overlapping lifetimes'):
            reconcile(budget, rows,
                      [replace(disk, peak_inodes_ceiling=disk.peak_inodes_ceiling - 1),
                       domains[1]], snapshot=5)
        # Ordinary uncovered rows may use half-open lifetime accounting;
        # the full V4 and runtime envelopes remain concurrent.
        for index, row in enumerate(rows):
            if row.domain == 'disk' and row.category not in ('v4_envelope',
                                                              'runtime_envelope'):
                rows[index] = replace(row, start=11 + index, end=12 + index,
                                      outstanding_at=11 + index)
        result = reconcile(budget, rows, domains, snapshot=5)
        self.assertEqual(result['lifetime_peaks']['disk']['bytes'], 180)

    def test_sequential_envelope_fragments_cannot_cover_capacity(self):
        budget, rows, _ = fixture()
        fragmented = []
        for index, row in enumerate(rows):
            if row.category in ('v4_envelope', 'runtime_envelope',
                                'parent_child_memory'):
                base = {'v4_envelope': 20, 'runtime_envelope': 40,
                        'parent_child_memory': 60}[row.category]
                size = row.maximum_bytes // 10
                for part in range(10):
                    start = base + part
                    fragmented.append(replace(
                        row, backing_claim=f'{row.backing_claim}-{part}',
                        start=start, end=start + 1, maximum_bytes=size,
                        outstanding_bytes=size, outstanding_at=start))
            else:
                start = 100 + index
                fragmented.append(replace(row, start=start, end=start + 1,
                                          outstanding_at=start))
        domains = [Domain('disk', 'DISK', 'disk-pool', 10, 1),
                   Domain('memory', 'MEMORY', 'memory-pool', 6, 0)]
        with self.assertRaisesRegex(ValueError, 'budget envelope'):
            reconcile(budget, fragmented, domains, snapshot=5)

    def test_whole_envelopes_must_overlap_without_phase_proof(self):
        budget, rows, domains = fixture()
        rows[0] = replace(rows[0], start=10, end=11, outstanding_at=10)
        rows[1] = replace(rows[1], start=11, end=12, outstanding_at=11)
        with self.assertRaisesRegex(ValueError, 'budget envelope'):
            reconcile(budget, rows, domains, snapshot=5)

    def test_concurrent_envelope_parts_cover_capacity(self):
        budget, rows, domains = fixture()
        domains[0] = replace(domains[0],
                             peak_inodes_ceiling=domains[0].peak_inodes_ceiling + 2)
        for index, row in reversed(tuple(enumerate(rows))):
            if row.category in ('v4_envelope', 'runtime_envelope',
                                'parent_child_memory'):
                part = row.maximum_bytes // 2
                rows[index:index + 1] = [
                    replace(row, backing_claim=f'{row.backing_claim}-{suffix}',
                            maximum_bytes=part, outstanding_bytes=part,
                            maximum_inodes=1 if row.domain == 'disk' else 0,
                            outstanding_inodes=1 if row.domain == 'disk' else 0)
                    for suffix in (0, 1)]
        result = reconcile(budget, rows, domains, snapshot=5)
        self.assertEqual(result['status'], 'UNQUALIFIED')
        self.assertEqual(result['lifetime_peaks']['disk']['bytes'],
                         domains[0].peak_bytes_ceiling)

    def test_excess_freshness_keys_refused_before_copy(self):
        budget, rows, domains = fixture()
        budget['fresh_ceiling_checks'].update(
            {f'extra_{i}': True for i in range(32768)})
        tracemalloc.start()
        try:
            with self.assertRaises(ValueError):
                reconcile(budget, rows, domains, snapshot=5)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 256_000)

    def test_marker_and_medium_subclasses_refused_without_hooks(self):
        class Hooked(str):
            calls = 0
            __hash__ = str.__hash__

            def __ne__(self, other):
                type(self).calls += 1
                return False

            def __eq__(self, other):
                type(self).calls += 1
                return True

        budget, rows, domains = fixture()
        budget['mode'] = Hooked('NOT_AN_OFFLINE_PROPOSAL')
        with self.assertRaises(ValueError):
            reconcile(budget, rows, domains, snapshot=5)
        self.assertEqual(Hooked.calls, 0)
        budget, rows, domains = fixture()
        domains[0] = replace(domains[0], medium=Hooked('DISK'))
        with self.assertRaises(ValueError):
            reconcile(budget, rows, domains, snapshot=5)
        self.assertEqual(Hooked.calls, 0)
        budget, rows, domains = fixture()
        budget[Hooked('extra')] = True
        with self.assertRaises(ValueError):
            reconcile(budget, rows, domains, snapshot=5)
        self.assertEqual(Hooked.calls, 0)

    def test_invalid_budget_or_arithmetic_has_no_result(self):
        budget, rows, domains = fixture()
        for field, value in (('manifest_validation', 'DECLARED'),
                             ('resource_qualification', True),
                             ('g3l', 'PASS'),
                             ('existing_occupancy', 'KNOWN')):
            changed = copy.deepcopy(budget)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                reconcile(changed, rows, domains, snapshot=5)
        rows[2] = replace(rows[2], maximum_bytes=True)
        with self.assertRaises(ValueError):
            reconcile(budget, rows, domains, snapshot=5)


if __name__ == '__main__':
    unittest.main()
