"""Offline exact-commit probes. No filesystem writes; mutations use in-memory mocks."""
import base64
import contextlib
import copy
import csv
import email
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path('/tmp/alpha-v11-gate3-a2a3-review-89f85ac')
STEM = 'docs/V11_R09_GATE3_A2A3_RETAINED_AUDIT_20261001'
AUDIT = json.loads((ROOT / (STEM + '.json')).read_text())
DOSSIER_PATH = ROOT / 'docs/V11_R09_GATE3_A2A3_OFFLINE_DOSSIER_20261001.json'
DOSSIER = json.loads(DOSSIER_PATH.read_text())
TERMINAL = json.loads((ROOT / (STEM + '_terminal.json')).read_text())
TOOL = ROOT / 'tools/v11_r09_gate3_a2a3_retained_audit.py'
spec = importlib.util.spec_from_file_location('candidate_audit', TOOL)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def run(*args):
    return subprocess.run(args, cwd=ROOT, check=True, capture_output=True,
                          env={**os.environ, 'LC_ALL': 'C', 'PYTHONDONTWRITEBYTECODE': '1'}).stdout


def identity(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest(), Path(path).stat().st_size


def recorded(value):
    algorithm, payload = value.split('=', 1)
    assert algorithm == 'sha256'
    return base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)).hex()


