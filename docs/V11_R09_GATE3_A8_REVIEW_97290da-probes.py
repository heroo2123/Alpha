"""Independent offline review probes; never confers real qualification.
Run --author for the reported four-file suite, --adjacent for store coverage,
or with no mode for independent probes. All scratch is prefix-owned /tmp.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

REPO = Path('/tmp/alpha-v11-gate3-a8-review-97290da')
sys.path.insert(0, str(REPO))
import pytest


def _no_network(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo', 'socket.sendto'):
        raise AssertionError('Network connections prohibited by independent review')


class BoundedScratch:
    closed_scratch_fds = 0
    minimum_observed_free_bytes = None

    def pytest_sessionfinish(self, session, exitstatus):
        print('REVIEW_CLEANUP=' + json.dumps({
            'closed_completed_test_scratch_fds': self.closed_scratch_fds,
            'minimum_observed_free_bytes_at_teardown': self.minimum_observed_free_bytes}))

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_teardown(self, item, nextitem):
        path = item.funcargs.get('tmp_path')
        yield
        if path is not None:
            root = Path(item.config.option.basetemp).resolve()
            path = Path(path).resolve()
            assert root in path.parents and str(root).startswith('/tmp/a8-exact-97290da-')
            free = shutil.disk_usage(root).free
            self.minimum_observed_free_bytes = min(free,
                self.minimum_observed_free_bytes if self.minimum_observed_free_bytes is not None else free)
            # Some test helpers omit ReportSink.close(). Unlink alone keeps
            # those reserves allocated until process exit. Close only FDs
            # owned by this already-completed test's unique scratch tree.
            for entry in Path('/proc/self/fd').iterdir():
                try:
                    target = os.readlink(entry)
                    if target.startswith(str(path) + '/'):
                        os.close(int(entry.name))
                        self.closed_scratch_fds += 1
                except FileNotFoundError:
                    pass
            shutil.rmtree(path, ignore_errors=False)
            # Keep a tiny name marker: pytest must not reuse this path while
            # fixture helpers retain per-path journal heads in memory.
            path.mkdir(mode=0o700)


if __name__ == '__main__':
    sys.addaudithook(_no_network)
    mode = sys.argv[1] if len(sys.argv) > 1 else '--probes'
    if mode == '--author':
        paths = [str(REPO / 'tests' / ('test_v11_r09_gate3_' + name + '.py'))
                 for name in ('a8_composition', 'g3l_prep', 'launch_v4', 'runtime')]
    elif mode == '--adjacent':
        paths = [str(REPO / 'tests' / ('test_v11_r09_gate3_' + name + '.py'))
                 for name in ('store_v1', 'restart_composition')]
    else:
        paths = [__file__]
    basetemp = tempfile.mkdtemp(prefix='a8-exact-97290da-' + mode[2:] + '-', dir='/tmp')
    print('REVIEW_BASETEMP=' + basetemp, flush=True)
    print('REVIEW_PROBE_SHA256=' + hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), flush=True)
    code = pytest.main(['-q', '-p', 'no:cacheprovider', '--import-mode=importlib',
                        '--basetemp=' + basetemp, *paths], plugins=[BoundedScratch()])
    print('REVIEW_EXIT_CODE=' + str(code), flush=True)
    raise SystemExit(code)

# Helpers below deliberately exercise offline sub-boundaries. They do not
# manufacture a valid real V4 package or bypass V4 in a launch path.
from types import SimpleNamespace
from tools import v11_r09_gate3_a8_composition as a8
from tools.v11_multimodel_panel import canonical
from tools.v11_r09_gate3_launch import LaunchContractError
from tools.v11_r09_gate3_runtime import FakeClock, FakeResourceProbe, FrozenPlan
from tools.v11_r09_gate3_store_v1 import VersionedImmutableObjectStore as Store


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pin_for_store():
    name = 'tools/v11_r09_gate3_store_v1.py'
    return {'path': name, 'sha256': sha((REPO / name).read_bytes()),
            'qualname': 'VersionedImmutableObjectStore'}


@pytest.mark.xfail(raises=LaunchContractError, strict=True, reason="repaired helper refuses old counterexample")
def test_f1_foreign_class_method_is_accepted_as_pinned_source(monkeypatch):
    obj = object.__new__(Store)
    def foreign_method(self):
        return 'unreviewed implementation'
    monkeypatch.setattr(Store, '_check_dirs', foreign_method)
    identity = a8._component(REPO, obj, pin_for_store(), '_check_dirs')
    assert identity[2] == id(foreign_method)
    assert obj._check_dirs() == 'unreviewed implementation'
    assert Path(foreign_method.__code__.co_filename) != REPO / pin_for_store()['path']


@pytest.mark.xfail(raises=LaunchContractError, strict=True, reason="repaired helper refuses old counterexample")
def test_f1_changed_method_code_survives_composition_recheck(monkeypatch):
    obj = object.__new__(Store)
    method = Store._check_dirs
    original = method.__code__
    pin = pin_for_store()
    identity = a8._component(REPO, obj, pin, '_check_dirs')
    package = b'offline isolated recheck fixture'
    prepared = a8.PreparedComposition(sha(package), 'b' * 64, (identity,), ('boot', 1, 2))
    def foreign_method(self):
        return 'changed after preparation'
    plan = object.__new__(FrozenPlan)
    object.__setattr__(plan, 'window', SimpleNamespace(start_utc=1, acquisition_end_utc=3))
    # Isolate recheck's comparison; the invoked component checker is unchanged.
    def isolated_validation(*args, **kwargs):
        return a8.PreparedComposition(sha(package), 'b' * 64,
            (a8._component(REPO, obj, pin, '_check_dirs'),), ('boot', 1, 2))
    monkeypatch.setattr(a8, 'validate_a8_composition', isolated_validation)
    try:
        method.__code__ = foreign_method.__code__
        assert obj._check_dirs() == 'changed after preparation'
        current = a8.recheck_a8_composition(prepared, package,
            expected_review_sha256='b' * 64, plan=plan, now_utc=2)
        assert current.component_identities == prepared.component_identities
    finally:
        method.__code__ = original


def live_fixture(tmp_path):
    root = tmp_path / 'store'
    root.mkdir(mode=0o700)
    (root / 'objects').mkdir(mode=0o700)
    store = Store(root, manifest_sha256='a' * 64, policy_sha256='b' * 64,
        build_id='offline-fixture', clock_method='reviewed-clock',
        max_clock_age_seconds=30, host_id='fixture-host', boot_id='fixture-boot')
    payload = {'time': {'uncertainty_seconds': 1},
        'clocks_and_receipts': {'preregistration': {'max_measurement_age_seconds': 30}},
        'runtime': {'resource_bounds': {'local_storage_quota_bytes': 100},
                    'policy': {'sha256': 'b' * 64}},
        'limits': {'min_free_disk_bytes': 100, 'min_available_memory_bytes': 100,
                   'decoded': 100}}
    context = {'boot_id': 'fixture-boot', 'clock_method': 'reviewed-clock',
        'store_descriptor': store.descriptor_sha256, 'store_policy': 'b' * 64}
    plan = SimpleNamespace(runtime_context_raw=canonical(context), manifest_sha256='a' * 64)
    clock = FakeClock('fixture-boot', utc=100, mono=100, method='reviewed-clock')
    resources = FakeResourceProbe(free_disk_bytes=200, available_memory_bytes=200)
    components = {'clock': clock, 'resources': resources, 'storage': store}
    return store, payload, plan, components


@pytest.mark.xfail(raises=LaunchContractError, strict=True, reason="repaired helper refuses old counterexample")
@pytest.mark.parametrize('change', ['clock_method', 'descriptor', 'policy', 'failed_store'])
def test_f2_changed_live_context_still_passes(tmp_path, change):
    store, payload, plan, components = live_fixture(tmp_path)
    try:
        before = a8._fresh_context(payload, plan, components, now_utc=100)
        if change == 'clock_method':
            store.context['clock_method'] = components['clock'].method = 'unreviewed-clock'
        elif change == 'descriptor':
            store.descriptor_sha256 = 'c' * 64
        elif change == 'policy':
            store.context['policy'] = 'd' * 64
        else:
            store._failed = True
        after = a8._fresh_context(payload, plan, components, now_utc=100)
        assert before[:3] == after[:3]
        if change == 'clock_method':
            assert json.loads(plan.runtime_context_raw)['clock_method'] != store.context['clock_method']
    finally:
        store.close()


@pytest.mark.parametrize('change,reason', [
    ('boot', 'A8_CLOCK_STALE_OR_DIFFERENT_HOST'),
    ('stale', 'A8_CLOCK_STALE_OR_DIFFERENT_HOST'),
    ('disk', 'A8_CAPACITY_STALE_OR_INSUFFICIENT'),
    ('memory', 'A8_CAPACITY_STALE_OR_INSUFFICIENT'),
    ('manifest', 'A8_STORAGE_CONTEXT'),
    ('mode', 'OBJECT_DIRECTORY_PRIVATE'),
])
def test_live_refusal_controls(tmp_path, change, reason):
    store, payload, plan, components = live_fixture(tmp_path)
    try:
        a8._fresh_context(payload, plan, components, now_utc=100)
        if change == 'boot':
            components['clock'].boot_id = 'other-boot'
        elif change == 'stale':
            components['clock'].set(measured_mono=69)
        elif change == 'disk':
            components['resources'].free_disk_bytes = 199
        elif change == 'memory':
            components['resources'].available_memory_bytes = 199
        elif change == 'manifest':
            store.context['manifest'] = 'e' * 64
        else:
            store.root.chmod(0o755)
        with pytest.raises(LaunchContractError, match=reason):
            a8._fresh_context(payload, plan, components, now_utc=100)
    finally:
        store.root.chmod(0o700)
        store.close()


def sealed_write(root, raw):
    path = root / sha(raw)
    path.write_bytes(raw)
    return {'sha256': sha(raw), 'byte_length': len(raw),
            'media_type': 'application/json', 'path': path.name}


def test_external_pin_and_exact_input_bytes(tmp_path):
    from tests.test_v11_r09_gate3_a8_composition import _package
    from tools.v11_r09_gate3_g3l_prep import private_v4_null_template
    manifest = canonical(private_v4_null_template('2026-10-03'))
    inventory, review = b'{}', b'{}'
    args = dict(manifest_raw=manifest, inventory_raw=inventory, review_raw=review,
        expected_review_sha256='0' * 64, plan=None, components={},
        repo=REPO, object_root=tmp_path, now_utc=1)
    with pytest.raises(LaunchContractError, match='A8_EXTERNAL_REVIEW_MISMATCH'):
        a8.validate_a8_composition(_package(manifest, inventory), **args)
    args['expected_review_sha256'] = sha(review)
    for field in ('manifest_raw', 'inventory_raw'):
        changed = dict(args, **{field: args[field] + b' '})
        with pytest.raises(LaunchContractError, match='A8_PACKAGE_BYTES'):
            a8.validate_a8_composition(_package(manifest, inventory), **changed)


def test_all_79_inventory_identities_are_required(tmp_path):
    from tests.test_v11_r09_gate3_g3l_prep import (
        _complete_evidence, _inventory, TARGET_DATE, NOW)
    from tools.v11_r09_gate3_g3l_prep import check_inventory, ALL_IDS
    evidence = _complete_evidence(tmp_path)
    assert len(ALL_IDS) == 79
    def validate(e):
        return check_inventory(_inventory(e), target_date=TARGET_DATE,
            now_utc=NOW, stage='FINAL', object_root=tmp_path)
    assert validate(evidence) == []
    for name in ALL_IDS:
        findings = validate(dict(evidence, **{name: None}))
        assert any(f['id'] == name and f['state'] == 'MISSING' for f in findings)
        altered = {**evidence[name], 'ref': {**evidence[name]['ref'], 'sha256': 'f' * 64}}
        assert any(f['id'] == name and f['state'] == 'INVALID'
                   for f in validate(dict(evidence, **{name: altered})))


def test_distinct_review_records_are_binding_not_authentication(tmp_path):
    # Deliberately meaningless evidence is structurally accepted. This is the
    # documented external authentication boundary, not an asserted defect.
    evidence = sealed_write(tmp_path, b'UNAUTHENTICATED OFFLINE PLACEHOLDER')
    refs = {}
    for gate in a8.GATES:
        record = canonical({'gate': gate, 'status': 'ACCEPTED',
            'evidence_sha256': evidence['sha256'], 'commit_oid': '1' * 40,
            'tree_oid': '2' * 40})
        refs[gate] = {'evidence': evidence, 'review': sealed_write(tmp_path, record)}
    assert len(a8._prerequisite_reviews(refs, tmp_path)) == 7
    for gate in a8.GATES:
        with pytest.raises(LaunchContractError, match='A8_PREREQUISITE_SET'):
            a8._prerequisite_reviews({k: v for k, v in refs.items() if k != gate}, tmp_path)
    reused = dict(refs)
    reused['A7'] = refs['A1']
    with pytest.raises(LaunchContractError, match='A8_PREREQUISITE_NOT_ACCEPTED'):
        a8._prerequisite_reviews(reused, tmp_path)


def test_typed_purpose_bytes_bind_all_15_contracts(tmp_path):
    (tmp_path / 'objects').mkdir()
    sources, endpoints = {}, []
    for provider in ('GEFS', 'IFS', 'AIFS'):
        sources[provider] = {'dossier': {'sha256': sha(provider.encode())}, 'purpose_mappings': {}}
        for purpose in a8.PURPOSES:
            endpoint = {'provider': provider, 'purpose': purpose,
                'origin': 'https://offline.example.invalid', 'path_spec': [],
                'parser_identity': {'sha256': sha(purpose.encode())}}
            contract = {'schema': a8.PURPOSE_SCHEMA, 'provider': provider,
                'purpose': purpose, 'origin': endpoint['origin'], 'method': 'GET',
                'path_spec': [], 'source_dossier_sha256': sources[provider]['dossier']['sha256'],
                'parser_sha256': endpoint['parser_identity']['sha256']}
            ref = sealed_write(tmp_path / 'objects', canonical(contract))
            del ref['path']
            sources[provider]['purpose_mappings'][purpose] = {'response_contract': ref}
            endpoints.append(endpoint)
    payload = {'sources': sources, 'network': {'endpoints': endpoints}}
    a8._purpose_contracts(payload, tmp_path)
    for endpoint in endpoints:
        old = endpoint['parser_identity']['sha256']
        endpoint['parser_identity']['sha256'] = 'f' * 64
        with pytest.raises(LaunchContractError, match='A8_PURPOSE_CONTRACT_MISMATCH'):
            a8._purpose_contracts(payload, tmp_path)
        endpoint['parser_identity']['sha256'] = old


def test_all_20_component_reviews_and_identity_bindings(tmp_path):
    source_pins = {role: {'sha256': sha(role.encode())} for role in a8.ROLES}
    sources = {p: {'purpose_mappings': {purpose: {'response_contract': {
        'sha256': sha((p + ':' + purpose).encode())}} for purpose in a8.PURPOSES}}
        for p in ('GEFS', 'IFS', 'AIFS')}
    payload = {'sources': sources}
    refs = {}
    for component in a8.COMPONENTS:
        identity = source_pins[component]['sha256'] if component in a8.ROLES else \
            sources[component.split(':')[0]]['purpose_mappings'][component.split(':')[1]]['response_contract']['sha256']
        evidence = sealed_write(tmp_path, canonical({'offline_fixture': component}))
        reviewed = sealed_write(tmp_path, canonical({'component': component,
            'status': 'ACCEPTED', 'evidence_sha256': evidence['sha256'],
            'identity_sha256': identity, 'commit_oid': '1' * 40, 'tree_oid': '2' * 40}))
        refs[component] = {'evidence': evidence, 'review': reviewed}
    assert len(a8._component_reviews(refs, payload, source_pins, tmp_path)) == 20
    for name in a8.COMPONENTS:
        with pytest.raises(LaunchContractError, match='A8_COMPONENT_REVIEW_SET'):
            a8._component_reviews({k: v for k, v in refs.items() if k != name},
                                  payload, source_pins, tmp_path)
        record = json.loads((tmp_path / refs[name]['review']['path']).read_bytes())
        record['identity_sha256'] = 'f' * 64
        bad = {**refs, name: {**refs[name], 'review': sealed_write(tmp_path, canonical(record))}}
        with pytest.raises(LaunchContractError, match='A8_COMPONENT_NOT_ACCEPTED'):
            a8._component_reviews(bad, payload, source_pins, tmp_path)


def test_component_object_function_and_source_inode_controls(tmp_path):
    import importlib.util
    directory = tmp_path / 'tools'
    directory.mkdir()
    source = directory / 'review_probe.py'
    raw = b'def decode(value):\n    return value\n'
    source.write_bytes(raw)
    spec = importlib.util.spec_from_file_location('a8_local_review_probe', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    pin = {'path': 'tools/review_probe.py', 'sha256': sha(raw), 'qualname': 'decode'}
    initial = a8._component(tmp_path, module.decode, pin)
    replacement = directory / 'replacement.py'
    replacement.write_bytes(raw)
    os.replace(replacement, source)
    after = a8._component(tmp_path, module.decode, pin)
    assert after != initial and after[5] != initial[5]
    source.write_bytes(raw + b'# modified\n')
    with pytest.raises(LaunchContractError, match='A8_COMPONENT_CHANGED'):
        a8._component(tmp_path, module.decode, pin)
    first, second = object.__new__(Store), object.__new__(Store)
    assert a8._component(REPO, first, pin_for_store(), '_check_dirs') != \
           a8._component(REPO, second, pin_for_store(), '_check_dirs')


def test_real_mapping_still_refuses_without_changing_validator(tmp_path, monkeypatch):
    from tests.test_v11_r09_gate3_launch_v4 import candidate
    from tools.v11_r09_gate3_launch_v4 import validate_manifest_v4
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    raw = canonical(payload)
    assert validate_manifest_v4(raw, repo=repo, object_root=root, now_utc=start - 4000) == sha(raw)
    payload['identity']['mapping_scope'] = 'PROVIDER_REVIEW_REQUIRED'
    payload['identity']['pilot_id'] = 'offline_review_fixture'
    with pytest.raises(LaunchContractError, match='SOURCE_MAPPING_UNSUPPORTED'):
        validate_manifest_v4(canonical(payload), repo=repo, object_root=root, now_utc=start - 4000)


@pytest.mark.parametrize('replacement', ['instance', 'class', 'in_place_code'])
def test_r1_unchecked_usable_replacement_accepts_failed_store(tmp_path, monkeypatch, replacement):
    """Counterexample: actual health call is not included in the source identity."""
    store, payload, plan, components = live_fixture(tmp_path)
    original = Store._usable.__code__
    try:
        before = a8._component(REPO, store, pin_for_store(), '_check_dirs')
        host = a8._fresh_context(payload, plan, components, now_utc=100)
        store._failed = True
        with pytest.raises(LaunchContractError, match='OBJECT_DURABILITY_UNCERTAIN'):
            a8._fresh_context(payload, plan, components, now_utc=100)
        def unchecked(self):
            return None
        if replacement == 'instance':
            monkeypatch.setattr(store, '_usable', lambda: None)
        elif replacement == 'class':
            monkeypatch.setattr(Store, '_usable', unchecked)
        else:
            Store._usable.__code__ = unchecked.__code__
        assert a8._component(REPO, store, pin_for_store(), '_check_dirs') == before
        assert a8._fresh_context(payload, plan, components, now_utc=100) == host
        assert store._failed is True
    finally:
        if replacement == 'in_place_code':
            Store._usable.__code__ = original
        store.close()


def test_r1_unchecked_usable_skips_directory_privacy(tmp_path, monkeypatch):
    store, payload, plan, components = live_fixture(tmp_path)
    try:
        before = a8._component(REPO, store, pin_for_store(), '_check_dirs')
        host = a8._fresh_context(payload, plan, components, now_utc=100)
        store.root.chmod(0o755)
        with pytest.raises(LaunchContractError, match='OBJECT_DIRECTORY_PRIVATE'):
            store._check_dirs()
        monkeypatch.setattr(store, '_usable', lambda: None)
        assert a8._component(REPO, store, pin_for_store(), '_check_dirs') == before
        assert a8._fresh_context(payload, plan, components, now_utc=100) == host
    finally:
        store.root.chmod(0o700)
        store.close()


def test_equal_recompiled_code_changes_retained_identity():
    store = object.__new__(Store)
    before = a8._component(REPO, store, pin_for_store(), '_check_dirs')
    method = Store._check_dirs
    original = method.__code__
    try:
        method.__code__ = original.replace()
        assert method.__code__ == original and method.__code__ is not original
        after = a8._component(REPO, store, pin_for_store(), '_check_dirs')
        assert after != before
    finally:
        method.__code__ = original
