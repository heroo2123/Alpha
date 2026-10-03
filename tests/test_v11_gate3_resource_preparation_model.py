"""Small in-memory adverse syscall doubles; no resource reservation fixtures."""

from dataclasses import FrozenInstanceError, asdict
import hashlib
import json
from pathlib import Path
import ast
import unittest
from unittest.mock import patch

from tools import v11_gate3_resource_preparation_model as model


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii')


def fixture():
    p = dict(source_budget_sha256='a' * 64, manifest_sha256='b' * 64,
             runtime_disk_bytes=32 * 1024**2, runtime_memory_bytes=1024**2,
             v4_quota_formula_bytes=64 * 1024**2, report_bytes=model.REPORT_BYTES,
             disk_floor_bytes=model.DISK_FLOOR,
             memory_floor_bytes=model.MEMORY_FLOOR, uncovered_disk_bytes=8192,
             uncovered_memory_bytes=8192, new_inodes=3, fresh_ceiling_checks=True)
    emergency = dict(identity='emergency-inode', backing='NATIVE_KEEP_SIZE_OWNED',
                     persistence='SUPPLIED_DURABLE', available_bytes=16384,
                     memory_bytes=16384, metadata_bytes=4096, retained=True)
    disk = p['runtime_disk_bytes'] + p['v4_quota_formula_bytes'] + 8192 + 4096
    memory = p['runtime_memory_bytes'] + 8192 + 16384
    before = dict(namespace='ns', mount='mount', device='device', pool='pool',
                  quota='quota', root='root-inode', backing='LOCAL_PERSISTENT_NATIVE',
                  scope='ALL_CONSUMERS_BOUNDED', ancestry='COMPLETE_SINGLE_ANCESTOR',
                  aliases='SINGLE_MOUNT_NO_ALIASES', quota_visibility='KNOWN',
                  monitoring_bound='ENFORCED_NOT_SAMPLED',
                  free_disk_bytes=disk + model.DISK_FLOOR,
                  available_memory_bytes=memory + model.MEMORY_FLOOR,
                  quota_headroom_bytes=disk, pool_headroom_bytes=disk + model.DISK_FLOOR,
                  free_inodes=3, ancestor_headroom_bytes=memory,
                  foreign_disk_bytes=0, foreign_memory_bytes=0,
                  outstanding_disk_bytes=0, outstanding_memory_bytes=0)
    after = before.copy()
    for key in ('free_disk_bytes', 'quota_headroom_bytes', 'pool_headroom_bytes'):
        after[key] -= model.REPORT_BYTES
    owner = dict(creator_process='pid100:start1', current_process='pid100:start1',
                 boot='boot1', recorded_boot='boot1', independent_head='head1',
                 journal_head='head1', history='RECONCILED',
                 predecessor='EXITED_REAPED_ALL_HOLDERS', inherited_handles=False,
                 exclusive_owner=True)
    # Explicit double script independent of the implementation's script table.
    rows = [
        ('INTENT_WRITE', 'emergency-inode', 'NATIVE_KEEP_SIZE_OWNED', 4096, 4096),
        ('INTENT_FSYNC', 'emergency-inode', 'UNCHANGED', 0, 4096),
        ('REPORT_ALLOCATE', 'report-inode', 'NATIVE_EXCLUSIVE', 16777216, 16777216),
        ('REPORT_FSYNC', 'report-inode', 'UNCHANGED', 0, 16777216),
        ('REPORT_DIR_FSYNC', 'root-inode', 'UNCHANGED', 0, 0),
        ('REFUSAL_WRITE', 'report-inode', 'IN_PLACE', 4096, 16777216),
        ('REFUSAL_TRUNCATE', 'report-inode', 'SHRINK_SAME_INODE', 0, 4096),
        ('REFUSAL_FSYNC', 'report-inode', 'UNCHANGED', 0, 4096),
        ('REFUSAL_LINK', 'report-inode', 'SAME_INODE_NO_REPLACE', 0, 4096),
        ('REFUSAL_DIR_FSYNC', 'root-inode', 'UNCHANGED', 0, 0),
    ]
    events = [dict(op=op, outcome='OK', identity=identity, backing=backing,
                   count=count, logical_size=size)
              for op, identity, backing, count, size in rows]
    return p, dict(version=1, owner=owner, before=before, after=after,
                   emergency=emergency, report_identity='report-inode', events=events)