class RetainedReview(unittest.TestCase):
    def test_01_exact_commit_and_artifact_bindings(self):
        self.assertEqual(run('git', 'rev-parse', 'HEAD').decode().strip(), '89f85acdb973fb2c7cceafbcf3bb0cb1848c7ead')
        self.assertEqual(run('git', 'rev-parse', 'HEAD^{tree}').decode().strip(), '9ff38fb832c2b68836e81ccdc03eed409f36283b')
        self.assertEqual(run('git', 'status', '--porcelain'), b'')
        self.assertEqual(run('git', 'diff', '--check', '6ce37ab..89f85ac'), b'')
        self.assertEqual(run('git', 'rev-parse', TERMINAL['content_commit'] + '^{tree}').decode().strip(), TERMINAL['content_tree'])
        for path, claim in TERMINAL['artifacts'].items():
            self.assertEqual(identity(ROOT / path), (claim['sha256'], claim['bytes']))
            self.assertEqual(run('git', 'show', TERMINAL['content_commit'] + ':' + path), (ROOT / path).read_bytes())
        self.assertEqual(identity(DOSSIER_PATH)[0], AUDIT['historical_dossier_sha256'])
        self.assertEqual(DOSSIER_PATH.read_bytes(), Path('/tmp/alpha-v11-gate3-a2a3-dossier-20261001.json').read_bytes())
        compile(TOOL.read_bytes(), str(TOOL), 'exec')

    def test_02_fresh_generator_equality(self):
        fresh = json.loads(run(sys.executable, '-B', str(TOOL)))
        expected = copy.deepcopy(AUDIT)
        fresh.pop('observed_utc'); expected.pop('observed_utc')
        self.assertEqual(fresh, expected)

    def test_03_all_75_payloads_and_record_rows(self):
        self.assertEqual(len(AUDIT['record_discrepancies']), 25)
        self.assertEqual(len({r['path'] for r in AUDIT['record_discrepancies']}), 25)
        old = {r['path']: r for r in DOSSIER['record_discrepancies']}
        for label, site in mod.SITES.items():
            record = site / 'eckitlib-2.3.0.30.dist-info/RECORD'
            self.assertEqual(identity(record)[0], AUDIT['record_copies'][label])
            rows = list(csv.reader(io.StringIO(record.read_text())))
            self.assertEqual(len(rows), len({r[0] for r in rows}))
            table = {r[0]: r for r in rows}
            for claim in AUDIT['record_discrepancies']:
                name = claim['path']; row = table[name]; actual = identity(site / name)
                self.assertEqual((recorded(row[1]), int(row[2])), (claim['record_sha256'], claim['record_bytes']))
                self.assertEqual(actual, (claim['copies'][label]['sha256'], claim['copies'][label]['bytes']))
                self.assertEqual(actual, (old[name]['cached_wheel_and_installed_sha256'], old[name]['cached_wheel_and_installed_bytes']))
                self.assertNotEqual(actual[0], recorded(row[1]))
                self.assertNotEqual(actual[1], int(row[2]))
                self.assertEqual(actual[1] - int(row[2]), old[name]['delta_bytes'])
                self.assertEqual(claim['cause'], 'UNDETERMINED')
                self.assertEqual(claim['review_verdict'], 'UNRESOLVED_DO_NOT_QUALIFY')

    def test_04_independent_objdump_all_elf_tags_and_edges(self):
        historic = {e['path']: e for e in DOSSIER['native_mapped_file_observations']}
        graph = AUDIT['historical_map_elf_dynamic']
        self.assertEqual(len(graph), 78)
        self.assertEqual(set(historic), {e['path'] for e in graph})
        dynamic = {}; names = {}
        for claim in graph:
            p = claim['path']; old = historic[p]
            self.assertEqual(identity(p), (old['sha256'], old['bytes']))
            self.assertEqual(claim['sha256'], old['sha256'])
            parsed = {tag: [] for tag in ('NEEDED', 'SONAME', 'RPATH', 'RUNPATH')}
            for line in run('/usr/bin/objdump', '-p', p).decode().splitlines():
                match = re.fullmatch(r'\s*(NEEDED|SONAME|RPATH|RUNPATH)\s+(\S+)\s*', line)
                if match: parsed[match[1]].append(match[2])
            self.assertEqual(parsed, claim['dynamic'], p)
            dynamic[p] = parsed
            for name in {Path(p).name, *parsed['SONAME']}:
                names.setdefault(name, []).append(p)
        edges = 0
        for claim in graph:
            expected = [{'soname': n, 'paths': names.get(n, [])} for n in dynamic[claim['path']]['NEEDED']]
            self.assertEqual(expected, claim['needed_candidates_in_historical_map'])
            for edge in expected: self.assertEqual(len(edge['paths']), 1)
            edges += len(expected)
        self.assertEqual(edges, 305)
        summary = AUDIT['historical_map_static_edge_summary']
        self.assertEqual([summary[k] for k in ('mapped_files','needed_edges','ambiguous_in_historical_map','missing_from_historical_map_by_filename_or_soname')], [78,305,0,0])
        self.assertEqual(identity(AUDIT['readelf']['path'])[0], AUDIT['readelf']['sha256'])
        self.assertEqual(run('/usr/bin/readelf', '--version').decode().splitlines()[0], AUDIT['readelf']['version_line'])

    def test_05_build_metadata(self):
        self.assertEqual(len(AUDIT['packaged_build_metadata']), 5)
        for claim in AUDIT['packaged_build_metadata']:
            self.assertEqual(identity(claim['path']), (claim['sha256'], claim['bytes']))
            lines = Path(claim['path']).read_text().splitlines()
            selected = [x.strip() for x in lines if x.startswith(('CXX=', 'CMAKE_BUILD_TYPE=')) or 'IMPORTED_CONFIGURATIONS MINSIZEREL' in x or '#define HAVE_' in x]
            self.assertEqual(selected[:80], claim['selected_markers'])
        pc = Path(AUDIT['packaged_build_metadata'][0]['path']).read_text()
        self.assertIn('CXX=/opt/rh/gcc-toolset-14/root/usr/bin/c++', pc)
        for name in ('requirements-runtime-hashed.txt', 'requirements-execution-hashed.txt'):
            self.assertIsNone(re.search(r'^(eccodes|eccodeslib|eckitlib|numpy|findlibs)\b', (ROOT / name).read_text(), re.M | re.I))

    def test_06_dossier_packages_and_interpreter(self):
        self.assertEqual(len(DOSSIER['python_packages']), 8)
        for pkg in DOSSIER['python_packages']:
            dist = Path(pkg['dist_info']); rec = dist / 'RECORD'
            self.assertEqual(identity(rec), (pkg['record_sha256'], pkg['record_bytes']))
            self.assertEqual(identity(dist / 'METADATA')[0], pkg['metadata_sha256'])
            meta = email.message_from_bytes((dist / 'METADATA').read_bytes())
            self.assertEqual(meta['Version'], pkg['installed_version'])
            self.assertEqual(meta.get_all('Requires-Dist', []), pkg['requires_dist'])
            self.assertEqual((dist / 'direct_url.json').exists(), pkg['direct_url_present'])
            rows = list(csv.reader(io.StringIO(rec.read_text())))
            self.assertEqual(len(rows), pkg['record_rows'])
            self.assertEqual(sum(not r[1] for r in rows), pkg['unhashed_record_rows'])
            missing = []; mismatches = []
            for name, value, size in rows:
                if not value: continue
                p = dist.parent / name
                if not p.is_file(): missing.append(name)
                elif identity(p) != (recorded(value), int(size)): mismatches.append(name)
            self.assertEqual(missing, pkg['missing_hashed_rows'])
            self.assertEqual(mismatches, pkg['mismatched_hashed_rows'])
        interp = DOSSIER['interpreter']
        self.assertEqual(str(Path(interp['observed_executable']).resolve()), interp['resolved_path'])
        self.assertEqual(identity(interp['resolved_path']), (interp['sha256'], interp['bytes']))

    def test_07_historical_wheel_evidence_is_only_historical(self):
        self.assertFalse(Path(AUDIT['historical_cache']['path']).exists())
        self.assertFalse(AUDIT['historical_cache']['present_now'])
        self.assertEqual(AUDIT['historical_cache']['prior_sha256'], DOSSIER['cache']['sha256'])
        for key, name in [('source_observation_sha256','V11_R09_GATE3_DECODER_BUILD_OBSERVATION_20261001.json'), ('source_review_sha256','V11_R09_GATE3_DECODER_BUILD_REVIEW_82e1619.json')]:
            self.assertEqual(identity(ROOT / 'docs' / name)[0], DOSSIER[key])
        observed = json.loads((ROOT / 'docs/V11_R09_GATE3_DECODER_BUILD_OBSERVATION_20261001.json').read_text())
        cached = observed['local_cached_wheel_comparison']
        self.assertEqual((cached['archive_sha256'], cached['archive_bytes']), (DOSSIER['cache']['sha256'], DOSSIER['cache']['bytes']))
        review = json.loads((ROOT / 'docs/V11_R09_GATE3_DECODER_BUILD_REVIEW_82e1619.json').read_text())
        self.assertEqual(review['verdict'], 'PASS_OBSERVATION_ONLY')
        wheel = review['cached_wheel_comparison']
        self.assertEqual((wheel['archive_sha256'], wheel['archive_bytes'], wheel['wheel_record_sha256']), (DOSSIER['cache']['sha256'], DOSSIER['cache']['bytes'], DOSSIER['cache']['wheel_record_sha256']))
        old_by_path = {e['path']: e for e in DOSSIER['record_discrepancies']}
        self.assertEqual(len(wheel['vendored_entries']), 25)
        for entry in wheel['vendored_entries']:
            old = old_by_path[entry['path']]
            self.assertEqual((entry['wheel_sha256'], entry['wheel_bytes'], entry['record_sha256'], entry['record_bytes']), (old['cached_wheel_and_installed_sha256'], old['cached_wheel_and_installed_bytes'], old['record_sha256'], old['record_bytes']))

    def test_08_qualification_gates(self):
        self.assertEqual(AUDIT['qualification_credit'], 0)
        for key in ('authenticated_original','complete_loader_closure','reproducible_installation_verified'):
            self.assertIs(AUDIT[key], False)
        for key in ('a2_qualified','a3_qualified','authenticated_original','complete_dependency_lock','reproducible_installation_verified'):
            self.assertIs(TERMINAL[key], False)
        self.assertEqual(TERMINAL['g3l'], 'NO-GO')
        self.assertEqual(TERMINAL['independent_review'], 'PENDING')

    def reject(self, mutate, message):
        prior = copy.deepcopy(DOSSIER); mutate(prior)
        class MemoryDossier:
            def read_text(self): return json.dumps(prior)
        output = io.StringIO()
        with patch.object(mod, 'DOSSIER', MemoryDossier()), patch.object(sys, 'argv', [str(TOOL)]), contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(RuntimeError, message): mod.main()
        self.assertEqual(output.getvalue(), '')

    def test_09_changed_historical_record_rejected(self):
        self.reject(lambda d: d['record_discrepancies'][0].update(record_sha256='0'*64), 'RECORD row drift')

    def test_10_changed_payload_hash_rejected(self):
        self.reject(lambda d: d['record_discrepancies'][0].update(cached_wheel_and_installed_sha256='0'*64), 'payload drift')

    def test_11_changed_mapped_dependency_rejected(self):
        self.reject(lambda d: d['native_mapped_file_observations'][0].update(sha256='0'*64), 'mapped-file drift')

    def test_12_missing_or_duplicate_discrepancy_rejected(self):
        self.reject(lambda d: d['record_discrepancies'].pop(), '25 unique rows')
        self.reject(lambda d: d['record_discrepancies'].__setitem__(1, copy.deepcopy(d['record_discrepancies'][0])), '25 unique rows')

    def test_13_local_record_copy_disagreement_rejected(self):
        original = mod.digest
        target = mod.SITES['brain_history'] / 'eckitlib-2.3.0.30.dist-info/RECORD'
        def changed(path):
            h, n = original(path)
            return ('0'*64, n) if path == target else (h, n)
        with patch.object(mod, 'digest', changed):
            self.reject(lambda d: None, 'retained RECORD copies disagree')


if __name__ == '__main__':
    unittest.main(verbosity=2)
