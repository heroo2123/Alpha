"""Offline IA1 document checks, not a wire validator or runtime implementation.

Run directly under normal and optimized Python. Only local public Git objects
and fixed candidate files are read; the retained /tmp adjudication is optional
for portable reproduction. No project implementation is imported.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import unittest

from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[1]
STEM = 'docs/V11_GATE3_V5_FC1_INTERFACE_AMENDMENT_1_20261003'
OLD = 'docs/V11_GATE3_V5_FULL_COHORT_EXECUTION_CONTRACT_20261003'
BASE = '480793cbe4a9832ebf1ffcd95c17d12a1bd333e5'
CHECKER = 'tests/verify_v11_fc1_ia1_documents.py'
CHANGED = ['FC01', 'FC05', 'FC06', 'FC11', 'FC12', 'FC13', 'FC14',
           'FC16', 'FC17', 'FC18', 'FC24']


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def parse(raw):
    def invalid_constant(value):
        raise ValueError('nonfinite JSON number: ' + value)
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)


def local(path):
    require(isinstance(path, str) and not Path(path).is_absolute()
            and '..' not in Path(path).parts
            and path.startswith(('docs/', 'tests/', 'tools/', 'config/',
                                 'polymarket_scanner/')), 'nonpublic path')
    return ROOT / path


def git(*args):
    env = {'PATH': os.defpath, 'LC_ALL': 'C', 'GIT_NO_REPLACE_OBJECTS': '1',
           'GIT_NO_LAZY_FETCH': '1', 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_ALLOW_PROTOCOL': ''}
    return subprocess.run(['git', '-c', 'protocol.allow=never', '-C', str(ROOT),
                           *args], env=env, check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=15).stdout


def source_bytes(row):
    local(row['path'])
    require(re.fullmatch('[0-9a-f]{40}', row['commit']) is not None, 'commit')
    return git('show', '--no-textconv', row['commit'] + ':' + row['path'])


def verify_pin(row, raw):
    require(type(row['byte_length']) is int and len(raw) == row['byte_length'],
            'length mismatch: ' + row['path'])
    require(hashlib.sha256(raw).hexdigest() == row['sha256'],
            'digest mismatch: ' + row['path'])
    if 'git_blob' in row:
        require(hashlib.sha1(f'blob {len(raw)}\0'.encode() + raw).hexdigest()
                == row['git_blob'], 'Git blob mismatch')


class Documents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v = parse(local(STEM + '.verification.json').read_bytes())
        cls.s = parse(local(STEM + '.sources.json').read_bytes())
        cls.schema = parse(local(STEM + '.schema.json').read_bytes())
        cls.old = parse(local(OLD + '.verification.json').read_bytes())
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema)

    def test_closed_document_schema(self):
        self.validator.validate(self.v)
        self.validator.validate(self.s)
        # This schema has no resolver or runtime schema dependencies.
        self.assertNotIn('"$ref"', json.dumps(self.schema))
        for document in (self.v, self.s):
            altered = copy.deepcopy(document)
            altered['unknown'] = 1
            with self.assertRaises(ValidationError):
                self.validator.validate(altered)
        for value in (True, -1, 2**63, 1.5):
            altered = copy.deepcopy(self.v)
            altered['read_witness']['frozen_k'] = value
            with self.assertRaises(ValidationError):
                self.validator.validate(altered)
        altered = copy.deepcopy(self.v)
        del altered['future_matrix'][0]['owner']
        with self.assertRaises(ValidationError):
            self.validator.validate(altered)

    def test_strict_json(self):
        for raw in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'):
            with self.assertRaises(ValueError):
                parse(raw)
        for suffix in ('.sources.json', '.verification.json', '.schema.json'):
            raw = local(STEM + suffix).read_bytes()
            self.assertEqual(raw, (json.dumps(parse(raw), sort_keys=True,
                                             indent=2) + '\n').encode())

    def test_exact_sources_and_preserved_originals(self):
        self.assertEqual(self.s['baseline_commit'], BASE)
        self.assertEqual(self.v['baseline_commit'], BASE)
        self.assertEqual(git('rev-parse', BASE + '^{tree}').decode().strip(),
                         self.s['baseline_tree'])
        original_sources = parse(local(OLD + '.sources.json').read_bytes())['sources']
        self.assertEqual(self.s['sources'][:len(original_sources)], original_sources)
        identities = [(r['commit'], r['path']) for r in self.s['sources']]
        self.assertEqual(len(identities), len(set(identities)))
        for row in self.s['sources']:
            with self.subTest(source=row['path'], commit=row['commit']):
                raw = source_bytes(row)
                verify_pin(row, raw)
                if row['commit'] == BASE:
                    self.assertEqual(local(row['path']).read_bytes(), raw)
        verify_pin({'path': OLD + '.md', **self.old['candidate_contract']},
                   local(OLD + '.md').read_bytes())

    def test_candidate_pins_links_and_adjudication(self):
        expected = {STEM + x for x in ('.md', '.verification.json', '.schema.json')}
        expected.add(CHECKER)
        self.assertEqual({p['path'] for p in self.s['candidate_files']}, expected)
        self.assertEqual(len(self.s['candidate_files']), len(expected))
        for row in self.s['candidate_files']:
            verify_pin(row, local(row['path']).read_bytes())
        verify_pin(self.v['candidate_contract'], local(STEM + '.md').read_bytes())
        adjudication = self.s['retained_adjudication']
        self.assertEqual(adjudication['local_path'],
                         '/tmp/alpha-v11-v5-fc1-interface-adjudication.final')
        retained = Path(adjudication['local_path'])
        if retained.exists():
            verify_pin({'path': adjudication['local_path'], **adjudication},
                       retained.read_bytes())
        else:
            print('Retained adjudication unavailable locally; Git pins remain replayable.')
        doc = local(STEM + '.md')
        for target in re.findall(r'\]\(([^)]+)\)', doc.read_text()):
            self.assertTrue((doc.parent / target).is_file(), target)

    def test_pin_mutations_refuse(self):
        row = self.v['candidate_contract']
        raw = local(row['path']).read_bytes()
        for bad in (raw + b'\n', b'X' + raw[1:]):
            with self.assertRaises(ValueError):
                verify_pin(row, bad)
        with self.assertRaises(ValueError):
            local('../private')

    def test_full_matrix_and_identity_preservation(self):
        rows = self.v['future_matrix']
        self.assertEqual([r['id'] for r in rows], [f'FC{i:02}' for i in range(1, 25)])
        self.assertEqual(self.v['changed_cases'], CHANGED)
        self.assertEqual(self.v['inherited_g3l_preservation'],
                         self.old['inherited_g3l_preservation'])
        self.assertEqual(len(self.v['inherited_g3l_preservation']), 79)
        for before, after in zip(self.old['future_matrix'], rows):
            if after['id'] not in CHANGED:
                self.assertEqual(before, after)
            else:
                original_mutations = before['mutations']
                if after['id'] == 'FC16':
                    original_mutations = [m for m in original_mutations if
                        m['change'] != '65th read when frozen k=64']
                for mutation in original_mutations:
                    self.assertIn(mutation, after['mutations'])
            self.assertEqual(after['execution_status'], 'FUTURE_NOT_RUN')
            self.assertEqual(after['preserve_on_refusal'], before['preserve_on_refusal'])
            self.assertEqual(len({m['change'] for m in after['mutations']}),
                             len(after['mutations']))

    def test_structural_arithmetic_and_journals(self):
        w = self.v['structural_witness']
        self.assertEqual(w, self.old['structural_witness'])
        n = 31 * 25 + 51 * 25 + 51 * 13
        self.assertEqual(n, 2713)
        self.assertEqual(w['roles'], 3 * n)
        self.assertEqual(775 * 2**21 + 1938 * 2**22, 9753853952)
        self.assertEqual(w['body_reservation_bytes'], n * 2**18)
        self.assertEqual(w['body_plus_abort_bytes'], n * 2**18 + 2**16)
        self.assertLessEqual(w['body_plus_abort_bytes'], 2**30)
        objects = n + 64 + 24 + 2
        self.assertEqual(w['final_objects'], objects)
        self.assertEqual(w['peak_objects'], objects + 1)
        counts = {'budget': 4*n+4, 'session': 12*n+4,
                  'denial': 4*n+4, 'store': 3*objects+4}
        sizes = {'budget': counts['budget']*1024, 'session': counts['session']*2048,
                 'denial': counts['denial']*4096,
                 'store': objects*(8192+8192+1024)+4*1024+2**24}
        self.assertEqual(w['journal_events'], counts)
        self.assertEqual(w['journal_bytes'], sizes)
        for key, limit in [('budget', 131072), ('session', 32768),
                           ('denial', 32768), ('store', 10000)]:
            self.assertLessEqual(counts[key], limit)
            self.assertLessEqual(sizes[key], 2**26)
        self.assertEqual(32768-counts['session'], 208)
        self.assertEqual(2**26-sizes['session'], 425984)
        self.assertLessEqual((12*(n+17)+4)*2048, 2**26)
        self.assertGreater((12*(n+18)+4)*2048, 2**26)

    def test_pacing_bound_and_deadline_counterexamples(self):
        w, p = self.v['structural_witness'], self.v['pacing_witness']
        n, d, s = w['request_count'], w['deadline_ms'], w['spacing_ms']
        self.assertEqual(p['start_bound_total_ms'], n*p['start_bound_each_ms'])
        self.assertEqual(p['dispatch_bound_total_ms'], p['start_bound_total_ms'])
        self.assertEqual(2*p['start_bound_total_ms']+p['other_jitter_ms'], w['jitter_ms'])
        tail = sum(w[k] for k in ('local_wall_ms','finalization_ms','jitter_ms','clock_guard_ms'))
        total = (n-1)*max(s,d)+d+tail
        self.assertEqual(total, 9586000)
        self.assertEqual(total, w['elapsed_bound_ms'])
        self.assertEqual(10800000-total, p['slack_ms'])
        self.assertLessEqual(total+p['slack_ms'], 10800000)
        self.assertGreater(total+p['slack_ms']+1, 10800000)
        self.assertEqual(n*d+(n-1)*s+tail, p['legacy_close_plus_spacing_ms'])
        self.assertGreater(p['legacy_close_plus_spacing_ms'], 10800000)
        # Finite arithmetic counterexamples only: no scheduler implementation.
        reservation, actual, upper = 0, 1500, 1550
        self.assertLess(reservation+s-actual, s)
        self.assertGreaterEqual(upper+s-actual, s)
        self.assertLess(upper+s-1, upper+s)
        fixed_deadline, delayed_permission = 2000, 2001
        self.assertGreater(delayed_permission, fixed_deadline)
        for life in (1, 1999, 2000, 2001, 30000):
            for bracket in (0, 1, 50, 2001):
                self.assertLessEqual(max(life, s+bracket), max(life,s)+bracket)

    def test_counted_eof_boundaries(self):
        w = self.v['read_witness']
        payload = (w['cap_bytes'] + w['read_max_bytes']-1)//w['read_max_bytes']
        self.assertEqual(payload, w['payload_calls'])
        self.assertEqual(w['eof_calls'], 1)
        self.assertEqual(payload+1, w['frozen_k'])
        self.assertEqual(w['frozen_k'], 65)
        self.assertLess(64, payload+1)
        for cap, lower in ((1, 2),(65536, 2),(65537, 3),(4194304, 65)):
            self.assertEqual((cap+65535)//65536+1, lower)
            self.assertLessEqual(lower, min(cap+1,65536))
        # One-byte positive reads of a 65-byte body can exhaust k=65
        # with no remaining EOF slot. A lower-bound admission is not success.
        self.assertGreater(65+1, 65)

    def test_holds_and_seam_matrix_coverage(self):
        self.assertEqual(self.v['status'], 'PROPOSED_BLOCKED')
        self.assertEqual(self.v['qualification_credit'], 0)
        self.assertFalse(self.v['author_checks']['future_matrix_executed'])
        for key in ('structural_witness','pacing_witness','read_witness'):
            self.assertEqual(self.v[key]['qualification_credit'], 0)
        matrix = {r['id']:r for r in self.v['future_matrix']}
        required = {
            'FC05': ['delayed reservation fsync', 'delayed intent fsync', 'u_i+1999ms', 'slow close'],
            'FC06': ['start-bound slack', 'EOF probe'],
            'FC13': ['receipt before terminal', 'preterminal hash', 'outcome differs', 'duplicate capture', 'fsync failure'],
            'FC16': ['frozen k=64', 'call k_i+1', 'without final EOF', 'short reads', 'extra queued', 'read after cancellation'],
            'FC17': ['OBJECT_WITNESSED before TERMINAL', 'TERMINAL before CAPTURE_RECEIPT', 'before acknowledgement'],
            'FC18': ['fresh clock', 'missing receipt', 'resets request']}
        for case, terms in required.items():
            changes = '\n'.join(m['change'] for m in matrix[case]['mutations'])
            for term in terms:
                self.assertIn(term, changes)


if __name__ == '__main__':
    unittest.main(verbosity=2)
