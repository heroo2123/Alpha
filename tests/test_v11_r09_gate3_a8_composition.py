"""Offline refusal probes for the unqualified A8 composition boundary."""
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.v11_multimodel_panel import canonical
from tools.v11_r09_gate3_a8_composition import (
    PreparedComposition, _adapters, _component, _component_reviews, _fresh_context,
    _prerequisite_reviews,
    _purpose_contracts, _sealed, recheck_a8_composition,
    validate_a8_composition,
)
from tools.v11_r09_gate3_g3l_prep import private_v4_null_template
from tools.v11_r09_gate3_launch import LaunchContractError
from tools.v11_r09_gate3_runtime import (
    FakeClock, FakeResourceProbe, FrozenPlan, SyntheticTransport,
)
from tools.v11_r09_gate3_offline_io import SyntheticExchange
from tools.v11_r09_gate3_store_v1 import VersionedImmutableObjectStore


def _ref(path: Path, root: Path):
    raw = path.read_bytes()
    return {'sha256': hashlib.sha256(raw).hexdigest(), 'byte_length': len(raw),
            'media_type': 'application/json', 'path': str(path.relative_to(root))}


def _package(manifest_raw, inventory_raw):
    return canonical({
        'schema': 'R09_GATE3_A8_COMPOSITION_PREPARATION_V1',
        'manifest_sha256': hashlib.sha256(manifest_raw).hexdigest(),
        'inventory_sha256': hashlib.sha256(inventory_raw).hexdigest(),
        'plan_sha256': '1' * 64, 'runtime_context_sha256': '2' * 64,
        'commit_oid': '3' * 40, 'tree_oid': '4' * 40,
        'reviewed_utc': 1, 'adapter_sources': {}, 'prerequisite_reviews': {},
        'component_reviews': {},
    })


def test_composition_refuses_synthetic_manifest_before_any_adapter_use(tmp_path):
    manifest = private_v4_null_template('2026-10-03')
    manifest['identity']['mapping_scope'] = 'SYNTHETIC_OFFLINE_ONLY'
    manifest['identity']['pilot_id'] = 'synthetic_offline'
    manifest_raw = canonical(manifest)
    inventory_raw = canonical({'unfilled': True})
    review_raw = b'{}'
    with pytest.raises(LaunchContractError, match='A8_SYNTHETIC_MANIFEST_FORBIDDEN'):
        validate_a8_composition(
            _package(manifest_raw, inventory_raw), manifest_raw=manifest_raw,
            inventory_raw=inventory_raw, review_raw=review_raw,
            expected_review_sha256=hashlib.sha256(review_raw).hexdigest(),
            plan=None, components={}, repo=tmp_path, object_root=tmp_path,
            now_utc=1)


def test_manifest_byte_substitution_refuses_before_v4_validation(tmp_path):
    manifest_raw = canonical(private_v4_null_template('2026-10-03'))
    inventory_raw = canonical({'unfilled': True})
    review_raw = b'{}'
    with pytest.raises(LaunchContractError, match='A8_PACKAGE_BYTES'):
        validate_a8_composition(
            _package(manifest_raw, inventory_raw),
            manifest_raw=manifest_raw + b' ', inventory_raw=inventory_raw,
            review_raw=review_raw,
            expected_review_sha256=hashlib.sha256(review_raw).hexdigest(),
            plan=None, components={}, repo=tmp_path, object_root=tmp_path,
            now_utc=1)


def test_fake_clock_synthetic_transport_and_resource_probe_refuse(tmp_path):
    fake_clock = FakeClock('boot', utc=100)
    fake_resource = FakeResourceProbe(free_disk_bytes=10**10,
                                      available_memory_bytes=10**10)
    transport = SyntheticTransport(SyntheticExchange({}))
    components = {'transport': transport, 'clock': fake_clock,
                  'storage': object(), 'resources': fake_resource,
                  'decoder': lambda raw: raw}
    with pytest.raises(LaunchContractError, match='A8_REAL_ADAPTERS_REQUIRED'):
        _adapters(tmp_path, components, {key: {} for key in components})