class PreparationTests(unittest.TestCase):
    def run_model(self, p, s):
        raw = canonical(p)
        r = model.evaluate(raw, canonical(s), hashlib.sha256(raw).hexdigest())
        self.assertEqual(r.status, 'UNQUALIFIED')
        self.assertEqual(r.g3l, 'NO_GO')
        self.assertEqual(r.qualification_credit, 0)
        self.assertEqual(r.denominator, 2713)
        self.assertEqual(r.dispatch_calls, 0)
        self.assertEqual(r.released_bytes, 0)
        for key, value in asdict(r).items():
            if key.endswith('_authority') or key in ('resource_qualification', 'constructors_allowed'):
                self.assertIs(value, False)
        return r

    def assert_no_write(self, result):
        self.assertFalse(any(':ATTEMPTED' in op for op in result.trace))

    def test_complete_trace_is_still_refusal_and_never_recredits_truncation(self):
        p, s = fixture()
        r = self.run_model(p, s)
        self.assertEqual(r.state, 'SYNTHETIC_REFUSAL_PERSISTED_HELD')
        self.assertEqual(r.reason, 'CONSTRUCTOR_ALLOCATION_MAP_UNIMPLEMENTED')
        self.assertEqual(r.held_report_bytes, model.REPORT_BYTES)
        self.assertEqual(r.outstanding_disk_bytes,
                         p['runtime_disk_bytes'] + p['v4_quota_formula_bytes'] +
                         8192 + 4096 - model.REPORT_BYTES)
        with self.assertRaises(FrozenInstanceError):
            r.status = 'QUALIFIED'
        self.assertFalse(hasattr(r, '__dict__'))

    def test_inode_roles_must_be_pairwise_distinct_with_matching_events(self):
        for alias in ('report_root', 'emergency_root', 'report_emergency', 'all_root'):
            with self.subTest(alias=alias):
                p, s = fixture()
                root = s['before']['root']
                report = s['report_identity']
                emergency = s['emergency']['identity']
                if alias in ('report_root', 'all_root'):
                    s['report_identity'] = root
                if alias in ('emergency_root', 'all_root'):
                    s['emergency']['identity'] = root
                if alias == 'report_emergency':
                    s['report_identity'] = emergency
                replacements = {report: s['report_identity'],
                                emergency: s['emergency']['identity']}
                # Keep every event consistent with its supplied inode role so
                # rejection cannot come from a mismatched syscall identity.
                for event in s['events']:
                    event['identity'] = replacements.get(event['identity'], event['identity'])
                r = self.run_model(p, s)
                self.assertEqual(r.reason, 'INPUT_INVALID')
                self.assertEqual(r.state, 'UNCERTAIN_HELD')
                self.assertEqual(r.trace, ())
                self.assertEqual(r.held_report_bytes, 0)
                self.assertEqual(r.accounting, 'NO_RELEASE_UNKNOWN_CLAIMS_RETAINED')

    def test_exact_floor_minus_one_and_stricter_manifest_floors(self):
        for key in ('free_disk_bytes', 'available_memory_bytes', 'quota_headroom_bytes',
                    'pool_headroom_bytes', 'free_inodes', 'ancestor_headroom_bytes'):
            with self.subTest(key=key):
                p, s = fixture()
                s['before'][key] -= 1
                r = self.run_model(p, s)
                self.assertEqual(r.reason, 'PROSPECTIVE_FLOOR_QUOTA_OR_INODE')
                self.assert_no_write(r)
        for key in ('disk_floor_bytes', 'memory_floor_bytes'):
            p, s = fixture()
            p[key] += 1
            self.assert_no_write(self.run_model(p, s))

    def test_constructor_attempts_before_checks_and_after_preparation_refuse(self):
        for constructor in ('MKDIR', 'CREATE_SHARED', 'CREATE_SESSION', 'CREATE_BUDGET',
                            'CREATE_STORE', 'CREATE_REPORT', 'RUNTIME_CONTEXT_WRITE',
                            'DNS', 'TLS', 'DISPATCH', 'UNLINK_RESERVE', 'RELEASE'):
            for position in (0, 2, 5, 10):
                with self.subTest(constructor=constructor, position=position):
                    p, s = fixture()
                    fake = dict(s['events'][0], op=constructor)
                    s['events'].insert(position, fake)
                    r = self.run_model(p, s)
                    self.assertNotIn(constructor + ':ATTEMPTED', r.trace)
                    self.assertIn(r.reason, ('OPERATION_ORDER_OR_CONSTRUCTOR_REFUSED',
                                            'TRAILING_OPERATION_REFUSED'))

    def test_ordering_spy(self):
        p, s = fixture()
        trace = self.run_model(p, s).trace
        self.assertLess(trace.index('READ_ONLY_RECOVERY_FENCED'),
                        trace.index('PROSPECTIVE_RESOURCES_CHECKED'))
        self.assertLess(trace.index('PROSPECTIVE_RESOURCES_CHECKED'),
                        trace.index('PREEXISTING_EMERGENCY_CHECKED'))
        self.assertLess(trace.index('PREEXISTING_EMERGENCY_CHECKED'),
                        trace.index('INTENT_WRITE:ATTEMPTED'))
        self.assertLess(trace.index('INTENT_FSYNC:SUPPLIED_OK'),
                        trace.index('REPORT_ALLOCATE:ATTEMPTED'))

    def test_emergency_missing_sparse_exhausted_and_memory_metadata(self):
        mutations = [('available_bytes', 16383), ('memory_bytes', 16383),
                     ('metadata_bytes', 4095), ('backing', 'SPARSE'),
                     ('backing', 'UNKNOWN'), ('persistence', 'UNKNOWN'), ('retained', False)]
        for key, value in mutations:
            p, s = fixture()
            s['emergency'][key] = value
            r = self.run_model(p, s)
            self.assertEqual(r.reason, 'EMERGENCY_UNPROVEN')
            self.assert_no_write(r)

    def test_emergency_enospc_edquot_and_no_recursive_refusal_allocation(self):
        for outcome in ('ENOSPC', 'EDQUOT', 'SHORT', 'UNKNOWN'):
            p, s = fixture()
            s['events'][0]['outcome'] = outcome
            r = self.run_model(p, s)
            self.assertEqual(r.reason, 'SYSCALL_INTENT_WRITE_FAILED')
            self.assertEqual(r.trace[-1], 'INTENT_WRITE:ATTEMPTED')
            self.assertEqual(r.held_report_bytes, 0)

    def test_each_syscall_failure_is_terminal_held_without_retry(self):
        for index in range(10):
            for outcome in ('ENOSPC', 'EDQUOT', 'UNSUPPORTED', 'EIO', 'UNKNOWN'):
                p, s = fixture()
                s['events'][index]['outcome'] = outcome
                r = self.run_model(p, s)
                self.assertEqual(r.state, 'UNCERTAIN_HELD')
                self.assertEqual(r.trace[-1], s['events'][index]['op'] + ':ATTEMPTED')
                self.assertEqual(r.original_reason, 'CONSTRUCTOR_ALLOCATION_MAP_UNIMPLEMENTED')

    def test_native_partial_sparse_emulated_shared_and_dummy_transfer(self):
        for key, value in [('count', model.REPORT_BYTES - 1),
                           ('logical_size', model.REPORT_BYTES - 1),
                           ('backing', 'SPARSE'), ('backing', 'LIBC_EMULATED'),
                           ('backing', 'COW_SHARED'), ('backing', 'UNLINK_REALLOCATE'),
                           ('backing', 'UNSUPPORTED'), ('identity', 'replacement-inode')]:
            p, s = fixture()
            s['events'][2][key] = value
            r = self.run_model(p, s)
            self.assertEqual(r.reason, 'ALLOCATION_OR_PERSISTENCE_SEMANTICS')
            self.assertEqual(r.held_report_bytes, 0)

    def test_keep_size_emergency_does_not_accept_length_extended_preallocation(self):
        p, s = fixture()
        s['events'][0]['logical_size'] = 16384
        self.assertEqual(self.run_model(p, s).reason, 'ALLOCATION_OR_PERSISTENCE_SEMANTICS')

    def test_short_writes_hole_punch_link_replace_and_post_truncate_crashes(self):
        for index, key, value in [(0, 'count', 4095), (5, 'count', 4095),
                                  (5, 'backing', 'HOLE_PUNCHED'),
                                  (6, 'identity', 'new-inode'),
                                  (8, 'backing', 'REPLACE_EXISTING'),
                                  (8, 'identity', 'symlink')]:
            p, s = fixture()
            s['events'][index][key] = value
            self.assertEqual(self.run_model(p, s).state, 'UNCERTAIN_HELD')
        for length in range(10):
            p, s = fixture()
            s['events'] = s['events'][:length]
            r = self.run_model(p, s)
            self.assertEqual(r.reason, 'INCOMPLETE_SYSCALL_TRACE')
            self.assertEqual(r.state, 'UNCERTAIN_HELD')

    def test_predecessor_alive_timeout_unknown_lease_expiry_not_exit(self):
        for value in ('ALIVE', 'UNKNOWN', 'TERMINATION_REQUESTED', 'TIMEOUT',
                      'LEASE_EXPIRED', 'PARENT_EXITED_CHILD_ALIVE'):
            p, s = fixture()
            s['owner']['predecessor'] = value
            r = self.run_model(p, s)
            self.assertEqual(r.reason, 'PREDECESSOR_UNFENCED')
            self.assert_no_write(r)

    def test_fork_inherited_locked_mutex_double_is_never_touched(self):
        for key, value in [('current_process', 'pid101:start2'),
                           ('current_process', 'pid100:start2'), ('inherited_handles', True)]:
            p, s = fixture()
            s['owner'][key] = value
            # A real lock would hang if acquired after a fork while held. There
            # is no callback/lock interface: even construction must be unreachable.
            with patch('threading.Lock', side_effect=AssertionError('inherited mutex touched')):
                r = self.run_model(p, s)
            self.assertEqual(r.reason, 'INHERITED_OWNER')
            self.assertEqual(r.trace, ())

    def test_history_rollback_missing_boot_and_two_owners(self):
        for key, value in [('journal_head', 'old-head'), ('history', 'MISSING'),
                           ('history', 'NEW_EMPTY_DIRECTORY'), ('recorded_boot', 'old-boot'),
                           ('exclusive_owner', False)]:
            p, s = fixture()
            s['owner'][key] = value
            self.assert_no_write(self.run_model(p, s))

    def test_bounded_foreign_scope_aliases_quota_and_namespace(self):
        for key in ('scope', 'ancestry', 'aliases', 'quota_visibility', 'monitoring_bound', 'backing'):
            p, s = fixture()
            s['before'][key] = 'UNKNOWN'
            r = self.run_model(p, s)
            self.assertEqual(r.reason, 'DOMAIN_SCOPE_UNKNOWN')
            self.assert_no_write(r)
        for value in ('TMPFS', 'SHARED_POOL', 'COW', 'NFS'):
            p, s = fixture()
            s['before']['backing'] = value
            self.assert_no_write(self.run_model(p, s))
        for key in ('foreign_disk_bytes', 'foreign_memory_bytes',
                    'outstanding_disk_bytes', 'outstanding_memory_bytes'):
            p, s = fixture()
            s['before'][key] = 1
            self.assert_no_write(self.run_model(p, s))

    def test_post_allocation_loss_keeps_claims_and_no_constructor(self):
        for key in ('namespace', 'mount', 'device', 'pool', 'quota', 'root', 'aliases'):
            p, s = fixture()
            s['after'][key] = 'changed'
            r = self.run_model(p, s)
            self.assertEqual(r.reason, 'POST_ALLOCATION_CUSTODY_LOST')
            self.assertEqual(r.held_report_bytes, model.REPORT_BYTES)
            self.assertNotIn('REFUSAL_WRITE:ATTEMPTED', r.trace)
        for key in ('free_disk_bytes', 'available_memory_bytes', 'quota_headroom_bytes'):
            p, s = fixture()
            s['after'][key] -= 1
            self.assertEqual(self.run_model(p, s).reason, 'POST_ALLOCATION_CUSTODY_LOST')

    def test_closed_bounded_parser_and_replay(self):
        p, s = fixture()
        raw = canonical(p)
        pin = hashlib.sha256(raw).hexdigest()
        good = canonical(s)
        bad = [b'', good + b' ', b'{"version":1,"version":1}',
               b'[' * 7 + b'0' + b']' * 7, b'{}' * model.MAX_BYTES,
               b'{"x":NaN}', b'{"x":1.5}', b'{"x":-1}',
               b'{"x":9223372036854775808}', b'\xff']
        for value in bad:
            r = model.evaluate(raw, value, pin)
            self.assertEqual(r.reason, 'INPUT_INVALID')
            self.assertEqual(r.trace, ())
        self.assertEqual(model.evaluate(raw, good, 'c' * 64).reason, 'INPUT_INVALID')
        expected = self.run_model(p, s)
        for _ in range(100):
            self.assertEqual(self.run_model(p, s), expected)

    def test_closed_keys_bool_integer_huge_events_overflow(self):
        for location, key, value in [('owner', 'unknown', 1), ('before', 'free_inodes', True),
                                      ('events', 'unused', 0)]:
            p, s = fixture()
            if location == 'events':
                s['events'] *= 2
            else:
                s[location][key] = value
            self.assertEqual(self.run_model(p, s).reason, 'INPUT_INVALID')
        p, s = fixture()
        p['uncovered_disk_bytes'] = model.MAX_INT
        self.assertEqual(self.run_model(p, s).reason, 'INPUT_INVALID')
        p, s = fixture()
        p['fresh_ceiling_checks'] = False
        self.assert_no_write(self.run_model(p, s))

    def test_projection_from_current_offline_calculator(self):
        # Only the existing pure arithmetic fixture runs; no manifest validation,
        # filesystem constructors or concurrent export implementation is invoked.
        from test_v11_gate3_offline_resource_budget import fixture as budget_fixture
        from tools.v11_gate3_offline_resource_budget import calculate_offline_resource_budget
        manifest, plan = budget_fixture()
        budget = calculate_offline_resource_budget(manifest, plan)
        p, s = fixture()
        p.update(source_budget_sha256=hashlib.sha256(canonical(budget)).hexdigest(),
                 manifest_sha256=hashlib.sha256(canonical(manifest)).hexdigest(),
                 runtime_disk_bytes=budget['runtime_capacity']['disk_bytes'],
                 runtime_memory_bytes=budget['runtime_capacity']['memory_bytes'],
                 v4_quota_formula_bytes=budget['v4_quota_formula_bytes'],
                 fresh_ceiling_checks=all(budget['fresh_ceiling_checks'].values()))
        for domain in ('before', 'after'):
            for key in ('free_disk_bytes', 'available_memory_bytes', 'quota_headroom_bytes',
                        'pool_headroom_bytes', 'ancestor_headroom_bytes'):
                s[domain][key] += 1024**3
        r = self.run_model(p, s)
        self.assertEqual(r.state, 'SYNTHETIC_REFUSAL_PERSISTED_HELD')
        self.assertEqual(r.outstanding_disk_bytes,
                         budget['runtime_capacity']['disk_bytes'] +
                         budget['v4_quota_formula_bytes'] + 8192 + 4096 - model.REPORT_BYTES)

    def test_unknown_identity_and_post_arithmetic_overflow_fail_before_script(self):
        for section, key in [('before', 'namespace'), ('after', 'quota'),
                             ('owner', 'current_process'), ('owner', 'independent_head')]:
            p, s = fixture()
            s[section][key] = 'UNKNOWN'
            r = self.run_model(p, s)
            self.assertEqual(r.reason, 'INPUT_INVALID')
            self.assert_no_write(r)
        p, s = fixture()
        s['after']['foreign_disk_bytes'] = model.MAX_INT
        r = self.run_model(p, s)
        self.assertEqual(r.reason, 'INPUT_INVALID')
        self.assert_no_write(r)

    def test_no_host_or_runtime_imports_or_reachable_syscalls(self):
        tree = ast.parse(Path(model.__file__).read_text())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module)
        self.assertEqual(imports, {'dataclasses', 'hashlib', 'json'})
        p, s = fixture()
        with patch('builtins.open', side_effect=AssertionError('filesystem')), \
             patch('os.open', side_effect=AssertionError('filesystem')), \
             patch('socket.socket', side_effect=AssertionError('network')), \
             patch('time.time', side_effect=AssertionError('host clock')), \
             patch('os.posix_fallocate', side_effect=AssertionError('allocator')):
            self.assertEqual(self.run_model(p, s).state, 'SYNTHETIC_REFUSAL_PERSISTED_HELD')


if __name__ == '__main__':
    unittest.main()
