"""Synthetic checks only; no host, allocator, provider or network interaction."""

from dataclasses import replace
import copy
import unittest

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
        # All rows survive the snapshot, but sequential future intervals need
        # only their actual concurrent peak, not the sum of all disk rows.
        for index, row in enumerate(rows):
            if row.domain == 'disk':
                rows[index] = replace(row, start=6 + index, end=7 + index,
                                      outstanding_at=6 + index)
        result = reconcile(budget, rows, domains, snapshot=5)
        self.assertEqual(result['lifetime_peaks']['disk']['bytes'], 100)

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