def test_opaque_or_synthetic_purpose_contract_refuses(tmp_path):
    raw = canonical({'opaque_pin': 'synthetic'})
    contract_path = tmp_path / 'objects' / hashlib.sha256(raw).hexdigest()
    contract_path.parent.mkdir()
    contract_path.write_bytes(raw)
    ref = _ref(contract_path, tmp_path)
    # The endpoint table itself is complete; the first referenced contract
    # still has to be typed and bound to its provider, path, and parser.
    endpoints = [{'provider': p, 'purpose': purpose,
                  'origin': 'https://example.invalid', 'path_spec': [],
                  'parser_identity': {'sha256': '1' * 64}}
                 for p in ('GEFS', 'IFS', 'AIFS')
                 for purpose in ('FIELD', 'INDEX', 'OBJECT_ID', 'METADATA', 'PROBE')]
    payload = {'network': {'endpoints': endpoints},
               'sources': {p: {'purpose_mappings': {
                   purpose: {'response_contract': {
                       'sha256': ref['sha256'], 'byte_length': ref['byte_length'],
                       'media_type': ref['media_type']}}
                   for purpose in ('FIELD', 'INDEX', 'OBJECT_ID', 'METADATA', 'PROBE')}}
                   for p in ('GEFS', 'IFS', 'AIFS')}}
    with pytest.raises(LaunchContractError, match='A8_PURPOSE_CONTRACT_SCHEMA'):
        _purpose_contracts(payload, tmp_path)


def test_sealed_object_rejects_symlink_and_changed_bytes(tmp_path):
    source = tmp_path / 'object'
    source.write_bytes(b'original')
    ref = _ref(source, tmp_path)
    assert _sealed(tmp_path, ref) == b'original'
    source.write_bytes(b'changed!')
    with pytest.raises(LaunchContractError, match='A8_ARTIFACT_CHANGED'):
        _sealed(tmp_path, ref)
    source.write_bytes(b'original')
    link = tmp_path / 'link'
    link.symlink_to(source)
    with pytest.raises(LaunchContractError, match='A8_ARTIFACT_SYMLINK'):
        _sealed(tmp_path, {**ref, 'path': 'link'})


def test_a1_a7_require_distinct_accepted_review_records(tmp_path):
    refs = {}
    for n in range(1, 8):
        gate = f'A{n}'
        evidence = tmp_path / f'{gate}-evidence'
        evidence.write_bytes(canonical({'gate': gate, 'fixture': True}))
        review = tmp_path / f'{gate}-review'
        review.write_bytes(canonical({
            'gate': gate, 'status': 'ACCEPTED',
            'evidence_sha256': _ref(evidence, tmp_path)['sha256'],
            'commit_oid': '1' * 40, 'tree_oid': '2' * 40}))
        refs[gate] = {'evidence': _ref(evidence, tmp_path),
                      'review': _ref(review, tmp_path)}
    assert set(_prerequisite_reviews(refs, tmp_path)) == set(refs)
    with pytest.raises(LaunchContractError, match='A8_PREREQUISITE_SET'):
        _prerequisite_reviews({k: v for k, v in refs.items() if k != 'A7'}, tmp_path)
    bad = tmp_path / 'A7-bad-review'
    bad.write_bytes(canonical({
        'gate': 'A7', 'status': 'PENDING',
        'evidence_sha256': refs['A7']['evidence']['sha256'],
        'commit_oid': '1' * 40, 'tree_oid': '2' * 40}))
    refs['A7']['review'] = _ref(bad, tmp_path)
    with pytest.raises(LaunchContractError, match='A8_PREREQUISITE_NOT_ACCEPTED'):
        _prerequisite_reviews(refs, tmp_path)


def test_each_adapter_and_purpose_contract_needs_own_accepted_review(tmp_path):
    roles = ('transport', 'clock', 'storage', 'resources', 'decoder')
    purposes = ('FIELD', 'INDEX', 'OBJECT_ID', 'METADATA', 'PROBE')
    names = roles + tuple(f'{p}:{purpose}' for p in ('GEFS', 'IFS', 'AIFS')
                    for purpose in purposes)
    source_pins = {role: {'sha256': hashlib.sha256(role.encode()).hexdigest()}
                   for role in roles}
    source_refs = {p: {'purpose_mappings': {purpose: {'response_contract': {
        'sha256': hashlib.sha256(f'{p}:{purpose}'.encode()).hexdigest()}}
        for purpose in purposes}} for p in ('GEFS', 'IFS', 'AIFS')}
    payload = {'sources': source_refs}
    refs = {}
    for name in names:
        evidence = tmp_path / f'{name.replace(":", "_")}-evidence'
        evidence.write_bytes(canonical({'component': name}))
        review = tmp_path / f'{name.replace(":", "_")}-review'
        identity = (source_pins[name]['sha256'] if name in roles else
                    source_refs[name.split(':')[0]]['purpose_mappings'][
                        name.split(':')[1]]['response_contract']['sha256'])
        review.write_bytes(canonical({
            'component': name, 'status': 'ACCEPTED',
            'evidence_sha256': _ref(evidence, tmp_path)['sha256'],
            'identity_sha256': identity, 'commit_oid': '1' * 40,
            'tree_oid': '2' * 40}))
        refs[name] = {'evidence': _ref(evidence, tmp_path),
                      'review': _ref(review, tmp_path)}
    assert set(_component_reviews(refs, payload, source_pins, tmp_path)) == set(names)
    with pytest.raises(LaunchContractError, match='A8_COMPONENT_REVIEW_SET'):
        _component_reviews({k: v for k, v in refs.items() if k != 'GEFS:INDEX'},
                           payload, source_pins, tmp_path)
    source_pins['clock']['sha256'] = '0' * 64
    with pytest.raises(LaunchContractError, match='A8_COMPONENT_NOT_ACCEPTED'):
        _component_reviews(refs, payload, source_pins, tmp_path)


def test_component_source_identity_rejects_different_hash():
    import tools.v11_r09_gate3_a8_composition as module
    repo = Path(__file__).resolve().parents[1]
    path = repo / 'tools' / 'v11_r09_gate3_a8_composition.py'
    pin = {'path': 'tools/v11_r09_gate3_a8_composition.py',
           'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
           'qualname': '_sealed'}
    assert _component(repo, module._sealed, pin)[-1] == pin['sha256']
    with pytest.raises(LaunchContractError, match='A8_COMPONENT_CHANGED'):
        _component(repo, module._sealed, {**pin, 'sha256': '0' * 64})


def test_adapter_method_override_refuses_even_with_same_source_hash():
    repo = Path(__file__).resolve().parents[1]
    source = repo / 'tools' / 'v11_r09_gate3_store_v1.py'
    pin = {'path': 'tools/v11_r09_gate3_store_v1.py',
           'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
           'qualname': 'VersionedImmutableObjectStore'}
    store = object.__new__(VersionedImmutableObjectStore)
    assert _component(repo, store, pin, '_check_dirs')
    store._check_dirs = lambda: None
    with pytest.raises(LaunchContractError, match='A8_COMPONENT_METHOD_REPLACED'):
        _component(repo, store, pin, '_check_dirs')


def test_foreign_class_method_refuses_pinned_source(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    source = repo / 'tools' / 'v11_r09_gate3_store_v1.py'
    pin = {'path': 'tools/v11_r09_gate3_store_v1.py',
           'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
           'qualname': 'VersionedImmutableObjectStore'}
    store = object.__new__(VersionedImmutableObjectStore)
    def foreign(self):
        return 'unreviewed implementation'
    monkeypatch.setattr(VersionedImmutableObjectStore, '_check_dirs', foreign)
    with pytest.raises(LaunchContractError, match='A8_COMPONENT_METHOD_REPLACED'):
        _component(repo, store, pin, '_check_dirs')


def test_in_place_code_change_refuses_at_recheck(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    source = repo / 'tools' / 'v11_r09_gate3_store_v1.py'
    pin = {'path': 'tools/v11_r09_gate3_store_v1.py',
           'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
           'qualname': 'VersionedImmutableObjectStore'}
    store = object.__new__(VersionedImmutableObjectStore)
    before = _component(repo, store, pin, '_check_dirs')
    package = b'offline isolated recheck fixture'
    prepared = PreparedComposition(hashlib.sha256(package).hexdigest(),
                                   'b' * 64, (before,), ('boot', 1, 2))
    def changed(self):
        return 'changed after preparation'
    method = VersionedImmutableObjectStore._check_dirs
    original_code = method.__code__
    plan = object.__new__(FrozenPlan)
    object.__setattr__(plan, 'window', SimpleNamespace(start_utc=1,
                                                       acquisition_end_utc=3))
    def isolated_validation(*args, **kwargs):
        return PreparedComposition(prepared.package_sha256, prepared.review_sha256,
                                   (_component(repo, store, pin, '_check_dirs'),),
                                   prepared.host_identity)
    monkeypatch.setattr('tools.v11_r09_gate3_a8_composition.validate_a8_composition',
                        isolated_validation)
    try:
        method.__code__ = changed.__code__
        with pytest.raises(LaunchContractError, match='A8_COMPONENT_METHOD_REPLACED'):
            recheck_a8_composition(prepared, package,
                expected_review_sha256='b' * 64, plan=plan, now_utc=2)
    finally:
        method.__code__ = original_code


def _store_pin():
    repo = Path(__file__).resolve().parents[1]
    source = repo / 'tools' / 'v11_r09_gate3_store_v1.py'
    return repo, {'path': 'tools/v11_r09_gate3_store_v1.py',
                  'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                  'qualname': 'VersionedImmutableObjectStore'}


def _live_context_fixture(tmp_path):
    root = tmp_path / 'store'
    root.mkdir(mode=0o700)
    (root / 'objects').mkdir(mode=0o700)
    store = VersionedImmutableObjectStore(
        root, manifest_sha256='a' * 64, policy_sha256='b' * 64,
        build_id='offline-fixture', clock_method='reviewed-clock',
        max_clock_age_seconds=30, host_id='fixture-host', boot_id='fixture-boot')
    payload = {'time': {'uncertainty_seconds': 1},
        'clocks_and_receipts': {'preregistration': {
            'max_measurement_age_seconds': 30}},
        'runtime': {'resource_bounds': {'local_storage_quota_bytes': 100},
                    'policy': {'sha256': 'b' * 64}},
        'limits': {'min_free_disk_bytes': 100, 'min_available_memory_bytes': 100,
                   'decoded': 100}}
    context = {'boot_id': 'fixture-boot', 'clock_method': 'reviewed-clock',
               'store_descriptor': store.descriptor_sha256,
               'store_policy': 'b' * 64}
    plan = SimpleNamespace(runtime_context_raw=canonical(context),
                           manifest_sha256='a' * 64)
    clock = FakeClock('fixture-boot', utc=100, mono=100, method='reviewed-clock')
    resources = FakeResourceProbe(free_disk_bytes=200, available_memory_bytes=200)
    repo, storage_pin = _store_pin()
    return (store, payload, plan, {'clock': clock, 'resources': resources,
                                   'storage': store}, repo, storage_pin)


def test_exact_reviewed_live_context_passes(tmp_path):
    store, payload, plan, components, repo, storage_pin = _live_context_fixture(tmp_path)
    try:
        host = _fresh_context(payload, plan, components, now_utc=100,
                              repo=repo, storage_pin=storage_pin)
        assert host[:3] == ('fixture-boot', *store.root_identity)
        assert host[3:6] == (store.descriptor_sha256, 'b' * 64,
                             'reviewed-clock')
    finally:
        store.close()


@pytest.mark.parametrize('change,reason', [
    ('clock_method', 'A8_LIVE_EVIDENCE_REQUIRED'),
    ('descriptor', 'A8_STORAGE_CONTEXT'),
    ('policy', 'A8_STORAGE_CONTEXT'),
    ('failed_store', 'OBJECT_DURABILITY_UNCERTAIN'),
])
def test_changed_reviewed_live_context_refuses(tmp_path, change, reason):
    store, payload, plan, components, repo, storage_pin = _live_context_fixture(tmp_path)
    try:
        _fresh_context(payload, plan, components, now_utc=100,
                       repo=repo, storage_pin=storage_pin)
        if change == 'clock_method':
            store.context['clock_method'] = components['clock'].method = 'unreviewed-clock'
        elif change == 'descriptor':
            store.descriptor_sha256 = 'c' * 64
        elif change == 'policy':
            store.context['policy'] = 'd' * 64
        else:
            store._failed = True
        with pytest.raises(LaunchContractError, match=reason):
            _fresh_context(payload, plan, components, now_utc=100,
                           repo=repo, storage_pin=storage_pin)
    finally:
        store.close()


def test_store_usable_instance_override_refuses_after_genuine_accept(tmp_path):
    """R1 repro 1: a genuine store is accepted once, `_failed` refuses
    correctly, then an instance `_usable` replacement must not bypass it."""
    store, payload, plan, components, repo, storage_pin = _live_context_fixture(tmp_path)
    try:
        _fresh_context(payload, plan, components, now_utc=100,
                       repo=repo, storage_pin=storage_pin)
        store._failed = True
        with pytest.raises(LaunchContractError, match='OBJECT_DURABILITY_UNCERTAIN'):
            _fresh_context(payload, plan, components, now_utc=100,
                           repo=repo, storage_pin=storage_pin)
        store._failed = False
        store._usable = lambda: None
        with pytest.raises(LaunchContractError, match='A8_COMPONENT_METHOD_REPLACED'):
            _fresh_context(payload, plan, components, now_utc=100,
                           repo=repo, storage_pin=storage_pin)
    finally:
        if '_usable' in store.__dict__:
            del store.__dict__['_usable']
        store.close()


def test_store_usable_class_override_refuses_after_genuine_accept(tmp_path, monkeypatch):
    """R1 repro 2: a class-level `_usable` replacement must not bypass the
    directory check either, even with the unchanged genuine `_check_dirs`."""
    store, payload, plan, components, repo, storage_pin = _live_context_fixture(tmp_path)
    try:
        _fresh_context(payload, plan, components, now_utc=100,
                       repo=repo, storage_pin=storage_pin)
        def foreign(self):
            return None
        monkeypatch.setattr(VersionedImmutableObjectStore, '_usable', foreign)
        with pytest.raises(LaunchContractError, match='A8_COMPONENT_METHOD_REPLACED'):
            _fresh_context(payload, plan, components, now_utc=100,
                           repo=repo, storage_pin=storage_pin)
    finally:
        monkeypatch.undo()
        store.close()


def test_store_usable_inplace_code_replacement_refuses_after_genuine_accept(tmp_path):
    """R1 repro 3: replacing `_usable.__code__` in place, restored after."""
    store, payload, plan, components, repo, storage_pin = _live_context_fixture(tmp_path)
    method = VersionedImmutableObjectStore._usable
    original_code = method.__code__
    def changed(self):
        return None
    try:
        _fresh_context(payload, plan, components, now_utc=100,
                       repo=repo, storage_pin=storage_pin)
        method.__code__ = changed.__code__
        with pytest.raises(LaunchContractError, match='A8_COMPONENT_METHOD_REPLACED'):
            _fresh_context(payload, plan, components, now_utc=100,
                           repo=repo, storage_pin=storage_pin)
    finally:
        method.__code__ = original_code
        store.close()


def test_store_directory_privacy_refusal_survives_usable_override(tmp_path):
    """R1 repro 4: a widened store root would only be caught by genuine
    `_check_dirs`; an instance `_usable` override must still refuse, not
    silently admit the now-public directory."""
    store, payload, plan, components, repo, storage_pin = _live_context_fixture(tmp_path)
    try:
        _fresh_context(payload, plan, components, now_utc=100,
                       repo=repo, storage_pin=storage_pin)
        os.chmod(store.root, 0o755)
        store._usable = lambda: None
        with pytest.raises(LaunchContractError, match='A8_COMPONENT_METHOD_REPLACED'):
            _fresh_context(payload, plan, components, now_utc=100,
                           repo=repo, storage_pin=storage_pin)
    finally:
        if '_usable' in store.__dict__:
            del store.__dict__['_usable']
        os.chmod(store.root, 0o700)
        store.close()


def test_store_directory_mode_change_refuses_without_override(tmp_path):
    """Positive control for the underlying refusal R1's bypass hid: without
    any `_usable` override, a widened store root is still rejected by the
    genuine, unreplaced directory check."""
    store, payload, plan, components, repo, storage_pin = _live_context_fixture(tmp_path)
    try:
        _fresh_context(payload, plan, components, now_utc=100,
                       repo=repo, storage_pin=storage_pin)
        os.chmod(store.root, 0o755)
        with pytest.raises(LaunchContractError, match='OBJECT_DIRECTORY_PRIVATE'):
            _fresh_context(payload, plan, components, now_utc=100,
                           repo=repo, storage_pin=storage_pin)
    finally:
        os.chmod(store.root, 0o700)
        store.close()


def test_store_usable_identity_rejects_instance_class_and_inplace_replacement(monkeypatch):
    repo, pin = _store_pin()
    store = object.__new__(VersionedImmutableObjectStore)
    assert _component(repo, store, pin, '_usable')
    store._usable = lambda: None
    with pytest.raises(LaunchContractError, match='A8_COMPONENT_METHOD_REPLACED'):
        _component(repo, store, pin, '_usable')
    del store._usable
    def foreign(self):
        return 'unreviewed implementation'
    monkeypatch.setattr(VersionedImmutableObjectStore, '_usable', foreign)
    with pytest.raises(LaunchContractError, match='A8_COMPONENT_METHOD_REPLACED'):
        _component(repo, store, pin, '_usable')
    monkeypatch.undo()
    method = VersionedImmutableObjectStore._usable
    original_code = method.__code__
    def changed(self):
        return 'changed'
    try:
        method.__code__ = changed.__code__
        with pytest.raises(LaunchContractError, match='A8_COMPONENT_METHOD_REPLACED'):
            _component(repo, store, pin, '_usable')
    finally:
        method.__code__ = original_code


def test_stale_clock_and_capacity_refuse_before_storage_use(tmp_path):
    clock = FakeClock('boot', utc=100, mono=100, measured_mono=0,
                      method='reviewed_clock')
    resources = FakeResourceProbe(free_disk_bytes=10**10,
                                  available_memory_bytes=10**10)
    storage = SimpleNamespace(context={'clock_method': 'reviewed_clock'}, boot_id='boot')
    payload = {'time': {'uncertainty_seconds': 1},
               'clocks_and_receipts': {'preregistration': {
                   'max_measurement_age_seconds': 10}},
               'runtime': {'resource_bounds': {'local_storage_quota_bytes': 100}},
               'limits': {'min_free_disk_bytes': 100,
                          'min_available_memory_bytes': 100, 'decoded': 100}}
    plan = SimpleNamespace(runtime_context_raw=canonical({
        'boot_id': 'boot', 'clock_method': 'reviewed_clock'}))
    repo, storage_pin = _store_pin()
    with pytest.raises(LaunchContractError, match='A8_CLOCK_STALE_OR_DIFFERENT_HOST'):
        _fresh_context(payload, plan,
                       {'clock': clock, 'resources': resources,
                        'storage': storage}, now_utc=100,
                       repo=repo, storage_pin=storage_pin)
    clock.set(measured_mono=100)
    resources.free_disk_bytes = 199
    with pytest.raises(LaunchContractError, match='A8_CAPACITY_STALE_OR_INSUFFICIENT'):
        _fresh_context(payload, plan,
                       {'clock': clock, 'resources': resources,
                        'storage': storage}, now_utc=100,
                       repo=repo, storage_pin=storage_pin)
    resources.free_disk_bytes = 1000
    root_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        storage.root_fd = root_fd
        storage.context['manifest'] = '1' * 64
        storage.root_identity = (0, 0)
        storage._check_dirs = lambda: None
        plan.manifest_sha256 = '1' * 64
        with pytest.raises(LaunchContractError, match='A8_STORAGE_CONTEXT'):
            _fresh_context(payload, plan,
                           {'clock': clock, 'resources': resources,
                            'storage': storage}, now_utc=100,
                           repo=repo, storage_pin=storage_pin)
    finally:
        os.close(root_fd)


def test_review_to_use_refuses_package_and_component_substitution(monkeypatch):
    prepared = PreparedComposition('a' * 64, 'b' * 64, ((1,),), ('boot', 1, 2, 'x'))
    with pytest.raises(LaunchContractError, match='A8_REVIEW_TO_USE_SUBSTITUTION'):
        recheck_a8_composition(prepared, b'changed', expected_review_sha256='b' * 64)
    package = b'original'
    prepared = PreparedComposition(hashlib.sha256(package).hexdigest(),
                                   'b' * 64, ((1,),), ('boot', 1, 2, 'x'))
    monkeypatch.setattr('tools.v11_r09_gate3_a8_composition.validate_a8_composition',
                        lambda *a, **kw: PreparedComposition(prepared.package_sha256,
                            prepared.review_sha256, ((2,),), prepared.host_identity))
    plan = object.__new__(FrozenPlan)
    object.__setattr__(plan, 'window', SimpleNamespace(start_utc=1,
                                                       acquisition_end_utc=3))
    with pytest.raises(LaunchContractError, match='A8_REVIEW_TO_USE_SUBSTITUTION'):
        recheck_a8_composition(prepared, package, expected_review_sha256='b' * 64,
                               plan=plan, now_utc=2)
    with pytest.raises(LaunchContractError, match='A8_USE_OUTSIDE_WINDOW'):
        recheck_a8_composition(prepared, package, expected_review_sha256='b' * 64,
                               plan=plan, now_utc=3)
